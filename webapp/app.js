/* Mini App «Нужные приложения» — витрина, заказ и панель продавца. */

const tg = window.Telegram && window.Telegram.WebApp;

const state = {
  boot: null,
  view: 'home',
  error: null,
  showAllApps: false,
  filter: 'Все',
  udid: '',
  udidChecked: null,
  udidError: null,
  agreeTerms: false,
  agreeUdid: false,
  busy: false,
  seller: { status: 'open', q: '', orders: [], counts: {}, current: null, instruction: '' },
};

const STEP_LABELS = [
  ['Оплата получена', 'Тестовая покупка подтверждена'],
  ['UDID передан', 'Номер подтверждён покупателем'],
  ['Сертификат установлен', 'После получения UDID'],
  ['Инструкция готова', 'Появится после завершения установки'],
];

/* ------------------------------------------------------------------ утилиты */

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function haptic(kind) {
  try {
    if (!tg || !tg.HapticFeedback) return;
    if (kind === 'error') tg.HapticFeedback.notificationOccurred('error');
    else if (kind === 'success') tg.HapticFeedback.notificationOccurred('success');
    else tg.HapticFeedback.impactOccurred('light');
  } catch (e) { /* не критично */ }
}

let toastTimer = null;
function toast(message, isError) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = 'toast show' + (isError ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2800);
  haptic(isError ? 'error' : 'success');
}

