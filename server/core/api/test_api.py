# -*- coding: utf-8 -*-
"""Тесты API (фаза 3).

Главное, что здесь проверяется, — не «эндпоинт отвечает 200», а два свойства,
которые легко потерять:

* **Ограничение по ОП работает в QuerySet.** Запрос в обход интерфейса не должен
  отдавать чужие площадки (ТЗ §7а).
* **Оптимистичная блокировка.** Правка поверх устаревшей версии отклоняется с 409,
  а не затирает чужую молча.

Запуск:  ../server_venv/bin/python -m pytest core/api/test_api.py -q
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from core import models as m


@pytest.fixture
def data(db):
    """Две площадки с данными: «Своя» и «Чужая»."""
    for name in ('Своя', 'Чужая'):
        m.Op.objects.create(src_id=hash(name) % 1000, name=name, workshop='Швейный цех',
                            process_type='ШЦ', op_type='БТК', shift_duration=480)
        m.MicroplanRow.objects.create(
            src_id=(1 if name == 'Своя' else 2), date='2026-08-03', op_name=name,
            workshop='Швейный цех', stage='ШЦ', release_order=f'З-{name}',
            plan_release=10, fact_release=0)
        m.MacroplanRow.objects.create(src_id=(11 if name == 'Своя' else 12), op_name=name,
                                      workshop='Швейный цех', month='2026-08', qty_sew=100)
    return True


def _with_roles(user, *names):
    """Выдать роли. Группы создаются по требованию — список ролей живёт в коде."""
    from core.api.roles import group_for
    user.groups.set([group_for(n) for n in names])
    return user


ALL_ROLES = ('Справочники', 'Заказы', 'Макропланирование', 'Микропланирование')


@pytest.fixture
def master(db, data):
    """Мастер, у которого доступ только к своей площадке.

    Роли планирования есть — иначе он не смог бы править вообще ничего, и тесты
    ограничения по ОП проверяли бы не то ограничение, которое им нужно.
    """
    user = User.objects.create_user('master', password='x')
    _with_roles(user, 'Макропланирование', 'Микропланирование')
    m.UserOpAccess.objects.create(user=user, op_name='Своя', can_edit=True)
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def boss(db, data):
    """Пользователь без записей доступа — видит все площадки, но НЕ администратор."""
    user = User.objects.create_user('boss', password='x')
    _with_roles(user, *ALL_ROLES)
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def plain(db, data):
    """Наблюдатель: ни ролей, ни ограничений по ОП. Видит всё, не правит ничего."""
    user = User.objects.create_user('plain', password='x')
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def admin(db, data):
    """Администратор. Признак один — `is_superuser`."""
    user = User.objects.create_superuser('root', password='x')
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_anonymous_is_rejected(data):
    """Без входа API не отдаёт ничего."""
    resp = APIClient().get('/api/microplan/')
    assert resp.status_code in (401, 403), 'аноним не должен видеть данные'


def test_op_scope_hides_foreign_rows(master):
    """Мастер видит только свою площадку — фильтрация в QuerySet, а не в интерфейсе."""
    resp = master.get('/api/microplan/')
    assert resp.status_code == 200
    ops = {r['op_name'] for r in resp.data['results']}
    assert ops == {'Своя'}, f'просочились чужие ОП: {ops}'

    resp = master.get('/api/macroplan/')
    assert {r['op_name'] for r in resp.data['results']} == {'Своя'}


def test_no_access_records_means_all_ops(boss):
    """Отсутствие записей доступа = доступ ко всем ОП."""
    resp = boss.get('/api/microplan/')
    assert resp.status_code == 200
    assert {r['op_name'] for r in resp.data['results']} == {'Своя', 'Чужая'}


def test_foreign_row_not_reachable_by_id(master):
    """Прямое обращение по id к чужой строке тоже закрыто."""
    foreign = m.MicroplanRow.objects.get(op_name='Чужая')
    resp = master.get(f'/api/microplan/{foreign.id}/')
    assert resp.status_code == 404, 'чужая строка не должна открываться по прямой ссылке'


def test_version_conflict_returns_409(master):
    """Правка поверх устаревшей версии отклоняется, а не затирает чужую."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    url = f'/api/microplan/{row.id}/'

    ok = master.patch(url, {'comment': 'первая правка', 'version': row.version}, format='json')
    assert ok.status_code == 200, ok.data
    assert ok.data['version'] == row.version + 1

    # Второй пользователь всё ещё держит старую версию.
    stale = master.patch(url, {'comment': 'вторая правка', 'version': row.version},
                         format='json')
    assert stale.status_code == 409, 'ожидался конфликт версий'
    assert 'version' in stale.data

    row.refresh_from_db()
    assert row.comment == 'первая правка', 'вторая правка не должна была примениться'


