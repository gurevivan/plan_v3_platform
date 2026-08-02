# -*- coding: utf-8 -*-
"""Теневой режим: сверка серверного расчёта с боевой выгрузкой.

Идея (вариант А из обсуждения 01.08.2026): люди работают как работали, а сервер
раз в день берёт свежую выгрузку и проверяет себя на ней. Нагрузка на людей —
одна выгрузка, которую они и так делают при обмене между компьютерами.

Проверяются ДВА независимых свойства:

1. **Паритет расчёта.** Загружаем состояние из файла, прогоняем Python-пересчёт и
   сравниваем план построчно с тем, что лежит в файле. Расхождение означает, что
   серверное ядро считает не так, как приложение.

2. **Круговой рейс через базу.** Импортируем файл в Postgres, выгружаем обратно и
   сравниваем по смыслу. Расхождение означает потерю данных в слое хранения.

Почему оба: паритет расчёта не заметит, что при записи в базу потерялось поле, а
круговой рейс не заметит, что план посчитан неверно.

Запуск:

    manage.py shadow_check                       # разобрать новые файлы из папки бота
    manage.py shadow_check --file ФАЙЛ           # конкретный файл
    manage.py shadow_check --no-telegram         # без отправки отчёта
    manage.py shadow_check --keep-db             # не трогать текущие данные в базе

Внимание: без `--keep-db` команда ЗАМЕЩАЕТ содержимое базы данными из файла —
это теневая инсталляция, её база и есть копия боевых данных.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand

from core import models as m
from core.calc import autoplan as ap
from core.calc.snapshot import from_export

# Куда бот складывает присланные выгрузки. Меняется через --inbox или PLAN_INBOX.
INBOX = Path(os.environ.get('PLAN_INBOX', '/root/bot_agent/tg_img'))
BACKUP_DIR = Path('/root/plan/backups')
REQUIRED_KEYS = {'microplan', 'macroplan', 'orders', 'nomenclature', 'ops'}
MAX_SHOWN = 8


class Command(BaseCommand):
    help = 'Сверить серверный расчёт со свежей боевой выгрузкой'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='конкретный файл вместо поиска новых')
        parser.add_argument('--inbox', default=str(INBOX), help='папка с выгрузками')
        parser.add_argument('--no-telegram', action='store_true', help='не отправлять отчёт')
        parser.add_argument('--keep-db', action='store_true',
                            help='не импортировать в базу (только паритет расчёта)')
        parser.add_argument('--backup-dir', default=str(BACKUP_DIR),
                            help='где лежат резервные копии базы')

    def handle(self, *args, **opts):
        backup = _backup_status(Path(opts['backup_dir']))

        if opts['file']:
            files = [Path(opts['file'])]
        else:
            files = self._new_files(Path(opts['inbox']))
            if not files:
                self.stdout.write('Новых выгрузок нет.')
                # Молчим, только если и с копиями базы всё хорошо. Иначе о
                # сломавшемся бэкапе никто не узнал бы: в тихие дни отчёт не
                # уходит вовсе, а замечают такое обычно при восстановлении.
                if backup['ok']:
                    return
                text = 'РЕЗЕРВНЫЕ КОПИИ БАЗЫ\n\n' + backup['text']
                self.stdout.write(text)
                if not opts['no_telegram']:
                    _send_telegram(text)
                return

        for path in files:
            report = self._check(path, keep_db=opts['keep_db'])
            report['backup'] = backup
            text = self._format(report)
            self.stdout.write(text)
            if not opts['no_telegram']:
                _send_telegram(text)

    # ── поиск новых файлов ──────────────────────────────────────────────────
    def _new_files(self, inbox: Path) -> list[Path]:
        """JSON-выгрузки, которых ещё не проверяли.

        Признак «уже проверяли» — запись в ImportRun с этим путём. Так история
        сверок остаётся в базе, и повторный запуск ничего не дублирует.
        """
        if not inbox.exists():
            return []
        seen = set(m.ImportRun.objects.values_list('source', flat=True))
        out = []
        for p in sorted(inbox.glob('*.json'), key=lambda x: x.stat().st_mtime):
            if str(p) in seen:
                continue
            if not _looks_like_export(p):
                continue
            out.append(p)
        return out

    # ── сама сверка ─────────────────────────────────────────────────────────
    def _check(self, path: Path, keep_db: bool) -> dict:
        rep = {'file': path.name, 'ok': True, 'errors': [], 'calc': None, 'roundtrip': None}
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            rep['ok'] = False
            rep['errors'].append(f'не читается: {exc}')
            return rep

        rep['stats'] = {
            'микроплан': len(raw.get('microplan') or []),
            'макроплан': len(raw.get('macroplan') or []),
            'заказы': len(raw.get('orders') or []),
            'бригады': len(raw.get('schedules') or []),
        }

        rep['calc'] = self._calc_parity(raw)
        if not rep['calc']['ok']:
            rep['ok'] = False

        if not keep_db:
            rep['roundtrip'] = self._roundtrip(path)
            if not rep['roundtrip']['ok']:
                rep['ok'] = False
        return rep

    def _calc_parity(self, raw: dict) -> dict:
        """Пересчёт Python-ядром против плана, сохранённого в файле."""
        stored = {}
        for row in raw.get('microplan') or []:
            stored[row.get('id')] = {
                'plan': float(row.get('planRelease') or 0),
                'date': row.get('date') or '', 'op': row.get('op') or '',
                'stage': row.get('stage') or '', 'order': row.get('releaseOrder') or '',
                'manual': bool(row.get('planReleaseIsManual')),
                'subs': {s.get('id'): float(s.get('planRelease') or 0)
                         for s in (row.get('subOrders') or [])},
            }

        snap = from_export(json.loads(json.dumps(raw)))
        ap.recalc_all_micro_plans(snap)

        diffs = []
        for row in snap.microplan:
            was = stored.get(row.get('id'))
            if not was:
                continue
            now = float(row.get('planRelease') or 0)
            if abs(now - was['plan']) > 1e-9:
                diffs.append({'id': row.get('id'), 'date': was['date'], 'op': was['op'],
                              'stage': was['stage'], 'order': was['order'],
                              'было': was['plan'], 'стало': now, 'ручная': was['manual']})
            for sub in (row.get('subOrders') or []):
                sw = was['subs'].get(sub.get('id'))
                if sw is None:
                    continue
                sn = float(sub.get('planRelease') or 0)
                if abs(sn - sw) > 1e-9:
                    diffs.append({'id': f"{row.get('id')}/подзаказ {sub.get('id')}",
                                  'date': was['date'], 'op': was['op'], 'stage': was['stage'],
                                  'order': sub.get('releaseOrder') or '',
                                  'было': sw, 'стало': sn, 'ручная': False})
        return {'ok': not diffs, 'rows': len(stored), 'diffs': diffs}

    def _roundtrip(self, path: Path) -> dict:
        """Импорт в базу и выгрузка обратно."""
        out = Path('/tmp/shadow_roundtrip.json')
        try:
            quiet = io.StringIO()   # вывод команд не нужен: отчёт формируем сами
            call_command('import_json', str(path), stdout=quiet)
            call_command('export_json', str(out), stdout=quiet)
        except Exception as exc:
            return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}

        proc = subprocess.run(
            [sys.executable, 'manage.py', 'compare_json', str(path), str(out)],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[3]))
        ok = proc.returncode == 0
        detail = [l for l in (proc.stdout or '').splitlines()
                  if 'ПОТЕРЯН' in l or 'записей' in l and 'совпадают' not in l]
        return {'ok': ok, 'detail': detail[:MAX_SHOWN]}

    # ── отчёт ───────────────────────────────────────────────────────────────
    def _format(self, rep: dict) -> str:
        lines = [f"ТЕНЕВАЯ СВЕРКА · {rep['file']}", '']
        st = rep.get('stats')
        if st:
            lines.append('  ' + ', '.join(f'{k} {v}' for k, v in st.items()))
            lines.append('')

        for err in rep['errors']:
            lines.append(f'ОШИБКА: {err}')

        calc = rep.get('calc')
        if calc:
            if calc['ok']:
                lines.append(f"Расчёт: совпал на всех {calc['rows']} строках.")
            else:
                lines.append(f"Расчёт: РАСХОЖДЕНИЙ {len(calc['diffs'])} из {calc['rows']} строк.")
                for d in calc['diffs'][:MAX_SHOWN]:
                    mark = ' (ручная)' if d['ручная'] else ''
                    lines.append(f"  {d['date']} {d['stage']} {d['op']} {d['order']}: "
                                 f"было {d['было']:.0f}, стало {d['стало']:.0f}{mark}")
                if len(calc['diffs']) > MAX_SHOWN:
                    lines.append(f"  … ещё {len(calc['diffs']) - MAX_SHOWN}")

        rt = rep.get('roundtrip')
        if rt is not None:
            if rt.get('ok'):
                lines.append('База: круговой рейс эквивалентен.')
            else:
                lines.append('База: ПОТЕРИ при круговом рейсе.')
                if rt.get('error'):
                    lines.append(f"  {rt['error']}")
                for d in rt.get('detail', []):
                    lines.append(f'  {d.strip()}')

        bk = rep.get('backup')
        if bk:
            lines.append('Копии базы: ' + bk['text'])

        lines.append('')
        ok = rep['ok'] and (not bk or bk['ok'])
        lines.append('Итог: всё сходится.' if ok
                     else 'Итог: ЕСТЬ РАСХОЖДЕНИЯ — нужно разобраться до перехода на сервер.')
        return '\n'.join(lines)


def _backup_status(dir_path: Path = BACKUP_DIR) -> dict:
    """Состояние резервных копий базы: есть ли свежая и не пуста ли она.

    Проверять нужно именно возраст И размер. Дамп упавшего pg_dump бывает
    непустым, но крошечным, и в каталоге он выглядит как обычная копия —
    заметить это при восстановлении уже поздно.
    """
    import time

    if not dir_path.is_dir():
        return {'ok': False, 'text': f'каталога копий нет ({dir_path})'}
    files = sorted(dir_path.glob('plandb_*.sql.gz'),
                   key=lambda p: p.stat().st_mtime, reverse=True)

    if not files:
        return {'ok': False, 'text': 'НЕТ НИ ОДНОЙ — база не бэкапится'}

    last = files[0]
    age_h = (time.time() - last.stat().st_mtime) / 3600
    size_kb = last.stat().st_size // 1024
    text = f'{len(files)} шт., последняя {last.name} ({size_kb} КБ, {age_h:.1f} ч назад)'

    # Копия снимается ежечасно; сутки без свежей — это уже сломанный cron.
    if age_h > 24:
        return {'ok': False, 'text': 'УСТАРЕЛИ — ' + text}
    if size_kb < 10:
        return {'ok': False, 'text': 'ПОДОЗРИТЕЛЬНО МАЛА — ' + text}
    return {'ok': True, 'text': text}


def _looks_like_export(path: Path) -> bool:
    """Отсеиваем файлы, которые не являются выгрузкой «Плана»."""
    try:
        if path.stat().st_size > 64 * 1024 * 1024:
            return False
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return False
    return isinstance(data, dict) and REQUIRED_KEYS.issubset(data.keys())


def _send_telegram(text: str) -> None:
    """Отчёт в Telegram. Молча пропускаем, если бот не настроен.

    Настройки бота (`token`, `owner_chat_id`) лежат в отдельном файле вне
    репозитория; путь задаётся `PLAN_TG_CONFIG`. Держать их в коде нельзя —
    токен даёт полный доступ к боту.
    """
    import os

    try:
        import requests
        cfg_path = Path(os.environ.get('PLAN_TG_CONFIG', '/root/bot_agent/config.json'))
        if not cfg_path.exists():
            return
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        for i in range(0, len(text), 3900):
            requests.post(f"https://api.telegram.org/bot{cfg['token']}/sendMessage",
                          json={'chat_id': cfg['owner_chat_id'], 'text': text[i:i + 3900],
                                'disable_web_page_preview': True}, timeout=30)
    except Exception as exc:      # отчёт не должен ронять проверку
        print(f'(отчёт в Telegram не отправлен: {exc})', file=sys.stderr)
