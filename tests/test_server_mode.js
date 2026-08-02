// Клиент серверного режима (фаза 4, вкладка номенклатуры).
//
// Сеть подменяется: проверяем не то, что сервер отвечает, а то, как приложение
// себя ведёт — что отправляет, как переводит форматы и что делает при конфликте
// версий. Настоящий сервер проверяется тестами API на стороне Django.
const { load, makeChecker } = require('./sandbox');

const ctx = load();
const T = ctx.__T;
const { check, finish } = makeChecker();

ctx.renderActive = () => {};
ctx.renderRefs = () => {};   // локальные ветки зовут его напрямую
ctx.save = () => {};
let alerts = [];
ctx._alert = (msg) => { alerts.push(String(msg)); };
ctx._confirm = () => Promise.resolve(true);

// ── Подмена сети ────────────────────────────────────────────────────────────
let calls = [];
let responses = [];
ctx.fetch = (url, opt) => {
  calls.push({ url, method: (opt && opt.method) || 'GET',
               headers: (opt && opt.headers) || {},
               body: opt && opt.body ? JSON.parse(opt.body) : null });
  const r = responses.shift() || { status: 200, data: {} };
  return Promise.resolve({
    ok: r.status >= 200 && r.status < 300,
    status: r.status,
    statusText: 'test',
    json: () => Promise.resolve(r.data),
  });
};
ctx.document.cookie = 'csrftoken=TOKEN123';
// Режим включаем напрямую: localStorage в песочнице заглушён.
Object.defineProperty(ctx.SRV, 'on', { get: () => true, set: () => {} });

const srvCard = (over = {}) => Object.assign({
  id: 7, src_id: 700, article_gp: '01.100', article_item: '01.100/2', name: 'Куртка',
  model_code: 'M1', assortment_group: 'Верх', norm_type: 'БТК', ac_flag: false,
  nom_ops: ['ОП-А'], nom_workshops: [], version: 3,
  route: [{ id: 1, ordinal: 0, stage: 'ШЦ', article_item: '01.100/2',
            name: 'Пошив', time_cost: '60.0000' }],
}, over);

(async () => {
  // ── Формат сервера → формат приложения ────────────────────────────────────
  console.log('── Перевод форматов ──');
  const local = ctx.srvNomToLocal(srvCard());
  check('идентификатор берётся из src_id', local.id, 700);
  check('первичный ключ сервера сохранён', local.srvId, 7);
  check('версия сохранена', local.srvVersion, 3);
  check('артикул ГП', local.articleGp, '01.100');
  check('модель', local.model, 'M1');
  check('норма маршрута стала числом', local.route[0].timeCost, 60);
  check('переопределения маршрута — пустой массив', Array.isArray(local.routeOverrides), true);

  const body = ctx.localNomToSrv(local);
  check('в запрос уходит article_gp', body.article_gp, '01.100');
  check('в запрос уходит model_code', body.model_code, 'M1');
  check('версия в тело не попадает сама', body.version, undefined);

  // ── Загрузка списка ───────────────────────────────────────────────────────
  console.log('\n── Загрузка с сервера ──');
  calls = []; responses = [{ status: 200, data: { results: [srvCard()], next: null } }];
  const ok = await ctx.srvLoadNomenclature();
  check('загрузка успешна', ok, true);
  check('запрос ушёл на nomenclature', calls[0].url.includes('/api/nomenclature/'), true);
  check('карточка попала в состояние', T.S.nomenclature.length, 1);
  check('состояние в формате приложения', T.S.nomenclature[0].articleGp, '01.100');

  // ── Правка: версия уходит на сервер ───────────────────────────────────────
  console.log('\n── Правка карточки ──');
  calls = []; alerts = [];
  responses = [{ status: 200, data: srvCard({ version: 4, name: 'Куртка утеплённая' }) }];
  const card = T.S.nomenclature[0];
  card.name = 'Куртка утеплённая';
  await ctx.srvUpdNom(card);
  check('метод PATCH', calls[0].method, 'PATCH');
  check('адрес с ключом сервера', calls[0].url, '/api/nomenclature/7/');
  check('версия отправлена', calls[0].body.version, 3);
  check('CSRF-токен отправлен', calls[0].headers['X-CSRFToken'], 'TOKEN123');
  check('новая версия принята', card.srvVersion, 4);
  check('ошибок не показано', alerts.length, 0);

  console.log('\n── CSRF только на изменяющих запросах ──');
  // Загружаем ту же карточку обратно: список нужен следующим секциям.
  calls = []; responses = [{ status: 200, data: { results: [srvCard({ version: 4 })], next: null } }];
  await ctx.srvLoadNomenclature();
  check('на GET токен не нужен', calls[0].headers['X-CSRFToken'], undefined);
  check('список не опустел', T.S.nomenclature.length, 1);

  // ── Конфликт версий ───────────────────────────────────────────────────────
  console.log('\n── Конфликт версий ──');
  calls = []; alerts = [];
  responses = [
    { status: 409, data: { version: 'изменено другим' } },
    { status: 200, data: { results: [srvCard({ version: 9, name: 'Чужая правка' })], next: null } },
  ];
  await ctx.srvUpdNom(T.S.nomenclature[0]);
  check('пользователю показано сообщение', alerts.length, 1);
  check('текст про другого пользователя',
        /другой пользователь/i.test(alerts[0]), true);
  check('список перечитан с сервера', calls.length, 2);
  check('в состоянии — данные сервера, а не наши', T.S.nomenclature[0].name, 'Чужая правка');
  check('версия обновлена', T.S.nomenclature[0].srvVersion, 9);

  // ── Ошибка прав ───────────────────────────────────────────────────────────
  console.log('\n── Отказ в правах ──');
  calls = []; alerts = [];
  responses = [{ status: 403, data: { detail: 'нет доступа' } },
               { status: 200, data: { results: [srvCard()], next: null } }];
  await ctx.srvUpdNom(T.S.nomenclature[0]);
  check('сообщение показано', /нет прав/i.test(alerts[0] || ''), true);

  // ── Удаление ──────────────────────────────────────────────────────────────
  console.log('\n── Удаление ──');
  calls = []; alerts = [];
  responses = [{ status: 204, data: null },
               { status: 200, data: { results: [], next: null } }];
  await ctx.delNom(T.S.nomenclature[0].id);
  check('метод DELETE', calls[0].method, 'DELETE');
  check('список перечитан', T.S.nomenclature.length, 0);

  // ── Постраничная выдача собирается целиком ────────────────────────────────
  console.log('\n── Постраничная выдача ──');
  calls = [];
  responses = [
    { status: 200, data: { results: [srvCard({ id: 1, src_id: 1 })],
                           next: 'http://x/api/nomenclature/?limit=200&offset=200' } },
    { status: 200, data: { results: [srvCard({ id: 2, src_id: 2 })], next: null } },
  ];
  await ctx.srvLoadNomenclature();
  check('обе страницы запрошены', calls.length, 2);
  check('вторая страница по относительному адресу',
        !!(calls[1] && calls[1].url.startsWith('/api/nomenclature/')), true);
  check('собраны обе записи', T.S.nomenclature.length, 2);

  await opsSection();
  await refsSection();
  finish();
})();