def test_recalc_respects_scope_and_does_not_write_by_default(master):
    """Пересчёт считает только доступное и по умолчанию ничего не пишет."""
    before = {r.src_id: r.plan_release for r in m.MicroplanRow.objects.all()}
    resp = master.post('/api/microplan/recalc', {'month': '2026-08'}, format='json')
    assert resp.status_code == 200, resp.data
    ops = {r['op'] for r in resp.data['rows']}
    assert ops <= {'Своя'}, f'в пересчёт попали чужие ОП: {ops}'
    assert resp.data['committed'] == 0, 'без commit писать в базу нельзя'
    after = {r.src_id: r.plan_release for r in m.MicroplanRow.objects.all()}
    assert before == after, 'база изменилась без commit'


def test_validate_returns_severity(master):
    """Проверки возвращают уровень: stop блокирует, warn — нет."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    resp = master.post('/api/microplan/validate',
                       {'src_id': row.src_id, 'plan': 10 ** 9, 'fact': 0}, format='json')
    assert resp.status_code == 200, resp.data
    checks = resp.data['checks']
    assert 'plan_order_limit' in checks and 'stage_fact' in checks
    for name, v in checks.items():
        assert v['severity'] in (None, 'stop', 'warn'), (name, v)
    # Блокирующими могут быть только проверки факта.
    for name in resp.data['blocking']:
        assert name in ('stage_fact', 'fact_vs_order_qty', 'release_vs_launches'), name


def test_validate_foreign_row_is_hidden(master):
    """Чужую строку нельзя проверить даже зная её идентификатор."""
    foreign = m.MicroplanRow.objects.get(op_name='Чужая')
    resp = master.post('/api/microplan/validate',
                       {'src_id': foreign.src_id, 'plan': 1}, format='json')
    assert resp.status_code == 404


def test_analytics_requires_month_and_scopes(master):
    """Аналитика требует месяц и считает только по доступным ОП."""
    assert master.get('/api/analytics').status_code == 400
    resp = master.get('/api/analytics?month=2026-08')
    assert resp.status_code == 200, resp.data
    assert resp.data['month'] == '2026-08'
    assert resp.data['rows_micro'] == 1, 'должна учитываться только своя площадка'
    assert 'weeks' in resp.data and 'baseline' in resp.data


def test_export_available(boss):
    """Аварийный люк: выгрузка в формате приложения."""
    resp = boss.get('/api/export')
    assert resp.status_code == 200
    assert 'microplan' in resp.data and 'tcRcOverrides' in resp.data


# ── Вложенная запись (без неё микроплан не редактируется) ───────────────────

def test_nested_write_replaces_sub_orders(master):
    """Подзаказы, состав и запуски правятся вместе со строкой.

    Семантика замещающая: присланный список полностью заменяет прежний. Не
    прислали ключ — прежнее не трогаем.
    """
    row = m.MicroplanRow.objects.get(op_name='Своя')
    url = f'/api/microplan/{row.id}/'

    resp = master.patch(url, {
        'version': row.version,
        'sub_orders': [{'release_order': 'ЗАК-2', 'plan_release': 5,
                        'article_items': ['А-1']}],
        'workers': [{'name': 'Смена 1', 'staff_count': 8, 'shift_time': 480, 'eff_pct': 100}],
        'article_items': ['А-1'],
    }, format='json')
    assert resp.status_code == 200, resp.data
    row.refresh_from_db()
    assert row.sub_orders.count() == 1
    assert row.workers.count() == 1
    assert [x.article_item for x in row.article_items.all()] == ['А-1']

    sub_id = row.sub_orders.first().id
    # Обновление существующего подзаказа — по id, запись должна сохраниться.
    resp = master.patch(url, {'version': row.version,
                              'sub_orders': [{'id': sub_id, 'release_order': 'ЗАК-2',
                                              'plan_release': 9}]}, format='json')
    assert resp.status_code == 200, resp.data
    row.refresh_from_db()
    assert row.sub_orders.count() == 1
    assert row.sub_orders.first().id == sub_id, 'подзаказ пересоздан вместо обновления'
    assert float(row.sub_orders.first().plan_release) == 9

    # Пустой список очищает.
    resp = master.patch(url, {'version': row.version, 'sub_orders': []}, format='json')
    assert resp.status_code == 200
    row.refresh_from_db()
    assert row.sub_orders.count() == 0
    assert row.workers.count() == 1, 'не присланный ключ не должен очищаться'


# ── Вход ────────────────────────────────────────────────────────────────────

def test_login_flow(data):
    """Вход по логину, `me` отдаёт доступные ОП, выход закрывает сессию."""
    User.objects.create_user('petr', password='secret')
    client = APIClient()

    bad = client.post('/api/login', {'username': 'petr', 'password': 'wrong'}, format='json')
    assert bad.status_code == 401

    ok = client.post('/api/login', {'username': 'petr', 'password': 'secret'}, format='json')
    assert ok.status_code == 200, ok.data
    assert ok.data['username'] == 'petr'
    assert ok.data['all_ops'] is True, 'без записей доступа видны все ОП'

    me = client.get('/api/me')
    assert me.status_code == 200 and me.data['username'] == 'petr'

    client.post('/api/logout')
    assert client.get('/api/me').status_code in (401, 403)


def test_me_reports_editable_ops(master):
    """`me` различает, где можно читать, а где править."""
    resp = master.get('/api/me')
    assert resp.status_code == 200
    assert resp.data['all_ops'] is False
    assert resp.data['ops_read'] == ['Своя']
    assert resp.data['ops_edit'] == ['Своя']


# ── Загрузка состояния ──────────────────────────────────────────────────────

def test_import_is_admin_only(master, boss):
    """Загрузка состояния замещает всё, поэтому доступна только администратору."""
    payload = {'microplan': [], 'macroplan': [], 'orders': [], 'nomenclature': [], 'ops': []}
    assert master.post('/api/import', payload, format='json').status_code == 403
    # boss не суперпользователь — тоже нельзя.
    assert boss.post('/api/import', payload, format='json').status_code == 403


def test_import_rejects_garbage(db):
    """Мусор вместо выгрузки отклоняется до записи в базу."""
    admin = User.objects.create_superuser('root2', password='x')
    client = APIClient()
    client.force_authenticate(admin)
    assert client.post('/api/import', {'что-то': 1}, format='json').status_code == 400


def test_new_endpoints_are_reachable(boss):
    """Справочники, которых не хватало для перевода интерфейса."""
    for url in ('/api/cal-overrides/', '/api/manual-frv/', '/api/holidays/',
                '/api/delivery-matrix/', '/api/plan-baseline/', '/api/order-links/',
                '/api/time-costs/'):
        assert boss.get(url).status_code == 200, url


# ── Перевод остальных вкладок (фаза 4) ──────────────────────────────────────
#
# Проверяется не «сохранилось», а то, что при сохранении НЕ теряется. Интерфейс
# держит запись целиком и шлёт её целиком, поэтому любое поле, о котором API не
# знает, обнуляется молча — на экране всё правильно, а в базе пусто.

def test_absent_field_survives_round_trip(boss):
    """«Поля не было» ≠ «поле равно значению по умолчанию».

    У работника пустая смена означает «как у ОП». Если API отдаст 480 и примет 480
    обратно как настоящее значение, мощность дня уедет, и никто этого не заметит.
    """
    sched = m.Schedule.objects.create(src_id=91, name='Смена 1', op_name='Своя',
                                      workshop='Швейный цех', shift_time=720)
    m.ScheduleWorker.objects.create(schedule=sched, ordinal=0, src_id=1, name='Смена 1',
                                    staff_count=2, extra={'__absent': ['shiftTime']})

    got = boss.get(f'/api/schedules/{sched.pk}/').json()
    assert got['workers'][0]['absent_fields'] == ['shiftTime']

    resp = boss.patch(f'/api/schedules/{sched.pk}/',
                      {'version': got['version'], 'workers': got['workers']}, format='json')
    assert resp.status_code == 200, resp.data
    again = boss.get(f'/api/schedules/{sched.pk}/').json()
    assert again['workers'][0]['absent_fields'] == ['shiftTime'], \
        'признак отсутствия потерян — смена работника станет настоящими 480'


def test_unmodelled_field_survives_round_trip(boss):
    """Несмоделированные поля живут в `extra` и обязаны вернуться.

    В правках дня так хранится СОСТАВ РАБОТНИКОВ на конкретный день.
    """
    ov = m.CalOverride.objects.create(schedule_src_id=103, date='2026-08-03',
                                      day_type='vacation',
                                      extra={'workers': [{'id': 1, 'staffCount': 3}]})
    got = boss.get(f'/api/cal-overrides/{ov.pk}/').json()
    assert got['extra_fields']['workers'][0]['staffCount'] == 3

    boss.patch(f'/api/cal-overrides/{ov.pk}/',
               {'version': got['version'], 'day_type': 'work',
                'extra_fields': got['extra_fields']}, format='json')
    ov.refresh_from_db()
    assert ov.day_type == 'work'
    assert ov.extra['workers'][0]['staffCount'] == 3, 'состав дня стёрт при сохранении'


def test_order_stage_ops_can_be_rewritten(boss):
    """Повторная запись тех же ОП заказа не должна упираться в уникальность.

    Интерфейс шлёт ОП передела без идентификаторов, поэтому они пересоздаются.
    Если удалять пропавшие ПОСЛЕ создания новых, вторая же отправка падает 500 —
    и правка заказа перестаёт сохраняться.
    """
    order = m.ProductionOrder.objects.create(src_id=64, number='ЗАК-0002', quantity=7765)
    m.ProductionOrderOp.objects.create(order=order, stage='ШЦ', op_name='Своя', ordinal=0)

    body = {'version': 1, 'stage_ops': [{'stage': 'ШЦ', 'op_name': 'Своя'},
                                        {'stage': 'РЦ', 'op_name': 'Своя'}]}
    assert boss.patch(f'/api/orders/{order.pk}/', body, format='json').status_code == 200
    body['version'] = 2
    resp = boss.patch(f'/api/orders/{order.pk}/', body, format='json')
    assert resp.status_code == 200, 'повторная отправка тех же ОП сломалась'
    assert set(order.stage_ops.values_list('stage', flat=True)) == {'ШЦ', 'РЦ'}


def test_macroplan_order_numbers_are_writable(master):
    """Без привязки заказа норма из макроплана не доходит до микроплана (§4.3),
    поэтому список заказов строки обязан не только читаться, но и писаться."""
    row = m.MacroplanRow.objects.get(src_id=11)
    got = master.get(f'/api/macroplan/{row.pk}/').json()
    assert got['order_numbers'] == []

    resp = master.patch(f'/api/macroplan/{row.pk}/',
                        {'version': got['version'], 'order_numbers': ['ЗАК-0011', 'ЗАК-0012']},
                        format='json')
    assert resp.status_code == 200, resp.data
    assert list(row.order_nums.order_by('ordinal').values_list('order_number', flat=True)) \
        == ['ЗАК-0011', 'ЗАК-0012']


def test_nomenclature_route_is_writable(boss):
    """Маршрут карточки несёт НОРМУ. Если он только читается, правка нормы в
    серверном режиме уходит в никуда, а план продолжает считаться по старой."""
    nom = m.Nomenclature.objects.create(src_id=700, article_gp='01.100', name='Куртка')
    m.NomRoute.objects.create(nomenclature=nom, ordinal=0, stage='ШЦ',
                              article_item='01.100', time_cost=1)

    got = boss.get(f'/api/nomenclature/{nom.pk}/').json()
    route = got['route']
    route[0]['time_cost'] = '2.5000'
    resp = boss.patch(f'/api/nomenclature/{nom.pk}/',
                      {'version': got['version'], 'route': route}, format='json')
    assert resp.status_code == 200, resp.data
    assert float(nom.route.first().time_cost) == 2.5


def test_nomenclature_route_overrides_are_writable(boss):
    """Маршрут на уровень ОП (§4.1): разные площадки шьют изделие по-разному.
    Пустой список означает «переопределений нет» — тогда действует базовый."""
    nom = m.Nomenclature.objects.create(src_id=701, article_gp='01.000001', name='Костюм')
    body = {'version': 1, 'route_overrides': [
        {'op_name': 'ОП-5', 'items': [
            {'stage': 'ШЦ', 'article_item': '01.000001', 'time_cost': '40.0000'}]}]}
    assert boss.patch(f'/api/nomenclature/{nom.pk}/', body, format='json').status_code == 200
    ov = nom.route_overrides.get()
    assert ov.op_name == 'ОП-5'
    assert float(ov.items.get().time_cost) == 40.0

    body = {'version': 2, 'route_overrides': []}
    assert boss.patch(f'/api/nomenclature/{nom.pk}/', body, format='json').status_code == 200
    assert nom.route_overrides.count() == 0, 'снятое переопределение осталось'


def test_trailing_space_is_not_trimmed(boss):
    """DRF по умолчанию режет пробелы. Название «Футболка » — данные пользователя,
    и молча менять их сохранение не должно."""
    c = m.Contract.objects.create(src_id=54, number='К-03', name='Футболка ')
    boss.patch(f'/api/contracts/{c.pk}/', {'version': 1, 'name': 'Футболка '},
               format='json')
    c.refresh_from_db()
    assert c.name == 'Футболка ', 'пробел срезан при сохранении'


def test_contract_nested_json_is_exposed(boss):
    """Вкладка «Контракты» правит график поставок, дедлайны и артикулы.
    В схеме это JSON-колонки; не отдать их — значит не дать вкладке работать."""
    c = m.Contract.objects.create(
        src_id=14, number='К-01', quantity=17331,
        delivery_schedule=[{'month': '2026-06', 'quantity': 12453}],
        deadlines=[{'id': 76, 'date': '2026-04-30', 'quantity': 60000}],
        contract_article_items=[{'id': 77, 'articleItem': '01.000001'}])
    got = boss.get(f'/api/contracts/{c.pk}/').json()
    assert got['delivery_schedule'][0]['quantity'] == 12453
    assert got['deadlines'][0]['date'] == '2026-04-30'
    assert got['contract_article_items'][0]['articleItem'] == '01.000001'


def test_macro_eff_is_op_scoped(master):
    """Эффективность (ОП, цех, месяц) — тоже данные площадки, и режется по ОП."""
    m.MacroEff.objects.create(src_id=1, op_name='Своя', workshop='Швейный цех',
                              month='2026-08', eff=95)
    m.MacroEff.objects.create(src_id=2, op_name='Чужая', workshop='Швейный цех',
                              month='2026-08', eff=80)
    got = master.get('/api/macro-eff/').json()
    names = {r['op_name'] for r in got['results']}
    assert names == {'Своя'}, 'видна чужая площадка'


def test_manual_frv_segments_are_writable(boss):
    """Варианты ФРВ (люди × смена × дней) — то, из чего считается мощность месяца."""
    frv = m.ManualFrv.objects.create(src_id=31, op_name='Своя', workshop='Раскройный цех',
                                     month='2026-06', eff_pct=100)
    body = {'version': 1, 'segments': [
        {'src_id': 30, 'name': '', 'people': 10, 'shift_min': 480, 'work_days': 19,
         'absent_fields': ['effPct']}]}
    assert boss.patch(f'/api/manual-frv/{frv.pk}/', body, format='json').status_code == 200
    seg = frv.segments.get()
    assert seg.shift_min == 480 and float(seg.people) == 10
    assert seg.extra.get('__absent') == ['effPct']


# ── Роли: что человек может править ─────────────────────────────────────────
#
# Два измерения независимы, и проверять надо оба: раздел («что») и площадка
# («где»). Право на запись — их пересечение. Тесты ниже отдельно ломают каждое
# из них, иначе одно могло бы молча закрывать дыру во втором.

def test_reading_is_not_limited_by_roles(plain):
    """Без ролей читать можно всё доступное по ОП.

    Аналитика считается на клиенте по данным сервера и берёт микроплан, макроплан
    и справочники сразу. Запрет читать раздел сломал бы её тому, кому её и открыли.
    """
    for url in ('/api/microplan/', '/api/macroplan/', '/api/nomenclature/',
                '/api/analytics?month=2026-08'):
        assert plain.get(url).status_code == 200, url


def test_no_roles_means_no_writing(plain):
    """Наблюдатель не правит ничего, хотя площадки ему видны все."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    was = row.comment

    resp = plain.patch(f'/api/microplan/{row.id}/',
                       {'version': row.version, 'comment': 'правка'}, format='json')
    assert resp.status_code == 403, resp.data
    row.refresh_from_db()
    assert row.comment == was, 'запись прошла, несмотря на отказ'


