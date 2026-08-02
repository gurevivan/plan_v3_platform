# -*- coding: utf-8 -*-
"""Маршруты изделия по переделам. Порт группы 2 из ТЗ §4.

Ключевой инвариант (CLAUDE.md §4.1): разные ОП могут иметь разные маршруты для
одного `articleGp` без дублирования карточки. `routeForOp` отдаёт переопределение,
если оно непустое, иначе базовый маршрут. Пока переопределений нет, обе ветки
дают одно и то же — это свойство безопасности переноса.
"""
from __future__ import annotations

from .snapshot import Snapshot

# Физический поток ВСЕГДА такой. Не разворачивать (CLAUDE.md §4.6).
STAGE_ORDER = ['РЦ', 'АЦ', 'ШЦ', 'У', 'База']


def route_override_entry(nom: dict | None, op: str | None) -> dict | None:
    """Запись переопределения маршрута для ОП."""
    if not nom or not op:
        return None
    for entry in (nom.get('routeOverrides') or []):
        if entry and entry.get('op') == op:
            return entry
    return None


def route_for_op(nom: dict | None, op: str | None) -> list[dict]:
    """Порт `routeForOp`: переопределение ОП, если непустое, иначе базовый маршрут.

    `op` пустой → базовый маршрут (так же, как в JS).
    """
    entry = route_override_entry(nom, op)
    if entry:
        route = entry.get('route')
        if isinstance(route, list) and route:
            return route
    if nom and isinstance(nom.get('route'), list):
        return nom['route']
    return []


def all_routes_of(nom: dict | None) -> list[dict]:
    """Порт `allRoutesOf`: базовый маршрут плюс все переопределения.

    Нужен, когда контекст ОП недоступен — например, при поиске принадлежности
    артикула к ГП.
    """
    out: list[dict] = []
    if nom and isinstance(nom.get('route'), list):
        out.extend(nom['route'])
    if nom and isinstance(nom.get('routeOverrides'), list):
        for entry in nom['routeOverrides']:
            if entry and isinstance(entry.get('route'), list):
                out.extend(entry['route'])
    return out


def nom_row_ops(nom: dict | None) -> list[str]:
    """Порт `nomRowOps`: ОП карточки."""
    if not nom:
        return []
    ops = nom.get('nomOps')
    if isinstance(ops, list) and ops:
        return [o for o in ops if o]
    if nom.get('op'):
        return [nom['op']]
    return []


def route_stages_for_gp(snap: Snapshot, article_gp: str, op: str | None = None) -> set[str]:
    """Порт `routeStagesForGp`. Карточки нет или маршрут пуст → {'ШЦ'}."""
    nom = snap.nom_by_gp(article_gp)
    if not nom:
        return {'ШЦ'}
    route = route_for_op(nom, op) if op else all_routes_of(nom)
    if not route:
        return {'ШЦ'}
    return {item.get('stage') for item in route if item.get('stage')}


def prev_stage_in_route(snap: Snapshot, article_gp: str, current_stage: str,
                        op: str | None = None) -> str | None:
    """Порт `prevStageInRoute` — предыдущий передел с учётом того, что АЦ может не быть.

    Режим «От ШЦ» — НЕ разворот цепочки (CLAUDE.md §4.6). Швейный цех планируется
    первым как якорь, поэтому у него нет «предыдущего» передела и upstream-проверки
    к нему не применяются. Поток при этом физически остаётся РЦ→АЦ→ШЦ→У.
    """
    if snap.micro_check_mode == 'fromShc' and current_stage == 'ШЦ':
        return None
    stages = route_stages_for_gp(snap, article_gp, op)
    ordered = [s for s in STAGE_ORDER if s in stages]
    try:
        idx = ordered.index(current_stage)
    except ValueError:
        return None
    if idx <= 0:
        return None
    return ordered[idx - 1]
