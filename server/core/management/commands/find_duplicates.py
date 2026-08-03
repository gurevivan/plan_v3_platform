# -*- coding: utf-8 -*-
"""Поиск и схлопывание дублей в базе.

    python manage.py find_duplicates          # только показать
    python manage.py find_duplicates --fix    # схлопнуть

Дубли появляются, если выгрузки заливали до того, как появилось слияние, или
заводили одно и то же руками на разных компьютерах.

Что считается дублем — то же самое, что при слиянии: совпадение ДЕЛОВОГО ключа
(номер контракта, номер заказа, артикул ГП, название площадки, у смены микроплана
дата вместе с ОП, переделом, бригадой, заказом и артикулами). Список ключей один
на обе задачи — он в `core/services/merge.py`. Второй такой список рано или
поздно разошёлся бы с первым, и «дубль» значил бы разное в разных местах.

Схлопывание идёт тем же путём, что и слияние: выгружаем состояние, сливаем его
само с собой (повторы при этом склеиваются, а ссылки переносятся на оставшуюся
запись) и записываем обратно. Отдельного кода удаления нет намеренно.

По умолчанию НИЧЕГО не меняет: сначала смотрим список, потом решаем.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.services import merge as mg

MAX_SHOWN = 10


class Command(BaseCommand):
    help = 'Найти записи-дубли в базе и (по желанию) схлопнуть их'

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true',
                            help='схлопнуть найденные дубли')
        parser.add_argument('--limit', type=int, default=MAX_SHOWN,
                            help='сколько примеров показывать на коллекцию')

    def handle(self, *args, **opts):
        from core.management.commands.export_json import build_export

        state = build_export()
        groups = self._find(state)

        if not groups:
            self.stdout.write(self.style.SUCCESS('Дублей не найдено.'))
            return

        total = sum(len(v) - 1 for rows in groups.values() for v in rows.values())
        self.stdout.write(f'Найдено лишних записей: {total}\n')
        for coll, rows in groups.items():
            extra = sum(len(v) - 1 for v in rows.values())
            self.stdout.write(self.style.WARNING(
                f'  {coll}: групп {len(rows)}, лишних записей {extra}'))
            for key, ids in list(rows.items())[:opts['limit']]:
                shown = ', '.join(str(i) for i in ids)
                self.stdout.write(f'      {self._key_text(coll, key)} → id: {shown}')
            if len(rows) > opts['limit']:
                self.stdout.write(f'      … ещё {len(rows) - opts["limit"]} групп')

        if not opts['fix']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Ничего не изменено. Схлопнуть: повторите с --fix.\n'
                'Перед этим снимите копию базы: ./db_backup.sh'))
            return

        # Слияние состояния самого с собой: повторы склеиваются, ссылки на
        # «проигравшие» записи переносятся на оставшуюся.
        empty = {c: [] for c in mg.LIST_COLLS}
        empty.update({c: {} for c in mg.DICT_COLLS})
        empty['nextId'] = 1
        merged, _ = mg.prepare(empty, state)

        from core.management.commands.import_json import Command as ImportCommand
        importer = ImportCommand()
        with transaction.atomic():
            importer._purge()
            counts = importer._load(merged)

        left = self._find(merged)
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Схлопнуто. Осталось записей:'))
        for k, v in counts.items():
            if v:
                self.stdout.write(f'  {k}  {v}')
        if left:
            self.stdout.write(self.style.ERROR(
                'ВНИМАНИЕ: часть дублей осталась — сообщите об этом, это ошибка.'))

    # ── поиск ───────────────────────────────────────────────────────────────
    def _find(self, state: dict) -> dict:
        out = {}
        for coll in mg.LIST_COLLS:
            by_key = {}
            for rec in state.get(coll) or []:
                key = mg.key_of(coll, rec)
                if key is None:
                    continue
                by_key.setdefault(key, []).append(rec.get('id'))
            dupes = {k: v for k, v in by_key.items() if len(v) > 1}
            if dupes:
                out[coll] = dupes
        return out

    def _key_text(self, coll: str, key) -> str:
        fields = mg.NATURAL_KEYS.get(coll, ())
        parts = [f'{f}={v!r}' for f, v in zip(fields, key) if v not in ('', None, ())]
        return ', '.join(parts) or '(пустой ключ)'
