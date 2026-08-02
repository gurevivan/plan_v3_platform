# -*- coding: utf-8 -*-
"""Расчётное ядро «План V3» на Python (фаза 2 миграции).

Чистые функции над срезом данных (`Snapshot`), без ORM внутри — так их можно
сверять с эталонами, снятыми с JS-версии, и переиспользовать в API и отчётах.

Перенесено (порядок по ТЗ §4):
  * группа 1 — даты и доставки: `dates`
  * группа 2 — маршруты: `routes`
  * группа 3 — нормы: `norms`
  * группа 4 — ФРВ и мощность бригады-дня: `capacity`
  * группа 5 — авто-план: `autoplan`
  * группа 6 — остатки и покрытие заказа: `coverage`
  * группа 7 — проверки микроплана: `validation`
  * группа 8 — аналитика (расчётная часть): `analytics`

Все восемь групп перенесены. Вёрстка аналитики осталась во фронте — по решению
из ТЗ §7 интерфейс не переписывается, он получает данные от сервера.
"""
from .capacity import (available_min_for_row, brigade_day_group_rows,
                       brigade_day_pool_raw_min, brigade_day_used_by_others_raw_min,
                       learning_factor, micro_row_eff_pct, row_used_raw_min,
                       workers_available_min_sum)
from .analytics import (Filters, MonthTotals, article_gp_for, baseline_vs_fact,
                        kit_qty_from_entries, macro_row_labor_minutes, month_totals,
                        month_weeks, pick_agg_stage, stage_kit_qty_for_rows)
from .autoplan import (js_round, recalc_all_micro_plans, recalc_plan_release,
                       recalc_plan_release_core)
from .coverage import covered_before, order_stage_qty, remaining_to_plan
from .dates import add_cal_days, calc_arrival_date, delivery_days_for, prev_workday
from .norms import (norm_from_macro_for_order, stage_norm_override, stage_time_cost_for,
                    tc_for_article, tc_for_micro_row, tc_for_sub_order, time_cost_of)
from .routes import all_routes_of, prev_stage_in_route, route_for_op, route_stages_for_gp
from .snapshot import Snapshot, from_export, order_key, orders_eq
from .validation import (STOP, WARN, Verdict, check_delivery_date, stage_fact_limit,
                         validate_fact_vs_order_qty, validate_plan_order_limit,
                         validate_plan_vs_prev_stage_plan, validate_release_vs_launches,
                         validate_stage_fact, validate_stage_launch)

__all__ = [
    'Snapshot', 'from_export', 'order_key', 'orders_eq',
    'route_for_op', 'all_routes_of', 'route_stages_for_gp', 'prev_stage_in_route',
    'stage_time_cost_for', 'time_cost_of', 'stage_norm_override',
    'norm_from_macro_for_order', 'tc_for_article', 'tc_for_micro_row', 'tc_for_sub_order',
    'order_stage_qty', 'covered_before', 'remaining_to_plan',
    'add_cal_days', 'prev_workday', 'delivery_days_for', 'calc_arrival_date',
    'workers_available_min_sum', 'micro_row_eff_pct', 'brigade_day_group_rows',
    'brigade_day_pool_raw_min', 'brigade_day_used_by_others_raw_min',
    'row_used_raw_min', 'available_min_for_row', 'learning_factor',
    'recalc_plan_release_core', 'recalc_all_micro_plans', 'recalc_plan_release', 'js_round',
    'Verdict', 'STOP', 'WARN', 'stage_fact_limit', 'validate_stage_fact',
    'validate_fact_vs_order_qty', 'validate_release_vs_launches', 'validate_plan_order_limit',
    'validate_stage_launch', 'validate_plan_vs_prev_stage_plan', 'check_delivery_date',
    'Filters', 'MonthTotals', 'month_totals', 'baseline_vs_fact', 'article_gp_for',
    'kit_qty_from_entries', 'stage_kit_qty_for_rows', 'macro_row_labor_minutes',
    'pick_agg_stage', 'month_weeks',
]
