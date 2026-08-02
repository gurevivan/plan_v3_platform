# -*- coding: utf-8 -*-
"""Нормализованная схема «План V3» (фаза 1 миграции).

Соответствие коллекциям `S.*` из План.html — см. DATA_CONTRACT.md.

Два сквозных решения:

1. **`extra` (JSONB) у каждой модели.** Хранит поля исходной записи, которые не
   разложены по колонкам: легаси-остатки и то, что появится в приложении раньше,
   чем здесь. Без него круговой рейс JSON→БД→JSON молча терял бы данные, а
   критерий приёмки фазы 1 — эквивалентность. Заглядывать в `extra` полезно:
   что там накопилось, то и не смоделировано.

2. **`src_id`** — идентификатор записи в исходном JSON (`S.*[].id`). Нужен, чтобы
   при импорте связывать записи между собой и чтобы экспорт отдавал те же id.
   Первичный ключ свой: в разных выгрузках исходные id пересекаются.
"""
from django.conf import settings
from django.db import models

STAGES = [('РЦ', 'Раскрой'), ('АЦ', 'Автоматизация'), ('ШЦ', 'Пошив'), ('У', 'Упаковка')]


class Base(models.Model):
    """Общие поля. Абстрактная — своей таблицы нет."""
    src_id = models.BigIntegerField(null=True, blank=True, db_index=True,
                                    verbose_name='id в исходном JSON')
    extra = models.JSONField(default=dict, blank=True,
                             verbose_name='несмоделированные поля исходной записи')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1,
                                          verbose_name='версия для оптимистичной блокировки')

    class Meta:
        abstract = True


# ─────────────────────────── Справочники ────────────────────────────────────

class DeliveryBase(Base):
    """S.bases — справочник баз поставки.

    Наследует `Base` ради `version`: без неё оптимистичная блокировка на этой
    сущности не работала бы, и правка вслепую затирала бы чужую.
    """
    name = models.CharField(max_length=255, unique=True)

    class Meta:
        verbose_name = 'база'
        verbose_name_plural = 'базы'

    def __str__(self):
        return self.name


class Op(Base):
    """S.ops — ОП (обособленное подразделение) и цех.

    В исходном приложении ОП идентифицируется ПО ИМЕНИ: переименование рвёт ссылки
    во всех коллекциях. Здесь суррогатный ключ, а пара (name, workshop) уникальна,
    чтобы импорт сопоставлял записи по имени (DATA_CONTRACT.md §3).
    """
    name = models.CharField(max_length=255, verbose_name='название ОП')
    workshop = models.CharField(max_length=255, blank=True, default='', verbose_name='цех')
    op_type = models.CharField(max_length=32, blank=True, default='', verbose_name='тип (БП/БТК)')
    process_type = models.CharField(max_length=8, choices=STAGES, blank=True, default='',
                                    verbose_name='передел')
    shift_duration = models.PositiveIntegerField(default=480, verbose_name='смена, мин')
    frv_min = models.PositiveIntegerField(default=0, verbose_name='ФРВ, мин')

    class Meta:
        verbose_name = 'ОП'
        verbose_name_plural = 'ОП'
        constraints = [
            models.UniqueConstraint(fields=['name', 'workshop'], name='uniq_op_name_workshop'),
        ]

    def __str__(self):
        return f'{self.name} / {self.workshop}' if self.workshop else self.name


class Nomenclature(Base):
    """S.nomenclature — карточка изделия.

    Инвариант домена: одна карточка = один `article_gp` (CLAUDE.md §3).
    """
    article_gp = models.CharField(max_length=128, unique=True, verbose_name='артикул ГП')
    article_item = models.CharField(max_length=128, blank=True, default='')
    name = models.CharField(max_length=512, blank=True, default='')
    model_code = models.CharField(max_length=128, blank=True, default='', verbose_name='модель')
    assortment_group = models.CharField(max_length=255, blank=True, default='',
                                        verbose_name='ассортиментная группа')
    norm_type = models.CharField(max_length=32, blank=True, default='', verbose_name='БП/БТК')
    ac_flag = models.BooleanField(default=False, verbose_name='признак АЦ')
    nom_ops = models.JSONField(default=list, blank=True, verbose_name='ОП карточки')
    nom_workshops = models.JSONField(default=list, blank=True, verbose_name='цеха карточки')

    class Meta:
        verbose_name = 'номенклатура'
        verbose_name_plural = 'номенклатура'

    def __str__(self):
        return f'{self.article_gp} {self.name}'.strip()


