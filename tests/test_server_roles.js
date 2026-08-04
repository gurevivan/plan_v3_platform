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
    contracts: [], customerOrders: [], orders: [], macroplan: [], macroEff: [],
    microplan: [],
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


  // ── Площадки в полосе режима и при сохранении пользователя ──────────────
  console.log('\n── Видно, какие площадки доступны ──');
  asUser([], { username: 'мастер', all_ops: false,
               ops_read: ['Бухара'], ops_edit: ['Бухара'] });
  const banner = ctx.srvBannerHtml('microplan');
  // Раньше выводились только площадки на ПРАВКУ, и человек с доступом к одной
  // площадке не понимал, почему «пропали» данные — а они просто не его.
  check('названа доступная площадка', /Бухара/.test(banner), true);
  check('не сказано «все площадки»', /все площадки/.test(banner), false);

  asUser([], { all_ops: false, ops_read: [], ops_edit: [] });
  check('пустой доступ назван прямо',
        /площадок не выдано/.test(ctx.srvBannerHtml('microplan')), true);

  asUser([], { is_superuser: true, all_sections: true, all_ops: true });
  check('администратору — все площадки',
        /все площадки/.test(ctx.srvBannerHtml('microplan')), true);

  console.log('\n── Доступы не стираются из-за незагруженного справочника ──');
  baseState(); reset();
  asUser([], { is_superuser: true, all_sections: true });
  T.S.ops = [];                              // справочник ОП не загружен
  // После сохранения экран перечитывается, поэтому заглушка должна выдерживать
  // и перерисовку: querySelector там ищет свои кнопки.
  const fakeBox = (opsSel) => ({
    querySelectorAll: (sel) => (opsSel && /data-op/.test(sel)) ? opsSel : [],
    querySelector: (sel) => (/data-admin/.test(sel) || /data-active/.test(sel))
      ? { checked: true }
      : { onclick: null, onchange: null, value: '', files: [] },
    _close: () => {},
    innerHTML: '',
  });
  ctx._srvUsersBox = fakeBox(null);
  await ctx.srvUserSave(7);
  await flush();
  const sent = byMethod('PATCH').find(c => /\/api\/users\//.test(c.url));
  check('запрос ушёл', !!sent, true);
  // Отправить «ни одной отмеченной» здесь значило бы стереть человеку все
  // площадки — молча и не тем действием, которое затевалось.
  check('площадки не отправлены', sent && sent.body.ops === undefined, true);
  check('роли отправлены', sent && Array.isArray(sent.body.roles), true);
  check('пользователю сказали', /площадки оставлены без изменений/.test(alerts.join('\n')), true);

  reset();
  T.S.ops = [{ id: 1, name: 'Бухара', workshop: 'Швейный цех' }];
  ctx._srvUsersBox = fakeBox([{ value: 'edit', getAttribute: () => 'Бухара' }]);
  await ctx.srvUserSave(7);
  await flush();
  const sent2 = byMethod('PATCH').find(c => /\/api\/users\//.test(c.url));
  check('со справочником площадки уходят',
        sent2 && sent2.body.ops && sent2.body.ops[0].op_name, 'Бухара');
  check('право правки передано', sent2 && sent2.body.ops[0].can_edit, true);

  // ── Видно, что лежит в базе ─────────────────────────────────────────────
  console.log('\n── Полоса показывает состав базы ──');
  baseState(); reset();
  asUser([], { is_superuser: true, all_sections: true });
  T.S.nomenclature = [{ id: 1 }, { id: 2 }];
  T.S.ops = [{ id: 1 }];
  ctx.activeTab = 'refs';
  const refsBanner = ctx.srvBannerHtml('refs');
  // Без этой подписи процесс невидим: человек грузит файл в браузер, видит
  // справочники, жмёт «Обновить с сервера» — и они пропадают, потому что в базе
  // их никогда не было.
  check('видно, сколько записей в базе', /в базе:/.test(refsBanner), true);
  check('названа номенклатура', /номенклатура 2/.test(refsBanner), true);
  check('и ОП', /ОП 1/.test(refsBanner), true);

  baseState();
  check('пустая база названа нулями', /номенклатура 0/.test(ctx.srvBannerHtml('refs')), true);

  // ── Включение режима предупреждает о замещении ──────────────────────────
  console.log('\n── Локальные данные не исчезают молча ──');
  baseState(); reset();
  T.S.nomenclature = [{ id: 1 }, { id: 2 }];
  T.S.ops = [{ id: 1 }];
  check('локальные записи посчитаны', ctx.srvLocalRecordCount(), 3);

  serverOn = false;
  let asked = [];
  const savedConfirm = ctx._confirm;
  ctx._confirm = (msg) => { asked.push(String(msg)); return Promise.resolve(false); };
  await ctx.toggleServerMode(true);
  check('спросили перед замещением', asked.length, 1);
  check('в вопросе названо количество', /3 записей/.test(asked[0] || ''), true);
  check('сказано, как перенести в базу', /Залить файл/.test(asked[0] || ''), true);
  check('отказ оставил режим выключенным', serverOn, false);
  check('локальные данные на месте', T.S.nomenclature.length, 2);

  // Пустой браузер — терять нечего, спрашивать не о чем.
  baseState(); asked = [];
  serverOn = false;
  await ctx.toggleServerMode(true);
  check('на пустом состоянии не спрашиваем', asked.length, 0);
  ctx._confirm = savedConfirm;
  serverOn = true;

  // ── Кнопка «Загрузить» не кладёт файл мимо базы ─────────────────────────
  console.log('\n── Файл в серверном режиме идёт в базу, а не в браузер ──');
  baseState(); reset();
  serverOn = true;
  asUser([], { is_superuser: true, all_sections: true });

  // Раньше кнопка «Загрузить» звала `_applyFileData` напрямую, минуя проверку
  // серверного режима: файл ложился в браузер, база оставалась прежней, и первое
  // же «Обновить с сервера» его стирало. Выглядело как потеря данных.
  let opened = null;
  const savedDialog = ctx.srvUploadDialog;
  ctx.srvUploadDialog = (f) => { opened = f; };
  // Подменяем чтение файла целиком: в песочнице нет FileReader, а нам важно
  // одно — дошло ли дело до применения файла к браузеру.
  let applied = 0;
  const savedLoad = ctx._loadJsonFile;
  ctx._loadJsonFile = () => { applied++; };

  await ctx._loadFileInput({ name: 'выгрузка.json' });
  check('файл не применён к браузеру', applied, 0);
  check('открыт диалог заливки в базу', opened && opened.name, 'выгрузка.json');

  // Не администратору заливать нечем — ему объясняют, а не молчат. И называют
  // учётную запись: если человек считает себя администратором, расхождение видно
  // сразу, без догадок.
  opened = null; alerts = [];
  asUser([], { is_superuser: false, username: 'пётр', roles: ['Микропланирование'] });
  await ctx._loadFileInput({ name: 'выгрузка.json' });
  check('обычному пользователю диалог не открыт', opened, null);
  check('файл всё равно не применён', applied, 0);
  check('названа учётная запись', /пётр/.test(alerts.join('\n')), true);
  check('названы роли', /Микропланирование/.test(alerts.join('\n')), true);
  check('сказано, что это не администратор',
        /не администратор/.test(alerts.join('\n')), true);

  // Пользователь ещё не определён (страница не успела спросить «кто я») —
  // отказывать нельзя, сначала спрашиваем.
  opened = null; alerts = [];
  ctx.SRV.user = null;
  let askedWho = 0;
  const savedWhoami = ctx.SRV.whoami;
  ctx.SRV.whoami = async () => { askedWho++; ctx.SRV.user = { username: 'шеф', is_superuser: true }; return ctx.SRV.user; };
  await ctx._loadFileInput({ name: 'выгрузка.json' });
  check('спросили «кто я» перед отказом', askedWho, 1);
  check('администратору открыли заливку', opened && opened.name, 'выгрузка.json');
  ctx.SRV.whoami = savedWhoami;

  // Вне серверного режима файл грузится локально, как раньше.
  serverOn = false;
  await ctx._loadFileInput({ name: 'выгрузка.json' });
  check('локально файл применяется', applied, 1);
  serverOn = true;

  ctx.srvUploadDialog = savedDialog;
  ctx._loadJsonFile = savedLoad;

  // ── Одна копия данных: в серверном режиме браузер не пишем ──────────────
  console.log('\n── Состояние не дублируется в браузер ──');
  baseState(); reset();
  asUser([], { is_superuser: true, all_sections: true });

  // Следим за localStorage: в серверном режиме туда не должно уходить ничего.
  let written = [];
  ctx.localStorage.setItem = (k, v) => { written.push({ k, len: String(v).length }); };
  ctx.localStorage.getItem = () => null;

  serverOn = true;
  T.S.nomenclature = [{ id: 1, name: 'Куртка' }];
  ctx.saveReal();          // песочница глушит save(); здесь нужна настоящая
  // Локальная копия — «тень»: при следующем открытии она показалась бы первой,
  // а при недоступном сервере так и осталась бы, с виду рабочая.
  check('в серверном режиме localStorage не трогаем', written.length, 0);

  serverOn = false;
  ctx.saveReal();
  check('вне режима сохраняем как раньше', written.length > 0, true);

  // ── Незакрытые правки видно ─────────────────────────────────────────────
  console.log('\n── Неотправленные правки распознаются ──');
  baseState(); ctx.SRV.mirror = {}; reset();
  serverOn = true;
  asUser(['contracts']);
  seed('contracts', [contract()]);
  check('всё отправлено — терять нечего', ctx.srvHasUnsent(), false);

  T.S.contracts[0].quantity = 12345;
  check('правка есть — есть что терять', ctx.srvHasUnsent(), true);

  await ctx.srvSync();
  await flush();          // синхронизация могла встать в очередь за предыдущей
  check('после отправки снова чисто', ctx.srvHasUnsent(), false);

  serverOn = false;
  check('вне серверного режима вопрос не стоит', ctx.srvHasUnsent(), false);
  serverOn = true;

  finish();
})();
