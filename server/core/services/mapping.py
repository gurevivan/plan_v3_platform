# -*- coding: utf-8 -*-
"""Соответствие полей JSON-экспорта и колонок БД.

Одно описание на импорт и экспорт: если бы они хранили карты полей раздельно, то
рано или поздно разошлись бы, и круговой рейс начал бы терять данные молча —
ровно так уже случилось в самом приложении с `orderLinks` и `macroEff`
(DATA_CONTRACT.md §4).

Формат карты: `('имя_в_json', 'имя_колонки', тип)` или `(..., значение_по_умолчанию)`.
Тип задаёт преобразование в обе стороны. Всё, чего нет в карте и не разложено по
дочерним таблицам, уезжает в `extra` — и возвращается оттуда при экспорте.

**Значение по умолчанию задавать обязательно там, где домен трактует отсутствие
поля не как нуль.** Пример: `learningEff` отсутствует у большинства строк
микроплана, и приложение читает это как 100 % (`m.learningEff ?? 100`). Записать
туда 0 значило бы обнулить эффективность освоения и сломать расчёт.

**Отсутствие поля сохраняется.** Список ключей, которых в исходной записи не было,
кладётся в `extra['__absent']`, и экспорт их не выводит. Иначе круговой рейс
дописывал бы записям поля со значениями по умолчанию — формально не потеря, но
файл переставал совпадать с исходником, и настоящие потери тонули бы в этом шуме.
"""
from datetime import date, datetime
from decimal import Decimal

# ── Типы полей ──────────────────────────────────────────────────────────────
STR = 'str'      # строка; None → ''
NUM = 'num'      # число; в БД Decimal, в JSON int/float
INT = 'int'      # целое
BOOL = 'bool'
DATE = 'date'    # 'ГГГГ-ММ-ДД' ↔ date
JSON = 'json'    # список/словарь как есть
NUMN = 'numn'    # число, допускающее None (норма-переопределение)


def to_db(value, kind):
    """JSON → значение для колонки."""
    if kind == STR:
        return '' if value is None else str(value)
    if kind == NUM:
        if value in (None, ''):
            return Decimal(0)
        return Decimal(str(value))
    if kind == NUMN:
        if value in (None, ''):
            return None
        return Decimal(str(value))
    if kind == INT:
        if value in (None, ''):
            return 0
        return int(float(value))
    if kind == BOOL:
        return bool(value)
    if kind == DATE:
        if not value:
            return None
        try:
            return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
    if kind == JSON:
        return value if value is not None else []
    return value


def to_json(value, kind):
    """Значение колонки → JSON."""
    if kind == STR:
        return value or ''
    if kind in (NUM, NUMN):
        if value is None:
            return None
        d = Decimal(value)
        # Целые отдаём целыми: в исходнике количества — int, и «3721.0» вместо
        # «3721» сделало бы сравнение с исходным файлом шумным.
        return int(d) if d == d.to_integral_value() else float(d)
    if kind == INT:
        return int(value or 0)
    if kind == BOOL:
        return bool(value)
    if kind == DATE:
        if not value:
            return ''
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    if kind == JSON:
        return value
    return value


# ── Карты полей по коллекциям ───────────────────────────────────────────────
# Поля, разложенные в дочерние таблицы, перечислены в CHILD_KEYS — они не должны
# попадать в `extra`, иначе продублируются при экспорте.

OPS = [('id', 'src_id', INT), ('name', 'name', STR), ('workshop', 'workshop', STR),
       ('type', 'op_type', STR), ('processType', 'process_type', STR),
       ('shiftDuration', 'shift_duration', INT, 480), ('frvMin', 'frv_min', INT)]

BASES = [('id', 'src_id', INT), ('name', 'name', STR)]

NOMENCLATURE = [('id', 'src_id', INT), ('articleGp', 'article_gp', STR),
                ('articleItem', 'article_item', STR), ('name', 'name', STR),
                ('model', 'model_code', STR), ('assortmentGroup', 'assortment_group', STR),
                ('normType', 'norm_type', STR), ('acFlag', 'ac_flag', BOOL),
                ('nomOps', 'nom_ops', JSON), ('nomWorkshops', 'nom_workshops', JSON)]
NOMENCLATURE_CHILD = ['route', 'routeOverrides']

NOM_ROUTE = [('stage', 'stage', STR), ('articleItem', 'article_item', STR),
             ('name', 'name', STR), ('timeCost', 'time_cost', NUM)]

CONTRACTS = [('id', 'src_id', INT), ('number', 'number', STR),
             ('articleGp', 'article_gp', STR), ('name', 'name', STR),
             ('quantity', 'quantity', NUM), ('deliveryDate', 'delivery_date', DATE),
             ('deliverySchedule', 'delivery_schedule', JSON),
             ('deadlines', 'deadlines', JSON),
             ('articleItems', 'contract_article_items', JSON)]

