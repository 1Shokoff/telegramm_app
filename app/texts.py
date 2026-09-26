from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from . import catalog, const, udid as udid_mod
from .config import Config

DONE_MARK = "✅"
CURRENT_MARK = "🔸"
WAIT_MARK = "▫️"


def money(rub: int) -> str:
    return "{:,} ₽".format(rub).replace(",", " ")


def apps_count(n: int) -> str:
    """«1 приложение», «2 приложения», «24 приложения», «25 приложений»."""
    tail = n % 100
    last = n % 10
    if 11 <= tail <= 14 or last == 0 or last >= 5:
        word = "приложений"
    elif last == 1:
        word = "приложение"
    else:
        word = "приложения"
    return "%d %s" % (n, word)


def e(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=False)


def when(iso: str) -> str:
    """Метки времени в базе в UTC — показываем во времени сервера."""
    try:
        moment = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return e(iso or "")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%d.%m %H:%M")


def user_line(order: dict) -> str:
    name = e(order.get("first_name") or "Покупатель")
    username = order.get("username")
    tail = " @" + e(username) if username else ""
    return "%s%s · <code>%s</code>" % (name, tail, order["user_id"])


def steps_block(order: dict) -> str:
    """Четыре шага воронки с отметками — как на экране «Мой заказ»."""
    step = const.STEP.get(order["status"], 0)
    labels = (
        "Оплата получена",
        "UDID передан",
        "Сертификат установлен",
        "Инструкция готова",
    )
    lines = []
    for i, label in enumerate(labels, start=1):
        if step > i:
            mark = DONE_MARK
        elif step == i:
            mark = CURRENT_MARK
        else:
            mark = WAIT_MARK
        lines.append("%s %s" % (mark, label))
    return "\n".join(lines)


HINTS = {
    const.NEW: "Оплатите заказ, чтобы продолжить.",
    const.PAYMENT_CHECK: "Мы проверяем оплату. Обычно это занимает немного времени.",
    const.PAID: "Пришлите UDID вашего iPhone — 40 символов.",
    const.UDID: "UDID у продавца. Идёт подготовка сертификата.",
    const.INSTALLED: "Сертификат готов, собираем инструкцию по установке.",
    const.DONE: "Заказ закрыт. Инструкция отправлена в этот чат.",
    const.CANCELLED: "Заказ отменён. Новый можно оформить командой /start.",
}


def buyer_order_card(order: dict) -> str:
    head = "<b>Заказ %s</b>\n%s · %s" % (
        e(order["code"]),
        e(catalog.product_name(order.get("app_slug"))),
        money(order["price_rub"]),
    )
    status = "Статус: <b>%s</b>" % e(const.TITLES.get(order["status"], order["status"]))
    body = steps_block(order)
    parts = [head, status, body]
    if order.get("device_udid"):
        parts.append("UDID: <code>%s</code>" % udid_mod.mask(order["device_udid"]))
    hint = HINTS.get(order["status"])
    if hint:
        parts.append("<i>%s</i>" % e(hint))
    return "\n\n".join(parts)


def seller_order_card(order: dict) -> str:
    lines = [
        "<b>Заказ %s</b> · %s" % (e(order["code"]), e(const.TITLES.get(order["status"], "—"))),
        "Покупатель: %s" % user_line(order),
        "Товар: %s" % e(catalog.product_name(order.get("app_slug"))),
        "Сумма: %s · оплата: %s" % (money(order["price_rub"]), e(order["payment_mode"])),
    ]
    if order.get("device_udid"):
        lines.append("UDID: <code>%s</code>" % e(order["device_udid"]))
    else:
        lines.append("UDID: —")
    if order.get("payment_ref"):
        lines.append("Платёж: <code>%s</code>" % e(order["payment_ref"]))
    if order.get("seller_note"):
        lines.append("Заметка: %s" % e(order["seller_note"]))
    lines.append("Создан: %s" % when(order["created_at"]))
    return "\n".join(lines)


