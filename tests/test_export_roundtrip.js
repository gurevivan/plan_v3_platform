// Круговой рейс данных: экспорт → импорт должен давать эквивалентное состояние.
// Это критерий готовности фазы 1 миграции («round-trip JSON→БД→JSON эквивалентен исходному»),
// и здесь же он проверяется для текущего файлового обмена между компьютерами.
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

ctx.recalcAllMicroPlans = () => {};
ctx.renderActive = () => {};
ctx._alert = () => {};
ctx.save = () => {};

const EMPTY = {
  bases: [], nomenclature: [], ops: [], contracts: [], customerOrders: [], orders: [],
  macroplan: [], microplan: [], schedules: [], scheduleMonthOverrides: [], calOverrides: [],
  holidays: [], manualFrv: [], manualFrvExtraMonths: [], manualFrvPivotExtraRows: [],
  deliveryMatrix: [], planBaseline: [], tcRcOverrides: {}, tcShOverrides: {}, nextId: 1,
};

// ── Экспорт должен нести все коллекции состояния ──────────────────────────
console.log('── Состав экспорта ──');
T.S = Object.assign({}, T.S, EMPTY, {
  customerOrders: [{ id: 7, number: 'ЗК-1' }],
  orders: [{ id: 1, number: 'ПЗ-1', quantity: 100, contractId: 7 }],
  orderLinks: [{ id: 50, customerOrderId: 7, productionOrderId: 1, qty: 100 }],
  tcRcOverrides: { 'ПЗ-1': 12.5 },
  tcShOverrides: { 'ПЗ-1': 240 },
  macroEff: [{ id: 70, op: 'ОП-5', workshop: 'Ц1', month: '2026-07', eff: 85 }],
});
const dump = ctx._buildFullExportData();
check('экспорт содержит tcRcOverrides', dump.tcRcOverrides['ПЗ-1'], 12.5);
check('экспорт содержит tcShOverrides', dump.tcShOverrides['ПЗ-1'], 240);
check('экспорт содержит связи ЗК↔ПЗ', Array.isArray(dump.orderLinks), true);
check('связь не потеряна', (dump.orderLinks || []).length, 1);
check('экспорт содержит эффективность макроплана', (dump.macroEff || []).length, 1);
check('значение эффективности', ((dump.macroEff || [])[0] || {}).eff, 85);

// ── Импорт не должен тащить связи от ПРЕДЫДУЩЕГО набора данных ────────────
// Сценарий: на компьютере уже открыт один набор, пользователь грузит другой файл.
console.log('\n── Импорт чужого файла поверх текущих данных ──');
T.S.orderLinks = [{ id: 99, customerOrderId: 555, productionOrderId: 444, qty: 7 }]; // «хвост» старых данных
T.S.macroEff = [{ id: 98, op: 'ЧУЖОЙ-ОП', workshop: '', month: '2026-01', eff: 42 }];
ctx._applyFullImport(Object.assign({}, EMPTY, {
  customerOrders: [{ id: 20, number: 'ЗК-НОВЫЙ' }],
  orders: [{ id: 30, number: 'ПЗ-НОВЫЙ', quantity: 500, contractId: 20 }],
  orderLinks: [{ id: 60, customerOrderId: 20, productionOrderId: 30, qty: 500 }],
  macroEff: [{ id: 61, op: 'ОП-5', workshop: 'Ц1', month: '2026-07', eff: 90 }],
}));
const links = T.S.orderLinks || [];
check('связи взяты из файла, а не из прошлых данных', links.length, 1);
check('связь указывает на заказ клиента из файла', links[0] && links[0].customerOrderId, 20);
check('связь указывает на заказ производства из файла', links[0] && links[0].productionOrderId, 30);
check('эффективность взята из файла', ((T.S.macroEff || [])[0] || {}).op, 'ОП-5');
check('чужая эффективность не осталась', (T.S.macroEff || []).some(x => x.op === 'ЧУЖОЙ-ОП'), false);

// ── Старый файл без orderLinks: связи восстанавливаются из contractId ─────
console.log('\n── Обратная совместимость: файл без связей ──');
T.S.orderLinks = [{ id: 99, customerOrderId: 555, productionOrderId: 444, qty: 7 }];
ctx._applyFullImport(Object.assign({}, EMPTY, {
  customerOrders: [{ id: 40, number: 'ЗК-СТАРЫЙ' }],
  orders: [{ id: 41, number: 'ПЗ-СТАРЫЙ', quantity: 300, contractId: 40 }],
  // orderLinks нет — формат старого экспорта
}));
const rebuilt = T.S.orderLinks || [];
check('связь восстановлена из contractId', rebuilt.length, 1);
check('заказ клиента', rebuilt[0] && rebuilt[0].customerOrderId, 40);
check('заказ производства', rebuilt[0] && rebuilt[0].productionOrderId, 41);
check('количество', rebuilt[0] && rebuilt[0].qty, 300);

// ── Полный круг: экспорт → импорт → экспорт ───────────────────────────────
console.log('\n── Экспорт → импорт → экспорт ──');
const original = Object.assign({}, EMPTY, {
  customerOrders: [{ id: 7, number: 'ЗК-1' }],
  orders: [{ id: 1, number: 'ПЗ-1', quantity: 100, contractId: 7 }],
  orderLinks: [{ id: 50, customerOrderId: 7, productionOrderId: 1, qty: 100 }],
  tcRcOverrides: { 'ПЗ-1': 12.5 },
  tcShOverrides: { 'ПЗ-1': 240 },
});
ctx._applyFullImport(original);
const again = ctx._buildFullExportData();
check('нормы РЦ пережили круг', again.tcRcOverrides['ПЗ-1'], 12.5);
check('нормы ШЦ пережили круг', again.tcShOverrides['ПЗ-1'], 240);
check('связи пережили круг', (again.orderLinks || []).length, 1);
check('связь та же', ((again.orderLinks || [])[0] || {}).customerOrderId, 7);

finish();
