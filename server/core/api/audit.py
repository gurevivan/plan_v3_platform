# -*- coding: utf-8 -*-
"""Журнал изменений: кто что менял.

Точка записи одна — `VersionedSerializer` и `perform_destroy` базового набора.
Расставлять вызовы по обработчикам нельзя: их десятки, и забытый обработчик
означал бы правку без следа — причём заметную только когда след понадобится.

Пишем РАЗНИЦУ, а не копию записи (см. `ChangeLog`). Пустую разницу не пишем:
сохранение без изменений — обычное дело при работе с формой, и журнал из таких
записей нечитаем.
"""
from core import models as m

# Служебные поля: меняются при каждом сохранении и о сути правки не говорят.
SKIP_FIELDS = {'version', 'id'}


def snapshot(instance, fields):
    """Значения полей до правки — для последующего сравнения."""
    return {f: _plain(getattr(instance, f, None)) for f in fields
            if f not in SKIP_FIELDS}


def record(request, view, instance, action, before=None, note=''):
    """Записать правку. Молчит, если писать нечего или некому.

    Ошибка журналирования не должна ронять сохранение: данные пользователя
    важнее записи о них. Поэтому исключения гасим — но не молча в никуда, а в
    журнал приложения.
    """
    try:
        user = getattr(request, 'user', None)
        changes = {}
        if before is not None:
            for field, was in before.items():
                now = _plain(getattr(instance, field, None))
                if was != now:
                    changes[field] = [was, now]
            if not changes and action == 'update':
                return None

        return m.ChangeLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            username=getattr(user, 'username', '') or '',
            entity=getattr(view, 'entity', '') or '',
            src_id=getattr(instance, 'src_id', None),
            row_id=getattr(instance, 'pk', None),
            op_name=_op_of(instance),
            action=action,
            changes=changes,
            note=note,
        )
    except Exception as exc:      # pragma: no cover — защита от падения сохранения
        import logging
        logging.getLogger(__name__).warning('журнал изменений: %s', exc)
        return None


def record_bulk(request, entity, action, note, op_name=''):
    """Одна запись на массовую операцию.

    Пересчёт месяца трогает сотни строк; строка на каждую сделала бы журнал
    бесполезным ровно в тот день, когда в нём нужно разобраться.
    """
    try:
        user = getattr(request, 'user', None)
        return m.ChangeLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            username=getattr(user, 'username', '') or '',
            entity=entity, action=action, note=note, op_name=op_name, changes={})
    except Exception as exc:      # pragma: no cover
        import logging
        logging.getLogger(__name__).warning('журнал изменений: %s', exc)
        return None


def _op_of(instance):
    """Площадка записи — по ней журнал фильтруется так же, как сами данные."""
    for attr in ('op_name', 'op'):
        val = getattr(instance, attr, None)
        if isinstance(val, str):
            return val
        if val is not None and hasattr(val, 'name'):
            return val.name
    return ''


def _plain(value):
    """Значение в виде, пригодном для JSON и сравнения.

    `Decimal('10.0000')` и `10.0` — одно и то же число, и показывать это как
    изменение нельзя: норма «поменялась с 10 на 10» только запутает.
    """
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return str(value)
