# -*- coding: utf-8 -*-
"""Паритет Python-порта с JS-оригиналом.

Критерий приёмки фазы 2 (ТЗ §5): группа функций считается перенесённой только
когда её эталон сходится.

Тесты идут по ДВУМ фикстурам:

* `real_export_20260801.json` — боевые данные. Главная проверка, но покрывает не
  всё: в ней каждая бригада-день содержит ровно одну строку, эффективность
  освоения везде 100 %, все смены 480 мин, все цеха типа БП.
* `synthetic_capacity.json` — рукотворная, закрывает именно эти ветки: несколько
  строк в одной бригаде-дне (деление пула), эфф. освоения 50/80/120 %, смены
  480/600/720, оба типа цеха, переопределение маршрута на уровень ОП, нормы РЦ и
  ШЦ по заказу. Коммерческих данных не содержит.

Синтетика здесь законна: сверяются две реализации между собой, а не расчёт с
реальностью. Эталон для обеих снимается с работающего приложения:

    cd tests && node golden_calc.js
    cd tests && node golden_calc.js synthetic_capacity.json

Запуск:

    cd server && ../server_venv/bin/python -m pytest core/calc/test_parity.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.calc import capacity as cap
from core.calc import coverage as cov
from core.calc import dates as dt
from core.calc import norms as nm
from core.calc import routes as rt
from core.calc.snapshot import from_export

FIXTURES = Path(__file__).resolve().parents[3] / 'tests' / 'fixtures'
GOLDEN = FIXTURES / 'golden'

CASES = [
    ('real_export_20260801.json', ''),
    ('synthetic_capacity.json', 'synthetic_capacity__'),
]
AVAILABLE = [c for c in CASES
             if (FIXTURES / c[0]).exists() and (GOLDEN / f'{c[1]}calc_routes.json').exists()]

pytestmark = pytest.mark.skipif(
    not AVAILABLE,
    reason='нет фикстур или эталонов — снимите: cd tests && node golden_calc.js',
)


class Case:
    """Пара «срез + его эталоны»."""

    def __init__(self, fixture: str, prefix: str):
        self.name = fixture
        self.prefix = prefix
        # Берём состояние ПОСЛЕ импорта, на котором снят эталон, а не сырой файл:
        # импорт прогоняет миграции и пересчёт, и авто-строки могут отличаться.
        # Сверять две реализации можно только на одинаковом входе.
        state = GOLDEN / f'{prefix}state.json'
        src = state if state.exists() else FIXTURES / fixture
        self.snap = from_export(json.loads(src.read_text(encoding='utf-8')))

    def fresh_snapshot(self):
        """Новый срез из того же состояния — пересчёт меняет данные на месте."""
        state = GOLDEN / f'{self.prefix}state.json'
        src = state if state.exists() else FIXTURES / self.name
        return from_export(json.loads(src.read_text(encoding='utf-8')))

    def gold(self, name: str):
        return json.loads((GOLDEN / f'{self.prefix}{name}').read_text(encoding='utf-8'))


@pytest.fixture(scope='module', params=[c[0] for c in AVAILABLE], ids=[c[0] for c in AVAILABLE])
def case(request):
    prefix = dict(CASES)[request.param]
    return Case(request.param, prefix)


@pytest.fixture(scope='module')
def snap(case):
    return case.snap


def close(a, b, eps=1e-9):
    """Числа сравниваем по значению: в JS всё float, в Python бывает int."""
    if a is None or b is None:
        return a == b
    return abs(float(a) - float(b)) < eps


# ── Группа 2: маршруты ──────────────────────────────────────────────────────

def test_route_for_op(case, snap):
    """route_for_op / all_routes_of отдают тот же маршрут, что JS."""
    bad = []
    for exp in case.gold('calc_routes.json'):
        nom = snap.nom_by_gp(exp['articleGp'])
        got = rt.all_routes_of(nom) if exp['op'] is None else rt.route_for_op(nom, exp['op'])
        simple = [{'stage': i.get('stage'), 'articleItem': i.get('articleItem'),
                   'timeCost': float(i.get('timeCost') or 0)} for i in got]
        want = [{'stage': i['stage'], 'articleItem': i['articleItem'],
                 'timeCost': float(i['timeCost'])} for i in exp['route']]
        if simple != want:
            bad.append((exp['articleGp'], exp['op'], want, simple))
    assert not bad, f'расхождений маршрутов: {len(bad)}; первое: {bad[0]}'


def test_route_stages_and_prev_stage(case, snap):
    """Набор переделов маршрута и предыдущий передел."""
    bad_stages, bad_prev = [], []
    for exp in case.gold('calc_prev_stage.json'):
        got_stages = sorted(rt.route_stages_for_gp(snap, exp['articleGp'], exp['op']))
        if got_stages != exp['stages']:
            bad_stages.append((exp['articleGp'], exp['op'], exp['stages'], got_stages))
        got_prev = rt.prev_stage_in_route(snap, exp['articleGp'], exp['stage'], exp['op'])
        if got_prev != exp['prev']:
            bad_prev.append((exp['articleGp'], exp['op'], exp['stage'], exp['prev'], got_prev))
    assert not bad_stages, f'расхождений по набору переделов: {len(bad_stages)}; {bad_stages[0]}'
    assert not bad_prev, f'расхождений по предыдущему переделу: {len(bad_prev)}; {bad_prev[0]}'


def test_prev_stage_from_shc_is_not_reversal(case, snap):
    """Режим «От ШЦ»: у ШЦ нет предыдущего передела, но поток НЕ разворачивается.

    Ловушка из CLAUDE.md §4.6: «От ШЦ» — это планирование от якоря, а не обратная
    цепочка. У остальных переделов порядок остаётся РЦ→АЦ→ШЦ→У.
    """
    gp = next((n['articleGp'] for n in snap.nomenclature), None)
    assert gp, 'в фикстуре нет номенклатуры'
    snap.micro_check_mode = 'fromShc'
    try:
        assert rt.prev_stage_in_route(snap, gp, 'ШЦ') is None
        assert rt.prev_stage_in_route(snap, gp, 'У') in ('ШЦ', 'АЦ', 'РЦ', None)
        assert rt.prev_stage_in_route(snap, gp, 'РЦ') is None
    finally:
        snap.micro_check_mode = 'forward'


# ── Группа 3: нормы ─────────────────────────────────────────────────────────

def test_stage_time_cost(case, snap):
    """stage_time_cost_for и time_cost_of по всем артикулам, переделам и ОП."""
    bad = []
    for exp in case.gold('calc_stage_norms.json'):
        if exp.get('stage') is None:
            got = nm.time_cost_of(snap, exp['articleItem'], exp['op'])
            want = exp['tcAny']
        else:
            got = nm.stage_time_cost_for(snap, exp['articleItem'], exp['stage'], exp['op'])
            want = exp['tc']
        if not close(got, want):
            bad.append((exp['articleItem'], exp.get('stage'), exp['op'], want, got))
    assert not bad, f'расхождений норм: {len(bad)} из 500; первые: {bad[:3]}'


def test_row_and_sub_order_norms(case, snap):
    """Норма строки микроплана и суммарная норма подзаказа.

    Здесь проверяется весь приоритет: макроплан → override по заказу → норма
    стадии → общая норма (CLAUDE.md §4.3).
    """
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_row_norms.json'):
        if exp['kind'] == 'row':
            row = rows.get(exp['id'])
            if row is None:
                bad.append((exp['id'], 'строки нет'))
                continue
            for per in exp['perArticle']:
                got = nm.tc_for_micro_row(snap, row, per['articleItem'])
                if not close(got, per['tc']):
                    bad.append(('row', exp['id'], per['articleItem'], per['tc'], got))
        else:
            row = rows.get(exp['rowId'])
            sub = next((s for s in (row or {}).get('subOrders', []) if s.get('id') == exp['id']), None)
            if sub is None:
                bad.append((exp['id'], 'подзаказа нет'))
                continue
            got = nm.tc_for_sub_order(snap, sub, exp['op'], exp['stage'])
            if not close(got, exp['tcSum']):
                bad.append(('sub', exp['id'], exp['order'], exp['tcSum'], got))
    assert not bad, f'расхождений норм строк: {len(bad)}; первые: {bad[:3]}'


def test_macro_norm_requires_bound_order(case, snap):
    """Норма из макроплана доходит только при привязанном заказе.

    Следствие §4.3, которое легко потерять при переносе: строка макроплана с
    `normOverride`, но без `orderNums`, нормы не даёт.
    """
    row = next((r for r in snap.macroplan
                if r.get('normOverride') is not None and r.get('orderNums')), None)
    if row is None:
        pytest.skip('в фикстуре нет строки макроплана с нормой и привязанным заказом')
    order = row['orderNums'][0]
    stage = nm.stage_of_macro_row(snap, row)
    assert close(nm.norm_from_macro_for_order(snap, order, row['articleItem'], stage),
                 float(row['normOverride']))
    assert nm.norm_from_macro_for_order(snap, 'ЗАКАЗ-КОТОРОГО-НЕТ',
                                        row['articleItem'], stage) is None


# ── Группа 6: покрытие и остаток заказа ─────────────────────────────────────

def test_order_coverage_all_metrics(case, snap):
    """Покрытие заказа по всем метрикам и окнам, которые использует ядро."""
    bad = []
    for exp in case.gold('calc_coverage.json'):
        o, st, d, ex = exp['order'], exp['stage'], exp['date'], exp['excludeRowId']
        checks = [
            ('coveredBefore', cov.covered_before(snap, o, st, d, exclude_row_id=ex)),
            ('planBefore', cov.order_stage_qty(snap, o, st, d, metric=cov.PLAN, when='<',
                                               exclude_row_id=ex)),
            ('factOrPlanBefore', cov.order_stage_qty(snap, o, st, d, metric=cov.FACT_OR_PLAN,
                                                     when='<', exclude_row_id=ex)),
            ('maxFactPlanBefore', cov.order_stage_qty(snap, o, st, d, metric=cov.MAX_FACT_PLAN,
                                                      when='<', exclude_row_id=ex)),
            ('planSameDay', cov.order_stage_qty(snap, o, st, d, metric=cov.PLAN, when='==',
                                                exclude_row_id=ex, skip_manual_main=True)),
            ('allPlan', cov.order_stage_qty(snap, o, st, d, metric=cov.PLAN, when='all')),
        ]
        for name, got in checks:
            if not close(got, exp[name]):
                bad.append((o, st, d, name, exp[name], got))
    assert not bad, f'расхождений покрытия: {len(bad)}; первые: {bad[:3]}'


def test_remaining_to_plan(case, snap):
    """Остаток к планированию совпадает с подсказкой «≤N (ЗАК)» из приложения."""
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_order_limit.json'):
        if exp['available'] is None:
            continue
        row = rows.get(exp['id'])
        if row is None:
            continue
        got = cov.remaining_to_plan(snap, exp['order'], exp['stage'], exp['date'],
                                    exclude_row_id=exp['id'])
        if not close(got, exp['available']):
            bad.append((exp['id'], exp['order'], exp['stage'], exp['available'], got))
    assert not bad, f'расхождений остатка: {len(bad)}; первые: {bad[:3]}'


def test_coverage_includes_sub_orders(case, snap):
    """Подзаказы входят в покрытие (инвариант CLAUDE.md §4.5).

    Проверка на самих данных, а не на эталоне: если бы подзаказы потерялись,
    сумма по заказу оказалась бы меньше.
    """
    target = None
    for m in snap.microplan:
        for sub in (m.get('subOrders') or []):
            if sub.get('releaseOrder') and float(sub.get('planRelease') or 0) > 0:
                target = (m, sub)
                break
        if target:
            break
    if not target:
        pytest.skip('в фикстуре нет подзаказов с ненулевым планом')
    row, sub = target
    stage = row.get('stage') or 'ШЦ'
    total = cov.order_stage_qty(snap, sub['releaseOrder'], stage, metric=cov.PLAN, when='all')
    assert total >= float(sub['planRelease']), 'подзаказ не попал в сумму по заказу'


# ── Группа 1: даты и доставки ───────────────────────────────────────────────

def test_dates(case, snap):
    """addCalDays и prevWorkday совпадают с JS.

    Важно, что арифметика идёт по локальной дате: в JS для этого специально
    `new Date(s + 'T00:00:00')`, иначе UTC сдвигает сутки.
    """
    bad = []
    for exp in case.gold('calc_dates.json'):
        if exp['fn'] == 'addCalDays':
            got = dt.add_cal_days(exp['date'], exp['n'])
        else:
            got = dt.prev_workday(exp['date'])
        if got != exp['out']:
            bad.append((exp['fn'], exp['date'], exp.get('n'), exp['out'], got))
    assert not bad, f'расхождений по датам: {len(bad)}; первые: {bad[:3]}'


def test_prev_workday_weekend_shift():
    """Суббота и воскресенье уезжают на пятницу, будни не трогаются."""
    assert dt.prev_workday('2026-08-01') == '2026-07-31'   # сб → пт
    assert dt.prev_workday('2026-08-02') == '2026-07-31'   # вс → пт
    assert dt.prev_workday('2026-08-03') == '2026-08-03'   # пн без сдвига
    assert dt.prev_workday('') == ''


def test_delivery_days(case, snap):
    """deliveryDaysFor: явная запись матрицы, иначе 1 день внутри одного ОП."""
    bad = []
    for exp in case.gold('calc_delivery_days.json'):
        got = dt.delivery_days_for(snap, exp['fromOp'], exp['toOp'])
        if not close(got, exp['days']):
            bad.append((exp['fromOp'], exp['toOp'], exp['days'], got))
    assert not bad, f'расхождений по доставке: {len(bad)}; первые: {bad[:3]}'


# ── Группа 4: мощность бригады-дня ──────────────────────────────────────────

def test_capacity(case, snap):
    """Состав группы, физический пул и занятое другими строками.

    Здесь проверяется инвариант §4.4: пул считается БЕЗ эффективности освоения,
    а расход — С ней в знаменателе.
    """
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_capacity.json'):
        row = rows.get(exp['id'])
        if row is None:
            bad.append((exp['id'], 'строки нет'))
            continue
        group = cap.brigade_day_group_rows(snap, row)
        got_ids = sorted(g['id'] for g in group)
        if got_ids != exp['groupIds']:
            bad.append(('группа', exp['id'], exp['groupIds'], got_ids))
            continue
        checks = [
            ('availMin', cap.workers_available_min_sum(snap, row.get('workers') or [],
                                                       row.get('op'), row.get('workshop'))),
            ('effPct', cap.micro_row_eff_pct(snap, row.get('workers') or [],
                                             row.get('op'), row.get('workshop'))),
            ('poolRaw', cap.brigade_day_pool_raw_min(snap, group)),
            ('usedByOthers', cap.brigade_day_used_by_others_raw_min(snap, row, group)),
            ('shiftDur', cap.shift_duration_of(snap, row.get('op'), row.get('workshop'))),
            ('defEff', cap.default_eff_pct(snap, row.get('op'), row.get('workshop'))),
        ]
        for name, got in checks:
            if not close(got, exp[name], eps=1e-6):
                bad.append((exp['id'], name, exp[name], got))
    assert not bad, f'расхождений по мощности: {len(bad)}; первые: {bad[:3]}'


def test_learning_factor_semantics():
    """Эффективность освоения: отсутствие = 100 %, ноль не делит на ноль, >100 % допустимо.

    Ветка «эфф. освоения ≠ 100 %» в боевой фикстуре НЕ представлена (везде 100),
    поэтому проверяется синтетикой. Заявленная семантика: >100 % раздувает план
    сверх физической мощности — это не баг (CLAUDE.md §4.4).
    """
    assert close(cap.learning_factor({}), 1.0)
    assert close(cap.learning_factor({'learningEff': 100}), 1.0)
    assert close(cap.learning_factor({'learningEff': 50}), 0.5)
    assert close(cap.learning_factor({'learningEff': 200}), 2.0)
    assert close(cap.learning_factor({'learningEff': 0}), 0.01)


def test_absence_is_per_worker(case, snap):
    """Абсентеизм ×0,95 применяется к группе работников, а не ко всей смене."""
    op = ws = ''
    with_abs = cap.workers_available_min_sum(
        snap, [{'staffCount': 10, 'shiftTime': 480, 'effPct': 100}], op, ws)
    without_abs = cap.workers_available_min_sum(
        snap, [{'staffCount': 10, 'shiftTime': 480, 'effPct': 100, 'absence': False}], op, ws)
    assert close(with_abs, 480 * 10 * 0.95)
    assert close(without_abs, 480 * 10)


def test_pool_excludes_learning_eff(case, snap):
    """Пул мощности не зависит от эффективности освоения, а расход зависит.

    Ошибка, которую этот тест ловит: если поставить эфф. освоения в пул вместо
    знаменателя расхода, числа разойдутся незаметно.
    """
    workers = [{'staffCount': 5, 'shiftTime': 480, 'effPct': 100}]
    base = {'id': 1, 'op': '', 'workshop': '', 'stage': 'ШЦ', 'date': '2026-07-01',
            'workers': workers, 'articleItems': [], 'planRelease': 0}
    pool_100 = cap.brigade_day_pool_raw_min(snap, [dict(base, learningEff=100)])
    pool_50 = cap.brigade_day_pool_raw_min(snap, [dict(base, learningEff=50)])
    assert close(pool_100, pool_50), 'эфф. освоения не должна влиять на пул'


# ── Группа 5: авто-план ─────────────────────────────────────────────────────

def test_autoplan_full_recalc(case):
    """Полный пересчёт даёт тот же план, что JS, построчно и по подзаказам.

    Считаем от `state.json` — состояния, на котором JS запускал пересчёт, — иначе
    сравнивали бы разные входы.
    """
    from core.calc import autoplan as ap
    snap = case.fresh_snapshot()
    ap.recalc_all_micro_plans(snap)

    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_autoplan.json'):
        row = rows.get(exp['id'])
        if row is None:
            bad.append((exp['id'], 'строки нет'))
            continue
        for field, key in (('planRelease', 'planRelease'), ('planLaunch', 'planLaunch'),
                           ('planReleaseMain', 'planReleaseMain'),
                           ('planReleaseExtra', 'planReleaseExtra')):
            if not close(float(row.get(key) or 0), exp[field]):
                bad.append((exp['id'], exp['date'], exp['order'], field, exp[field],
                            row.get(key)))
        got_subs = {s['id']: s for s in (row.get('subOrders') or [])}
        for es in exp['subs']:
            s = got_subs.get(es['id'])
            if s is None:
                bad.append((exp['id'], 'подзаказа нет', es['id']))
            elif not close(float(s.get('planRelease') or 0), es['planRelease']):
                bad.append((exp['id'], 'подзаказ', es['id'], es['order'],
                            es['planRelease'], s.get('planRelease')))
    assert not bad, f'расхождений авто-плана: {len(bad)}; первые: {bad[:4]}'


def test_autoplan_by_article(case):
    """Разбивка плана по артикулам (`planByArticle`) совпадает."""
    from core.calc import autoplan as ap
    snap = case.fresh_snapshot()
    ap.recalc_all_micro_plans(snap)
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_autoplan.json'):
        row = rows.get(exp['id'])
        if row is None:
            continue
        got = row.get('planByArticle') or {}
        want = exp['planByArticle'] or {}
        if set(got) != set(want):
            bad.append((exp['id'], 'состав артикулов', sorted(want), sorted(got)))
            continue
        for ai, v in want.items():
            if not close(float(got.get(ai) or 0), v):
                bad.append((exp['id'], ai, v, got.get(ai)))
    assert not bad, f'расхождений planByArticle: {len(bad)}; первые: {bad[:4]}'


def test_autoplan_is_idempotent(case):
    """Повторный пересчёт не двигает план — как и в приложении."""
    from core.calc import autoplan as ap
    snap = case.fresh_snapshot()
    ap.recalc_all_micro_plans(snap)
    first = [float(m.get('planRelease') or 0) for m in snap.microplan]
    ap.recalc_all_micro_plans(snap)
    second = [float(m.get('planRelease') or 0) for m in snap.microplan]
    assert first == second, 'повторный пересчёт изменил план'


def test_manual_rows_keep_their_plan(case):
    """Ручной план не перетирается авторасчётом (он лишь резервирует пул)."""
    from core.calc import autoplan as ap
    snap = case.fresh_snapshot()
    manual_before = {m['id']: float(m.get('planRelease') or 0)
                     for m in snap.microplan if m.get('planReleaseIsManual')}
    if not manual_before:
        pytest.skip('в фикстуре нет ручных строк')
    ap.recalc_all_micro_plans(snap)
    bad = [(i, v, float(next(m for m in snap.microplan if m['id'] == i).get('planRelease') or 0))
           for i, v in manual_before.items()
           if not close(v, float(next(m for m in snap.microplan if m['id'] == i).get('planRelease') or 0))]
    assert not bad, f'ручной план изменён: {bad[:3]}'


def test_js_round_half_up():
    """Округление половин вверх, как `Math.round`, а не банковское из Python.

    `round(0.5)` в Python даёт 0, `round(2.5)` даёт 2 — на плане это разошлось бы
    на штуку.
    """
    from core.calc.autoplan import js_round
    assert js_round(0.5) == 1
    assert js_round(1.5) == 2
    assert js_round(2.5) == 3
    assert js_round(2.4) == 2
    assert js_round(-0.5) == 0


# ── Группа 7: валидации ─────────────────────────────────────────────────────

def test_stage_fact_limit(case, snap):
    """Лимит передела: сколько физически можно выпустить, исходя из предыдущего.

    Упаковка считается комплектом — минимум по частям, а не сумма.
    """
    from core.calc import validation as vd
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_stage_fact_limit.json'):
        row = rows.get(exp['id'])
        if row is None:
            continue
        order = snap.order_by_number(exp['order'])
        ais = nm.micro_row_article_items(row)
        got = vd.stage_fact_limit(snap, exp['order'], order.get('articleGp'), exp['stage'],
                                  ais[0] if ais else (row.get('articleItem') or ''),
                                  row.get('op'))
        want = exp['info']
        if want is None:
            if got is not None:
                bad.append((exp['id'], 'ожидался None', got))
            continue
        if got is None:
            bad.append((exp['id'], 'получили None', want))
            continue
        if not close(got['limit'], want['limit']) or got['prevStage'] != want['prevStage']:
            bad.append((exp['id'], want, {'limit': got['limit'], 'prevStage': got['prevStage']}))
        elif got['detail'] != want['detail']:
            bad.append((exp['id'], 'текст', want['detail'], got['detail']))
    assert not bad, f'расхождений лимита передела: {len(bad)}; первые: {bad[:3]}'


def test_validations(case, snap):
    """Вердикты проверок на нескольких значениях каждой строки.

    Сверяются не только `ok` и уровень, но и ТЕКСТ сообщения: он показывается
    пользователю, расхождение было бы заметно сразу.
    """
    from core.calc import validation as vd
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_validation.json'):
        row = rows.get(exp['id'])
        if row is None:
            continue
        probe = exp['probe']
        if exp['kind'] == 'fact':
            pairs = [
                ('stageFact', vd.validate_stage_fact(snap, row, probe, exclude_row_id=exp['id'])),
                ('factVsQty', vd.validate_fact_vs_order_qty(snap, row, probe,
                                                            exclude_row_id=exp['id'])),
                ('relVsLaunch', vd.validate_release_vs_launches(snap, row, probe)),
            ]
        else:
            pairs = [('planLimit', vd.validate_plan_order_limit(snap, row, probe))]

        for name, got in pairs:
            want = exp[name]
            if got.ok != want['ok']:
                bad.append((exp['id'], probe, name, 'ok', want['ok'], got.ok))
                continue
            if (got.severity or None) != want['severity']:
                bad.append((exp['id'], probe, name, 'severity', want['severity'], got.severity))
            if 'available' in want and want['available'] is not None:
                if not close(got.available, want['available']):
                    bad.append((exp['id'], probe, name, 'available',
                                want['available'], got.available))
            if want.get('msg') and got.msg != want['msg']:
                bad.append((exp['id'], probe, name, 'текст',
                            want['msg'][:70], (got.msg or '')[:70]))
    assert not bad, f'расхождений вердиктов: {len(bad)}; первые: {bad[:3]}'


def test_severity_rule(case, snap):
    """Правило: проверки ФАКТА — stop, проверки ПЛАНА — warn.

    Регрессия на решение из CLAUDE.md §4.8: ввод плана не должен блокироваться.
    """
    from core.calc import validation as vd
    seen_stop = seen_warn = False
    for exp in case.gold('calc_validation.json'):
        if exp['kind'] == 'fact':
            for name in ('stageFact', 'factVsQty', 'relVsLaunch'):
                if not exp[name]['ok']:
                    assert exp[name]['severity'] == vd.STOP, f'{name} должна быть stop'
                    seen_stop = True
        else:
            if not exp['planLimit']['ok']:
                assert exp['planLimit']['severity'] == vd.WARN, 'проверка плана должна быть warn'
                seen_warn = True
    assert seen_stop or seen_warn, 'ни одна проверка не сработала — эталон пустой'


def test_plan_vs_prev_stage_and_delivery(case, snap):
    """План против плана предыдущего передела и подсказка даты доставки."""
    from core.calc import validation as vd
    rows = {m['id']: m for m in snap.microplan}
    bad = []
    for exp in case.gold('calc_validation2.json'):
        row = rows.get(exp['id'])
        if row is None:
            continue
        want = exp['res']
        if exp['kind'] == 'planPrev':
            got = vd.validate_plan_vs_prev_stage_plan(snap, row, exp['probe'])
            if got.ok != want['ok']:
                bad.append((exp['id'], exp['probe'], 'ok', want['ok'], got.ok))
                continue
            if (got.severity or None) != want['severity']:
                bad.append((exp['id'], exp['probe'], 'severity', want['severity'], got.severity))
            if want['available'] is not None and not close(got.available, want['available']):
                bad.append((exp['id'], exp['probe'], 'available',
                            want['available'], got.available))
            if want.get('msg') and got.msg != want['msg']:
                bad.append((exp['id'], exp['probe'], 'текст',
                            want['msg'][:60], (got.msg or '')[:60]))
        else:
            got = vd.check_delivery_date(snap, row)
            if got.ok != want['ok']:
                bad.append((exp['id'], 'доставка ok', want['ok'], got.ok))
            elif not want['ok']:
                if got.extra.get('minDate') != want['minDate']:
                    bad.append((exp['id'], 'minDate', want['minDate'], got.extra.get('minDate')))
                if got.extra.get('days') != want['days']:
                    bad.append((exp['id'], 'days', want['days'], got.extra.get('days')))
    assert not bad, f'расхождений: {len(bad)}; первые: {bad[:3]}'


def test_stage_launch(case, snap):
    """Проверка запусков: суммарный лимит и последовательность по датам."""
    from core.calc import validation as vd
    bad = []
    for exp in case.gold('calc_launch_validation.json'):
        got = vd.validate_stage_launch(snap, exp['order'], exp['articleItem'], exp['stage'],
                                       exp['date'] or None, exp['probe'],
                                       exclude_entry_id=exp['entryId'],
                                       op=next((m.get('op') for m in snap.microplan
                                                if m.get('id') == exp['rowId']), None))
        want = exp['res']
        if got.ok != want['ok']:
            bad.append((exp['rowId'], exp['order'], exp['probe'], 'ok', want['ok'], got.ok))
            continue
        if (got.severity or None) != want['severity']:
            bad.append((exp['rowId'], exp['probe'], 'severity', want['severity'], got.severity))
        if want['available'] is not None and not close(got.available, want['available']):
            bad.append((exp['rowId'], exp['probe'], 'available',
                        want['available'], got.available))
        if want.get('msg') and got.msg != want['msg']:
            bad.append((exp['rowId'], exp['probe'], 'текст',
                        want['msg'][:60], (got.msg or '')[:60]))
    assert not bad, f'расхождений по запускам: {len(bad)}; первые: {bad[:3]}'


def test_from_shc_readiness_is_informational(case, snap):
    """Режим «От ШЦ»: на строке ШЦ индикатор готовности, а не блокировка.

    Ловушка §4.6: «От ШЦ» — планирование от якоря, а не разворот цепочки.
    Индикатор обязан быть информационным (`ok=True`) даже когда кроя не хватает.
    """
    from core.calc import validation as vd
    shc = next((m for m in snap.microplan
                if (m.get('stage') or 'ШЦ') == 'ШЦ' and m.get('releaseOrder')
                and m.get('releaseOrder') != '__extra__'), None)
    if shc is None:
        pytest.skip('в фикстуре нет строк ШЦ с заказом')
    snap.micro_check_mode = 'fromShc'
    try:
        got = vd.validate_plan_vs_prev_stage_plan(snap, shc, 10 ** 9)
        assert got.ok, 'ШЦ в режиме «От ШЦ» не должен блокироваться'
        if got.extra.get('kind') == 'readiness':
            assert got.extra['prev_stage'] if 'prev_stage' in got.extra else True
            assert 'Готовность' in got.msg
        # РЦ и АЦ в этом режиме свободны.
        for st in ('РЦ', 'АЦ'):
            row = dict(shc, stage=st)
            assert vd.validate_plan_vs_prev_stage_plan(snap, row, 10 ** 9).ok
    finally:
        snap.micro_check_mode = 'forward'


# ── Группа 8: аналитика ─────────────────────────────────────────────────────

def test_analytics_primitives(case, snap):
    """Примитивы аналитики: количество комплекта, трудоёмкость строки, ГП артикула.

    Комплектность — главное здесь: целые изделия суммируются, узлы сводятся по
    минимуму. Простое сложение дало бы завышенный объём готовой продукции.
    """
    from core.calc import analytics as an
    macro = {r['id']: r for r in snap.macroplan}
    bad = []
    for exp in case.gold('calc_analytics.json'):
        kind = exp['kind']
        if kind == 'macroRow':
            row = macro.get(exp['id'])
            if row is None:
                continue
            if not close(an.macro_row_kit_quantity(row), exp['kitQty']):
                bad.append((exp['id'], 'kitQty', exp['kitQty'],
                            an.macro_row_kit_quantity(row)))
            if not close(an.macro_row_labor_minutes(snap, row), exp['laborMin'], eps=1e-6):
                bad.append((exp['id'], 'laborMin', exp['laborMin'],
                            an.macro_row_labor_minutes(snap, row)))
            if nm.stage_of_macro_row(snap, row) != exp['stage']:
                bad.append((exp['id'], 'stage', exp['stage'],
                            nm.stage_of_macro_row(snap, row)))
        elif kind == 'gpOf':
            got = an.article_gp_for(snap, exp['articleItem'])
            if got != exp['gp']:
                bad.append((exp['articleItem'], 'gp', exp['gp'], got))
        elif kind == 'kitQty':
            rows = [r for r in snap.macroplan if r.get('month') == exp['month']]
            got = an.stage_kit_qty_for_rows(snap, rows)
            if not close(got, exp['qty']):
                bad.append((exp['month'], 'kitQty', exp['qty'], got))
        elif kind == 'kitQtyStage':
            rows = [r for r in snap.macroplan
                    if r.get('month') == exp['month']
                    and nm.stage_of_macro_row(snap, r) == exp['stage']]
            got = an.stage_kit_qty_for_rows(snap, rows)
            if not close(got, exp['qty']):
                bad.append((exp['month'], exp['stage'], 'kitQty', exp['qty'], got))
            got_min = round(sum(an.macro_row_labor_minutes(snap, r) for r in rows))
            if not close(got_min, exp['laborMin']):
                bad.append((exp['month'], exp['stage'], 'laborMin', exp['laborMin'], got_min))
    assert not bad, f'расхождений аналитики: {len(bad)}; первые: {bad[:3]}'


def test_kit_quantity_uses_min_for_parts(snap):
    """Узлы одного изделия сводятся по МИНИМУМУ, целые изделия складываются.

    Сложить узлы вместо минимума — типичная ошибка, завышающая объём готовой
    продукции: из 10 курток и 4 брюк выйдет 4 костюма, а не 14.
    """
    from core.calc import analytics as an

    # Ищем ГП, у которого на одном переделе несколько узлов.
    target = None
    for nom in snap.nomenclature:
        by_stage = {}
        for item in rt.all_routes_of(nom):
            by_stage.setdefault(item.get('stage'), []).append(item.get('articleItem'))
        for stage, items in by_stage.items():
            uniq = [i for i in dict.fromkeys(items) if i and i != nom.get('articleGp')]
            if len(uniq) >= 2:
                target = (nom['articleGp'], uniq[:2])
                break
        if target:
            break
    if not target:
        pytest.skip('в фикстуре нет изделия из нескольких узлов')

    gp, (part_a, part_b) = target
    gp_set = {n['articleGp'] for n in snap.nomenclature if n.get('articleGp')}

    # Только узлы: комплектов ровно столько, сколько самой дефицитной части.
    assert close(an.kit_qty_from_entries(snap, [(part_a, 10), (part_b, 4)], gp_set), 4)
    # Порядок не важен.
    assert close(an.kit_qty_from_entries(snap, [(part_b, 4), (part_a, 10)], gp_set), 4)
    # Целое изделие складывается с комплектами из узлов.
    assert close(an.kit_qty_from_entries(snap, [(gp, 3), (part_a, 10), (part_b, 4)], gp_set), 7)
    # Один узел без пары — он и есть минимум.
    assert close(an.kit_qty_from_entries(snap, [(part_a, 10)], gp_set), 10)


def test_month_weeks_split():
    """Разбивка месяца на недели пн–вс, обрезанные границами месяца."""
    from core.calc.analytics import month_weeks
    w = month_weeks('2026-08')          # 1 августа 2026 — суббота
    assert w[0] == (1, 2), f'первая неделя должна обрываться на воскресенье: {w[0]}'
    assert w[-1][1] == 31
    assert all(a <= b for a, b in w)
    assert sum(b - a + 1 for a, b in w) == 31, 'недели должны покрывать месяц без дыр'


def test_month_totals_runs(case, snap):
    """Итоги месяца считаются и согласованы между штуками и минутами.

    Точное сравнение с приложением тут невозможно: `renderAnalytics` отдаёт HTML,
    а не числа. Поэтому проверяются инварианты, а примитивы — эталоном выше.
    """
    from core.calc import analytics as an
    months = sorted({(m.get('date') or '')[:7] for m in snap.microplan if m.get('date')})
    if not months:
        pytest.skip('в фикстуре нет дат микроплана')
    month = months[0]

    qty = an.month_totals(snap, an.Filters(month=month, unit='qty'))
    mins = an.month_totals(snap, an.Filters(month=month, unit='min'))

    assert qty.rows_micro == mins.rows_micro, 'фильтры не должны зависеть от единиц'
    assert qty.agg_stage == mins.agg_stage
    assert qty.micro_plan >= 0 and mins.micro_plan >= 0
    assert sum(v['plan'] for v in qty.by_date.values()) >= 0
    # Фильтр по переделу сужает выборку, а не расширяет.
    for st in ('РЦ', 'ШЦ', 'У'):
        part = an.month_totals(snap, an.Filters(month=month, stage=st, unit='qty'))
        assert part.rows_micro <= qty.rows_micro


def test_agg_stage_is_deepest(snap):
    """Передел агрегации — САМЫЙ ГЛУБОКИЙ с данными, а не первый попавшийся.

    Это и есть готовая продукция. Взять первый (РЦ вместо У) — значит считать
    объём по крою, а складывать все переделы нельзя вовсе: у изделия «целиком»
    артикул ГП стоит и на ШЦ, и на У, и факт удвоился бы.
    """
    from core.calc import analytics as an
    mk = lambda st: {'op': '', 'workshop': '', 'stage': st, 'articleItem': ''}

    assert an.pick_agg_stage(snap, [], [mk('РЦ'), mk('ШЦ')]) == 'ШЦ'
    assert an.pick_agg_stage(snap, [], [mk('РЦ'), mk('ШЦ'), mk('У')]) == 'У'
    assert an.pick_agg_stage(snap, [mk('У')], [mk('РЦ')]) == 'У'
    assert an.pick_agg_stage(snap, [], [mk('РЦ')]) == 'РЦ'
    assert an.pick_agg_stage(snap, [], []) == ''
    # Явный фильтр всегда важнее автоматического выбора.
    assert an.pick_agg_stage(snap, [], [mk('РЦ'), mk('У')], stage_filter='РЦ') == 'РЦ'