def test_role_opens_only_its_own_section(db, data):
    """Макропланировщик правит макроплан и не трогает микроплан.

    Это главное свойство ролей: раньше право было одно на всё, и человек,
    заполняющий макроплан, мог случайно переписать чужую смену.
    """
    user = _with_roles(User.objects.create_user('makro', password='x'), 'Макропланирование')
    client = APIClient()
    client.force_authenticate(user)

    macro = m.MacroplanRow.objects.get(src_id=11)
    ok = client.patch(f'/api/macroplan/{macro.pk}/',
                      {'version': macro.version, 'qty_sew': 150}, format='json')
    assert ok.status_code == 200, ok.data

    micro = m.MicroplanRow.objects.get(op_name='Своя')
    denied = client.patch(f'/api/microplan/{micro.id}/',
                          {'version': micro.version, 'comment': 'не должно пройти'},
                          format='json')
    assert denied.status_code == 403
    micro.refresh_from_db()
    assert micro.comment != 'не должно пройти'


def test_role_and_op_must_both_allow(db, data):
    """Роль без права правки на площадке не даёт записи.

    Раздел и площадка перемножаются: микропланировщик Бухары не правит Ташкент.
    """
    user = _with_roles(User.objects.create_user('shy', password='x'), 'Микропланирование')
    m.UserOpAccess.objects.create(user=user, op_name='Своя', can_edit=False)
    client = APIClient()
    client.force_authenticate(user)

    row = m.MicroplanRow.objects.get(op_name='Своя')
    resp = client.patch(f'/api/microplan/{row.id}/',
                        {'version': row.version, 'comment': 'нет права на ОП'}, format='json')
    assert resp.status_code == 403, resp.data
    row.refresh_from_db()
    assert row.comment != 'нет права на ОП'


