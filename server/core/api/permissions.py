# -*- coding: utf-8 -*-
"""Права доступа. Ограничение по ОП — часть модели данных, а не проверка в UI.

Правило из ТЗ §7а: фильтрация идёт в QuerySet. Проверка только в интерфейсе
означала бы, что любой запрос в обход UI отдаёт чужие площадки.

Отсутствие записей `UserOpAccess` у пользователя = доступ ко всем ОП. Так
администратору не нужно перечислять два десятка площадок, а новому мастеру
доступ выдаётся явным добавлением строк.
"""
from rest_framework import permissions

from core.api.roles import SECTIONS, can_edit_section
from core.services.snapshot_db import accessible_op_names, editable_op_names

SAFE = permissions.SAFE_METHODS


class OpScopedPermission(permissions.BasePermission):
    """Читать можно доступные ОП, править — только те, где стоит `can_edit`.

    Плюс роль: право на запись — это И раздел (что), И площадка (где). Раздел
    проверяем в `has_permission`, потому что у POST объекта ещё нет; площадку —
    в `has_object_permission`, когда объект есть.
    """

    message = 'Нет доступа к этому ОП'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE:
            return True
        section = getattr(view, 'section', None)
        if not can_edit_section(request.user, section):
            title = SECTIONS.get(section, (section,))[0]
            self.message = f'Нет прав на изменение раздела «{title}»'
            return False
        return True

    def has_object_permission(self, request, view, obj):
        op = _op_of(obj)
        if op is None:
            return True
        if request.method in SAFE:
            allowed = accessible_op_names(request.user)
        else:
            allowed = editable_op_names(request.user)
        return allowed is None or op in allowed


class IsSystemAdmin(permissions.BasePermission):
    """Администратор — это `is_superuser`, и только он.

    Штатный `IsAdminUser` смотрит на `is_staff` — признак доступа в админку
    Django. Опираться на него значило бы завести второй способ стать
    администратором, помимо `is_superuser`.
    """

    message = 'Действие доступно только администратору'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated
                    and request.user.is_superuser)


def _op_of(obj):
    """Имя ОП объекта, если оно у него есть."""
    for attr in ('op_name', 'op'):
        val = getattr(obj, attr, None)
        if isinstance(val, str):
            return val
        if val is not None and hasattr(val, 'name'):
            return val.name
    return None


def scope_queryset(qs, user, field='op_name'):
    """Сузить выборку до доступных пользователю ОП."""
    allowed = accessible_op_names(user)
    if allowed is None:
        return qs
    return qs.filter(**{f'{field}__in': allowed})