class NomRoute(models.Model):
    """Строка базового маршрута карточки: `nomenclature.route[]`."""
    nomenclature = models.ForeignKey(Nomenclature, on_delete=models.CASCADE, related_name='route')
    ordinal = models.PositiveIntegerField(default=0, verbose_name='порядок в маршруте')
    stage = models.CharField(max_length=8, choices=STAGES)
    article_item = models.CharField(max_length=128, blank=True, default='')
    name = models.CharField(max_length=512, blank=True, default='')
    time_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0,
                                    verbose_name='норма, мин/шт')
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']
        verbose_name = 'строка маршрута'
        verbose_name_plural = 'маршрут'


class NomRouteOverride(models.Model):
    """Маршрут, переопределённый на уровень ОП: `nomenclature.routeOverrides[]`.

    Разные ОП шьют одно изделие по-разному без дублирования карточки (CLAUDE.md §4.1).
    Свойство безопасности: пока переопределений нет, действует базовый маршрут.
    """
    nomenclature = models.ForeignKey(Nomenclature, on_delete=models.CASCADE,
                                     related_name='route_overrides')
    op_name = models.CharField(max_length=255, verbose_name='ОП (по имени, как в исходнике)')
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['nomenclature', 'op_name'],
                                    name='uniq_route_override_per_op'),
        ]


class NomRouteOverrideItem(models.Model):
    """Строка переопределённого маршрута."""
    override = models.ForeignKey(NomRouteOverride, on_delete=models.CASCADE, related_name='items')
    ordinal = models.PositiveIntegerField(default=0)
    stage = models.CharField(max_length=8, choices=STAGES)
    article_item = models.CharField(max_length=128, blank=True, default='')
    name = models.CharField(max_length=512, blank=True, default='')
    time_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']


class DeliveryMatrix(Base):
    """S.deliveryMatrix — дни доставки между ОП."""
    from_op = models.CharField(max_length=255, blank=True, default='')
    to_op = models.CharField(max_length=255, blank=True, default='')
    days = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'строка матрицы доставок'
        verbose_name_plural = 'матрица доставок'


class Holiday(Base):
    """S.holidays."""
    date = models.DateField(null=True, blank=True)
    name = models.CharField(max_length=255, blank=True, default='')


# ──────────────────────────── Контур заказов ────────────────────────────────

class Contract(Base):
    """S.contracts."""
    number = models.CharField(max_length=128, verbose_name='номер контракта')
    article_gp = models.CharField(max_length=128, blank=True, default='')
    name = models.CharField(max_length=512, blank=True, default='')
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    delivery_date = models.DateField(null=True, blank=True)
    # Вложенные структуры контракта. Осознанно оставлены JSON: расчётное ядро их не
    # использует, а нормализация добавила бы три таблицы. Разложить при переносе
    # аналитики по срокам поставки (фаза 6).
    delivery_schedule = models.JSONField(default=list, blank=True,
                                         verbose_name='график поставок по месяцам')
    deadlines = models.JSONField(default=list, blank=True, verbose_name='дедлайны')
    contract_article_items = models.JSONField(default=list, blank=True,
                                              verbose_name='артикулы контракта')

    class Meta:
        verbose_name = 'контракт'
        verbose_name_plural = 'контракты'

    def __str__(self):
        return self.number


class CustomerOrder(Base):
    """S.customerOrders — заказ клиента."""
    number = models.CharField(max_length=128, blank=True, default='')
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='customer_orders')
    contract_src_id = models.BigIntegerField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    delivery_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = 'заказ клиента'
        verbose_name_plural = 'заказы клиентов'


