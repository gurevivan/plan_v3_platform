# -*- coding: utf-8 -*-
"""Авто-план микроплана. Порт группы 5 из ТЗ §4.

Модель расчёта (CLAUDE.md §4.4): у бригады-дня есть общий пул физических минут;
строки и подзаказы его делят. Ручные строки идут первыми — они фиксируют свой
кусок пула, авто-строки делят остаток.

**План НЕ реагирует на факт.** Перевыполнение видно как факт больше плана, план
остаётся чистым. Это сознательное решение, а не недоделка: авто-план не двигает
строки от факта, меняется только то, как считается остаток заказа (§4.7).

⚠️ В оригинале ДВЕ РАЗНЫЕ формулы множителя эффективности освоения:

    brigadeDayUsedByOthersRawMin:  Math.max(0.01, (x ?? 100) / 100)
    _recalcPlanReleaseCore:        Math.max(0.01, (x ?? 100)) / 100

Деление стоит в разных местах относительно `max`. При обычных значениях (целые
проценты от 1 и выше) обе дают x/100, поэтому расхождение незаметно. Разойдутся
они только на значениях от 0 до 1: при x=0 первая даёт 0.01, вторая 0.0001.
Порт воспроизводит КАЖДОЕ место как есть — задача паритета повторить поведение,
а не причесать его. Если решите унифицировать, это правка в JS-оригинале, и
эталон придётся переснять.
"""
from __future__ import annotations

from .capacity import (brigade_day_group_rows, brigade_day_pool_raw_min,
                       brigade_day_used_by_others_raw_min, micro_row_eff_pct)
from .coverage import PLAN, covered_before, order_stage_qty
from .norms import micro_row_article_items, tc_for_micro_row, tc_for_sub_order
from .snapshot import Snapshot, num


def js_round(x: float) -> int:
    """Округление как `Math.round` в JS: половина всегда ВВЕРХ, в том числе у отрицательных.

    Python `round()` использует банковское округление (round-half-to-even):
    round(0.5) == 0, round(2.5) == 2. Для плана это дало бы расхождение на штуку.
    """
    import math
    return math.floor(x + 0.5)


def core_learning_factor(obj: dict) -> float:
    """Множитель освоения в ЯДРЕ: `Math.max(0.01, x) / 100`.

    Отличается от `capacity.learning_factor` местом деления — см. заголовок модуля.
    """
    val = obj.get('learningEff')
    if val is None:
        val = 100
    return max(0.01, num(val)) / 100.0


def unique_article_items(row: dict) -> list[str]:
    """Уникальные артикулы строки с сохранением порядка (порт `[...new Set(...)]`)."""
    seen, out = set(), []
    for ai in micro_row_article_items(row):
        if ai not in seen:
            seen.add(ai)
            out.append(ai)
    return out


def macro_row_kit_quantity(row: dict) -> float:
    """Порт `macroRowKitQuantity`."""
    if row.get('qtySew') not in (None, ''):
        return max(0.0, num(row.get('qtySew')))
    return max(0.0, num(row.get('quantity')))


def _order_remaining(snap: Snapshot, row: dict, order_number, stage: str,
                     article_item: str | None = None) -> float | None:
    """Остаток заказа для лимита 6a. None — если заказа нет или количество не задано."""
    order = snap.order_by_number(order_number)
    if not order or num(order.get('quantity')) <= 0:
        return None
    qty = num(order.get('quantity'))
    if article_item is not None:
        # Ветка нескольких артикулов: лимит применяется НЕЗАВИСИМО по каждому,
        # и план того же дня здесь не вычитается — так в оригинале.
        before = covered_before(snap, order_number, stage, row.get('date'),
                                exclude_row_id=row.get('id'), article_item=article_item)
        return max(0.0, qty - before)
    before = covered_before(snap, order_number, stage, row.get('date'),
                            exclude_row_id=row.get('id'))
    same_day = order_stage_qty(snap, order_number, stage, row.get('date'),
                               metric=PLAN, when='==', exclude_row_id=row.get('id'),
                               skip_manual_main=True)
    return max(0.0, qty - before - same_day)


