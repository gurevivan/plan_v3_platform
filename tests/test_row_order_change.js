// При ИСПРАВЛЕНИИ заказа в заполненной строке микроплана должны меняться изделие и норма.
// Регрессия на пункт «При исправлении заказа в строке не меняются изделие и норма» (help.html §6).
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

// Побочный пересчёт плана требует графиков/состава — для этой проверки не нужен.
ctx.recalcPlanRelease = () => {};

T.S = Object.assign({}, T.S, {
  nomenclature: [
    { id: 1, articleGp: 'ГП-КУРТКА', name: 'Куртка', routeOverrides: [],
      route: [{ stage: 'РЦ', articleItem: '01.100/1', name: 'Крой куртки', timeCost: 40 },
              { stage: 'ШЦ', articleItem: '01.100/2', name: 'Пошив куртки', timeCost: 120 }] },
    { id: 2, articleGp: 'ГП-БРЮКИ', name: 'Брюки', routeOverrides: [],
      route: [{ stage: 'РЦ', articleItem: '02.200/1', name: 'Крой брюк', timeCost: 25 },
              { stage: 'ШЦ', articleItem: '02.200/2', name: 'Пошив брюк', timeCost: 60 }] },
  ],
  orders: [
    { id: 1, number: 'ЗАК-001', articleGp: 'ГП-КУРТКА', quantity: 500, contractId: 10 },
    { id: 2, number: 'ЗАК-002', articleGp: 'ГП-БРЮКИ', quantity: 700, contractId: 20 },
    { id: 3, number: 'ЗАК-003', articleGp: 'ГП-КУРТКА', quantity: 300, contractId: 30 },
  ],
  microplan: [
    { id: 1, date: '2026-07-10', op: 'ОП-5', workshop: 'Ц1', stage: 'ШЦ', scheduleId: 7,
      releaseOrder: 'ЗАК-001', articleItem: '01.100/2', articleItems: ['01.100/2'],
      contractId: 10, planRelease: 100, factRelease: 0, subOrders: [], launches: [] },
  ],
});

const row = () => T.S.microplan[0];
const norm = () => ctx.tcForMicroRow(row(), row().articleItem);

console.log('── Исходное состояние ──');
check('изделие', row().articleItem, '01.100/2');
check('норма ШЦ', norm(), 120);

console.log('\n── Меняем заказ на ЗАК-002 (другой ГП) ──');
ctx.updM(1, 'releaseOrder', 'ЗАК-002');
check('изделие пересчитано', row().articleItem, '02.200/2');
check('состав изделий строки', row().articleItems, ['02.200/2']);
check('норма пересчитана', norm(), 60);
check('контракт от нового заказа', row().contractId, 20);

console.log('\n── Возврат на ЗАК-001 ──');
ctx.updM(1, 'releaseOrder', 'ЗАК-001');
check('изделие вернулось', row().articleItem, '01.100/2');
check('норма вернулась', norm(), 120);

console.log('\n── Заказ с тем же ГП: выбор пользователя сохраняем ──');
ctx.updM(1, 'releaseOrder', 'ЗАК-003');
check('изделие не сброшено', row().articleItem, '01.100/2');
check('контракт всё равно обновлён', row().contractId, 30);

console.log('\n── Сброс заказа очищает строку ──');
ctx.updM(1, 'releaseOrder', '');
check('изделие очищено', row().articleItem, '');
check('план обнулён', row().planRelease, 0);

finish();
