# -*- coding: utf-8 -*-
"""Слияние выгрузок: что должно получиться и что потерять нельзя.

Проверяется не «функция отработала», а свойства, ради которых слияние и делалось:

* второй файл не затирает первый;
* совпадающая запись обновляется, а не задваивается;
* ссылки (микроплан → бригада, микроплан → контракт) после слияния указывают
  туда же, куда указывали в своём файле, — даже когда id в двух файлах совпали
  у РАЗНЫХ записей. Это главная ловушка: счётчик id в каждом браузере свой.

Запуск:  ../server_venv/bin/python -m pytest core/services/test_merge.py -q
"""
from __future__ import annotations

from core.services import merge as mg


def state(**kw):
    """Пустое состояние с нужными коллекциями."""
    base = {c: [] for c in mg.LIST_COLLS}
    base.update({'tcRcOverrides': {}, 'tcShOverrides': {}, 'nextId': 1})
    base.update(kw)
    return base


def contract(cid, number, **kw):
    return dict({'id': cid, 'number': number, 'name': 'Изделие', 'quantity': 100}, **kw)


def schedule(sid, name, op='ОП-1', workshop='Швейный цех'):
    return {'id': sid, 'name': name, 'op': op, 'workshop': workshop, 'workers': []}


def micro(mid, date, order, schedule_id, contract_id=None, op='ОП-1',
          stage='ШЦ', workshop='Швейный цех', plan=10):
    return {'id': mid, 'date': date, 'op': op, 'workshop': workshop, 'stage': stage,
            'releaseOrder': order, 'scheduleId': schedule_id,
            'contractId': contract_id, 'planRelease': plan, 'factRelease': 0,
            'articleItems': [], 'subOrders': [], 'launchEntries': [], 'workers': []}


# ── Ничего не теряется ──────────────────────────────────────────────────────

def test_second_file_does_not_wipe_the_first():
    """Ради этого всё и затевалось: две выгрузки складываются, а не затирают."""
    first = state(contracts=[contract(1, 'К-01')],
                  orders=[{'id': 1, 'number': 'ЗАК-0001', 'quantity': 50}])
    second = state(contracts=[contract(1, 'К-02')],
                   orders=[{'id': 1, 'number': 'ЗАК-0002', 'quantity': 70}])

    merged, stats = mg.prepare(first, second)

    assert {c['number'] for c in merged['contracts']} == {'К-01', 'К-02'}
    assert {o['number'] for o in merged['orders']} == {'ЗАК-0001', 'ЗАК-0002'}
    assert stats['contracts'] == {'обновлено': 0, 'добавлено': 1}


def test_same_record_is_updated_not_duplicated():
    """Совпал деловой ключ — запись одна, с новыми значениями."""
    first = state(contracts=[contract(1, 'К-01', quantity=100)])
    second = state(contracts=[contract(77, 'К-01', quantity=250)])

    merged, stats = mg.prepare(first, second)

    assert len(merged['contracts']) == 1, 'контракт задвоился'
    assert merged['contracts'][0]['quantity'] == 250, 'взято старое значение'
    assert stats['contracts'] == {'обновлено': 1, 'добавлено': 0}


def test_trailing_space_is_the_same_record():
    """«К-01 » и «К-01» — один контракт: пробел в выгрузке не должен плодить дубли."""
    merged, _ = mg.prepare(state(contracts=[contract(1, 'К-01')]),
                           state(contracts=[contract(5, 'К-01 ')]))
    assert len(merged['contracts']) == 1


# ── Ссылки остаются верными ─────────────────────────────────────────────────

