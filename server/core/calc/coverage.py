# -*- coding: utf-8 -*-
"""Покрытие и остаток заказа. Порт группы 6 из ТЗ §4.

Здесь же снимается главная проблема производительности. В JS-версии
`orderStagePlannedQty` при каждом вызове перебирает весь `S.microplan`, а
вызывается по несколько раз на строку — отсюда квадратичный рост полного
пересчёта (9,2 с на 2520 строк, ТЗ §8). Здесь выборка идёт по индексу
`(заказ, передел)`, построенному один раз при сборке среза.

Правило метрики (CLAUDE.md §4.7): **прошлое считаем по факту, будущее — по плану.**
Граница проходит там, где в смену введён факт. Одна точка ответа на вопрос
«сколько покрыто» — `covered_before`; менять метрику только здесь.
"""
from __future__ import annotations

from .snapshot import Snapshot, num, order_key

# Метрики. Совпадают по именам с JS, чтобы эталоны читались без перевода.
PLAN = 'plan'
FACT = 'fact'
FACT_OR_PLAN = 'factOrPlan'
MAX_FACT_PLAN = 'maxFactPlan'


def _value(obj: dict, metric: str) -> float:
    plan = num(obj.get('planRelease'))
    fact = num(obj.get('factRelease'))
    if metric == MAX_FACT_PLAN:
        return max(fact, plan)
    if metric == FACT_OR_PLAN:
        # Смена отработана (факт введён) → считаем по факту; иначе по плану.
        return fact if fact > 0 else plan
    if metric == FACT:
        return fact
    return plan


def order_stage_qty(snap: Snapshot, order_number, stage: str, base_date: str | None = None, *,
                    metric: str = PLAN, when: str = 'all',
                    exclude_row_id=None, exclude_sub_id=None,
                    skip_manual_main: bool = False,
                    article_item: str | None = None) -> float:
    """Порт `orderStagePlannedQty`.

    Сумма по заказу+переделу из микроплана, ВКЛЮЧАЯ подзаказы других строк —
    иначе остаток не учитывал бы «второй заказ в смене» (CLAUDE.md §4.5).

    `exclude_row_id` исключает строку ЦЕЛИКОМ (main вместе с её подзаказами):
    вклад своей строки вызывающий добавляет отдельно, иначе был бы двойной счёт.
    """
    total = 0.0
    for entry in snap.order_stage_entries(order_number, stage):
        row, obj, date, is_sub = entry['row'], entry['obj'], entry['date'], entry['is_sub']

        if when == '<' and not (date < (base_date or '')):
            continue
        if when == '<=' and not (date <= (base_date or '')):
            continue
        if when == '==' and date != (base_date or ''):
            continue

        if exclude_row_id is not None and row.get('id') == exclude_row_id:
            continue

        if is_sub:
            if exclude_sub_id is not None and obj.get('id') == exclude_sub_id:
                continue
        else:
            # Подзаказы всегда авто, пропуск ручных касается только main-строк.
            if skip_manual_main and row.get('planReleaseIsManual'):
                continue

        if article_item is not None:
            items = obj.get('articleItems') or []
            if obj.get('articleItem') != article_item and article_item not in items:
                continue

        total += _value(obj, metric)
    return total


def covered_before(snap: Snapshot, order_number, stage: str, base_date: str | None, **opts) -> float:
    """Порт `orderCoveredBefore` — сколько по заказу+переделу покрыто ДО даты.

    Единственная точка ответа на этот вопрос. В JS до июля 2026 метрику задавали
    в каждом месте отдельно, и они разошлись: подсказка считала по чистому плану,
    ядро — по max(факт, план). При перевыполнении ядро считало заказ выбранным и
    ставило план 0, а подсказка показывала живой остаток.
    """
    opts.pop('metric', None)
    opts.pop('when', None)
    return order_stage_qty(snap, order_number, stage, base_date,
                           metric=FACT_OR_PLAN, when='<', **opts)


def remaining_to_plan(snap: Snapshot, order_number, stage: str, base_date: str | None, *,
                      exclude_row_id=None) -> float:
    """Остаток заказа к планированию: количество − покрыто ранее − запланировано в ту же дату.

    Это то, что показывает подсказка «≤N (ЗАК)» и чем ограничивает себя авто-план.
    """
    order = snap.order_by_number(order_number)
    if not order:
        return 0.0
    qty = num(order.get('quantity'))
    if qty <= 0:
        return 0.0
    before = covered_before(snap, order_number, stage, base_date, exclude_row_id=exclude_row_id)
    same_day = order_stage_qty(snap, order_number, stage, base_date,
                               metric=PLAN, when='==', exclude_row_id=exclude_row_id,
                               skip_manual_main=True)
    return max(0.0, qty - before - same_day)
