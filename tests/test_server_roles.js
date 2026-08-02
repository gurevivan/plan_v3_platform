// Роли в интерфейсе: что человек может править.
//
// Настоящий запрет живёт на сервере (`core/api/test_api.py`) — здесь проверяется
// поведение клиента: не отправлять заведомо запрещённое и, главное, НЕ ОСТАВЛЯТЬ
// правку на экране. Молча показанная, но не сохранённая строка хуже отказа:
// человек уходит с уверенностью, что план записан.
//
// Сеть подменена. Каталог разделов клиент получает с сервера и своей копии
// деления не держит — это тоже проверяется.
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

ctx.renderActive = () => {};
ctx.renderRefs = () => {};
let alerts = [];
ctx._alert = (msg) => { alerts.push(String(msg)); };
ctx._confirm = () => Promise.resolve(true);

// Форму «+ Добавить» подменяем полем: `addBase` читает название из DOM.
let formValue = '';
ctx.document.getElementById = (id) => (id === 'basf-name'
  ? { get value() { return formValue; } } : null);

// ── Подмена сети ────────────────────────────────────────────────────────────
let calls = [];
let serverOn = true;
ctx.fetch = (url, opt) => {
  const method = (opt && opt.method) || 'GET';
  const body = opt && opt.body ? JSON.parse(opt.body) : null;
  calls.push({ url, method, body });
  const data = method === 'POST' ? { id: 9001, version: 1, ...(body || {}) }
             : method === 'PATCH' ? { id: 1, version: 2, ...(body || {}) }
             : { results: [], next: null };
  return Promise.resolve({ ok: true, status: method === 'DELETE' ? 204 : 200,
                           statusText: 'ok', json: () => Promise.resolve(data) });
};
ctx.document.cookie = 'csrftoken=TOKEN123';
Object.defineProperty(ctx.SRV, 'on', { get: () => serverOn, set: (v) => { serverOn = v; } });

// Каталог разделов — ровно в том виде, в каком его отдаёт `/api/roles`.
const SECTIONS = [
  { key: 'refs', title: 'Справочники',
    paths: ['nomenclature', 'ops', 'bases', 'delivery-matrix', 'holidays'] },
  { key: 'contracts', title: 'Контракты и заказы',
    paths: ['contracts', 'orders', 'order-links'] },
  { key: 'macroplan', title: 'Макроплан',
    paths: ['macroplan', 'macro-eff', 'time-costs'] },
  { key: 'schedule', title: 'Графики бригад и ФРВ',
    paths: ['schedules', 'schedule-month-overrides', 'cal-overrides', 'manual-frv'] },
  { key: 'microplan', title: 'Микроплан', paths: ['microplan', 'plan-baseline'] },
];

function asUser(sections, over = {}) {
  ctx.SRV.sections = SECTIONS;
  ctx.SRV.user = Object.assign({
    username: 'мастер', is_superuser: false, roles: [],
    all_ops: true, ops_read: [], ops_edit: [],
    all_sections: false, sections_edit: sections,
  }, over);
}

function baseState() {
  T.S = {
    contracts: [], orders: [], macroplan: [], macroEff: [], microplan: [],
    schedules: [], scheduleMonthOverrides: [], calOverrides: [], manualFrv: [],
    planBaseline: [], holidays: [], orderLinks: [],
    nomenclature: [], ops: [], bases: [], deliveryMatrix: [],
    tcRcOverrides: {}, tcShOverrides: {}, nextId: 100,
  };
}

function seed(kind, rows) {
  const e = ctx.SRV_ENTITIES[kind];
  T.S[e.coll] = rows;
  ctx.srvRemember(kind);
}

const reset = () => { calls = []; alerts = []; };
// Обработчики вкладки зовут `srvUpdate` без `await` (они висят на onblur), поэтому
// после вызова нужно дать очереди дойти до конца — иначе проверяем середину.
const flush = () => new Promise(r => setTimeout(r, 0));
const byMethod = (m) => calls.filter(c => c.method === m);

