from __future__ import annotations

from html import escape

from . import const, udid as udid_mod

DONE_MARK = "✅"
CURRENT_MARK = "🔸"
WAIT_MARK = "▫️"


def money(rub: int) -> str:
    return "{:,} ₽".format(rub).replace(",", " ")


def e(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=False)


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
        e(order.get("product_title") or "Доступ для iPhone"),
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
    lines.append("Создан: %s" % e(order["created_at"].replace("T", " ")[:16]))
    return "\n".join(lines)


def welcome(first_name: str | None, price_rub: int, apps_count: int) -> str:
    name = e(first_name or "")
    hello = "Привет, %s!" % name if name else "Привет!"
    return (
        "%s\n\n"
        "<b>Все приложения. Одна цена.</b>\n"
        "Весь каталог из %d приложений за %s. "
        "Без лимита установок и доплат за приложения.\n\n"
        "Как это работает:\n"
        "1. Оплата\n"
        "2. Вы присылаете UDID iPhone\n"
        "3. Мы ставим сертификат\n"
        "4. Вы получаете инструкцию\n"
    ) % (hello, apps_count, money(price_rub))


def payment_instructions(order: dict, details: str) -> str:
    body = e(details) if details else "Реквизиты уточните у продавца."
    return (
        "<b>Оплата заказа %s</b>\n"
        "Сумма: <b>%s</b>\n\n"
        "%s\n\n"
        "Укажите в комментарии к платежу номер заказа <code>%s</code>, "
        "затем нажмите «Я оплатил» — продавец подтвердит поступление."
    ) % (e(order["code"]), money(order["price_rub"]), body, e(order["code"]))


def udid_request() -> str:
    return (
        "<b>Теперь добавьте iPhone</b>\n\n"
        "Нужен <b>UDID</b> — идентификатор устройства из 40 символов "
        "(цифры и латинские буквы от a до f).\n\n"
        "<b>Где его взять</b>\n"
        "Подключите iPhone к компьютеру кабелем.\n"
        "• macOS: Finder → ваш iPhone → строка под именем устройства. "
        "Нажимайте на неё, пока не появится UDID, затем правый клик → «Скопировать».\n"
        "• Windows: iTunes → значок устройства → «Обзор» → нажмите на «Серийный номер», "
        "он сменится на UDID, затем правый клик → «Скопировать».\n\n"
        "Отправьте номер сообщением в этот чат."
    )


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
        "/forget — удалить мои данные из бота",
    ]
    if has_webapp:
        lines.append("")
        lines.append("Кнопка «Открыть приложение» показывает каталог и статус заказа.")
    if support_username:
        lines.append("")
        lines.append("Живой человек: @%s" % e(support_username))
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
            % (who, e(msg["created_at"].replace("T", " ")[5:16]), e(msg["text"]))
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
