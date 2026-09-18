from __future__ import annotations

NEW = "new"
PAYMENT_CHECK = "payment_check"
PAID = "paid"
IMEI = "imei"
INSTALLED = "installed"
DONE = "done"
CANCELLED = "cancelled"

ACTIVE_STATUSES = (NEW, PAYMENT_CHECK, PAID, IMEI, INSTALLED, DONE)
OPEN_STATUSES = (NEW, PAYMENT_CHECK, PAID, IMEI, INSTALLED)

# Подпись статуса: для покупателя и для продавца.
TITLES = {
    NEW: "Ожидает оплаты",
    PAYMENT_CHECK: "Оплата на проверке",
    PAID: "Нужен IMEI",
    IMEI: "Ставим сертификат",
    INSTALLED: "Готовим инструкцию",
    DONE: "Готово",
    CANCELLED: "Отменён",
}

# Шаг воронки (1..4), по которому подсвечивается прогресс в Mini App.
STEP = {
    NEW: 1,
    PAYMENT_CHECK: 1,
    PAID: 2,
    IMEI: 3,
    INSTALLED: 4,
    DONE: 5,
    CANCELLED: 0,
}

STEP_NAMES = ("Оплата", "IMEI", "Установка", "Инструкция")
