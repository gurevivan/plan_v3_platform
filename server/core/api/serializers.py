# -*- coding: utf-8 -*-
"""Сериализаторы API.

`version` отдаётся всегда и требуется при изменении — это оптимистичная
блокировка (ТЗ §8). При 100 пользователях правки пересекаются редко, потому что
люди работают в разных ОП и разных сущностях, поэтому пессимистичные блокировки
не нужны — достаточно поймать конфликт и переспросить.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from core import models as m
from core.api import audit


class VersionedSerializer(serializers.ModelSerializer):
    """Базовый: проверяет `version` при обновлении.

    Клиент присылает версию, которую видел. Если в базе она уже другая — значит
    кто-то сохранил раньше, и мы отвечаем 409, а не затираем чужую правку молча.
    """

    # Служебные ключи `extra`: это не данные записи, а бухгалтерия импорта.
    EXTRA_SKIP = ('__absent', '__null')

    def update(self, instance, validated_data):
        sent = self.initial_data.get('version')
        if sent is not None and int(sent) != instance.version:
            raise serializers.ValidationError({
                'version': f'Запись изменена другим пользователем '
                           f'(у вас {sent}, в базе {instance.version}). Обновите данные.',
                'code': 'version_conflict',
            })
        validated_data['version'] = instance.version + 1
        before = audit.snapshot(instance, validated_data.keys())
        instance = super().update(instance, validated_data)
        self._save_extra(instance)
        self._audit(instance, 'update', before)
        return instance

    def create(self, validated_data):
        instance = super().create(validated_data)
        self._save_extra(instance)
        self._audit(instance, 'create')
        return instance

    def _audit(self, instance, action, before=None):
        """Запись в журнал изменений. Здесь, а не в обработчиках вкладок: точка
        сохранения одна, и забыть её нельзя."""
        ctx = getattr(self, 'context', None) or {}
        request, view = ctx.get('request'), ctx.get('view')
        if request is None or view is None:
            return                      # вызов не из API (импорт, команда) — не журналим
        audit.record(request, view, instance, action, before)

    def to_representation(self, instance):
        """Отдать поля, которые не разложены по колонкам.

        Они лежат в `extra` и при выгрузке возвращаются в запись как есть. Если их
        не отдавать, интерфейс о них не узнает и, сохранив запись, сотрёт. Это не
        абстрактный риск: в `calOverrides` так живёт СОСТАВ РАБОТНИКОВ на конкретный
        день — потеря означала бы неверную мощность этого дня, причём молча.
        """
        data = super().to_representation(instance)
        raw = getattr(instance, 'extra', None) or {}
        ex = {k: v for k, v in raw.items() if k not in self.EXTRA_SKIP}
        if ex:
            data['extra_fields'] = ex
        absent = raw.get('__absent') or []
        if absent:
            data['absent_fields'] = list(absent)
        nulls = raw.get('__null') or []
        if nulls:
            data['null_fields'] = list(nulls)
        return data

    def build_standard_field(self, field_name, model_field):
        """Не обрезать пробелы в текстовых полях.

        DRF по умолчанию делает `trim_whitespace=True`, и сохранение записи молча
        превращало бы «Футболка » в «Футболка». Пользователь этого не просил, а
        сравнение с выгрузкой начинало бы показывать расхождения на ровном месте.
        """
        cls, kwargs = super().build_standard_field(field_name, model_field)
        if issubclass(cls, serializers.CharField):
            kwargs.setdefault('trim_whitespace', False)
        return cls, kwargs

    def _save_extra(self, instance) -> None:
        """Вернуть в `extra` то, что интерфейс о записи знает, но колонками не описано,
        и список полей, которых в записи не было."""
        raw = self.initial_data.get('extra_fields')
        absent = self.initial_data.get('absent_fields')
        nulls = self.initial_data.get('null_fields')
        if not isinstance(raw, dict) and absent is None and nulls is None:
            return
        cur = getattr(instance, 'extra', None) or {}
        ex = {k: v for k, v in cur.items() if k in self.EXTRA_SKIP}
        if isinstance(raw, dict):
            ex.update(raw)
        for key, val in (('__absent', absent), ('__null', nulls)):
            if val is None:
                continue
            if val:
                ex[key] = list(val)
            else:
                ex.pop(key, None)
        instance.extra = ex
        instance.save(update_fields=['extra'])


class ChildSerializer(serializers.ModelSerializer):
    """База для вложенных записей (работники, подзаказы, запуски, варианты ФРВ).

    Даёт то же, что `VersionedSerializer` даёт записям верхнего уровня:
    несмоделированные поля (`extra_fields`) и признак отсутствия (`absent_fields`).
    Своей версии у дочерней записи нет — она правится только вместе с родителем,
    и блокировка проверяется на нём.
    """

    EXTRA_SKIP = ('__absent', '__null')

    absent_fields = serializers.SerializerMethodField()
    extra_fields = serializers.SerializerMethodField()

    def get_absent_fields(self, obj):
        return list((getattr(obj, 'extra', None) or {}).get('__absent', []) or [])

    def get_extra_fields(self, obj):
        return {k: v for k, v in (getattr(obj, 'extra', None) or {}).items()
                if k not in self.EXTRA_SKIP}

    def build_standard_field(self, field_name, model_field):
        cls, kwargs = super().build_standard_field(field_name, model_field)
        if issubclass(cls, serializers.CharField):
            kwargs.setdefault('trim_whitespace', False)
        return cls, kwargs


class NestedWriteMixin:
    """Запись вложенных списков.

    Семантика ЗАМЕЩАЮЩАЯ: присланный список полностью заменяет прежний. Так проще
    и предсказуемее, чем частичное слияние: интерфейс всегда держит запись целиком
    и отправляет её целиком. Не прислали ключ — вложенный список не трогаем.

    Записи с существующим `id` обновляются на месте (сохраняется `src_id`, а с ним
    связь с исходной выгрузкой), новые создаются, пропавшие удаляются.

    Механизм один на все вкладки. Заводить по своему на каждую нельзя: ровно так
    в самом приложении разошлись экспорт и импорт и потеряли `orderLinks`
    (DATA_CONTRACT.md §4).
    """

    nested = {}        # {имя_поля: (модель, related_name)} — списки объектов
    simple_lists = {}  # {имя_поля: (модель, related_name, колонка)} — списки строк

    def _write_children(self, instance, raw: dict) -> None:
        self._write_nested(instance, raw)
        self._write_simple(instance, raw)

    def _write_nested(self, instance, raw: dict) -> None:
        for field, (model, related) in self.nested.items():
            if field not in raw:
                continue
            items = raw.get(field) or []
            manager = getattr(instance, related)
            names = {f.name for f in model._meta.get_fields()}
            # Пропавшие удаляем ДО создания новых. Иначе пересоздание записи с теми
            # же значениями упирается в уникальность: старая строка ещё жива.
            # Так падало сохранение заказа — у ОП передела ограничение
            # (заказ, передел, ОП), а интерфейс шлёт их без идентификаторов.
            manager.exclude(pk__in=[i['id'] for i in items
                                    if isinstance(i, dict) and i.get('id')]).delete()
            keep = []
            for ordinal, item in enumerate(items):
                item = dict(item)
                item['ordinal'] = ordinal
                obj_id = item.pop('id', None)
                item.pop('row', None)
                absent = item.pop('absent_fields', None)
                extra = item.pop('extra_fields', None)
                obj = manager.filter(pk=obj_id).first() if obj_id else None
                if obj:
                    for k, v in item.items():
                        if k in names:
                            setattr(obj, k, v)
                    obj.save()
                else:
                    obj = manager.create(**{k: v for k, v in item.items() if k in names})
                self._write_child_extra(obj, absent, extra)
                keep.append(obj.pk)
            manager.exclude(pk__in=keep).delete()

    @staticmethod
    def _write_child_extra(obj, absent, extra) -> None:
        """Служебная часть `extra` дочерней записи: отсутствующие и несмоделированные
        поля. Пишем одним сохранением — иначе второе затирало бы первое."""
        if absent is None and extra is None:
            return
        ex = dict(getattr(obj, 'extra', None) or {})
        if extra is not None:
            ex = {k: v for k, v in ex.items() if k in ('__absent', '__null')}
            ex.update(extra)
        if absent is not None:
            if absent:
                ex['__absent'] = list(absent)
            else:
                ex.pop('__absent', None)
        obj.extra = ex
        obj.save(update_fields=['extra'])

    def _write_simple(self, instance, raw: dict) -> None:
        """Список простых значений (`article_items`, `order_numbers`): пересоздаём.

        Своего идентификатора у элемента нет, обновлять на месте нечего.
        """
        for field, (model, related, column) in self.simple_lists.items():
            if field not in raw:
                continue
            manager = getattr(instance, related)
            manager.all().delete()
            for ordinal, val in enumerate(raw.get(field) or []):
                if val not in (None, ''):
                    manager.create(**{column: val, 'ordinal': ordinal})

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        self._write_children(instance, self.initial_data)
        return instance

    def create(self, validated_data):
        instance = super().create(validated_data)
        self._write_children(instance, self.initial_data)
        return instance


class OpSerializer(VersionedSerializer):
    class Meta:
        model = m.Op
        fields = ['id', 'src_id', 'name', 'workshop', 'op_type', 'process_type',
                  'shift_duration', 'frv_min', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class NomRouteSerializer(ChildSerializer):
    class Meta:
        model = m.NomRoute
        fields = ['id', 'ordinal', 'stage', 'article_item', 'name', 'time_cost', 'absent_fields', 'extra_fields']


class NomRouteOverrideItemSerializer(ChildSerializer):
    class Meta:
        model = m.NomRouteOverrideItem
        fields = ['id', 'ordinal', 'stage', 'article_item', 'name', 'time_cost',
                  'absent_fields', 'extra_fields']


class NomRouteOverrideSerializer(serializers.ModelSerializer):
    """Маршрут, переопределённый на уровень ОП (CLAUDE.md §4.1).

    Свойство безопасности: пока переопределений нет, действует базовый маршрут.
    Поэтому пустой список — это «нет переопределений», а не «маршрут пустой».
    """

    items = NomRouteOverrideItemSerializer(many=True, read_only=True)

    class Meta:
        model = m.NomRouteOverride
        fields = ['id', 'op_name', 'items']


class NomenclatureSerializer(NestedWriteMixin, VersionedSerializer):
    """Карточка изделия. Инвариант: одна карточка = один `articleGp`.

    Маршрут и переопределения по ОП пишутся вместе с карточкой: норма живёт в
    строке маршрута, и карточка без маршрута ничего не считает.
    """

    route = NomRouteSerializer(many=True, read_only=True)
    route_overrides = NomRouteOverrideSerializer(many=True, read_only=True)

    nested = {'route': (m.NomRoute, 'route')}

    class Meta:
        model = m.Nomenclature
        fields = ['id', 'src_id', 'article_gp', 'article_item', 'name', 'model_code',
                  'assortment_group', 'norm_type', 'ac_flag', 'nom_ops', 'nom_workshops',
                  'route', 'route_overrides', 'version', 'updated_at']
        read_only_fields = ['updated_at']

    def _write_children(self, instance, raw: dict) -> None:
        super()._write_children(instance, raw)
        self._write_overrides(instance, raw)

    def _write_overrides(self, instance, raw: dict) -> None:
        if 'route_overrides' not in raw:
            return
        sent = raw.get('route_overrides') or []
        keep = []
        for ov in sent:
            op_name = (ov or {}).get('op_name') or ''
            if not op_name:
                continue
            obj, _ = m.NomRouteOverride.objects.get_or_create(
                nomenclature=instance, op_name=op_name)
            keep.append(obj.pk)
            obj.items.all().delete()
            for ordinal, it in enumerate(ov.get('items') or []):
                it = dict(it)
                it.pop('id', None)
                absent = it.pop('absent_fields', None)
                extra = it.pop('extra_fields', None)
                child = m.NomRouteOverrideItem.objects.create(
                    override=obj, ordinal=ordinal,
                    stage=it.get('stage') or '',
                    article_item=it.get('article_item') or '',
                    name=it.get('name') or '',
                    time_cost=it.get('time_cost') or 0)
                NestedWriteMixin._write_child_extra(child, absent, extra)
        instance.route_overrides.exclude(pk__in=keep).delete()


class ContractSerializer(VersionedSerializer):
    """Контракт.

    `delivery_schedule`, `deadlines`, `contract_article_items` отдаются как есть:
    в схеме они JSON-колонки (models.Contract), расчётное ядро в них не ходит.
    Не отдавать их нельзя — вкладка «Контракты» правит именно их.
    """

    class Meta:
        model = m.Contract
        fields = ['id', 'src_id', 'number', 'article_gp', 'name', 'quantity',
                  'delivery_date', 'delivery_schedule', 'deadlines',
                  'contract_article_items', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class ProductionOrderOpSerializer(ChildSerializer):
    class Meta:
        model = m.ProductionOrderOp
        fields = ['id', 'stage', 'op_name', 'ordinal', 'absent_fields', 'extra_fields']


class OrderDeliverySerializer(ChildSerializer):
    class Meta:
        model = m.OrderDelivery
        fields = ['id', 'src_id', 'ordinal', 'base', 'date', 'quantity', 'absent_fields', 'extra_fields']


class ProductionOrderSerializer(NestedWriteMixin, VersionedSerializer):
    """Заказ на производство.

    ОП заказа — МАССИВЫ по переделам (CLAUDE.md §4.1), поэтому `stage_ops` — список
    пар (передел, ОП), а не одно поле. Единого `order.op` у заказа нет и заводить его
    нельзя: один передел делается сразу на нескольких площадках.
    """

    stage_ops = ProductionOrderOpSerializer(many=True, read_only=True)
    deliveries = OrderDeliverySerializer(many=True, read_only=True)

    nested = {
        'stage_ops': (m.ProductionOrderOp, 'stage_ops'),
        'deliveries': (m.OrderDelivery, 'deliveries'),
    }

    class Meta:
        model = m.ProductionOrder
        fields = ['id', 'src_id', 'number', 'article_gp', 'name', 'quantity',
                  'contract_num', 'contract_src_id', 'delivery_date',
                  'stage_ops', 'deliveries', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class MacroplanRowSerializer(NestedWriteMixin, VersionedSerializer):
    """Строка макроплана.

    НЕ агрегируется: каждая введённая запись — своя строка со своими нормой и
    заказами (CLAUDE.md §7). `order_numbers` обязателен на запись — без привязки
    заказа норма из макроплана не доходит до микроплана (§4.3).
    """

    order_numbers = serializers.SerializerMethodField()

    simple_lists = {'order_numbers': (m.MacroplanRowOrder, 'order_nums', 'order_number')}

    class Meta:
        model = m.MacroplanRow
        fields = ['id', 'src_id', 'contract_src_id', 'article_item', 'op_name', 'workshop',
                  'month', 'qty_sew', 'qty_rc', 'qty_ac', 'qty_pack', 'volume_type',
                  'eff', 'norm_override', 'macro_release_order', 'order_numbers',
                  'version', 'updated_at']
        read_only_fields = ['updated_at']

    def get_order_numbers(self, obj):
        return [x.order_number for x in obj.order_nums.all()]


class MicroSubOrderSerializer(ChildSerializer):
    class Meta:
        model = m.MicroSubOrder
        fields = ['id', 'src_id', 'ordinal', 'release_order', 'launch_order', 'article_items', 'plan_release', 'plan_release_main', 'plan_release_extra', 'fact_release', 'fact_launch', 'plan_release_is_manual', 'learning_eff', 'absent_fields', 'extra_fields']


class MicroRowWorkerSerializer(ChildSerializer):
    class Meta:
        model = m.MicroRowWorker
        fields = ['id', 'src_id', 'ordinal', 'name', 'staff_count', 'attendance', 'shift_time', 'eff_pct', 'absence', 'absent_fields', 'extra_fields']


class LaunchEntrySerializer(ChildSerializer):
    class Meta:
        model = m.LaunchEntry
        fields = ['id', 'src_id', 'ordinal', 'order_number', 'article_item', 'qty', 'absent_fields', 'extra_fields']


class MicroplanRowSerializer(VersionedSerializer):
    sub_orders = MicroSubOrderSerializer(many=True, read_only=True)
    workers = MicroRowWorkerSerializer(many=True, read_only=True)
    launches = LaunchEntrySerializer(many=True, read_only=True)
    article_items = serializers.SerializerMethodField()

    class Meta:
        model = m.MicroplanRow
        fields = ['id', 'src_id', 'date', 'schedule_src_id', 'op_name', 'workshop', 'stage',
                  'release_order', 'launch_order', 'article_item', 'article_items',
                  'plan_release', 'plan_launch', 'fact_release', 'fact_launch',
                  'plan_release_main', 'plan_release_extra', 'plan_by_article',
                  'contract_src_id', 'plan_release_is_manual', 'learning_eff', 'comment',
                  'sub_orders', 'workers', 'launches', 'version', 'updated_at']
        read_only_fields = ['updated_at']

    def get_article_items(self, obj):
        return [x.article_item for x in obj.article_items.all()]


class ScheduleWorkerSerializer(ChildSerializer):
    class Meta:
        model = m.ScheduleWorker
        fields = ['id', 'src_id', 'ordinal', 'name', 'staff_count', 'shift_time', 'eff_pct', 'absence', 'absent_fields', 'extra_fields']


class ScheduleSerializer(NestedWriteMixin, VersionedSerializer):
    workers = ScheduleWorkerSerializer(many=True, read_only=True)

    nested = {'workers': (m.ScheduleWorker, 'workers')}

    class Meta:
        model = m.Schedule
        fields = ['id', 'src_id', 'name', 'op_name', 'workshop', 'shift_time',
                  'graph_preset', 'work_days', 'rest_days', 'cycle_start', 'skip_weekends',
                  'staff_count', 'eff_pct', 'active_months', 'workers',
                  'version', 'updated_at']
        read_only_fields = ['updated_at']


class ScheduleMonthOverrideSerializer(VersionedSerializer):
    """Помесячная подмена параметров бригады (имя/смена/эффективность).

    Своего `id` в выгрузке у записи нет — ключ естественный: (бригада, месяц).
    """

    class Meta:
        model = m.ScheduleMonthOverride
        fields = ['id', 'schedule_src_id', 'month', 'name', 'shift_time', 'eff_pct',
                  'version', 'updated_at']
        read_only_fields = ['updated_at']


class MacroEffSerializer(VersionedSerializer):
    """S.macroEff — эффективность (ОП, цех, месяц). Терялась при экспорте
    (DATA_CONTRACT.md §4), поэтому в API заведена явной сущностью."""

    class Meta:
        model = m.MacroEff
        fields = ['id', 'src_id', 'op_name', 'workshop', 'month', 'eff',
                  'version', 'updated_at']
        read_only_fields = ['updated_at']


class DeliveryBaseSerializer(VersionedSerializer):
    class Meta:
        model = m.DeliveryBase
        fields = ['id', 'src_id', 'name', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class UserOpAccessSerializer(serializers.ModelSerializer):
    """Доступ к одной площадке. Правится только внутри пользователя (см. UserSerializer)."""

    class Meta:
        model = m.UserOpAccess
        fields = ['op_name', 'can_edit']


class UserSerializer(serializers.ModelSerializer):
    """Пользователь: кто он, что правит (роли) и где (доступы к ОП).

    Всё хранится по одному разу: человек и пароль — в `auth_user`, роли — в
    группах Django, площадки — в `UserOpAccess`. Здесь только сборка их в один
    ответ, чтобы администратору не пришлось ходить по трём экранам.

    Доступы к ОП правятся ТОЛЬКО отсюда: отдельная точка на ту же таблицу дала бы
    два пути записи, и однажды они разошлись бы.
    """

    password = serializers.CharField(write_only=True, required=False,
                                     allow_blank=True, style={'input_type': 'password'})
    roles = serializers.ListField(child=serializers.CharField(), required=False)
    ops = UserOpAccessSerializer(source='op_access', many=True, required=False)
    all_ops = serializers.SerializerMethodField()

    class Meta:
        model = None      # проставляется ниже: User берём из настроек проекта
        fields = ['id', 'username', 'first_name', 'last_name', 'is_active',
                  'is_superuser', 'password', 'roles', 'ops', 'all_ops', 'last_login']
        read_only_fields = ['id', 'last_login']

    def get_all_ops(self, obj):
        """Признак «видит все площадки» — то же правило, что в снимке для расчёта."""
        from core.services.snapshot_db import accessible_op_names
        return accessible_op_names(obj) is None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['roles'] = sorted(instance.groups.values_list('name', flat=True))
        return data

    # ── запись ──────────────────────────────────────────────────────────────
    def validate_roles(self, value):
        from core.api.roles import ROLES
        unknown = [r for r in value if r not in ROLES]
        if unknown:
            raise serializers.ValidationError(
                'Неизвестные роли: ' + ', '.join(unknown))
        return value

    def validate_password(self, value):
        """Пустая строка = «пароль не меняем», иначе проверяем правилами Django."""
        if not value:
            return value
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages))
        return value

    def create(self, validated_data):
        roles = validated_data.pop('roles', [])
        ops = validated_data.pop('op_access', [])
        password = validated_data.pop('password', '')
        if not password:
            raise serializers.ValidationError({'password': 'Нужен пароль для нового пользователя'})
        user = self.Meta.model(**validated_data)
        user.set_password(password)
        # В админку Django пускает `is_staff`; отдельно его не спрашиваем, чтобы
        # не заводить второй признак администратора рядом с `is_superuser`.
        user.is_staff = user.is_superuser
        user.save()
        self._apply_roles(user, roles)
        self._apply_ops(user, ops)
        return user

    def update(self, instance, validated_data):
        roles = validated_data.pop('roles', None)
        ops = validated_data.pop('op_access', None)
        password = validated_data.pop('password', '')
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.is_staff = instance.is_superuser
        instance.save()
        if roles is not None:
            self._apply_roles(instance, roles)
        if ops is not None:
            self._apply_ops(instance, ops)
        return instance

    def _apply_roles(self, user, roles):
        from core.api.roles import group_for
        user.groups.set([group_for(name) for name in roles])

    def _apply_ops(self, user, ops):
        """Полная замена набора площадок: интерфейс присылает список целиком.

        Разница вместо замены здесь не нужна — набор маленький, а «прислали
        неполный список» на замене видно сразу, тогда как разница потеряла бы
        снятый доступ молча.
        """
        user.op_access.all().delete()
        seen = set()
        for rec in ops:
            name = (rec.get('op_name') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            m.UserOpAccess.objects.create(user=user, op_name=name,
                                          can_edit=bool(rec.get('can_edit')))


UserSerializer.Meta.model = get_user_model()


class MicroplanRowWriteSerializer(NestedWriteMixin, MicroplanRowSerializer):
    """Строка микроплана с возможностью править подзаказы, состав и запуски."""

    nested = {
        'sub_orders': (m.MicroSubOrder, 'sub_orders'),
        'workers': (m.MicroRowWorker, 'workers'),
        'launches': (m.LaunchEntry, 'launches'),
    }
    simple_lists = {'article_items': (m.MicroplanRowArticleItem, 'article_items',
                                      'article_item')}

    class Meta(MicroplanRowSerializer.Meta):
        pass


class CalOverrideSerializer(VersionedSerializer):
    class Meta:
        model = m.CalOverride
        fields = ['id', 'src_id', 'schedule_src_id', 'date', 'day_type', 'staff_count',
                  'version', 'updated_at']
        read_only_fields = ['updated_at']


class ManualFrvSegmentSerializer(ChildSerializer):
    class Meta:
        model = m.ManualFrvSegment
        fields = ['id', 'src_id', 'ordinal', 'name', 'people', 'shift_min', 'work_days', 'eff_pct', 'absence', 'absent_fields', 'extra_fields']


class ManualFrvSerializer(NestedWriteMixin, VersionedSerializer):
    """ФРВ месяца. `frv_min` — устаревшая плоская сумма: работает, только пока
    вариантов нет (`manualFrvNominalForRecord`)."""

    segments = ManualFrvSegmentSerializer(many=True, read_only=True)

    nested = {'segments': (m.ManualFrvSegment, 'segments')}

    class Meta:
        model = m.ManualFrv
        fields = ['id', 'src_id', 'op_name', 'workshop', 'month', 'frv_min', 'eff_pct',
                  'segments', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class HolidaySerializer(VersionedSerializer):
    class Meta:
        model = m.Holiday
        fields = ['id', 'src_id', 'date', 'name', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class DeliveryMatrixSerializer(VersionedSerializer):
    class Meta:
        model = m.DeliveryMatrix
        fields = ['id', 'src_id', 'from_op', 'to_op', 'days', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class PlanBaselineSerializer(VersionedSerializer):
    class Meta:
        model = m.PlanBaseline
        fields = ['id', 'src_id', 'month', 'date', 'schedule_src_id', 'op_name', 'workshop',
                  'stage', 'release_order', 'article_items', 'plan_release', 'snap_at',
                  'version', 'updated_at']
        read_only_fields = ['updated_at']


class OrderLinkSerializer(VersionedSerializer):
    class Meta:
        model = m.OrderLink
        fields = ['id', 'src_id', 'customer_order_src_id', 'production_order_src_id',
                  'qty', 'version', 'updated_at']
        read_only_fields = ['updated_at']


class TimeCostOverrideSerializer(serializers.ModelSerializer):
    """Индивидуальная норма по заказу. Норма РЦ по заказу — сознательное требование
    домена: «спрямлять» её на все заказы артикула нельзя (CLAUDE.md §4.3)."""

    class Meta:
        model = m.TimeCostOverride
        fields = ['id', 'order_number', 'stage', 'time_cost']


class ChangeLogSerializer(serializers.ModelSerializer):
    """Запись журнала. Только чтение: историю не правят."""

    class Meta:
        model = m.ChangeLog
        fields = ['id', 'at', 'username', 'entity', 'src_id', 'row_id',
                  'op_name', 'action', 'changes', 'note']
        read_only_fields = fields