def test_colliding_ids_do_not_mix_records():
    """Два файла, в каждом бригада id=1, но это РАЗНЫЕ бригады.

    Если сливать по id, микроплан второго файла привяжется к бригаде первого —
    и смена окажется у чужой площадки. Это самая дорогая ошибка слияния: на
    экране всё выглядит правдоподобно.
    """
    first = state(schedules=[schedule(1, 'Бригада А', op='ОП-1')],
                  microplan=[micro(1, '2026-08-03', 'ЗАК-0001', schedule_id=1)])
    second = state(schedules=[schedule(1, 'Бригада Б', op='ОП-2')],
                   microplan=[micro(1, '2026-08-03', 'ЗАК-0002', schedule_id=1,
                                    op='ОП-2')])

    merged, _ = mg.prepare(first, second)

    assert len(merged['schedules']) == 2, 'разные бригады слиплись в одну'
    by_name = {s['name']: s['id'] for s in merged['schedules']}
    assert by_name['Бригада А'] != by_name['Бригада Б']

    rows = {r['releaseOrder']: r for r in merged['microplan']}
    assert rows['ЗАК-0001']['scheduleId'] == by_name['Бригада А']
    assert rows['ЗАК-0002']['scheduleId'] == by_name['Бригада Б'], \
        'строка привязана к чужой бригаде'


def test_contract_reference_follows_the_record():
    """Микроплан и макроплан ссылаются на свой контракт, а не на однономерный."""
    first = state(contracts=[contract(1, 'К-01')],
                  microplan=[micro(1, '2026-08-03', 'ЗАК-0001', 5, contract_id=1)],
                  schedules=[schedule(5, 'Бригада А')])
    second = state(contracts=[contract(1, 'К-02')],
                   macroplan=[{'id': 1, 'contractId': 1, 'articleItem': 'А-1',
                               'op': 'ОП-2', 'workshop': 'Швейный цех',
                               'month': '2026-08', 'qtySew': 500,
                               'volumeType': 'main', 'orderNums': []}])

    merged, _ = mg.prepare(first, second)

    by_number = {c['number']: c['id'] for c in merged['contracts']}
    assert merged['microplan'][0]['contractId'] == by_number['К-01']
    assert merged['macroplan'][0]['contractId'] == by_number['К-02']


def test_existing_record_keeps_its_id():
    """Если запись уже в базе, входная получает ЕЁ id — иначе ссылки в базе
    указывали бы на исчезнувший номер."""
    first = state(schedules=[schedule(7, 'Бригада А')],
                  microplan=[micro(1, '2026-08-03', 'ЗАК-0001', 7)])
    second = state(schedules=[schedule(99, 'Бригада А')],
                   microplan=[micro(1, '2026-08-04', 'ЗАК-0001', 99)])

    merged, _ = mg.prepare(first, second)

    assert len(merged['schedules']) == 1
    assert merged['schedules'][0]['id'] == 7, 'бригада сменила id — ссылки в базе поедут'
    assert {r['scheduleId'] for r in merged['microplan']} == {7}


# ── Микроплан ───────────────────────────────────────────────────────────────

def test_microplan_same_shift_is_updated():
    """Одна и та же смена (дата+ОП+передел+бригада+заказ) — одна строка."""
    first = state(schedules=[schedule(1, 'Бригада А')],
                  microplan=[micro(10, '2026-08-03', 'ЗАК-0001', 1, plan=10)])
    second = state(schedules=[schedule(1, 'Бригада А')],
                   microplan=[micro(50, '2026-08-03', 'ЗАК-0001', 1, plan=42)])

    merged, stats = mg.prepare(first, second)

    assert len(merged['microplan']) == 1, 'смена задвоилась'
    assert merged['microplan'][0]['planRelease'] == 42
    assert stats['microplan'] == {'обновлено': 1, 'добавлено': 0}


def test_different_days_and_orders_are_kept():
    """Разные дни и разные заказы — разные строки."""
    first = state(schedules=[schedule(1, 'Бригада А')],
                  microplan=[micro(1, '2026-08-03', 'ЗАК-0001', 1)])
    second = state(schedules=[schedule(1, 'Бригада А')],
                   microplan=[micro(1, '2026-08-04', 'ЗАК-0001', 1),
                              micro(2, '2026-08-03', 'ЗАК-0002', 1)])

    merged, _ = mg.prepare(first, second)
    assert len(merged['microplan']) == 3


# ── Прочее ──────────────────────────────────────────────────────────────────

