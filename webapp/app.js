/* Mini App «iApki» — витрина, заказ и панель продавца. */

const tg = window.Telegram && window.Telegram.WebApp;

const state = {
  boot: null,
  view: 'home',
  error: null,
  showAllApps: false,
  filter: 'Все',
  newOrder: false,
  checkoutApp: null,
  screen: '',
  udid: '',
  udidChecked: null,
  udidError: null,
  agreeTerms: false,
  agreeUdid: false,
  busy: false,
  seller: { status: 'open', q: '', orders: [], counts: {}, current: null, instruction: '' },
  chat: { orderId: null, title: '', from: 'order', messages: [], timer: null },
  notify: { kinds: [], order: null, modes: [], from: 'help' },
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
  // Файл иконки называет сервер: буква остаётся запасным вариантом.
  const img = app.icon
    ? '<img src="/static/icons/' + esc(app.icon) + '" alt="" loading="lazy" onerror="this.remove()">'
    : '';
  // span, а не div: иконка живёт и внутри кнопки-карточки.
  return (
    '<span class="' + cls + '" style="background:' + esc(app.color) + '">' +
    '<span>' + esc(app.letter) + '</span>' + img +
    '</span>'
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
    '<button type="button" class="app-card" data-action="pick-app:' + esc(app.slug) + '" ' +
      'aria-label="' + esc(app.name) + ' — оформить">' +
    '<span class="arrow">↗</span>' +
    iconHtml(app) +
    '<span class="name">' + esc(app.name) + '</span>' +
    '<span class="cat">' + esc(app.category) + '</span>' +
    '</button>'
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
      '<h1>Твои приложения.<br><em>Снова на iPhone.</em></h1>' +
      '<p>Весь каталог за ' + esc(b.product.priceText) + '. Без лимита установок и доплат за приложения.</p>' +
      '<div class="cluster">' + featured.map((a) => iconHtml(a)).join('') + '</div>' +
      '<button class="btn btn-primary" data-action="buy-catalog">Весь каталог · ' + esc(b.product.priceText) + ' →</button>' +
      '<div class="cta-note">Один платёж · все приложения каталога</div>' +
    '</section>' +
    flowline(0) +
    '<div class="section-head">' +
      '<h2>Нужное каждый день</h2>' +
      '<button data-action="tab:apps">Все ' + b.product.appsCount + ' →</button>' +
    '</div>' +
    '<div class="grid">' + shown.map(appCard).join('') + '</div>'
  );
}

