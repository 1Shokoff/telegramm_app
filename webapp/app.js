/* Mini App «iApki» — витрина, заказ и панель продавца. */

const tg = window.Telegram && window.Telegram.WebApp;

const state = {
  boot: null,
  view: 'home',
  error: null,
  // Витрину можно открыть и без Telegram — тогда работает только просмотр.
  public: false,
  doc: { key: '', title: '', html: '', from: 'about' },
  filter: 'Все',
  newOrder: false,
  checkoutApp: null,
  screen: '',
  udid: '',
  udidChecked: null,
  udidError: null,
  agreeTerms: false,
  agreeUdid: false,
  // Покупатель нажал «Закрыть заказ» и видит вопрос «Точно закрыть?».
  confirmClose: false,
  // Промокод на оформлении, пока заказа нет: что введено, что сервер насчитал, почему отказ.
  checkoutPromo: { code: '', check: null, error: '' },
  // Промокод на экране оплаты: поле раскрыто, что введено, почему отказ.
  orderPromo: { open: false, code: '', error: '' },
  busy: false,
  // prefs — сортировка и фильтры каждой подгруппы, читаются из памяти устройства при первом обращении.
  seller: {
    status: 'open', q: '', orders: [], counts: {}, current: null,
    total: 0, titles: {}, prefs: null, panel: false,
    // mode — «Заказы», «Аналитика» или «Промокоды»; analytics — последний отчёт, period — выбранный период.
    mode: 'orders', analytics: null, period: null,
    // Промокоды: фильтр по статусу, список, открытая карточка и форма создания или правки.
    promo: { status: 'all', list: [], counts: {}, total: 0, current: null, form: null, confirmDelete: false },
  },
  supplier: { orders: [], current: null },
  chat: { orderId: null, title: '', from: 'order', messages: [], timer: null },
  notify: { kinds: [], order: null, modes: [], from: 'help' },
};

/* Цену у карточек не показываем: доступ покупается один на весь каталог. */
const ACCESS_NOTE = 'Входит в полный доступ';

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
/* ------------------------------------------------------------- движение */

// Системная настройка «уменьшить движение»: тогда ни конфетти, ни счётчиков.
const REDUCED_MOTION = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

/* Числа в карточках аналитики набегают от нуля. Итог сразу стоит в разметке —
   без JS и при «уменьшить движение» он и остаётся. */
function animateNumbers(root) {
  root.querySelectorAll('[data-count]').forEach((el) => {
    const target = Number(el.dataset.count) || 0;
    const fmt = el.dataset.format;
    const show = (v) => { el.textContent = fmt === 'rub' ? rub(v) : fmt === 'pct' ? v + '%' : String(v); };
    if (REDUCED_MOTION || target <= 0) { show(target); return; }
    const start = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - start) / 700);
      show(Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1 && el.isConnected) requestAnimationFrame(step);
    };
    show(0);
    requestAnimationFrame(step);
  });
}

/* Конфетти — на важных шагах заказа: оплата подтверждена, всё готово. */
function celebrate() {
  haptic('success');
  if (REDUCED_MOTION) return;
  const box = document.createElement('div');
  box.className = 'confetti';
  box.setAttribute('aria-hidden', 'true');
  const colors = ['#c8ff31', '#ffffff', '#30d158', '#ffd60a'];
  for (let i = 0; i < 30; i++) {
    const piece = document.createElement('span');
    const angle = (Math.random() * 140 - 70) * Math.PI / 180;
    const dist = 110 + Math.random() * 170;
    piece.style.setProperty('--dx', Math.round(Math.sin(angle) * dist) + 'px');
    piece.style.setProperty('--dy', Math.round(-Math.cos(angle) * dist) + 'px');
    piece.style.setProperty('--rot', Math.round(Math.random() * 720 - 360) + 'deg');
    piece.style.setProperty('--delay', Math.round(Math.random() * 140) + 'ms');
    piece.style.background = colors[i % colors.length];
    box.appendChild(piece);
  }
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 2000);
}

/* Заказ продвинулся с прошлого раза, когда его видели на этом устройстве, —
   празднуем. Первый показ заказа только запоминаем, без конфетти. */
const SEEN_KEY = 'iapki.seen.v1';
const STATUS_RANK = { new: 0, payment_check: 1, paid: 2, udid: 3, installed: 4, done: 5 };

function watchMilestones() {
  const order = state.boot && state.boot.order;
  if (!order || state.public || state.view !== 'order') return;
  let seen = {};
  try { seen = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}') || {}; } catch (e) { seen = {}; }
  const prev = seen[order.code];
  if (prev === order.status) return;
  delete seen[order.code];
  seen[order.code] = order.status;
  const codes = Object.keys(seen);
  if (codes.length > 20) codes.slice(0, codes.length - 20).forEach((c) => delete seen[c]);
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(seen)); } catch (e) { /* без памяти — без праздника */ }
  const before = prev === undefined ? undefined : STATUS_RANK[prev];
  if (before !== undefined && STATUS_RANK[order.status] > before &&
      (order.status === 'paid' || order.status === 'done')) {
    setTimeout(celebrate, 250);
  }
}

/* Кнопка копирования отвечает галочкой-вспышкой, а не только тостом. */
function flashCopied(key) {
  document.querySelectorAll('[data-action="copy:' + key + '"]').forEach((el) => {
    el.classList.remove('copied');
    void el.offsetWidth;
    el.classList.add('copied');
    setTimeout(() => el.classList.remove('copied'), 1200);
  });
}

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

function appsCount(n) {
  const tail = n % 100;
  const last = n % 10;
  let word = 'приложений';
  if (tail < 11 || tail > 14) {
    if (last === 1) word = 'приложение';
    else if (last >= 2 && last <= 4) word = 'приложения';
  }
  return n + ' ' + word;
}

function appBySlug(slug) {
  return (state.boot.catalog || []).find((a) => a.slug === slug);
}

