// Единая метрика покрытия заказа (orderCoveredBefore).
// Регрессия на баг «план 0 при живом остатке ≤N (ЗАК)» — BUG_план0_при_остатке_ЗАК.md.
//
// Правило: прошлое считаем по ФАКТУ (смена отработана), будущее — по ПЛАНУ.
// Подсказка «≤N (ЗАК)» и ядро авто-плана обязаны давать ОДНО И ТО ЖЕ число.
const { load, makeChecker } = require('./sandbox');

const T = load().__T;
const { check, finish } = makeChecker();
const ORD = 'ЗАК-0001';

function setup(rows, qty = 1000) {
  T.S = Object.assign({}, T.S, {
    orders: [{ id: 1, number: ORD, quantity: qty, articleGp: '01.000001' }],
    microplan: rows.map((r, i) => Object.assign({
      id: i + 1, op: 'ОП-5', workshop: 'Ц1', stage: 'ШЦ', scheduleId: 7,
      releaseOrder: ORD, articleItems: [], subOrders: [], planReleaseIsManual: false,
    }, r)),
  });
}

// ── Кейс 1: перевыполнение ────────────────────────────────────────────────
// Заказ 1000. День 1: план 300, факт 400. День 2: план 300, факт 300.
// Физически покрыто 700 → к планированию остаётся 300.
// Было: подсказка считала по чистому плану (600 → «доступно 400»), ядро по max(факт,план)
// (700 → 300). Расхождение и давало «план 0, а подсказка показывает остаток».
console.log('── Кейс 1: перевыполнение ──');
setup([
  { date: '2026-07-01', planRelease: 300, factRelease: 400 },
  { date: '2026-07-02', planRelease: 300, factRelease: 300 },
  { date: '2026-07-03', planRelease: 0, factRelease: 0 },
]);
const covered1 = T.orderCoveredBefore(ORD, 'ШЦ', '2026-07-03', { excludeRowId: 3 });
check('покрыто до 03.07', covered1, 700);
check('подсказка «доступно к планированию»', T.validatePlanOrderLimit(T.S.microplan[2], 0).available, 300);
check('подсказка = кол-во − покрытие', T.validatePlanOrderLimit(T.S.microplan[2], 0).available, 1000 - covered1);

// ── Кейс 2: недовыполнение ────────────────────────────────────────────────
// День 1: план 300, факт 200. День 2: план 300, факт 300. Покрыто 500 → остаётся 500.
// Было: max(факт,план) засчитывал невыполненный план как сделанный (600 → 400),
// и 100 штук хвоста молча исчезали из графика.
console.log('\n── Кейс 2: недовыполнение (хвост заказа сохраняется) ──');
setup([
  { date: '2026-07-01', planRelease: 300, factRelease: 200 },
  { date: '2026-07-02', planRelease: 300, factRelease: 300 },
  { date: '2026-07-03', planRelease: 0, factRelease: 0 },
]);
check('покрыто до 03.07', T.orderCoveredBefore(ORD, 'ШЦ', '2026-07-03', { excludeRowId: 3 }), 500);
check('подсказка «доступно к планированию»', T.validatePlanOrderLimit(T.S.microplan[2], 0).available, 500);

// ── Кейс 3: факта ещё нет — считаем по плану ──────────────────────────────
console.log('\n── Кейс 3: будущее считается по плану ──');
setup([
  { date: '2026-07-01', planRelease: 300, factRelease: 0 },
  { date: '2026-07-02', planRelease: 300, factRelease: 0 },
  { date: '2026-07-03', planRelease: 0, factRelease: 0 },
]);
check('покрыто до 03.07', T.orderCoveredBefore(ORD, 'ШЦ', '2026-07-03', { excludeRowId: 3 }), 600);

// ── Кейс 4: подзаказы входят в покрытие (инвариант CLAUDE.md §4.5) ────────
console.log('\n── Кейс 4: подзаказы учитываются ──');
setup([
  { date: '2026-07-01', planRelease: 200, factRelease: 250,
    subOrders: [{ id: 91, releaseOrder: ORD, planRelease: 100, factRelease: 150 }] },
  { date: '2026-07-03', planRelease: 0, factRelease: 0 },
]);
check('покрыто до 03.07 (main 250 + подзаказ 150)',
      T.orderCoveredBefore(ORD, 'ШЦ', '2026-07-03', { excludeRowId: 2 }), 400);

// ── Кейс 5: заказ закрыт по факту — остаток не уходит в минус ─────────────
console.log('\n── Кейс 5: заказ перевыполнен по факту ──');
setup([
  { date: '2026-07-01', planRelease: 900, factRelease: 1100 },
  { date: '2026-07-03', planRelease: 0, factRelease: 0 },
]);
check('доступно к планированию', T.validatePlanOrderLimit(T.S.microplan[1], 0).available, 0);

// ── Кейс 6: покрытие считается по ПЕРЕДЕЛУ строки ─────────────────────────
// Тот же заказ на РЦ не должен влиять на остаток по ШЦ.
console.log('\n── Кейс 6: переделы не смешиваются ──');
setup([
  { date: '2026-07-01', stage: 'РЦ', planRelease: 500, factRelease: 500 },
  { date: '2026-07-02', stage: 'ШЦ', planRelease: 200, factRelease: 200 },
  { date: '2026-07-03', stage: 'ШЦ', planRelease: 0, factRelease: 0 },
]);
check('покрыто по ШЦ (РЦ не считается)',
      T.orderCoveredBefore(ORD, 'ШЦ', '2026-07-03', { excludeRowId: 3 }), 200);

finish();