CUSTOMER_ORDERS = [('id', 'src_id', INT), ('number', 'number', STR),
                   ('contractId', 'contract_src_id', INT),
                   ('quantity', 'quantity', NUM), ('deliveryDate', 'delivery_date', DATE)]

ORDERS = [('id', 'src_id', INT), ('number', 'number', STR),
          ('articleGp', 'article_gp', STR), ('name', 'name', STR),
          ('quantity', 'quantity', NUM), ('contractNum', 'contract_num', STR),
          ('contractId', 'contract_src_id', INT), ('deliveryDate', 'delivery_date', DATE)]
ORDERS_CHILD = ['opRc', 'opAc', 'opSh', 'opU', 'deliveries']
STAGE_BY_OP_FIELD = {'opRc': 'РЦ', 'opAc': 'АЦ', 'opSh': 'ШЦ', 'opU': 'У'}

ORDER_DELIVERIES = [('id', 'src_id', INT), ('base', 'base', STR),
                    ('date', 'date', DATE), ('quantity', 'quantity', NUM)]

ORDER_LINKS = [('id', 'src_id', INT), ('customerOrderId', 'customer_order_src_id', INT),
               ('productionOrderId', 'production_order_src_id', INT), ('qty', 'qty', NUM)]

MACROPLAN = [('id', 'src_id', INT), ('contractId', 'contract_src_id', INT),
             ('articleItem', 'article_item', STR), ('op', 'op_name', STR),
             ('workshop', 'workshop', STR), ('month', 'month', STR),
             ('qtySew', 'qty_sew', NUM), ('qtyRc', 'qty_rc', NUM),
             ('qtyAc', 'qty_ac', NUM), ('qtyPack', 'qty_pack', NUM),
             ('volumeType', 'volume_type', STR, 'main'), ('eff', 'eff', NUM, 100),
             ('normOverride', 'norm_override', NUMN),
             ('macroReleaseOrder', 'macro_release_order', STR)]
MACROPLAN_CHILD = ['orderNums']

MACRO_EFF = [('id', 'src_id', INT), ('op', 'op_name', STR), ('workshop', 'workshop', STR),
             ('month', 'month', STR), ('eff', 'eff', NUM, 100)]

SCHEDULES = [('id', 'src_id', INT), ('name', 'name', STR), ('op', 'op_name', STR),
             ('workshop', 'workshop', STR), ('shiftTime', 'shift_time', INT, 480),
             ('activeMonths', 'active_months', JSON), ('graphPreset', 'graph_preset', STR),
             ('workDays', 'work_days', INT), ('restDays', 'rest_days', INT),
             ('cycleStart', 'cycle_start', DATE), ('skipWeekends', 'skip_weekends', BOOL),
             ('staffCount', 'staff_count', NUM), ('effPct', 'eff_pct', NUM, 100)]
SCHEDULES_CHILD = ['workers']

WORKERS = [('id', 'src_id', INT), ('name', 'name', STR), ('staffCount', 'staff_count', NUM),
           ('shiftTime', 'shift_time', INT, 480), ('effPct', 'eff_pct', NUM, 100),
           ('absence', 'absence', BOOL, True)]

SCHED_MONTH_OVR = [('scheduleId', 'schedule_src_id', INT), ('month', 'month', STR),
                   ('name', 'name', STR), ('shiftTime', 'shift_time', INT, 480),
                   ('effPct', 'eff_pct', NUM, 100)]

CAL_OVERRIDES = [('scheduleId', 'schedule_src_id', INT), ('date', 'date', DATE),
                 ('dayType', 'day_type', STR), ('staffCount', 'staff_count', NUMN)]

MANUAL_FRV = [('id', 'src_id', INT), ('op', 'op_name', STR), ('workshop', 'workshop', STR),
              ('month', 'month', STR), ('frvMin', 'frv_min', NUM),
              ('effPct', 'eff_pct', NUM, 100)]
MANUAL_FRV_CHILD = ['frvSegments']

# Вариант ФРВ: люди × смена × дней. Абсентеизм — поварианто, по умолчанию учитывается
# (`segAbsFactor`: снят только явным absence === false), поэтому default True, как у WORKERS.
MANUAL_FRV_SEGMENTS = [('id', 'src_id', INT), ('name', 'name', STR),
                       ('people', 'people', NUM), ('shiftMin', 'shift_min', INT),
                       ('workDays', 'work_days', NUM), ('effPct', 'eff_pct', NUM, 100),
                       ('absence', 'absence', BOOL, True)]

