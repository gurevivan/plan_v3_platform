// Снятие эталонов для ПОРТА расчётного ядра в Python (фаза 2).
//
// golden_snapshot.js снимает результат целиком (план по строкам). Здесь — выходы
// отдельных функций, чтобы порт можно было сверять по группам, а не «всё сразу»:
// когда сойдётся план, но разойдётся норма, разбираться будет негде.
//
// Запуск:  node golden_calc.js [фикстура]
// Пишет:   fixtures/golden/calc_*.json
const fs = require('fs');
const path = require('path');
const { load } = require('./sandbox');

const fixName = process.argv[2] || 'real_export_20260801.json';
const raw = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', fixName), 'utf8'));

const ctx = load();
const T = ctx.__T;
ctx.renderActive = () => {}; ctx.renderMicroplan = () => {}; ctx._alert = () => {}; ctx.save = () => {};
ctx._applyFullImport(JSON.parse(JSON.stringify(raw)));
const S = T.S;

// Состояние ПОСЛЕ импорта. Импорт прогоняет миграции и recalcAllMicroPlans(),
// поэтому авто-строки могут отличаться от того, что лежит в файле. Паритет обязан
// сверяться на одинаковом входе, иначе Python сравнивают с результатом чужого
// пересчёта. На боевой фикстуре разницы нет (пересчёт воспроизводит сохранённое),
// на синтетике — есть.

const goldDir = path.join(__dirname, 'fixtures', 'golden');
fs.mkdirSync(goldDir, { recursive: true });
const prefix = fixName.startsWith('real_') ? '' : fixName.replace(/\.json$/, '') + '__';
const write = (name, data) => {
  fs.writeFileSync(path.join(goldDir, prefix + name), JSON.stringify(data, null, 2), 'utf8');
  console.log(`  ${prefix + name}: ${data.length} записей`);
};

fs.mkdirSync(goldDir, { recursive: true });
fs.writeFileSync(path.join(goldDir, (fixName.startsWith('real_') ? '' : fixName.replace(/\.json$/, '') + '__') + 'state.json'),
                 JSON.stringify(ctx._buildFullExportData()), 'utf8');

const opNames = [...new Set(S.ops.map(o => o.name).filter(Boolean))];
const stages = ['РЦ', 'АЦ', 'ШЦ', 'У'];

// ── Маршруты ──────────────────────────────────────────────────────────────
const routes = [];
for (const n of S.nomenclature) {
  routes.push({
    articleGp: n.articleGp, op: null,
    route: ctx.allRoutesOf(n).map(i => ({ stage: i.stage, articleItem: i.articleItem, timeCost: +i.timeCost || 0 })),
  });
  for (const op of opNames) {
    routes.push({
      articleGp: n.articleGp, op,
      route: ctx.routeForOp(n, op).map(i => ({ stage: i.stage, articleItem: i.articleItem, timeCost: +i.timeCost || 0 })),
    });
  }
}
write('calc_routes.json', routes);

// ── Переделы маршрута и предыдущий передел ────────────────────────────────
const prevStages = [];
for (const n of S.nomenclature) {
  for (const op of [null, ...opNames]) {
    const set = [...ctx.routeStagesForGp(n.articleGp, op)].sort();
    for (const st of stages) {
      prevStages.push({
        articleGp: n.articleGp, op, stage: st,
        stages: set,
        prev: ctx.prevStageInRoute(n.articleGp, st, op),
      });
    }
  }
}
write('calc_prev_stage.json', prevStages);

// ── Нормы: stageTimeCostFor и timeCostOf по всем встречающимся артикулам ──
const items = new Set();
for (const n of S.nomenclature) for (const i of ctx.allRoutesOf(n)) if (i.articleItem) items.add(i.articleItem);
for (const m of S.microplan) {
  for (const ai of (m.articleItems || [])) items.add(ai);
  if (m.articleItem) items.add(m.articleItem);
}
const stageNorms = [];
for (const ai of [...items].sort()) {
  for (const op of [null, ...opNames]) {
    for (const st of stages) {
      stageNorms.push({ articleItem: ai, stage: st, op, tc: ctx.stageTimeCostFor(ai, st, op) });
    }
    stageNorms.push({ articleItem: ai, stage: null, op, tcAny: ctx.timeCostOf(ai, op) });
  }
}
write('calc_stage_norms.json', stageNorms);

