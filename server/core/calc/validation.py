# -*- coding: utf-8 -*-
"""Проверки микроплана. Порт группы 7 из ТЗ §4.

Два уровня (CLAUDE.md §4.8), и это не косметика, а поведение интерфейса:

* **`stop`** — физическое нарушение или порча данных: нельзя сшить больше, чем
  накроено; выпуск больше суммы запусков; факт больше количества заказа. Почти
  всегда опечатка, поэтому ввод блокируется подтверждением.
* **`warn`** — расхождение планирования: план разошёлся с остатком заказа или с
  планом предыдущего передела. Это нормальная жизнь цеха (идём с опережением или
  отстаём), а не ошибка ввода. Не блокирует, показывается жёлтым индикатором.

**Правило: проверки ФАКТА — stop, проверки ПЛАНА — warn.**

Возвращается `Verdict`: `ok`, `severity`, `msg` и числа для индикатора. Текст
сообщения повторяет оригинал — он показывается пользователю, и расхождение в
формулировке было бы заметно сразу.

Перенесено: `validateStageFact`, `validateFactVsOrderQty`,
`validateReleaseVsLaunches`, `validatePlanOrderLimit`.
Ещё НЕ перенесено: `validateStageLaunch` (102 строки),
`validatePlanVsPrevStagePlan` (79 строк, включая ветку «готовность кроя» для
режима «От ШЦ»), `checkDeliveryDate`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .norms import micro_row_article_items
from .routes import prev_stage_in_route, route_for_op
from .snapshot import Snapshot, num, order_key, orders_eq

STOP = 'stop'
WARN = 'warn'


@dataclass
class Verdict:
    """Результат проверки. `ok=True` — нарушения нет."""
    ok: bool = True
    severity: str | None = None
    msg: str = ''
    limit: float | None = None
    available: float | None = None
    already_used: float | None = None
    prev_stage: str | None = None
    extra: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok


OK = Verdict()


def _fmt(x: float) -> str:
    """Числа в сообщениях — как в JS: целые без дробной части."""
    return str(int(x)) if float(x).is_integer() else str(x)


# ── Факт по заказу и переделу ───────────────────────────────────────────────

def fact_by_order_stage_article(snap: Snapshot, order_number, stage: str,
                                article_item: str | None = None,
                                exclude_row_id=None, max_date: str | None = None) -> float:
    """Порт `factByOrderStageArticle` и `…UpToDate` (одна функция с `max_date`).

    Считает и main-строки, и подзаказы. `article_item=None` — по всем артикулам
    (так считается упаковка: она идёт комплектом).
    """
    total = 0.0
    for row in snap.microplan:
        if row.get('stage') != stage:
            continue
        if max_date and row.get('date') and row['date'] > max_date:
            continue
        if exclude_row_id is not None and row.get('id') == exclude_row_id:
            # В оригинале исключение строки применяется ТОЛЬКО к main-части;
            # подзаказы этой же строки продолжают считаться.
            pass
        else:
            if orders_eq(row.get('releaseOrder'), order_number):
                ais = micro_row_article_items(row)
                if not article_item or article_item in ais:
                    total += num(row.get('factRelease'))
        for sub in (row.get('subOrders') or []):
            if orders_eq(sub.get('releaseOrder'), order_number):
                sub_ais = sub.get('articleItems') or []
                if not article_item or article_item in sub_ais:
                    total += num(sub.get('factRelease'))
    return total


def stage_fact_limit(snap: Snapshot, order_number, article_gp: str, current_stage: str,
                     article_item: str, op: str | None = None) -> dict | None:
    """Порт `stageFactLimit` — сколько физически можно выпустить на переделе.

    Первый передел маршрута ограничений сверху не имеет → None.

    Упаковка считается КОМПЛЕКТОМ: если на предыдущем переделе несколько частей
    (куртка, брюки, рубашка), лимит — минимум по частям, а не сумма. Упаковать
    можно столько комплектов, сколько собрано самой дефицитной части.
    """
    if not order_number or not article_gp or not current_stage:
        return None

    prev = prev_stage_in_route(snap, article_gp, current_stage, op)
    if not prev:
        return None

    nom = snap.nom_by_gp(article_gp)
    if not nom:
        return None

    if current_stage == 'У':
        # op falsy → базовый маршрут: один связный набор частей.
        route = route_for_op(nom, op)
        sew_items = [i for i in route if i.get('stage') == prev]
        if not sew_items:
            return None
        if len(sew_items) == 1:
            fact = fact_by_order_stage_article(snap, order_number, prev,
                                               sew_items[0].get('articleItem'))
            return {'limit': fact, 'prevStage': prev,
                    'detail': f'{prev}: выпущено {_fmt(fact)} шт'}
        parts = []
        for item in sew_items:
            fact = fact_by_order_stage_article(snap, order_number, prev,
                                               item.get('articleItem'))
            parts.append((item.get('name') or item.get('articleItem'), fact))
        min_fact = min(f for _, f in parts)
        detail = ', '.join(f'{n}: {_fmt(f)} шт' for n, f in parts)
        return {'limit': min_fact, 'prevStage': prev,
                'detail': f'Комплект = min({detail}) = {_fmt(min_fact)} шт'}

    prev_items = [i for i in route_for_op(nom, op) if i.get('stage') == prev]
    if not prev_items:
        return None
    prev_fact = sum(fact_by_order_stage_article(snap, order_number, prev, i.get('articleItem'))
                    for i in prev_items)
    return {'limit': prev_fact, 'prevStage': prev,
            'detail': f'{prev}: выпущено {_fmt(prev_fact)} шт'}


# ── Проверки ФАКТА (severity = stop) ────────────────────────────────────────

def validate_stage_fact(snap: Snapshot, row: dict, new_fact, exclude_row_id=None) -> Verdict:
    """Порт `validateStageFact` — нельзя выпустить больше, чем поступило с предыдущего передела.

    Две проверки: суммарный лимит передела и последовательность по датам
    (нельзя выпустить сегодня то, что на предыдущем переделе сделают завтра).
    """
    if not row or not row.get('releaseOrder'):
        return OK
    order = snap.order_by_number(row['releaseOrder'])
    if not order:
        return OK

    stage = row.get('stage') or 'ШЦ'
    article_gp = order.get('articleGp')
    ais = micro_row_article_items(row)
    primary_ai = ais[0] if ais else (row.get('articleItem') or '')

    info = stage_fact_limit(snap, row['releaseOrder'], article_gp, stage, primary_ai, row.get('op'))
    if not info:
        return OK

    # Упаковка считается комплектом — по всем артикулам сразу.
    # ⚠️ Эталоном НЕ различается: отличие от `primary_ai` проявляется только если у
    # одного заказа на упаковке есть строки с РАЗНЫМИ артикулами, а маршрут даёт
    # для У один артикул. Ветка защитная; подгонять фикстуру под неё не стал.
    ai_filter = None if stage == 'У' else primary_ai
    already = fact_by_order_stage_article(snap, row['releaseOrder'], stage, ai_filter,
                                          exclude_row_id=exclude_row_id)
    available = max(0.0, info['limit'] - already)
    val = num(new_fact)

    if val > available:
        return Verdict(
            ok=False, severity=STOP,
            msg=(f'Превышен лимит передела {stage}!\n\n'
                 f"{info['detail']}\n"
                 f'Уже выпущено на {stage}: {_fmt(already)} шт\n'
                 f'Доступно к выпуску: {_fmt(available)} шт\n'
                 f'Вы пытаетесь ввести: {_fmt(val)} шт'),
            limit=info['limit'], available=available, already_used=already,
            prev_stage=info['prevStage'])

    # Последовательность по датам.
    if val > 0 and row.get('date') and info['prevStage']:
        nom = snap.nom_by_gp(article_gp)
        if nom:
            prev_items = [i for i in route_for_op(nom, row.get('op'))
                          if i.get('stage') == info['prevStage']]
            if stage == 'У' and len(prev_items) > 1:
                per_item = [fact_by_order_stage_article(snap, row['releaseOrder'],
                                                        info['prevStage'], i.get('articleItem'),
                                                        max_date=row['date'])
                            for i in prev_items]
                prev_fact_by_date = min(per_item)
            else:
                prev_fact_by_date = sum(
                    fact_by_order_stage_article(snap, row['releaseOrder'], info['prevStage'],
                                                i.get('articleItem'), max_date=row['date'])
                    for i in prev_items)

            cur_row = next((m for m in snap.microplan if m.get('id') == exclude_row_id), None)
            own_fact = num(cur_row.get('factRelease')) if cur_row else 0.0
            used_by_date = fact_by_order_stage_article(
                snap, row['releaseOrder'], stage, ai_filter, max_date=row['date']) - own_fact
            avail_by_date = max(0.0, prev_fact_by_date - used_by_date)

            if val > avail_by_date:
                return Verdict(
                    ok=False, severity=STOP,
                    msg=(f'Нарушена последовательность по датам!\n\n'
                         f"На {info['prevStage']} до {row['date']} выпущено: "
                         f'{_fmt(prev_fact_by_date)} шт\n'
                         f"На {stage} до {row['date']} уже выпущено: {_fmt(used_by_date)} шт\n"
                         f"Доступно к выпуску на {row['date']}: {_fmt(avail_by_date)} шт\n"
                         f'Вы пытаетесь ввести: {_fmt(val)} шт\n\n'
                         f"Нельзя выпустить на {stage} больше, чем сделано на "
                         f"{info['prevStage']} к этой дате."),
                    limit=prev_fact_by_date, available=avail_by_date,
                    prev_stage=info['prevStage'])
    return OK


def validate_fact_vs_order_qty(snap: Snapshot, row: dict, new_fact, exclude_row_id=None) -> Verdict:
    """Порт `validateFactVsOrderQty` — факт не может превысить количество заказа.

    Работает ВСЕГДА, независимо от режима межпередельных проверок.
    """
    if not row or not row.get('releaseOrder'):
        return OK
    order = snap.order_by_number(row['releaseOrder'])
    if not order:
        return OK
    qty = num(order.get('quantity'))
    if qty <= 0:
        return OK

    stage = row.get('stage') or 'ШЦ'
    ais = micro_row_article_items(row)
    primary_ai = ais[0] if ais else (row.get('articleItem') or '')
    ai_filter = None if stage == 'У' else primary_ai

    already = fact_by_order_stage_article(snap, row['releaseOrder'], stage, ai_filter,
                                          exclude_row_id=exclude_row_id)
    available = max(0.0, qty - already)
    val = num(new_fact)
    if val > available:
        return Verdict(
            ok=False, severity=STOP,
            msg=(f'Превышено количество заказа на переделе {stage}!\n\n'
                 f'Кол-во заказа: {_fmt(qty)} шт\n'
                 f'Уже выпущено на {stage}: {_fmt(already)} шт\n'
                 f'Доступно к выпуску: {_fmt(available)} шт\n'
                 f'Вы пытаетесь ввести: {_fmt(val)} шт'),
            limit=qty, available=available, already_used=already)
    return OK


def validate_release_vs_launches(snap: Snapshot, row: dict, new_fact) -> Verdict:
    """Порт `validateReleaseVsLaunches` — выпуск не больше суммы запусков.

    РЦ пропускается: крой ничего не «запускает», он первый передел.
    """
    if not row or not row.get('releaseOrder') or row.get('releaseOrder') == '__extra__':
        return Verdict(ok=True, available=None)
    stage = row.get('stage') or 'ШЦ'
    if stage == 'РЦ':
        return Verdict(ok=True, available=None)

    ais = micro_row_article_items(row)
    row_date = row.get('date')

    total_launched = 0.0
    released_other = 0.0
    for m in snap.microplan:
        if not orders_eq(m.get('releaseOrder'), row['releaseOrder']):
            continue
        if (m.get('stage') or 'ШЦ') != stage:
            continue
        if row_date and m.get('date') and m['date'] > row_date:
            continue
        for le in (m.get('launches') or []):
            if not orders_eq(le.get('order'), row['releaseOrder']):
                continue
            le_ai = le.get('articleItem')
            if ais and le_ai and le_ai not in ais:
                continue
            total_launched += num(le.get('qty'))
        if m.get('id') != row.get('id'):
            released_other += num(m.get('factRelease'))

    available = max(0.0, total_launched - released_other)
    val = num(new_fact)
    if total_launched > 0 and val > available:
        return Verdict(
            ok=False, severity=STOP,
            msg=(f'Выпуск превышает сумму запусков!\n'
                 f'Запущено на {stage} по заказу (до {row_date}): {_fmt(total_launched)} шт\n'
                 f'Уже выпущено другими строками: {_fmt(released_other)} шт\n'
                 f'Доступно к выпуску: {_fmt(available)} шт · Введено: {_fmt(val)} шт'),
            available=available, extra={'totalLaunched': total_launched})
    return Verdict(ok=True, available=available if total_launched > 0 else None)


# ── Проверки ПЛАНА (severity = warn) ────────────────────────────────────────

def validate_plan_order_limit(snap: Snapshot, row: dict, new_plan) -> Verdict:
    """Порт `validatePlanOrderLimit` — план не больше остатка заказа.

    Уровень `warn`: ввод НЕ блокируется. Расхождение с остатком — это опережение
    или отставание, нормальная жизнь цеха. Раньше каждое такое расхождение било
    модалкой, и заполнение микроплана превращалось в череду подтверждений.
    """
    from .coverage import PLAN, covered_before, order_stage_qty

    if not row or not row.get('releaseOrder') or row.get('releaseOrder') == '__extra__':
        return OK
    order = snap.order_by_number(row['releaseOrder'])
    if not order or num(order.get('quantity')) <= 0:
        return OK

    stage = row.get('stage') or 'ШЦ'
    qty = num(order['quantity'])
    before = covered_before(snap, row['releaseOrder'], stage, row.get('date'),
                            exclude_row_id=row.get('id'))
    same_day = order_stage_qty(snap, row['releaseOrder'], stage, row.get('date'),
                               metric=PLAN, when='==', exclude_row_id=row.get('id'),
                               skip_manual_main=True)
    available = max(0.0, qty - before - same_day)
    val = num(new_plan)

    if val > available:
        return Verdict(
            ok=False, severity=WARN,
            msg=(f'План превышает остаток заказа!\n'
                 f"Кол-во по заказу: {_fmt(qty)} шт\n"
                 f'Запланировано ранее ({stage}): {_fmt(before)} шт\n'
                 f'Запланировано в ту же дату: {_fmt(same_day)} шт\n'
                 f'Доступно к планированию: {_fmt(available)} шт\n'
                 f'Введено: {_fmt(val)} шт'),
            available=available, limit=qty, already_used=before)
    return Verdict(ok=True, available=available)


def name_of(snap: Snapshot, article_item: str) -> str:
    """Порт `nameOf` — название изделия по артикулу.

    Приоритет: имя строки маршрута, затем имя карточки. Нужно для текста
    сообщений: пользователю показывают «Пошив куртки», а не «01.000001/1».
    """
    from .routes import all_routes_of
    for nom in snap.nomenclature:
        if nom.get('articleGp') == article_item:
            return nom.get('name') or ''
        for item in all_routes_of(nom):
            if item.get('articleItem') == article_item:
                return item.get('name') or nom.get('name') or ''
    return ''


def _find_nom_by_article(snap: Snapshot, article_item: str) -> dict | None:
    """Карточка, в маршруте которой встречается артикул (порт `findNomByArticle`)."""
    from .routes import all_routes_of
    for nom in snap.nomenclature:
        if nom.get('articleGp') == article_item:
            return nom
        if any(i.get('articleItem') == article_item for i in all_routes_of(nom)):
            return nom
    return None


def launch_entry_avail_qty(snap: Snapshot, order_number, article_item: str, stage: str,
                           exclude_entry_id=None) -> dict | None:
    """Порт `launchEntryAvailQty` — сколько ещё можно запустить в работу.

    РЦ пропускается: крой ничего не запускает.

    ⚠️ Ветка «Доп. объём» (`__extra__`) перенесена по коду, но эталоном НЕ покрыта:
    таких строк нет ни в боевых данных, ни в синтетике. Соответствие артикулов
    между переделами там ищется ПО ПОЗИЦИИ в маршруте (рубашка[0] на ШЦ ↔
    рубашка[0] на АЦ) — это допущение оригинала, а не свойство данных.
    """
    from .routes import prev_stage_in_route

    if not order_number or stage == 'РЦ':
        return None

    if order_key(order_number) == '__extra__':
        nom = _find_nom_by_article(snap, article_item) if article_item else None
        gp = (nom.get('articleGp') if nom else None) or article_item
        prev = prev_stage_in_route(snap, gp, stage) if gp else None
        if not prev:
            return None
        route = (snap.nom_by_gp(gp) or {}).get('route') or []
        cur_items = [i for i in route if i.get('stage') == stage]
        prev_items = [i for i in route if i.get('stage') == prev]
        cur_idx = next((k for k, i in enumerate(cur_items)
                        if i.get('articleItem') == article_item), -1) if article_item else -1
        prev_ai = prev_items[cur_idx].get('articleItem') if 0 <= cur_idx < len(prev_items) else None

        prev_fact = 0.0
        for mr in snap.microplan:
            if mr.get('stage') != prev:
                continue
            if mr.get('releaseOrder') == '__extra__':
                ais = micro_row_article_items(mr)
                mr_ai = ais[0] if ais else ''
                if not prev_ai or not mr_ai or mr_ai == prev_ai:
                    prev_fact += num(mr.get('factRelease'))
            for sub in (mr.get('subOrders') or []):
                if sub.get('releaseOrder') != '__extra__':
                    continue
                sub_ais = sub.get('articleItems') or []
                sub_ai = sub_ais[0] if sub_ais else ''
                if not prev_ai or not sub_ai or sub_ai == prev_ai:
                    prev_fact += num(sub.get('factRelease'))

        already = _launched_on_stage(snap, '__extra__', article_item, stage, exclude_entry_id)
        return {'avail': max(0.0, prev_fact - already), 'prevStage': prev}

    order = snap.order_by_number(order_number)
    if not order:
        return None
    info = stage_fact_limit(snap, order_number, order.get('articleGp'), stage, article_item)
    if not info:
        return None
    already = _launched_on_stage(snap, order_number, article_item, stage, exclude_entry_id)
    return {'avail': max(0.0, info['limit'] - already), 'prevStage': info['prevStage']}


def _launched_on_stage(snap: Snapshot, order_number, article_item: str, stage: str,
                       exclude_entry_id=None, max_date: str | None = None) -> float:
    """Сумма запусков по заказу и переделу.

    Пустой `articleItem` у записи запуска считается подходящим под любой артикул —
    так в оригинале (`!le.articleItem ||`).
    """
    total = 0.0
    is_extra = order_key(order_number) == '__extra__'
    for row in snap.microplan:
        if (row.get('stage') or 'ШЦ') != stage:
            continue
        if max_date and row.get('date') and row['date'] > max_date:
            continue
        for le in (row.get('launches') or []):
            if exclude_entry_id is not None and le.get('id') == exclude_entry_id:
                continue
            if is_extra:
                if le.get('order') != '__extra__':
                    continue
                if article_item and le.get('articleItem') and le['articleItem'] != article_item:
                    continue
            else:
                if not orders_eq(le.get('order'), order_number):
                    continue
                if article_item and le.get('articleItem') and le['articleItem'] != article_item:
                    continue
            total += num(le.get('qty'))
    return total


def validate_stage_launch(snap: Snapshot, order_number, article_item: str, stage: str,
                          date: str | None, new_qty, exclude_entry_id=None,
                          op: str | None = None) -> Verdict:
    """Порт `validateStageLaunch` — нельзя запустить больше, чем выпущено на предыдущем переделе.

    Уровень `stop`: это физика, а не расхождение планирования.
    """
    from .routes import route_for_op

    if not order_number or not stage or stage == 'РЦ':
        return OK

    if order_key(order_number) == '__extra__':
        info = launch_entry_avail_qty(snap, '__extra__', article_item, stage, exclude_entry_id)
        if not info:
            return OK
        val = num(new_qty)
        if val > info['avail']:
            return Verdict(
                ok=False, severity=STOP,
                msg=(f'Превышен лимит запуска на переделе {stage} (Доп. объём)!\n\n'
                     f"Факт выпуска на {info['prevStage']}: {_fmt(info['avail'])} шт\n"
                     f"Доступно к запуску: {_fmt(info['avail'])} шт\n"
                     f'Вы пытаетесь ввести: {_fmt(val)} шт'),
                available=info['avail'], prev_stage=info['prevStage'])
        return OK

    order = snap.order_by_number(order_number)
    if not order:
        return OK
    article_gp = order.get('articleGp')
    info = stage_fact_limit(snap, order_number, article_gp, stage, article_item, op)
    if not info:
        return OK

    already = _launched_on_stage(snap, order_number, article_item, stage, exclude_entry_id)
    available = max(0.0, info['limit'] - already)
    val = num(new_qty)

    if val > available:
        label = name_of(snap, article_item) or article_item or 'все'
        return Verdict(
            ok=False, severity=STOP,
            msg=(f'Превышен лимит запуска на переделе {stage}!\n\n'
                 f"{info['detail']}\n"
                 f'Уже запущено на {stage} ({label}): {_fmt(already)} шт\n'
                 f'Доступно к запуску: {_fmt(available)} шт\n'
                 f'Вы пытаетесь ввести: {_fmt(val)} шт'),
            limit=info['limit'], available=available, prev_stage=info['prevStage'])

    if val > 0 and date and info['prevStage']:
        nom = snap.nom_by_gp(article_gp)
        if nom:
            prev_items = [i for i in route_for_op(nom, op) if i.get('stage') == info['prevStage']]
            prev_fact_by_date = sum(
                fact_by_order_stage_article(snap, order_number, info['prevStage'],
                                            i.get('articleItem'), max_date=date)
                for i in prev_items)
            launched_by_date = _launched_on_stage(snap, order_number, article_item, stage,
                                                  max_date=date)
            avail_by_date = max(0.0, prev_fact_by_date - launched_by_date)
            if val > avail_by_date:
                return Verdict(
                    ok=False, severity=STOP,
                    msg=(f'Нарушена последовательность по датам!\n\n'
                         f"На {info['prevStage']} до {date} выпущено: "
                         f'{_fmt(prev_fact_by_date)} шт\n'
                         f'На {stage} до {date} уже запущено: {_fmt(launched_by_date)} шт\n'
                         f'Доступно к запуску на {date}: {_fmt(avail_by_date)} шт\n'
                         f'Вы пытаетесь ввести: {_fmt(val)} шт\n\n'
                         f"Нельзя запустить на {stage} раньше, чем выпущено на "
                         f"{info['prevStage']}."),
                    limit=prev_fact_by_date, available=avail_by_date,
                    prev_stage=info['prevStage'])
    return OK


def validate_plan_vs_prev_stage_plan(snap: Snapshot, row: dict, new_plan) -> Verdict:
    """Порт `validatePlanVsPrevStagePlan` — план не больше плана предыдущего передела.

    Уровень `warn`: ввод не блокируется.

    **Режим «От ШЦ» — это НЕ разворот цепочки** (CLAUDE.md §4.6). ШЦ планируется
    первым как якорь, поэтому у него нет upstream-проверки; вместо неё строка ШЦ
    получает ИНФОРМАЦИОННЫЙ индикатор готовности кроя (`kind='readiness'`), который
    ничего не блокирует. РЦ и АЦ в этом режиме свободны. Поток при этом физически
    остаётся РЦ→АЦ→ШЦ→У.

    Ёмкость предыдущего передела распределяется ПО ДАТАМ: более ранние строки
    забирают доступное первыми, поэтому переполнение помечается на позднем дне,
    а не на первом.
    """
    from .coverage import PLAN, order_stage_qty
    from .dates import add_cal_days, delivery_days_for
    from .routes import route_stages_for_gp

    if not row or not row.get('releaseOrder') or row.get('releaseOrder') == '__extra__':
        return OK
    stage = row.get('stage') or 'ШЦ'

    if snap.micro_check_mode == 'fromShc':
        if stage in ('РЦ', 'АЦ'):
            return OK
        if stage == 'ШЦ':
            order = snap.order_by_number(row['releaseOrder'])
            route_stages = route_stages_for_gp(snap, order.get('articleGp'),
                                               row.get('op')) if order else set()
            feeder = next((s for s in ('АЦ', 'РЦ') if s in route_stages), None)
            if not feeder:
                return OK
            need = order_stage_qty(snap, row['releaseOrder'], 'ШЦ', row.get('date'),
                                   metric=PLAN, when='<=' if row.get('date') else 'all')
            if need <= 0:
                return OK
            feeder_rows = [m for m in snap.microplan
                           if orders_eq(m.get('releaseOrder'), row['releaseOrder'])
                           and (m.get('stage') or 'ШЦ') == feeder]
            feeder_op = feeder_rows[0].get('op') if feeder_rows else ''
            lead = int(delivery_days_for(snap, feeder_op, row.get('op')) or 0)
            supply_by = add_cal_days(row.get('date'), -lead) if row.get('date') else ''
            have = order_stage_qty(snap, row['releaseOrder'], feeder, supply_by,
                                   metric=PLAN, when='<=' if supply_by else 'all')
            short = have < need
            lead_txt = f' (с учётом {lead} дн. доставки)' if lead else ''
            tail = f' — не успеваем (−{_fmt(need - have)})!' if short else ' ✓'
            return Verdict(
                ok=True, prev_stage=feeder,
                msg=(f"Готовность {feeder} к {row.get('date') or '—'}{lead_txt}: "
                     f'нужно {_fmt(need)} шт, готово {_fmt(have)} шт{tail}'),
                extra={'kind': 'readiness', 'need': need, 'have': have, 'short': short})
        # У — обычная прямая логика ниже.

    stage_order = ['РЦ', 'АЦ', 'ШЦ', 'У']
    try:
        my_idx = stage_order.index(stage)
    except ValueError:
        return OK
    if my_idx <= 0:
        return OK

    order = snap.order_by_number(row['releaseOrder'])
    if not order:
        return OK

    # Предыдущий передел берётся из МАРШРУТА заказа: АЦ может отсутствовать, тогда
    # для ШЦ предыдущий — РЦ. Фиксированный АЦ означал бы, что для заказов без АЦ
    # проверка ищет несуществующий передел и не срабатывает вовсе.
    route_stages = route_stages_for_gp(snap, order.get('articleGp'), row.get('op'))
    prev_stage = next((s for s in reversed(stage_order[:my_idx]) if s in route_stages), None)
    if not prev_stage:
        return OK

    prev_rows = [m for m in snap.microplan
                 if orders_eq(m.get('releaseOrder'), row['releaseOrder'])
                 and (m.get('stage') or 'ШЦ') == prev_stage]
    prev_op = prev_rows[0].get('op') if prev_rows else ''
    deliv_days = int(delivery_days_for(snap, prev_op, row.get('op')) or 0)
    cutoff = add_cal_days(row.get('date'), -deliv_days) if deliv_days else row.get('date')

    prev_plan = order_stage_qty(snap, row['releaseOrder'], prev_stage, cutoff,
                                metric=PLAN, when='<=' if cutoff else 'all')

    my_date = row.get('date') or ''
    my_id = num(row.get('id'))
    used_on_current = 0.0
    for m in snap.microplan:
        if m.get('id') == row.get('id'):
            continue
        if not orders_eq(m.get('releaseOrder'), row['releaseOrder']):
            continue
        if (m.get('stage') or 'ШЦ') != stage:
            continue
        m_date = m.get('date') or ''
        earlier = m_date < my_date or (m_date == my_date and num(m.get('id')) < my_id)
        if earlier:
            used_on_current += max(num(m.get('planRelease')), num(m.get('factRelease')))

    available = max(0.0, prev_plan - used_on_current)
    val = num(new_plan)
    if prev_plan > 0 and val > available:
        deliv_txt = f', учтено {deliv_days} дн. доставки' if deliv_days else ''
        return Verdict(
            ok=False, severity=WARN,
            msg=(f'План превышает план предыдущего передела!\n'
                 f'{prev_stage}: план {_fmt(prev_plan)} шт (до {cutoff}{deliv_txt})\n'
                 f'На {stage} уже запланировано: {_fmt(used_on_current)} шт\n'
                 f'Доступно: {_fmt(available)} шт · Введено: {_fmt(val)} шт'),
            available=available, prev_stage=prev_stage, extra={'prevPlan': prev_plan})
    return Verdict(ok=True, available=available)


def check_delivery_date(snap: Snapshot, row: dict) -> Verdict:
    """Порт `checkDeliveryDate` — дата строки не раньше, чем успеет прийти с предыдущего передела.

    Не проверка в смысле severity, а подсказка «не ранее ДД.ММ» в ячейке даты.
    """
    from .dates import add_cal_days, delivery_days_for
    from .routes import all_routes_of, prev_stage_in_route

    if not row.get('date') or not row.get('releaseOrder'):
        return OK
    order_num = row['releaseOrder']
    order = None if order_num == '__extra__' else snap.order_by_number(order_num)

    if order:
        article_gp = order.get('articleGp')
    else:
        ais = micro_row_article_items(row)
        ai = ais[0] if ais else (row.get('articleItem') or '')
        nom = _find_nom_by_article(snap, ai)
        article_gp = nom.get('articleGp') if nom else None

    prev_stage = prev_stage_in_route(snap, article_gp, row.get('stage') or 'ШЦ')
    if not prev_stage:
        return OK

    prev_date = prev_op = None
    for m in snap.microplan:
        if m.get('id') == row.get('id') or m.get('stage') != prev_stage:
            continue
        if order_num == '__extra__':
            # Доп. объём сверяется только с другими __extra__ строками того же ГП.
            if m.get('releaseOrder') != '__extra__':
                continue
            m_ais = micro_row_article_items(m)
            m_ai = m_ais[0] if m_ais else (m.get('articleItem') or '')
            m_nom = _find_nom_by_article(snap, m_ai)
            if ((m_nom.get('articleGp') if m_nom else m_ai) != article_gp):
                continue
        else:
            if not orders_eq(m.get('releaseOrder'), order_num):
                continue
        if not m.get('date'):
            continue
        if prev_date is None or m['date'] < prev_date:
            prev_date, prev_op = m['date'], m.get('op')

    if not prev_date or not prev_op:
        return OK
    days = int(delivery_days_for(snap, prev_op, row.get('op')) or 0)
    if days <= 0:
        return OK
    min_date = add_cal_days(prev_date, days)
    if row['date'] < min_date:
        return Verdict(ok=False, prev_stage=prev_stage,
                       extra={'minDate': min_date, 'prevOp': prev_op, 'days': days})
    return OK
