from __future__ import annotations

NEW = "new"
PAYMENT_CHECK = "payment_check"
PAID = "paid"
UDID = "udid"
INSTALLED = "installed"
DONE = "done"
CANCELLED = "cancelled"

ACTIVE_STATUSES = (NEW, PAYMENT_CHECK, PAID, UDID, INSTALLED, DONE)
OPEN_STATUSES = (NEW, PAYMENT_CHECK, PAID, UDID, INSTALLED)

# Подпись статуса: для покупателя и для продавца.
TITLES = {
    NEW: "Ожидает оплаты",
    PAYMENT_CHECK: "Оплата на проверке",
    PAID: "Нужен UDID",
    UDID: "Ставим сертификат",
    INSTALLED: "Готовим инструкцию",
    DONE: "Готово",
    CANCELLED: "Отменён",
}

# Шаг воронки (1..4), по которому подсвечивается прогресс в Mini App.
STEP = {
    NEW: 1,
    PAYMENT_CHECK: 1,
    PAID: 2,
    UDID: 3,
    INSTALLED: 4,
    DONE: 5,
    CANCELLED: 0,
}

STEP_NAMES = ("Оплата", "UDID", "Установка", "Инструкция")

# ------------------------------------------------------------- уведомления

NOTIFY_STATUS = "status"
NOTIFY_CHAT = "chat"
NOTIFY_NEW_ORDER = "new_order"
NOTIFY_PAYMENT = "payment"
NOTIFY_UDID = "udid_received"
NOTIFY_HELP = "help"

# (ключ, заголовок, пояснение) — порядок сохраняется в настройках.
BUYER_NOTIFICATIONS = (
    (NOTIFY_STATUS, "Статус заказа", "Оплата подтверждена, сертификат установлен, инструкция готова"),
    (NOTIFY_CHAT, "Сообщения продавца", "Ответы в переписке по заказу"),
)

SELLER_NOTIFICATIONS = (
    (NOTIFY_NEW_ORDER, "Новые заказы", "Кто-то оформил заказ"),
    (NOTIFY_PAYMENT, "Оплаты", "Покупатель нажал «Я оплатил»"),
    (NOTIFY_UDID, "Полученные UDID", "Покупатель прислал номер устройства"),
    (NOTIFY_CHAT, "Сообщения покупателей", "Вопросы по заказам"),
    (NOTIFY_HELP, "Запросы помощи", "Кнопка «Помощь по заказу»"),
)


def notifications_for(is_seller: bool) -> tuple[tuple[str, str, str], ...]:
    return SELLER_NOTIFICATIONS if is_seller else BUYER_NOTIFICATIONS


def notification_title(kind: str) -> str:
    for key, title, _hint in SELLER_NOTIFICATIONS + BUYER_NOTIFICATIONS:
        if key == kind:
            return title
    return kind


# Режимы для отдельного заказа: значение по умолчанию берётся из общих настроек.
ORDER_NOTIFY_DEFAULT = "default"
ORDER_NOTIFY_ON = "on"
ORDER_NOTIFY_OFF = "off"

ORDER_NOTIFY_TITLES = {
    ORDER_NOTIFY_DEFAULT: "По общим настройкам",
    ORDER_NOTIFY_ON: "Всегда уведомлять",
    ORDER_NOTIFY_OFF: "Не беспокоить",
}
