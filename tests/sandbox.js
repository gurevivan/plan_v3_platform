// Общая песочница для тестов расчётного ядра «План V3».
// Поднимает собранный харнесс (_build/harness.js) в vm с заглушками браузерного окружения.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const noop = () => {};

function fakeEl() {
  return {
    style: {},
    classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    appendChild: noop, removeChild: noop, setAttribute: noop, removeAttribute: noop,
    addEventListener: noop, removeEventListener: noop,
    querySelector: () => null, querySelectorAll: () => [],
    insertAdjacentHTML: noop, focus: noop, blur: noop, click: noop,
    innerHTML: '', textContent: '', value: '', dataset: {}, children: [], parentNode: null,
  };
}

/**
 * Загружает харнесс и возвращает контекст (он же globalThis приложения).
 *
 * `opts.storage` — что лежит в localStorage НА МОМЕНТ загрузки. Нужно для
 * проверок кода инициализации: он читает хранилище один раз при старте, и
 * подменить его потом уже поздно.
 */
function load(opts) {
  const file = path.join(__dirname, '_build', 'harness.js');
  if (!fs.existsSync(file)) {
    throw new Error('нет _build/harness.js — сначала запустите: python3 build.py');
  }
  const data = Object.assign({}, (opts && opts.storage) || {});
  const storage = {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
    clear: () => { for (const k in data) delete data[k]; },
    _data: data,
  };
  const sandbox = {
    console,
    document: {
      getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
      createElement: fakeEl, addEventListener: noop, removeEventListener: noop,
      body: fakeEl(), head: fakeEl(), documentElement: fakeEl(), readyState: 'complete',
    },
    localStorage: storage, sessionStorage: storage,
    indexedDB: { open: () => ({ addEventListener: noop }) },
    requestAnimationFrame: noop, cancelAnimationFrame: noop,
    setTimeout, clearTimeout, setInterval, clearInterval,
    alert: noop, confirm: () => true, prompt: () => null,
    // `window` в песочнице — это сам sandbox. Без этих заглушек любой
    // обработчик уровня окна (например, вопрос при закрытии вкладки) роняет
    // загрузку харнесса.
    addEventListener: noop, removeEventListener: noop, dispatchEvent: () => true,
    navigator: { userAgent: 'node' }, location: { href: '', search: '', hash: '' },
    fetch: () => Promise.reject(new Error('сеть в харнессе недоступна')),
    Blob: function () {}, URL: { createObjectURL: () => '', revokeObjectURL: noop },
    FileReader: function () {}, Image: function () {}, Event: function () {},
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(file, 'utf8'), sandbox, { filename: 'harness.js' });

  // Побочные эффекты UI и хранилища глушим — тестируем доменную логику.
  // Настоящую `save` оставляем доступной: без неё не проверить, КУДА она пишет,
  // а это отдельное поведение (в серверном режиме браузер писать нельзя).
  sandbox.saveReal = sandbox.save;
  sandbox.save = noop;
  sandbox.renderActive = noop;
  sandbox.renderMicroplan = noop;
  sandbox.flushMicroWorkerInputsFromDom = noop;

  return sandbox;
}

/** Мини-ассерты с человекочитаемым выводом. */
function makeChecker() {
  const state = { failed: 0, total: 0 };
  const check = (title, got, want) => {
    state.total++;
    const ok = JSON.stringify(got) === JSON.stringify(want);
    if (!ok) state.failed++;
    console.log(`${ok ? 'OK  ' : 'FAIL'} ${title}: получили ${JSON.stringify(got)}, ожидали ${JSON.stringify(want)}`);
  };
  const finish = () => {
    console.log(state.failed
      ? `\nПРОВАЛЕНО ПРОВЕРОК: ${state.failed} из ${state.total}`
      : `\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ (${state.total})`);
    process.exit(state.failed ? 1 : 0);
  };
  return { check, finish, state };
}

module.exports = { load, makeChecker };
