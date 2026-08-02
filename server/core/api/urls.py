# -*- coding: utf-8 -*-
"""Маршруты API."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.api import views
from core.api.permissions import IsSystemAdmin
from core.api.roles import SECTION_BY_PREFIX


def _writes(viewset):
    """Умеет ли набор менять данные. Только читающему раздел не нужен."""
    return any(hasattr(viewset, name)
               for name in ('create', 'update', 'partial_update', 'destroy'))


def _admin_only(viewset):
    """Закрыт ли набор проверкой администратора — тогда роли к нему не применимы."""
    return any(p is IsSystemAdmin for p in getattr(viewset, 'permission_classes', []))


router = DefaultRouter()
router.register('ops', views.OpViewSet)
router.register('nomenclature', views.NomenclatureViewSet)
router.register('contracts', views.ContractViewSet)
router.register('orders', views.ProductionOrderViewSet)
router.register('macroplan', views.MacroplanViewSet)
router.register('microplan', views.MicroplanViewSet)
router.register('schedules', views.ScheduleViewSet)
router.register('schedule-month-overrides', views.ScheduleMonthOverrideViewSet)
router.register('macro-eff', views.MacroEffViewSet)
router.register('cal-overrides', views.CalOverrideViewSet)
router.register('manual-frv', views.ManualFrvViewSet)
router.register('holidays', views.HolidayViewSet)
router.register('bases', views.DeliveryBaseViewSet)
router.register('delivery-matrix', views.DeliveryMatrixViewSet)
router.register('plan-baseline', views.PlanBaselineViewSet)
router.register('order-links', views.OrderLinkViewSet)
router.register('time-costs', views.TimeCostOverrideViewSet)

# Пользователи стоят отдельно: они не раздел плана, их закрывает IsSystemAdmin.
router.register('users', views.UserViewSet, basename='user')
# Журнал изменений — тоже не раздел: он только читается, и режется по ОП сам.
router.register('changes', views.ChangeLogViewSet, basename='changelog')

# Раздел берётся из пути, а не задаётся на каждом наборе руками: второй список
# рано или поздно разошёлся бы с первым. Новый ПИШУЩИЙ путь без раздела — ошибка
# прямо при старте, иначе он молча оказался бы открыт всем на запись.
#
# Цикл стоит после ВСЕХ регистраций намеренно: стой он выше, добавленный ниже
# путь проверку бы не проходил — то есть ровно новый путь и остался бы без неё.
for prefix, viewset, _basename in router.registry:
    section = SECTION_BY_PREFIX.get(prefix)
    if section is not None:
        viewset.section = section
        viewset.entity = prefix
        continue
    if _writes(viewset) and not _admin_only(viewset):
        raise ImportError(
            f'Путь «{prefix}» пишет данные, но не отнесён ни к одному разделу в '
            f'core/api/roles.py — он был бы открыт на запись любому пользователю')
    viewset.entity = prefix

urlpatterns = [
    path('', include(router.urls)),
    path('microplan/recalc', views.microplan_recalc, name='microplan-recalc'),
    path('microplan/validate', views.microplan_validate, name='microplan-validate'),
    path('analytics', views.analytics, name='analytics'),
    path('export', views.export_state, name='export-state'),
    path('import', views.import_state, name='import-state'),
    path('login', views.login_view, name='api-login'),
    path('logout', views.logout_view, name='api-logout'),
    path('me', views.me_view, name='api-me'),
    path('roles', views.roles_catalog, name='api-roles'),
]
