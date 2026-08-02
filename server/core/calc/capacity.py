# -*- coding: utf-8 -*-
"""Мощность бригады-дня. Порт группы 4 из ТЗ §4.

Здесь живёт инвариант, на котором проще всего ошибиться (CLAUDE.md §4.4):

**Пул мощности считается БЕЗ эффективности освоения, а строки потребляют
физические минуты С ней.**

    пул   = Σ (смена × численность × эфф.работников/100 × абсентеизм)
    расход = штуки × норма / эфф.освоения

Эффективность работников входит в пул (это про то, как работают люди).
Эффективность освоения — множитель выработки конкретной строки: при 50 % та же
штука съедает вдвое больше физических минут, при 120 % — меньше. Поставить её в
пул вместо знаменателя расхода — ошибка, которую не видно глазом: план просто
окажется другим.

Абсентеизм ×0,95 применяется ПОРАБОТНИКУ, а не ко всей смене: снятая галочка
`absence: false` даёт множитель 1 для этой группы.
"""
from __future__ import annotations

from .norms import micro_row_article_items, tc_for_micro_row, tc_for_sub_order
from .snapshot import Snapshot, num

ABSENCE_FACTOR = 0.95   # абсентеизм: доля времени, которую реально отрабатывают
DEFAULT_SHIFT_MIN = 480


def shift_duration_of(snap: Snapshot, op: str, workshop: str) -> float:
    """Порт `shiftDurationOf`. Ищется ТОЧНАЯ пара (ОП, цех); иначе 480."""
    rec = snap._op_by_name_workshop.get((op or '', workshop or ''))
    return num(rec.get('shiftDuration')) if rec else DEFAULT_SHIFT_MIN


def workshop_type(snap: Snapshot, op: str, workshop: str) -> str:
    """Порт `workshopType`. По умолчанию БТК."""
    rec = snap._op_by_name_workshop.get((op or '', workshop or ''))
    return (rec.get('type') if rec else None) or 'БТК'


def default_eff_pct(snap: Snapshot, op: str, workshop: str) -> float:
    """Порт `defaultEffPct`: у БП базовая эффективность 85 %, у БТК — 100 %."""
    return 85.0 if workshop_type(snap, op, workshop) == 'БП' else 100.0


def _worker_eff(snap: Snapshot, worker: dict, op: str, workshop: str) -> float:
    """Эффективность группы работников: своя, если задана числом, иначе по типу цеха."""
    val = worker.get('effPct')
    if val is not None:
        try:
            f = float(val)
        except (TypeError, ValueError):
            return default_eff_pct(snap, op, workshop)
        if f == f and f not in (float('inf'), float('-inf')):   # порт Number.isFinite
            return max(0.0, f)
    return default_eff_pct(snap, op, workshop)


def _worker_shift(snap: Snapshot, worker: dict, op: str, workshop: str) -> float:
    """Смена группы: своя, если положительная, иначе смена ОП."""
    shift = num(worker.get('shiftTime'))
    return shift if shift > 0 else shift_duration_of(snap, op, workshop)


def workers_available_min_sum(snap: Snapshot, workers: list[dict],
                              op: str, workshop: str) -> float:
    """Порт `workersAvailableMinSum` — физические минуты состава за смену.

    Эффективность освоения сюда НЕ входит: пул физический.
    """
    total = 0.0
    for w in workers or []:
        staff = num(w.get('staffCount'))
        if staff <= 0:
            continue
        shift = _worker_shift(snap, w, op, workshop)
        eff = _worker_eff(snap, w, op, workshop) / 100.0
        absence = 1.0 if w.get('absence') is False else ABSENCE_FACTOR
        total += shift * staff * eff * absence
    return total


def micro_row_eff_pct(snap: Snapshot, workers: list[dict], op: str, workshop: str) -> float:
    """Порт `microRowEffPct` — средневзвешенная эффективность (вес = смена × численность)."""
    numerator = denominator = 0.0
    for w in workers or []:
        staff = num(w.get('staffCount'))
        if staff <= 0:
            continue
        shift = _worker_shift(snap, w, op, workshop)
        eff = _worker_eff(snap, w, op, workshop)
        numerator += shift * staff * eff
        denominator += shift * staff
    return numerator / denominator if denominator > 0 else 100.0


def _group_key(row: dict) -> tuple:
    """Ключ бригады-дня. Порт `brigadeDayGroupRows`.

    Бригада определяется по `scheduleId`; если его нет — по паре (ОП, цех).
    Плюс дата и передел.
    """
    sid = row.get('scheduleId')
    who = f'S{sid}' if sid not in (None, '') else f"W{row.get('op') or ''}|{row.get('workshop') or ''}"
    return (who, row.get('date') or '', row.get('stage') or 'ШЦ')


def brigade_day_group_rows(snap: Snapshot, row: dict) -> list[dict]:
    """Строки одной бригады-дня-передела — через индекс, а не перебором.

    В JS это фильтр по всему микроплану на каждый вызов; здесь ключ считается
    один раз при сборке среза.
    """
    if not row:
        return []
    return snap.brigade_day_rows(_group_key(row))


def learning_factor(obj: dict) -> float:
    """Множитель эффективности освоения. Порт `Math.max(0.01, (x ?? 100) / 100)`.

    Нижняя граница 0,01 не даёт делению на ноль. Значение выше 100 % раздувает
    план сверх физической мощности — это заявленная семантика, а не баг (§4.4).
    """
    val = obj.get('learningEff')
    if val is None:
        val = 100
    return max(0.01, num(val) / 100.0)


def row_total_tc(snap: Snapshot, row: dict) -> float:
    """Суммарная норма строки по её уникальным артикулам."""
    seen, total = set(), 0.0
    for ai in micro_row_article_items(row):
        if ai in seen:
            continue
        seen.add(ai)
        total += tc_for_micro_row(snap, row, ai)
    return total


def row_used_raw_min(snap: Snapshot, row: dict) -> float:
    """Физические минуты, которые занимает строка вместе со своими подзаказами."""
    used = 0.0
    tc = row_total_tc(snap, row)
    if tc > 0:
        used += num(row.get('planRelease')) * tc / learning_factor(row)
    for sub in (row.get('subOrders') or []):
        sub_tc = tc_for_sub_order(snap, sub, row.get('op'), row.get('stage'))
        if sub_tc > 0:
            used += num(sub.get('planRelease')) * sub_tc / learning_factor(sub)
    return used


def brigade_day_pool_raw_min(snap: Snapshot, group_rows: list[dict]) -> float:
    """Порт `brigadeDayPoolRawMin` — сырой физический пул бригады-дня."""
    return sum(workers_available_min_sum(snap, g.get('workers') or [],
                                         g.get('op'), g.get('workshop'))
               for g in group_rows)


def brigade_day_used_by_others_raw_min(snap: Snapshot, row: dict,
                                       group_rows: list[dict]) -> float:
    """Порт `brigadeDayUsedByOthersRawMin` — минуты, занятые ДРУГИМИ строками группы."""
    row_id = row.get('id')
    return sum(row_used_raw_min(snap, g) for g in group_rows
               if g is not row and g.get('id') != row_id)


def available_min_for_row(snap: Snapshot, row: dict) -> float:
    """Доступные физические минуты строки: пул группы минус занятое другими."""
    group = brigade_day_group_rows(snap, row)
    pool = brigade_day_pool_raw_min(snap, group)
    used = brigade_day_used_by_others_raw_min(snap, row, group)
    return max(0.0, pool - used)