def test_recalc_commit_needs_microplan_role(db, data):
    """Пересчёт с записью меняет те же строки, что и ручной ввод, — и требует того же права."""
    user = _with_roles(User.objects.create_user('an', password='x'), 'Макропланирование')
    m.UserOpAccess.objects.create(user=user, op_name='Своя', can_edit=True)
    client = APIClient()
    client.force_authenticate(user)

    before = {r.src_id: r.plan_release for r in m.MicroplanRow.objects.all()}
    resp = client.post('/api/microplan/recalc',
                       {'month': '2026-08', 'commit': True}, format='json')
    assert resp.status_code == 403, resp.data
    after = {r.src_id: r.plan_release for r in m.MicroplanRow.objects.all()}
    assert before == after, 'пересчёт записал план в обход прав'


def test_me_reports_sections(db, data):
    """`me` отдаёт разделы — по ним интерфейс решает, что показывать на правку."""
    user = _with_roles(User.objects.create_user('mk', password='x'), 'Микропланирование')
    client = APIClient()
    client.force_authenticate(user)

    me = client.get('/api/me').json()
    assert me['roles'] == ['Микропланирование']
    assert set(me['sections_edit']) == {'microplan', 'schedule'}
    assert me['all_sections'] is False


def test_admin_edits_everything(admin):
    """У администратора разделов нет — потому что доступны все."""
    me = admin.get('/api/me').json()
    assert me['is_superuser'] is True
    assert me['all_sections'] is True and me['all_ops'] is True

    row = m.MicroplanRow.objects.get(op_name='Чужая')
    resp = admin.patch(f'/api/microplan/{row.id}/',
                       {'version': row.version, 'comment': 'админ'}, format='json')
    assert resp.status_code == 200, resp.data