class ProductionOrder(Base):
    """S.orders — заказ на производство.

    Внимание (CLAUDE.md §4.1): у заказа НЕТ единого поля `op`. ОП задаются по
    переделам и являются МАССИВАМИ — один передел может делаться на нескольких ОП.
    Вынесено в ProductionOrderOp.
    """
    number = models.CharField(max_length=128, unique=True, verbose_name='номер заказа')
    article_gp = models.CharField(max_length=128, blank=True, default='')
    name = models.CharField(max_length=512, blank=True, default='')
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    contract_num = models.CharField(max_length=128, blank=True, default='')
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='production_orders')
    contract_src_id = models.BigIntegerField(null=True, blank=True)
    delivery_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = 'заказ на производство'
        verbose_name_plural = 'заказы на производство'

    def __str__(self):
        return self.number


class ProductionOrderOp(models.Model):
    """ОП заказа по переделу (opRc/opAc/opSh/opU — массивы имён)."""
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='stage_ops')
    stage = models.CharField(max_length=8, choices=STAGES)
    op_name = models.CharField(max_length=255)
    ordinal = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordinal']
        constraints = [
            models.UniqueConstraint(fields=['order', 'stage', 'op_name'],
                                    name='uniq_order_stage_op'),
        ]


class OrderDelivery(models.Model):
    """Строка графика поставок заказа: `orders[].deliveries[]`."""
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='deliveries')
    src_id = models.BigIntegerField(null=True, blank=True)
    ordinal = models.PositiveIntegerField(default=0)
    base = models.CharField(max_length=255, blank=True, default='')
    date = models.DateField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']


class OrderLink(Base):
    """S.orderLinks — связь «заказ клиента ↔ заказ на производство».

    Раньше терялась при экспорте (DATA_CONTRACT.md §4). В файлах старого формата
    отсутствует и восстанавливается из `order.contractId` один-к-одному.
    """
    customer_order_src_id = models.BigIntegerField(null=True, blank=True)
    production_order_src_id = models.BigIntegerField(null=True, blank=True)
    production_order = models.ForeignKey(ProductionOrder, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='links')
    qty = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = 'связь ЗК↔ПЗ'
        verbose_name_plural = 'связи ЗК↔ПЗ'


class TimeCostOverride(models.Model):
    """S.tcRcOverrides / S.tcShOverrides — индивидуальная норма по номеру заказа.

    В исходнике два словаря `{номер_заказа: норма}`; здесь одна таблица с переделом.
    Норма РЦ индивидуальна по заказу сознательно — «спрямлять» на все заказы артикула
    нельзя (CLAUDE.md §4.3).
    """
    order_number = models.CharField(max_length=128)
    stage = models.CharField(max_length=8, choices=STAGES)
    time_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['order_number', 'stage'], name='uniq_tc_order_stage'),
        ]
        verbose_name = 'норма по заказу'
        verbose_name_plural = 'нормы по заказам'


# ──────────────────────────────── Планы ─────────────────────────────────────

class MacroplanRow(Base):
    """S.macroplan — строка макроплана.

    НЕ агрегировать: каждая введённая запись — отдельная строка (CLAUDE.md §7).
    """
    contract_src_id = models.BigIntegerField(null=True, blank=True)
    contract = models.ForeignKey(Contract, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='macroplan_rows')
    article_item = models.CharField(max_length=128, blank=True, default='')
    op_name = models.CharField(max_length=255, blank=True, default='')
    workshop = models.CharField(max_length=255, blank=True, default='')
    month = models.CharField(max_length=7, blank=True, default='', verbose_name='ГГГГ-ММ')
    qty_sew = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    qty_rc = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    qty_ac = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    qty_pack = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    volume_type = models.CharField(max_length=16, default='main', verbose_name='main/extra')
    eff = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    norm_override = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    macro_release_order = models.CharField(max_length=128, blank=True, default='')

    class Meta:
        verbose_name = 'строка макроплана'
        verbose_name_plural = 'макроплан'
        indexes = [models.Index(fields=['month', 'op_name'])]


