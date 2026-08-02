# -*- coding: utf-8 -*-
"""Нормы трудоёмкости (мин/шт). Порт группы 3 из ТЗ §4.

Два инварианта, которые нельзя «улучшать» при переносе:

1. **Норма берётся по ПЕРЕДЕЛУ строки, а не по первому совпадению артикула**
   (CLAUDE.md §4.2). Один артикул встречается на нескольких переделах: целый
   костюм и в ШЦ, и в У. Для упаковки нужна норма У, а не ШЦ.

2. **Норма РЦ и ШЦ индивидуальна по заказу** (CLAUDE.md §4.3). Приоритет:
   строка макроплана с привязанным заказом → override по заказу → норма стадии →
   общая норма артикула. «Спрямлять» на все заказы артикула нельзя — это
   сознательное требование, а не недоделка.
"""
from __future__ import annotations

from .routes import STAGE_ORDER, all_routes_of, nom_row_ops, route_for_op
from .snapshot import Snapshot, num, order_key

STAGE_NAMES = set(STAGE_ORDER)  # порт константы STAGES


def stage_norm_override(snap: Snapshot, stage: str, order_number) -> float | None:
    """Порт `stageNormOverride`: РЦ → tcRcOverrides, ШЦ → tcShOverrides, иначе None."""
    key = order_key(order_number)
    if not key or key == '__extra__':
        return None
    if stage == 'РЦ':
        val = snap.tc_rc_overrides.get(key)
        return None if val is None else num(val)
    if stage == 'ШЦ':
        val = snap.tc_sh_overrides.get(key)
        return None if val is None else num(val)
    return None


def stage_of_article_item(snap: Snapshot, article_item: str) -> str:
    """Порт `stageOfArticleItem`: передел первой строки маршрута с этим артикулом."""
    for nom in snap.nomenclature:
        for item in all_routes_of(nom):
            if item.get('articleItem') == article_item:
                return item.get('stage') or 'ШЦ'
    return 'ШЦ'


def stage_of_macro_row(snap: Snapshot, row: dict) -> str:
    """Порт `stageOfMacroRow` — передел строки макро/микроплана.

    Приоритет намеренно НЕ начинается с поля `stage`: пара (ОП, цех) надёжнее.
    Один артикул встречается на разных переделах — целый костюм и в ШЦ, и в У, —
    и именно цех говорит, о каком переделе речь. Брать `row['stage']` первым
    (как напрашивается) даёт неверную норму: на боевых данных это разошлось на
    24 строках, 1.05 вместо 1.35.

    1) точная пара (ОП, цех) из справочника → processType;
    2) сам цех, если назван кодом передела;
    3) поле `stage`, если это валидный передел;
    4) ОП без цеха → processType;
    5) передел артикула из маршрута.
    """
    if not row:
        return 'ШЦ'
    op_name, workshop = row.get('op'), row.get('workshop')

    if op_name and workshop:
        exact = snap._op_by_name_workshop.get((op_name, workshop))
        if exact and exact.get('processType'):
            return exact['processType']

    if workshop and workshop in STAGE_NAMES:
        return workshop

    if row.get('stage') and row['stage'] in STAGE_NAMES:
        return row['stage']

    if op_name:
        by_op = snap._op_by_name.get(op_name)
        if by_op and by_op.get('processType'):
            return by_op['processType']

    return stage_of_article_item(snap, row.get('articleItem') or '')


def norm_from_macro_for_order(snap: Snapshot, order_number, article_item: str,
                              stage: str) -> float | None:
    """Порт `normFromMacroForOrder`.

    Норма из строки макроплана, у которой этот заказ привязан в `orderNums`.
    Отсюда следствие, которое легко упустить: **без привязки заказа норма из
    макроплана до микроплана не доходит**.
    """
    if not order_number:
        return None
    key = order_key(order_number)
    for row in snap.macroplan:
        if row.get('normOverride') is None:
            continue
        nums = row.get('orderNums')
        if not isinstance(nums, list) or not any(order_key(n) == key for n in nums):
            continue
        if row.get('articleItem') != article_item:
            continue
        if stage_of_macro_row(snap, row) != stage:
            continue
        return num(row['normOverride'])
    return None