def test_unknown_role_is_rejected(admin):
    """Роль — это известное имя из кода, а не произвольная строка от клиента."""
    resp = admin.post('/api/users/', {'username': 'nn', 'password': 'Пароль-длинный-9',
                                     'roles': ['Хозяин мира']}, format='json')
    assert resp.status_code == 400, resp.data
    assert not User.objects.filter(username='nn').exists()


def test_every_writing_path_belongs_to_a_section():
    """Пишущий путь обязан попасть в раздел, иначе он открыт всем на запись.

    Проверка есть и при импорте маршрутов, здесь — чтобы падало осмысленно.
    Исключения ровно два и оба осмысленные: путь только читает, либо закрыт
    проверкой администратора, к которой роли неприменимы.
    """
    from core.api.urls import _admin_only, _writes, router

    loose = [p for p, vs, _ in router.registry
             if getattr(vs, 'section', None) is None
             and _writes(vs) and not _admin_only(vs)]
    assert not loose, f'пишут, но раздела нет: {loose}'


def test_readonly_path_needs_no_section():
    """Журнал изменений раздела не имеет — и не должен: его нельзя править."""
    from core.api.urls import _writes, router

    vs = next(vs for p, vs, _ in router.registry if p == 'changes')
    assert not _writes(vs), 'журнал изменений стал доступен на запись'
    assert getattr(vs, 'section', None) is None