const contract = (over = {}) => Object.assign({
  id: 14, srvId: 140, srvVersion: 3, number: 'К-01', articleGp: '01.000002',
  name: 'Костюм', quantity: 17331, deliveryDate: '', deliverySchedule: [],
  deadlines: [], articleItems: [],
}, over);

(async () => {
  // ── Раздел выводится из адреса, а не из своего списка ────────────────────
  console.log('── Раздел определяется по каталогу сервера ──');
  ctx.SRV.sections = SECTIONS;
  check('микроплан', ctx.srvSectionOfKind('microplan'), 'microplan');
  check('нормы по заказам — это макроплан', ctx.srvSectionOfUrl('time-costs/'), 'macroplan');
  check('ФРВ отнесён к графикам', ctx.srvSectionOfKind('manualFrv'), 'schedule');
  check('базы — справочники', ctx.srvSectionOfKind('bases'), 'refs');

  // Каталога нет — раздел неизвестен. Глушить интерфейс из-за неотвеченного
  // запроса нельзя: последнее слово всё равно за сервером.
  ctx.SRV.sections = null;
  check('без каталога раздел неизвестен', ctx.srvSectionOfKind('microplan'), null);
  asUser([]);
  ctx.SRV.sections = null;
  check('неизвестный раздел не запрещаем', ctx.srvCanEdit(null), true);

  // ── Кто что правит ──────────────────────────────────────────────────────
  console.log('\n── Право на раздел ──');
  asUser(['microplan', 'schedule']);
  check('свой раздел открыт', ctx.srvCanEdit('microplan'), true);
  check('чужой закрыт', ctx.srvCanEdit('macroplan'), false);

  asUser([], { is_superuser: true, all_sections: true });
  check('администратору открыто всё', ctx.srvCanEdit('macroplan'), true);

  asUser([]);
  check('без ролей не правит ничего', ctx.srvCanEdit('microplan'), false);

  serverOn = false;
  check('вне серверного режима правит кто угодно', ctx.srvCanEdit('macroplan'), true);
  serverOn = true;

  // Неудачный `me` не должен запирать работу: отказ придёт с сервера, а он о
  // правах знает точно. Иначе один сбойный запрос парализовал бы смену.
  const savedUser = ctx.SRV.user;
  ctx.SRV.user = null;
  check('без ответа «кто я» правку не запрещаем', ctx.srvCanEdit('macroplan'), true);
  ctx.SRV.user = savedUser;

  // ── Синхронизация не отправляет чужой раздел ────────────────────────────
  console.log('\n── Правка чужого раздела не уходит на сервер ──');
  baseState(); ctx.SRV.mirror = {}; reset();
  asUser(['microplan']);
  seed('contracts', [contract()]);
  T.S.contracts[0].quantity = 20000;
  await ctx.srvSync();
  check('PATCH не отправлен', byMethod('PATCH').length, 0);
  check('пользователю сказали, а не промолчали', alerts.length, 1);
  check('в сообщении назван раздел',
        /Контракты и заказы/.test(alerts[0] || ''), true);

  // Экран обязан вернуться к данным сервера: иначе на нём осталась бы правка,
  // которой в базе нет, и человек ушёл бы с уверенностью, что сохранил.
  check('коллекция перечитана с сервера', byMethod('GET').some(c => /contracts/.test(c.url)), true);
  check('неотправленная правка убрана с экрана', T.S.contracts.length, 0);

  reset();
  asUser(['contracts']);
  seed('contracts', [contract()]);
  T.S.contracts[0].quantity = 33000;
  await ctx.srvSync();
  check('после выдачи права правка уходит', byMethod('PATCH').length, 1);
  check('ушло верное количество', byMethod('PATCH')[0].body.quantity, 33000);

  // ── Свой раздел не задет ────────────────────────────────────────────────
  console.log('\n── Свой раздел работает как раньше ──');
  baseState(); ctx.SRV.mirror = {}; reset();
  asUser(['microplan']);
  seed('microplan', [{
    id: 1, srvId: 11, srvVersion: 1, date: '2026-08-03', op: 'ОП-5',
    workshop: 'Швейный цех', stage: 'ШЦ', releaseOrder: 'ЗАК-1',
    planRelease: 10, factRelease: 0, articleItems: [], subOrders: [],
    launchEntries: [], workers: [],
  }]);
  T.S.microplan[0].planRelease = 25;
  await ctx.srvSync();
  check('микроплан уходит', byMethod('PATCH').length, 1);
  check('без лишних сообщений', alerts.length, 0);

  // ── Точечные обработчики справочников ───────────────────────────────────
  console.log('\n── Справочники: обработчики вкладки ──');
  baseState(); ctx.SRV.mirror = {}; reset();
  asUser(['microplan']);
  T.S.bases = [{ id: 5, srvId: 50, srvVersion: 1, name: 'Ташкент' }];
  ctx.srvRemember('bases');

  await ctx.updBase(5, 'name', 'Бухара');
  await flush();
  check('правка справочника не отправлена', byMethod('PATCH').length, 0);
  check('отказ показан', /Справочники/.test(alerts.join('\n')), true);
  // Точечные обработчики правят `S` ДО обращения к серверу, поэтому откат тут
  // так же обязателен: иначе на экране осталась бы «Бухара», которой в базе нет.
  check('справочник перечитан с сервера',
        byMethod('GET').some(c => /bases/.test(c.url)), true);
  check('локальная правка не осталась', (T.S.bases || []).length, 0);

  reset();
  T.S.bases = [{ id: 5, srvId: 50, srvVersion: 1, name: 'Ташкент' }];
  ctx.srvRemember('bases');
  await ctx.delBase(5);
  await flush();
  check('удаление не отправлено', byMethod('DELETE').length, 0);

  reset();
  formValue = 'Самарканд';
  await ctx.addBase();
  await flush();
  check('создание не отправлено', byMethod('POST').length, 0);

  reset();
  asUser(['refs']);
  await ctx.addBase();
  await flush();
  check('со своей ролью создание уходит', byMethod('POST').length, 1);

  // ── Что показывает полоса режима ────────────────────────────────────────
  console.log('\n── Полоса режима называет роль и запрет ──');
  asUser(['microplan'], { roles: ['Микропланирование'], username: 'иван' });
  ctx.activeTab = 'macroplan';
  const locked = ctx.srvBannerHtml('macroplan');
  check('видно, что раздел только для просмотра', /Только просмотр/.test(locked), true);
  check('названа роль пользователя', /Микропланирование/.test(locked), true);
  check('кнопки «Пользователи» у не-админа нет', /srvUsersDialog/.test(locked), false);

  const own = ctx.srvBannerHtml('microplan');
  check('на своей вкладке запрета нет', /Только просмотр/.test(own), false);

  asUser([], { is_superuser: true, all_sections: true, username: 'root' });
  const adminBanner = ctx.srvBannerHtml('macroplan');
  check('администратор видит кнопку «Пользователи»',
        /srvUsersDialog/.test(adminBanner), true);
  check('и не видит запрета', /Только просмотр/.test(adminBanner), false);

  // Аналитика ничего не правит — запрет на ней бессмыслен и не показывается.
  asUser([]);
  check('на аналитике запрета нет',
        /Только просмотр/.test(ctx.srvBannerHtml('analytics')), false);

  // ── Соответствие вкладок разделам ───────────────────────────────────────
  console.log('\n── Каждая правящая вкладка отнесена к разделу ──');
  const known = new Set(SECTIONS.map(s => s.key));
  const tabs = ctx.SRV_TAB_SECTION || globalThis.SRV_TAB_SECTION;
  for (const tab of Object.keys(ctx.SRV_TAB_KINDS)) {
    const sec = tabs[tab];
    check(`вкладка «${tab}» → раздел`, sec === null || known.has(sec), true);
  }

  finish();
})();
