from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .. import db, keyboards, texts
from ..config import Config

log = logging.getLogger(__name__)
router = Router(name="supplier")

LIST_LIMIT = 20


def setup(cfg: Config) -> Router:
    """Роутер работает только для аккаунтов из SUPPLIER_IDS.

    Поставщик видит номер заказа, покупателя, время создания и UDID —
    и ничего больше: ни сумм, ни переписки, ни кнопок управления заказом.
    """
    suppliers = set(cfg.supplier_ids)
    router.message.filter(F.from_user.id.in_(suppliers))
    router.callback_query.filter(F.from_user.id.in_(suppliers))
    return router


async def _card(order: dict) -> tuple[str, InlineKeyboardMarkup]:
    user = await db.get_user(order["user_id"]) or {}
    full = dict(order)
    full.setdefault("username", user.get("username"))
    full.setdefault("first_name", user.get("first_name"))
    return texts.supplier_order_card(full), keyboards.supplier_order_kb(order)


# ------------------------------------------------------------------- команды


@router.message(Command("udids"))
async def cmd_udids(message: Message) -> None:
    orders = await db.list_orders_with_udid(limit=LIST_LIMIT)
    if not orders:
        await message.answer("Присланных UDID пока нет.")
        return

    rows = [
        [
            InlineKeyboardButton(
                text="%s · %s" % (o["code"], texts.when(o["created_at"])),
                callback_data=keyboards.cb("ucard", o["id"]),
            )
        ]
        for o in orders
    ]
    await message.answer(
        "<b>Заказы с UDID</b> · %d" % len(orders),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("supplierhelp"))
async def cmd_supplierhelp(message: Message) -> None:
    await message.answer(
        "<b>Команды поставщика</b>\n\n"
        "/udids — заказы с присланным UDID\n\n"
        "О новом заказе бот пишет сам — в уведомлении есть кнопка «Открыть заказ». "
        "В карточке видно номер заказа, покупателя, время создания и UDID, "
        "а кнопка «Скопировать UDID» кладёт номер в буфер обмена."
    )


# ------------------------------------------------------------------- кнопки


@router.callback_query(F.data.startswith("o:ucard:"))
async def cb_card(call: CallbackQuery) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    order = await db.get_order(parsed[1])
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    text, kb = await _card(order)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        # Уведомление могло быть картинкой или уже содержать этот же текст.
        await call.message.answer(text, reply_markup=kb)
    await call.answer()
