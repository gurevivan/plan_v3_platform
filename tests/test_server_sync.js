// Синхронизация вкладок планирования по разнице (фаза 4, все вкладки).
//
// Сеть подменяется: проверяем поведение приложения, а не ответы сервера.
// Настоящий сервер и потери формата проверяются `live_api_check.js` — он гоняет
// боевую выгрузку через API туда и обратно и сверяет с исходным файлом.
//
// Здесь — то, чего живая проверка не покажет: что синхронизация НЕ делает.
// Не удаляет по недогруженному списку, не шлёт лишнего, не работает при
// выключённом режиме, не запускает вторую пачку поверх первой.
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

ctx.renderActive = () => {};
ctx.renderRefs = () => {};
let alerts = [];
ctx._alert = (msg) => { alerts.push(String(msg)); };
let confirmAnswer = true;
let confirms = [];
ctx._confirm = (msg) => { confirms.push(String(msg)); return Promise.resolve(confirmAnswer); };

// ── Подмена сети ────────────────────────────────────────────────────────────
let calls = [];
let nextFail = null;          // {url: подстрока, status} — уронить подходящие запросы
let serverOn = true;
ctx.fetch = (url, opt) => {
  const method = (opt && opt.method) || 'GET';
  const body = opt && opt.body ? JSON.parse(opt.body) : null;
  calls.push({ url, method, body });
  if (nextFail && url.indexOf(nextFail.url) >= 0) {
    return Promise.resolve({ ok: false, status: nextFail.status, statusText: 'test',
                             json: () => Promise.resolve({ detail: 'сбой' }) });
  }
  // POST возвращает созданную запись: клиенту нужны её id и версия.
  const data = method === 'POST' ? { id: 9001, version: 1, ...(body || {}) }
             : method === 'PATCH' ? { id: 1, version: 2, ...(body || {}) }
             : { results: [], next: null };
  return Promise.resolve({ ok: true, status: method === 'DELETE' ? 204 : 200,
                           statusText: 'ok', json: () => Promise.resolve(data) });
};
ctx.document.cookie = 'csrftoken=TOKEN123';
Object.defineProperty(ctx.SRV, 'on', { get: () => serverOn, set: (v) => { serverOn = v; } });

// `reset` чистит только наблюдения; слепки живут дальше — иначе следующий шаг
// сценария сравнивать было бы не с чем.
const reset = () => { calls = []; alerts = []; confirms = []; nextFail = null;
                      confirmAnswer = true; };
const resetAll = () => { reset(); ctx.SRV.mirror = {}; };
const byMethod = (m) => calls.filter(c => c.method === m);
const urls = (m) => byMethod(m).map(c => c.url);

// ── Опоры ───────────────────────────────────────────────────────────────────
function baseState() {
  T.S = {
    contracts: [], orders: [], macroplan: [], macroEff: [], microplan: [],
    schedules: [], scheduleMonthOverrides: [], calOverrides: [], manualFrv: [],
    planBaseline: [], holidays: [], orderLinks: [],
    nomenclature: [], ops: [], bases: [], deliveryMatrix: [],
    tcRcOverrides: {}, tcShOverrides: {}, nextId: 100,
  };
}

/** Положить коллекцию и объявить, что сервер видел ровно это. */
function seed(kind, rows) {
  const e = ctx.SRV_ENTITIES[kind];
  T.S[e.coll] = rows;
  ctx.srvRemember(kind);
}

const contract = (over = {}) => Object.assign({
  id: 14, srvId: 140, srvVersion: 3, number: 'К-01', articleGp: '01.000002',
  name: 'Костюм', quantity: 17331, deliveryDate: '', deliverySchedule: [],
  deadlines: [], articleItems: [],
}, over);

