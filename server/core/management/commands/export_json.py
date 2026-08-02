# -*- coding: utf-8 -*-
"""Выгрузка из БД обратно в формат JSON-экспорта «План V3».

    python manage.py export_json /tmp/out.json

Формат совпадает с `_buildFullExportData()` в План.html: те же 21 ключ, те же имена
полей. Это аварийный люк из ТЗ §11 — данные всегда можно вернуть в автономный
`План.html`, и это же способ проверить, что перенос ничего не потерял
(критерий приёмки фазы 1).
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from core import models as m
from core.services import mapping as mp


class Command(BaseCommand):
    help = 'Выгрузить базу в JSON-формат приложения'

    def add_arguments(self, parser):
        parser.add_argument('path', help='куда записать JSON')
        parser.add_argument('--indent', type=int, default=None,
                            help='отступ для читаемости (по умолчанию компактно)')

    def handle(self, *args, **opts):
        data = build_export()
        out = Path(opts['path'])
        out.write_text(json.dumps(data, ensure_ascii=False, indent=opts['indent']),
                       encoding='utf-8')
        total = sum(len(v) for v in data.values() if isinstance(v, list))
        self.stdout.write(self.style.SUCCESS(
            f'Выгружено в {out}: {len(data)} ключей, {total} записей в списках'))


def build_export():
    """Собирает структуру экспорта. Вынесено отдельно — пригодится для API `GET /api/export`."""
    d = {}

    d['bases'] = [mp.build(o, mp.BASES) for o in m.DeliveryBase.objects.all().order_by('id')]
    d['nomenclature'] = [_nomenclature(n) for n in
                         m.Nomenclature.objects.prefetch_related('route', 'route_overrides__items')
                         .order_by('id')]
    d['ops'] = [mp.build(o, mp.OPS) for o in m.Op.objects.all().order_by('id')]
    d['contracts'] = [mp.build(o, mp.CONTRACTS) for o in m.Contract.objects.all().order_by('id')]
    d['customerOrders'] = [mp.build(o, mp.CUSTOMER_ORDERS)
                           for o in m.CustomerOrder.objects.all().order_by('id')]
    d['orders'] = [_order(o) for o in
                   m.ProductionOrder.objects.prefetch_related('stage_ops', 'deliveries')
                   .order_by('id')]
    d['macroplan'] = [_macro(r) for r in
                      m.MacroplanRow.objects.prefetch_related('order_nums').order_by('id')]
    d['microplan'] = [_micro(r) for r in
                      m.MicroplanRow.objects.prefetch_related(
                          'article_items', 'sub_orders', 'launches', 'workers').order_by('id')]
    d['schedules'] = [_schedule(s) for s in
                      m.Schedule.objects.prefetch_related('workers').order_by('id')]
    d['scheduleMonthOverrides'] = [mp.build(o, mp.SCHED_MONTH_OVR)
                                   for o in m.ScheduleMonthOverride.objects.all().order_by('id')]
    d['calOverrides'] = [mp.build(o, mp.CAL_OVERRIDES)
                         for o in m.CalOverride.objects.all().order_by('id')]
    d['holidays'] = [mp.build(o, mp.HOLIDAYS) for o in m.Holiday.objects.all().order_by('id')]
    d['manualFrv'] = [_frv(f) for f in
                      m.ManualFrv.objects.prefetch_related('segments').order_by('id')]
    d['manualFrvExtraMonths'] = []
    d['manualFrvPivotExtraRows'] = []
    d['deliveryMatrix'] = [mp.build(o, mp.DELIVERY_MATRIX)
                           for o in m.DeliveryMatrix.objects.all().order_by('id')]
    d['planBaseline'] = [mp.build(o, mp.PLAN_BASELINE)
                         for o in m.PlanBaseline.objects.all().order_by('id')]
    d['orderLinks'] = [mp.build(o, mp.ORDER_LINKS) for o in m.OrderLink.objects.all().order_by('id')]
    d['macroEff'] = [mp.build(o, mp.MACRO_EFF) for o in m.MacroEff.objects.all().order_by('id')]

    d['tcRcOverrides'] = {t.order_number: mp.to_json(t.time_cost, mp.NUM)
                          for t in m.TimeCostOverride.objects.filter(stage='РЦ')}
    d['tcShOverrides'] = {t.order_number: mp.to_json(t.time_cost, mp.NUM)
                          for t in m.TimeCostOverride.objects.filter(stage='ШЦ')}
    # nextId в приложении пересчитывается при импорте, здесь отдаём максимум + 1.
    d['nextId'] = _next_id()
    return d


def _nomenclature(n):
    rec = mp.build(n, mp.NOMENCLATURE)
    rec['route'] = [mp.build(r, mp.NOM_ROUTE) for r in n.route.all()]
    rec['routeOverrides'] = [
        dict({'op': ovr.op_name,
              'route': [mp.build(i, mp.NOM_ROUTE) for i in ovr.items.all()]}, **(ovr.extra or {}))
        for ovr in n.route_overrides.all()]
    return rec


def _order(o):
    rec = mp.build(o, mp.ORDERS)
    for js_field, stage in mp.STAGE_BY_OP_FIELD.items():
        rec[js_field] = [so.op_name for so in o.stage_ops.all() if so.stage == stage]
    rec['deliveries'] = [mp.build(x, mp.ORDER_DELIVERIES) for x in o.deliveries.all()]
    return rec


def _macro(r):
    rec = mp.build(r, mp.MACROPLAN)
    nums = [x.order_number for x in r.order_nums.all()]
    if nums:
        rec['orderNums'] = nums
    return rec


def _micro(r):
    rec = mp.build(r, mp.MICROPLAN)
    rec['articleItems'] = [x.article_item for x in r.article_items.all()]
    rec['subOrders'] = [mp.build(s, mp.SUB_ORDERS) for s in r.sub_orders.all()]
    rec['launches'] = [mp.build(x, mp.LAUNCHES) for x in r.launches.all()]
    rec['workers'] = [mp.build(w, mp.MICRO_WORKERS) for w in r.workers.all()]
    return rec


def _schedule(s):
    rec = mp.build(s, mp.SCHEDULES)
    rec['workers'] = [mp.build(w, mp.WORKERS) for w in s.workers.all()]
    return rec


def _frv(f):
    rec = mp.build(f, mp.MANUAL_FRV)
    rec['frvSegments'] = [mp.build(seg, mp.MANUAL_FRV_SEGMENTS) for seg in f.segments.all()]
    return rec


def _next_id():
    biggest = 0
    for model in (m.Op, m.Nomenclature, m.Contract, m.CustomerOrder, m.ProductionOrder,
                  m.MacroplanRow, m.MicroplanRow, m.Schedule, m.ManualFrv, m.OrderLink):
        val = model.objects.exclude(src_id=None).order_by('-src_id').values_list('src_id', flat=True).first()
        if val:
            biggest = max(biggest, val)
    return biggest + 1
