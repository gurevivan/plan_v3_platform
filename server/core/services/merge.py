# -*- coding: utf-8 -*-
"""Слияние выгрузки с тем, что уже лежит в базе.

Задача: залить несколько выгрузок (с разных компьютеров, по разным площадкам) в
одну базу так, чтобы **новое добавлялось, а совпадающее обновлялось** — вместо
того чтобы каждая следующая загрузка затирала предыдущую.

## Почему нельзя сопоставлять по `id`

В однофайловой версии `id` выдаёт счётчик `nextId` в localStorage, у каждого
компьютера свой. Контракт `id=14` из выгрузки Бухары и контракт `id=14` из
выгрузки Самарканда — это РАЗНЫЕ контракты. Слить их по `id` значит смешать
данные двух площадок, причём молча.

Поэтому дубли ищем по **деловому ключу**: номер контракта, номер заказа, артикул
ГП, имя площадки, у строки микроплана — дата вместе с ОП, переделом, бригадой и
заказом. Это то, чем запись определяется для человека.

## Почему одного upsert мало

На `id` ссылаются другие коллекции: микроплан помнит `contractId` и `scheduleId`,
правки дня — `scheduleId`, связи ЗК↔ПЗ — оба номера заказов. Если у входной
записи `id` уже занят в базе другой записью, ссылки поедут — микроплан окажется
привязан к чужой бригаде.

Поэтому сначала строим карту `старый id → id в базе` для коллекций, на которые
ссылаются (контракты, заказы, бригады), выдавая новым записям свободные номера, а
затем переписываем по этой карте все ссылки во входных данных. И только потом
пишем.
"""
from __future__ import annotations

# ── Деловые ключи ───────────────────────────────────────────────────────────
#
# Чем запись определяется для человека. Пустой ключ означает, что коллекция
# сливается только по `id` (после переотображения) — так у справочных мелочей.
NATURAL_KEYS = {
    'ops': ('name', 'workshop'),
    'bases': ('name',),
    'deliveryMatrix': ('fromOp', 'toOp'),
    'holidays': ('date',),
    'nomenclature': ('articleGp',),
    # Номер контракта НЕ уникален: под одним номером идут разные изделия
    # («QA 10-26» — и футболка, и трусы). Сливать их по номеру значит потерять
    # изделие. Проверено на боевой выгрузке: 3 таких пары из 12 контрактов.
    'contracts': ('number', 'articleGp'),
    'customerOrders': ('number',),
    'orders': ('number',),
    # Бригада с тем же названием живёт в РАЗНЫХ месяцах отдельными записями
    # («Бригада 1» на 2026-05 и на 2026-06..08 — разный состав и разные графики).
    # Без месяцев слияние схлопывало 5 бригад из 22.
    'schedules': ('op', 'workshop', 'name', 'activeMonths'),
    'macroEff': ('op', 'workshop', 'month'),
    'manualFrv': ('op', 'workshop', 'month'),
    # Макроплан НЕ агрегируется: каждая введённая запись — отдельная строка со
    # своей нормой и своими заказами (CLAUDE.md §7). Две строки на один месяц и
    # артикул законны и различаются количеством, нормой и заказами — они и входят
    # в ключ. Иначе слияние складывало бы их в одну и теряло объём: на боевых
    # данных 15729 + 23953 превращались в 23953.
    'macroplan': ('contractId', 'articleItem', 'op', 'workshop', 'month', 'volumeType',
                  'qtySew', 'normOverride', 'orderNums'),
    # Артикулы в ключе обязательны: изделие из нескольких частей даёт в одной
    # смене несколько законных строк (куртка и брюки костюма). Без них слияние
    # схлопывало их в одну — и половина плана исчезала молча.
    'microplan': ('date', 'op', 'workshop', 'stage', 'scheduleId', 'releaseOrder',
                  'articleItems'),
    'calOverrides': ('scheduleId', 'date'),
    'scheduleMonthOverrides': ('scheduleId', 'month'),
    'planBaseline': ('month', 'date', 'scheduleId', 'stage', 'releaseOrder'),
    'orderLinks': ('customerOrderId', 'productionOrderId'),
}

#: Коллекции, на `id` которых ссылаются другие. Порядок важен: сначала те, от
#: кого зависят остальные.
ANCHORS = ('contracts', 'customerOrders', 'orders', 'schedules')