MICROPLAN = [('id', 'src_id', INT), ('date', 'date', DATE),
             ('scheduleId', 'schedule_src_id', INT), ('op', 'op_name', STR),
             ('workshop', 'workshop', STR), ('stage', 'stage', STR),
             ('releaseOrder', 'release_order', STR), ('launchOrder', 'launch_order', STR),
             ('articleItem', 'article_item', STR),
             ('planRelease', 'plan_release', NUM), ('planLaunch', 'plan_launch', NUM),
             ('factRelease', 'fact_release', NUM), ('factLaunch', 'fact_launch', NUM),
             ('planReleaseMain', 'plan_release_main', NUM),
             ('planReleaseExtra', 'plan_release_extra', NUM),
             ('planReleaseIsManual', 'plan_release_is_manual', BOOL),
             ('learningEff', 'learning_eff', NUM, 100),
             ('planByArticle', 'plan_by_article', JSON),
             ('contractId', 'contract_src_id', INT), ('comment', 'comment', STR)]
MICROPLAN_CHILD = ['articleItems', 'subOrders', 'launches', 'workers']

SUB_ORDERS = [('id', 'src_id', INT), ('releaseOrder', 'release_order', STR),
              ('launchOrder', 'launch_order', STR), ('articleItems', 'article_items', JSON),
              ('planRelease', 'plan_release', NUM),
              ('planReleaseMain', 'plan_release_main', NUM),
              ('planReleaseExtra', 'plan_release_extra', NUM),
              ('factRelease', 'fact_release', NUM), ('factLaunch', 'fact_launch', NUM),
              ('planReleaseIsManual', 'plan_release_is_manual', BOOL),
              ('learningEff', 'learning_eff', NUM, 100)]

LAUNCHES = [('id', 'src_id', INT), ('order', 'order_number', STR),
            ('articleItem', 'article_item', STR), ('qty', 'qty', NUM)]

MICRO_WORKERS = [('id', 'src_id', INT), ('name', 'name', STR),
                 ('staffCount', 'staff_count', NUM), ('attendance', 'attendance', NUMN),
                 ('shiftTime', 'shift_time', INT, 480), ('effPct', 'eff_pct', NUM, 100),
                 ('absence', 'absence', BOOL, True)]

PLAN_BASELINE = [('month', 'month', STR), ('date', 'date', DATE),
                 ('scheduleId', 'schedule_src_id', INT), ('op', 'op_name', STR),
                 ('workshop', 'workshop', STR), ('stage', 'stage', STR),
                 ('releaseOrder', 'release_order', STR),
                 ('articleItems', 'article_items', JSON),
                 ('planRelease', 'plan_release', NUM), ('snapAt', 'snap_at', STR)]

DELIVERY_MATRIX = [('id', 'src_id', INT), ('fromOp', 'from_op', STR),
                   ('toOp', 'to_op', STR), ('days', 'days', INT)]

HOLIDAYS = [('id', 'src_id', INT), ('date', 'date', DATE), ('name', 'name', STR)]


ABSENT_KEY = '__absent'   # ключей не было в записи вовсе
NULL_KEY = '__null'       # ключ был, но со значением null — это не то же самое


def _unpack(entry):
    """Строка карты: (js, col, kind) либо (js, col, kind, default)."""
    if len(entry) == 4:
        return entry
    js, col, kind = entry
    return js, col, kind, None


def split(record, field_map, child_keys=()):
    """Разбирает запись JSON: {колонка: значение} + остаток и список отсутствующих в extra."""
    known = {e[0] for e in field_map} | set(child_keys)
    fields, absent, nulls = {}, [], []
    for entry in field_map:
        js, col, kind, default = _unpack(entry)
        if js in record and record[js] is not None:
            fields[col] = to_db(record[js], kind)
        else:
            if js not in record:
                absent.append(js)
            else:
                nulls.append(js)
            fields[col] = to_db(default, kind) if default is not None else to_db(None, kind)
    extra = {k: v for k, v in record.items() if k not in known}
    if absent:
        extra[ABSENT_KEY] = absent
    if nulls:
        extra[NULL_KEY] = nulls
    return fields, extra


def build(obj, field_map):
    """Собирает запись JSON из объекта модели: колонки + extra, без отсутствовавших полей."""
    extra = dict(getattr(obj, 'extra', None) or {})
    absent = set(extra.pop(ABSENT_KEY, []) or [])
    nulls = set(extra.pop(NULL_KEY, []) or [])
    out = {}
    for entry in field_map:
        js, col, kind, _ = _unpack(entry)
        if js in absent:
            continue
        out[js] = None if js in nulls else to_json(getattr(obj, col), kind)
    out.update(extra)
    return out