def recalc_plan_release_core(snap: Snapshot, row: dict) -> None:
    """Порт `_recalcPlanReleaseCore` — расчёт одной строки и её подзаказов.

    Меняет строку на месте, как оригинал. У РУЧНОЙ строки план не трогает, но
    пересчитывает `planByArticle` и подзаказы.
    """
    if not row:
        return
    workers = row.get('workers') or []
    ais = unique_article_items(row)
    row['planByArticle'] = {}

    subs = row.get('subOrders') or []
    tc1 = sum(tc_for_micro_row(snap, row, ai) for ai in ais)
    tc_subs = [tc_for_sub_order(snap, sub, row.get('op'), row.get('stage')) for sub in subs]

    group = brigade_day_group_rows(snap, row)
    pool_raw = brigade_day_pool_raw_min(snap, group)
    used_others = brigade_day_used_by_others_raw_min(snap, row, group)
    avail_min = max(0.0, pool_raw - used_others)
    l_eff = core_learning_factor(row)
    effective_avail = avail_min * l_eff
    stage = row.get('stage') or 'ШЦ'
    is_manual = bool(row.get('planReleaseIsManual'))

    new_plan = 0

    if not workers or not ais:
        new_plan = 0

    elif subs and not is_manual:
        # Главный заказ при наличии подзаказов: мощность смены, но не больше остатка.
        new_plan = js_round(effective_avail / tc1) if tc1 > 0 and effective_avail > 0 else 0
        if row.get('releaseOrder'):
            rem = _order_remaining(snap, row, row['releaseOrder'], stage)
            if rem is not None:
                new_plan = min(new_plan, rem)
        for ai in ais:
            row['planByArticle'][ai] = new_plan

    elif len(ais) == 1:
        new_plan = js_round(effective_avail / tc1) if tc1 > 0 and effective_avail > 0 else 0
        if not is_manual and row.get('releaseOrder'):
            rem = _order_remaining(snap, row, row['releaseOrder'], stage)
            if rem is not None:
                new_plan = min(new_plan, rem)
        row['planByArticle'][ais[0]] = new_plan

    else:
        # Несколько артикулов: время делится пропорционально трудоёмкости,
        # лимит заказа применяется по каждому артикулу НЕЗАВИСИМО.
        for ai in ais:
            ai_tc = tc_for_micro_row(snap, row, ai)
            share_min = (ai_tc / tc1) * effective_avail if tc1 > 0 else 0.0
            ai_plan = js_round(share_min / ai_tc) if ai_tc > 0 else 0
            if not is_manual and row.get('releaseOrder'):
                rem = _order_remaining(snap, row, row['releaseOrder'], stage, article_item=ai)
                if rem is not None:
                    ai_plan = min(ai_plan, rem)
            row['planByArticle'][ai] = ai_plan
        new_plan = sum(row['planByArticle'].values())

    if not is_manual and num(row.get('planRelease')) != new_plan:
        row['planRelease'] = new_plan
        row['planLaunch'] = new_plan

    # Разбивку осн/доп убрали: основной = весь план, доп всегда 0.
    plan_now = num(row.get('planRelease'))
    row['planReleaseMain'] = plan_now
    row['planReleaseExtra'] = 0

    if subs:
        _recalc_sub_orders(snap, row, subs, tc_subs, tc1, l_eff, avail_min, stage)


