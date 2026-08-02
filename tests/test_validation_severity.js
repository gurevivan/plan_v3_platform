// Два уровня проверок микроплана: 'stop' (физика, блокирует) и 'warn' (планирование, не блокирует).
// Правило: проверки ФАКТА — stop, проверки ПЛАНА — warn. См. блок про severity над validateStageFact.
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

ctx.recalcPlanRelease = () => {};
ctx.microValidation = true;

const ORD = 'ЗАК-100';
// Заказ на 1000; на 01.07 уже покрыто 900 → к планированию остаётся 100.
function setup() {
  T.S = Object.assign({}, T.S, {
    orders: [{ id: 1, number: ORD, quantity: 1000, articleGp: 'ГП-1' }],
    microplan: [
      { id: 1, date: '2026-07-01', op: 'ОП-5', workshop: 'Ц1', stage: 'ШЦ', scheduleId: 7,
        releaseOrder: ORD, articleItems: [], subOrders: [], planRelease: 900, factRelease: 900 },
      { id: 2, date: '2026-07-02', op: 'ОП-5', workshop: 'Ц1', stage: 'ШЦ', scheduleId: 7,
        releaseOrder: ORD, articleItems: [], subOrders: [], planRelease: 0, factRelease: 0,
        planReleaseIsManual: false },
    ],
  });
}

// ── Уровень проверок плана ────────────────────────────────────────────────
console.log('── Проверки ПЛАНА имеют уровень warn ──');
setup();
const overPlan = T.validatePlanOrderLimit(T.S.microplan[1], 250); // 250 при остатке 100
check('validatePlanOrderLimit сработала', overPlan.ok, false);
check('уровень — предупреждение', overPlan.severity, 'warn');
check('в подсказке отдан лимит', overPlan.available, 100);

// ── Уровень проверок факта ────────────────────────────────────────────────
console.log('\n── Проверки ФАКТА имеют уровень stop ──');
setup();
const overFact = ctx.validateFactVsOrderQty(T.S.microplan[1], 250, 2); // факт сверх кол-ва заказа
check('validateFactVsOrderQty сработала', overFact.ok, false);
check('уровень — стоп', overFact.severity, 'stop');

// ── Главное: план сверх остатка СОХРАНЯЕТСЯ без подтверждения ─────────────
// _confirm подменяем на «взрыв»: если ввод плана попытается заблокировать — тест упадёт.
console.log('\n── План сверх остатка сохраняется молча ──');
setup();
let confirmCalls = 0;
ctx._confirm = () => { confirmCalls++; return Promise.resolve(false); };

(async () => {
  await ctx.setMicroPlanManual(2, 250);
  check('план сохранён как введено', T.S.microplan[1].planRelease, 250);
  check('строка помечена ручной', T.S.microplan[1].planReleaseIsManual, true);
  check('модалка подтверждения НЕ показывалась', confirmCalls, 0);

  // Предупреждение при этом никуда не делось — его видно в строке.
  const afterChk = T.validatePlanOrderLimit(T.S.microplan[1], T.S.microplan[1].planRelease);
  check('предупреждение сохраняется', afterChk.ok, false);
  check('превышение = план − лимит', T.S.microplan[1].planRelease - afterChk.available, 150);

  // ── План в пределах остатка — предупреждения нет ────────────────────────
  console.log('\n── План в пределах остатка ──');
  await ctx.setMicroPlanManual(2, 80);
  check('план сохранён', T.S.microplan[1].planRelease, 80);
  check('предупреждения нет', T.validatePlanOrderLimit(T.S.microplan[1], 80).ok, true);
  check('модалка по-прежнему не звалась', confirmCalls, 0);

  finish();
})();