# ── Управление пользователями ───────────────────────────────────────────────

def test_users_are_admin_only(master, plain, admin):
    """Список людей и прав видит только администратор."""
    assert master.get('/api/users/').status_code == 403
    assert plain.get('/api/users/').status_code == 403
    assert admin.get('/api/users/').status_code == 200


def test_admin_creates_user_with_roles_and_ops(admin):
    """Создание человека: пароль, роли и площадки одним запросом."""
    resp = admin.post('/api/users/', {
        'username': 'novyi', 'password': 'Длинный-Пароль-77',
        'roles': ['Микропланирование'],
        'ops': [{'op_name': 'Своя', 'can_edit': True},
                {'op_name': 'Чужая', 'can_edit': False}],
    }, format='json')
    assert resp.status_code == 201, resp.data

    user = User.objects.get(username='novyi')
    assert user.check_password('Длинный-Пароль-77'), 'пароль сохранён не хешем'
    assert list(user.groups.values_list('name', flat=True)) == ['Микропланирование']
    assert {(a.op_name, a.can_edit) for a in user.op_access.all()} \
        == {('Своя', True), ('Чужая', False)}
    assert resp.data['all_ops'] is False


def test_password_is_never_returned(admin):
    """Пароль не должен возвращаться ни в каком виде."""
    admin.post('/api/users/', {'username': 'tihiy', 'password': 'Длинный-Пароль-77'},
               format='json')
    body = admin.get('/api/users/').json()
    text = str(body)
    assert 'password' not in body['results'][0]
    assert 'Длинный-Пароль-77' not in text
    assert 'pbkdf2' not in text, 'хеш пароля тоже наружу не отдаём'


def test_weak_password_is_rejected(admin):
    """Пароль проверяется правилами Django, а не принимается любым."""
    resp = admin.post('/api/users/', {'username': 'слабый', 'password': '123'},
                      format='json')
    assert resp.status_code == 400, resp.data
    assert not User.objects.filter(username='слабый').exists()