// ── Норма строки микроплана и подзаказов (главный приоритет норм) ─────────
const rowNorms = [];
for (const m of S.microplan) {
  const ais = ctx.microRowArticleItems(m);
  rowNorms.push({
    id: m.id, kind: 'row', order: m.releaseOrder || '', stage: m.stage || 'ШЦ', op: m.op || '',
    perArticle: ais.map(ai => ({ articleItem: ai, tc: ctx.tcForMicroRow(m, ai) })),
  });
  for (const sub of (m.subOrders || [])) {
    rowNorms.push({
      id: sub.id, kind: 'sub', rowId: m.id, order: sub.releaseOrder || '',
      stage: m.stage || 'ШЦ', op: m.op || '',
      tcSum: ctx.tcForSubOrder(sub, m.op, m.stage || 'ШЦ'),
    });
  }
}
write('calc_row_norms.json', rowNorms);

// ── Покрытие заказа: все метрики и окна, какие использует ядро ────────────
const cov = [];
const seen = new Set();
for (const m of S.microplan) {
  const ro = m.releaseOrder;
  if (!ro || ro === '__extra__') continue;
  const stage = m.stage || 'ШЦ';
  const key = ro + '|' + stage + '|' + (m.date || '');
  if (seen.has(key)) continue;
  seen.add(key);
  const opts = { excludeRowId: m.id };
  cov.push({
    order: ro, stage, date: m.date || '', excludeRowId: m.id,
    coveredBefore: T.orderCoveredBefore(ro, stage, m.date, opts),
    planBefore: T.orderStagePlannedQty(ro, stage, m.date, { ...opts, metric: 'plan', when: '<' }),
    factOrPlanBefore: T.orderStagePlannedQty(ro, stage, m.date, { ...opts, metric: 'factOrPlan', when: '<' }),
    maxFactPlanBefore: T.orderStagePlannedQty(ro, stage, m.date, { ...opts, metric: 'maxFactPlan', when: '<' }),
    planSameDay: T.orderStagePlannedQty(ro, stage, m.date, { ...opts, metric: 'plan', when: '==', skipManualMain: true }),
    allPlan: T.orderStagePlannedQty(ro, stage, m.date, { metric: 'plan', when: 'all' }),
  });
}
write('calc_coverage.json', cov);

// ── Остаток к планированию (подсказка «≤N (ЗАК)») ─────────────────────────
const avail = [];
for (const m of S.microplan) {
  if (!m.releaseOrder || m.releaseOrder === '__extra__') continue;
  const chk = T.validatePlanOrderLimit(m, +m.planRelease || 0);
  avail.push({ id: m.id, order: m.releaseOrder, stage: m.stage || 'ШЦ', date: m.date || '',
               available: chk.available ?? null, ok: chk.ok !== false });
}
write('calc_order_limit.json', avail);

// ── Группа 1: даты и доставки ─────────────────────────────────────────────
const dateProbes = [];
const sampleDates = [...new Set(S.microplan.map(m => m.date).filter(Boolean))].sort().slice(0, 40);
for (const d of sampleDates) {
  for (const n of [-7, -3, -1, 0, 1, 5, 30]) {
    dateProbes.push({ fn: 'addCalDays', date: d, n, out: ctx.addCalDays(d, n) });
  }
  dateProbes.push({ fn: 'prevWorkday', date: d, out: ctx.prevWorkday(d) });
}
write('calc_dates.json', dateProbes);

const opNamesAll = [...new Set([...S.ops.map(o => o.name), ...S.microplan.map(m => m.op)])].filter(Boolean);
const deliv = [];
for (const a of opNamesAll) for (const b of opNamesAll) {
  deliv.push({ fromOp: a, toOp: b, days: ctx.deliveryDaysFor(a, b) });
}
write('calc_delivery_days.json', deliv);

// ── Группа 4: мощность бригады-дня ────────────────────────────────────────
const cap = [];
for (const m of S.microplan) {
  const grp = ctx.brigadeDayGroupRows(m);
  cap.push({
    id: m.id, date: m.date || '', op: m.op || '', workshop: m.workshop || '',
    stage: m.stage || 'ШЦ', scheduleId: m.scheduleId ?? null,
    groupIds: grp.map(g => g.id).sort((a, b) => a - b),
    availMin: ctx.workersAvailableMinSum(ctx.microRowWorkers(m), m.op, m.workshop),
    effPct: ctx.microRowEffPct(ctx.microRowWorkers(m), m.op, m.workshop),
    poolRaw: ctx.brigadeDayPoolRawMin(grp),
    usedByOthers: ctx.brigadeDayUsedByOthersRawMin(m, grp),
    shiftDur: ctx.shiftDurationOf(m.op, m.workshop),
    defEff: ctx.defaultEffPct(m.op, m.workshop),
  });
}
write('calc_capacity.json', cap);