function iconHtml(app, extraClass) {
  if (!app) return '';
  const cls = 'app-icon' + (app.dark ? ' dark' : '') + (extraClass ? ' ' + extraClass : '');
  // Адрес иконки даёт сервер (своя картинка или вынутая из сборки),
  // буква остаётся запасным вариантом.
  const img = app.iconUrl
    ? '<img src="' + esc(app.iconUrl) + '" alt="" loading="lazy" onerror="this.remove()">'
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

/* Версия и минимальная iOS берутся из самой сборки, лежащей на сервере. */
function buildMeta(build) {
  const parts = [];
  if (build.version) parts.push('Версия ' + build.version);
  if (build.minOs) parts.push('iOS ' + build.minOs + '+');
  return parts.length ? '<span class="app-meta">' + esc(parts.join(' · ')) + '</span>' : '';
}

function appCard(app, index) {
  const build = app.build;
  // --i задаёт задержку появления: карточки выкладываются волной.
  // Карточка ведёт на ту же оплату, что и кнопка «Весь каталог»:
  // отдельных цен по приложениям нет, доступ один на весь каталог.
  return (
    '<div class="app-card" style="--i:' + (index || 0) + '">' +
      '<button type="button" class="app-open" data-action="buy-catalog" ' +
        'aria-label="' + esc(app.name) + ' — оформить доступ ко всему каталогу">' +
      '<span class="arrow">↗</span>' +
      iconHtml(app) +
      '<span class="name">' + esc(app.name) + '</span>' +
      '<span class="cat">' + esc(app.category) + '</span>' +
      (build ? buildMeta(build) : '') +
      (app.desc ? '<span class="desc">' + esc(app.desc) + '</span>' : '') +
      '<span class="price">' + ACCESS_NOTE + '</span>' +
      '</button>' +
      (build
        ? '<a class="app-get" href="' + esc(build.url) + '" ' +
            'data-action="ipa:' + esc(app.slug) + '">↓ .ipa · ' +
            esc(build.sizeText) + '</a>'
        : '') +
    '</div>'
  );
}

/* Акция: обычная цена зачёркнута рядом с ценой по акции. Данные приходят
   с сервера (product.promo), сроки и подпись задаются в .env. */
function promoOf() {
  return (state.boot && state.boot.product && state.boot.product.promo) || null;
}

function priceHtml(priceText, oldPriceText) {
  if (!oldPriceText) return esc(priceText);
  return '<s class="old-price">' + esc(oldPriceText) + '</s> <span class="new-price">' + esc(priceText) + '</span>';
}

function promoPill(promo) {
  if (!promo) return '';
  return '<div class="promo-pill"><span class="promo-flame" aria-hidden="true">🔥</span> ' + esc(promo.label) +
    (promo.untilText ? ' · ' + esc(promo.untilText) : '') + '</div>';
}

function viewHome() {
  const b = state.boot;
  const promo = promoOf();
  const catalogPrice = priceHtml(b.product.priceText, promo && promo.oldPriceText);
  const featured = (b.featured || []).map(appBySlug).filter(Boolean);
  const cats = ['Все'].concat(b.categories || []);
  const list = state.filter === 'Все'
    ? b.catalog
    : b.catalog.filter((a) => a.category === state.filter);

  return (
    '<section class="hero">' +
      '<div class="hero-head">' +
        '<span class="eyebrow">Приложения для iPhone</span>' +
        '<span class="badge">iOS</span>' +
      '</div>' +
      promoPill(promo) +
      '<h1>Твои приложения.<br><em>Снова на iPhone.</em></h1>' +
      '<p>Весь каталог за ' + catalogPrice + '. Без лимита установок и доплат за приложения.</p>' +
      '<div class="cluster">' + featured.map((a) => iconHtml(a)).join('') + '</div>' +
      '<button class="btn btn-primary" data-action="buy-catalog">Весь каталог · ' + esc(b.product.priceText) + ' →</button>' +
      '<div class="cta-note">Один платёж · все приложения каталога</div>' +
    '</section>' +
    flowline(0) +
    '<div class="section-head">' +
      '<h2>Каталог</h2>' +
      '<span class="muted">' + appsCount(b.product.appsCount) + '</span>' +
    '</div>' +
    '<div class="filters">' + cats.map((c) => (
      '<button class="chip' + (c === state.filter ? ' active' : '') + '" aria-pressed="' + (c === state.filter) + '" data-action="filter:' + esc(c) + '">' + esc(c) + '</button>'
    )).join('') + '</div>' +
    '<div class="grid">' + list.map(appCard).join('') + '</div>' +
    '<div class="cta-note mt">Цена не зависит от числа приложений: ' +
      catalogPrice + ' — это установка на одно устройство, ' +
      'хоть одного приложения, хоть всего каталога.</div>' +
    '<div class="mt"><button class="btn btn-primary" data-action="buy-catalog">' +
      (state.public ? 'Оформить в Telegram' : 'Весь каталог · ' + esc(b.product.priceText)) + '</button></div>'
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
  // Скидка действует на общую цену; у приложения со своей ценой её нет.
  const promo = promoOf();
  const oldPrice = promo && (app ? app.oldPriceText : promo.oldPriceText);
  // Промокод проверен сервером: цена с ним — та, что будет в заказе.
  const pc = state.checkoutPromo.check;

  return (
    stepper(1) +
    '<h1>Оформление заказа</h1>' +
    '<div class="card">' +
      '<div class="product">' +
        (app ? iconHtml(app) : '<span class="app-icon" style="background:var(--surface-3)"><span>📱</span></span>') +
        '<div><div class="name">' + esc(app ? app.name : 'Весь каталог') + '</div>' +
        '<div class="sub">' + esc(app ? app.category : 'Все ' + appsCount(b.product.appsCount)) +
        ' · UDID укажете следующим шагом</div></div>' +
      '</div>' +
      (app && app.desc ? '<div class="product-desc">' + esc(app.desc) + '</div>' : '') +
      '<div class="row"><span class="label">Цена</span><span class="value">' + priceHtml(price, oldPrice) + '</span></div>' +
      (oldPrice
        ? '<div class="row"><span class="label">Скидка</span><span class="value promo-value">' +
            esc(promo.label) + ' · −' + esc(promo.discountText) + '</span></div>'
        : '') +
      (pc
        ? '<div class="row"><span class="label">Промокод ' + esc(pc.code) + '</span>' +
            '<span class="value promo-value">−' + esc(pc.discountText) + '</span></div>' +
          '<div class="row"><span class="label">К оплате</span><span class="value new-price">' + esc(pc.priceText) + '</span></div>'
        : '') +
      '<div class="row"><span class="label">Оплата</span><span class="value">' + esc(modeText) + '</span></div>' +
      (b.payment.mode !== 'stars' ? promoCodeBlock('checkout', pc && pc.code) : '') +
      (b.payment.mode === 'stars'
        ? '<div class="row"><span class="label">Сумма в Stars</span><span class="value">' + (b.payment.stars || '—') + '</span></div>'
        : '') +
      '<details class="disclosure"><summary>Что входит в услугу</summary>' +
        '<div class="body">Мы ставим на ваш iPhone выбранные приложения и профиль подписи, ' +
        'без которого они не запустятся. В цену входит установка любых приложений каталога ' +
        'на одно устройство.\n\n' +
        'Порядок: оплата → вы присылаете UDID устройства → мы выполняем установку → ' +
        'вы получаете инструкцию в чате бота. Срок — как правило, в течение рабочего дня ' +
        'после получения UDID.\n\n' + esc(b.privacy) + '</div>' +
      '</details>' +
      '<label class="check"><input type="checkbox" id="agreeTerms"' + (state.agreeTerms ? ' checked' : '') + '>' +
        '<span>Соглашаюсь с условиями и даю согласие на обработку данных.</span></label>' +
      docLinksHtml() +
      '<button class="btn btn-primary" id="buyBtn" data-action="buy"' + (state.agreeTerms ? '' : ' disabled') + '>' +
        'Оформить заказ и перейти к оплате</button>' +
    '</div>' +
    (state.newOrder && hasOrderScreen(b.order)
      ? '<button class="btn btn-ghost btn-sm" data-action="back-to-order">Вернуться к заказу ' +
        esc(b.order.code) + '</button>'
      : '')
  );
}

/* --------------------------------------------------------------- оплата */

function copyIcon() {
  return (
    '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<rect x="9" y="9" width="11" height="11" rx="2.5"/>' +
      '<path d="M5 15V6a2 2 0 0 1 2-2h8"/>' +
    '</svg>'
  );
}

function copyButton(key, label) {
  return (
    '<button type="button" class="req-copy" data-action="copy:' + esc(key) + '" ' +
      'aria-label="Скопировать: ' + esc(label) + '">' + copyIcon() + '</button>'
  );
}

function requisiteRow(row) {
  return (
    '<div class="req-row">' +
      (row.iconUrl
        ? '<span class="req-icon"><img src="' + esc(row.iconUrl) + '" alt="" loading="lazy"></span>'
        : '') +
      '<span class="req-text">' +
        '<span class="req-label">' + esc(row.label) + '</span>' +
        '<span class="req-value">' + esc(row.value) + '</span>' +
      '</span>' +
      (row.copy ? copyButton(row.key, row.label) : '') +
    '</div>'
  );
}

/* Ещё один чек к заказу, который уже на проверке: например, PDF вдогонку к скриншоту. */
function receiptBlock() {
  // Поле выбора файла — в конце: между двумя кнопками оно съедало отступ .btn + .btn.
  return (
    '<button class="btn btn-secondary" data-action="receipt-pick">🧾 Прикрепить ещё чек</button>' +
    '<div class="upload-bar" id="uploadBar" hidden><span></span></div>' +
    '<div class="cta-note">Скриншот перевода или чек из банка — продавец увидит его ' +
      'в заказе. Можно выбрать несколько файлов, вместе до 15 МБ. Ссылку на документ ' +
      'можно прислать сообщением в чат.</div>' +
    '<input type="file" id="receiptFile" multiple hidden>'
  );
}

/* Без чека оплата не заявляется: заказ уходит на проверку в тот момент,
   когда покупатель прикладывает скриншот, PDF или ссылку на чек. Так продавцу
   не приходится искать платежи, которых не было. */
function proofBlock() {
  return (
    '<div class="proof">' +
      '<div class="proof-title">Подтвердите оплату</div>' +
      '<div class="proof-note">После перевода прикрепите чек — скриншот, PDF или другие файлы ' +
        '(можно несколько, вместе до 15 МБ) либо ссылку. Без чека заказ не уйдёт на проверку.</div>' +
      '<input type="file" id="receiptFile" multiple hidden>' +
      '<button class="btn btn-primary" data-action="receipt-pick">📎 Я оплатил — прикрепить чек</button>' +
      '<div class="upload-bar" id="uploadBar" hidden><span></span></div>' +
      '<div class="proof-link">' +
        '<input class="search" id="receiptLink" type="url" inputmode="url" autocomplete="off" ' +
          'enterkeyhint="send" placeholder="или ссылка на чек" aria-label="Ссылка на чек">' +
        '<button class="btn btn-secondary btn-sm" data-action="receipt-link">Отправить</button>' +
      '</div>' +
    '</div>'
  );
}

/* Заказ оформлен по цене акции — показываем, какой была цена без скидки.
   С промокодом сравниваем цену до него: акция и промокод складываются. */
function orderPromo(order) {
  const promo = promoOf();
  if (!promo || !order) return null;
  const base = order.basePrice != null ? order.basePrice : order.price;
  return base === state.boot.product.price ? promo : null;
}

/* Зачёркнутая цена у заказа: обычная — во время акции, иначе цена до промокода. */
function orderOldPrice(order) {
  const sale = orderPromo(order);
  if (sale) return sale.oldPriceText;
  return order.promoActive ? order.basePriceText : null;
}

/* Поле промокода: на оформлении (заказа ещё нет) и на экране оплаты.
   applied — код уже за заказом или проверен: тогда вместо поля — «Убрать». */
function promoCodeBlock(where, applied) {
  if (applied) {
    return (
      '<div class="promo-applied"><span>🎟 Промокод <b>' + esc(applied) + '</b> применён</span>' +
        '<button type="button" class="linkish" data-action="bp-remove:' + where + '">Убрать</button></div>'
    );
  }
  const s = where === 'checkout' ? state.checkoutPromo : state.orderPromo;
  const id = 'promoInput-' + where;
  return (
    '<div class="promo-box">' +
      '<label class="promo-label" for="' + id + '">Промокод</label>' +
      '<div class="input-row">' +
        '<input id="' + id + '" class="search promo-input" value="' + esc(s.code) + '" maxlength="40" ' +
          'placeholder="Если есть" autocapitalize="characters" autocomplete="off" autocorrect="off" ' +
          'spellcheck="false" enterkeyhint="done">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-action="bp-apply:' + where + '">Применить</button>' +
      '</div>' +
      (s.error ? '<div class="field-error">' + esc(s.error) + '</div>' : '') +
    '</div>'
  );
}

/* На экране оплаты поле не мешает: сначала — ссылка «Есть промокод?». */
function orderPromoSection(order) {
  if (order.paymentMode === 'stars') return '';
  if (order.promoActive) return promoCodeBlock('order', order.promoCode);
  if (!state.orderPromo.open) {
    return '<button type="button" class="linkish promo-open" data-action="bp-open">🎟 У меня есть промокод</button>';
  }
  return promoCodeBlock('order');
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
    action = proofBlock();
  }

  return (
    stepper(1) +
    '<h1>Оплата заказа</h1>' +
    '<p class="muted">Заказ ' + esc(order.code) + ' · ' + esc(order.productName) + ' · ' +
      priceHtml(order.priceText, orderOldPrice(order)) + '</p>' +
    '<div class="card">' +
      orderPromoSection(order) +
      (mode === 'manual' ? paymentDetails(order) : '<div class="notice">' + esc(order.hint) + '</div>') +
      action +
    '</div>' +
    '<button class="btn btn-danger btn-sm" data-action="order-cancel">Отменить заказ</button>'
  );
}

function paymentDetails(order) {
  const b = state.boot;
  const rows = b.payment.requisites || [];
  const note = b.payment.details
    ? '<div class="body mt" style="white-space:pre-wrap">' + esc(b.payment.details) + '</div>'
    : '';

  if (!rows.length) {
    return (
      (b.payment.details
        ? '<div class="body" style="white-space:pre-wrap">' + esc(b.payment.details) + '</div>'
        : '<div class="body">Реквизиты уточните у продавца.</div>') +
      '<div class="requisites">' + amountRow(order) + '</div>' +
      paymentComment(order)
    );
  }
  return (
    '<div class="requisites">' + rows.map(requisiteRow).join('') + amountRow(order) + '</div>' +
    note + paymentComment(order)
  );
}

function amountRow(order) {
  // Сумма из .env важнее цены заказа: продавец может попросить другую.
  // Но о промокоде она не знает — с ним платят цену заказа.
  const fixed = !order.promoActive && state.boot.payment && state.boot.payment.amount;
  const text = fixed || order.priceText;
  const sale = fixed ? null : orderPromo(order);
  const parts = [];
  if (sale) parts.push(sale.label);
  if (order.promoActive) parts.push('промокод ' + order.promoCode);
  return (
    '<div class="req-row">' +
      '<span class="req-text">' +
        '<span class="req-label">Сумма к оплате</span>' +
        '<span class="req-value">' + esc(text) + '</span>' +
        (parts.length ? '<span class="req-note">Скидка уже учтена: ' + esc(parts.join(' и ')) + '</span>' : '') +
      '</span>' +
      copyButton('amount', 'сумму') +
    '</div>'
  );
}

function paymentComment(order) {
  // Номер заказа — тоже реквизит: без него продавец не поймёт, чей это перевод.
  return (
    '<div class="req-row comment">' +
      '<span class="req-text">' +
        '<span class="req-label">Комментарий к платежу</span>' +
        '<span class="req-value">' + esc(order.code) + '</span>' +
      '</span>' +
      copyButton('code', 'номер заказа') +
    '</div>'
  );
}

function viewWaitingPayment(order) {
  return (
    stepper(1) +
    '<div class="pill">⏳ Оплата на проверке</div>' +
    '<h1>Проверяем платёж</h1>' +
    '<p class="muted">Заказ ' + esc(order.code) + ' · ' + esc(order.productName) +
      '. Продавец подтвердит поступление — после этого попросим UDID.</p>' +
    '<div class="card">' +
      // Подсказка общая с ботом: продавец проверяет вручную и может ответить не сразу.
      '<div class="notice notice-top">' + esc(order.hint) + '</div>' +
      '<button class="btn btn-secondary" data-action="order-refresh">Обновить статус</button>' +
      (order.paymentMode === 'manual' ? receiptBlock() : '') +
    '</div>' +
    chatButton('Чат с продавцом') +
    '<button class="btn btn-ghost btn-sm" data-action="order-help">Помощь по заказу</button>'
  );
}

function viewUdid(order) {
  const b = state.boot;
  const len = state.udid.length;
  const ready = len > 0 && state.agreeUdid && !state.busy;

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
      '4. После установки откроется страница с вашим UDID — скопируйте его</div>' +
      '<button class="btn btn-secondary btn-sm mt" data-action="open-udid-site">' +
        'Открыть udid.tech</button>' +
      '<img class="guide" src="/static/img/udid-guide.webp" loading="lazy" ' +
        'alt="Как узнать UDID на iPhone: пошаговые экраны">' +
    '</details>' +
    '<div class="field">' +
      '<label for="udidInput">UDID устройства</label>' +
      '<input id="udidInput" class="udid" autocomplete="off" autocapitalize="off" spellcheck="false" ' +
      'enterkeyhint="send" maxlength="40" ' +
      'placeholder="Вставьте UDID" value="' + esc(state.udid) + '">' +
      '<div class="field-foot"><span>Можно вставить из буфера</span>' +
      '<span class="count" id="udidCount">' + len + ' / 40</span></div>' +
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
  const shown = state.chat.shown || 0;
  return list.map((m, i) => (
    '<div class="msg' + (m.mine ? ' mine' : '') + (i >= shown ? ' msg-new' : '') + '">' +
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
  state.chat.shown = state.chat.messages.length;
  if (scroll !== false) window.scrollTo(0, document.body.scrollHeight);
}

async function loadChat(scroll) {
  const data = await api('/api/chat?orderId=' + state.chat.orderId);
  const changed = data.messages.length !== state.chat.messages.length;
  state.chat.messages = data.messages;
  if (state.boot) {
    state.boot.unread = 0;
    const row = (state.seller.orders || []).find((o) => o.id === state.chat.orderId);
    // Тестовые заказы в общий счётчик не входят — и вычитать их из него нечего.
    if (row && row.unread && !row.isTest) {
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
  state.chat.shown = 0;
  state.view = 'chat';
  render();
  guard(async () => {
    await loadChat(true);
    startChatPolling();
  });
}

function openBot() {
  const url = state.boot && state.boot.botUrl;
  if (!url) { toast('Откройте бота iApki в Telegram', true); return; }
  if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
  else window.open(url, '_blank');
}

function startCheckout(slug) {
  // В браузере заказ не оформить: покупка идёт через бота.
  if (state.public) { openBot(); return; }
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
  state.checkoutPromo = { code: '', check: null, error: '' };
  state.view = 'order';
  render();
}

function chatButton(label) {
  const unread = state.boot && state.boot.unread ? state.boot.unread : 0;
  const badge = unread ? ' <span class="count-badge">' + unread + '</span>' : '';
  return '<button class="btn btn-secondary" data-action="chat">' + esc(label) + badge + '</button>';
}

/* ---------------------------------------------------------- инструкция */

/* Упоминания ботов в тексте делаем ссылками: покупателю не нужно искать
   их вручную. Текст берётся из нашего же шаблона, поэтому подстановка
   безопасна — экранируем до разбора. */
function withBotLinks(text) {
  return esc(text).replace(/@([A-Za-z0-9_]{4,32})/g, (match, name) => (
    '<a class="instr-mention" href="https://t.me/' + name + '" data-action="bot:' + name + '">' +
    match + '</a>'
  ));
}

/* Карточка шага повторяет слайд инструкции: шапка со счётчиком, номер,
   заголовок, кадр переписки, подпись и полоса пройденных шагов. */
function instructionStep(step, total) {
  const num = ('0' + step.number).slice(-2);
  const dots = [];
  for (let i = 1; i <= total; i++) {
    dots.push('<span class="instr-tick' + (i <= step.number ? ' on' : '') + '"></span>');
  }
  return (
    '<li class="instr-card">' +
      '<div class="instr-card-top">' +
        '<span class="instr-logo">iApki</span>' +
        '<span class="instr-count">' + num + ' / ' + total + '</span>' +
      '</div>' +
      '<div class="instr-head">' +
        '<span class="instr-num">' + num + '</span>' +
        '<span class="instr-title">' + esc(step.title) + '</span>' +
      '</div>' +
      '<div class="instr-sub">' + withBotLinks(step.subtitle) + '</div>' +
      '<div class="instr-shot">' +
        '<img src="' + esc(step.image) + '" loading="lazy" alt="Шаг ' + step.number +
          ' из ' + total + ': ' + esc(step.title) + '">' +
      '</div>' +
      '<div class="instr-hint">' + withBotLinks(step.hint) + '</div>' +
      '<div class="instr-sign">@iApkibot</div>' +
      '<div class="instr-ticks">' + dots.join('') + '</div>' +
    '</li>'
  );
}

/* Режим разработчика — до всех шагов: без него iOS 16 и новее не запустит
   приложение, подписанное сертификатом разработчика. */
function developerModeBlock(dev) {
  if (!dev) return '';
  return (
    '<div class="instr-dev">' +
      '<div class="instr-dev-title">⚙️ ' + esc(dev.title) + '</div>' +
      '<ol class="instr-dev-steps">' +
        dev.steps.map((step) => '<li>' + esc(step) + '</li>').join('') +
      '</ol>' +
      '<img class="instr-dev-img" src="' + esc(dev.image) + '" loading="lazy" ' +
        'alt="Настройки → Конфиденциальность и безопасность → Режим разработчика">' +
      (dev.note ? '<div class="instr-dev-note">' + esc(dev.note) + '</div>' : '') +
    '</div>'
  );
}

function instructionBlock(order) {
  const data = (state.boot && state.boot.instruction) || { steps: [], bots: [] };
  if (!data.steps.length) return '';

  const bots = data.bots.map((bot) => (
    '<a class="instr-bot" href="' + esc(bot.url) + '" data-action="bot:' + esc(bot.username) + '">' +
      '<span class="instr-bot-name">@' + esc(bot.username) + '</span>' +
      '<span class="instr-bot-hint">' + esc(bot.hint) + '</span>' +
      '<span class="instr-bot-go">↗</span>' +
    '</a>'
  )).join('');

  return (
    '<div class="section-head mt"><h2>Инструкция по установке</h2>' +
      '<span class="badge">' + data.steps.length + ' шагов</span></div>' +
    '<div class="card instr">' +
      developerModeBlock(data.developerMode) +
      '<p class="muted">Всё делается в двух ботах: сначала получаем сертификат, ' +
        'потом подписываем и ставим приложение.</p>' +
      '<div class="instr-bots">' + bots + '</div>' +
      (data.sources
        ? '<div class="instr-sources">' +
            '<div class="instr-sources-title">📌 ' + esc(data.sources.title) + '</div>' +
            '<div class="instr-sources-text">' + withBotLinks(data.sources.text) + '</div>' +
          '</div>'
        : '') +
      (order.hasUdid
        ? '<div class="req-row"><span class="req-text">' +
            '<span class="req-label">Ваш UDID</span>' +
            '<span class="req-value mono">' + esc(order.udidMasked) + '</span>' +
          '</span></div>'
        : '') +
      '<div class="instr-swipe">Листайте шаги вбок: свайпом или мышью</div>' +
      '<ol class="instr-steps" tabindex="0" aria-label="Шаги инструкции">' +
        data.steps.map((step) => instructionStep(step, data.steps.length)).join('') +
      '</ol>' +
    '</div>'
  );
}

function viewOrderStatus(order) {
  const step = order.step;
  const rows = STEP_LABELS.map((pair, i) => {
    const n = i + 1;
    const cls = step > n ? 'tl done' : (step === n ? 'tl active' : 'tl pending');
    const mark = step > n ? '✓' : String(n);
    // Последний шаг после отправки объясняет, где искать саму инструкцию.
    const sub = n === 4 && order.instructionReady ? 'Инструкция ниже: листайте шаги' : pair[1];
    return (
      '<div class="' + cls + '"><div class="dot">' + mark + '</div>' +
      '<div><div class="t">' + esc(pair[0]) + '</div>' +
      '<div class="s">' + esc(sub) + '</div></div></div>'
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
      // Пока продавец не отправил инструкцию, на четвёртом шаге видна подсказка,
      // а после отправки на её месте — сама инструкция со скриншотами.
      (order.instructionReady ? '' : '<div class="notice">' + esc(order.hint) + '</div>') +
      '<button class="btn btn-secondary" data-action="order-refresh">Обновить статус</button>' +
    '</div>' +
    (order.instructionReady ? instructionBlock(order) : '') +
    (order.status === 'done'
      ? '<button class="btn btn-primary" data-action="new-order">Оформить новый заказ</button>' +
        closeOrderBlock(order)
      : '') +
    chatButton('Чат с продавцом') +
    '<button class="btn btn-ghost" data-action="order-help">Помощь по заказу</button>'
  );
}

/* Вопрос «Закрыть заказ?» задаём прямо на странице. Системное окно Telegram
   (showConfirm) есть не во всех клиентах: где его нет, кнопка молчала
   и заказ было не закрыть. */
function closeOrderBlock(order) {
  if (!state.confirmClose) {
    return '<button class="btn btn-secondary" data-action="order-close">Закрыть заказ</button>';
  }
  return (
    '<div class="card confirm-box">' +
      '<div class="confirm-title">Закрыть заказ ' + esc(order.code) + '?</div>' +
      '<div class="confirm-text">Он пропадёт с этого экрана, а инструкция останется в чате бота.</div>' +
      '<div class="btn-row">' +
        '<button class="btn btn-secondary" data-action="order-close-no">Оставить</button>' +
        '<button class="btn btn-danger" data-action="order-close-yes">Закрыть</button>' +
      '</div>' +
    '</div>'
  );
}

/* Есть ли у заказа свой экран: открытый или выполненный, пока покупатель
   его не закрыл. Отменённый и закрытый уступают место оформлению нового. */
function hasOrderScreen(order) {
  return Boolean(order) && !order.closed && (order.isOpen || order.status === 'done');
}

function viewOrder() {
  const order = state.boot.order;
  // Готовый заказ не должен закрывать дорогу новому.
  if (state.newOrder) return viewCheckout();
  if (!hasOrderScreen(order)) return viewCheckout();
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
      // Уведомления настраиваются только внутри Telegram: в браузере
      // сервер не знает, чей это аккаунт.
      (state.public
        ? ''
        : '<button class="btn btn-secondary" data-action="notify">🔔 Настроить уведомления</button>') +
      '<button class="btn btn-secondary mt" data-action="tab:about">О нас · документы и реквизиты</button>' +
      support +
    '</div>'
  );
}

/* ----------------------------------------------------------------- о нас */

function docs() {
  return (state.boot && state.boot.about && state.boot.about.docs) || [];
}

function docLinksHtml() {
  const ready = docs().filter((d) => d.url);
  if (!ready.length) return '';
  return (
    '<div class="cta-note">' + ready.map((d) => (
      '<button class="linkish" data-action="doc:' + esc(d.key) + '">' + esc(d.title) + '</button>'
    )).join(' · ') + '</div>'
  );
}

function footerHtml() {
  const about = (state.boot && state.boot.about) || {};
  const links = ['<button class="linkish" data-action="tab:about">О нас</button>'];
  docs().forEach((d) => {
    if (d.url) links.push('<button class="linkish" data-action="doc:' + esc(d.key) + '">' + esc(d.title) + '</button>');
  });

  const contacts = [];
  if (about.email) contacts.push(esc(about.email));
  if (about.phone) contacts.push(esc(about.phone));
  if (state.boot && state.boot.support) contacts.push('@' + esc(state.boot.support));

  return (
    '<footer class="footer">' +
      '<div class="footer-links">' + links.join('') + '</div>' +
      (contacts.length ? '<div class="footer-line">' + contacts.join(' · ') + '</div>' : '') +
      '<div class="footer-line legal">' +
        (about.legalLine ? esc(about.legalLine) : 'Реквизиты продавца появятся здесь до открытия продаж.') +
      '</div>' +
    '</footer>'
  );
}

function viewDoc() {
  const d = state.doc;
  return (
    '<button class="backlink" data-action="doc-back">← Назад</button>' +
    '<div class="doc">' + (d.html ? d.html : '<div class="muted">Загружаем документ…</div>') + '</div>'
  );
}

function openDoc(key) {
  const doc = docs().find((d) => d.key === key);
  if (!doc || !doc.url) { toast('Документ готовим', true); return; }
  // Чужую ссылку из .env открываем снаружи, свой документ — прямо здесь.
  if (/^https?:/i.test(doc.url)) {
    if (tg && tg.openLink) tg.openLink(doc.url);
    else window.open(doc.url, '_blank');
    return;
  }
  state.doc = { key, title: doc.title, html: '', from: state.view === 'doc' ? state.doc.from : state.view };
  state.view = 'doc';
  render();
  guard(async () => {
    const data = await api('/api/doc/' + encodeURIComponent(key));
    if (state.view === 'doc' && state.doc.key === key) {
      state.doc.html = data.html;
      render();
    }
  });
}

function docRow(doc) {
  if (!doc.url) {
    return (
      '<div class="doc-row pending">' +
        '<span class="doc-text"><span class="doc-title">' + esc(doc.title) + '</span>' +
        '<span class="doc-hint">' + esc(doc.hint) + '</span></span>' +
        '<span class="doc-flag">готовим</span>' +
      '</div>'
    );
  }
  return (
    '<button type="button" class="doc-row" data-action="doc:' + esc(doc.key) + '">' +
      '<span class="doc-text"><span class="doc-title">' + esc(doc.title) + '</span>' +
      '<span class="doc-hint">' + esc(doc.hint) + '</span></span>' +
      '<span class="doc-flag open">↗</span>' +
    '</button>'
  );
}

function contactRows(about) {
  const rows = [];
  if (about.email) rows.push(['Почта', about.email]);
  if (about.phone) rows.push(['Телефон', about.phone]);
  if (state.boot.support) rows.push(['Telegram', '@' + state.boot.support]);
  if (!rows.length) return '<div class="muted">Появятся здесь до открытия продаж.</div>';
  return rows.map(([label, value]) => (
    '<div class="row"><span class="label">' + esc(label) + '</span>' +
    '<span class="value">' + esc(value) + '</span></div>'
  )).join('');
}

function viewAbout() {
  const b = state.boot;
  const about = b.about || { docs: [] };
  const legal = about.legalLine || '';

  return (
    '<h1>О нас</h1>' +
    '<p class="muted">Сервис iApki ставит на iPhone приложения, которых нет ' +
      'в App Store. Оплата, UDID, установка сертификата, инструкция — весь путь ' +
      'проходит здесь и в чате бота.</p>' +
    '<div class="card">' +
      '<div class="row"><span class="label">Каталог</span>' +
        '<span class="value">' + appsCount(b.product.appsCount) + '</span></div>' +
      '<div class="row"><span class="label">Цена</span>' +
        '<span class="value">' + priceHtml(b.product.priceText, promoOf() && promoOf().oldPriceText) + '</span></div>' +
    '</div>' +
    flowline(0) +
    '<div class="section-head"><h2>Документы</h2></div>' +
    '<div class="card tight">' + (about.docs || []).map(docRow).join('') + '</div>' +
    '<div class="section-head"><h2>Контакты</h2></div>' +
    '<div class="card">' + contactRows(about) + '</div>' +
    '<div class="section-head"><h2>Реквизиты</h2></div>' +
    '<div class="card">' +
      (legal
        ? '<div class="legal">' + esc(legal) + '</div>'
        : '<div class="muted">Появятся здесь до открытия продаж.</div>') +
    '</div>' +
    '<div class="cta-note mt">' + esc(b.privacy) + '</div>'
  );
}

/* -------------------------------------------------------------- продавец */

/* Заказ тестировщика из TEST_IDS: в списках и карточке — пометка 🧪. */
function testTag(o) {
  return o.isTest ? ' <span class="test-tag">🧪 тест</span>' : '';
}

function sellerLine(o) {
  return (
    '<button class="order-line" data-action="seller-open:' + o.id + '">' +
      '<span class="status-dot ' + esc(o.status) + '"></span>' +
      '<span class="grow"><span class="code">' + esc(o.code) + '</span>' + testTag(o) + '<br>' +
      '<span class="meta">' + esc(o.statusTitle) + ' · ' + esc(o.priceText) + (o.promoActive ? ' 🎟' : '') +
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
    // Одна кнопка закрывает заказ: покупателю сразу уходит готовая инструкция.
    buttons.push('<button class="btn btn-primary btn-sm" data-action="seller-act:installed">Сертификат установлен</button>');
  } else if (o.status === 'installed') {
    // Заказы, застрявшие на этом статусе до объединения шагов.
    buttons.push('<button class="btn btn-primary btn-sm" data-action="seller-act:installed">Отправить инструкцию</button>');
  }
  if (!buttons.length) return '';
  return '<div class="btn-row mt">' + buttons.join('') + '</div>';
}

/* UDID в карточке заказа копируется нажатием на сам номер: так его
   быстрее всего переложить в бота регистрации устройств. */
function udidValue(udid, key) {
  if (!udid) return '<span class="value mono">—</span>';
  return (
    '<button type="button" class="value mono copyable" data-action="copy:' + key + '" ' +
      'aria-label="Скопировать UDID">' + esc(udid) + ' ' + copyIcon() + '</button>'
  );
}

function viewSellerOrder(o) {
  return (
    '<button class="backlink" data-action="seller-back">← Все заказы</button>' +
    '<div class="section-head"><h2>' + esc(o.code) + testTag(o) + '</h2>' +
      '<span class="badge">' + esc(o.statusTitle) + '</span></div>' +
    '<div class="card">' +
      '<div class="row"><span class="label">Покупатель</span><span class="value">' +
        esc(o.firstName || '—') + (o.username ? ' @' + esc(o.username) : '') + '</span></div>' +
      '<div class="row"><span class="label">Telegram ID</span><span class="value mono">' + esc(o.userId) + '</span></div>' +
      '<div class="row"><span class="label">Сумма</span><span class="value">' + esc(o.priceText) + '</span></div>' +
      (o.promoCode
        ? '<div class="row"><span class="label">Промокод</span><span class="value">' + esc(o.promoCode) +
            ' · −' + esc(o.promoDiscountText) + (o.promoActive ? '' : ' · применение возвращено') + '</span></div>'
        : '') +
      '<div class="row"><span class="label">UDID</span>' + udidValue(o.udid, 'seller-udid') + '</div>' +
      '<div class="row"><span class="label">Создан</span><span class="value">' + esc(dateOf(o.createdAt)) + '</span></div>' +
      (o.note ? '<div class="row"><span class="label">Заметка</span><span class="value">' + esc(o.note) + '</span></div>' : '') +
      sellerActions(o) +
    '</div>' +
    '<button class="btn btn-secondary" data-action="seller-notify">🔔 Уведомления по заказу</button>' +
    '<button class="btn btn-secondary mt" data-action="seller-chat">💬 Переписка' +
      (o.unread ? ' <span class="count-badge">' + o.unread + '</span>' : '') + '</button>' +
    (o.status !== 'cancelled'
      ? '<button class="btn btn-danger btn-sm mt" data-action="seller-act:cancel">' +
        (o.status === 'done' ? 'Закрыть заказ' : 'Отменить заказ') + '</button>'
      : '') +
    // Удаление из базы — только из группы «Все»: там лежат старые и тестовые заказы.
    (state.seller.status === 'all' ? deleteOrderBlock(o) : '')
  );
}

/* Удалить заказ насовсем: вопрос на странице, как у «Закрыть заказ». */
function deleteOrderBlock(o) {
  if (!state.seller.confirmDelete) {
    return '<button class="btn btn-ghost btn-sm mt danger-text" data-action="seller-delete">🗑 Удалить из базы</button>';
  }
  return (
    '<div class="card confirm-box">' +
      '<div class="confirm-title">Удалить заказ ' + esc(o.code) + ' из базы?</div>' +
      '<div class="confirm-text">Вместе с ним удалятся переписка и история заказа. Вернуть их не получится.' +
        (o.paid ? ' Оплата по заказу подтверждена — он пропадёт из продаж и выручки в аналитике.' : '') +
      '</div>' +
      '<div class="btn-row">' +
        '<button class="btn btn-secondary" data-action="seller-delete-no">Оставить</button>' +
        '<button class="btn btn-danger" data-action="seller-delete-yes">Удалить</button>' +
      '</div>' +
    '</div>'
  );
}

/* ------------------------------------------------------------ аналитика */

const ANALYTICS_PERIODS = [
  ['today', 'Сегодня'], ['7d', '7 дней'], ['30d', '30 дней'],
  ['month', 'Этот месяц'], ['all', 'Всё время'], ['custom', 'Свои даты'],
];
const ANALYTICS_KEY = 'iapki.analytics.period.v1';

function rub(n) {
  return new Intl.NumberFormat('ru-RU').format(n || 0) + ' ₽';
}

/* Выбранный период помнит устройство продавца, как и фильтры заказов. */
function analyticsPeriod() {
  const s = state.seller;
  if (!s.period) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(ANALYTICS_KEY) || 'null'); } catch (e) { saved = null; }
    s.period = Object.assign({ key: 'all', from: '', to: '' }, saved || {});
  }
  return s.period;
}

function saveAnalyticsPeriod() {
  try { localStorage.setItem(ANALYTICS_KEY, JSON.stringify(state.seller.period)); } catch (e) { /* без памяти — до закрытия */ }
}

async function loadAnalytics() {
  const p = analyticsPeriod();
  const params = new URLSearchParams({ period: p.key });
  if (p.key === 'custom') {
    // Без дат «свои даты» ничего не выбирают — ждём, пока продавец их укажет.
    if (!p.from && !p.to) { state.seller.analytics = null; return; }
    if (p.from) params.set('from', p.from);
    if (p.to) params.set('to', p.to);
  }
  state.seller.analytics = await api('/api/seller/analytics?' + params.toString());
}

function sellerModeSwitch() {
  const mode = state.seller.mode;
  const btn = (key, label) => (
    '<button class="seg-btn' + (mode === key ? ' active' : '') + '" data-action="seller-mode:' + key + '" ' +
      'aria-pressed="' + (mode === key) + '">' + label + '</button>'
  );
  return '<div class="seg">' + btn('orders', 'Заказы') + btn('analytics', 'Аналитика') + btn('promos', 'Промокоды') + '</div>';
}

function pointLabel(label, unit) {
  const parts = label.split('-');
  return unit === 'day' ? parts[2] + '.' + parts[1] : parts[1] + '.' + parts[0];
}

/* Столбики выручки: высота — доля от лучшего дня (месяца), подписи — по краям и в середине. */
function analyticsChart(series) {
  const points = series.points || [];
  if (!points.some((p) => p.count)) return '';
  const max = Math.max.apply(null, points.map((p) => p.revenue)) || 1;
  const best = points.reduce((a, b) => (b.revenue > a.revenue ? b : a));
  const bars = points.map((p, i) => {
    const h = p.revenue ? Math.max(4, Math.round(p.revenue * 100 / max)) : 0;
    return '<span class="bar' + (p.revenue ? '' : ' empty') + '" style="--i:' + i + ';height:' + (h || 2) + '%" ' +
      'title="' + esc(pointLabel(p.label, series.unit) + ': ' + p.count + ' · ' + rub(p.revenue)) + '"></span>';
  }).join('');
  const mid = points[Math.floor(points.length / 2)];
  const axis = points.length > 1
    ? '<div class="chart-axis"><span>' + esc(pointLabel(points[0].label, series.unit)) + '</span>' +
        (points.length > 2 ? '<span>' + esc(pointLabel(mid.label, series.unit)) + '</span>' : '') +
        '<span>' + esc(pointLabel(points[points.length - 1].label, series.unit)) + '</span></div>'
    : '';
  return (
    '<div class="card">' +
      '<div class="card-title">Выручка ' + (series.unit === 'day' ? 'по дням' : 'по месяцам') + '</div>' +
      '<div class="chart" role="img" aria-label="Выручка за период">' + bars + '</div>' + axis +
      '<div class="cta-note">Лучший ' + (series.unit === 'day' ? 'день' : 'месяц') + ': ' +
        esc(pointLabel(best.label, series.unit)) + ' — ' + best.count + ' · ' + rub(best.revenue) + '</div>' +
    '</div>'
  );
}

function viewAnalytics() {
  const s = state.seller;
  const p = analyticsPeriod();
  const r = s.analytics;
  const chips = ANALYTICS_PERIODS.map(([key, label]) => (
    '<button class="chip' + (p.key === key ? ' active' : '') + '" data-action="an-period:' + key + '" ' +
      'aria-pressed="' + (p.key === key) + '">' + esc(label) + '</button>'
  )).join('');
  const dates = p.key === 'custom'
    ? '<div class="panel-dates">' +
        '<label>с <input type="date" id="anFrom" value="' + esc(p.from) + '"></label>' +
        '<label>по <input type="date" id="anTo" value="' + esc(p.to) + '"></label>' +
      '</div>'
    : '';
  const head = '<h1>Продавец</h1>' + sellerModeSwitch() + '<div class="filters">' + chips + '</div>' + dates;
  if (!r) {
    return head + '<div class="notice">' +
      (p.key === 'custom' ? 'Укажите даты периода.' : 'Загружаем…') + '</div>';
  }

  const metric = (label, value, count, format) => (
    '<div class="metric"><div class="metric-label">' + esc(label) + '</div>' +
      '<div class="metric-value"' +
        (count != null ? ' data-count="' + count + '" data-format="' + (format || 'int') + '"' : '') + '>' +
        esc(value) + '</div></div>'
  );
  const lost = r.cancelledAfterPaid;
  const statuses = r.statuses.map((st) => (
    '<div class="stat-row"><div class="stat-head"><span>' + esc(st.title) + '</span>' +
      '<span class="stat-num">' + st.count + ' · ' + st.percent + '%</span></div>' +
      '<div class="stat-bar"><span style="width:' + st.percent + '%"></span></div></div>'
  )).join('');

  return (
    head +
    '<div class="muted an-dates">' + esc(r.period.datesText) + '</div>' +
    '<div class="metrics">' +
      metric('Продажи', String(r.sales.count), r.sales.count) +
      metric('Выручка', rub(r.sales.revenue), r.sales.revenue, 'rub') +
      metric('Средний чек', r.sales.count ? rub(r.sales.average) : '—', r.sales.count ? r.sales.average : null, 'rub') +
      metric('Дошли до оплаты', r.created ? r.conversion + '%' : '—', r.created ? r.conversion : null, 'pct') +
    '</div>' +
    (lost.count
      ? '<div class="notice warn">Отменено после оплаты: ' + lost.count + ' на ' + rub(lost.revenue) +
          ' · без отменённых: ' + r.net.count + ' на ' + rub(r.net.revenue) + '</div>'
      : '') +
    analyticsChart(r.series) +
    '<div class="card">' +
      '<div class="card-title">Заказы за период по статусам · оформлено ' + r.created + '</div>' +
      (statuses || '<div class="muted">Новых заказов за период нет.</div>') +
    '</div>' +
    '<div class="cta-note">Продажа — заказ с подтверждённой оплатой, по дате подтверждения. ' +
      'Тестовые заказы и демо-оплата не учитываются.</div>'
  );
}

/* ------------------------------------------------------------ промокоды */

const PROMO_FILTERS = [
  ['all', 'Все'], ['active', 'Активные'], ['scheduled', 'Ещё не начались'],
  ['expired', 'Истекли'], ['exhausted', 'Исчерпаны'], ['disabled', 'Выключены'],
];

async function loadPromos() {
  const p = state.seller.promo;
  const data = await api('/api/seller/promocodes?status=' + encodeURIComponent(p.status));
  p.list = data.promocodes;
  p.counts = data.counts || {};
  p.total = data.total || 0;
}

async function openPromo(id) {
  const data = await api('/api/seller/promocode?id=' + encodeURIComponent(id));
  const p = state.seller.promo;
  p.current = data.promocode;
  p.form = null;
  p.confirmDelete = false;
}

function promoLine(p) {
  return (
    '<button class="order-line" data-action="promo-open:' + p.id + '">' +
      '<span class="status-dot promo-' + esc(p.status) + '"></span>' +
      '<span class="grow"><span class="code promo-code">' + esc(p.code) + '</span><br>' +
      '<span class="meta">' + esc(p.valueText) + ' · ' + esc(p.statusTitle) + ' · применён ' + esc(p.usesText) + '</span></span>' +
      '<span class="meta">›</span>' +
    '</button>'
  );
}

function viewPromos() {
  const p = state.seller.promo;
  if (p.form) return viewPromoForm(p.form);
  if (p.current) return viewPromo(p.current);
  const chips = PROMO_FILTERS.map(([key, label]) => {
    const n = key === 'all' ? p.total : (p.counts[key] || 0);
    return '<button class="chip' + (p.status === key ? ' active' : '') + '" data-action="promo-filter:' + key + '" ' +
      'aria-pressed="' + (p.status === key) + '">' + esc(label) + ' · ' + n + '</button>';
  }).join('');
  let list = p.list.map(promoLine).join('');
  if (!list) {
    list = '<div class="notice">' + (p.total
      ? 'В этом фильтре промокодов нет.'
      : 'Промокодов пока нет. Создайте первый — код можно сгенерировать.') + '</div>';
  }
  return (
    '<h1>Продавец</h1>' + sellerModeSwitch() +
    '<button class="btn btn-primary mb" data-action="promo-new">＋ Новый промокод</button>' +
    '<div class="filters">' + chips + '</div>' + list +
    '<div class="cta-note">Покупатели промокодов не видят — публикуйте их сами, где нужно. ' +
      'Покупатель вводит код при оформлении заказа или на шаге оплаты, пока не отправил чек.</div>'
  );
}

function promoRow(label, value) {
  return '<div class="row"><span class="label">' + esc(label) + '</span><span class="value">' + value + '</span></div>';
}

function viewPromo(p) {
  // Цена с промокодом: сейчас, а во время акции — и после неё.
  const prices = p.prices.map((row) => promoRow(row.title, row.final
    ? '<span class="old-price">' + esc(row.priceText) + '</span> <span class="new-price">' + esc(row.finalText) + '</span>'
    : '<span class="danger-text">не сработает: скидка не меньше цены</span>')).join('');
  const history = p.history.map((h) => (
    '<div class="hist-row">' +
      '<div class="hist-head"><span>' + esc(h.title) + '</span><span class="meta">' + esc(dateOf(h.at)) + '</span></div>' +
      (h.detail ? '<div class="hist-detail">' + esc(h.detail) + '</div>' : '') +
      '<div class="meta">' + esc(h.actor) + '</div>' +
    '</div>'
  )).join('');
  return (
    '<button class="backlink" data-action="promo-back">← Все промокоды</button>' +
    '<div class="section-head"><h2>Промокод</h2><span class="badge">' + esc(p.statusTitle) + '</span></div>' +
    '<div class="card">' +
      '<div class="row"><span class="label">Код</span>' +
        '<button type="button" class="value mono copyable" data-action="copy:promo-code" aria-label="Скопировать код">' +
          esc(p.code) + ' ' + copyIcon() + '</button></div>' +
      promoRow('Скидка', esc(p.valueText)) + prices +
      promoRow('Действует', esc(p.periodText)) +
      promoRow('Применён', esc(p.usesText)) +
      promoRow('На одного покупателя', esc(p.perUserText)) +
      (p.note ? promoRow('Заметка', esc(p.note)) : '') +
      promoRow('Создал', esc(p.createdBy) + ', ' + esc(dateOf(p.createdAt))) +
    '</div>' +
    '<button class="btn btn-secondary" data-action="promo-edit">✏️ Изменить условия</button>' +
    '<button class="btn btn-secondary mt" data-action="promo-toggle">' + (p.enabled ? '⏸ Выключить' : '▶️ Включить') + '</button>' +
    deletePromoBlock(p) +
    promoOrders(p.orders || []) +
    (history ? '<div class="card mt"><div class="card-title">История</div>' + history + '</div>' : '')
  );
}

/* Заказы, которые сейчас занимают применения промокода. */
function promoOrders(orders) {
  if (!orders.length) return '';
  return (
    '<div class="card mt"><div class="card-title">Заказы с промокодом · ' + orders.length + '</div>' +
      orders.map((o) => (
        '<div class="hist-row">' +
          '<div class="hist-head"><span>' + esc(o.code) + '</span><span class="meta">' + esc(o.priceText) + '</span></div>' +
          '<div class="meta">' + esc(o.statusTitle) + ' · ' + esc(o.buyer) + ' · скидка ' + esc(o.discountText) + '</div>' +
        '</div>'
      )).join('') +
    '</div>'
  );
}

/* Удалить можно только промокод, который ни разу не применяли. */
function deletePromoBlock(p) {
  if (!p.canDelete) {
    return '<div class="cta-note">Промокод уже применяли — удалить нельзя, только выключить.</div>';
  }
  if (!state.seller.promo.confirmDelete) {
    return '<button class="btn btn-ghost btn-sm mt danger-text" data-action="promo-delete">🗑 Удалить промокод</button>';
  }
  return (
    '<div class="card confirm-box">' +
      '<div class="confirm-title">Удалить промокод ' + esc(p.code) + '?</div>' +
      '<div class="confirm-text">Его ещё ни разу не применяли. Если код уже где-то опубликован, ' +
        'ввести его будет нельзя. Вернуть промокод не получится — только создать заново.</div>' +
      '<div class="btn-row">' +
        '<button class="btn btn-secondary" data-action="promo-delete-no">Оставить</button>' +
        '<button class="btn btn-danger" data-action="promo-delete-yes">Удалить</button>' +
      '</div>' +
    '</div>'
  );
}

function promoField(id, label, control, hint) {
  return (
    '<div class="field"><label' + (id ? ' for="' + id + '"' : '') + '>' + esc(label) + '</label>' + control +
      (hint ? '<div class="field-hint">' + esc(hint) + '</div>' : '') + '</div>'
  );
}

function numberInput(id, value, placeholder) {
  return '<input id="' + id + '" type="number" inputmode="numeric" min="1" step="1" ' +
    'value="' + esc(value) + '" placeholder="' + esc(placeholder) + '">';
}

/* Форма создания и правки. Значения живут в state.seller.promo.form:
   перед перерисовкой (смена типа скидки, ошибка) их снимает readPromoForm. */
function viewPromoForm(f) {
  const isNew = !f.id;
  const kindBtn = (key, label) => (
    '<button type="button" class="seg-btn' + (f.kind === key ? ' active' : '') + '" data-action="pf-kind:' + key + '" ' +
      'aria-pressed="' + (f.kind === key) + '">' + label + '</button>'
  );
  const code = isNew
    ? promoField('pfCode', 'Код',
        '<div class="input-row"><input id="pfCode" class="pf-code" value="' + esc(f.code) + '" maxlength="32" ' +
          'autocapitalize="characters" autocomplete="off" autocorrect="off" spellcheck="false">' +
          // Кнопка — одним значком: на узком экране случайный код иначе не влезает в поле.
          '<button type="button" class="btn btn-secondary btn-sm pf-dice" data-action="pf-generate" ' +
            'aria-label="Сгенерировать другой код" title="Сгенерировать другой код">🎲</button></div>',
        'Латиница, цифры и дефис; 🎲 — другой случайный код. После создания код не меняется.')
    : promoField('', 'Код', '<div class="pf-code pf-fixed">' + esc(f.code) + '</div>', 'Код после создания не меняется.');
  return (
    '<button class="backlink" data-action="promo-form-back">← ' + (isNew ? 'Все промокоды' : 'К промокоду') + '</button>' +
    '<h2>' + (isNew ? 'Новый промокод' : 'Условия промокода') + '</h2>' +
    '<div class="card pform mt">' +
      code +
      promoField('pfValue', 'Скидка',
        '<div class="seg">' + kindBtn('percent', 'В процентах') + kindBtn('rub', 'В рублях') + '</div>' +
        '<div class="input-suffix">' + numberInput('pfValue', f.value, f.kind === 'rub' ? '500' : '20') +
          '<span>' + (f.kind === 'rub' ? '₽' : '%') + '</span></div>',
        f.kind === 'rub'
          ? 'Меньше цены: бесплатных промокодов нет.'
          : 'От 1 до 99%. Во время акции — от цены по акции.') +
      '<div class="pf-pair">' +
        promoField('pfStarts', 'Действует с', '<input id="pfStarts" type="date" value="' + esc(f.startsOn) + '">') +
        promoField('pfEnds', 'по', '<input id="pfEnds" type="date" value="' + esc(f.endsOn) + '">') +
      '</div>' +
      '<div class="field-hint">Даты по Москве, включительно. Пусто — без ограничения.</div>' +
      '<div class="pf-pair">' +
        promoField('pfMax', 'Всего применений', numberInput('pfMax', f.maxUses, 'без лимита')) +
        promoField('pfPer', 'На покупателя', numberInput('pfPer', f.perUser, 'без лимита')) +
      '</div>' +
      promoField('pfNote', 'Заметка для продавцов',
        '<textarea id="pfNote" maxlength="300" placeholder="Где опубликован, для кого">' + esc(f.note) + '</textarea>',
        'Покупатель её не видит.') +
      '<label class="switch-row"><span class="switch-text"><span class="switch-title">Включён</span>' +
        '<span class="switch-hint">Выключенный промокод не сработает, даже если срок идёт.</span></span>' +
        '<input type="checkbox" class="switch" id="pfEnabled"' + (f.enabled ? ' checked' : '') + '></label>' +
    '</div>' +
    (f.error ? '<div class="field-error pf-error">' + esc(f.error) + '</div>' : '') +
    '<button class="btn btn-primary mt" data-action="pf-save">' + (isNew ? 'Создать промокод' : 'Сохранить') + '</button>' +
    '<button class="btn btn-ghost" data-action="promo-form-back">Отмена</button>'
  );
}

function readPromoForm() {
  const f = state.seller.promo.form;
  if (!f) return;
  const fields = { pfCode: 'code', pfValue: 'value', pfStarts: 'startsOn', pfEnds: 'endsOn', pfMax: 'maxUses', pfPer: 'perUser', pfNote: 'note' };
  Object.keys(fields).forEach((id) => {
    const el = document.getElementById(id);
    if (el) f[fields[id]] = el.value;
  });
  const enabled = document.getElementById('pfEnabled');
  if (enabled) f.enabled = enabled.checked;
}

function promoForm(p) {
  const text = (v) => (v == null ? '' : String(v));
  return {
    id: p.id, code: p.code, kind: p.kind, value: text(p.value),
    startsOn: text(p.startsOn), endsOn: text(p.endsOn),
    maxUses: text(p.maxUses), perUser: text(p.perUser),
    enabled: p.enabled, note: text(p.note), error: '',
  };
}

function viewSeller() {
  const s = state.seller;
  if (s.mode === 'promos') return viewPromos();
  if (s.current) return viewSellerOrder(s.current);
  if (s.mode === 'analytics') return viewAnalytics();

  const filters = [['open', 'Открытые'], ['payment_check', 'Проверить оплату'], ['udid', 'Ставить сертификат'],
    ['installed', 'Инструкция'], ['done', 'Готовые'], ['all', 'Все']];

  return (
    '<h1>Продавец</h1>' + sellerModeSwitch() +
    '<input class="search" id="sellerSearch" type="search" enterkeyhint="search" autocomplete="off" ' +
      'placeholder="Код заказа, UDID или @юзернейм" value="' + esc(s.q) + '">' +
    '<div class="filters">' + filters.map(([key, label]) => {
      const n = key === 'all' || key === 'open' ? '' : ' · ' + (s.counts[key] || 0);
      return '<div class="chip' + (s.status === key ? ' active' : '') + '" data-action="seller-filter:' + key + '">' +
        esc(label) + esc(n) + '</div>';
    }).join('') + '</div>' +
    '<div id="sellerTools">' + sellerToolbar() + '</div>' +
    '<div id="sellerList">' + sellerListHtml() + '</div>'
  );
}

function sellerListHtml() {
  const s = state.seller;
  if (!s.orders.length) {
    if (s.q.trim()) return '<div class="notice">Ничего не нашли.</div>';
    return activeSellerFilters(sellerPrefs(s.status), s.status)
      ? '<div class="notice">Под эти фильтры заказов нет. ' +
          '<button class="linkish" data-action="seller-reset">Сбросить фильтры</button></div>'
      : '<div class="notice">Заказов в этом фильтре нет.</div>';
  }
  // Список ограничен сервером — не прячем молча тех, кто не влез.
  const more = s.total > s.orders.length
    ? '<div class="cta-note">Показаны ' + s.orders.length + ' из ' + s.total +
      ' — уточните фильтры, чтобы увидеть остальные.</div>'
    : '';
  return s.orders.map(sellerLine).join('') + more;
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
  applySellerData(data);
  const list = document.getElementById('sellerList');
  if (list) list.innerHTML = sellerListHtml();
  // Поиск выключает фильтры подгрупп — панель под полем должна это показать.
  const tools = document.getElementById('sellerTools');
  if (tools) tools.innerHTML = sellerToolbar();
}

/* -------------------------------------------- продавец: сортировка и фильтры */

// У каждой подгруппы («Открытые», «Готовые», «Все»…) свои сортировка и фильтры:
// переход в другую подгруппу их не переносит. Хранятся на этом устройстве.
const SELLER_PREFS_KEY = 'iapki.seller.prefs.v1';

const SELLER_SORTS = [
  ['created_desc', 'Новые сверху'],
  ['created_asc', 'Старые сверху'],
  ['activity', 'Последняя активность'],
  ['unread', 'Непрочитанные сверху'],
  ['buyer', 'Покупатель А→Я'],
];

const SELLER_PERIODS = [
  ['', 'Всё время'], ['today', 'Сегодня'], ['7d', '7 дней'], ['30d', '30 дней'], ['custom', 'Свои даты'],
];

// Фильтры по статусу и UDID нужны там, где в подгруппе несколько статусов.
const SELLER_GROUP_STATUSES = {
  open: ['new', 'payment_check', 'paid', 'udid', 'installed'],
  all: ['new', 'payment_check', 'paid', 'udid', 'installed', 'done', 'cancelled'],
};

function defaultSellerPrefs() {
  return {
    sort: 'created_desc', statuses: [], period: '', from: '', to: '', unread: false, udid: '', hideTest: false,
  };
}

function sellerPrefs(group) {
  const s = state.seller;
  if (!s.prefs) {
    try {
      const saved = JSON.parse(localStorage.getItem(SELLER_PREFS_KEY) || '{}');
      s.prefs = saved && typeof saved === 'object' ? saved : {};
    } catch (e) {
      s.prefs = {};
    }
  }
  // Недостающие поля добираем значениями по умолчанию — на случай старого сохранения.
  s.prefs[group] = Object.assign(defaultSellerPrefs(), s.prefs[group] || {});
  return s.prefs[group];
}

function saveSellerPrefs() {
  try {
    localStorage.setItem(SELLER_PREFS_KEY, JSON.stringify(state.seller.prefs || {}));
  } catch (e) { /* хранилище недоступно — настройки живут до закрытия */ }
}

function activeSellerFilters(prefs, group) {
  let n = 0;
  if (SELLER_GROUP_STATUSES[group] && prefs.statuses.length) n += 1;
  // «Свои даты» без дат ничего не отбирают — фильтром не считаем.
  if (prefs.period && (prefs.period !== 'custom' || prefs.from || prefs.to)) n += 1;
  if (prefs.unread) n += 1;
  if (SELLER_GROUP_STATUSES[group] && prefs.udid) n += 1;
  if (prefs.hideTest) n += 1;
  return n;
}

/* Границы периода — в часовом поясе телефона продавца: «сегодня» начинается
   в его полночь. На сервер уходят моменты в UTC. */
function sellerPeriod(prefs) {
  const day = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const back = (days) => { const d = day(new Date()); d.setDate(d.getDate() - days); return d; };
  if (prefs.period === 'today') return { from: back(0) };
  if (prefs.period === '7d') return { from: back(6) };
  if (prefs.period === '30d') return { from: back(29) };
  if (prefs.period === 'custom') {
    const range = {};
    if (prefs.from) range.from = new Date(prefs.from + 'T00:00:00');
    if (prefs.to) {
      const to = new Date(prefs.to + 'T00:00:00');
      to.setDate(to.getDate() + 1);  // «по 13-е» включает весь день 13-го
      range.to = to;
    }
    return range;
  }
  return {};
}

function sellerParams(params, group) {
  const prefs = sellerPrefs(group);
  if (prefs.sort !== 'created_desc') params.set('sort', prefs.sort);
  if (SELLER_GROUP_STATUSES[group]) {
    if (prefs.statuses.length) params.set('st', prefs.statuses.join(','));
    if (prefs.udid) params.set('udid', prefs.udid);
  }
  const range = sellerPeriod(prefs);
  if (range.from && !isNaN(range.from)) params.set('from', range.from.toISOString());
  if (range.to && !isNaN(range.to)) params.set('to', range.to.toISOString());
  if (prefs.unread) params.set('unread', '1');
  if (prefs.hideTest) params.set('test', 'hide');
}

function sellerToolbar() {
  const s = state.seller;
  if (s.q.trim()) {
    return '<div class="seller-note">Поиск идёт по всем заказам — сортировка и фильтры подгрупп к нему не применяются.</div>';
  }
  const prefs = sellerPrefs(s.status);
  const sortLabel = (SELLER_SORTS.find(([key]) => key === prefs.sort) || SELLER_SORTS[0])[1];
  const n = activeSellerFilters(prefs, s.status);
  const changed = n > 0 || prefs.sort !== 'created_desc';
  return (
    '<div class="seller-tools">' +
      '<button class="tool-btn' + (s.panel ? ' on' : '') + (n ? ' active' : '') + '" data-action="seller-panel" ' +
        'aria-expanded="' + s.panel + '">' +
        '<span class="tool-text">⇅ ' + esc(sortLabel) + (n ? ' · фильтры: ' + n : '') + '</span>' +
        '<span class="tool-caret">' + (s.panel ? '▴' : '▾') + '</span></button>' +
      (changed ? '<button class="tool-reset" data-action="seller-reset">Сбросить</button>' : '') +
    '</div>' +
    (s.panel ? sellerPanel(prefs, s.status) : '')
  );
}

function sellerPanel(prefs, group) {
  const chip = (action, label, on) => (
    '<button class="chip' + (on ? ' active' : '') + '" data-action="' + action + '" aria-pressed="' + on + '">' +
      esc(label) + '</button>'
  );
  const statuses = SELLER_GROUP_STATUSES[group];
  const titles = state.seller.titles || {};
  return (
    '<div class="seller-panel">' +
      '<div class="panel-label">Сортировка</div>' +
      '<div class="panel-chips">' +
        SELLER_SORTS.map(([key, label]) => chip('seller-sort:' + key, label, prefs.sort === key)).join('') +
      '</div>' +
      (statuses
        ? '<div class="panel-label">Статус</div><div class="panel-chips">' +
            statuses.map((st) => chip('seller-st:' + st, titles[st] || st,
              !prefs.statuses.length || prefs.statuses.indexOf(st) !== -1)).join('') +
          '</div>'
        : '') +
      '<div class="panel-label">Период создания</div>' +
      '<div class="panel-chips">' +
        SELLER_PERIODS.map(([key, label]) => chip('seller-period:' + key, label, prefs.period === key)).join('') +
      '</div>' +
      (prefs.period === 'custom'
        ? '<div class="panel-dates">' +
            '<label>с <input type="date" id="sellerFrom" value="' + esc(prefs.from) + '"></label>' +
            '<label>по <input type="date" id="sellerTo" value="' + esc(prefs.to) + '"></label>' +
          '</div>'
        : '') +
      '<div class="panel-label">Сообщения</div>' +
      '<div class="panel-chips">' + chip('seller-unread', 'Только непрочитанные', prefs.unread) + '</div>' +
      '<div class="panel-label">Тестовые заказы</div>' +
      '<div class="panel-chips">' + chip('seller-hidetest', '🧪 Скрыть тестовые', prefs.hideTest) + '</div>' +
      (statuses
        ? '<div class="panel-label">UDID</div><div class="panel-chips">' +
            [['', 'Любой'], ['yes', 'Есть'], ['no', 'Нет']]
              .map(([key, label]) => chip('seller-udid:' + key, label, prefs.udid === key)).join('') +
          '</div>'
        : '') +
    '</div>'
  );
}

/* Поменяли настройку подгруппы — сохраняем и перезапрашиваем список. */
function sellerPrefsChanged() {
  saveSellerPrefs();
  guard(async () => { await loadSellerOrders(); render(); });
}

function toggleSellerStatus(status) {
  const group = state.seller.status;
  const all = SELLER_GROUP_STATUSES[group];
  if (!all) return;
  const prefs = sellerPrefs(group);
  // Пустой список значит «все статусы».
  let picked = prefs.statuses.length ? prefs.statuses.slice() : all.slice();
  if (picked.indexOf(status) !== -1) {
    if (picked.length === 1) {
      toast('Хотя бы один статус должен остаться');
      return;
    }
    picked = picked.filter((st) => st !== status);
  } else {
    picked.push(status);
  }
  prefs.statuses = picked.length === all.length ? [] : all.filter((st) => picked.indexOf(st) !== -1);
  sellerPrefsChanged();
}

/* ------------------------------------------------------------- поставщик */

function supplierLine(o) {
  return (
    '<button class="order-line" data-action="supplier-open:' + o.id + '">' +
      '<span class="status-dot ' + esc(o.status) + '"></span>' +
      '<span class="grow"><span class="code">' + esc(o.code) + '</span>' + testTag(o) + '<br>' +
      '<span class="meta">' + esc(o.statusTitle) + ' · ' + esc(dateOf(o.createdAt)) +
      (o.username ? ' · @' + esc(o.username) : '') + '</span></span>' +
      '<span class="meta">›</span>' +
    '</button>'
  );
}

function viewSupplierOrder(o) {
  return (
    '<button class="backlink" data-action="supplier-back">← Все заказы</button>' +
    '<div class="section-head"><h2>' + esc(o.code) + '</h2>' +
      '<span class="badge">' + esc(o.statusTitle) + '</span></div>' +
    '<div class="card">' +
      '<div class="row"><span class="label">Номер заказа</span><span class="value">' +
        esc(o.code) + '</span></div>' +
      '<div class="row"><span class="label">Статус</span><span class="value">' +
        esc(o.statusTitle) + '</span></div>' +
      '<div class="row"><span class="label">Покупатель</span><span class="value">' +
        esc(o.firstName || '—') + (o.username ? ' @' + esc(o.username) : '') + '</span></div>' +
      '<div class="row"><span class="label">Создан</span><span class="value">' +
        esc(dateOf(o.createdAt)) + '</span></div>' +
      '<div class="row"><span class="label">UDID</span>' + udidValue(o.udid, 'udid') + '</div>' +
      (o.udid
        ? '<div class="mt"><button class="btn btn-primary" data-action="copy:udid">' +
          'Скопировать UDID</button></div>'
        : '') +
    '</div>'
  );
}

function viewSupplier() {
  const s = state.supplier;
  if (s.current) return viewSupplierOrder(s.current);

  return (
    '<h1>UDID заказов</h1>' +
    '<div class="cta-note">Номера устройств по заказам, где покупатель уже прислал UDID.</div>' +
    '<div class="mt">' +
      (s.orders.length
        ? s.orders.map(supplierLine).join('')
        : '<div class="notice">Присланных UDID пока нет.</div>') +
    '</div>'
  );
}

/* ------------------------------------------------------------------- каркас */

function navIcon(key) {
  const paths = {
    home: '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    order: '<rect x="5" y="4" width="14" height="17" rx="3"/><path d="M9 3h6v4H9zM9 12h6M9 16h4"/>',
    about: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    seller: '<rect x="3" y="7" width="18" height="14" rx="3"/><path d="M8 7V4h8v3M3 12h18M10 12v3h4v-3"/>',
    supplier: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M10.5 5.5h3M12 18.5h.01"/>',
  };
  return '<svg class="nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[key] + '</svg>';
}

function tabbar() {
  const b = state.boot;
  const order = b.order;
  const unread = b.unread || 0;
  const alert = unread > 0 || (order && order.isOpen && (order.status === 'paid' || order.status === 'new'));
  const sellerAlert = (b.sellerUnread || 0) > 0;
  // Каталог живёт на «Главной», помощь — кнопкой в шапке: нижний ряд
  // оставляем коротким, чтобы хватало места новым разделам.
  const tabs = state.public
    ? [['home', 'Главная'], ['about', 'О нас']]
    : [['home', 'Главная'], ['order', 'Мой заказ'], ['about', 'О нас']];
  if (b.user.isSeller) tabs.push(['seller', 'Продавец']);
  if (b.user.isSupplier) tabs.push(['supplier', 'Поставщик']);

  return tabs.map(([key, label]) => (
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
  if (state.view === 'seller' && state.seller.mode === 'promos') {
    const p = state.seller.promo;
    return 'seller:promos:' + (p.form ? 'form:' + (p.form.id || 'new') : (p.current ? p.current.id : 'list'));
  }
  if (state.view === 'seller') return 'seller:' + (state.seller.current ? state.seller.current.id : 'list');
  if (state.view === 'supplier') return 'supplier:' + (state.supplier.current ? state.supplier.current.id : 'list');
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
    home: viewHome, order: viewOrder, about: viewAbout, doc: viewDoc,
    help: viewHelp, seller: viewSeller, chat: viewChat, notify: viewNotify,
    supplier: viewSupplier,
  };
  document.body.classList.toggle('chat-mode', state.view === 'chat');
  // На витрине каталог занимает всю ширину, остальные экраны — колонка
  // по центру: на мониторе текст во всю ширину не читается.
  root.classList.toggle('narrow', state.view !== 'home');
  // Подвал с реквизитами и документами обязателен на каждом экране,
  // кроме переписки — там снизу поле ввода.
  root.innerHTML = (views[state.view] || viewHome)() + (state.view === 'chat' ? '' : footerHtml());
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
  if (key !== state.screen && state.view !== 'chat') {
    window.scrollTo(0, 0);
    // Перезапуск анимации появления: перерисовка того же экрана
    // (галочка, фильтр) не должна мигать.
    root.classList.remove('view-in');
    void root.offsetWidth;
    root.classList.add('view-in');
    // Класс снимаем, когда волна прошла: иначе она повторялась бы при каждой
    // перерисовке того же экрана (галочка, фильтр, тост).
    clearTimeout(viewInTimer);
    viewInTimer = setTimeout(() => root.classList.remove('view-in'), 1200);
  }
  state.screen = key;
  if (state.view !== lastTabView) {
    const on = document.querySelector('#tabbar button.on');
    if (on) on.classList.add('pop');
    lastTabView = state.view;
  }
  animateNumbers(root);
  watchMilestones();
}

let viewInTimer = null;
let lastTabView = null;

const TABS = ['home', 'order', 'about', 'seller', 'supplier'];

function updateBackButton() {
  if (!tg || !tg.BackButton) return;
  // Разделы нижнего ряда — верхний уровень. Помощь из шапки, переписка,
  // уведомления и карточка заказа у продавца — вложенные экраны.
  const nested = TABS.indexOf(state.view) === -1
    || (state.view === 'seller' && !!state.seller.current)
    || (state.view === 'seller' && promoNested())
    || (state.view === 'supplier' && !!state.supplier.current);
  if (nested) tg.BackButton.show();
  else tg.BackButton.hide();
}

/* Карточка промокода и форма — вложенные экраны раздела «Промокоды». */
function promoNested() {
  const p = state.seller.promo;
  return state.seller.mode === 'promos' && !!(p.current || p.form);
}

/* Ряды фильтров прокручиваются вбок. На телефоне это делает палец,
   а на компьютере колесо мыши крутит страницу, и часть ряда недостижима —
   поэтому колесо над рядом двигаем его сами, плюс даём тащить мышью. */
function bindScrollX(el) {
  el.addEventListener('wheel', (event) => {
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 1) return;
    const sideways = Math.abs(event.deltaX) > Math.abs(event.deltaY);
    const delta = sideways ? event.deltaX : event.deltaY;
    if (!delta) return;
    // На краю ряда колесо возвращается странице — иначе прокрутка упирается.
    if ((delta < 0 && el.scrollLeft <= 0) || (delta > 0 && el.scrollLeft >= max - 1)) return;
    event.preventDefault();
    el.scrollBy({ left: delta, behavior: 'auto' });
  }, { passive: false });

  let startX = 0;
  let startLeft = 0;
  let dragging = false;
  let moved = false;

  el.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'touch' || event.button !== 0) return;
    dragging = true;
    moved = false;
    startX = event.clientX;
    startLeft = el.scrollLeft;
  });

  el.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const dx = event.clientX - startX;
    if (!moved && Math.abs(dx) > 4) {
      moved = true;
      el.classList.add('dragging');
    }
    if (moved) el.scrollLeft = startLeft - dx;
  });

  const stop = () => {
    dragging = false;
    el.classList.remove('dragging');
  };
  el.addEventListener('pointerup', stop);
  el.addEventListener('pointercancel', stop);
  el.addEventListener('pointerleave', stop);

  // После перетаскивания палец (или курсор) стоит на чипе — но это была
  // прокрутка, а не выбор фильтра.
  el.addEventListener('click', (event) => {
    if (!moved) return;
    moved = false;
    event.preventDefault();
    event.stopPropagation();
  }, true);
}

