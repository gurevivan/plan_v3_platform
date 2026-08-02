// Живая проверка серверного режима на НАСТОЯЩЕМ сервере и настоящих данных.
//
// Что доказывает (и чего не докажет тест с подменённой сетью):
//   1. `toLocal` восстанавливает состояние приложения из API без потерь —
//      выгрузка, собранная из загруженного с сервера S, совпадает с исходным файлом;
//   2. `toSrv` доносит это состояние обратно — после того как КАЖДАЯ запись
//      отправлена на сервер через синхронизацию, выгрузка сервера по-прежнему
//      совпадает с исходным файлом.
//
// Второй шаг важнее первого: именно там ловятся поля, которые интерфейс читает,
// но не отправляет обратно. Такая потеря молчалива — на экране всё правильно,
// а в базе поле обнулилось.
//
// Запуск (сервер должен быть поднят, база — залита той же фикстурой):
//   node live_api_check.js [http://127.0.0.1:8011] [логин] [пароль]
const fs = require('fs');
const path = require('path');
const { load } = require('./sandbox');

const BASE = process.argv[2] || 'http://127.0.0.1:8011';
const USER = process.argv[3] || 'tester';
const PASS = process.argv[4] || 'tester12345';
const OUT = process.env.OUT_DIR || '/tmp';

// ── Клиент с куками: сессия Django и CSRF ───────────────────────────────────
const jar = new Map();
function cookieHeader() {
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join('; ');
}
function absorb(resp) {
  const raw = resp.headers.getSetCookie ? resp.headers.getSetCookie() : [];
  for (const line of raw) {
    const [pair] = line.split(';');
    const i = pair.indexOf('=');
    if (i > 0) jar.set(pair.slice(0, i).trim(), pair.slice(i + 1).trim());
  }
}

async function rawFetch(url, opt = {}) {
  const headers = Object.assign({}, opt.headers, { Cookie: cookieHeader() });
  if (jar.has('csrftoken')) headers['X-CSRFToken'] = jar.get('csrftoken');
  headers['Referer'] = BASE;
  const resp = await fetch(BASE + url, Object.assign({}, opt, { headers, redirect: 'manual' }));
  absorb(resp);
  return resp;
}

async function login() {
  await rawFetch('/api/me');                       // получить csrftoken
  const r = await rawFetch('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USER, password: PASS }),
  });
  if (!r.ok) throw new Error('вход не удался: ' + r.status + ' ' + (await r.text()));
  return r.json();
}

// ── Песочница приложения с сетью, ведущей на настоящий сервер ───────────────
function makeCtx() {
  const ctx = load();
  let modeOn = false;
  ctx.localStorage = {
    getItem: k => (k === 'plan-server-mode' ? (modeOn ? '1' : '0') : null),
    setItem: (k, v) => { if (k === 'plan-server-mode') modeOn = v === '1'; },
    removeItem() {}, clear() {},
  };
  ctx.document.cookie = '';                        // CSRF берём из своего jar
  ctx.fetch = (url, opt) => rawFetch(url.startsWith('/') ? url : '/' + url, opt);
  ctx._alert = msg => { console.log('  [alert] ' + String(msg)); };
  ctx._confirm = async () => true;
  ctx.renderActive = () => {};
  ctx.save = () => {};
  return ctx;
}

function deepStripUndef(v) { return JSON.parse(JSON.stringify(v)); }

(async () => {
  const fixture = process.env.FIXTURE ||
    path.join(__dirname, 'fixtures', 'real_export_20260801.json');
  if (!fs.existsSync(fixture)) {
    console.log('нет фикстуры ' + fixture + ' — пропускаю');
    process.exit(0);
  }

  const me = await login();
  console.log(`вход выполнен: ${me.username}${me.is_superuser ? ' (администратор)' : ''}`);

  const ctx = makeCtx();
  ctx.SRV.on = true;

  // ── Шаг 1: собрать состояние приложения из API ────────────────────────────
  console.log('\n1. Загрузка всех вкладок с сервера');
  for (const kind of ctx.SRV_LOAD_ORDER) {
    const ok = await ctx.srvLoad(kind);
    const coll = kind === 'timeCosts' ? 'tcRcOverrides'
      : ctx.SRV_ENTITIES[kind].coll;
    const val = ctx.__T.S[coll];
    const n = Array.isArray(val) ? val.length : Object.keys(val || {}).length;
    console.log(`   ${ok ? '✓' : '✗'} ${kind.padEnd(24)} ${n}`);
    if (!ok) process.exit(1);
  }

  const fromApi = path.join(OUT, 'live_from_api.json');
  fs.writeFileSync(fromApi, JSON.stringify(deepStripUndef(ctx._buildFullExportData()),
                                           null, 1), 'utf8');
  console.log(`   выгрузка из загруженного состояния: ${fromApi}`);

  // ── Шаг 2: отправить КАЖДУЮ запись обратно ────────────────────────────────
  // Слепок заполняем заведомо непохожим телом, чтобы разница увидела все записи
  // как изменённые. Так проверяется весь путь `toSrv`, а не только те поля,
  // которые случайно тронуты.
  console.log('\n2. Отправка всех записей обратно через синхронизацию');
  for (const kind of ctx.SRV_LOAD_ORDER) {
    if (kind === 'timeCosts') continue;
    const e = ctx.SRV_ENTITIES[kind];
    if (!e.sync) continue;
    const snap = {};
    for (const o of (ctx.__T.S[e.coll] || [])) {
      snap[String(e.key ? e.key(o) : o.id)] = { body: '<принудительно>', srvId: o.srvId };
    }
    ctx.SRV.mirror[e.coll] = snap;
  }
  ctx.SRV.mirror.__tc = {};                        // нормы тоже переотправить
  const t0 = Date.now();
  await ctx.srvSync();

  // Справочники синхронизацией не ходят — там перевод сделан перехватом
  // обработчиков. Прогоняем их тем же путём, каким ходит вкладка: `srvUpdate`.
  // Иначе маршрут карточки (а с ним норма) в проверке не участвовал бы вовсе.
  for (const kind of ['nomenclature', 'ops', 'bases', 'deliveryMatrix']) {
    for (const o of (ctx.__T.S[ctx.SRV_ENTITIES[kind].coll] || [])) {
      await ctx.srvUpdate(kind, o);
    }
  }
  console.log(`   отправлено за ${((Date.now() - t0) / 1000).toFixed(1)} с`);

  const afterPush = path.join(OUT, 'live_after_push.json');
  const exp = await rawFetch('/api/export');
  if (!exp.ok) throw new Error('выгрузка сервера не удалась: ' + exp.status);
  fs.writeFileSync(afterPush, JSON.stringify(await exp.json(), null, 1), 'utf8');
  console.log(`   выгрузка сервера после отправки: ${afterPush}`);

  console.log('\nСверить (обе команды должны сказать «эквивалентен»):');
  console.log(`  cd server && ../server_venv/bin/python manage.py compare_json \\\n` +
              `      ${fixture} ${fromApi}`);
  console.log(`  cd server && ../server_venv/bin/python manage.py compare_json \\\n` +
              `      ${fixture} ${afterPush}`);
})().catch(err => { console.error(err); process.exit(1); });