// ── Группа 5: авто-план ───────────────────────────────────────────────────
// Эталон снимается ПОСЛЕ полного пересчёта: state.json несёт тот же вход,
// поэтому Python считает от идентичного состояния.
ctx.recalcAllMicroPlans();
const plan = S.slice ? [] : [];
const planRows = S.microplan.map(m => ({
  id: m.id, date: m.date || '', op: m.op || '', stage: m.stage || 'ШЦ',
  order: m.releaseOrder || '', manual: !!m.planReleaseIsManual,
  planRelease: +m.planRelease || 0, planLaunch: +m.planLaunch || 0,
  planReleaseMain: +m.planReleaseMain || 0, planReleaseExtra: +m.planReleaseExtra || 0,
  planByArticle: m.planByArticle || {},
  subs: (m.subOrders || []).map(s => ({ id: s.id, order: s.releaseOrder || '',
                                        planRelease: +s.planRelease || 0,
                                        manual: !!s.planReleaseIsManual })),
}));
write('calc_autoplan.json', planRows);

// Состояние ПОСЛЕ пересчёта — вход для сверки авто-плана.
fs.writeFileSync(path.join(goldDir, prefix + 'state_after_recalc.json'),
                 JSON.stringify(ctx._buildFullExportData()), 'utf8');


// ── Группа 7: валидации ───────────────────────────────────────────────────
// Пробуем несколько значений на каждой строке: как есть, ноль, чуть больше
// лимита и заведомо много — чтобы поймать и границу, и явное нарушение.
const val = [];
for (const m of S.microplan) {
  const curFact = +m.factRelease || 0;
  const curPlan = +m.planRelease || 0;
  for (const probe of [curFact, 0, curFact + 1, curFact + 1000]) {
    const a = ctx.validateStageFact(m, probe, m.id);
    const b = ctx.validateFactVsOrderQty(m, probe, m.id);
    const c = ctx.validateReleaseVsLaunches(m, probe);
    val.push({ id: m.id, probe, kind: 'fact',
      stageFact: { ok: a.ok !== false, severity: a.severity ?? null, available: a.available ?? null, limit: a.limit ?? null, msg: a.msg ?? '' },
      factVsQty: { ok: b.ok !== false, severity: b.severity ?? null, msg: b.msg ?? '' },
      relVsLaunch: { ok: c.ok !== false, severity: c.severity ?? null, available: c.available ?? null, msg: c.msg ?? '' } });
  }
  for (const probe of [curPlan, 0, curPlan + 1, curPlan + 100000]) {
    const d = ctx.validatePlanOrderLimit(m, probe);
    val.push({ id: m.id, probe, kind: 'plan',
      planLimit: { ok: d.ok !== false, severity: d.severity ?? null, available: d.available ?? null, msg: d.msg ?? '' } });
  }
}
write('calc_validation.json', val);

// Лимиты передела отдельно — они питают проверку факта.
const limits = [];
for (const m of S.microplan) {
  if (!m.releaseOrder || m.releaseOrder === '__extra__') continue;
  const ord = S.orders.find(o => ctx.releaseOrderEq(o.number, m.releaseOrder));
  if (!ord) continue;
  const ais = ctx.microRowArticleItems(m);
  const info = ctx.stageFactLimit(m.releaseOrder, ord.articleGp, m.stage || 'ШЦ',
                                  ais[0] || m.articleItem || '', m.id, m.op);
  limits.push({ id: m.id, order: m.releaseOrder, stage: m.stage || 'ШЦ',
                info: info ? { limit: info.limit, prevStage: info.prevStage, detail: info.detail } : null });
}
write('calc_stage_fact_limit.json', limits);


