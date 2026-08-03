// Журнал изменений в интерфейсе: «кто менял».
//
// Сама запись истории живёт на сервере (`core/api/test_api.py` — там же проверено,
// что пишется разница, а не копия записи, и что чужую площадку через журнал не
// видно). Здесь — клиентская часть: правильные сущности вкладки, читаемый вид
// значений и то, что журнал НЕ оседает в состоянии приложения.
//
// Последнее важнее, чем кажется: положив историю в `S`, мы завели бы вторую её
// версию, которая уехала бы в экспорт и разошлась с настоящей.
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

ctx.renderActive = () => {};
ctx.renderRefs = () => {};
let alerts = [];
ctx._alert = (msg) => { alerts.push(String(msg)); };

let calls = [];
let serverOn = true;
let journal = [];
ctx.fetch = (url, opt) => {
  const method = (opt && opt.method) || 'GET';
  calls.push({ url, method });
  const data = url.indexOf('/api/changes/') === 0
    ? { results: journal, next: null }
    : { results: [], next: null };
  return Promise.resolve({ ok: true, status: 200, statusText: 'ok',
                           json: () => Promise.resolve(data) });
};
ctx.document.cookie = 'csrftoken=TOKEN123';

// Заглушка DOM в песочнице отдаёт `null` на любой querySelector, а диалог
// навешивает обработчики на свои кнопки. Подменяем только здесь: другие тесты
// опираются на прежнее поведение.
function el() {
  const node = {
    style: {}, dataset: {}, children: [], parentNode: null,
    innerHTML: '', textContent: '', value: '',
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild() {}, removeChild() {}, setAttribute() {}, removeAttribute() {},
    addEventListener() {}, removeEventListener() {}, focus() {}, blur() {}, click() {},
    querySelector: () => el(), querySelectorAll: () => [],
  };
  return node;
}
ctx.document.createElement = el;
ctx.document.body = el();
Object.defineProperty(ctx.SRV, 'on', { get: () => serverOn, set: (v) => { serverOn = v; } });

function baseState() {
  T.S = {
    contracts: [], customerOrders: [], orders: [], macroplan: [], macroEff: [],
    microplan: [],
    schedules: [], scheduleMonthOverrides: [], calOverrides: [], manualFrv: [],
    planBaseline: [], holidays: [], orderLinks: [],
    nomenclature: [], ops: [], bases: [], deliveryMatrix: [],
    tcRcOverrides: {}, tcShOverrides: {}, nextId: 100,
  };
}