/* Лента шагов инструкции — это страницы, а не ряд чипов: на экране один
   шаг, и короткого движения достаточно, чтобы пришёл следующий. Пальцем
   это делает сам браузер, мыши и клавиатуре помогаем здесь. */
function markCurrentStep(el) {
  const cards = Array.prototype.slice.call(el.children);
  if (!cards.length || !('IntersectionObserver' in window)) return;
  el.classList.add('paged');
  cards[0].classList.add('is-current');
  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
        cards.forEach((card) => card.classList.toggle('is-current', card === entry.target));
      }
    });
  }, { root: el, threshold: [0.6] });
  cards.forEach((card) => io.observe(card));
}

function bindPager(el) {
  markCurrentStep(el);
  // Шаг ленты берём из разметки, чтобы не повторять зазор из стилей.
  const pageWidth = () => (
    el.children.length > 1
      ? el.children[1].offsetLeft - el.children[0].offsetLeft
      : el.clientWidth
  );
  const current = () => Math.round(el.scrollLeft / Math.max(1, pageWidth()));

  let dragging = false;
  let moved = false;
  let startX = 0;
  let startLeft = 0;

  const go = (index) => {
    const last = el.children.length - 1;
    const target = Math.max(0, Math.min(last, index));
    el.scrollTo({ left: target * pageWidth(), behavior: 'smooth' });
  };

  // Боковое колесо и трекпад листают ленту, вертикальное остаётся странице.
  el.addEventListener('wheel', (event) => {
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
    const max = el.scrollWidth - el.clientWidth;
    if (max <= 1) return;
    if ((event.deltaX < 0 && el.scrollLeft <= 0) || (event.deltaX > 0 && el.scrollLeft >= max - 1)) return;
    event.preventDefault();
    el.scrollBy({ left: event.deltaX, behavior: 'auto' });
  }, { passive: false });

  el.addEventListener('pointerdown', (event) => {
    if (event.pointerType === 'touch' || event.button !== 0) return;
    dragging = true;
    moved = false;
    startX = event.clientX;
    startLeft = el.scrollLeft;
    el.classList.add('dragging');
  });

  el.addEventListener('pointermove', (event) => {
    if (!dragging) return;
    const dx = event.clientX - startX;
    if (!moved && Math.abs(dx) > 4) moved = true;
    if (moved) el.scrollLeft = startLeft - dx;
  });

  const stop = (event) => {
    if (!dragging) return;
    dragging = false;
    const dx = event && typeof event.clientX === 'number' ? event.clientX - startX : 0;
    const from = Math.round(startLeft / Math.max(1, pageWidth()));
    if (moved) go(dx <= -40 ? from + 1 : (dx >= 40 ? from - 1 : from));
    // Снап возвращаем, когда лента доехала: включённый посреди движения,
    // он утащил бы карточку к ближайшему краю.
    window.setTimeout(() => { if (!dragging) el.classList.remove('dragging'); }, 420);
  };
  el.addEventListener('pointerup', stop);
  el.addEventListener('pointercancel', stop);
  el.addEventListener('pointerleave', stop);

  // Стрелок у ленты нет, поэтому клавиатуре даём листать напрямую.
  el.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    go(current() + (event.key === 'ArrowRight' ? 1 : -1));
  });

  // После перетаскивания курсор стоит на ссылке внутри карточки — но это
  // была прокрутка, а не нажатие.
  el.addEventListener('click', (event) => {
    if (!moved) return;
    moved = false;
    event.preventDefault();
    event.stopPropagation();
  }, true);
}