#: Где лежат ссылки: коллекция → {поле: на какую коллекцию ссылается}.
REFERENCES = {
    'macroplan': {'contractId': 'contracts'},
    'microplan': {'contractId': 'contracts', 'scheduleId': 'schedules'},
    'calOverrides': {'scheduleId': 'schedules'},
    'scheduleMonthOverrides': {'scheduleId': 'schedules'},
    'planBaseline': {'scheduleId': 'schedules'},
    'orderLinks': {'customerOrderId': 'customerOrders',
                   'productionOrderId': 'orders'},
}


def key_of(coll: str, rec: dict):
    """Деловой ключ записи. `None` — у коллекции ключа нет, сливаем по `id`."""
    fields = NATURAL_KEYS.get(coll)
    if not fields:
        return None
    return tuple(_norm(rec.get(f)) for f in fields)


def _norm(value):
    """Приводим к виду, в котором ключи сравнимы.

    Номер контракта «QZ 1 » и «QZ 1» — один контракт: пробелы по краям в
    выгрузках встречаются, и разделять по ним записи нельзя. Числа приводим к
    int, иначе 14 и 14.0 дали бы два разных ключа.
    """
    if value is None:
        return ''
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        # Набор артикулов строки: порядок в выгрузках не гарантирован, а строка
        # от него не меняется — сравниваем как множество.
        return tuple(sorted(_norm(v) for v in value))
    return value


def build_id_map(data: dict, existing: dict, next_ids: dict) -> dict:
    """Карта `коллекция → {старый id: id в базе}`.

    `existing[coll]` — {деловой ключ: id в базе} по тому, что уже загружено.
    `next_ids[coll]` — с какого номера выдавать новые id.

    Совпал ключ — берём id из базы (запись будет обновлена). Не совпал — выдаём
    свободный номер, даже если исходный id свободен: иначе два файла, где счётчики
    шли независимо, рано или поздно столкнулись бы на одном номере.
    """
    id_map = {}
    for coll in ANCHORS:
        mapping = {}
        used = set(existing.get(coll, {}).values())
        nxt = next_ids.get(coll, 1)
        seen = {}                      # ключ → id, выданный в ЭТОМ же файле
        for rec in data.get(coll) or []:
            old = rec.get('id')
            if old is None:
                continue
            key = key_of(coll, rec)
            found = existing.get(coll, {}).get(key)
            if found is not None:
                mapping[old] = found
                continue
            # Тот же ключ уже встречался в файле — это дубль, и он обязан получить
            # ТОТ ЖЕ номер. Иначе слияние оставит одну запись, а ссылки на второй
            # номер повиснут: строка микроплана окажется привязана к контракту,
            # которого больше нет.
            if key is not None and key in seen:
                mapping[old] = seen[key]
                continue
            while nxt in used:
                nxt += 1
            mapping[old] = nxt
            if key is not None:
                seen[key] = nxt
            used.add(nxt)
            nxt += 1
        id_map[coll] = mapping
    return id_map


def apply_id_map(data: dict, id_map: dict) -> dict:
    """Переписать во входных данных сами id и все ссылки на них.

    Возвращает НОВЫЙ объект: входной файл не трогаем, чтобы при ошибке было с чем
    сравнить.
    """
    out = {k: v for k, v in data.items()}

    for coll, mapping in id_map.items():
        rows = []
        for rec in data.get(coll) or []:
            rec = dict(rec)
            if rec.get('id') in mapping:
                rec['id'] = mapping[rec['id']]
            rows.append(rec)
        if coll in data:
            out[coll] = rows

    for coll, refs in REFERENCES.items():
        rows = []
        for rec in data.get(coll) or []:
            rec = dict(rec)
            for field, target in refs.items():
                old = rec.get(field)
                if old is not None and old in id_map.get(target, {}):
                    rec[field] = id_map[target][old]
            rows.append(rec)
        if coll in data:
            out[coll] = rows

    return out


