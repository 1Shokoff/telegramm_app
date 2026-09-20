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