def test_admin_can_promote_another_admin(admin):
    """Администратор назначает администратора — иначе всё держится на одном человеке."""
    user = User.objects.create_user('preemnik', password='x')
    resp = admin.patch(f'/api/users/{user.pk}/', {'is_superuser': True}, format='json')
    assert resp.status_code == 200, resp.data

    user.refresh_from_db()
    assert user.is_superuser is True
    assert user.is_staff is True, 'без is_staff новый админ не войдёт в админку Django'

    client = APIClient()
    client.force_authenticate(user)
    assert client.get('/api/users/').status_code == 200, 'назначенный админ не получил прав'


def test_last_admin_cannot_be_stripped(admin):
    """Снять последнего администратора нельзя — управлять правами станет некому."""
    me = User.objects.get(username='root')
    resp = admin.patch(f'/api/users/{me.pk}/', {'is_superuser': False}, format='json')
    assert resp.status_code == 400, resp.data
    me.refresh_from_db()
    assert me.is_superuser is True

    # Деактивация запирает систему так же надёжно.
    assert admin.patch(f'/api/users/{me.pk}/', {'is_active': False},
                       format='json').status_code == 400
    assert admin.delete(f'/api/users/{me.pk}/').status_code == 400
    assert User.objects.filter(username='root').exists()


def test_admin_can_step_down_when_another_exists(admin):
    """Когда админов двое, сложить полномочия можно."""
    other = User.objects.create_superuser('vtoroy', password='x')
    me = User.objects.get(username='root')
    resp = admin.patch(f'/api/users/{me.pk}/', {'is_superuser': False}, format='json')
    assert resp.status_code == 200, resp.data
    me.refresh_from_db()
    assert me.is_superuser is False and me.is_staff is False
    assert other.is_superuser is True


def test_ops_are_replaced_not_merged(admin):
    """Список площадок присылается целиком: снятый доступ должен исчезнуть."""
    user = User.objects.create_user('smena', password='x')
    m.UserOpAccess.objects.create(user=user, op_name='Своя', can_edit=True)
    m.UserOpAccess.objects.create(user=user, op_name='Чужая', can_edit=True)

    resp = admin.patch(f'/api/users/{user.pk}/',
                       {'ops': [{'op_name': 'Своя', 'can_edit': False}]}, format='json')
    assert resp.status_code == 200, resp.data
    assert {(a.op_name, a.can_edit) for a in user.op_access.all()} == {('Своя', False)}


def test_roles_catalog_is_readable(plain):
    """Справочник ролей нужен интерфейсу, чтобы не хранить их список у себя."""
    body = plain.get('/api/roles').json()
    names = {r['name'] for r in body['roles']}
    assert 'Микропланирование' in names
    assert 'Администратор' not in names, 'админ — это is_superuser, а не роль'
    micro = next(r for r in body['roles'] if r['name'] == 'Микропланирование')
    assert set(micro['sections']) == {'microplan', 'schedule'}
    assert {s['key'] for s in body['sections']} >= {'macroplan', 'microplan', 'refs'}


def test_staff_flag_alone_is_not_admin(db, data):
    """`is_staff` — это доступ в админку Django, а не право раздавать права.

    Если проверять администратора по нему (как делает штатный `IsAdminUser`),
    появится второй способ стать администратором помимо `is_superuser`.
    """
    user = User.objects.create_user('shtat', password='x', is_staff=True)
    client = APIClient()
    client.force_authenticate(user)
    assert client.get('/api/users/').status_code == 403


# ── Журнал изменений ────────────────────────────────────────────────────────
#
# Смысл журнала — ответить «кто поставил этот план». Поэтому проверяется не факт
# записи, а то, что запись пригодна для ответа: есть автор, видно старое и новое
# значение, и чужую площадку через журнал не подсмотреть.

def test_change_is_recorded_with_author_and_diff(master):
    """Правка попадает в журнал с автором и разницей значений."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    was = float(row.plan_release)
    master.patch(f'/api/microplan/{row.id}/',
                 {'version': row.version, 'plan_release': 42}, format='json')

    log = m.ChangeLog.objects.get(entity='microplan', action='update')
    assert log.username == 'master'
    assert log.op_name == 'Своя'
    assert log.src_id == row.src_id
    assert log.changes['plan_release'] == [was, 42.0], log.changes


def test_unchanged_save_is_not_logged(master):
    """Сохранение без изменений журнал не засоряет.

    Интерфейс отправляет запись целиком при любом уходе из поля, и запись «ничего
    не изменил» на каждый такой уход сделала бы журнал нечитаемым.
    """
    row = m.MicroplanRow.objects.get(op_name='Своя')
    resp = master.patch(f'/api/microplan/{row.id}/',
                        {'version': row.version, 'plan_release': row.plan_release},
                        format='json')
    assert resp.status_code == 200
    assert m.ChangeLog.objects.filter(action='update').count() == 0


def test_service_fields_are_not_in_diff(master):
    """`version` меняется при каждом сохранении и о сути правки не говорит."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    master.patch(f'/api/microplan/{row.id}/',
                 {'version': row.version, 'comment': 'смена'}, format='json')
    log = m.ChangeLog.objects.get(action='update')
    assert 'version' not in log.changes
    assert list(log.changes) == ['comment']


