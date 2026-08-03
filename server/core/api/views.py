# -*- coding: utf-8 -*-
"""API. CRUD по сущностям и расчётные точки поверх перенесённого ядра.

Сервер — источник правды (ТЗ §2): расчёт делается здесь, интерфейс перерисовывается
из ответа. Дублировать расчётную логику на клиенте нельзя, иначе два расчёта
разойдутся ровно так же, как разошлись экспорт и импорт в самом приложении.

Ограничение по ОП применяется в QuerySet, а не в интерфейсе (ТЗ §7а).
"""
import io

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core import models as m
from core.api import audit
from core.api import roles as rl
from core.api import serializers as ser
from core.api.permissions import IsSystemAdmin, OpScopedPermission, scope_queryset
from core.calc import analytics as an
from core.calc import autoplan as ap
from core.calc import validation as vd
from core.services.snapshot_db import accessible_op_names, build_snapshot, editable_op_names


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, OpScopedPermission]
    op_field = None          # имя поля с ОП; None — сущность не привязана к ОП

    def get_queryset(self):
        qs = self.queryset
        if self.op_field:
            qs = scope_queryset(qs, self.request.user, self.op_field)
        return qs

    def perform_destroy(self, instance):
        """Удаление тоже попадает в журнал — и именно до самого удаления.

        Запись `ChangeLog` не ссылается на строку внешним ключом как раз ради
        этого случая: строки уже нет, а след того, кто её убрал, обязан остаться.
        """
        audit.record(self.request, self, instance, 'delete')
        instance.delete()

    def handle_exception(self, exc):
        """Конфликт версий отдаём как 409, а не как обычную ошибку валидации."""
        if isinstance(exc, ValidationError):
            detail = exc.detail
            if isinstance(detail, dict) and detail.get('code') == 'version_conflict':
                return Response(detail, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)


class OpViewSet(BaseViewSet):
    queryset = m.Op.objects.all().order_by('name', 'workshop')
    serializer_class = ser.OpSerializer
    op_field = 'name'


class NomenclatureViewSet(BaseViewSet):
    queryset = m.Nomenclature.objects.prefetch_related(
                    'route', 'route_overrides__items').order_by('article_gp')
    serializer_class = ser.NomenclatureSerializer


class ContractViewSet(BaseViewSet):
    queryset = m.Contract.objects.all().order_by('number')
    serializer_class = ser.ContractSerializer


class ProductionOrderViewSet(BaseViewSet):
    queryset = m.ProductionOrder.objects.prefetch_related('stage_ops').order_by('number')
    serializer_class = ser.ProductionOrderSerializer


class MacroplanViewSet(BaseViewSet):
    queryset = m.MacroplanRow.objects.prefetch_related('order_nums').order_by('month', 'id')
    serializer_class = ser.MacroplanRowSerializer
    op_field = 'op_name'

    def get_queryset(self):
        qs = super().get_queryset()
        month = self.request.query_params.get('month')
        if month:
            qs = qs.filter(month=month)
        return qs


class MicroplanViewSet(BaseViewSet):
    queryset = (m.MicroplanRow.objects
                .prefetch_related('sub_orders', 'workers', 'article_items', 'launches')
                .order_by('date', 'id'))
    serializer_class = ser.MicroplanRowWriteSerializer
    op_field = 'op_name'

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('month'):
            qs = qs.filter(date__startswith=p['month'])
        if p.get('date'):
            qs = qs.filter(date=p['date'])
        if p.get('stage'):
            qs = qs.filter(stage=p['stage'])
        if p.get('order'):
            qs = qs.filter(release_order=p['order'])
        return qs


class ScheduleViewSet(BaseViewSet):
    queryset = m.Schedule.objects.prefetch_related('workers').order_by('op_name', 'name')
    serializer_class = ser.ScheduleSerializer
    op_field = 'op_name'


class ScheduleMonthOverrideViewSet(BaseViewSet):
    """Помесячные правки графика. К ОП привязаны через бригаду, а не полем,
    поэтому по ОП не режутся — как и сама бригада, они видны в пределах доступа."""

    queryset = m.ScheduleMonthOverride.objects.all().order_by('schedule_src_id', 'month')
    serializer_class = ser.ScheduleMonthOverrideSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('schedule_id'):
            qs = qs.filter(schedule_src_id=p['schedule_id'])
        if p.get('month'):
            qs = qs.filter(month=p['month'])
        return qs