async function api(path, options) {
  const opts = options || {};
  const headers = { 'Content-Type': 'application/json' };
  if (tg && tg.initData) headers['X-Init-Data'] = tg.initData;
  const res = await fetch(path, {
    method: opts.method || 'GET',
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* пустой ответ */ }
  if (!res.ok) throw new Error(data.error || 'Ошибка ' + res.status);
  return data;
}

function appBySlug(slug) {
  return (state.boot.catalog || []).find((a) => a.slug === slug);
}

function iconHtml(app, extraClass) {
  if (!app) return '';
  const cls = 'app-icon' + (app.dark ? ' dark' : '') + (extraClass ? ' ' + extraClass : '');
  return (
    '<div class="' + cls + '" style="background:' + esc(app.color) + '">' +
    '<span>' + esc(app.letter) + '</span>' +
    '<img src="/static/icons/' + esc(app.slug) + '.png" alt="" loading="lazy" onerror="this.remove()">' +
    '</div>'
  );
}

/* --------------------------------------------------------------------- вид */

function stepper(active) {
  return '<div class="stepper">' + state.boot.steps.map((name, i) => {
    const n = i + 1;
    const cls = n < active ? 'chip done' : (n === active ? 'chip active' : 'chip');
    const mark = n < active ? '✓' : String(n);
    return '<div class="' + cls + '">' + mark + ' · ' + esc(name) + '</div>';
  }).join('') + '</div>';
}

function flowline(step) {
  return '<div class="flowline">' + state.boot.steps.map((name, i) => (
    '<span class="' + (i + 1 === step ? 'on' : '') + '">' + esc(name) + '</span>'
  )).join('<span class="sep">→</span>') + '</div>';
}

function appCard(app) {
  return (
    '<div class="app-card">' +
    '<span class="arrow">↗</span>' +
    iconHtml(app) +
    '<div class="name">' + esc(app.name) + '</div>' +
    '<div class="cat">' + esc(app.category) + '</div>' +
    '</div>'
  );
}

function viewHome() {
  const b = state.boot;
  const featured = (b.featured || []).map(appBySlug).filter(Boolean);
  const shown = (b.catalog || []).slice(0, 6);

  return (
    '<section class="hero">' +
      '<div class="hero-head">' +
        '<span class="eyebrow">Приложения для iPhone</span>' +
        '<span class="badge">iOS</span>' +
      '</div>' +
      '<h1>Все приложения.<br>Одна цена.</h1>' +
      '<p>Весь каталог за ' + esc(b.product.priceText) + '. Без лимита установок и доплат за приложения.</p>' +
      '<div class="cluster">' + featured.map((a) => iconHtml(a)).join('') + '</div>' +
      '<button class="btn btn-primary" data-action="tab:order">Весь каталог · ' + esc(b.product.priceText) + ' →</button>' +
      '<div class="cta-note">Один платёж · все приложения каталога</div>' +
    '</section>' +
    flowline(0) +
    '<div class="section-head">' +
      '<h2>Нужное каждый день</h2>' +
      '<button data-action="tab:apps">Все ' + b.product.appsCount + ' →</button>' +
    '</div>' +
    '<div class="grid">' + shown.map(appCard).join('') + '</div>' +
    '<div class="cta-note mt">Демо-каталог · доступность проверяется до запуска</div>'
  );
}

function viewApps() {
  const b = state.boot;
  const cats = ['Все'].concat(b.categories || []);
  const list = state.filter === 'Все'
    ? b.catalog
    : b.catalog.filter((a) => a.category === state.filter);

  return (
    '<div class="filters">' + cats.map((c) => (
      '<div class="chip' + (c === state.filter ? ' active' : '') + '" data-action="filter:' + esc(c) + '">' + esc(c) + '</div>'
    )).join('') + '</div>' +
    '<div class="grid">' + list.map(appCard).join('') + '</div>' +
    '<div class="cta-note mt">Состав услуги и совместимость подтвердим до открытия продаж.</div>' +
    '<div class="mt"><button class="btn btn-primary" data-action="tab:order">Оформить · ' + esc(b.product.priceText) + '</button></div>'
  );
}

/* ------------------------------------------------------------ экраны заказа */

function viewCheckout() {
  const b = state.boot;
  const modeText = {
    manual: 'Перевод с подтверждением',
    demo: 'Демо · без списания',
    stars: 'Telegram Stars ⭐',
  }[b.payment.mode];

  return (
    stepper(1) +
    '<h1>Оформление заказа</h1>' +
    '<div class="card">' +
      '<div class="product">' +
        '<div class="app-icon" style="background:var(--surface-3)"><span>📱</span></div>' +
        '<div><div class="name">' + esc(b.product.title) + '</div>' +
        '<div class="sub">UDID укажете следующим шагом</div></div>' +
      '</div>' +
      '<div class="row"><span class="label">Цена</span><span class="value">' + esc(b.product.priceText) + '</span></div>' +
      '<div class="row"><span class="label">Оплата</span><span class="value">' + esc(modeText) + '</span></div>' +
      (b.payment.mode === 'stars'
        ? '<div class="row"><span class="label">Сумма в Stars</span><span class="value">' + (b.payment.stars || '—') + '</span></div>'
        : '') +
      '<details class="disclosure"><summary>Условия заказа</summary>' +
        '<div class="body">После оплаты укажите UDID устройства. Продавец выполнит установку сертификата и подготовит инструкцию.\n\n' +
        esc(b.privacy) + '</div>' +
      '</details>' +
      '<label class="check"><input type="checkbox" id="agreeTerms"' + (state.agreeTerms ? ' checked' : '') + '>' +
        '<span>Ознакомился с условиями и понимаю порядок работы.</span></label>' +
      '<button class="btn btn-primary" id="buyBtn" data-action="buy"' + (state.agreeTerms ? '' : ' disabled') + '>' +
        'Оформить заказ и перейти к оплате</button>' +
    '</div>'
  );
}

function viewPayment(order) {
  const b = state.boot;
  const mode = order.paymentMode;
  let action = '';
  if (mode === 'demo') {
    action = '<button class="btn btn-primary" data-action="pay-demo">Имитировать оплату</button>' +
      '<div class="cta-note">Без списания денег</div>';
  } else if (mode === 'stars') {
    action = '<button class="btn btn-primary" data-action="pay-stars">Оплатить ' + (b.payment.stars || '') + ' ⭐</button>';
  } else {
    action = '<button class="btn btn-primary" data-action="pay-claim">Я оплатил</button>' +
      '<div class="cta-note">Продавец проверит поступление и подтвердит</div>';
  }

  return (
    stepper(1) +
    '<h1>Оплата заказа</h1>' +
    '<p class="muted">Заказ ' + esc(order.code) + ' · ' + esc(order.priceText) + '</p>' +
    '<div class="card">' +
      (mode === 'manual'
        ? '<div class="body" style="white-space:pre-wrap">' + esc(b.payment.details || 'Реквизиты уточните у продавца.') + '</div>' +
          '<div class="notice">Укажите в комментарии к платежу номер заказа ' + esc(order.code) + '.</div>'
        : '<div class="notice">' + esc(order.hint) + '</div>') +
      action +
    '</div>' +
    '<button class="btn btn-danger btn-sm" data-action="order-cancel">Отменить заказ</button>'
  );
}

function viewWaitingPayment(order) {
  return (
    stepper(1) +
    '<div class="pill">⏳ Оплата на проверке</div>' +
    '<h1>Проверяем платёж</h1>' +
    '<p class="muted">Заказ ' + esc(order.code) + '. Продавец подтвердит поступление — после этого попросим UDID.</p>' +
    '<div class="card"><button class="btn btn-secondary" data-action="order-refresh">Обновить статус</button></div>' +
    '<button class="btn btn-ghost btn-sm" data-action="order-help">Помощь по заказу</button>'
  );
}

function viewUdid(order) {
  const b = state.boot;
  const len = state.udid.length;
  const ready = len === 40 && state.agreeUdid && !state.busy;

  return (
    stepper(2) +
    '<div class="pill ok">✓ Оплата получена</div>' +
    '<h1>Теперь добавьте iPhone</h1>' +
    '<p class="muted">Отправьте UDID для заказа ' + esc(order.code) +
      '. Перед передачей продавцу вы сможете проверить номер.</p>' +
    '<details class="disclosure" open><summary>Где найти UDID?</summary>' +
      '<div class="body">Подключите iPhone к компьютеру кабелем.\n\n' +
      '• macOS: Finder → ваш iPhone → строка под именем устройства. Нажимайте на неё, ' +
      'пока не появится UDID, затем правый клик → «Скопировать».\n' +
      '• Windows: iTunes → значок устройства → «Обзор» → нажмите на «Серийный номер», ' +
      'он сменится на UDID.\n\n' +
      'UDID — 40 символов: цифры и латинские буквы от a до f.</div>' +
    '</details>' +
    '<div class="field">' +
      '<label for="udidInput">UDID устройства</label>' +
      '<input id="udidInput" class="udid" autocomplete="off" autocapitalize="off" spellcheck="false" ' +
      'placeholder="40 символов" value="' + esc(state.udid) + '">' +
      '<div class="field-foot"><span>Можно вставить из буфера</span>' +
      '<span class="count' + (len === 40 ? ' full' : '') + '" id="udidCount">' + len + ' / 40</span></div>' +
      (state.udidError ? '<div class="field-error">' + esc(state.udidError) + '</div>' : '') +
    '</div>' +
    (b.testUdid ? '<button class="linkish" data-action="udid-test">Использовать тестовый номер</button>' : '') +
    '<label class="check"><input type="checkbox" id="agreeUdid"' + (state.agreeUdid ? ' checked' : '') + '>' +
      '<span>Разрешаю передать UDID продавцу для обработки заказа.</span></label>' +
    (state.udidChecked
      ? '<div class="card tight"><div class="row"><span class="label">Проверено</span>' +
        '<span class="value mono">' + esc(state.udidChecked) + '</span></div>' +
        '<button class="btn btn-primary" data-action="udid-send">Отправить продавцу</button></div>'
      : '<button class="btn btn-primary" id="udidBtn" data-action="udid-check"' + (ready ? '' : ' disabled') + '>' +
        'Проверить номер →</button>') +
    '<div class="cta-note">' + esc(b.privacy) + '</div>'
  );
}

function viewOrderStatus(order) {
  const b = state.boot;
  const step = order.step;
  const rows = STEP_LABELS.map((pair, i) => {
    const n = i + 1;
    const cls = step > n ? 'tl done' : (step === n ? 'tl active' : 'tl pending');
    const mark = step > n ? '✓' : String(n);
    return (
      '<div class="' + cls + '"><div class="dot">' + mark + '</div>' +
      '<div><div class="t">' + esc(pair[0]) + '</div>' +
      '<div class="s">' + esc(pair[1]) + '</div></div></div>'
    );
  }).join('');

  return (
    '<div class="section-head"><h2>Заказ ' + esc(order.code) + '</h2>' +
      '<span class="badge">' + esc(order.statusTitle) + '</span></div>' +
    '<div class="card">' +
      '<div class="product">' +
        '<div class="app-icon" style="background:var(--surface-3)"><span>📱</span></div>' +
        '<div><div class="name">' + esc(b.product.title) + '</div>' +
        '<div class="sub">UDID ' + esc(order.udidMasked) + '</div></div>' +
      '</div>' +
      '<div class="timeline">' + rows + '</div>' +
      '<div class="notice">' + esc(order.hint) + '</div>' +
      (order.instruction
        ? '<div class="card tight"><pre class="instruction">' + esc(order.instruction) + '</pre></div>'
        : '') +
      '<button class="btn btn-secondary" data-action="order-refresh">Обновить статус</button>' +
    '</div>' +
    '<button class="btn btn-ghost" data-action="order-help">Помощь по заказу</button>'
  );
}

function viewOrder() {
  const order = state.boot.order;
  if (!order || (!order.isOpen && order.status !== 'done')) return viewCheckout();
  if (order.status === 'new') return viewPayment(order);
  if (order.status === 'payment_check') return viewWaitingPayment(order);
  if (order.status === 'paid') return viewUdid(order);
  return viewOrderStatus(order);
}

function viewHelp() {
  const b = state.boot;
  const support = b.support
    ? '<button class="btn btn-secondary" data-action="support">Написать @' + esc(b.support) + '</button>'
    : '<button class="btn btn-secondary" data-action="support">Написать продавцу</button>';

  return (
    '<h1>Помощь</h1>' +
    '<div class="card">' +
      '<details class="disclosure"><summary>Как проходит заказ?</summary><div class="body">' +
        'Оплата → вы присылаете UDID → продавец ставит сертификат → вы получаете инструкцию.' +
      '</div></details>' +
      '<details class="disclosure"><summary>Где найти UDID?</summary><div class="body">' +
        'Подключите iPhone к компьютеру: Finder на macOS или iTunes на Windows → нажмите ' +
        'на серийный номер устройства, он сменится на UDID из 40 символов.' +
      '</div></details>' +
      '<details class="disclosure"><summary>Что с моими данными?</summary><div class="body">' +
        esc(b.privacy) + '\n\nКоманда /forget в чате бота удаляет заказы и UDID.' +
      '</div></details>' +
      support +
    '</div>'
  );
}

/* -------------------------------------------------------------- продавец */

function sellerLine(o) {
  return (
    '<button class="order-line" data-action="seller-open:' + o.id + '">' +
      '<span class="status-dot ' + esc(o.status) + '"></span>' +
      '<span class="grow"><span class="code">' + esc(o.code) + '</span><br>' +
      '<span class="meta">' + esc(o.statusTitle) + ' · ' + esc(o.priceText) +
      (o.username ? ' · @' + esc(o.username) : '') + '</span></span>' +
      '<span class="meta">›</span>' +
    '</button>'
  );
}

function sellerActions(o) {
  const buttons = [];
  if (o.status === 'new' || o.status === 'payment_check') {
    buttons.push('<button class="btn btn-primary btn-sm" data-action="seller-act:payok">Оплата получена</button>');
    buttons.push('<button class="btn btn-secondary btn-sm" data-action="seller-act:payno">Платёж не найден</button>');
  } else if (o.status === 'udid') {
    buttons.push('<button class="btn btn-primary btn-sm" data-action="seller-act:installed">Сертификат установлен</button>');
  } else if (o.status === 'installed') {
    buttons.push('<button class="btn btn-primary btn-sm" data-action="seller-instr">Отправить инструкцию</button>');
  }
  if (!buttons.length) return '';
  return '<div class="btn-row mt">' + buttons.join('') + '</div>';
}

function viewSellerOrder(o) {
  return (
    '<button class="backlink" data-action="seller-back">← Все заказы</button>' +
    '<div class="section-head"><h2>' + esc(o.code) + '</h2>' +
      '<span class="badge">' + esc(o.statusTitle) + '</span></div>' +
    '<div class="card">' +
      '<div class="row"><span class="label">Покупатель</span><span class="value">' +
        esc(o.firstName || '—') + (o.username ? ' @' + esc(o.username) : '') + '</span></div>' +
      '<div class="row"><span class="label">Telegram ID</span><span class="value mono">' + esc(o.userId) + '</span></div>' +
      '<div class="row"><span class="label">Сумма</span><span class="value">' + esc(o.priceText) + '</span></div>' +
      '<div class="row"><span class="label">UDID</span><span class="value mono">' + esc(o.udid || '—') + '</span></div>' +
      '<div class="row"><span class="label">Создан</span><span class="value">' + esc((o.createdAt || '').replace('T', ' ').slice(0, 16)) + '</span></div>' +
      (o.note ? '<div class="row"><span class="label">Заметка</span><span class="value">' + esc(o.note) + '</span></div>' : '') +
      sellerActions(o) +
    '</div>' +
    (state.seller.instruction !== null && o.status === 'installed'
      ? '<div class="card"><label class="field"><span>Текст инструкции</span>' +
        '<textarea id="instrText" placeholder="Что сделать покупателю после установки…">' + esc(state.seller.instruction) + '</textarea></label>' +
        '<button class="btn btn-primary mt" data-action="seller-send-instr">Отправить покупателю</button></div>'
      : '') +
    (o.isOpen ? '<button class="btn btn-danger btn-sm" data-action="seller-act:cancel">Отменить заказ</button>' : '')
  );
}

function viewSeller() {
  const s = state.seller;
  if (s.current) return viewSellerOrder(s.current);

  const filters = [['open', 'Открытые'], ['payment_check', 'Проверить оплату'], ['udid', 'Ставить сертификат'],
    ['installed', 'Инструкция'], ['done', 'Готовые'], ['all', 'Все']];

  return (
    '<h1>Заказы</h1>' +
    '<input class="search" id="sellerSearch" placeholder="Код заказа, UDID или @юзернейм" value="' + esc(s.q) + '">' +
    '<div class="filters">' + filters.map(([key, label]) => {
      const n = key === 'all' || key === 'open' ? '' : ' · ' + (s.counts[key] || 0);
      return '<div class="chip' + (s.status === key ? ' active' : '') + '" data-action="seller-filter:' + key + '">' +
        esc(label) + esc(n) + '</div>';
    }).join('') + '</div>' +
    (s.orders.length
      ? s.orders.map(sellerLine).join('')
      : '<div class="notice">Заказов в этом фильтре нет.</div>')
  );
}

/* ------------------------------------------------------------------- каркас */

function tabbar() {
  const b = state.boot;
  const order = b.order;
  const alert = order && order.isOpen && (order.status === 'paid' || order.status === 'new');
  const tabs = [
    ['home', 'Главная', '⌂'],
    ['apps', 'Приложения', '▦'],
    ['order', 'Мой заказ', '✈'],
    ['help', 'Помощь', '?'],
  ];
  if (b.user.isSeller) tabs.push(['seller', 'Продавец', '★']);

  return tabs.map(([key, label, icon]) => (
    '<button class="' + (state.view === key ? 'on' : '') + '" data-action="tab:' + key + '">' +
    '<span>' + icon + '</span>' +
    (key === 'order' && alert ? '<span class="dot-badge"></span>' : '') +
    '<span>' + esc(label) + '</span></button>'
  )).join('');
}

function render() {
  const root = document.getElementById('view');

  if (state.error) {
    root.innerHTML = '<div class="card mt"><h2>Не открылось</h2><p class="muted">' + esc(state.error) +
      '</p><p class="muted">Откройте приложение кнопкой в чате бота.</p></div>';
    return;
  }
  if (!state.boot) return;

  const views = { home: viewHome, apps: viewApps, order: viewOrder, help: viewHelp, seller: viewSeller };
  root.innerHTML = (views[state.view] || viewHome)();
  document.getElementById('tabbar').innerHTML = tabbar();

  const sub = document.getElementById('brandSub');
  const order = state.boot.order;
  if (sub) {
    sub.textContent = order && order.isOpen
      ? 'Заказ ' + order.code + ' · ' + order.statusTitle
      : 'Доступ для вашего iPhone';
  }

  bindInputs();
  updateBackButton();
  window.scrollTo(0, 0);
}

function updateBackButton() {
  if (!tg || !tg.BackButton) return;
  const nested = state.view === 'seller' && state.seller.current;
  if (nested || (state.view !== 'home' && state.view !== 'order')) tg.BackButton.show();
  else tg.BackButton.hide();
}

function bindInputs() {
  const udid = document.getElementById('udidInput');
  if (udid) {
    udid.addEventListener('input', () => {
      const clean = udid.value.replace(/[^0-9a-fA-F]+/g, '').toLowerCase().slice(0, 40);
      if (udid.value !== clean) udid.value = clean;
      state.udid = clean;
      state.udidChecked = null;
      const count = document.getElementById('udidCount');
      if (count) {
        count.textContent = clean.length + ' / 40';
        count.className = 'count' + (clean.length === 40 ? ' full' : '');
      }
      const btn = document.getElementById('udidBtn');
      if (btn) btn.disabled = !(clean.length === 40 && state.agreeUdid);
    });
  }

  const agreeUdid = document.getElementById('agreeUdid');
  if (agreeUdid) {
    agreeUdid.addEventListener('change', () => {
      state.agreeUdid = agreeUdid.checked;
      const btn = document.getElementById('udidBtn');
      if (btn) btn.disabled = !(state.udid.length === 40 && state.agreeUdid);
    });
  }

  const agreeTerms = document.getElementById('agreeTerms');
  if (agreeTerms) {
    agreeTerms.addEventListener('change', () => {
      state.agreeTerms = agreeTerms.checked;
      const btn = document.getElementById('buyBtn');
      if (btn) btn.disabled = !state.agreeTerms;
    });
  }

  const search = document.getElementById('sellerSearch');
  if (search) {
    let timer = null;
    search.addEventListener('input', () => {
      state.seller.q = search.value;
      clearTimeout(timer);
      timer = setTimeout(() => loadSellerOrders().then(render), 350);
    });
  }

  const instr = document.getElementById('instrText');
  if (instr) instr.addEventListener('input', () => { state.seller.instruction = instr.value; });
}

/* ----------------------------------------------------------------- действия */

async function guard(fn) {
  if (state.busy) return;
  state.busy = true;
  try {
    await fn();
  } catch (err) {
    toast(err.message || 'Что-то пошло не так', true);
  } finally {
    state.busy = false;
  }
}

async function refreshOrder() {
  const data = await api('/api/order');
  state.boot.order = data.order;
}

async function loadSellerOrders() {
  const params = new URLSearchParams({ status: state.seller.status });
  if (state.seller.q.trim()) params.set('q', state.seller.q.trim());
  const data = await api('/api/seller/orders?' + params.toString());
  state.seller.orders = data.orders;
  state.seller.counts = data.counts || {};
}

async function sellerAction(action, extra) {
  const current = state.seller.current;
  if (!current) return;
  const body = Object.assign({ action, orderId: current.id }, extra || {});
  const data = await api('/api/seller/action', { method: 'POST', body });
  state.seller.current = data.order;
  await loadSellerOrders();
  toast('Готово');
}

const actions = {
  'support': () => {
    const username = state.boot && state.boot.support;
    if (username && tg) tg.openTelegramLink('https://t.me/' + username);
    else if (tg) tg.close();
  },

  'buy': () => guard(async () => {
    const data = await api('/api/order/create', { method: 'POST' });
    state.boot.order = data.order;
    state.view = 'order';
    toast('Заказ ' + data.order.code + ' создан');
    render();
  }),

  'pay-claim': () => guard(async () => {
    const data = await api('/api/order/claim', { method: 'POST', body: { orderId: state.boot.order.id } });
    state.boot.order = data.order;
    toast('Отправлено продавцу');
    render();
  }),

  'pay-demo': () => guard(async () => {
    const data = await api('/api/order/demopay', { method: 'POST', body: { orderId: state.boot.order.id } });
    state.boot.order = data.order;
    toast('Оплата имитирована');
    render();
  }),

  'pay-stars': () => guard(async () => {
    const data = await api('/api/order/invoice', { method: 'POST', body: { orderId: state.boot.order.id } });
    if (!tg || !tg.openInvoice) throw new Error('Оплата доступна только в Telegram');
    tg.openInvoice(data.link, async (status) => {
      if (status === 'paid') {
        await refreshOrder();
        toast('Оплачено');
      } else if (status === 'failed') {
        toast('Платёж не прошёл', true);
      }
      render();
    });
  }),

  'udid-test': () => {
    state.udid = state.boot.testUdid || '';
    state.udidChecked = null;
    state.udidError = null;
    render();
  },

  'udid-check': () => guard(async () => {
    state.udidError = null;
    const data = await api('/api/order/udid/check', { method: 'POST', body: { udid: state.udid } });
    if (!data.valid) {
      state.udidError = data.error;
      state.udidChecked = null;
      haptic('error');
    } else {
      state.udidChecked = data.masked;
      haptic('success');
    }
    render();
  }),

  'udid-send': () => guard(async () => {
    const data = await api('/api/order/udid', {
      method: 'POST',
      body: { orderId: state.boot.order.id, udid: state.udid },
    });
    state.boot.order = data.order;
    state.udid = '';
    state.udidChecked = null;
    state.agreeUdid = false;
    toast('UDID передан продавцу');
    render();
  }),

  'order-refresh': () => guard(async () => {
    await refreshOrder();
    toast('Обновлено');
    render();
  }),

  'order-cancel': () => guard(async () => {
    const data = await api('/api/order/cancel', { method: 'POST', body: { orderId: state.boot.order.id } });
    state.boot.order = data.order;
    toast('Заказ отменён');
    render();
  }),

  'order-help': () => guard(async () => {
    await api('/api/order/help', { method: 'POST', body: { orderId: state.boot.order.id } });
    toast('Продавец получил запрос');
  }),

  'seller-back': () => { state.seller.current = null; render(); },

  'seller-instr': () => { state.seller.instruction = ''; render(); },

  'seller-send-instr': () => guard(async () => {
    await sellerAction('instruction', { text: state.seller.instruction });
    state.seller.instruction = '';
    render();
  }),
};

function handleAction(raw) {
  if (actions[raw]) { haptic('light'); actions[raw](); return; }

  const [kind, value] = raw.split(':');
  if (kind === 'tab') {
    state.view = value;
    state.seller.current = null;
    haptic('light');
    if (value === 'seller') guard(async () => { await loadSellerOrders(); render(); });
    else if (value === 'order') guard(async () => { await refreshOrder(); render(); });
    else render();
    return;
  }
  if (kind === 'filter') { state.filter = value; render(); return; }
  if (kind === 'seller-filter') {
    state.seller.status = value;
    guard(async () => { await loadSellerOrders(); render(); });
    return;
  }
  if (kind === 'seller-open') {
    state.seller.current = state.seller.orders.find((o) => String(o.id) === value) || null;
    state.seller.instruction = '';
    render();
    return;
  }
  if (kind === 'seller-act') {
    guard(async () => { await sellerAction(value); render(); });
    return;
  }
}

document.addEventListener('click', (event) => {
  const target = event.target.closest('[data-action]');
  if (!target || target.disabled) return;
  handleAction(target.getAttribute('data-action'));
});

/* ------------------------------------------------------------------- старт */

/* Вне Telegram (например, в браузере при отладке) telegram-web-app.js
   всё равно подгружается и всегда сообщает светлую тему — поэтому за её
   пределами ориентируемся на системную. */
function isLight() {
  const inTelegram = !!(tg && tg.initData);
  if (inTelegram) return tg.colorScheme === 'light';
  return window.matchMedia('(prefers-color-scheme: light)').matches;
}

function applyTheme() {
  document.documentElement.dataset.theme = isLight() ? 'light' : 'dark';
}

async function boot() {
  applyTheme();

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.onEvent) tg.onEvent('themeChanged', applyTheme);
    try { tg.setHeaderColor(isLight() ? '#f2f2f7' : '#000000'); } catch (e) { /* старый клиент */ }
    if (tg.BackButton) {
      tg.BackButton.onClick(() => {
        if (state.seller.current) state.seller.current = null;
        else state.view = 'home';
        render();
      });
    }
  }

  try {
    state.boot = await api('/api/bootstrap');
    // Незакрытый заказ важнее витрины — открываем сразу на нём.
    if (state.boot.order && state.boot.order.isOpen) state.view = 'order';
  } catch (err) {
    state.error = err.message;
  }
  render();
}

boot();