(async () => {
  // ── Разница: что отправляется ───────────────────────────────────────────
  console.log('── Отправляется только изменившееся ──');
  resetAll(); baseState();
  seed('contracts', [contract(), contract({ id: 15, srvId: 150, number: 'К-02' })]);
  await ctx.srvSync();
  check('без правок запросов нет', calls.length, 0);

  T.S.contracts[0].quantity = 20000;
  await ctx.srvSync();
  check('изменилась одна запись — один PATCH', byMethod('PATCH').length, 1);
  check('адрес по серверному id', urls('PATCH')[0], '/api/contracts/140/');
  check('ушло новое количество', byMethod('PATCH')[0].body.quantity, 20000);
  check('версия отправлена', byMethod('PATCH')[0].body.version, 3);

  reset();
  T.S.contracts.push(contract({ id: 16, srvId: undefined, number: 'НОВЫЙ' }));
  await ctx.srvSync();
  check('новая запись создаётся', byMethod('POST').length, 1);
  check('локальный id уехал как src_id', byMethod('POST')[0].body.src_id, 16);
  check('серверный id принят из ответа', T.S.contracts[2].srvId, 9001);

  reset();
  T.S.contracts.splice(2, 1);
  await ctx.srvSync();
  check('пропавшая запись удаляется', urls('DELETE'), ['/api/contracts/9001/']);

  // ── Слепка нет — синхронизация молчит ───────────────────────────────────
  console.log('\n── Без слепка ничего не удаляется ──');
  resetAll(); baseState();
  T.S.contracts = [];                     // слепок не снимали: сервер «не видел»
  await ctx.srvSync();
  check('пустая коллекция без слепка не трогает сервер', calls.length, 0);

  // Загрузка не удалась → слепок обязан быть забыт, иначе следующая
  // синхронизация сочтёт недогруженный список полным и всё сотрёт.
  resetAll(); baseState();
  seed('contracts', [contract(), contract({ id: 15, srvId: 150 })]);
  nextFail = { url: 'contracts/', status: 500 };
  const ok = await ctx.srvLoad('contracts');
  check('сбой загрузки виден вызывающему', ok, false);
  check('слепок забыт', ctx.SRV.mirror.contracts === undefined, true);
  nextFail = null; calls = [];
  T.S.contracts = [];
  await ctx.srvSync();
  check('после сбоя загрузки удалений нет', byMethod('DELETE').length, 0);

  // ── Массовое удаление спрашивает ────────────────────────────────────────
  console.log('\n── Массовое удаление требует подтверждения ──');
  resetAll(); baseState();
  const many = Array.from({ length: 20 }, (_, i) =>
    contract({ id: 100 + i, srvId: 200 + i }));
  seed('contracts', many);
  T.S.contracts = many.slice(0, 2);       // убрали 18 из 20
  confirmAnswer = false;
  await ctx.srvSync();
  check('спросили перед удалением', confirms.length, 1);
  check('отказ — ничего не удалено', byMethod('DELETE').length, 0);

  resetAll(); seed('contracts', many); T.S.contracts = many.slice(0, 2);
  confirmAnswer = true;
  await ctx.srvSync();
  check('согласие — удаления ушли', byMethod('DELETE').length, 18);

  resetAll(); seed('contracts', many); T.S.contracts = many.slice(0, 17);
  await ctx.srvSync();
  check('мелкое удаление не спрашивает', confirms.length, 0);
  check('и всё же выполняется', byMethod('DELETE').length, 3);

  // ── Режим выключен — сети нет ───────────────────────────────────────────
  console.log('\n── Выключённый режим не ходит в сеть ──');
  resetAll(); baseState();
  seed('contracts', [contract()]);
  T.S.contracts[0].quantity = 1;
  serverOn = false;
  await ctx.srvSync();
  check('при выключенном режиме запросов нет', calls.length, 0);
  serverOn = true;

  // ── Отсутствие поля ≠ значение по умолчанию ─────────────────────────────
  console.log('\n── «Поля не было» переживает круг ──');
  resetAll(); baseState();
  const e = ctx.SRV_ENTITIES.schedules;
  const srvSched = {
    id: 91, src_id: 91, version: 1, name: 'Смена 1', op_name: 'БТ-5',
    workshop: 'Цех упаковки', shift_time: 720, active_months: ['2026-06'],
    graph_preset: '2/2', work_days: 2, rest_days: 2, cycle_start: '2026-06-02',
    skip_weekends: false, staff_count: 2, eff_pct: 100,
    workers: [{ id: 1, src_id: 1, name: 'Смена 1', staff_count: 2, shift_time: 480,
                eff_pct: 100, absence: true, absent_fields: ['shiftTime'],
                extra_fields: {} }],
  };
  T.S.ops = [{ id: 1, name: 'БТ-5', workshop: 'Цех упаковки', shiftDuration: 720 }];
  const local = ctx.srvToLocal(e, srvSched);
  check('смена работника выброшена как отсутствующая',
        local.workers[0].shiftTime === undefined, true);
  check('но численность осталась', local.workers[0].staffCount, 2);
  // Ради этого всё и затевалось: без поля берётся смена ОП (720), а не
  // подставленные сервером 480 — иначе мощность дня уехала бы на треть.
  check('мощность считается по смене ОП (720)',
        ctx.workersAvailableMinSum(local.workers, local.op, local.workshop),
        720 * 2 * 1 * 0.95);
  local.workers[0].shiftTime = 480;
  check('а с явной сменой 480 — по ней',
        ctx.workersAvailableMinSum(local.workers, local.op, local.workshop),
        480 * 2 * 1 * 0.95);
  delete local.workers[0].shiftTime;
  const back = ctx.srvToSrv(e, local);
  check('отсутствие поля отправлено обратно',
        back.workers[0].absent_fields, ['shiftTime']);

  // ── Несмоделированные поля не теряются ──────────────────────────────────
  console.log('\n── Состав дня переживает круг ──');
  const ce = ctx.SRV_ENTITIES.calOverrides;
  const dayWorkers = [{ id: 1176, name: 'Бригада 1 У', staffCount: 3 }];
  const ovLocal = ctx.srvToLocal(ce, {
    id: 5, version: 1, schedule_src_id: 103, date: '2026-08-03',
    day_type: 'vacation', staff_count: null,
    extra_fields: { workers: dayWorkers },
  });
  check('состав дня вернулся в запись', ovLocal.workers, dayWorkers);
  check('и уезжает обратно', ctx.srvToSrv(ce, ovLocal).extra_fields.workers, dayWorkers);

  // ── Нормы по заказу: два словаря ↔ одна таблица ─────────────────────────
  console.log('\n── Нормы по заказу ──');
  resetAll(); baseState();
  ctx.SRV.tcIds = { 'ЗАК-0011|РЦ': 11 };
  T.S.tcRcOverrides = { 'ЗАК-0011': 1.35 };
  ctx.SRV.mirror.__tc = ctx.srvTcFlat();
  T.S.tcRcOverrides['ЗАК-0011'] = 1.5;          // правка
  T.S.tcShOverrides['ЗАК-0012'] = 2.0;          // новая
  await ctx.srvSync();
  check('изменённая норма — PATCH', urls('PATCH'), ['/api/time-costs/11/']);
  check('новая норма — POST', byMethod('POST')[0].body,
        { order_number: 'ЗАК-0012', stage: 'ШЦ', time_cost: 2 });

  reset();
  delete T.S.tcRcOverrides['ЗАК-0011'];
  await ctx.srvSync();
  check('снятая норма удаляется', urls('DELETE'), ['/api/time-costs/11/']);

  // ── Ошибки собираются в одно сообщение ──────────────────────────────────
  console.log('\n── Пачка ошибок — одно сообщение ──');
  resetAll(); baseState();
  const rows = Array.from({ length: 12 }, (_, i) => contract({ id: 300 + i, srvId: 400 + i }));
  seed('contracts', rows);
  rows.forEach(r => { r.quantity = 1; });
  nextFail = { url: 'contracts/', status: 500 };
  await ctx.srvSync();
  check('сообщение одно, а не по штуке на запись', alerts.length, 1);
  check('в нём сказано, сколько ещё', /ещё 7/.test(alerts[0]), true);

  // ── Выгрузка не тащит служебные поля ────────────────────────────────────
  console.log('\n── Выгрузка чистая ──');
  baseState();
  T.S.contracts = [contract()];
  T.S.microplan = [{ id: 1, srvId: 5, srvVersion: 2, date: '2026-06-01',
                     workers: [{ id: 2, srvId: 9, staffCount: 3 }] }];
  const dump = ctx._buildFullExportData();
  check('srvId убран на верхнем уровне', 'srvId' in dump.contracts[0], false);
  check('srvVersion убран во вложенных', 'srvId' in dump.microplan[0].workers[0], false);
  check('данные на месте', dump.contracts[0].number, 'К-01');

  // ── Загрузка файла в серверном режиме запрещена ─────────────────────────
  console.log('\n── Файл поверх сервера не грузится ──');
  reset();
  serverOn = true;
  ctx.importAllData({ name: 'x.json' });
  check('импорт отклонён с объяснением', /серверный режим/i.test(alerts[0] || ''), true);
  serverOn = false;

  // ── Набор вкладок ───────────────────────────────────────────────────────
  console.log('\n── Все видимые вкладки переведены ──');
  const tabs = ['refs', 'contracts', 'manualFrv', 'macroplan', 'orders',
                'schedule', 'microplan', 'analytics'];
  check('у каждой вкладки объявлен набор данных',
        tabs.filter(t => !ctx.SRV_TAB_KINDS[t]), []);
  const known = new Set(Object.keys(ctx.SRV_ENTITIES).concat(['timeCosts']));
  const unknown = [];
  for (const t of tabs) for (const k of ctx.SRV_TAB_KINDS[t]) if (!known.has(k)) unknown.push(k);
  check('в наборах нет несуществующих сущностей', unknown, []);
  check('порядок загрузки покрывает все сущности',
        Object.keys(ctx.SRV_ENTITIES).filter(k => ctx.SRV_LOAD_ORDER.indexOf(k) < 0), []);

  finish();
})().catch(err => { console.error(err); process.exit(1); });
