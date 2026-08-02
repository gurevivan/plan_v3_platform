# -*- coding: utf-8 -*-
"""Даты и доставки. Порт группы 1 из ТЗ §4.

Даты в приложении — строки `ГГГГ-ММ-ДД`, и вся арифметика идёт по локальному
времени, а не UTC (в JS специально `new Date(s + 'T00:00:00')` и собственный
`localDateStr`, чтобы не словить сдвиг на сутки). Здесь используется `date`,
который часовых поясов не знает вовсе, — эквивалент того же намерения.
"""
from __future__ import annotations

from datetime import date, timedelta

from .snapshot import Snapshot, num, order_key


def parse_date(value) -> date | None:
    """'ГГГГ-ММ-ДД' → date. Пустое и битое → None (как falsy-проверки в JS)."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        y, m, d = str(value)[:10].split('-')
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def fmt_date(value: date | None) -> str:
    """date → 'ГГГГ-ММ-ДД'. Порт `localDateStr`."""
    return value.isoformat() if value else ''


def add_cal_days(date_str, n: int) -> str:
    """Порт `addCalDays` — сдвиг на N календарных дней.

    Ноль дней возвращает исходную строку без разбора — как `if (!n) return dateStr`.
    """
    if not date_str or not n:
        return date_str or ''
    d = parse_date(date_str)
    if d is None:
        return date_str
    return fmt_date(d + timedelta(days=int(n)))


def prev_workday(date_str) -> str:
    """Порт `prevWorkday` — суббота и воскресенье сдвигаются на пятницу.

    Праздники здесь НЕ учитываются: в оригинале тоже только выходные дни недели.
    """
    if not date_str:
        return ''
    d = parse_date(date_str)
    if d is None:
        return ''
    # В JS getDay(): 0 — воскресенье, 6 — суббота. В Python weekday(): 6 и 5.
    if d.weekday() == 6:
        d -= timedelta(days=2)
    elif d.weekday() == 5:
        d -= timedelta(days=1)
    return fmt_date(d)


def delivery_days_for(snap: Snapshot, from_op: str, to_op: str) -> float:
    """Порт `deliveryDaysFor`.

    Явная запись матрицы переопределяет всё; в пределах одного ОП — 1 день на
    передачу; иначе 0.
    """
    for row in (snap.delivery_matrix or []):
        if row.get('fromOp') == from_op and row.get('toOp') == to_op:
            return num(row.get('days'))
    if from_op and to_op and from_op == to_op:
        return 1
    return 0


def calc_arrival_date(snap: Snapshot, customer_order: dict) -> dict:
    """Порт `calcArrivalDate` — срок отгрузки и крайний срок упаковки.

    Отгрузка = дата поставки − дни транзита, сдвинутая на предыдущий рабочий день.
    Упаковка должна закончиться минимум за один рабочий день до отгрузки.
    """
    co = customer_order or {}
    if not co.get('deliveryDate'):
        return {'shipmentDate': '', 'packDeadline': '', 'fromOp': '', 'days': 0}

    prod_orders = [
        o for o in snap.orders
        if o.get('customerOrderId') == co.get('id')
        or (o.get('contractId') == co.get('contractId')
            and o.get('articleGp') == co.get('articleGp'))
    ]
    order_nums = {order_key(o.get('number')) for o in prod_orders}
    pack_row = next((m for m in snap.microplan
                     if m.get('stage') == 'У'
                     and order_key(m.get('releaseOrder')) in order_nums), None)

    from_op = (pack_row.get('op') or '') if pack_row else (
        (prod_orders[0].get('op') or '') if prod_orders else '')
    to_op = co.get('base') or ''
    days = delivery_days_for(snap, from_op, to_op)

    raw_shipment = add_cal_days(co['deliveryDate'], -int(days))
    shipment = prev_workday(raw_shipment or co['deliveryDate'])
    pack_deadline = prev_workday(add_cal_days(shipment, -1))
    return {'shipmentDate': shipment, 'packDeadline': pack_deadline,
            'fromOp': from_op, 'days': days}