class MacroplanRowOrder(models.Model):
    """`macroplan[].orderNums[]` — заказы, привязанные к строке макроплана.

    Без привязки норма из макроплана НЕ доходит до микроплана (CLAUDE.md §4.3).
    """
    row = models.ForeignKey(MacroplanRow, on_delete=models.CASCADE, related_name='order_nums')
    order_number = models.CharField(max_length=128)
    ordinal = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordinal']
        constraints = [
            models.UniqueConstraint(fields=['row', 'order_number'], name='uniq_macro_row_order'),
        ]


class MacroEff(Base):
    """S.macroEff — эффективность (ОП, цех, месяц) в загрузке макроплана, %."""
    op_name = models.CharField(max_length=255, blank=True, default='')
    workshop = models.CharField(max_length=255, blank=True, default='')
    month = models.CharField(max_length=7, blank=True, default='')
    eff = models.DecimalField(max_digits=8, decimal_places=2, default=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['op_name', 'workshop', 'month'],
                                    name='uniq_macro_eff'),
        ]


class Schedule(Base):
    """S.schedules — бригада и её график."""
    name = models.CharField(max_length=255, blank=True, default='')
    op_name = models.CharField(max_length=255, blank=True, default='')
    workshop = models.CharField(max_length=255, blank=True, default='')
    shift_time = models.PositiveIntegerField(default=480, verbose_name='смена, мин')
    active_months = models.JSONField(default=list, blank=True)
    # Параметры графика — нужны расчётному ядру (фаза 2), поэтому колонками, а не в extra.
    graph_preset = models.CharField(max_length=32, blank=True, default='',
                                    verbose_name='пресет графика, напр. 2/2')
    work_days = models.PositiveIntegerField(default=0, verbose_name='рабочих дней в цикле')
    rest_days = models.PositiveIntegerField(default=0, verbose_name='выходных в цикле')
    cycle_start = models.DateField(null=True, blank=True, verbose_name='начало цикла')
    skip_weekends = models.BooleanField(default=False)
    staff_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    eff_pct = models.DecimalField(max_digits=8, decimal_places=2, default=100)

    class Meta:
        verbose_name = 'бригада'
        verbose_name_plural = 'бригады'

    def __str__(self):
        return f'{self.name} ({self.op_name})'


class ScheduleWorker(models.Model):
    """Группа работников бригады: `schedules[].workers[]`."""
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='workers')
    src_id = models.BigIntegerField(null=True, blank=True)
    ordinal = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=255, blank=True, default='')
    staff_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shift_time = models.PositiveIntegerField(default=480)
    eff_pct = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    absence = models.BooleanField(default=True, verbose_name='учитывать абсентеизм ×0.95')
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']


class ScheduleMonthOverride(Base):
    """S.scheduleMonthOverrides — помесячные правки графика."""
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='month_overrides')
    schedule_src_id = models.BigIntegerField(null=True, blank=True)
    month = models.CharField(max_length=7, blank=True, default='')
    # Помесячная подмена параметров бригады: имя, смена, эффективность.
    name = models.CharField(max_length=255, blank=True, default='')
    shift_time = models.PositiveIntegerField(default=480)
    eff_pct = models.DecimalField(max_digits=8, decimal_places=2, default=100)


class CalOverride(Base):
    """S.calOverrides — правки по конкретному дню, включая состав дня."""
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='cal_overrides')
    schedule_src_id = models.BigIntegerField(null=True, blank=True)
    date = models.DateField(null=True, blank=True)
    day_type = models.CharField(max_length=32, blank=True, default='')
    staff_count = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['schedule_src_id', 'date'])]


class ManualFrv(Base):
    """S.manualFrv — фонд рабочего времени по ОП и месяцам."""
    op_name = models.CharField(max_length=255, blank=True, default='')
    workshop = models.CharField(max_length=255, blank=True, default='')
    month = models.CharField(max_length=7, blank=True, default='')
    # Устаревшая плоская сумма чел-мин: используется, только когда вариантов нет.
    frv_min = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    eff_pct = models.DecimalField(max_digits=8, decimal_places=2, default=100)