class MacroEffViewSet(BaseViewSet):
    queryset = m.MacroEff.objects.all().order_by('op_name', 'workshop', 'month')
    serializer_class = ser.MacroEffSerializer
    op_field = 'op_name'


class UserViewSet(viewsets.ModelViewSet):
    """Пользователи, роли и доступы к площадкам. Только для администратора.

    Администратор может назначить администратором другого — иначе система
    зависела бы от одного человека. Обратная сторона: снять последнего админа
    нельзя, иначе управлять правами станет некому и придётся лезть в консоль.
    """

    serializer_class = ser.UserSerializer
    permission_classes = [IsSystemAdmin]

    def get_queryset(self):
        return (get_user_model().objects
                .prefetch_related('groups', 'op_access')
                .order_by('username'))

    def perform_update(self, serializer):
        self._guard_last_admin(serializer.instance, serializer.validated_data)
        serializer.save()

    def perform_destroy(self, instance):
        self._guard_last_admin(instance, {'is_active': False, 'is_superuser': False})
        instance.delete()

    def _guard_last_admin(self, user, changes):
        """Не дать остаться без администратора.

        Считаем именно АКТИВНЫХ админов: снятая галочка «активен» запирает
        систему так же надёжно, как снятая галочка «администратор».
        """
        if not user.is_superuser or not user.is_active:
            return
        stays_admin = changes.get('is_superuser', user.is_superuser)
        stays_active = changes.get('is_active', user.is_active)
        if stays_admin and stays_active:
            return
        others = (get_user_model().objects
                  .filter(is_superuser=True, is_active=True)
                  .exclude(pk=user.pk).count())
        if others == 0:
            raise ValidationError({
                'detail': 'Это последний администратор. Сначала назначьте другого — '
                          'иначе управлять правами станет некому.'})


class ChangeLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Журнал изменений: кто что менял. Только чтение — история не правится.

    Режется по ОП, как и сами данные: иначе через журнал было бы видно чужие
    площадки, которые в самих таблицах закрыты. Записи без площадки (справочники,
    массовые операции) видны всем — они и не привязаны к конкретной фабрике.
    """

    queryset = m.ChangeLog.objects.all()
    serializer_class = ser.ChangeLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        allowed = accessible_op_names(self.request.user)
        if allowed is not None:
            qs = qs.filter(Q(op_name='') | Q(op_name__in=allowed))
        p = self.request.query_params
        if p.get('entity'):
            qs = qs.filter(entity=p['entity'])
        if p.get('src_id'):
            qs = qs.filter(src_id=p['src_id'])
        if p.get('user'):
            qs = qs.filter(username=p['user'])
        if p.get('op'):
            qs = qs.filter(op_name=p['op'])
        if p.get('since'):
            qs = qs.filter(at__gte=p['since'])
        return qs


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def roles_catalog(request):
    """Справочник ролей: какая роль что открывает. Источник — код, не таблица.

    Пути раздела отдаются сюда же, чтобы интерфейс определял раздел сущности по
    её адресу, а не хранил у себя вторую копию деления — она разошлась бы с этой.
    """
    return Response({'roles': rl.role_catalog(),
                     'sections': [{'key': k, 'title': title, 'paths': list(paths)}
                                  for k, (title, paths) in rl.SECTIONS.items()]})


# ── Расчётные точки ─────────────────────────────────────────────────────────

def _snapshot_for(request, month=None, mode='forward'):
    return build_snapshot(op_names=accessible_op_names(request.user),
                          month=month, micro_check_mode=mode)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def microplan_recalc(request):
    """Пересчёт авто-плана.

    Без параметров пересчитывает весь доступный срез; с `date`+`schedule_id`
    (или `op`) — только эту бригаду-день. Частичный пересчёт обязателен на объёме:
    полный на 20 площадках уходит в десятки минут (ТЗ §8).

    По умолчанию НЕ пишет в базу — возвращает, что получилось бы. Запись только
    при `commit=true` и только по ОП, где у пользователя есть право правки.
    """
    p = request.data or {}
    month = p.get('month')
    snap = _snapshot_for(request, month=month, mode=p.get('mode', 'forward'))

    if p.get('date') and (p.get('schedule_id') is not None or p.get('op')):
        row = next((r for r in snap.microplan
                    if r.get('date') == p['date']
                    and (p.get('schedule_id') is None
                         or str(r.get('scheduleId')) == str(p['schedule_id']))
                    and (not p.get('op') or r.get('op') == p['op'])), None)
        if row is None:
            return Response({'detail': 'Строка бригады-дня не найдена'},
                            status=status.HTTP_404_NOT_FOUND)
        ap.recalc_plan_release(snap, row)
        touched = ap.brigade_day_group_rows(snap, row) if hasattr(ap, 'brigade_day_group_rows') \
            else [r for r in snap.microplan if r.get('date') == row.get('date')]
        scope = 'brigade_day'
    else:
        ap.recalc_all_micro_plans(snap)
        touched = snap.microplan
        scope = 'all'

    result = [{'src_id': r.get('id'), 'date': r.get('date'), 'op': r.get('op'),
               'stage': r.get('stage'), 'releaseOrder': r.get('releaseOrder'),
               'planRelease': r.get('planRelease'), 'planLaunch': r.get('planLaunch'),
               'planByArticle': r.get('planByArticle') or {},
               'subOrders': [{'src_id': s.get('id'), 'releaseOrder': s.get('releaseOrder'),
                              'planRelease': s.get('planRelease')}
                             for s in (r.get('subOrders') or [])]}
              for r in touched]

    committed = 0
    if str(p.get('commit', '')).lower() in ('1', 'true', 'yes'):
        # Запись плана — это правка микроплана, а не отдельное право: пересчёт
        # с commit меняет ровно те же строки, что и ручной ввод.
        if not rl.can_edit_section(request.user, 'microplan'):
            return Response({'detail': 'Нет прав на изменение микроплана'},
                            status=status.HTTP_403_FORBIDDEN)
        editable = editable_op_names(request.user)
        with transaction.atomic():
            for r in result:
                if editable is not None and r['op'] not in editable:
                    continue
                upd = m.MicroplanRow.objects.filter(src_id=r['src_id'])
                if upd.update(plan_release=r['planRelease'] or 0,
                              plan_launch=r['planLaunch'] or 0):
                    committed += 1
                for s in r['subOrders']:
                    m.MicroSubOrder.objects.filter(src_id=s['src_id']).update(
                        plan_release=s['planRelease'] or 0)

    if committed:
        # Одна запись на пересчёт, а не на каждую строку: месяц — это сотни строк,
        # и построчный журнал стал бы нечитаем ровно тогда, когда понадобится.
        audit.record_bulk(request, 'microplan', 'recalc',
                          f'пересчёт ({scope}), строк записано: {committed}'
                          + (f', месяц {month}' if month else ''),
                          op_name=p.get('op') or '')

    return Response({'scope': scope, 'rows': result, 'committed': committed})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def microplan_validate(request):
    """Вердикты проверок по строке.

    Возвращает и уровень: `stop` блокирует ввод, `warn` — только жёлтый индикатор
    (CLAUDE.md §4.8). Интерфейс обязан различать: раньше всё било модалкой, и при
    любом опережении или отставании заполнение превращалось в череду подтверждений.
    """
    p = request.data or {}
    src_id = p.get('src_id')
    if src_id is None:
        return Response({'detail': 'Нужен src_id строки микроплана'},
                        status=status.HTTP_400_BAD_REQUEST)

    snap = _snapshot_for(request, mode=p.get('mode', 'forward'))
    row = next((r for r in snap.microplan if r.get('id') == src_id), None)
    if row is None:
        return Response({'detail': 'Строка не найдена или недоступна'},
                        status=status.HTTP_404_NOT_FOUND)

    out = {}
    if 'fact' in p:
        for name, verdict in (
            ('stage_fact', vd.validate_stage_fact(snap, row, p['fact'], exclude_row_id=src_id)),
            ('fact_vs_order_qty', vd.validate_fact_vs_order_qty(snap, row, p['fact'],
                                                                exclude_row_id=src_id)),
            ('release_vs_launches', vd.validate_release_vs_launches(snap, row, p['fact'])),
        ):
            out[name] = _verdict(verdict)
    if 'plan' in p:
        out['plan_order_limit'] = _verdict(vd.validate_plan_order_limit(snap, row, p['plan']))
        out['plan_vs_prev_stage'] = _verdict(
            vd.validate_plan_vs_prev_stage_plan(snap, row, p['plan']))
    out['delivery_date'] = _verdict(vd.check_delivery_date(snap, row))

    blocking = [k for k, v in out.items() if not v['ok'] and v['severity'] == vd.STOP]
    return Response({'checks': out, 'blocking': blocking, 'has_warnings':
                     any(not v['ok'] and v['severity'] == vd.WARN for v in out.values())})


def _verdict(v):
    return {'ok': v.ok, 'severity': v.severity, 'msg': v.msg,
            'available': v.available, 'limit': v.limit, 'prev_stage': v.prev_stage,
            **({'extra': v.extra} if v.extra else {})}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def analytics(request):
    """Агрегаты аналитики за месяц.

    Штуки считаются через комплектность на переделе агрегации, минуты — суммой:
    труд аддитивен по переделам и узлам, а готовая продукция нет.
    """
    p = request.query_params
    month = p.get('month')
    if not month:
        return Response({'detail': 'Нужен параметр month (ГГГГ-ММ)'},
                        status=status.HTTP_400_BAD_REQUEST)

    snap = _snapshot_for(request)
    f = an.Filters(month=month, op=p.get('op', ''), stage=p.get('stage', ''),
                   brigade_id=p.get('brigade', ''), contract=p.get('contract', ''),
                   assortment=p.get('assortment', ''), article_gp=p.get('article', ''),
                   unit='min' if p.get('unit') == 'min' else 'qty')
    totals = an.month_totals(snap, f)
    return Response({
        'month': month, 'unit': totals.unit,
        'agg_stage': totals.agg_stage, 'agg_stage_micro': totals.agg_stage_micro,
        'macro_plan': totals.macro_plan,
        'micro_plan': totals.micro_plan, 'micro_fact': totals.micro_fact,
        'rows_micro': totals.rows_micro, 'rows_macro': totals.rows_macro,
        'by_date': totals.by_date,
        'weeks': an.month_weeks(month),
        'baseline': an.baseline_vs_fact(snap, month),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_state(request):
    """Выгрузка в формате приложения — аварийный люк из ТЗ §11.

    Данные всегда можно забрать и вернуться к автономному `План.html`.
    """
    from core.management.commands.export_json import build_export
    return Response(build_export())


class CalOverrideViewSet(BaseViewSet):
    queryset = m.CalOverride.objects.all().order_by('date', 'id')
    serializer_class = ser.CalOverrideSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('schedule_id'):
            qs = qs.filter(schedule_src_id=p['schedule_id'])
        if p.get('month'):
            qs = qs.filter(date__startswith=p['month'])
        return qs


class ManualFrvViewSet(BaseViewSet):
    queryset = m.ManualFrv.objects.prefetch_related('segments').order_by('op_name', 'month')
    serializer_class = ser.ManualFrvSerializer
    op_field = 'op_name'


class HolidayViewSet(BaseViewSet):
    queryset = m.Holiday.objects.all().order_by('date')
    serializer_class = ser.HolidaySerializer


class DeliveryBaseViewSet(BaseViewSet):
    """Справочник баз поставки. К ОП не привязан — виден всем."""
    queryset = m.DeliveryBase.objects.all().order_by('name')
    serializer_class = ser.DeliveryBaseSerializer


class DeliveryMatrixViewSet(BaseViewSet):
    queryset = m.DeliveryMatrix.objects.all().order_by('from_op', 'to_op')
    serializer_class = ser.DeliveryMatrixSerializer


class PlanBaselineViewSet(BaseViewSet):
    queryset = m.PlanBaseline.objects.all().order_by('month', 'date', 'id')
    serializer_class = ser.PlanBaselineSerializer
    op_field = 'op_name'

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('month'):
            qs = qs.filter(month=self.request.query_params['month'])
        return qs


class OrderLinkViewSet(BaseViewSet):
    queryset = m.OrderLink.objects.all().order_by('id')
    serializer_class = ser.OrderLinkSerializer


class TimeCostOverrideViewSet(BaseViewSet):
    queryset = m.TimeCostOverride.objects.all().order_by('order_number', 'stage')
    serializer_class = ser.TimeCostOverrideSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('order'):
            qs = qs.filter(order_number=p['order'])
        if p.get('stage'):
            qs = qs.filter(stage=p['stage'])
        return qs


# ── Вход и загрузка состояния ───────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    """Вход по логину и паролю. Ставит сессионную куку — её и использует интерфейс."""
    from django.contrib.auth import authenticate, login

    username = (request.data or {}).get('username')
    password = (request.data or {}).get('password')
    user = authenticate(request, username=username, password=password)
    if user is None:
        return Response({'detail': 'Неверный логин или пароль'},
                        status=status.HTTP_401_UNAUTHORIZED)
    login(request, user)
    return Response(_whoami(user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    from django.contrib.auth import logout
    logout(request)
    return Response({'detail': 'Выход выполнен'})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def me_view(request):
    """Кто я и что мне доступно. Интерфейс строит по этому набор вкладок и прав."""
    return Response(_whoami(request.user))


def _whoami(user):
    readable = accessible_op_names(user)
    editable = editable_op_names(user)
    sections = rl.sections_of(user)
    return {
        'username': user.username,
        'is_superuser': user.is_superuser,
        # None означает «все ОП» — интерфейсу проще получить явный признак.
        'all_ops': readable is None,
        'ops_read': readable or [],
        'ops_edit': editable or [],
        'roles': sorted(user.groups.values_list('name', flat=True)),
        # Аналогично: None — «все разделы», интерфейс не должен это выводить сам.
        'all_sections': sections is None,
        'sections_edit': sections or [],
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def import_state(request):
    """Загрузка полного состояния в формате приложения.

    Два режима, и разница между ними важная:

    * без параметров — ЗАМЕЩАЕТ все данные;
    * `?merge=1` — сливает с тем, что уже в базе: совпадающие записи обновляются,
      новые добавляются. Так несколько выгрузок с разных компьютеров собираются
      в одну базу, не затирая друг друга.

    Дубли ищутся по деловому ключу (номер контракта, номер заказа, дата+ОП+передел
    для микроплана), а НЕ по `id`: счётчик id у каждого браузера свой, и слияние по
    нему смешало бы разные записи (`core/services/merge.py`).

    Только для администратора. Ждём файл, уже нормализованный приложением: слой из
    22 миграций старых форматов здесь намеренно не воспроизводится
    (DATA_CONTRACT.md §2).
    """
    if not request.user.is_superuser:
        return Response({'detail': 'Загрузка состояния доступна только администратору'},
                        status=status.HTTP_403_FORBIDDEN)

    data = request.data
    if not isinstance(data, dict) or 'microplan' not in data:
        return Response({'detail': 'Ожидается объект выгрузки «Плана»'},
                        status=status.HTTP_400_BAD_REQUEST)

    import json
    import tempfile
    from django.core.management import call_command

    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False,
                                     encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False)
        tmp = fh.name

    merge = str(request.query_params.get('merge', '')).lower() in ('1', 'true', 'yes')
    args = [tmp] + (['--merge'] if merge else [])

    out = io.StringIO()
    try:
        call_command('import_json', *args, stdout=out)
    except Exception as exc:
        return Response({'detail': f'{type(exc).__name__}: {exc}'},
                        status=status.HTTP_400_BAD_REQUEST)

    audit.record_bulk(request, 'import', 'import',
                      ('слияние с базой' if merge else 'полное замещение данных')
                      + f", файл на {len(data.get('microplan') or [])} строк микроплана")

    return Response({'detail': 'Данные слиты с базой' if merge else 'Состояние загружено',
                     'merge': merge,
                     'report': out.getvalue(),
                     'counts': {'microplan': m.MicroplanRow.objects.count(),
                                'macroplan': m.MacroplanRow.objects.count(),
                                'orders': m.ProductionOrder.objects.count(),
                                'contracts': m.Contract.objects.count(),
                                'schedules': m.Schedule.objects.count()}})