function viewApps() {
  const b = state.boot;
  const cats = ['Все'].concat(b.categories || []);
  const list = state.filter === 'Все'
    ? b.catalog
    : b.catalog.filter((a) => a.category === state.filter);

  return (
    '<h1>Каталог</h1><p class="muted">Все нужные приложения — в одном месте.</p>' +
    '<div class="filters">' + cats.map((c) => (
      '<button class="chip' + (c === state.filter ? ' active' : '') + '" aria-pressed="' + (c === state.filter) + '" data-action="filter:' + esc(c) + '">' + esc(c) + '</button>'
    )).join('') + '</div>' +
    '<div class="grid">' + list.map(appCard).join('') + '</div>' +
    '<div class="cta-note mt">Состав услуги и совместимость подтвердим до открытия продаж.</div>' +
    '<div class="mt"><button class="btn btn-primary" data-action="buy-catalog">Весь каталог · ' + esc(b.product.priceText) + '</button></div>'
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

  const app = state.checkoutApp ? appBySlug(state.checkoutApp) : null;
  const price = app ? app.priceText : b.product.priceText;

  return (
    stepper(1) +
    '<h1>Оформление заказа</h1>' +
    '<div class="card">' +
      '<div class="product">' +
        (app ? iconHtml(app) : '<span class="app-icon" style="background:var(--surface-3)"><span>📱</span></span>') +
        '<div><div class="name">' + esc(app ? app.name : 'Весь каталог') + '</div>' +
        '<div class="sub">' + esc(app ? app.category : 'Все ' + b.product.appsCount + ' приложений') +
        ' · UDID укажете следующим шагом</div></div>' +
      '</div>' +
      '<div class="row"><span class="label">Цена</span><span class="value">' + esc(price) + '</span></div>' +
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
    '</div>' +
    (state.newOrder && b.order
      ? '<button class="btn btn-ghost btn-sm" data-action="back-to-order">Вернуться к заказу ' +
        esc(b.order.code) + '</button>'
      : '')
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
    '<p class="muted">Заказ ' + esc(order.code) + ' · ' + esc(order.productName) + ' · ' + esc(order.priceText) + '</p>' +
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
    '<p class="muted">Заказ ' + esc(order.code) + ' · ' + esc(order.productName) +
      '. Продавец подтвердит поступление — после этого попросим UDID.</p>' +
    '<div class="card"><button class="btn btn-secondary" data-action="order-refresh">Обновить статус</button></div>' +
    chatButton('Чат с продавцом') +
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
      ' — номер проверим при отправке.</p>' +
    '<details class="disclosure" open><summary>Где найти UDID?</summary>' +
      '<div class="body">Компьютер не нужен — всё делается на самом iPhone.\n\n' +
      '1. Откройте в Safari сайт udid.tech\n' +
      '2. Нажмите Get My UDID и разрешите загрузку профиля\n' +
      '3. Зайдите в Настройки → Профиль загружен → Установить. ' +
      'Если попросит, введите код-пароль iPhone\n' +
      '4. После установки откроется страница с вашим UDID — скопируйте его\n\n' +
      'UDID — 40 символов: цифры и латинские буквы от a до f.</div>' +
      '<button class="btn btn-secondary btn-sm mt" data-action="open-udid-site">' +
        'Открыть udid.tech</button>' +
      '<img class="guide" src="/static/img/udid-guide.webp" loading="lazy" ' +
        'alt="Как узнать UDID на iPhone: пошаговые экраны">' +
    '</details>' +
    '<div class="field">' +
      '<label for="udidInput">UDID устройства</label>' +
      '<input id="udidInput" class="udid" autocomplete="off" autocapitalize="off" spellcheck="false" ' +
      'enterkeyhint="send" ' +
      'placeholder="40 символов" value="' + esc(state.udid) + '">' +
      '<div class="field-foot"><span>Можно вставить из буфера</span>' +
      '<span class="count' + (len === 40 ? ' full' : '') + '" id="udidCount">' + len + ' / 40</span></div>' +
      (state.udidError ? '<div class="field-error">' + esc(state.udidError) + '</div>' : '') +
    '</div>' +
    (b.testUdid ? '<button class="linkish" data-action="udid-test">Использовать тестовый номер</button>' : '') +
    '<label class="check"><input type="checkbox" id="agreeUdid"' + (state.agreeUdid ? ' checked' : '') + '>' +
      '<span>Разрешаю передать UDID продавцу для обработки заказа.</span></label>' +
    '<button class="btn btn-primary" id="udidBtn" data-action="udid-send"' + (ready ? '' : ' disabled') + '>' +
      'Отправить продавцу →</button>' +
    '<div class="cta-note">' + esc(b.privacy) + '</div>'
  );
}

function dateOf(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function timeOf(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

function chatMessagesHtml() {
  const list = state.chat.messages;
  if (!list.length) {
    return '<div class="chat-empty">Сообщений пока нет. Напишите первым — ответ придёт сюда и в чат бота.</div>';
  }
  return list.map((m) => (
    '<div class="msg' + (m.mine ? ' mine' : '') + '">' +
      '<div class="msg-text">' + esc(m.text) + '</div>' +
      '<div class="msg-time">' + esc(timeOf(m.createdAt)) + '</div>' +
    '</div>'
  )).join('');
}

function viewNotify() {
  const n = state.notify;
  return (
    '<button class="backlink" data-action="notify-back">← Назад</button>' +
    '<h1>Уведомления</h1>' +
    '<p class="muted">Что присылать в чат бота. Статус заказа и инструкция ' +
      'остаются в приложении, даже если всё выключить.</p>' +
    '<div class="card">' +
      (n.kinds.length ? n.kinds.map((k) => (
        '<label class="switch-row">' +
          '<span class="switch-text">' +
            '<span class="switch-title">' + esc(k.title) + '</span>' +
            '<span class="switch-hint">' + esc(k.hint) + '</span>' +
          '</span>' +
          '<input type="checkbox" class="switch" data-toggle="' + esc(k.key) + '"' +
            (k.enabled ? ' checked' : '') + '>' +
        '</label>'
      )).join('') : '<div class="muted">Загружаем…</div>') +
    '</div>' +
    (n.order
      ? '<div class="section-head"><h2>Заказ ' + esc(n.order.code) + '</h2></div>' +
        '<div class="card">' +
          '<p class="muted">Настройка по этому заказу сильнее общей.</p>' +
          '<div class="filters">' + n.modes.map((m) => (
            '<div class="chip' + (m.key === n.order.mode ? ' active' : '') +
            '" data-action="notify-mode:' + esc(m.key) + '">' + esc(m.title) + '</div>'
          )).join('') + '</div>' +
        '</div>'
      : '')
  );
}

async function loadNotify(orderId) {
  const query = orderId ? '?orderId=' + orderId : '';
  const data = await api('/api/notify' + query);
  state.notify.kinds = data.kinds;
  state.notify.order = data.order;
  state.notify.modes = data.modes;
}

function openNotify(orderId, from) {
  state.notify.from = from || 'help';
  state.view = 'notify';
  render();
  guard(async () => {
    await loadNotify(orderId);
    render();
  });
}

function viewChat() {
  return (
    '<button class="backlink" data-action="chat-back">← Назад</button>' +
    '<div class="section-head"><h2>' + esc(state.chat.title || 'Переписка') + '</h2></div>' +
    '<div class="chat-list" id="chatList">' + chatMessagesHtml() + '</div>' +
    '<div class="composer">' +
      '<textarea id="chatInput" rows="1" placeholder="Сообщение продавцу…"></textarea>' +
      '<button class="send-btn" data-action="chat-send" aria-label="Отправить">↑</button>' +
    '</div>'
  );
}

function paintChat(scroll) {
  const list = document.getElementById('chatList');
  if (!list) return;
  list.innerHTML = chatMessagesHtml();
  if (scroll !== false) window.scrollTo(0, document.body.scrollHeight);
}

async function loadChat(scroll) {
  const data = await api('/api/chat?orderId=' + state.chat.orderId);
  const changed = data.messages.length !== state.chat.messages.length;
  state.chat.messages = data.messages;
  if (state.boot) {
    state.boot.unread = 0;
    const row = (state.seller.orders || []).find((o) => o.id === state.chat.orderId);
    if (row && row.unread) {
      state.boot.sellerUnread = Math.max(0, (state.boot.sellerUnread || 0) - row.unread);
      row.unread = 0;
    }
  }
  if (changed || scroll === true) paintChat(scroll !== false);
}

function stopChatPolling() {
  if (state.chat.timer) {
    clearInterval(state.chat.timer);
    state.chat.timer = null;
  }
}

function startChatPolling() {
  stopChatPolling();
  let failures = 0;
  // Новые сообщения второй стороны подтягиваем сами: вебсокета тут нет.
  state.chat.timer = setInterval(() => {
    if (state.view !== 'chat') {
      stopChatPolling();
      return;
    }
    loadChat(false).then(() => { failures = 0; }).catch(() => {
      failures += 1;
      // Сервер недоступен или заказ уже не наш — не долбим его бесконечно.
      if (failures >= 3) {
        stopChatPolling();
        toast('Связь с сервером потеряна, откройте переписку заново', true);
      }
    });
  }, 5000);
}

function openChat(orderId, title, from) {
  state.chat.orderId = orderId;
  state.chat.title = title;
  state.chat.from = from || 'order';
  state.chat.messages = [];
  state.view = 'chat';
  render();
  guard(async () => {
    await loadChat(true);
    startChatPolling();
  });
}

function startCheckout(slug) {
  const order = state.boot.order;
  // Оплаченный заказ в работе не перенастраиваем — сначала его нужно довести.
  if (order && order.isOpen && order.status !== 'new') {
    state.newOrder = false;
    state.view = 'order';
    render();
    toast('Сначала завершите заказ ' + order.code);
    return;
  }
  state.checkoutApp = slug || null;
  state.newOrder = true;
  state.agreeTerms = false;
  state.view = 'order';
  render();
}

function confirmAction(message, onYes) {
  if (tg && tg.showConfirm) {
    tg.showConfirm(message, (ok) => { if (ok) onYes(); });
    return;
  }
  if (window.confirm(message)) onYes();
}

function chatButton(label) {
  const unread = state.boot && state.boot.unread ? state.boot.unread : 0;
  const badge = unread ? ' <span class="count-badge">' + unread + '</span>' : '';
  return '<button class="btn btn-secondary" data-action="chat">' + esc(label) + badge + '</button>';
}

function viewOrderStatus(order) {
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
        (appBySlug(order.app)
          ? iconHtml(appBySlug(order.app))
          : '<span class="app-icon" style="background:var(--surface-3)"><span>📱</span></span>') +
        '<div><div class="name">' + esc(order.productName) + '</div>' +
        '<div class="sub">UDID ' + esc(order.udidMasked) + '</div></div>' +
      '</div>' +
      '<div class="timeline">' + rows + '</div>' +
      '<div class="notice">' + esc(order.hint) + '</div>' +
      (order.instruction
        ? '<div class="card tight"><pre class="instruction">' + esc(order.instruction) + '</pre></div>'
        : '') +
      '<button class="btn btn-secondary" data-action="order-refresh">Обновить статус</button>' +
    '</div>' +
    (order.status === 'done'
      ? '<button class="btn btn-primary" data-action="new-order">Оформить новый заказ</button>'
      : '') +
    chatButton('Чат с продавцом') +
    (order.status === 'done'
      ? '<button class="btn btn-danger btn-sm" data-action="order-close">Закрыть заказ</button>'
      : '') +
    '<button class="btn btn-ghost" data-action="order-help">Помощь по заказу</button>'
  );
}

function viewOrder() {
  const order = state.boot.order;
  // Готовый заказ не должен закрывать дорогу новому.
  if (state.newOrder) return viewCheckout();
  if (!order || (!order.isOpen && order.status !== 'done')) return viewCheckout();
  if (order.status === 'new') return viewPayment(order);
  if (order.status === 'payment_check') return viewWaitingPayment(order);
  if (order.status === 'paid') return viewUdid(order);
  return viewOrderStatus(order);
}

function viewHelp() {
  const b = state.boot;
  // Было перепутано: без SUPPORT_USERNAME кнопка закрывала приложение,
  // а с ним вела в переписку заказа вместо поддержки.
  const support = b.support
    ? '<button class="btn btn-secondary" data-action="support">Написать @' + esc(b.support) + '</button>'
    : '<button class="btn btn-secondary" data-action="chat">💬 Написать продавцу</button>';

  return (
    '<h1>Помощь</h1>' +
    '<div class="card">' +
      '<details class="disclosure"><summary>Как проходит заказ?</summary><div class="body">' +
        'Оплата → вы присылаете UDID → продавец ставит сертификат → вы получаете инструкцию.' +
      '</div></details>' +
      '<details class="disclosure"><summary>Где найти UDID?</summary><div class="body">' +
        'Без компьютера: откройте в Safari сайт udid.tech, нажмите Get My UDID, ' +
        'установите профиль в Настройках — и страница покажет ваш UDID.' +
      '</div>' +
      '<img class="guide" src="/static/img/udid-guide.webp" loading="lazy" ' +
        'alt="Как узнать UDID на iPhone: пошаговые экраны"></details>' +
      '<details class="disclosure"><summary>Что с моими данными?</summary><div class="body">' +
        esc(b.privacy) + '\n\nКоманда /forget в чате бота удаляет заказы и UDID.' +
      '</div></details>' +
      '<button class="btn btn-secondary" data-action="notify">🔔 Настроить уведомления</button>' +
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
      (o.unread ? '<span class="count-badge">' + o.unread + '</span>' : '') +
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
      '<div class="row"><span class="label">Создан</span><span class="value">' + esc(dateOf(o.createdAt)) + '</span></div>' +
      (o.note ? '<div class="row"><span class="label">Заметка</span><span class="value">' + esc(o.note) + '</span></div>' : '') +
      sellerActions(o) +
    '</div>' +
    (state.seller.instruction !== null && o.status === 'installed'
      ? '<div class="card"><label class="field"><span>Текст инструкции</span>' +
        '<textarea id="instrText" placeholder="Что сделать покупателю после установки…">' + esc(state.seller.instruction) + '</textarea></label>' +
        '<button class="btn btn-primary mt" data-action="seller-send-instr">Отправить покупателю</button></div>'
      : '') +
    '<button class="btn btn-secondary" data-action="seller-notify">🔔 Уведомления по заказу</button>' +
    '<button class="btn btn-secondary mt" data-action="seller-chat">💬 Переписка' +
      (o.unread ? ' <span class="count-badge">' + o.unread + '</span>' : '') + '</button>' +
    (o.status !== 'cancelled'
      ? '<button class="btn btn-danger btn-sm mt" data-action="seller-act:cancel">' +
        (o.status === 'done' ? 'Закрыть заказ' : 'Отменить заказ') + '</button>'
      : '')
  );
}

function viewSeller() {
  const s = state.seller;
  if (s.current) return viewSellerOrder(s.current);

  const filters = [['open', 'Открытые'], ['payment_check', 'Проверить оплату'], ['udid', 'Ставить сертификат'],
    ['installed', 'Инструкция'], ['done', 'Готовые'], ['all', 'Все']];

  return (
    '<h1>Заказы</h1>' +
    '<input class="search" id="sellerSearch" type="search" enterkeyhint="search" autocomplete="off" ' +
      'placeholder="Код заказа, UDID или @юзернейм" value="' + esc(s.q) + '">' +
    '<div class="filters">' + filters.map(([key, label]) => {
      const n = key === 'all' || key === 'open' ? '' : ' · ' + (s.counts[key] || 0);
      return '<div class="chip' + (s.status === key ? ' active' : '') + '" data-action="seller-filter:' + key + '">' +
        esc(label) + esc(n) + '</div>';
    }).join('') + '</div>' +
    '<div id="sellerList">' + sellerListHtml() + '</div>'
  );
}

function sellerListHtml() {
  const s = state.seller;
  if (s.orders.length) return s.orders.map(sellerLine).join('');
  return '<div class="notice">' + (s.q.trim() ? 'Ничего не нашли.' : 'Заказов в этом фильтре нет.') + '</div>';
}

let sellerSearchSeq = 0;

async function refreshSellerList() {
  // Перерисовываем только список: поле ввода остаётся на месте, фокус и клавиатура тоже.
  const seq = ++sellerSearchSeq;
  let data;
  try {
    data = await fetchSellerOrders();
  } catch (err) {
    if (seq === sellerSearchSeq) toast(err.message || 'Поиск не удался', true);
    return;
  }
  // Ответ на устаревший запрос не должен перезаписать свежий результат.
  if (seq !== sellerSearchSeq) return;
  state.seller.orders = data.orders;
  state.seller.counts = data.counts || {};
  const list = document.getElementById('sellerList');
  if (list) list.innerHTML = sellerListHtml();
}

/* ------------------------------------------------------------------- каркас */

function navIcon(key) {
  const paths = {
    home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    apps: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    order: '<rect x="5" y="4" width="14" height="17" rx="3"/><path d="M9 3h6v4H9zM9 12h6M9 16h4"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01"/>',
    seller: '<rect x="3" y="7" width="18" height="14" rx="3"/><path d="M8 7V4h8v3M3 12h18M10 12v3h4v-3"/>',
  };
  return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[key] + '</svg>';
}

function tabbar() {
  const b = state.boot;
  const order = b.order;
  const unread = b.unread || 0;
  const alert = unread > 0 || (order && order.isOpen && (order.status === 'paid' || order.status === 'new'));
  const sellerAlert = (b.sellerUnread || 0) > 0;
  const tabs = [
    ['home', 'Главная', '⌂'],
    ['apps', 'Приложения', '▦'],
    ['order', 'Мой заказ', '✈'],
    ['help', 'Помощь', '?'],
  ];
  if (b.user.isSeller) tabs.push(['seller', 'Продавец', '★']);

  return tabs.map(([key, label, icon]) => (
    '<button class="' + (state.view === key ? 'on' : '') + '" data-action="tab:' + key + '">' +
    navIcon(key) +
    ((key === 'order' && alert) || (key === 'seller' && sellerAlert)
      ? '<span class="dot-badge"></span>' : '') +
    '<span>' + esc(label) + '</span></button>'
  )).join('');
}

function screenKey() {
  const o = state.boot && state.boot.order;
  if (state.view === 'order') {
    if (state.newOrder) return 'order:new:' + (state.checkoutApp || 'catalog');
    return 'order:' + (o ? o.id + ':' + o.status : 'none');
  }
  if (state.view === 'seller') return 'seller:' + (state.seller.current ? state.seller.current.id : 'list');
  return state.view;
}

function render() {
  const root = document.getElementById('view');

  if (state.error) {
    root.innerHTML = '<div class="card mt"><h2>Не открылось</h2><p class="muted">' + esc(state.error) +
      '</p><p class="muted">Откройте приложение кнопкой в чате бота.</p></div>';
    return;
  }
  if (!state.boot) return;

  const views = {
    home: viewHome, apps: viewApps, order: viewOrder,
    help: viewHelp, seller: viewSeller, chat: viewChat, notify: viewNotify,
  };
  document.body.classList.toggle('chat-mode', state.view === 'chat');
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
  // Прокрутка наверх — только при переходе на другой экран. Перерисовка того же
  // экрана (ошибка под полем, переключатель, фильтр) не должна уводить страницу.
  const key = screenKey();
  if (key !== state.screen && state.view !== 'chat') window.scrollTo(0, 0);
  state.screen = key;
}

function updateBackButton() {
  if (!tg || !tg.BackButton) return;
  const nested = state.view === 'chat' || state.view === 'notify'
    || (state.view === 'seller' && state.seller.current);
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
      // Старая ошибка к исправленному номеру уже не относится.
      if (state.udidError) {
        state.udidError = null;
        const error = udid.parentElement.querySelector('.field-error');
        if (error) error.remove();
      }
    });
    udid.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      if (state.udid.length === 40 && state.agreeUdid) handleAction('udid-send');
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
      timer = setTimeout(refreshSellerList, 300);
    });
  }

  document.querySelectorAll('[data-toggle]').forEach((box) => {
    box.addEventListener('change', () => {
      const kind = box.getAttribute('data-toggle');
      const enabled = box.checked;
      guard(async () => {
        const data = await api('/api/notify', { method: 'POST', body: { kind, enabled } });
        state.notify.kinds = data.kinds;
        toast(enabled ? 'Уведомления включены' : 'Уведомления выключены');
      });
    });
  });

  const chatInput = document.getElementById('chatInput');
  if (chatInput) {
    chatInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleAction('chat-send');
      }
    });
    chatInput.focus();
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