def _recalc_sub_orders(snap: Snapshot, row: dict, subs: list[dict], tc_subs: list[float],
                       tc1: float, l_eff: float, avail_min: float, stage: str) -> None:
    """Подзаказы делят минуты, оставшиеся после главного заказа, по порядку."""
    used_min = (num(row.get('planRelease')) * tc1 / l_eff) if tc1 > 0 else 0.0
    used_sub_min = 0.0

    for i, sub in enumerate(subs):
        sub_tc = tc_subs[i] if i < len(tc_subs) else 0.0
        sub_l_eff = core_learning_factor(sub)
        remain_min = max(0.0, avail_min - used_min - used_sub_min)
        sp = js_round(remain_min * sub_l_eff / sub_tc) if sub_tc > 0 and remain_min > 0 else 0

        if sub.get('releaseOrder') == '__extra__':
            sp = min(sp, _extra_volume_cap(snap, row, stage))
        elif sub.get('releaseOrder'):
            order = snap.order_by_number(sub['releaseOrder'])
            if order and num(order.get('quantity')) > 0:
                before = covered_before(snap, sub['releaseOrder'], stage, row.get('date'),
                                        exclude_row_id=row.get('id'))
                same_day = order_stage_qty(snap, sub['releaseOrder'], stage, row.get('date'),
                                           metric=PLAN, when='==', exclude_row_id=row.get('id'),
                                           skip_manual_main=True)
                # Вклад своей строки добавляем явно: её саму мы исключили целиком.
                parent = (num(row.get('planRelease'))
                          if row.get('releaseOrder') == sub['releaseOrder'] else 0.0)
                prev_subs = sum(num(s.get('planRelease')) for s in subs[:i]
                                if s.get('releaseOrder') == sub['releaseOrder'])
                sp = min(sp, max(0.0, num(order['quantity']) - before - same_day
                                 - parent - prev_subs))

        # Ручной план подзаказа фиксирован — авторасчёт его не перекрывает.
        if sub.get('planReleaseIsManual'):
            sp = num(sub.get('planRelease'))

        sub['planRelease'] = sp
        sub['planReleaseMain'] = sp
        sub['planReleaseExtra'] = 0
        used_sub_min += (sp * sub_tc / sub_l_eff) if sub_tc > 0 else 0.0


def _extra_volume_cap(snap: Snapshot, row: dict, stage: str) -> float:
    """Лимит подзаказа «Доп. объём» — остаток дополнительного объёма макроплана.

    ⚠️ Ни в боевых данных, ни в синтетике строк `__extra__` нет, поэтому ветка
    перенесена по коду, но эталоном НЕ покрыта.
    """
    macro_extra = 0.0
    for mr in snap.macroplan:
        if (mr.get('volumeType') or 'main') != 'extra':
            continue
        op_rec = snap._op_by_name_workshop.get((mr.get('op') or '', mr.get('workshop') or ''))
        row_stage = (op_rec.get('processType') if op_rec else None) or mr.get('workshop')
        if row_stage == stage:
            macro_extra += macro_row_kit_quantity(mr)

    already = 0.0
    for m in snap.microplan:
        if (m.get('stage') or 'ШЦ') != stage or m.get('id') == row.get('id'):
            continue
        if m.get('releaseOrder') == '__extra__':
            already += num(m.get('planRelease'))
        for s2 in (m.get('subOrders') or []):
            if s2.get('releaseOrder') == '__extra__':
                already += num(s2.get('planRelease'))
    return max(0.0, macro_extra - already)


def recalc_all_micro_plans(snap: Snapshot) -> None:
    """Порт `recalcAllMicroPlans` — пересчёт всей сетки.

    Порядок важен и воспроизводится точно: строки сортируются по дате, затем
    сбрасываются авто-планы (и подзаказы ВСЕХ строк, включая ручные — у ручной
    строки подзаказ всё равно авто), затем считаются СНАЧАЛА ручные строки
    (они резервируют пул), потом авто (делят остаток).
    """
    rows = sorted(snap.microplan, key=lambda r: str(r.get('date') or ''))

    for r in rows:
        if not r.get('planReleaseIsManual'):
            r['planRelease'] = 0
            r['planLaunch'] = 0
        for s in (r.get('subOrders') or []):
            s['planRelease'] = 0

    for r in rows:
        if r.get('planReleaseIsManual'):
            recalc_plan_release_core(snap, r)
    for r in rows:
        if not r.get('planReleaseIsManual'):
            recalc_plan_release_core(snap, r)


def recalc_plan_release(snap: Snapshot, row: dict) -> None:
    """Порт `recalcPlanRelease` — пересчёт одной бригады-дня.

    Это и есть частичный пересчёт, обязательный для сервера (ТЗ §8): трогается
    только группа, а не вся сетка.
    """
    if not row:
        return
    group = brigade_day_group_rows(snap, row)
    for r in group:
        if not r.get('planReleaseIsManual'):
            r['planRelease'] = 0
            r['planLaunch'] = 0
        for s in (r.get('subOrders') or []):
            if not s.get('planReleaseIsManual'):
                s['planRelease'] = 0

    ordered = sorted(group, key=lambda r: (0 if r.get('planReleaseIsManual') else 1,
                                           num(r.get('id'))))
    for r in ordered:
        recalc_plan_release_core(snap, r)