// ── Группа 7, продолжение: запуски, план vs предыдущий передел, дата ──────
const val2 = [];
for (const m of S.microplan) {
  const curPlan = +m.planRelease || 0;
  for (const probe of [curPlan, 0, curPlan + 1, curPlan + 100000]) {
    const d = ctx.validatePlanVsPrevStagePlan(m, probe);
    val2.push({ id: m.id, probe, kind: 'planPrev',
      res: { ok: d.ok !== false, severity: d.severity ?? null, available: d.available ?? null,
             prevStage: d.prevStage ?? null, kindOf: d.kind ?? null,
             need: d.need ?? null, have: d.have ?? null, short: d.short ?? null,
             msg: d.msg ?? '' } });
  }
  const dc = ctx.checkDeliveryDate(m);
  val2.push({ id: m.id, kind: 'delivery',
    res: { ok: dc.ok !== false, minDate: dc.minDate ?? null, prevOp: dc.prevOp ?? null,
           days: dc.days ?? null, prevStage: dc.prevStage ?? null } });
}
write('calc_validation2.json', val2);

// Запуски: пробуем разные количества по каждой существующей записи запуска
// и по каждой паре (заказ, передел) микроплана.
const lv = [];
for (const m of S.microplan) {
  const stage = m.stage || 'ШЦ';
  const ais = ctx.microRowArticleItems(m);
  const ai = ais[0] || m.articleItem || '';
  for (const probe of [0, 1, 25, 100000]) {
    const r = ctx.validateStageLaunch(m.releaseOrder, ai, stage, m.date, probe, null, m.op);
    lv.push({ rowId: m.id, order: m.releaseOrder || '', articleItem: ai, stage,
              date: m.date || '', probe, entryId: null,
              res: { ok: r.ok !== false, severity: r.severity ?? null,
                     available: r.available ?? null, limit: r.limit ?? null,
                     prevStage: r.prevStage ?? null, msg: r.msg ?? '' } });
  }
  for (const le of (m.launches || [])) {
    for (const probe of [+le.qty || 0, (+le.qty || 0) + 1, 100000]) {
      const r = ctx.validateStageLaunch(le.order, le.articleItem, stage, m.date, probe, le.id, m.op);
      lv.push({ rowId: m.id, order: le.order || '', articleItem: le.articleItem || '',
                stage, date: m.date || '', probe, entryId: le.id,
                res: { ok: r.ok !== false, severity: r.severity ?? null,
                       available: r.available ?? null, limit: r.limit ?? null,
                       prevStage: r.prevStage ?? null, msg: r.msg ?? '' } });
    }
  }
}
write('calc_launch_validation.json', lv);


// ── Группа 8: аналитика ───────────────────────────────────────────────────
// Агрегаты снимаем напрямую из функций-примитивов: renderAnalytics строит HTML,
// и сверять вёрстку бессмысленно — во фронте она и останется.
const anaPrim = [];
const gpSet = new Set(S.nomenclature.map(n => n.articleGp).filter(Boolean));
for (const r of S.macroplan) {
  anaPrim.push({ kind: 'macroRow', id: r.id,
                 kitQty: ctx.macroRowKitQuantity(r),
                 laborMin: ctx.macroRowLaborMinutes(r),
                 stage: ctx.stageOfMacroRow(r) });
}
const aiAll = new Set();
for (const n of S.nomenclature) for (const i of ctx.allRoutesOf(n)) if (i.articleItem) aiAll.add(i.articleItem);
for (const m of S.microplan) for (const ai of ctx.microRowArticleItems(m)) aiAll.add(ai);
for (const ai of [...aiAll].sort()) {
  anaPrim.push({ kind: 'gpOf', articleItem: ai, gp: ctx.articleGpForMacroArticleItem(ai) });
}
// Комплектность: по всем строкам макроплана каждого месяца.
for (const month of [...new Set(S.macroplan.map(r => r.month).filter(Boolean))].sort()) {
  const rows = S.macroplan.filter(r => r.month === month);
  anaPrim.push({ kind: 'kitQty', month, qty: ctx.stageKitQtyForRows(rows, gpSet) });
  for (const st of ['РЦ','АЦ','ШЦ','У']) {
    const at = rows.filter(r => ctx.stageOfMacroRow(r) === st);
    anaPrim.push({ kind: 'kitQtyStage', month, stage: st,
                   qty: ctx.stageKitQtyForRows(at, gpSet),
                   laborMin: Math.round(at.reduce((s, r) => s + ctx.macroRowLaborMinutes(r), 0)) });
  }
}
write('calc_analytics.json', anaPrim);

console.log('\nЭталоны для порта сняты.');