function fetchSellerOrders() {
  const params = new URLSearchParams({ status: state.seller.status });
  if (state.seller.q.trim()) params.set('q', state.seller.q.trim());
  return api('/api/seller/orders?' + params.toString());
}

async function loadSellerOrders() {
  const data = await fetchSellerOrders();
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
    if (!username) { toast('Контакт поддержки не указан', true); return; }
    if (tg && tg.openTelegramLink) tg.openTelegramLink('https://t.me/' + username);
    else window.open('https://t.me/' + username, '_blank');
  },

  'buy': () => guard(async () => {
    const data = await api('/api/order/create', {
      method: 'POST',
      body: { app: state.checkoutApp },
    });
    state.boot.order = data.order;
    state.newOrder = false;
    state.checkoutApp = null;
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

  // Сервер проверяет номер сам, поэтому отдельный шаг «Проверить» не нужен:
  // раньше после него страница уезжала наверх и кнопку приходилось искать и жать снова.
  'udid-send': () => guard(async () => {
    state.udidError = null;
    let data;
    try {
      data = await api('/api/order/udid', {
        method: 'POST',
        body: { orderId: state.boot.order.id, udid: state.udid },
      });
    } catch (err) {
      // Ошибку показываем под полем: её нужно прочитать и исправить номер.
      state.udidError = err.message;
      haptic('error');
      render();
      return;
    }
    state.boot.order = data.order;
    state.udid = '';
    state.agreeUdid = false;
    toast('UDID передан продавцу');
    render();
  }),

  'order-refresh': () => guard(async () => {
    await refreshOrder();
    toast('Обновлено');
    render();
  }),

  'back-to-order': () => { state.newOrder = false; state.checkoutApp = null; render(); },

  'new-order': () => startCheckout(null),

  'buy-catalog': () => startCheckout(null),

  'open-udid-site': () => {
    if (tg && tg.openLink) tg.openLink('https://udid.tech');
    else window.open('https://udid.tech', '_blank');
  },

  'order-close': () => confirmAction(
    'Закрыть заказ? Он пропадёт с экрана, а инструкция останется в чате бота.',
    () => guard(async () => {
      const data = await api('/api/order/cancel', {
        method: 'POST',
        body: { orderId: state.boot.order.id },
      });
      state.boot.order = data.order;
      state.newOrder = false;
      toast('Заказ закрыт');
      render();
    })
  ),

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

  'chat': () => {
    const order = state.boot.order;
    if (order) { openChat(order.id, 'Заказ ' + order.code, 'order'); return; }
    if (state.boot.support) { actions.support(); return; }
    // Переписка привязана к заказу; закрывать приложение вместо ответа нельзя.
    toast('Переписка откроется после оформления заказа');
  },

  'seller-chat': () => {
    const current = state.seller.current;
    if (current) openChat(current.id, 'Заказ ' + current.code, 'seller');
  },

  'chat-back': () => {
    stopChatPolling();
    state.view = state.chat.from === 'seller' ? 'seller' : 'order';
    guard(async () => {
      if (state.view === 'seller') await loadSellerOrders();
      else await refreshOrder();
      render();
    });
  },

  'chat-send': () => guard(async () => {
    const input = document.getElementById('chatInput');
    const text = (input && input.value || '').trim();
    if (!text) return;
    const data = await api('/api/chat', {
      method: 'POST',
      body: { orderId: state.chat.orderId, text },
    });
    input.value = '';
    state.chat.messages.push(data.message);
    paintChat(true);
    haptic('success');
  }),

  'notify': () => {
    const order = state.boot.order;
    openNotify(order ? order.id : null, 'help');
  },

  'seller-notify': () => {
    const current = state.seller.current;
    if (current) openNotify(current.id, 'seller');
  },

  'notify-back': () => {
    state.view = state.notify.from === 'seller' ? 'seller' : 'help';
    render();
  },

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
  if (kind === 'pick-app') { haptic('light'); startCheckout(value); return; }
  if (kind === 'notify-mode') {
    guard(async () => {
      const data = await api('/api/notify/order', {
        method: 'POST',
        body: { orderId: state.notify.order.id, mode: value },
      });
      state.notify.kinds = data.kinds;
      state.notify.order = data.order;
      render();
      toast('Настройка заказа сохранена');
    });
    return;
  }
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
  const light = isLight();
  document.documentElement.dataset.theme = light ? 'light' : 'dark';
  const bg = light ? '#f4f6ef' : '#0b0d09';
  document.querySelector('meta[name="theme-color"]').content = bg;
  try { if (tg) { tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } } catch (e) { /* старый клиент */ }
}

async function boot() {
  applyTheme();

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.onEvent) tg.onEvent('themeChanged', applyTheme);

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
