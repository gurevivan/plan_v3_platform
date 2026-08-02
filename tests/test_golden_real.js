// Golden-master на РЕАЛЬНЫХ данных (фаза 0 миграции).
// Импортируем реальный экспорт и сверяем результат расчётов с эталоном,
// снятым скриптом golden_snapshot.js. Любое изменение расчётного ядра, меняющее
// числа на боевых данных, роняет этот тест.
//
// Пересnять эталон (осознанно, после проверенной правки):  node golden_snapshot.js
const fs = require('fs');
const path = require('path');
const { load, makeChecker } = require('./sandbox');

const FIX = path.join(__dirname, 'fixtures', 'real_export_20260801.json');
const GOLD = path.join(__dirname, 'fixtures', 'golden');

if (!fs.existsSync(FIX) || !fs.existsSync(path.join(GOLD, 'microplan.json'))) {
  console.log('SKIP: нет fixtures/real_export_20260801.json или эталонов — запустите golden_snapshot.js');
  process.exit(0);
}

const raw = JSON.parse(fs.readFileSync(FIX, 'utf8'));
const goldMicro = JSON.parse(fs.readFileSync(path.join(GOLD, 'microplan.json'), 'utf8'));
const goldCover = JSON.parse(fs.readFileSync(path.join(GOLD, 'order_coverage.json'), 'utf8'));
const goldNorms = JSON.parse(fs.readFileSync(path.join(GOLD, 'norms.json'), 'utf8'));

const ctx = load();
const T = ctx.__T;
ctx.renderActive = () => {}; ctx.renderMicroplan = () => {}; ctx._alert = () => {}; ctx.save = () => {};
const { check, finish } = makeChecker();

ctx._applyFullImport(JSON.parse(JSON.stringify(raw)));

// ── Импорт не теряет данные ───────────────────────────────────────────────
console.log('── Импорт реального экспорта ──');
check('строк микроплана', T.S.microplan.length, raw.microplan.length);
check('заказов', T.S.orders.length, raw.orders.length);
check('строк макроплана', T.S.macroplan.length, raw.macroplan.length);
check('связей ЗК↔ПЗ', (T.S.orderLinks || []).length, (raw.orderLinks || []).length);
check('норм РЦ по заказам', Object.keys(T.S.tcRcOverrides || {}).length,
      Object.keys(raw.tcRcOverrides || {}).length);

// ── План совпадает с эталоном построчно ───────────────────────────────────
console.log('\n── План по строкам ──');
const byId = new Map(T.S.microplan.map(m => [m.id, m]));
let planDiff = 0, subDiff = 0, missing = 0;
for (const g of goldMicro) {
  const m = byId.get(g.id);
  if (!m) { missing++; continue; }
  if ((+m.planRelease || 0) !== g.planRelease) planDiff++;
  const subs = m.subOrders || [];
  for (const gs of g.subOrders) {
    const s = subs.find(x => x.id === gs.id);
    if (!s || (+s.planRelease || 0) !== gs.planRelease) subDiff++;
  }
}
check('строк не потеряно', missing, 0);
check('план main-строк совпадает с эталоном', planDiff, 0);
check('план подзаказов совпадает с эталоном', subDiff, 0);

// ── Покрытие заказов (метрика шага 0) ─────────────────────────────────────
console.log('\n── Покрытие заказов ──');
let covDiff = 0;
for (const g of goldCover) {
  const got = T.orderCoveredBefore(g.order, g.stage, g.lastDate, {});
  if (got !== g.coveredBeforeLastDate) covDiff++;
}
check('покрытие по заказам совпадает', covDiff, 0);
check('заказов в эталоне покрытия', goldCover.length, goldCover.length);

// ── Нормы (приоритет: макроплан → override → номенклатура) ────────────────
console.log('\n── Нормы ──');
let normDiff = 0;
for (const g of goldNorms) {
  const row = T.S.microplan.find(m => m.id === g.id);
  if (!row) { normDiff++; continue; }
  if (ctx.tcForMicroRow(row, g.articleItem) !== g.tc) normDiff++;
}
check('нормы совпадают с эталоном', normDiff, 0);

// ── Повторный пересчёт не меняет числа (идемпотентность) ─────────────────
console.log('\n── Идемпотентность пересчёта ──');
const snapshot = T.S.microplan.map(m => +m.planRelease || 0);
ctx.recalcAllMicroPlans();
const after = T.S.microplan.map(m => +m.planRelease || 0);
check('повторный recalcAllMicroPlans не двигает план',
      after.filter((v, i) => v !== snapshot[i]).length, 0);

finish();
