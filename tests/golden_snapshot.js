// Снятие golden-эталона с РЕАЛЬНОГО экспорта (фаза 0 миграции).
//
// Что делает:
//   1. грузит фикстуру через штатный _applyFullImport (со всеми 22 миграциями);
//   2. сравнивает план, сохранённый в файле, с планом, который пересчитало ядро —
//      расхождение означает, что движок считает не то, что лежит в данных;
//   3. пишет эталонные выходы в fixtures/golden/*.json — с ними потом сверяется Python-порт.
//
// Запуск:  node golden_snapshot.js [имя_фикстуры]
const fs = require('fs');
const path = require('path');
const { load } = require('./sandbox');

const fixName = process.argv[2] || 'real_export_20260801.json';
const fixPath = path.join(__dirname, 'fixtures', fixName);
const raw = JSON.parse(fs.readFileSync(fixPath, 'utf8'));

const ctx = load();
const T = ctx.__T;
ctx.renderActive = () => {};
ctx.renderMicroplan = () => {};
ctx._alert = () => {};
ctx.save = () => {};

// План, лежащий в файле ДО пересчёта.
const before = new Map();
for (const m of raw.microplan || []) {
  before.set(m.id, {
    date: m.date, op: m.op, stage: m.stage, order: m.releaseOrder,
    plan: +m.planRelease || 0, fact: +m.factRelease || 0,
    manual: !!m.planReleaseIsManual,
    subs: (m.subOrders || []).map(s => ({ id: s.id, order: s.releaseOrder, plan: +s.planRelease || 0 })),
  });
}

console.log(`Фикстура: ${fixName}`);
console.log(`Строк микроплана: ${(raw.microplan || []).length}, заказов: ${(raw.orders || []).length}\n`);

// Импорт прогоняет миграции и recalcAllMicroPlans().
ctx._applyFullImport(JSON.parse(JSON.stringify(raw)));

// ── 1. Расхождение «сохранено ↔ пересчитано» ──────────────────────────────
let same = 0, diff = 0, manualDiff = 0;
const examples = [];
for (const m of T.S.microplan) {
  const b = before.get(m.id);
  if (!b) continue;
  const now = +m.planRelease || 0;
  if (now === b.plan) { same++; continue; }
  diff++;
  if (b.manual) manualDiff++;
  if (examples.length < 12) {
    examples.push({ id: m.id, date: b.date, op: b.op, stage: b.stage, order: b.order,
                    было: b.plan, стало: now, факт: b.fact, ручной: b.manual });
  }
}
console.log(`Совпало: ${same}   Разошлось: ${diff}` + (diff ? `   (из них ручных строк: ${manualDiff})` : ''));
if (examples.length) {
  console.log('\nПримеры расхождений:');
  console.table(examples);
}

// ── 2. Эталонные выходы ───────────────────────────────────────────────────
const goldenDir = path.join(__dirname, 'fixtures', 'golden');
fs.mkdirSync(goldenDir, { recursive: true });

const microplan = T.S.microplan
  .slice()
  .sort((a, b) => String(a.date).localeCompare(String(b.date)) || (+a.id - +b.id))
  .map(m => ({
    id: m.id, date: m.date, op: m.op, workshop: m.workshop, stage: m.stage,
    scheduleId: m.scheduleId, releaseOrder: m.releaseOrder,
    articleItems: m.articleItems || [], planRelease: +m.planRelease || 0,
    factRelease: +m.factRelease || 0, planReleaseIsManual: !!m.planReleaseIsManual,
    learningEff: m.learningEff ?? 100,
    subOrders: (m.subOrders || []).map(s => ({
      id: s.id, releaseOrder: s.releaseOrder, planRelease: +s.planRelease || 0,
      factRelease: +s.factRelease || 0,
    })),
  }));

// Покрытие и остаток по каждому заказу+переделу — то, что чинили в шаге 0.
const coverage = [];
const seen = new Set();
for (const m of T.S.microplan) {
  const key = `${m.releaseOrder}|${m.stage}`;
  if (!m.releaseOrder || m.releaseOrder === '__extra__' || seen.has(key)) continue;
  seen.add(key);
  const ord = T.S.orders.find(o => ctx.releaseOrderEq(o.number, m.releaseOrder));
  if (!ord) continue;
  const last = T.S.microplan
    .filter(x => ctx.releaseOrderEq(x.releaseOrder, m.releaseOrder) && x.stage === m.stage && x.date)
    .map(x => x.date).sort().pop();
  coverage.push({
    order: m.releaseOrder, stage: m.stage, quantity: +ord.quantity || 0,
    coveredBeforeLastDate: T.orderCoveredBefore(m.releaseOrder, m.stage, last, {}),
    lastDate: last,
  });
}
coverage.sort((a, b) => a.order.localeCompare(b.order) || a.stage.localeCompare(b.stage));

// Нормы по строкам — ловят регрессии в приоритете норм (макро → override → номенклатура).
const norms = microplan
  .filter(m => m.releaseOrder && m.releaseOrder !== '__extra__' && m.articleItems.length)
  .map(m => ({ id: m.id, order: m.releaseOrder, stage: m.stage, articleItem: m.articleItems[0],
               tc: ctx.tcForMicroRow(T.S.microplan.find(x => x.id === m.id), m.articleItems[0]) }))
  .filter((v, i, a) => a.findIndex(x => x.order === v.order && x.stage === v.stage && x.articleItem === v.articleItem) === i);

const write = (name, data) => {
  const p = path.join(goldenDir, name);
  fs.writeFileSync(p, JSON.stringify(data, null, 2), 'utf8');
  console.log(`  ${name}: ${Array.isArray(data) ? data.length + ' записей' : 'ок'}`);
};
console.log('\nЭталоны записаны в fixtures/golden/:');
write('microplan.json', microplan);
write('order_coverage.json', coverage);
write('norms.json', norms);

console.log(`\nИтог: план сходится на ${same} строках из ${same + diff}.`);