// ── Вторая переведённая сущность: ОП и цеха ────────────────────────────────
// Проверяем, что общий механизм действительно общий: та же логика версий и
// ошибок работает для другой сущности без отдельного кода.
async function opsSection() {
  console.log('\n── ОП и цеха ──');
  const srvOp = (over = {}) => Object.assign({
    id: 11, src_id: 500, name: 'Ташкент', workshop: 'Швейный цех',
    op_type: 'БТК', process_type: 'ШЦ', shift_duration: 720, frv_min: 0, version: 2,
  }, over);

  calls = []; responses = [{ status: 200, data: { results: [srvOp()], next: null } }];
  check('загрузка ОП успешна', await ctx.srvLoad('ops'), true);
  check('запрос ушёл на ops', calls[0].url.includes('/api/ops/'), true);
  check('ОП попал в состояние', T.S.ops.length, 1);
  check('тип цеха переведён', T.S.ops[0].type, 'БТК');
  check('передел переведён', T.S.ops[0].processType, 'ШЦ');
  check('смена взята с сервера, а не по умолчанию', T.S.ops[0].shiftDuration, 720);

  // Правка: версия и обратный перевод полей.
  calls = []; alerts = [];
  responses = [{ status: 200, data: srvOp({ version: 3, workshop: 'Цех курток' }) }];
  const op = T.S.ops[0];
  op.workshop = 'Цех курток';
  await ctx.srvUpdate('ops', op);
  check('метод PATCH', calls[0].method, 'PATCH');
  check('адрес с ключом сервера', calls[0].url, '/api/ops/11/');
  check('поле переведено в op_type', calls[0].body.op_type, 'БТК');
  check('поле переведено в process_type', calls[0].body.process_type, 'ШЦ');
  check('версия отправлена', calls[0].body.version, 2);
  check('новая версия принята', op.srvVersion, 3);
  check('ошибок нет', alerts.length, 0);

  // Конфликт версий — тот же механизм, что и у номенклатуры.
  calls = []; alerts = [];
  responses = [
    { status: 409, data: { version: 'изменено' } },
    { status: 200, data: { results: [srvOp({ version: 8, name: 'Бухара' })], next: null } },
  ];
  await ctx.srvUpdate('ops', T.S.ops[0]);
  check('сообщение о конфликте', /другой пользователь/i.test(alerts[0] || ''), true);
  check('данные перечитаны с сервера', T.S.ops[0].name, 'Бухара');

  // Создание.
  calls = []; alerts = [];
  responses = [{ status: 201, data: srvOp({ id: 12, src_id: 501 }) },
               { status: 200, data: { results: [srvOp(), srvOp({ id: 12, src_id: 501 })], next: null } }];
  await ctx.srvCreate('ops', { name: 'Самарканд', workshop: 'ШЦ', type: 'БП',
                               processType: 'ШЦ', shiftDuration: 600, frvMin: 0 });
  check('метод POST', calls[0].method, 'POST');
  check('смена ушла числом', calls[0].body.shift_duration, 600);
  check('список перечитан после создания', T.S.ops.length, 2);

  // Удаление.
  calls = []; responses = [{ status: 204, data: null },
                           { status: 200, data: { results: [], next: null } }];
  await ctx.srvDelete('ops', T.S.ops[0].id);
  check('метод DELETE', calls[0].method, 'DELETE');
  check('список пуст', T.S.ops.length, 0);

  // ── Точки входа вкладки, а не внутренние функции ────────────────────────
  // Иначе можно отключить серверный режим в самом обработчике, и тесты этого
  // не заметят: они звали бы srvUpdate напрямую.
  console.log('\n── Обработчики вкладки ──');
  calls = []; alerts = [];
  responses = [{ status: 200, data: { results: [srvOp()], next: null } }];
  await ctx.srvLoad('ops');

  calls = [];
  responses = [{ status: 200, data: srvOp({ version: 5, workshop: 'Новый цех' }) }];
  ctx.updOp(T.S.ops[0].id, 'workshop', 'Новый цех');
  await new Promise(r => setTimeout(r, 0));
  check('updOp отправил правку на сервер', (calls[0] || {}).method, 'PATCH');
  check('в теле новое значение', (calls[0] || {}).body && calls[0].body.workshop, 'Новый цех');

  calls = [];
  responses = [{ status: 204, data: null },
               { status: 200, data: { results: [], next: null } }];
  await ctx.delOp(T.S.ops[0].id);
  check('delOp удалил на сервере', (calls[0] || {}).method, 'DELETE');
}