/* То же, что udid.normalize на сервере: пробелы и невидимые символы долой,
   кириллические А, В, С, Е — на латинские, тире и минус — на дефис.
   Глазом эти символы не отличить, поэтому исправляем молча. */
const UDID_LOOKALIKES = {
  'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'а': 'a', 'в': 'b', 'с': 'c', 'е': 'e',
};

function normalizeUdid(value) {
  return value
    .replace(/[\s​-‍⁠﻿]+/g, '')
    .replace(/[АВСЕавсе]/g, (ch) => UDID_LOOKALIKES[ch])
    .replace(/[‐-―−﹣－]/g, '-');
}

function bindInputs() {
  document.querySelectorAll('.filters, .stepper').forEach((el) => bindScrollX(el));
  document.querySelectorAll('.instr-steps').forEach((el) => bindPager(el));

  const udid = document.getElementById('udidInput');
  if (udid) {
    udid.addEventListener('input', () => {
      // Дефис и регистр — часть номера, их не трогаем, а лишние символы
      // покажет проверка на сервере, а не молча съест поле.
      const clean = normalizeUdid(udid.value).slice(0, 40);
      if (udid.value !== clean) udid.value = clean;
      state.udid = clean;
      state.udidChecked = null;
      const count = document.getElementById('udidCount');
      if (count) count.textContent = clean.length + ' / 40';
      const btn = document.getElementById('udidBtn');
      if (btn) btn.disabled = !(clean.length > 0 && state.agreeUdid);
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
      if (state.udid.length > 0 && state.agreeUdid) handleAction('udid-send');
    });
  }

  const receipt = document.getElementById('receiptFile');
  if (receipt) {
    receipt.addEventListener('change', () => {
      const files = Array.from(receipt.files || []);
      // Сбрасываем поле: иначе повторный выбор тех же файлов не сработает.
      receipt.value = '';
      uploadReceipts(files);
    });
  }

  const receiptLink = document.getElementById('receiptLink');
  if (receiptLink) {
    receiptLink.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        sendReceiptLink();
      }
    });
  }

  const agreeUdid = document.getElementById('agreeUdid');
  if (agreeUdid) {
    agreeUdid.addEventListener('change', () => {
      state.agreeUdid = agreeUdid.checked;
      const btn = document.getElementById('udidBtn');
      if (btn) btn.disabled = !(state.udid.length > 0 && state.agreeUdid);
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

  ['anFrom', 'anTo'].forEach((id) => {
    const input = document.getElementById(id);
    if (!input) return;
    input.addEventListener('change', () => {
      analyticsPeriod()[id === 'anFrom' ? 'from' : 'to'] = input.value;
      saveAnalyticsPeriod();
      guard(async () => { await loadAnalytics(); render(); });
    });
  });

  ['sellerFrom', 'sellerTo'].forEach((id) => {
    const input = document.getElementById(id);
    if (!input) return;
    input.addEventListener('change', () => {
      sellerPrefs(state.seller.status)[id === 'sellerFrom' ? 'from' : 'to'] = input.value;
      sellerPrefsChanged();
    });
  });

  // Поле промокода покупателя: текст живёт в состоянии, Enter — «Применить».
  ['checkout', 'order'].forEach((where) => {
    const input = document.getElementById('promoInput-' + where);
    if (!input) return;
    const s = where === 'checkout' ? state.checkoutPromo : state.orderPromo;
    input.addEventListener('input', () => {
      s.code = input.value;
      // Старая причина отказа к новому коду не относится.
      if (s.error) {
        s.error = '';
        const error = input.closest('.promo-box').querySelector('.field-error');
        if (error) error.remove();
      }
    });
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      applyBuyerPromo(where);
    });
  });

  // Код промокода — заглавными и без пробелов, как его сохранит сервер.
  const promoCode = document.getElementById('pfCode');
  if (promoCode) {
    promoCode.addEventListener('input', () => {
      const clean = promoCode.value.toUpperCase().replace(/\s+/g, '');
      if (promoCode.value !== clean) promoCode.value = clean;
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

}

/* ----------------------------------------------------------------- действия */

/* Копирование реквизита: в Telegram буфер обмена доступен через обычный API,
   в старых клиентах остаётся запасной путь через скрытое поле. */
function copyFallback(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.cssText = 'position:fixed;top:0;left:0;opacity:0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  area.remove();
  return ok;
}

function copyValue(key) {
  const order = state.boot && state.boot.order;
  const payment = (state.boot && state.boot.payment) || {};
  const rows = payment.requisites || [];

  let text = '';
  if (key === 'code') {
    text = order && order.code;
  } else if (key === 'amount') {
    // В банк удобнее вставлять голое число, без пробелов и знака рубля.
    const fixed = order && order.promoActive ? '' : payment.amount;
    const shown = fixed || (order && order.priceText) || '';
    text = shown.replace(/[^\d.,]/g, '') || shown;
  } else if (key === 'udid') {
    const current = state.supplier.current;
    text = current && current.udid;
  } else if (key === 'seller-udid') {
    const current = state.seller.current;
    text = current && current.udid;
  } else if (key === 'promo-code') {
    const current = state.seller.promo.current;
    text = current && current.code;
  } else {
    const row = rows.find((r) => r.key === key);
    text = row && row.value;
  }
  if (!text) return;

  const done = () => { toast('Скопировано'); flashCopied(key); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => {
      if (copyFallback(text)) done();
      else toast('Не получилось скопировать', true);
    });
    return;
  }
  if (copyFallback(text)) done();
  else toast('Не получилось скопировать', true);
}

const RECEIPT_LIMIT = 15 * 1024 * 1024;
const RECEIPT_FILES = 10;

function sizeText(bytes) {
  return (bytes / 1024 / 1024).toFixed(1).replace('.', ',') + ' МБ';
}

/* Ход загрузки: процент на самой кнопке и тонкая полоса под ней. Экран ради
   процентов не перерисовываем — меняем только эти два элемента. */
function showUploadProgress(share) {
  const btn = document.querySelector('[data-action="receipt-pick"]');
  const bar = document.getElementById('uploadBar');
  if (!btn) return;
  if (!btn.dataset.label) btn.dataset.label = btn.textContent;
  btn.disabled = true;
  const percent = Math.max(0, Math.min(100, Math.round(share * 100)));
  // Файл дошёл до сервера, но он ещё пересылает его продавцу в Telegram.
  btn.textContent = percent < 100 ? 'Загружаем… ' + percent + '%' : 'Отправляем продавцу…';
  if (bar) {
    bar.hidden = false;
    bar.firstElementChild.style.width = percent + '%';
  }
}

function resetUploadProgress() {
  const btn = document.querySelector('[data-action="receipt-pick"]');
  const bar = document.getElementById('uploadBar');
  if (btn && btn.dataset.label) {
    btn.textContent = btn.dataset.label;
    delete btn.dataset.label;
    btn.disabled = false;
  }
  if (bar) bar.hidden = true;
}

/* fetch не сообщает, сколько уже отправлено, поэтому файлы идут через XHR. */
function postFiles(url, files, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    if (tg && tg.initData) xhr.setRequestHeader('X-Init-Data', tg.initData);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.upload.onload = () => onProgress(1);
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { /* пустой ответ */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.error || 'Не удалось отправить файл'));
    };
    xhr.onerror = () => reject(new Error('Нет связи — файлы не отправлены'));
    const form = new FormData();
    files.forEach((file) => form.append('file', file));
    xhr.send(form);
  });
}

