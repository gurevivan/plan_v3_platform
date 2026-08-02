# -*- coding: utf-8 -*-
"""Аналитика. Порт группы 8 из ТЗ §4.

Переносится РАСЧЁТНАЯ часть `renderAnalytics` — фильтры и агрегаты. Вёрстка
остаётся во фронте: по решению из ТЗ §7 интерфейс не переписывается, он лишь
получает данные от сервера.

Три вещи, которые нельзя упростить при переносе:

**Комплектность.** Штуки готовой продукции — это не сумма строк. Целые изделия
складываются, узлы (`/1`, `/2`, `/3`) берутся по МИНИМУМУ: собрать комплект можно
столько раз, сколько есть самой дефицитной части. Считает `kit_qty_from_entries`.

**Передел агрегации.** Без фильтра по переделу берётся САМЫЙ ГЛУБОКИЙ передел, по
которому в месяце есть данные, — это и есть готовая продукция. Складывать все
переделы нельзя: у изделия «целиком» (случай ОП-5) артикул ГП стоит и на ШЦ, и
на У, и факт удвоился бы.

**Минуты аддитивны, штуки — нет.** Поэтому в минутах суммируются все строки
макроплана, а в штуках — только строки передела агрегации, и через комплектность.
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, field

from .norms import (micro_row_article_items, stage_of_macro_row, stage_time_cost_for)
from .routes import all_routes_of
from .snapshot import Snapshot, num, order_key, orders_eq

STAGES_A = ['РЦ', 'АЦ', 'ШЦ', 'У']


@dataclass
class Filters:
    """Фильтры вкладки аналитики. Пустое значение — «не фильтровать»."""
    month: str = ''
    op: str = ''
    stage: str = ''
    brigade_id: str = ''
    contract: str = ''
    assortment: str = ''
    article_gp: str = ''
    unit: str = 'qty'          # 'qty' — штуки, 'min' — минуты


def article_gp_for(snap: Snapshot, article_item: str) -> str:
    """Порт `articleGpForMacroArticleItem` — ГП, которому принадлежит артикул."""
    for nom in snap.nomenclature:
        if nom.get('articleGp') == article_item:
            return nom['articleGp']
        if any(i.get('articleItem') == article_item for i in all_routes_of(nom)):
            return nom.get('articleGp') or article_item
    return article_item


def kit_qty_from_entries(snap: Snapshot, entries: list[tuple[str, float]],
                         gp_set: set[str]) -> float:
    """Порт `kitQtyFromEntries` — количество готовой продукции в комплектах.

    Целые изделия суммируются, узлы одного ГП сводятся по МИНИМУМУ. Без строки ГП
    остаётся только минимум по узлам — один контур комплектации.
    """
    whole_by_gp: dict[str, float] = {}
    parts_by_gp: dict[str, dict[str, float]] = {}
    for article_item, qty in entries:
        ai = article_item or ''
        gp = article_gp_for(snap, ai)
        if ai in gp_set:
            whole_by_gp[gp] = whole_by_gp.get(gp, 0.0) + qty
        else:
            parts_by_gp.setdefault(gp, {})
            parts_by_gp[gp][ai] = parts_by_gp[gp].get(ai, 0.0) + qty

    total = 0.0
    for gp in set(whole_by_gp) | set(parts_by_gp):
        whole = whole_by_gp.get(gp, 0.0)
        parts = parts_by_gp.get(gp)
        part_kits = min(parts.values()) if parts else 0.0
        total += whole + part_kits
    return total


def macro_row_kit_quantity(row: dict) -> float:
    """Порт `macroRowKitQuantity`."""
    if row.get('qtySew') not in (None, ''):
        return max(0.0, num(row.get('qtySew')))
    return max(0.0, num(row.get('quantity')))


def stage_kit_qty_for_rows(snap: Snapshot, rows: list[dict]) -> float:
    """Порт `stageKitQtyForRows` — комплекты по строкам макроплана."""
    gp_set = {n['articleGp'] for n in snap.nomenclature if n.get('articleGp')}
    return kit_qty_from_entries(
        snap, [(r.get('articleItem') or '', macro_row_kit_quantity(r)) for r in rows], gp_set)


def macro_row_labor_minutes(snap: Snapshot, row: dict) -> float:
    """Порт `macroRowLaborMinutes` — трудоёмкость строки макроплана, мин.

    Норма берётся по ПЕРЕДЕЛУ строки, а не по первому совпадению артикула
    (CLAUDE.md §4.2). Приоритет: ручное переопределение строки → РЦ-override по
    заказу (только на РЦ) → норма передела из маршрута.
    """
    if not row:
        return 0.0
    qty = macro_row_kit_quantity(row)
    if qty <= 0:
        return 0.0
    stage = stage_of_macro_row(snap, row)

    norm = None
    if row.get('normOverride') not in (None, ''):
        norm = num(row['normOverride'])
    if norm is None and stage == 'РЦ' and row.get('macroReleaseOrder'):
        ov = snap.tc_rc_overrides.get(order_key(row['macroReleaseOrder']))
        if ov is not None:
            norm = num(ov)
    if norm is None:
        sn = stage_time_cost_for(snap, row.get('articleItem'), stage, row.get('op'))
        norm = sn if sn is not None else 0.0
    return qty * norm


# ── Фильтры ─────────────────────────────────────────────────────────────────

def assortment_of_row(snap: Snapshot, row: dict) -> str:
    """Ассортиментная группа строки — по ГП её артикула."""
    ais = micro_row_article_items(row)
    ai = ais[0] if ais else (row.get('articleItem') or '')
    nom = snap.nom_by_gp(article_gp_for(snap, ai))
    return (nom.get('assortmentGroup') or '') if nom else ''


def _contract_sets(snap: Snapshot, contract_number: str):
    """Множества id контрактов и номеров заказов под выбранный контракт."""
    if not contract_number:
        return None, None
    ids = {num(c.get('id')) for c in snap.contracts
           if c.get('number') == contract_number}
    order_nums = {order_key(o.get('number')) for o in snap.orders
                  if o.get('contractNum') == contract_number}
    return ids, order_nums


def micro_row_matches(snap: Snapshot, row: dict, f: Filters,
                      contract_ids=None, contract_orders=None) -> bool:
    """Порт `rowMatches` — подходит ли строка микроплана под фильтры."""
    if f.op and row.get('op') != f.op:
        return False
    if f.brigade_id and str(row.get('scheduleId')) != str(f.brigade_id):
        return False
    if f.stage and stage_of_macro_row(snap, row) != f.stage:
        return False
    if f.article_gp and not any(article_gp_for(snap, ai) == f.article_gp
                                for ai in micro_row_article_items(row)):
        return False
    if f.assortment and assortment_of_row(snap, row) != f.assortment:
        return False
    if f.contract:
        by_id = contract_ids is not None and num(row.get('contractId')) in contract_ids
        by_order = (row.get('releaseOrder') and contract_orders is not None
                    and order_key(row['releaseOrder']) in contract_orders)
        if not by_id and not by_order:
            return False
    return True


def macro_row_matches(snap: Snapshot, row: dict, f: Filters,
                      contract_ids=None, brigade_op: str = '') -> bool:
    """Порт `macroRowMatches`.

    Макроплан не привязан к бригаде, поэтому фильтр по бригаде согласуется через
    ОП этой бригады.
    """
    if f.op and row.get('op') != f.op:
        return False
    if brigade_op and row.get('op') != brigade_op:
        return False
    if f.contract and not (contract_ids is not None and num(row.get('contractId')) in contract_ids):
        return False
    if f.article_gp and article_gp_for(snap, row.get('articleItem')) != f.article_gp:
        return False
    if f.stage and stage_of_macro_row(snap, row) != f.stage:
        return False
    if f.assortment and assortment_of_row(snap, row) != f.assortment:
        return False
    return True


# ── Значения строки ─────────────────────────────────────────────────────────

def row_plan(row: dict) -> float:
    """План строки микроплана вместе с подзаказами."""
    return num(row.get('planRelease')) + sum(num(s.get('planRelease'))
                                             for s in (row.get('subOrders') or []))


def row_fact(row: dict) -> float:
    """Факт строки микроплана вместе с подзаказами."""
    return num(row.get('factRelease')) + sum(num(s.get('factRelease'))
                                             for s in (row.get('subOrders') or []))


# ── Агрегация ───────────────────────────────────────────────────────────────

def pick_agg_stage(snap: Snapshot, macro_rows: list[dict], micro_rows: list[dict],
                   stage_filter: str = '') -> str:
    """Передел агрегации: выбранный в фильтре, иначе самый глубокий с данными.

    Перебор идёт РЦ→АЦ→ШЦ→У без выхода из цикла: остаётся последний найденный,
    то есть самый глубокий. Это и есть готовая продукция.
    """
    if stage_filter:
        return stage_filter
    agg = ''
    for s in STAGES_A:
        if any(stage_of_macro_row(snap, r) == s for r in macro_rows) \
           or any(stage_of_macro_row(snap, m) == s for m in micro_rows):
            agg = s
    return agg


def month_weeks(month: str) -> list[tuple[int, int]]:
    """Разбивка месяца на недели пн–вс, обрезанные границами месяца."""
    year, mon = (int(x) for x in month.split('-')[:2])
    days = monthrange(year, mon)[1]
    from datetime import date as _date
    weeks, start = [], 1
    while start <= days:
        dow = _date(year, mon, start).weekday()   # 0 — понедельник
        end = min(start + (6 - dow), days)
        weeks.append((start, end))
        start = end + 1
    return weeks


@dataclass
class MonthTotals:
    """Итоги месяца по выбранным фильтрам."""
    unit: str = 'qty'
    agg_stage: str = ''
    agg_stage_micro: str = ''
    macro_plan: float = 0.0
    micro_plan: float = 0.0
    micro_fact: float = 0.0
    rows_micro: int = 0
    rows_macro: int = 0
    by_date: dict = field(default_factory=dict)


def month_totals(snap: Snapshot, f: Filters) -> MonthTotals:
    """Итоги месяца: план макро, план и факт микро, разбивка по датам.

    Штуки считаются через комплектность на переделе агрегации; минуты — суммой,
    потому что труд аддитивен по переделам и узлам.
    """
    contract_ids, contract_orders = _contract_sets(snap, f.contract)
    brigade_op = ''
    if f.brigade_id:
        sched = next((s for s in snap.schedules
                      if str(s.get('id')) == str(f.brigade_id)), None)
        brigade_op = (sched.get('op') or '') if sched else ''

    macro_rows = [r for r in snap.macroplan
                  if r.get('month') == f.month
                  and macro_row_matches(snap, r, f, contract_ids, brigade_op)]
    micro_rows = [m for m in snap.microplan
                  if (m.get('date') or '').startswith(f.month)
                  and micro_row_matches(snap, m, f, contract_ids, contract_orders)]

    agg_stage = pick_agg_stage(snap, macro_rows, micro_rows, f.stage)
    # Для ШТУК микроплана берётся самый глубокий передел, реально присутствующий
    # в МИКРОПЛАНЕ: иначе при частичном плане (например, только РЦ) штуки вышли бы
    # нулём, хотя минуты есть.
    agg_stage_micro = f.stage
    if not agg_stage_micro:
        for s in STAGES_A:
            if any(stage_of_macro_row(snap, m) == s for m in micro_rows):
                agg_stage_micro = s
        if not agg_stage_micro:
            agg_stage_micro = agg_stage

    res = MonthTotals(unit=f.unit, agg_stage=agg_stage, agg_stage_micro=agg_stage_micro,
                      rows_micro=len(micro_rows), rows_macro=len(macro_rows))

    if f.unit == 'min':
        res.macro_plan = round(sum(macro_row_labor_minutes(snap, r) for r in macro_rows))
    else:
        at_stage = [r for r in macro_rows
                    if not agg_stage or stage_of_macro_row(snap, r) == agg_stage]
        res.macro_plan = stage_kit_qty_for_rows(snap, at_stage)

    gp_set = {n['articleGp'] for n in snap.nomenclature if n.get('articleGp')}
    micro_at_stage = [m for m in micro_rows
                      if not agg_stage_micro or stage_of_macro_row(snap, m) == agg_stage_micro]

    if f.unit == 'min':
        from .norms import tc_for_micro_row
        for m in micro_rows:
            tc = sum(tc_for_micro_row(snap, m, ai)
                     for ai in dict.fromkeys(micro_row_article_items(m)))
            res.micro_plan += num(m.get('planRelease')) * tc
            res.micro_fact += num(m.get('factRelease')) * tc
        res.micro_plan = round(res.micro_plan)
        res.micro_fact = round(res.micro_fact)
    else:
        res.micro_plan = kit_qty_from_entries(
            snap, [(ai, row_plan(m)) for m in micro_at_stage
                   for ai in (micro_row_article_items(m) or [''])[:1]], gp_set)
        res.micro_fact = kit_qty_from_entries(
            snap, [(ai, row_fact(m)) for m in micro_at_stage
                   for ai in (micro_row_article_items(m) or [''])[:1]], gp_set)

    by_date: dict[str, dict] = {}
    for m in micro_at_stage:
        d = m.get('date') or ''
        cell = by_date.setdefault(d, {'plan': 0.0, 'fact': 0.0})
        cell['plan'] += row_plan(m)
        cell['fact'] += row_fact(m)
    res.by_date = dict(sorted(by_date.items()))
    return res


def baseline_vs_fact(snap: Snapshot, month: str) -> dict:
    """Секция «План (изначальный) ↔ Факт» за месяц.

    `planBaseline` — снимок плана на 100 %, зафиксированный при ПЕРВОМ вводе факта
    в месяце. Сравнивать факт нужно именно с ним, а не с текущим планом: текущий
    уже мог измениться.
    """
    base_rows = [b for b in snap.plan_baseline if b.get('month') == month]
    micro_rows = [m for m in snap.microplan if (m.get('date') or '').startswith(month)]

    by_order: dict[tuple, dict] = {}
    for b in base_rows:
        key = (order_key(b.get('releaseOrder')), b.get('stage') or 'ШЦ')
        cell = by_order.setdefault(key, {'baseline': 0.0, 'fact': 0.0})
        cell['baseline'] += num(b.get('planRelease'))
    for m in micro_rows:
        key = (order_key(m.get('releaseOrder')), m.get('stage') or 'ШЦ')
        if key not in by_order:
            continue
        by_order[key]['fact'] += row_fact(m)

    total_base = sum(v['baseline'] for v in by_order.values())
    total_fact = sum(v['fact'] for v in by_order.values())
    return {
        'month': month,
        'rows': [{'order': k[0], 'stage': k[1], **v} for k, v in sorted(by_order.items())],
        'baseline': total_base,
        'fact': total_fact,
        'pct': (total_fact / total_base * 100) if total_base > 0 else 0.0,
    }
