# -*- coding: utf-8 -*-
"""Семантическое сравнение двух JSON-экспортов «План V3».

    python manage.py compare_json исходный.json выгруженный.json

Критерий приёмки фазы 1: круговой рейс JSON → БД → JSON эквивалентен исходному.

«Эквивалентен» здесь означает по смыслу, а не побайтово:
  * порядок ключей в объектах не важен;
  * 3721 и 3721.0 — одно и то же число;
  * отсутствующее поле, пустая строка, пустой список и null считаются одинаковыми
    (в исходнике поле часто просто не создаётся, а в БД у колонки есть значение
    по умолчанию — это не потеря данных);
  * записи списков сопоставляются по `id`, если он есть, иначе по позиции —
    иначе перестановка строк давала бы ложные расхождения.

Выход: расхождения по коллекциям с примерами. Код возврата 1, если есть потери.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

EMPTY = (None, '', [], {}, 0)

# Коллекции без собственного `id`: сопоставлять по позиции нельзя — порядок выдачи
# базы и порядок в файле не совпадают, и любое сравнение превращалось бы в шум.
NATURAL_KEYS = {
    'calOverrides': ('scheduleId', 'date'),
    'scheduleMonthOverrides': ('scheduleId', 'month'),
    'planBaseline': ('month', 'date', 'scheduleId', 'stage', 'releaseOrder'),
}


class Command(BaseCommand):
    help = 'Сравнить два JSON-экспорта по смыслу'

    def add_arguments(self, parser):
        parser.add_argument('left', help='исходный файл')
        parser.add_argument('right', help='файл после кругового рейса')
        parser.add_argument('--limit', type=int, default=5, help='примеров на коллекцию')
        parser.add_argument('--strict-empty', action='store_true',
                            help='считать пустую строку и отсутствие поля разными')

    def handle(self, *args, **opts):
        left, right = Path(opts['left']), Path(opts['right'])
        for p in (left, right):
            if not p.exists():
                raise CommandError(f'файл не найден: {p}')
        a = json.loads(left.read_text(encoding='utf-8'))
        b = json.loads(right.read_text(encoding='utf-8'))
        self.strict = opts['strict_empty']
        self.limit = opts['limit']

        problems = 0
        keys = sorted(set(a) | set(b))
        self.stdout.write(f'Сравнение: {left.name} ↔ {right.name}\n')

        for key in keys:
            if key not in a:
                self.stdout.write(self.style.WARNING(f'  {key}: нет в исходном'))
                problems += 1
                continue
            if key not in b:
                self.stdout.write(self.style.ERROR(f'  {key}: ПОТЕРЯН при выгрузке'))
                problems += 1
                continue
            av, bv = a[key], b[key]
            if isinstance(av, list) and isinstance(bv, list):
                problems += self._cmp_list(key, av, bv)
            elif isinstance(av, dict) and isinstance(bv, dict):
                problems += self._cmp_dict_coll(key, av, bv)
            else:
                if not self._eq(av, bv):
                    self.stdout.write(f'  {key}: {av!r} → {bv!r}')
                    # nextId пересчитывается — это не потеря данных.
                    if key != 'nextId':
                        problems += 1
                else:
                    self.stdout.write(self.style.SUCCESS(f'  {key}: совпадает'))

        self.stdout.write('')
        if problems:
            self.stdout.write(self.style.ERROR(f'РАСХОЖДЕНИЙ: {problems}'))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('Круговой рейс эквивалентен исходнику'))

    # ── сравнение ───────────────────────────────────────────────────────────
    def _eq(self, x, y):
        if isinstance(x, bool) or isinstance(y, bool):
            return bool(x) == bool(y)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return abs(float(x) - float(y)) < 1e-9
        if not self.strict and x in EMPTY and y in EMPTY:
            # 0 и '' считаем разными: количество 0 и пустая строка — не одно и то же.
            num_x = isinstance(x, (int, float)) and not isinstance(x, bool)
            num_y = isinstance(y, (int, float)) and not isinstance(y, bool)
            if num_x == num_y:
                return True
        if isinstance(x, list) and isinstance(y, list):
            return len(x) == len(y) and all(self._eq(i, j) for i, j in zip(x, y))
        if isinstance(x, dict) and isinstance(y, dict):
            return all(self._eq(x.get(k), y.get(k)) for k in set(x) | set(y))
        return x == y

    def _cmp_list(self, key, av, bv):
        if len(av) != len(bv):
            self.stdout.write(self.style.ERROR(
                f'  {key}: записей {len(av)} → {len(bv)}'))
            return 1
        # Сопоставляем по id, если он есть у всех.
        if av and all(isinstance(r, dict) and 'id' in r for r in av) \
               and all(isinstance(r, dict) and 'id' in r for r in bv):
            bmap = {r['id']: r for r in bv}
            pairs = [(r, bmap.get(r['id'])) for r in av]
        elif key in NATURAL_KEYS and av and all(isinstance(r, dict) for r in av + bv):
            nk = NATURAL_KEYS[key]
            def _nk(r):
                return tuple(str(r.get(f, '')) for f in nk)
            bmap = {}
            for r in bv:
                bmap.setdefault(_nk(r), []).append(r)
            pairs = []
            for r in av:
                bucket = bmap.get(_nk(r)) or []
                pairs.append((r, bucket.pop(0) if bucket else None))
        else:
            pairs = list(zip(av, bv))

        diffs = []
        for src, got in pairs:
            if got is None:
                diffs.append((src.get('id') if isinstance(src, dict) else '?', 'записи нет', '', ''))
                continue
            if isinstance(src, dict) and isinstance(got, dict):
                for f in sorted(set(src) | set(got)):
                    if not self._eq(src.get(f), got.get(f)):
                        diffs.append((src.get('id', '—'), f, src.get(f), got.get(f)))
            elif not self._eq(src, got):
                diffs.append(('—', '', src, got))

        if not diffs:
            self.stdout.write(self.style.SUCCESS(f'  {key}: {len(av)} записей, совпадают'))
            return 0
        self.stdout.write(self.style.ERROR(
            f'  {key}: {len(av)} записей, расхождений в полях {len(diffs)}'))
        for rid, field, was, now in diffs[:self.limit]:
            self.stdout.write(f'      id={rid} поле «{field}»: {was!r} → {now!r}')
        if len(diffs) > self.limit:
            self.stdout.write(f'      … ещё {len(diffs) - self.limit}')
        return 1

    def _cmp_dict_coll(self, key, av, bv):
        miss = [k for k in av if k not in bv]
        extra = [k for k in bv if k not in av]
        bad = [k for k in av if k in bv and not self._eq(av[k], bv[k])]
        if not (miss or extra or bad):
            self.stdout.write(self.style.SUCCESS(f'  {key}: {len(av)} ключей, совпадают'))
            return 0
        self.stdout.write(self.style.ERROR(
            f'  {key}: потеряно {len(miss)}, лишних {len(extra)}, расходятся {len(bad)}'))
        for k in (miss + bad)[:self.limit]:
            self.stdout.write(f'      «{k}»: {av.get(k)!r} → {bv.get(k)!r}')
        return 1
