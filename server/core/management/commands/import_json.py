# -*- coding: utf-8 -*-
"""Импорт JSON-экспорта «План V3» в нормализованную БД.

    python manage.py import_json ../tests/fixtures/real_export_20260801.json

По умолчанию таблицы очищаются перед загрузкой (`--keep` отменяет). Всё внутри
одной транзакции: либо загрузилось целиком, либо не тронуто.

ВАЖНО (DATA_CONTRACT.md §2): на вход ждём НОРМАЛИЗОВАННЫЙ файл — тот, который
приложение отдаёт после `_applyFullImport` со всеми 22 миграциями. Старые форматы
сюда подавать нельзя: слой миграций здесь не воспроизводится намеренно, иначе
пришлось бы держать его в двух реализациях.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core import models as m
from core.services import mapping as mp


class Command(BaseCommand):
    help = 'Загрузить JSON-экспорт «План V3» в базу'

    def add_arguments(self, parser):
        parser.add_argument('path', help='путь к JSON-экспорту или «-» для ввода из потока')
        parser.add_argument('--keep', action='store_true',
                            help='не очищать таблицы перед загрузкой')
        parser.add_argument('--merge', action='store_true',
                            help='слить с тем, что уже в базе: дубли обновить, '
                                 'остальное добавить (иначе данные ЗАМЕЩАЮТСЯ)')

    def handle(self, *args, **opts):
        # «-» читает из стандартного ввода: внутрь контейнера файл иначе пришлось бы
        # сначала копировать, а это лишний шаг там, где он не нужен.
        if opts['path'] == '-':
            import sys
            raw = sys.stdin.read()
            if not raw.strip():
                raise CommandError('на вход ничего не подано')
            source = '<стандартный ввод>'
        else:
            path = Path(opts['path'])
            if not path.exists():
                raise CommandError(f'файл не найден: {path}')
            raw = path.read_text(encoding='utf-8')
            source = str(path)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError(f'это не JSON: {exc}')
        if not isinstance(data, dict) or 'microplan' not in data:
            raise CommandError('это не похоже на выгрузку «Плана» '
                               '(нет ключа microplan)')

        stats = None
        if opts['merge']:
            # Сливаем на уровне выгрузки, а записываем прежним путём. Второй способ
            # писать в базу заводить нельзя: карта полей одна на импорт и экспорт,
            # и построчный upsert стал бы её вторым, расходящимся экземпляром.
            from core.management.commands.export_json import build_export
            from core.services import merge as mg

            base = build_export()
            data, stats = mg.prepare(base, data)

        run = m.ImportRun.objects.create(source=source)
        try:
            with transaction.atomic():
                if not opts['keep']:
                    self._purge()
                counts = self._load(data)
            run.counts = counts
            run.ok = True
            run.finished_at = timezone.now()
            run.save()
        except Exception as exc:
            run.message = f'{type(exc).__name__}: {exc}'
            run.finished_at = timezone.now()
            run.save()
            raise

        if stats is not None:
            self.stdout.write(self.style.SUCCESS(f'Слито с базой из {source}:'))
            width = max((len(k) for k in stats), default=1)
            for coll, s in stats.items():
                self.stdout.write(f"  {coll.ljust(width)}  добавлено {s['добавлено']}, "
                                  f"обновлено {s['обновлено']}")
            self.stdout.write('')
            self.stdout.write('Стало в базе:')
        else:
            self.stdout.write(self.style.SUCCESS(f'Загружено из {source}:'))

        width = max(len(k) for k in counts)
        for k, v in counts.items():
            self.stdout.write(f'  {k.ljust(width)}  {v}')

    # ── очистка ─────────────────────────────────────────────────────────────
    def _purge(self):
        for model in (m.MicroRowWorker, m.LaunchEntry, m.MicroSubOrder,
                      m.MicroplanRowArticleItem, m.MicroplanRow,
                      m.MacroplanRowOrder, m.MacroplanRow, m.MacroEff,
                      m.ManualFrvSegment, m.ManualFrv,
                      m.CalOverride, m.ScheduleMonthOverride, m.ScheduleWorker, m.Schedule,
                      m.PlanBaseline, m.OrderLink, m.OrderDelivery, m.ProductionOrderOp,
                      m.ProductionOrder, m.CustomerOrder, m.Contract,
                      m.TimeCostOverride, m.NomRouteOverrideItem, m.NomRouteOverride,
                      m.NomRoute, m.Nomenclature, m.Op, m.DeliveryBase,
                      m.DeliveryMatrix, m.Holiday):
            model.objects.all().delete()

    # ── загрузка ────────────────────────────────────────────────────────────
    def _load(self, d):
        c = {}

        # Справочники
        c['bases'] = self._simple(d.get('bases'), m.DeliveryBase, mp.BASES)
        c['ops'] = self._simple(d.get('ops'), m.Op, mp.OPS)
        c['deliveryMatrix'] = self._simple(d.get('deliveryMatrix'), m.DeliveryMatrix,
                                           mp.DELIVERY_MATRIX)
        c['holidays'] = self._simple(d.get('holidays'), m.Holiday, mp.HOLIDAYS)

        # Номенклатура с маршрутами
        n_cnt = r_cnt = ro_cnt = 0
        for rec in d.get('nomenclature') or []:
            fields, extra = mp.split(rec, mp.NOMENCLATURE, mp.NOMENCLATURE_CHILD)
            nom = m.Nomenclature.objects.create(extra=extra, **fields)
            n_cnt += 1
            for i, item in enumerate(rec.get('route') or []):
                f, e = mp.split(item, mp.NOM_ROUTE)
                m.NomRoute.objects.create(nomenclature=nom, ordinal=i, extra=e, **f)
                r_cnt += 1
            for ovr in rec.get('routeOverrides') or []:
                override = m.NomRouteOverride.objects.create(
                    nomenclature=nom, op_name=str(ovr.get('op') or ''),
                    extra={k: v for k, v in ovr.items() if k not in ('op', 'route')})
                ro_cnt += 1
                for i, item in enumerate(ovr.get('route') or []):
                    f, e = mp.split(item, mp.NOM_ROUTE)
                    m.NomRouteOverrideItem.objects.create(override=override, ordinal=i,
                                                          extra=e, **f)
        c['nomenclature'] = n_cnt
        c['  строк маршрута'] = r_cnt
        c['  переопределений маршрута'] = ro_cnt

        # Контракты и заказы клиентов
        c['contracts'] = self._simple(d.get('contracts'), m.Contract, mp.CONTRACTS)
        c['customerOrders'] = self._simple(d.get('customerOrders'), m.CustomerOrder,
                                           mp.CUSTOMER_ORDERS)

        # Заказы на производство: ОП по переделам и поставки — в дочерние таблицы
        o_cnt = op_cnt = dl_cnt = 0
        for rec in d.get('orders') or []:
            fields, extra = mp.split(rec, mp.ORDERS, mp.ORDERS_CHILD)
            order = m.ProductionOrder.objects.create(extra=extra, **fields)
            o_cnt += 1
            for js_field, stage in mp.STAGE_BY_OP_FIELD.items():
                for i, op_name in enumerate(rec.get(js_field) or []):
                    if not op_name:
                        continue
                    m.ProductionOrderOp.objects.create(order=order, stage=stage,
                                                       op_name=str(op_name), ordinal=i)
                    op_cnt += 1
            for i, dlv in enumerate(rec.get('deliveries') or []):
                f, e = mp.split(dlv, mp.ORDER_DELIVERIES)
                m.OrderDelivery.objects.create(order=order, ordinal=i, extra=e, **f)
                dl_cnt += 1
        c['orders'] = o_cnt
        c['  ОП по переделам'] = op_cnt
        c['  поставок'] = dl_cnt

        # Связи ЗК↔ПЗ
        by_src = {o.src_id: o for o in m.ProductionOrder.objects.all() if o.src_id is not None}
        ln = 0
        for rec in d.get('orderLinks') or []:
            fields, extra = mp.split(rec, mp.ORDER_LINKS)
            m.OrderLink.objects.create(extra=extra,
                                       production_order=by_src.get(fields.get('production_order_src_id')),
                                       **fields)
            ln += 1
        c['orderLinks'] = ln

        # Нормы по заказам: два словаря → одна таблица с переделом
        tc = 0
        for stage, key in (('РЦ', 'tcRcOverrides'), ('ШЦ', 'tcShOverrides')):
            for order_number, value in (d.get(key) or {}).items():
                m.TimeCostOverride.objects.create(order_number=order_number, stage=stage,
                                                  time_cost=mp.to_db(value, mp.NUM))
                tc += 1
        c['нормы по заказам'] = tc

        # Макроплан
        mr = mo = 0
        for rec in d.get('macroplan') or []:
            fields, extra = mp.split(rec, mp.MACROPLAN, mp.MACROPLAN_CHILD)
            row = m.MacroplanRow.objects.create(extra=extra, **fields)
            mr += 1
            for i, num in enumerate(rec.get('orderNums') or []):
                m.MacroplanRowOrder.objects.create(row=row, order_number=str(num), ordinal=i)
                mo += 1
        c['macroplan'] = mr
        c['  привязанных заказов'] = mo
        c['macroEff'] = self._simple(d.get('macroEff'), m.MacroEff, mp.MACRO_EFF)

        # Бригады и графики
        s_cnt = w_cnt = 0
        for rec in d.get('schedules') or []:
            fields, extra = mp.split(rec, mp.SCHEDULES, mp.SCHEDULES_CHILD)
            sched = m.Schedule.objects.create(extra=extra, **fields)
            s_cnt += 1
            for i, w in enumerate(rec.get('workers') or []):
                f, e = mp.split(w, mp.WORKERS)
                m.ScheduleWorker.objects.create(schedule=sched, ordinal=i, extra=e, **f)
                w_cnt += 1
        c['schedules'] = s_cnt
        c['  групп работников'] = w_cnt

        sched_by_src = {s.src_id: s for s in m.Schedule.objects.all() if s.src_id is not None}
        c['scheduleMonthOverrides'] = self._simple(
            d.get('scheduleMonthOverrides'), m.ScheduleMonthOverride, mp.SCHED_MONTH_OVR,
            link=lambda f: {'schedule': sched_by_src.get(f.get('schedule_src_id'))})
        c['calOverrides'] = self._simple(
            d.get('calOverrides'), m.CalOverride, mp.CAL_OVERRIDES,
            link=lambda f: {'schedule': sched_by_src.get(f.get('schedule_src_id'))})

        # ФРВ
        f_cnt = seg_cnt = 0
        for rec in d.get('manualFrv') or []:
            fields, extra = mp.split(rec, mp.MANUAL_FRV, mp.MANUAL_FRV_CHILD)
            frv = m.ManualFrv.objects.create(extra=extra, **fields)
            f_cnt += 1
            for i, seg in enumerate(rec.get('frvSegments') or []):
                sf, sx = mp.split(seg, mp.MANUAL_FRV_SEGMENTS)
                m.ManualFrvSegment.objects.create(frv=frv, ordinal=i, extra=sx, **sf)
                seg_cnt += 1
        c['manualFrv'] = f_cnt
        c['  сегментов ФРВ'] = seg_cnt

        # Микроплан со всеми потрохами
        mi = ai = so = la = wk = 0
        for rec in d.get('microplan') or []:
            fields, extra = mp.split(rec, mp.MICROPLAN, mp.MICROPLAN_CHILD)
            row = m.MicroplanRow.objects.create(
                extra=extra, schedule=sched_by_src.get(fields.get('schedule_src_id')), **fields)
            mi += 1
            for i, item in enumerate(rec.get('articleItems') or []):
                m.MicroplanRowArticleItem.objects.create(row=row, article_item=str(item),
                                                          ordinal=i)
                ai += 1
            for i, sub in enumerate(rec.get('subOrders') or []):
                f, e = mp.split(sub, mp.SUB_ORDERS)
                m.MicroSubOrder.objects.create(row=row, ordinal=i, extra=e, **f)
                so += 1
            for i, lc in enumerate(rec.get('launches') or []):
                f, e = mp.split(lc, mp.LAUNCHES)
                m.LaunchEntry.objects.create(row=row, ordinal=i, extra=e, **f)
                la += 1
            for i, w in enumerate(rec.get('workers') or []):
                f, e = mp.split(w, mp.MICRO_WORKERS)
                m.MicroRowWorker.objects.create(row=row, ordinal=i, extra=e, **f)
                wk += 1
        c['microplan'] = mi
        c['  изделий в строках'] = ai
        c['  подзаказов'] = so
        c['  запусков'] = la
        c['  работников в строках'] = wk

        c['planBaseline'] = self._simple(d.get('planBaseline'), m.PlanBaseline, mp.PLAN_BASELINE)
        return c

    def _simple(self, records, model, field_map, link=None):
        n = 0
        for rec in records or []:
            fields, extra = mp.split(rec, field_map)
            kwargs = dict(fields)
            if link:
                kwargs.update(link(fields))
            model.objects.create(extra=extra, **kwargs)
            n += 1
        return n