class ManualFrvSegment(models.Model):
    """`manualFrv[].frvSegments[]` — вариант ФРВ: люди × смена × рабочих дней."""
    frv = models.ForeignKey(ManualFrv, on_delete=models.CASCADE, related_name='segments')
    src_id = models.BigIntegerField(null=True, blank=True)
    ordinal = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=255, blank=True, default='')
    people = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shift_min = models.PositiveIntegerField(default=0)
    work_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    eff_pct = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    absence = models.BooleanField(default=True, verbose_name='учитывать абсентеизм ×0.95')
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']


class MicroplanRow(Base):
    """S.microplan — строка микроплана (день × бригада × передел × заказ)."""
    date = models.DateField(null=True, blank=True, db_index=True)
    schedule = models.ForeignKey(Schedule, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='microplan_rows')
    schedule_src_id = models.BigIntegerField(null=True, blank=True)
    op_name = models.CharField(max_length=255, blank=True, default='')
    workshop = models.CharField(max_length=255, blank=True, default='')
    stage = models.CharField(max_length=8, choices=STAGES, blank=True, default='')
    release_order = models.CharField(max_length=128, blank=True, default='',
                                     verbose_name='заказ выпуска')
    launch_order = models.CharField(max_length=128, blank=True, default='')
    article_item = models.CharField(max_length=128, blank=True, default='')
    plan_release = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_launch = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fact_release = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fact_launch = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_release_main = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_release_extra = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_release_is_manual = models.BooleanField(default=False)
    learning_eff = models.DecimalField(max_digits=8, decimal_places=2, default=100,
                                       verbose_name='эфф. освоения, %')
    plan_by_article = models.JSONField(default=dict, blank=True)
    contract_src_id = models.BigIntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'строка микроплана'
        verbose_name_plural = 'микроплан'
        indexes = [
            # Ключевой индекс: покрытие заказа считается по (заказ, передел, дата).
            # Именно этот запрос в JS-версии делается сплошным перебором всего
            # микроплана и даёт квадратичную сложность — см. ТЗ §8.
            models.Index(fields=['release_order', 'stage', 'date'], name='idx_micro_order_stage'),
            models.Index(fields=['date', 'schedule_src_id'], name='idx_micro_date_sched'),
        ]


class MicroplanRowArticleItem(models.Model):
    """`microplan[].articleItems[]` — состав изделий строки."""
    row = models.ForeignKey(MicroplanRow, on_delete=models.CASCADE, related_name='article_items')
    article_item = models.CharField(max_length=128)
    ordinal = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['ordinal']


class MicroSubOrder(models.Model):
    """`microplan[].subOrders[]` — второй и далее заказ в смене.

    Инвариант (CLAUDE.md §4.5): любой подсчёт «сколько по заказу запланировано»
    обязан учитывать и main-строки, и подзаказы других строк.
    """
    row = models.ForeignKey(MicroplanRow, on_delete=models.CASCADE, related_name='sub_orders')
    src_id = models.BigIntegerField(null=True, blank=True)
    ordinal = models.PositiveIntegerField(default=0)
    release_order = models.CharField(max_length=128, blank=True, default='')
    launch_order = models.CharField(max_length=128, blank=True, default='')
    article_items = models.JSONField(default=list, blank=True)
    plan_release = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_release_main = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_release_extra = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fact_release = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fact_launch = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    plan_release_is_manual = models.BooleanField(default=False)
    learning_eff = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']
        indexes = [models.Index(fields=['release_order'])]


class LaunchEntry(models.Model):
    """`microplan[].launches[]` — запуск в работу."""
    row = models.ForeignKey(MicroplanRow, on_delete=models.CASCADE, related_name='launches')
    src_id = models.BigIntegerField(null=True, blank=True)
    ordinal = models.PositiveIntegerField(default=0)
    order_number = models.CharField(max_length=128, blank=True, default='')
    article_item = models.CharField(max_length=128, blank=True, default='')
    qty = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']