function uploadReceipts(files) {
  const order = state.boot.order;
  if (!files.length || !order) return;
  if (files.length > RECEIPT_FILES) {
    toast('Не больше ' + RECEIPT_FILES + ' файлов за раз', true);
    return;
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > RECEIPT_LIMIT) {
    toast((files.length > 1 ? 'Файлы вместе — ' : 'Файл — ') + sizeText(total) + ', а можно до 15 МБ', true);
    return;
  }

  guard(async () => {
    showUploadProgress(0);
    let data;
    try {
      data = await postFiles('/api/order/receipt?orderId=' + order.id, files, showUploadProgress);
    } catch (err) {
      resetUploadProgress();
      throw err;
    }
    if (data.order) state.boot.order = data.order;
    const many = files.length > 1;
    toast(data.claimed
      ? (many ? 'Чеки получены' : 'Чек получен') + ' — заказ на проверке'
      : (many ? 'Чеки отправлены продавцу' : 'Чек отправлен продавцу'));
    render();
  });
}

function sendReceiptLink() {
  const order = state.boot.order;
  const input = document.getElementById('receiptLink');
  const url = (input && input.value || '').trim();
  if (!order) return;
  // Ссылку из текста вытащит сервер, здесь лишь ловим явную ошибку.
  if (!/https?:\/\/\S+/i.test(url)) {
    toast('Вставьте ссылку на чек — она начинается с https://', true);
    if (input) input.focus();
    return;
  }
  guard(async () => {
    const data = await api('/api/order/receipt/link', {
      method: 'POST', body: { orderId: order.id, url },
    });
    state.boot.order = data.order;
    toast(data.claimed ? 'Чек получен — заказ на проверке' : 'Ссылка отправлена продавцу');
    render();
  });
}

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
  const q = state.seller.q.trim();
  // Поиск — по всем заказам, поэтому настройки подгруппы к нему не прикладываем.
  if (q) params.set('q', q);
  else sellerParams(params, state.seller.status);
  return api('/api/seller/orders?' + params.toString());
}