(async () => {
  baseState();
  ctx.SRV.user = { username: 'иван', is_superuser: false, roles: ['Микропланирование'],
                   all_ops: true, ops_edit: [], all_sections: false,
                   sections_edit: ['microplan', 'schedule'] };
  ctx.SRV.sections = [{ key: 'microplan', title: 'Микроплан',
                        paths: ['microplan', 'plan-baseline'] }];
  ctx.activeTab = 'microplan';

  // ── Какие сущности показывать ───────────────────────────────────────────
  console.log('── Сущности вкладки ──');
  const micro = ctx.srvChangeEntities('microplan');
  check('первой идёт главная сущность вкладки', micro[0], 'microplan');
  check('в списке есть смежные', micro.includes('orders'), true);
  check('пути без хвоста', micro.every(p => p.indexOf('/') < 0), true);
  check('дублей нет', new Set(micro).size, micro.length);

  const refs = ctx.srvChangeEntities('refs');
  check('у справочников своя главная сущность', refs[0], 'nomenclature');

  // ── Как выглядят значения ───────────────────────────────────────────────
  console.log('\n── Читаемость значений ──');
  // «→ » без пояснения выглядит как сбой, а не как очистка поля.
  check('пустая строка названа словом', ctx.srvChangeVal(''), '(пусто)');
  check('null — тоже', ctx.srvChangeVal(null), '(пусто)');
  check('ноль остаётся нулём', ctx.srvChangeVal(0), '0');
  check('число как есть', ctx.srvChangeVal(42.5), '42.5');
  check('объект разворачивается', ctx.srvChangeVal({ a: 1 }), '{"a":1}');

  // ── Кнопка в полосе режима ──────────────────────────────────────────────
  console.log('\n── Кнопка «Кто менял» ──');
  check('в серверном режиме кнопка есть',
        /srvChangesDialog/.test(ctx.srvBannerHtml('microplan')), true);
  serverOn = false;
  check('в локальном режиме кнопки нет',
        /srvChangesDialog/.test(ctx.srvBannerHtml('microplan')), false);
  serverOn = true;

  // ── Журнал не оседает в состоянии ───────────────────────────────────────
  console.log('\n── История не попадает в состояние ──');
  journal = [
    { id: 1, at: '2026-08-02T10:15:00+05:00', username: 'иван', entity: 'microplan',
      src_id: 77, op_name: 'ОП-5', action: 'update',
      changes: { plan_release: [10, 42] }, note: '' },
    { id: 2, at: '2026-08-02T09:00:00+05:00', username: 'пётр', entity: 'microplan',
      src_id: null, op_name: '', action: 'recalc', changes: {},
      note: 'пересчёт (all), строк записано: 315' },
  ];
  calls = [];
  const before = JSON.stringify(T.S);
  await ctx.srvChangesDialog('microplan');
  await new Promise(r => setTimeout(r, 0));

  check('запрошен журнал', calls.some(c => /\/api\/changes\//.test(c.url)), true);
  check('фильтр по сущности вкладки ушёл',
        calls.some(c => /entity=microplan/.test(c.url)), true);
  check('состояние приложения не тронуто', JSON.stringify(T.S), before);
  check('в S журнала нет', T.S.changes === undefined, true);
  check('ошибок не показано', alerts.length, 0);

  // ── Как строка журнала выглядит ─────────────────────────────────────────
  console.log('\n── Строка журнала ──');
  const rowHtml = ctx.srvChangeRowHtml(journal[0]);
  check('видно автора', /иван/.test(rowHtml), true);
  check('видно старое и новое значение', /10/.test(rowHtml) && /42/.test(rowHtml), true);
  check('действие по-русски', /изменил/.test(rowHtml), true);
  check('видна площадка', /ОП-5/.test(rowHtml), true);

  const bulkHtml = ctx.srvChangeRowHtml(journal[1]);
  check('массовая операция объяснена словами',
        /строк записано: 315/.test(bulkHtml), true);
  check('и помечена как пересчёт', /пересчитал/.test(bulkHtml), true);

  // ── Вне серверного режима ───────────────────────────────────────────────
  console.log('\n── Вне серверного режима ──');
  serverOn = false;
  calls = []; alerts = [];
  await ctx.srvChangesDialog('microplan');
  check('запросов нет', calls.length, 0);
  check('сказано, почему', /только в серверном режиме/.test(alerts.join('\n')), true);

  // ── Диалог заливки закрывается после успеха ─────────────────────────────
  console.log('\n── Кнопка после заливки закрывает окно ──');
  ctx.SRV.user = { username: 'шеф', is_superuser: true, roles: [],
                   all_ops: true, ops_read: [], ops_edit: [],
                   all_sections: true, sections_edit: [] };
  serverOn = true;

  // Собираем окно так же, как его строит приложение, и следим за кнопками.
  const buttons = {};
  let removed = 0;
  const node = (id) => {
    const el = {
      id, style: {}, textContent: '', innerHTML: '', disabled: false,
      onclick: null, onchange: null, value: 'merge', checked: true,
      files: [], parentNode: { insertBefore() {} },
      querySelector: (sel) => node(String(sel).replace('#', '')),
      querySelectorAll: () => [],
      appendChild() {}, insertBefore() {}, setAttribute() {}, addEventListener() {},
    };
    if (id) buttons[id] = buttons[id] || el;
    return buttons[id] || el;
  };
  ctx.document.createElement = () => node('');
  ctx.document.body = { appendChild() {}, removeChild() { removed++; } };

  baseState();
  // Файл отдаём заранее — так же, как это делает кнопка «Загрузить».
  const file = { name: 'выгрузка.json', text: async () => JSON.stringify({
    ops: [{ id: 1, name: 'ОП-1' }], microplan: [] }) };

  await ctx.srvUploadDialog(file);
  const go = buttons['_upGo'];
  check('кнопка «Залить» есть', !!go, true);

  await go.onclick();
  await new Promise(r => setTimeout(r, 0));

  check('кнопка снова активна', go.disabled, false);
  check('надпись стала «Закрыть»', go.textContent, 'Закрыть');
  check('и она закрывает окно', typeof go.onclick, 'function');

  const removedBefore = removed;
  go.onclick();
  check('окно действительно закрылось', removed > removedBefore, true);

  finish();
})();