def welcome(first_name: str | None, price_rub: int, apps_count: int) -> str:
    name = e(first_name or "")
    hello = "Привет, %s!" % name if name else "Привет!"
    return (
        "%s\n\n"
        "<b>iApki — твои приложения снова на iPhone.</b>\n"
        "Весь каталог из %d приложений за %s. "
        "Без лимита установок и доплат за приложения.\n\n"
        "Как это работает:\n"
        "1. Оплата\n"
        "2. Вы присылаете UDID iPhone\n"
        "3. Мы ставим сертификат\n"
        "4. Вы получаете инструкцию\n"
    ) % (hello, apps_count, money(price_rub))


def payment_instructions(order: dict, cfg: Config) -> str:
    """Реквизиты перевода. Значения — в <code>: в Telegram они копируются нажатием."""
    lines = [
        "<b>Оплата заказа %s</b>" % e(order["code"]),
        "%s · <b>%s</b>" % (
            e(catalog.product_name(order.get("app_slug"))), money(order["price_rub"])
        ),
        "",
    ]

    rows = cfg.requisites
    if rows:
        for row in rows:
            value = "<code>%s</code>" % e(row["value"]) if row["copy"] else e(row["value"])
            lines.append("%s: %s" % (e(row["label"]), value))
        lines.append("")
        lines.append("<i>Нажмите на значение, чтобы скопировать.</i>")
    elif cfg.payment_details:
        lines.append(e(cfg.payment_details))
    else:
        lines.append("Реквизиты уточните у продавца.")

    if rows and cfg.payment_details:
        lines.append("")
        lines.append(e(cfg.payment_details))

    lines.append("")
    lines.append(
        "Укажите в комментарии к платежу номер заказа <code>%s</code>, "
        "затем нажмите «Я оплатил» — продавец подтвердит поступление." % e(order["code"])
    )
    lines.append("")
    lines.append(
        "Чек или скриншот перевода можно прислать сюда картинкой или файлом — "
        "продавец увидит его в заказе."
    )
    return "\n".join(lines)


def receipt_from_buyer(order: dict, title: str) -> str:
    return "🧾 <b>Чек по заказу %s</b>\n%s\n\n%s" % (
        e(order["code"]), user_line(order), e(title)
    )


def udid_request(guide_url: str = "") -> str:
    lines = [
        "<b>Теперь добавьте iPhone</b>",
        "",
        "Нужен <b>UDID</b> — идентификатор устройства из 40 символов "
        "(цифры и латинские буквы от a до f).",
        "",
        "<b>Как узнать UDID прямо на iPhone, без компьютера</b>",
        "1. Откройте в Safari сайт <b>udid.tech</b>",
        "2. Нажмите <b>Get My UDID</b> → <b>Разрешить</b> загрузку профиля",
        "3. Зайдите в <b>Настройки → Профиль загружен → Установить</b>. "
        "Если попросит — введите код-пароль iPhone",
        "4. После установки откроется страница с вашим UDID — скопируйте его",
        "",
        "Отправьте номер сообщением в этот чат.",
    ]
    if guide_url:
        lines.append("")
        lines.append('<a href="%s">Инструкция со скриншотами</a>' % e(guide_url))
    return "\n".join(lines)


def udid_accepted(order: dict) -> str:
    return (
        "UDID принят: <code>%s</code>\n\n"
        "Заказ %s передан продавцу. Следующий шаг — установка сертификата."
    ) % (udid_mod.mask(order["device_udid"]), e(order["code"]))


def help_text(support_username: str, has_webapp: bool) -> str:
    lines = [
        "<b>Помощь</b>",
        "",
        "/start — витрина и новый заказ",
        "/order — статус вашего заказа",
        "/udid — как найти UDID",
        "/about — документы, контакты и реквизиты",
        "/forget — удалить мои данные из бота",
    ]
    if has_webapp:
        lines.append("")
        lines.append("Кнопка «Открыть приложение» показывает каталог и статус заказа.")
    if support_username:
        lines.append("")
        lines.append("Живой человек: @%s" % e(support_username))
    return "\n".join(lines)


def doc_links(cfg: Config) -> str:
    """Строка со ссылками на документы; пустая, пока документов нет."""
    links = []
    for key, title, _hint in const.DOCUMENTS:
        url = cfg.doc_url(key)
        if url:
            links.append('<a href="%s">%s</a>' % (e(url), e(title)))
    return " · ".join(links)