// ── Базы и матрица доставок ────────────────────────────────────────────────
// Третья и четвёртая сущности на общем механизме. Проверяются через настоящие
// обработчики вкладки: подмена внутри обработчика иначе прошла бы мимо.
async function refsSection() {
  console.log('\n── Базы ──');
  const srvBase = (over = {}) => Object.assign(
    { id: 21, src_id: 900, name: 'База Москва', version: 4 }, over);

  calls = []; responses = [{ status: 200, data: { results: [srvBase()], next: null } }];
  check('загрузка баз', await ctx.srvLoad('bases'), true);
  check('адрес bases', calls[0].url.includes('/api/bases/'), true);
  check('база в состоянии', T.S.bases[0].name, 'База Москва');
  check('версия сохранена', T.S.bases[0].srvVersion, 4);

  calls = []; alerts = [];
  responses = [{ status: 200, data: srvBase({ version: 5, name: 'База Тула' }) }];
  ctx.updBase(T.S.bases[0].id, 'name', 'База Тула');
  await new Promise(r => setTimeout(r, 0));
  check('updBase ушёл на сервер', (calls[0] || {}).method, 'PATCH');
  check('версия отправлена', (calls[0] || {}).body && calls[0].body.version, 4);
  check('имя в теле', (calls[0] || {}).body && calls[0].body.name, 'База Тула');

  calls = [];
  responses = [{ status: 204, data: null }, { status: 200, data: { results: [], next: null } }];
  await ctx.delBase(T.S.bases[0].id);
  check('delBase удалил на сервере', (calls[0] || {}).method, 'DELETE');
  check('список пуст', T.S.bases.length, 0);

  console.log('\n── Матрица доставок ──');
  const srvDm = (over = {}) => Object.assign(
    { id: 31, src_id: 950, from_op: 'ОП-А', to_op: 'ОП-Б', days: 2, version: 1 }, over);

  calls = []; responses = [{ status: 200, data: { results: [srvDm()], next: null } }];
  await ctx.srvLoad('deliveryMatrix');
  check('строка матрицы в состоянии', T.S.deliveryMatrix[0].fromOp, 'ОП-А');
  check('дни числом', T.S.deliveryMatrix[0].days, 2);

  calls = []; responses = [{ status: 200, data: srvDm({ days: 5, version: 2 }) }];
  ctx.updDeliveryMatrixDays(T.S.deliveryMatrix[0].id, '5');
  await new Promise(r => setTimeout(r, 0));
  check('правка дней ушла на сервер', (calls[0] || {}).method, 'PATCH');
  check('дни в теле', (calls[0] || {}).body && calls[0].body.days, 5);
  check('перевод в from_op', (calls[0] || {}).body && calls[0].body.from_op, 'ОП-А');

  // Отрицательные дни не должны уходить: обработчик их обрезает до нуля.
  calls = []; responses = [{ status: 200, data: srvDm({ days: 0, version: 3 }) }];
  ctx.updDeliveryMatrixDays(T.S.deliveryMatrix[0].id, '-7');
  await new Promise(r => setTimeout(r, 0));
  check('отрицательные дни обрезаны до нуля', (calls[0] || {}).body && calls[0].body.days, 0);

  calls = [];
  responses = [{ status: 204, data: null }, { status: 200, data: { results: [], next: null } }];
  await ctx.delDeliveryMatrixRow(T.S.deliveryMatrix[0].id);
  check('строка матрицы удалена на сервере', (calls[0] || {}).method, 'DELETE');
}
