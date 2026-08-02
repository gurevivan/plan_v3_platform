# -*- coding: utf-8 -*-
"""Сборка расчётного среза из базы.

Расчётное ядро (`core.calc`) работает с обычными словарями в формате
JSON-экспорта — именно на нём оно сверено с оригиналом построчно. Поэтому срез
собирается через тот же `build_export()`, что и выгрузка: так ядро гарантированно
видит формат, на котором проверено, и не появляется второй способ подать ему данные.

**Ограничение доступа по ОП живёт здесь, а не в интерфейсе.** Если срез собран с
`op_names`, в него не попадут чужие площадки, и любой расчёт поверх него уже
физически не сможет их задеть (ТЗ §7а).

Про производительность: пока срез собирается целиком. Для 100 пользователей этого
хватит на текущих объёмах (315 строк микроплана — 15 мс), но при росте нужен
частичный срез по месяцу и ОП — см. `month` и `op_names`.
"""
from __future__ import annotations

from core.calc.snapshot import Snapshot, from_export
from core.management.commands.export_json import build_export


def build_snapshot(*, op_names: list[str] | None = None, month: str | None = None,
                   micro_check_mode: str = 'forward') -> Snapshot:
    """Срез для расчётов.

    `op_names` — ограничение по доступным ОП (None = без ограничения).
    `month` — оставить в микроплане только этот месяц (`ГГГГ-ММ`).
    """
    data = build_export()

    if op_names is not None:
        allowed = set(op_names)
        data['microplan'] = [m for m in data['microplan'] if m.get('op') in allowed]
        data['macroplan'] = [r for r in data['macroplan'] if r.get('op') in allowed]
        data['schedules'] = [s for s in data['schedules'] if s.get('op') in allowed]
        data['ops'] = [o for o in data['ops'] if o.get('name') in allowed]

    if month:
        data['microplan'] = [m for m in data['microplan']
                             if (m.get('date') or '').startswith(month)]
        data['macroplan'] = [r for r in data['macroplan'] if r.get('month') == month]

    return from_export(data, micro_check_mode=micro_check_mode)


def accessible_op_names(user) -> list[str] | None:
    """ОП, доступные пользователю. `None` — доступны все.

    Отсутствие записей `UserOpAccess` означает полный доступ: так администратору
    не нужно перечислять все площадки. Пустой список вернуть нельзя — он означал
    бы «ничего не видно», а это не то же самое.
    """
    from core.models import UserOpAccess

    if not user or not user.is_authenticated:
        return []
    if user.is_superuser:
        return None
    names = list(UserOpAccess.objects.filter(user=user).values_list('op_name', flat=True))
    return None if not names else names


def editable_op_names(user) -> list[str] | None:
    """ОП, которые пользователь может ПРАВИТЬ. `None` — все."""
    from core.models import UserOpAccess

    if not user or not user.is_authenticated:
        return []
    if user.is_superuser:
        return None
    qs = UserOpAccess.objects.filter(user=user)
    if not qs.exists():
        return None
    return list(qs.filter(can_edit=True).values_list('op_name', flat=True))