def legal_footer(cfg: Config) -> str:
    """Подвал сообщения: реквизиты и документы — то же, что в витрине."""
    lines = []
    if cfg.legal_line:
        lines.append(e(cfg.legal_line))
    links = doc_links(cfg)
    if links:
        lines.append(links)
    if not lines:
        return ""
    return "\n\n<i>%s</i>" % "\n".join(lines)


CONSENT_NOTE = (
    "Оформляя заказ, вы принимаете условия публичной оферты и даёте согласие "
    "на обработку персональных данных."
)


def about(cfg: Config) -> str:
    """Экран «О нас»: что за сервис, документы, контакты и реквизиты.

    Те же сведения показывает Mini App. Платёжная система проверяет,
    что покупатель видит их до оплаты, поэтому не готовые пункты
    показываем честно, а не прячем.
    """
    lines = [
        "<b>О сервисе iApki</b>",
        "",
        "Ставим на ваш iPhone приложения, которых нет в App Store: "
        "в каталоге %s. Оплата → вы присылаете UDID → мы ставим "
        "сертификат → вы получаете инструкцию." % apps_count(catalog.count()),
        "",
        "<b>Документы</b>",
    ]
    for key, title, _hint in const.DOCUMENTS:
        url = cfg.doc_url(key)
        lines.append(
            '• <a href="%s">%s</a>' % (e(url), e(title)) if url
            else "• %s — готовится" % e(title)
        )

    contacts = []
    if cfg.contact_email:
        contacts.append("Почта: %s" % e(cfg.contact_email))
    if cfg.contact_phone:
        contacts.append("Телефон: %s" % e(cfg.contact_phone))
    if cfg.support_username:
        contacts.append("Telegram: @%s" % e(cfg.support_username))
    lines.append("")
    lines.append("<b>Контакты</b>")
    lines.extend(contacts or ["Появятся здесь до открытия продаж."])

    lines.append("")
    lines.append("<b>Реквизиты</b>")
    lines.append(e(cfg.legal_line) if cfg.legal_line else "Появятся здесь до открытия продаж.")
    return "\n".join(lines)


def chat_from_buyer(order: dict, body: str) -> str:
    return "💬 <b>Сообщение по заказу %s</b>\nОт: %s\n\n%s" % (
        e(order["code"]),
        user_line(order),
        e(body),
    )


def chat_from_seller(order: dict, body: str) -> str:
    return "💬 <b>Продавец по заказу %s</b>\n\n%s" % (e(order["code"]), e(body))


def chat_history(order: dict, messages: list[dict]) -> str:
    if not messages:
        return "По заказу <b>%s</b> переписки ещё нет." % e(order["code"])
    lines = ["<b>Переписка по заказу %s</b>" % e(order["code"]), ""]
    for msg in messages:
        who = "Продавец" if msg["author"] == "seller" else "Покупатель"
        lines.append(
            "<b>%s</b> · %s\n%s"
            % (who, when(msg["created_at"]), e(msg["text"]))
        )
    return "\n\n".join(lines)


def notify_screen(
    items: tuple[tuple[str, str, str], ...],
    prefs: dict[str, bool],
    order: dict | None,
    mode: str,
) -> str:
    lines = ["<b>Уведомления</b>", "Нажмите на пункт, чтобы включить или выключить.", ""]
    for key, title, hint in items:
        on = prefs.get(key, True)
        lines.append("%s <b>%s</b>\n<i>%s</i>" % ("🔔" if on else "🔕", title, e(hint)))
    if order:
        lines.append("")
        lines.append(
            "<b>Заказ %s:</b> %s\n<i>Настройка по заказу сильнее общей.</i>"
            % (e(order["code"]), e(const.ORDER_NOTIFY_TITLES[mode]))
        )
    lines.append("")
    lines.append(
        "<i>Статус заказа и инструкция всегда доступны в приложении, "
        "даже если уведомления выключены.</i>"
    )
    return "\n".join(lines)


CHAT_HINT_BUYER = (
    "Просто напишите сообщение в этот чат — продавец увидит его в карточке заказа "
    "и ответит здесь же."
)


PRIVACY = (
    "UDID нужен только для выпуска сертификата под ваше устройство. "
    "Он виден продавцу и хранится до закрытия заказа. "
    "Команда /forget удаляет ваши заказы и UDID из базы бота."
)