function applySellerData(data) {
  state.seller.orders = data.orders;
  state.seller.counts = data.counts || {};
  state.seller.total = data.total || data.orders.length;
  state.seller.titles = data.titles || state.seller.titles;
}

async function loadSellerOrders() {
  applySellerData(await fetchSellerOrders());
}

async function loadSupplierOrders() {
  const data = await api('/api/supplier/orders');
  state.supplier.orders = data.orders;
}

const SELLER_ACTION_DONE = { installed: 'Инструкция отправлена покупателю' };

async function sellerAction(action, extra) {
  const current = state.seller.current;
  if (!current) return;
  const body = Object.assign({ action, orderId: current.id }, extra || {});
  const data = await api('/api/seller/action', { method: 'POST', body });
  state.seller.current = data.order;
  await loadSellerOrders();
  toast(SELLER_ACTION_DONE[action] || 'Готово');
}

const actions = {
  'support': () => {
    const username = state.boot && state.boot.support;
    if (!username) { toast('Контакт поддержки не указан', true); return; }
    if (tg && tg.openTelegramLink) tg.openTelegramLink('https://t.me/' + username);
    else window.open('https://t.me/' + username, '_blank');
  },

  'buy': () => guard(async () => {
    const check = state.checkoutPromo.check;
    const data = await api('/api/order/create', {
      method: 'POST',
      body: { app: state.checkoutApp, promo: check ? check.code : '' },
    });
    state.boot.order = data.order;
    state.newOrder = false;
    state.checkoutApp = null;
    state.checkoutPromo = { code: '', check: null, error: '' };
    state.orderPromo = { open: false, code: '', error: '' };
    state.view = 'order';
    if (data.promo && !data.promo.ok) {
      // Пока оформляли, последнее применение могли занять: заказ есть, промокода нет.
      state.orderPromo = { open: true, code: check.code, error: data.promo.message };
      toast('Заказ ' + data.order.code + ' создан, но промокод не применился', true);
    } else {
      toast('Заказ ' + data.order.code + ' создан' + (data.promo ? ' со скидкой по промокоду' : ''));
    }
    render();
  }),

  'bp-open': () => {
    state.orderPromo.open = true;
    render();
    const input = document.getElementById('promoInput-order');
    if (input) input.focus();
  },

  'receipt-pick': () => {
    const input = document.getElementById('receiptFile');
    if (input) input.click();
  },

  'receipt-link': () => sendReceiptLink(),

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

  'order-close': () => {
    state.confirmClose = true;
    render();
    // Вопрос появляется внизу страницы — его кнопки не должны прятаться под панелью вкладок.
    const box = document.querySelector('.confirm-box');
    if (box) box.scrollIntoView({ block: 'center', behavior: 'smooth' });
  },

  'order-close-no': () => { state.confirmClose = false; render(); },

  'seller-delete': () => {
    state.seller.confirmDelete = true;
    render();
    const box = document.querySelector('.confirm-box');
    if (box) box.scrollIntoView({ block: 'center', behavior: 'smooth' });
  },

  'seller-delete-no': () => { state.seller.confirmDelete = false; render(); },

  'seller-delete-yes': () => guard(async () => {
    const current = state.seller.current;
    if (!current) return;
    const data = await api('/api/seller/action', { method: 'POST', body: { action: 'delete', orderId: current.id } });
    state.seller.confirmDelete = false;
    state.seller.current = null;
    await loadSellerOrders();
    toast('Заказ ' + data.code + ' удалён');
    render();
  }),

  'order-close-yes': () => guard(async () => {
    const data = await api('/api/order/cancel', {
      method: 'POST',
      body: { orderId: state.boot.order.id },
    });
    state.boot.order = data.order;
    state.newOrder = false;
    state.confirmClose = false;
    toast('Заказ закрыт');
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

  'chat': () => {
    if (state.public) { openBot(); return; }
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

  'doc-back': () => { state.view = state.doc.from || 'about'; render(); },

  'seller-back': () => { state.seller.current = null; state.seller.confirmDelete = false; render(); },

  'supplier-back': () => { state.supplier.current = null; render(); },

  'seller-panel': () => { state.seller.panel = !state.seller.panel; render(); },

  'seller-reset': () => {
    sellerPrefs(state.seller.status);
    state.seller.prefs[state.seller.status] = defaultSellerPrefs();
    sellerPrefsChanged();
  },

  'seller-unread': () => {
    const prefs = sellerPrefs(state.seller.status);
    prefs.unread = !prefs.unread;
    sellerPrefsChanged();
  },

  'seller-hidetest': () => {
    const prefs = sellerPrefs(state.seller.status);
    prefs.hideTest = !prefs.hideTest;
    sellerPrefsChanged();
  },

  // Новый промокод: форма сразу со случайным кодом — его можно заменить своим.
  'promo-new': () => guard(async () => {
    const data = await api('/api/seller/promocode/new');
    state.seller.promo.form = {
      id: null, code: data.code, kind: 'percent', value: '', startsOn: '', endsOn: '',
      maxUses: '', perUser: '1', enabled: true, note: '', error: '',
    };
    render();
  }),

  'pf-generate': () => guard(async () => {
    const data = await api('/api/seller/promocode/new');
    const f = state.seller.promo.form;
    if (f) f.code = data.code;
    const input = document.getElementById('pfCode');
    if (input) input.value = data.code;
  }),

  'pf-save': () => guard(async () => {
    readPromoForm();
    const p = state.seller.promo;
    const f = p.form;
    if (!f) return;
    const body = {
      action: f.id ? 'update' : 'create', kind: f.kind, value: f.value,
      startsOn: f.startsOn, endsOn: f.endsOn, maxUses: f.maxUses, perUser: f.perUser,
      enabled: f.enabled, note: f.note,
    };
    if (f.id) body.id = f.id;
    else body.code = f.code;
    let data;
    try {
      data = await api('/api/seller/promocode', { method: 'POST', body });
    } catch (err) {
      // Ошибку показываем над кнопкой: её нужно прочитать и поправить поле.
      f.error = err.message;
      haptic('error');
      render();
      const box = document.querySelector('.pf-error');
      if (box) box.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    p.current = data.promocode;
    p.form = null;
    await loadPromos();
    toast(f.id ? 'Промокод сохранён' : 'Промокод ' + data.promocode.code + ' создан');
    render();
  }),

  'promo-edit': () => {
    const current = state.seller.promo.current;
    if (!current) return;
    state.seller.promo.form = promoForm(current);
    state.seller.promo.confirmDelete = false;
    render();
  },

  'promo-form-back': () => { state.seller.promo.form = null; render(); },

  'promo-back': () => {
    state.seller.promo.current = null;
    state.seller.promo.confirmDelete = false;
    render();
  },

  'promo-toggle': () => guard(async () => {
    const current = state.seller.promo.current;
    if (!current) return;
    const data = await api('/api/seller/promocode', {
      method: 'POST', body: { action: current.enabled ? 'disable' : 'enable', id: current.id },
    });
    state.seller.promo.current = data.promocode;
    await loadPromos();
    toast(data.promocode.enabled ? 'Промокод включён' : 'Промокод выключен');
    render();
  }),

  'promo-delete': () => {
    state.seller.promo.confirmDelete = true;
    render();
    const box = document.querySelector('.confirm-box');
    if (box) box.scrollIntoView({ block: 'center', behavior: 'smooth' });
  },

  'promo-delete-no': () => { state.seller.promo.confirmDelete = false; render(); },

  'promo-delete-yes': () => guard(async () => {
    const p = state.seller.promo;
    if (!p.current) return;
    const data = await api('/api/seller/promocode', { method: 'POST', body: { action: 'delete', id: p.current.id } });
    p.current = null;
    p.confirmDelete = false;
    await loadPromos();
    toast('Промокод ' + data.code + ' удалён');
    render();
  }),
};

/* Промокод покупателя. На оформлении сервер только считает цену — применение
   займёт само оформление; на экране оплаты промокод сразу применяется к заказу.
   Отказ показываем под полем: причину нужно прочитать. */
function applyBuyerPromo(where) {
  const s = where === 'checkout' ? state.checkoutPromo : state.orderPromo;
  const input = document.getElementById('promoInput-' + where);
  s.code = ((input && input.value) || s.code || '').trim();
  if (!s.code) {
    s.error = 'Введите промокод.';
    render();
    return;
  }
  guard(async () => {
    try {
      if (where === 'checkout') {
        const data = await api('/api/promo/check', { method: 'POST', body: { code: s.code, app: state.checkoutApp } });
        s.check = data;
        s.error = '';
        toast('Скидка по промокоду ' + data.code + ': −' + data.discountText);
      } else {
        const data = await api('/api/order/promo', {
          method: 'POST', body: { orderId: state.boot.order.id, code: s.code },
        });
        state.boot.order = data.order;
        state.orderPromo = { open: false, code: '', error: '' };
        toast(data.message);
      }
    } catch (err) {
      s.error = err.message;
      haptic('error');
    }
    render();
  });
}

function removeBuyerPromo(where) {
  if (where === 'checkout') {
    state.checkoutPromo = { code: '', check: null, error: '' };
    render();
    return;
  }
  guard(async () => {
    const data = await api('/api/order/promo/remove', { method: 'POST', body: { orderId: state.boot.order.id } });
    state.boot.order = data.order;
    toast('Промокод убран');
    render();
  });
}

function handleAction(raw) {
  if (actions[raw]) { haptic('light'); actions[raw](); return; }

  const [kind, value] = raw.split(':');
  if (kind === 'tab') {
    state.view = value;
    state.seller.current = null;
    state.seller.promo.current = null;
    state.seller.promo.form = null;
    state.supplier.current = null;
    haptic('light');
    if (value === 'seller') {
      guard(async () => {
        if (state.seller.mode === 'analytics') await loadAnalytics();
        else if (state.seller.mode === 'promos') await loadPromos();
        else await loadSellerOrders();
        render();
      });
    }
    else if (value === 'supplier') guard(async () => { await loadSupplierOrders(); render(); });
    else if (value === 'order') guard(async () => { await refreshOrder(); render(); });
    else render();
    return;
  }
  if (kind === 'filter') { state.filter = value; render(); return; }
  if (kind === 'doc') { haptic('light'); openDoc(value); return; }
  if (kind === 'copy') { haptic('light'); copyValue(value); return; }
  if (kind === 'bot') {
    haptic('light');
    const url = 'https://t.me/' + value;
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, '_blank');
    return;
  }
  if (kind === 'ipa') {
    haptic('light');
    const url = location.origin + '/ipa/' + value;
    // Внутри Telegram файл сохраняет системный браузер: его и открываем.
    if (tg && tg.openLink) tg.openLink(url);
    else window.location.href = url;
    return;
  }
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
  if (kind === 'seller-sort') {
    sellerPrefs(state.seller.status).sort = value;
    sellerPrefsChanged();
    return;
  }
  if (kind === 'seller-st') { toggleSellerStatus(value); return; }
  if (kind === 'seller-period') {
    sellerPrefs(state.seller.status).period = value || '';
    sellerPrefsChanged();
    return;
  }
  if (kind === 'seller-udid') {
    sellerPrefs(state.seller.status).udid = value || '';
    sellerPrefsChanged();
    return;
  }
  if (kind === 'seller-mode') {
    state.seller.mode = value;
    if (value === 'analytics') guard(async () => { await loadAnalytics(); render(); });
    else if (value === 'promos') guard(async () => { await loadPromos(); render(); });
    else guard(async () => { await loadSellerOrders(); render(); });
    return;
  }
  if (kind === 'bp-apply') { applyBuyerPromo(value); return; }
  if (kind === 'bp-remove') { removeBuyerPromo(value); return; }
  if (kind === 'promo-filter') {
    state.seller.promo.status = value;
    guard(async () => { await loadPromos(); render(); });
    return;
  }
  if (kind === 'promo-open') {
    guard(async () => { await openPromo(value); render(); });
    return;
  }
  if (kind === 'pf-kind') {
    readPromoForm();
    if (state.seller.promo.form) state.seller.promo.form.kind = value;
    render();
    return;
  }
  if (kind === 'an-period') {
    analyticsPeriod().key = value;
    saveAnalyticsPeriod();
    guard(async () => { await loadAnalytics(); render(); });
    return;
  }
  if (kind === 'seller-filter') {
    state.seller.status = value;
    guard(async () => { await loadSellerOrders(); render(); });
    return;
  }
  if (kind === 'seller-open') {
    state.seller.current = state.seller.orders.find((o) => String(o.id) === value) || null;
    state.seller.confirmDelete = false;
    render();
    return;
  }
  if (kind === 'supplier-open') {
    state.supplier.current = state.supplier.orders.find((o) => String(o.id) === value) || null;
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
  // Ссылку открываем сами: внутри Telegram это делает openTelegramLink,
  // а href остаётся на случай, если скрипт не сработал.
  if (target.tagName === 'A') event.preventDefault();
  handleAction(target.getAttribute('data-action'));
});

/* Волна от точки нажатия: координаты знает только сам обработчик. */
document.addEventListener('pointerdown', (event) => {
  const btn = event.target.closest('.btn');
  if (!btn || btn.disabled) return;
  const box = btn.getBoundingClientRect();
  btn.style.setProperty('--x', (event.clientX - box.left) + 'px');
  btn.style.setProperty('--y', (event.clientY - box.top) + 'px');
  btn.classList.remove('rippling');
  void btn.offsetWidth;
  btn.classList.add('rippling');
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

/* Свежие клиенты рисуют поверх страницы свои кнопки сверху. Telegram сообщает,
   сколько места они занимают, — иначе наша шапка уезжает под них. */
function applyInsets() {
  if (!tg) return;
  const safe = tg.safeAreaInset || {};
  const content = tg.contentSafeAreaInset || {};
  const top = (safe.top || 0) + (content.top || 0);
  document.documentElement.style.setProperty('--tg-top', top + 'px');
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
  applyInsets();

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.onEvent) {
      tg.onEvent('themeChanged', applyTheme);
      tg.onEvent('safeAreaChanged', applyInsets);
      tg.onEvent('contentSafeAreaChanged', applyInsets);
    }

    if (tg.BackButton) {
      tg.BackButton.onClick(() => {
        if (state.view === 'doc') { handleAction('doc-back'); return; }
        if (state.view === 'seller' && promoNested()) {
          handleAction(state.seller.promo.form ? 'promo-form-back' : 'promo-back');
          return;
        }
        if (state.seller.current) state.seller.current = null;
        else if (state.supplier.current) state.supplier.current = null;
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
    // Открыли в браузере, а не из бота: показываем витрину только для чтения.
    if (tg && tg.initData) {
      state.error = err.message;
    } else {
      try {
        state.boot = await api('/api/public');
        state.public = true;
      } catch (publicErr) {
        state.error = publicErr.message;
      }
    }
  }
  render();
}

boot();