def stage_time_cost_for(snap: Snapshot, article_item: str, stage: str,
                        op: str | None = None) -> float | None:
    """Порт `stageTimeCostFor` — норма артикула на конкретном переделе.

    Шаг 1: строка маршрута с этим артикулом И этим переделом.
    Шаг 2: если артикул есть в маршруте на ДРУГОМ переделе — берём строку нужного
    передела той же карточки (0, если её нет). Именно этот шаг даёт упаковке норму У.
    """
    if not article_item or not stage:
        return None

    for nom in snap.nomenclature:
        route = route_for_op(nom, op) if op else all_routes_of(nom)
        for item in route:
            if item.get('articleItem') == article_item and item.get('stage') == stage:
                return max(0.0, num(item.get('timeCost')))

    for nom in snap.nomenclature:
        route = route_for_op(nom, op) if op else all_routes_of(nom)
        if not any(item.get('articleItem') == article_item for item in route):
            continue
        stage_row = next((i for i in route if i.get('stage') == stage), None)
        return max(0.0, num(stage_row.get('timeCost'))) if stage_row else 0.0
    return None


def time_cost_of(snap: Snapshot, article_item: str, op: str | None = None) -> float:
    """Порт `timeCostOf` — первая попавшаяся норма артикула в маршруте.

    Запасной вариант, когда передел определить не удалось. Карточки, привязанные
    к другим ОП, пропускаются.
    """
    for nom in snap.nomenclature:
        ops = nom_row_ops(nom)
        if op and ops and op not in ops:
            continue
        for item in route_for_op(nom, op):
            if item.get('articleItem') == article_item:
                return num(item.get('timeCost'))
    return 0.0


def tc_for_article(snap: Snapshot, article_item: str, order_number, stage: str,
                   op: str | None = None) -> float:
    """Единый приоритет нормы для пары «артикул + заказ + передел».

    Общая часть `tcForMicroRow` и `tcForSubOrder`: в JS этот приоритет выписан в
    двух местах, здесь — в одном, иначе они рано или поздно разойдутся.
    """
    key = order_key(order_number)
    real_order = bool(key) and key != '__extra__'

    if real_order:
        tc = norm_from_macro_for_order(snap, key, article_item, stage)
        if tc is not None:
            return tc
        ov = stage_norm_override(snap, stage, key)
        if ov is not None:
            return ov

    by_stage = stage_time_cost_for(snap, article_item, stage, op)
    return by_stage if by_stage is not None else time_cost_of(snap, article_item, op)


def micro_row_article_items(row: dict) -> list[str]:
    """Порт `microRowArticleItems`."""
    if not row:
        return []
    items = row.get('articleItems')
    if isinstance(items, list) and items:
        return [i for i in items if i]
    if row.get('articleItem'):
        return [row['articleItem']]
    return []


def tc_for_micro_row(snap: Snapshot, row: dict, article_item: str | None = None) -> float:
    """Порт `tcForMicroRow` — норма для строки микроплана."""
    ai = article_item or row.get('articleItem') or ''
    stage = stage_of_macro_row(snap, row)
    return tc_for_article(snap, ai, row.get('releaseOrder'), stage, row.get('op'))


def tc_for_sub_order(snap: Snapshot, sub: dict, op: str | None = None,
                     stage: str | None = None) -> float:
    """Порт `tcForSubOrder` — суммарная норма подзаказа по всем его артикулам.

    Считать подзаказ через общую функцию по списку артикулов нельзя: у подзаказа
    свой заказ, а значит и своя норма (CLAUDE.md §4.5).
    """
    items = sub.get('articleItems') if isinstance(sub.get('articleItems'), list) else []
    if not items:
        return 0.0
    st = stage or 'ШЦ'
    return sum(tc_for_article(snap, ai, sub.get('releaseOrder'), st, op) for ai in items)
