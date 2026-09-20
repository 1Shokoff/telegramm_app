from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)

from . import const
from .config import Config


def _rows(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def cb(action: str, order_id: int) -> str:
    return "o:%s:%d" % (action, order_id)


def parse_cb(data: str) -> tuple[str, int] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "o":
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


def webapp_button(cfg: Config, text: str = "Открыть приложение") -> InlineKeyboardButton | None:
    if not cfg.webapp_enabled:
        return None
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=cfg.webapp_url))


def start_kb(cfg: Config, has_order: bool) -> InlineKeyboardMarkup:
    first = []
    wa = webapp_button(cfg)
    if wa:
        first.append(wa)
    second = [InlineKeyboardButton(text="Мой заказ", callback_data="nav:order")] if has_order else [
        InlineKeyboardButton(text="Оформить заказ · весь каталог", callback_data="nav:buy")
    ]
    third = [
        InlineKeyboardButton(text="Каталог", callback_data="nav:catalog"),
        InlineKeyboardButton(text="Помощь", callback_data="nav:help"),
    ]
    return _rows(first, second, third)


def confirm_order_kb() -> InlineKeyboardMarkup:
    return _rows(
        [InlineKeyboardButton(text="Оформить заказ", callback_data="nav:buy_confirm")],
        [InlineKeyboardButton(text="Условия", callback_data="nav:terms")],
    )


def payment_kb(order_id: int, mode: str) -> InlineKeyboardMarkup:
    if mode == "demo":
        pay = [InlineKeyboardButton(text="Имитировать оплату", callback_data=cb("demopay", order_id))]
    elif mode == "stars":
        pay = [InlineKeyboardButton(text="Оплатить в Stars", callback_data=cb("stars", order_id))]
    else:
        pay = [InlineKeyboardButton(text="Я оплатил", callback_data=cb("claim", order_id))]
    return _rows(pay, [InlineKeyboardButton(text="Отменить заказ", callback_data=cb("cancel", order_id))])


def buyer_order_kb(cfg: Config, order: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    status = order["status"]
    if status == const.NEW:
        return payment_kb(order["id"], order["payment_mode"])
    if status == const.PAID:
        rows.append([InlineKeyboardButton(text="Как найти UDID", callback_data="nav:udid_help")])
    rows.append([InlineKeyboardButton(text="Обновить статус", callback_data=cb("refresh", order["id"]))])
    wa = webapp_button(cfg, "Открыть приложение")
    if wa:
        rows.append([wa])
    rows.append(
        [
            InlineKeyboardButton(text="💬 Написать продавцу", callback_data="nav:chat"),
            InlineKeyboardButton(text="Помощь", callback_data=cb("help", order["id"])),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def seller_order_kb(order: dict) -> InlineKeyboardMarkup:
    """Кнопки продавца зависят от того, чей сейчас ход."""
    oid = order["id"]
    status = order["status"]
    rows: list[list[InlineKeyboardButton]] = []

    if status in (const.NEW, const.PAYMENT_CHECK):
        rows.append(
            [
                InlineKeyboardButton(text="✅ Оплата получена", callback_data=cb("payok", oid)),
                InlineKeyboardButton(text="✖️ Отклонить", callback_data=cb("payno", oid)),
            ]
        )
    elif status == const.PAID:
        rows.append([InlineKeyboardButton(text="⏳ Ждём UDID от покупателя", callback_data=cb("noop", oid))])
    elif status == const.UDID:
        rows.append([InlineKeyboardButton(text="📲 Сертификат установлен", callback_data=cb("installed", oid))])
    elif status == const.INSTALLED:
        rows.append([InlineKeyboardButton(text="📄 Отправить инструкцию", callback_data=cb("instr", oid))])

    rows.append(
        [
            InlineKeyboardButton(text="💬 Написать", callback_data=cb("chat", oid)),
            InlineKeyboardButton(text="🔄 Обновить", callback_data=cb("card", oid)),
        ]
    )
    if status in const.OPEN_STATUSES:
        rows.append([InlineKeyboardButton(text="🚫 Отменить заказ", callback_data=cb("scancel", oid))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def seller_chat_kb(order_id: int) -> InlineKeyboardMarkup:
    """Под сообщением покупателя: ответить, не открывая карточку."""
    return _rows(
        [
            InlineKeyboardButton(text="💬 Ответить", callback_data=cb("chat", order_id)),
            InlineKeyboardButton(text="Карточка", callback_data=cb("card", order_id)),
        ]
    )


def instruction_prompt_kb(order_id: int, has_template: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_template:
        rows.append([InlineKeyboardButton(text="Отправить шаблон", callback_data=cb("tmpl", order_id))])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=cb("card", order_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)