def test_norms_by_order_are_merged():
    """Нормы по заказу — словарь; входные значения перекрывают прежние."""
    first = state(tcRcOverrides={'ЗАК-0001': 10.0})
    second = state(tcRcOverrides={'ЗАК-0001': 12.5, 'ЗАК-0002': 7.0})

    merged, _ = mg.prepare(first, second)
    assert merged['tcRcOverrides'] == {'ЗАК-0001': 12.5, 'ЗАК-0002': 7.0}


def test_next_id_covers_both_files():
    """Счётчик id обязан перекрыть оба набора, иначе следующая запись,
    созданная в интерфейсе, столкнётся с существующей."""
    first = state(contracts=[contract(100, 'К-01')], nextId=101)
    second = state(contracts=[contract(500, 'К-02')], nextId=501)

    merged, _ = mg.prepare(first, second)
    used = {c['id'] for c in merged['contracts']}
    assert merged['nextId'] > max(used)


def test_merging_into_empty_base_keeps_everything():
    """Первая загрузка в пустую базу — это тоже слияние, и терять нечего."""
    incoming = state(contracts=[contract(1, 'К-01')],
                     schedules=[schedule(1, 'Бригада А')],
                     microplan=[micro(1, '2026-08-03', 'ЗАК-0001', 1, contract_id=1)])

    merged, _ = mg.prepare(state(), incoming)

    assert len(merged['contracts']) == 1
    assert merged['microplan'][0]['scheduleId'] == merged['schedules'][0]['id']
    assert merged['microplan'][0]['contractId'] == merged['contracts'][0]['id']


def test_duplicates_inside_one_file_collapse():
    """Повтор внутри самого файла не должен превращаться в две записи."""
    incoming = state(contracts=[contract(1, 'К-01', quantity=10),
                                contract(2, 'К-01', quantity=20)])
    merged, stats = mg.prepare(state(), incoming)

    assert len(merged['contracts']) == 1, 'дубль внутри файла остался'
    assert merged['contracts'][0]['quantity'] == 20, 'осталась не последняя версия'
    assert stats['contracts'] == {'обновлено': 1, 'добавлено': 1}


def test_input_file_is_not_modified():
    """Входные данные не портим: при разборе беды должно быть с чем сравнить."""
    incoming = state(contracts=[contract(1, 'К-02')],
                     schedules=[schedule(1, 'Бригада Б')])
    before = repr(incoming)

    mg.prepare(state(contracts=[contract(1, 'К-01')],
                     schedules=[schedule(1, 'Бригада А')]), incoming)

    assert repr(incoming) == before, 'входной файл изменён на месте'


def test_parts_of_one_kit_are_separate_rows():
    """Изделие из нескольких частей: куртка и брюки в одной смене — ДВЕ строки.

    Найдено на настоящих данных: без артикулов в ключе слияние схлопывало их в
    одну, и половина плана исчезала молча.
    """
    rows = [micro(1, '2026-08-12', 'ЗАК-0005', 9), micro(2, '2026-08-12', 'ЗАК-0005', 9)]
    rows[0]['articleItems'] = ['03-ШЦ-К']      # куртка
    rows[1]['articleItems'] = ['03-ШЦ-Б']      # брюки

    merged, stats = mg.prepare(state(schedules=[schedule(9, 'Бригада А')]),
                               state(schedules=[schedule(9, 'Бригада А')],
                                     microplan=rows))

    assert len(merged['microplan']) == 2, 'части комплекта слиплись в одну строку'
    assert stats['microplan'] == {'обновлено': 0, 'добавлено': 2}


def test_article_order_does_not_split_rows():
    """Порядок артикулов в строке не делает из неё вторую строку."""
    first = state(schedules=[schedule(9, 'Бригада А')],
                  microplan=[dict(micro(1, '2026-08-12', 'ЗАК-0005', 9),
                                  articleItems=['А-1', 'А-2'])])
    second = state(schedules=[schedule(9, 'Бригада А')],
                   microplan=[dict(micro(7, '2026-08-12', 'ЗАК-0005', 9),
                                   articleItems=['А-2', 'А-1'], planRelease=99)])

    merged, _ = mg.prepare(first, second)
    assert len(merged['microplan']) == 1
    assert merged['microplan'][0]['planRelease'] == 99