def test_delete_is_recorded_before_row_disappears(master):
    """След удаления обязан пережить саму запись."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    src, rid = row.src_id, row.id
    assert master.delete(f'/api/microplan/{rid}/').status_code == 204

    log = m.ChangeLog.objects.get(action='delete')
    assert log.src_id == src and log.entity == 'microplan'
    assert log.username == 'master'
    assert not m.MicroplanRow.objects.filter(id=rid).exists()


def test_recalc_commit_logs_once_not_per_row(master):
    """Массовый пересчёт — одна запись, а не сотня.

    Построчный журнал на пересчёте месяца стал бы бесполезен ровно в тот день,
    когда в нём нужно разобраться.
    """
    resp = master.post('/api/microplan/recalc',
                       {'month': '2026-08', 'commit': True}, format='json')
    assert resp.status_code == 200, resp.data
    logs = m.ChangeLog.objects.filter(action='recalc')
    assert logs.count() == 1, 'ожидалась одна запись на всю операцию'
    assert 'пересчёт' in logs.get().note


def test_journal_is_scoped_by_op(master, boss):
    """Через журнал чужую площадку не видно — как и в самих данных."""
    foreign = m.MicroplanRow.objects.get(op_name='Чужая')
    boss.patch(f'/api/microplan/{foreign.id}/',
               {'version': foreign.version, 'plan_release': 77}, format='json')
    own = m.MicroplanRow.objects.get(op_name='Своя')
    boss.patch(f'/api/microplan/{own.id}/',
               {'version': own.version, 'plan_release': 88}, format='json')

    seen = {r['op_name'] for r in master.get('/api/changes/').json()['results']}
    assert seen == {'Своя'}, f'в журнале видны чужие площадки: {seen}'


def test_journal_is_read_only(master):
    """Историю не правят: иначе она не история."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    master.patch(f'/api/microplan/{row.id}/',
                 {'version': row.version, 'plan_release': 5}, format='json')
    log = m.ChangeLog.objects.get(action='update')

    assert master.post('/api/changes/', {'note': 'подделка'},
                       format='json').status_code in (403, 405)
    assert master.patch(f'/api/changes/{log.pk}/', {'note': 'подделка'},
                        format='json').status_code in (403, 405)
    assert master.delete(f'/api/changes/{log.pk}/').status_code in (403, 405)
    log.refresh_from_db()
    assert log.note == ''


def test_journal_filters_by_row(master):
    """История конкретной строки — то, ради чего журнал и открывают."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    for i, val in enumerate((11, 22, 33)):
        row.refresh_from_db()
        master.patch(f'/api/microplan/{row.id}/',
                     {'version': row.version, 'plan_release': val}, format='json')

    body = master.get(f'/api/changes/?entity=microplan&src_id={row.src_id}').json()
    assert body['count'] == 3
    # Свежие сверху: разбираться начинают с последней правки.
    values = [r['changes']['plan_release'][1] for r in body['results']]
    assert values == [33.0, 22.0, 11.0], values


def test_decimal_is_not_a_fake_change(master):
    """`Decimal('10.0000')` и `10.0` — одно число, и «изменением» быть не должно."""
    row = m.MicroplanRow.objects.get(op_name='Своя')
    master.patch(f'/api/microplan/{row.id}/',
                 {'version': row.version, 'plan_release': '10.0000'}, format='json')
    assert not m.ChangeLog.objects.filter(action='update').exists(), \
        'формат числа записан как правка'


def test_prune_changes_needs_confirmation(db):
    """Очистка журнала без `--yes` ничего не удаляет.

    История необратима: молчаливое удаление по запуску команды означало бы, что
    один неосторожный вызов стирает ответ на вопрос «кто это поставил».
    """
    import io
    from datetime import timedelta

    from django.core.management import call_command
    from django.utils import timezone

    old = m.ChangeLog.objects.create(entity='microplan', action='update', username='кто-то')
    m.ChangeLog.objects.filter(pk=old.pk).update(at=timezone.now() - timedelta(days=400))
    fresh = m.ChangeLog.objects.create(entity='microplan', action='update', username='кто-то')

    out = io.StringIO()
    call_command('prune_changes', '--days', '180', stdout=out)
    assert m.ChangeLog.objects.count() == 2, 'удалило без подтверждения'
    assert 'Ничего не удалено' in out.getvalue()

    call_command('prune_changes', '--days', '180', '--yes', stdout=io.StringIO())
    assert list(m.ChangeLog.objects.values_list('pk', flat=True)) == [fresh.pk], \
        'удалена не та запись'