def split_new_and_existing(coll: str, rows: list, existing_keys: dict):
    """Разложить записи на «обновить» и «создать».

    Внутри одного файла тоже бывают повторы (например, две одинаковые правки дня);
    второй такой записи мы даём тот же адрес, что и первой, — иначе слияние само
    создало бы дубль, от которого и должно избавлять.
    """
    to_update, to_create = [], []
    seen = {}
    for rec in rows or []:
        key = key_of(coll, rec)
        if key is None:
            to_create.append(rec)
            continue
        if key in seen:
            # Повтор внутри файла — тоже обновление, а не вторая запись. Считать
            # его добавлением значит соврать в отчёте: человек увидит «добавлено
            # 2», а в базе окажется одна строка, и пойдёт искать пропажу.
            to_update.append((seen[key], rec))
            continue
        found = existing_keys.get(key)
        seen[key] = found
        if found is not None:
            to_update.append((found, rec))
        else:
            to_create.append(rec)
    return to_update, to_create


# ── Слияние двух состояний ──────────────────────────────────────────────────

#: Коллекции-списки в выгрузке (порядок не важен, важен состав).
LIST_COLLS = (
    'bases', 'ops', 'deliveryMatrix', 'holidays', 'nomenclature',
    'contracts', 'customerOrders', 'orders', 'orderLinks',
    'macroplan', 'macroEff', 'schedules', 'scheduleMonthOverrides',
    'calOverrides', 'manualFrv', 'microplan', 'planBaseline',
)

#: Словари «номер заказа → норма».
DICT_COLLS = ('tcRcOverrides', 'tcShOverrides')


def index_by_key(rows: list, coll: str) -> dict:
    """{деловой ключ: id} по уже загруженным записям."""
    out = {}
    for rec in rows or []:
        key = key_of(coll, rec)
        if key is not None and rec.get('id') is not None:
            out.setdefault(key, rec['id'])
    return out


def next_id_for(rows: list) -> int:
    """С какого номера выдавать новые id в этой коллекции."""
    ids = [r.get('id') for r in rows or [] if isinstance(r.get('id'), int)]
    return (max(ids) + 1) if ids else 1


def merge_states(base: dict, incoming: dict) -> dict:
    """Состояние базы + входная выгрузка. Дубли обновляются, остальное добавляется.

    Ссылки во `incoming` должны быть уже переотображены (`apply_id_map`), иначе
    записи привяжутся к чужим контрактам и бригадам.
    """
    out = {k: v for k, v in base.items()}

    for coll in LIST_COLLS:
        base_rows = list(base.get(coll) or [])
        inc_rows = list(incoming.get(coll) or [])
        if not inc_rows:
            out[coll] = base_rows
            continue

        by_key, no_key = {}, []
        for i, rec in enumerate(base_rows):
            key = key_of(coll, rec)
            if key is None:
                no_key.append(i)
            else:
                by_key.setdefault(key, i)

        merged = list(base_rows)
        for rec in inc_rows:
            key = key_of(coll, rec)
            if key is not None and key in by_key:
                # Дубль: входная запись заменяет прежнюю целиком. Сливать поля
                # по одному нельзя — «пустое поле» и «поля не было» в выгрузке
                # значат разное, и частичное слияние молча смешало бы их.
                merged[by_key[key]] = rec
            else:
                if key is not None:
                    by_key[key] = len(merged)
                merged.append(rec)
        out[coll] = merged

    for coll in DICT_COLLS:
        merged = dict(base.get(coll) or {})
        merged.update(incoming.get(coll) or {})
        out[coll] = merged

    # Счётчик id должен перекрывать оба набора, иначе следующая запись,
    # созданная в интерфейсе, столкнётся с существующей.
    out['nextId'] = max(int(base.get('nextId') or 1),
                        int(incoming.get('nextId') or 1),
                        *(next_id_for(out.get(c)) for c in LIST_COLLS))

    for key, value in incoming.items():
        if key not in out:
            out[key] = value
    return out


def prepare(base: dict, incoming: dict) -> tuple[dict, dict]:
    """Полный путь слияния: перенумеровать ссылки и слить.

    Возвращает (объединённое состояние, сводку что добавилось и что обновилось).
    """
    existing = {c: index_by_key(base.get(c), c) for c in LIST_COLLS}
    next_ids = {c: next_id_for(base.get(c)) for c in LIST_COLLS}

    id_map = build_id_map(incoming, existing, next_ids)
    incoming = apply_id_map(incoming, id_map)

    stats = {}
    for coll in LIST_COLLS:
        rows = incoming.get(coll) or []
        if not rows:
            continue
        upd, new = split_new_and_existing(coll, rows, existing.get(coll, {}))
        if upd or new:
            stats[coll] = {'обновлено': len(upd), 'добавлено': len(new)}

    return merge_states(base, incoming), stats