class MicroRowWorker(models.Model):
    """`microplan[].workers[]` — состав смены в конкретной строке."""
    row = models.ForeignKey(MicroplanRow, on_delete=models.CASCADE, related_name='workers')
    src_id = models.BigIntegerField(null=True, blank=True)
    ordinal = models.PositiveIntegerField(default=0)
    name = models.CharField(max_length=255, blank=True, default='')
    staff_count = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    attendance = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                     verbose_name='явка')
    shift_time = models.PositiveIntegerField(default=480)
    eff_pct = models.DecimalField(max_digits=8, decimal_places=2, default=100)
    absence = models.BooleanField(default=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['ordinal']


class PlanBaseline(Base):
    """S.planBaseline — снимок изначального плана месяца (план на 100%)."""
    month = models.CharField(max_length=7, blank=True, default='', db_index=True)
    date = models.DateField(null=True, blank=True)
    schedule_src_id = models.BigIntegerField(null=True, blank=True)
    op_name = models.CharField(max_length=255, blank=True, default='')
    workshop = models.CharField(max_length=255, blank=True, default='')
    stage = models.CharField(max_length=8, blank=True, default='')
    release_order = models.CharField(max_length=128, blank=True, default='')
    article_items = models.JSONField(default=list, blank=True)
    plan_release = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    snap_at = models.CharField(max_length=64, blank=True, default='')


# ──────────────────────── Доступ (ТЗ §7а) ───────────────────────────────────

class UserOpAccess(models.Model):
    """Доступ пользователя к ОП.

    Ограничение по ОП — часть модели данных, а не проверка в интерфейсе: без
    фильтрации в QuerySet любой запрос в обход UI отдаст чужие данные (ТЗ §7а).
    Отсутствие записей у пользователя = доступ ко всем ОП (администратор).
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='op_access')
    op_name = models.CharField(max_length=255)
    can_edit = models.BooleanField(default=False,
                                   verbose_name='может править, а не только смотреть')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'op_name'], name='uniq_user_op'),
        ]
        verbose_name = 'доступ к ОП'
        verbose_name_plural = 'доступы к ОП'


class ImportRun(models.Model):
    """Журнал импортов: что и когда залили, чтобы разбираться постфактум."""
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=512, verbose_name='файл-источник')
    counts = models.JSONField(default=dict, blank=True,
                              verbose_name='сколько записей по коллекциям')
    ok = models.BooleanField(default=False)
    message = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-started_at']


class ChangeLog(models.Model):
    """Кто что менял. Одна запись — одно сохранение.

    Зачем: при сотне человек с разными ролями вопрос «кто поставил этот план»
    задаётся постоянно, а `version` отвечает только «сколько раз меняли».

    **Хранится РАЗНИЦА, а не копия записи.** Готовые решения (вроде
    `django-simple-history`) кладут полный снимок строки на каждое сохранение —
    это те же данные во второй раз, и на микроплане они быстро перевесят сами
    данные. Здесь лежит только то, что изменилось: `{поле: [было, стало]}`.

    Поля `entity` и `src_id` — не внешний ключ намеренно: строка может быть уже
    удалена, а запись о том, кто её удалил, обязана остаться.
    """

    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='changes')
    username = models.CharField(max_length=150, blank=True, default='',
                                verbose_name='логин на момент правки')
    entity = models.CharField(max_length=64, verbose_name='что правили (путь API)')
    src_id = models.BigIntegerField(null=True, blank=True,
                                    verbose_name='id записи в «Плане»')
    row_id = models.BigIntegerField(null=True, blank=True,
                                    verbose_name='id записи в базе')
    op_name = models.CharField(max_length=255, blank=True, default='',
                               verbose_name='площадка записи')
    action = models.CharField(max_length=16, verbose_name='create | update | delete')
    changes = models.JSONField(default=dict, blank=True,
                               verbose_name='{поле: [было, стало]}')
    note = models.CharField(max_length=255, blank=True, default='',
                            verbose_name='пояснение для массовых операций')

    class Meta:
        ordering = ['-at', '-id']
        indexes = [
            models.Index(fields=['entity', 'src_id']),
            models.Index(fields=['op_name', 'at']),
        ]
        verbose_name = 'изменение'
        verbose_name_plural = 'журнал изменений'
