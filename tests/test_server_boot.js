// Что показывается СРАЗУ при открытии страницы.
//
// Код инициализации читает localStorage один раз при загрузке, поэтому проверить
// его можно только загрузив харнесс с заранее подготовленным хранилищем.
//
// Главное свойство: в серверном режиме локальный снимок на экран НЕ попадает.
// Иначе первые секунды видно вчерашнее состояние, а при недоступном сервере оно
// таким и остаётся — с виду рабочее.
const { load, makeChecker } = require('./sandbox');
const { check, finish } = makeChecker();

const LS_KEY = 'prod-plan-v6';
const localData = JSON.stringify({
  nextId: 50,
  ops: [{ id: 1, name: 'Из браузера', workshop: 'ШЦ' }],
  nomenclature: [{ id: 1, articleGp: '01.000001', name: 'Куртка' }],
  contracts: [], customerOrders: [], orders: [], macroplan: [], microplan: [],
  schedules: [], calOverrides: [], holidays: [], manualFrv: [], bases: [],
  deliveryMatrix: [], planBaseline: [], tcRcOverrides: {}, tcShOverrides: {},
});

// ── Серверный режим включён ────────────────────────────────────────────────
const srv = load({ storage: { [LS_KEY]: localData, 'plan-server-mode': '1' } });
check('серверный режим распознан', srv.SRV.on, true);
check('локальные ОП на экран не попали', srv.__T.S.ops.length, 0);
check('и номенклатура тоже', srv.__T.S.nomenclature.length, 0);
check('состояние пригодно для рендера', Array.isArray(srv.__T.S.microplan), true);
// Данные в браузере остаются нетронутыми: к ним возвращаются, когда режим выключат.
check('локальная копия не стёрта',
      JSON.parse(srv.localStorage.getItem(LS_KEY)).ops.length, 1);

// ── Серверный режим выключен ───────────────────────────────────────────────
const local = load({ storage: { [LS_KEY]: localData, 'plan-server-mode': '0' } });
check('вне режима данные берутся из браузера', local.__T.S.ops.length, 1);
check('и это именно они', local.__T.S.ops[0].name, 'Из браузера');

finish();
