# -*- coding: utf-8 -*-
"""Срез данных для расчётов.

Расчётный слой — чистые функции над обычными структурами Python, без ORM внутри
(ТЗ §2). Так его можно прогонять в pytest против эталонов, снятых с JS-версии,
и переиспользовать в API и отчётах.

`Snapshot` собирается один раз на запрос и несёт **индексы**, а не только данные.
Это принципиально: в JS-версии `orderStagePlannedQty` при каждом вызове перебирает
весь микроплан, из-за чего полный пересчёт растёт квадратично — 9 секунд на 2520
строк (ТЗ §8). Здесь индекс `(заказ, передел)` строится за один проход, и выборка
становится обращением по ключу.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


def order_key(value: Any) -> str:
    """Ключ номера заказа. Порт `orderReleaseKey`: строка со срезанными пробелами."""
    return '' if value is None else str(value).strip()


def orders_eq(a: Any, b: Any) -> bool:
    """Порт `releaseOrderEq`."""
    return order_key(a) == order_key(b)


def num(value: Any) -> float:
    """Число как в JS: `+x || 0` — нечисловое и None дают 0."""
    if value is None or value is True or value is False:
        return 0.0 if not value else 1.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if f != f else f  # NaN → 0, как `+x || 0`


@dataclass
class Snapshot:
    """Состояние, на котором считают. Поля повторяют коллекции `S.*`."""
    nomenclature: list[dict] = field(default_factory=list)
    ops: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    macroplan: list[dict] = field(default_factory=list)
    microplan: list[dict] = field(default_factory=list)
    schedules: list[dict] = field(default_factory=list)
    delivery_matrix: list[dict] = field(default_factory=list)
    contracts: list[dict] = field(default_factory=list)
    plan_baseline: list[dict] = field(default_factory=list)
    tc_rc_overrides: dict[str, Any] = field(default_factory=dict)
    tc_sh_overrides: dict[str, Any] = field(default_factory=dict)
    micro_check_mode: str = 'forward'

    # ── индексы (строятся в __post_init__) ──────────────────────────────────
    _nom_by_gp: dict[str, dict] = field(default_factory=dict, repr=False)
    _order_by_number: dict[str, dict] = field(default_factory=dict, repr=False)
    _micro_by_order_stage: dict[tuple, list] = field(default_factory=dict, repr=False)
    _op_by_name_workshop: dict[tuple, dict] = field(default_factory=dict, repr=False)
    _op_by_name: dict[str, dict] = field(default_factory=dict, repr=False)
    _rows_by_brigade_day: dict[tuple, list] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.reindex()

    def reindex(self) -> None:
        """Перестроить индексы. Вызывать после изменения данных среза."""
        self._nom_by_gp = {}
        for n in self.nomenclature:
            gp = n.get('articleGp')
            if gp and gp not in self._nom_by_gp:
                self._nom_by_gp[gp] = n

        self._order_by_number = {}
        for o in self.orders:
            key = order_key(o.get('number'))
            if key and key not in self._order_by_number:
                self._order_by_number[key] = o

        self._op_by_name_workshop = {}
        self._op_by_name = {}
        for op in self.ops:
            name = op.get('name') or ''
            ws = op.get('workshop') or ''
            self._op_by_name_workshop.setdefault((name, ws), op)
            self._op_by_name.setdefault(name, op)

        # Главный индекс: заказ+передел → строки (и main, и подзаказы).
        # Подзаказ наследует дату и передел родительской строки — как в stageStats.
        idx: dict[tuple, list] = defaultdict(list)
        for m in self.microplan:
            stage = m.get('stage') or 'ШЦ'
            date = m.get('date') or ''
            ro = order_key(m.get('releaseOrder'))
            if ro:
                idx[(ro, stage)].append({'row': m, 'obj': m, 'date': date, 'is_sub': False})
            for sub in (m.get('subOrders') or []):
                sro = order_key(sub.get('releaseOrder'))
                if sro:
                    idx[(sro, stage)].append(
                        {'row': m, 'obj': sub, 'date': date, 'is_sub': True})
        self._micro_by_order_stage = dict(idx)

        # Индекс бригады-дня-передела. В JS `brigadeDayGroupRows` фильтрует весь
        # микроплан при каждом вызове, а вызывается он на каждую строку пересчёта.
        groups: dict[tuple, list] = defaultdict(list)
        for m in self.microplan:
            sid = m.get('scheduleId')
            who = f'S{sid}' if sid not in (None, '') else \
                f"W{m.get('op') or ''}|{m.get('workshop') or ''}"
            groups[(who, m.get('date') or '', m.get('stage') or 'ШЦ')].append(m)
        self._rows_by_brigade_day = dict(groups)

    # ── доступ по индексам ──────────────────────────────────────────────────
    def nom_by_gp(self, article_gp: str) -> dict | None:
        return self._nom_by_gp.get(article_gp)

    def order_by_number(self, number: Any) -> dict | None:
        return self._order_by_number.get(order_key(number))

    def op_record(self, op_name: str, workshop: str | None = None) -> dict | None:
        """Порт выбора ОП в `timeCostOfForOp`: сначала пара (ОП, цех), потом просто ОП."""
        if not op_name:
            return None
        if workshop:
            rec = self._op_by_name_workshop.get((op_name, workshop))
            if rec:
                return rec
        return self._op_by_name.get(op_name)

    def brigade_day_rows(self, key: tuple) -> list[dict]:
        """Строки одной бригады-дня-передела по ключу (кто, дата, передел)."""
        return self._rows_by_brigade_day.get(key, [])

    def order_stage_entries(self, order_number: Any, stage: str) -> list[dict]:
        """Строки и подзаказы по заказу+переделу — за одно обращение к индексу."""
        return self._micro_by_order_stage.get((order_key(order_number), stage or 'ШЦ'), [])


def from_export(data: dict, micro_check_mode: str = 'forward') -> Snapshot:
    """Срез из структуры JSON-экспорта — тот же формат, что у приложения."""
    return Snapshot(
        nomenclature=data.get('nomenclature') or [],
        ops=data.get('ops') or [],
        orders=data.get('orders') or [],
        macroplan=data.get('macroplan') or [],
        microplan=data.get('microplan') or [],
        schedules=data.get('schedules') or [],
        delivery_matrix=data.get('deliveryMatrix') or [],
        contracts=data.get('contracts') or [],
        plan_baseline=data.get('planBaseline') or [],
        tc_rc_overrides=data.get('tcRcOverrides') or {},
        tc_sh_overrides=data.get('tcShOverrides') or {},
        micro_check_mode=micro_check_mode,
    )
