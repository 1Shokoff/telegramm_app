from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from .. import db, keyboards, texts
from ..config import Config
from ..service import OrderService, ServiceError

log = logging.getLogger(__name__)
router = Router(name="payments")

PAYLOAD_PREFIX = "order:"


def payload_for(order_id: int) -> str:
    return PAYLOAD_PREFIX + str(order_id)


def order_id_from_payload(payload: str) -> int | None:
    if not payload.startswith(PAYLOAD_PREFIX):
        return None
    try:
        return int(payload[len(PAYLOAD_PREFIX):])
    except ValueError:
        return None


async def create_invoice_link(bot: Bot, cfg: Config, order: dict) -> str:
    """Ссылка на инвойс для openInvoice() в Mini App."""
    return await bot.create_invoice_link(
        title=cfg.product_title,
        description="Заказ %s · %s" % (order["code"], cfg.product_subtitle),
        payload=payload_for(order["id"]),
        currency="XTR",
        prices=[LabeledPrice(label=cfg.product_title, amount=cfg.price_stars)],
    )


@router.callback_query(F.data.startswith("o:stars:"))
async def cb_stars(call: CallbackQuery, bot: Bot, cfg: Config) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed or cfg.payment_mode != "stars":
        await call.answer()
        return
    order = await db.get_order(parsed[1])
    if not order or order["user_id"] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return

    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=cfg.product_title,
        description="Заказ %s · %s" % (order["code"], cfg.product_subtitle),
        payload=payload_for(order["id"]),
        currency="XTR",
        prices=[LabeledPrice(label=cfg.product_title, amount=cfg.price_stars)],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    order_id = order_id_from_payload(query.invoice_payload or "")
    order = await db.get_order(order_id) if order_id else None
    if not order:
        await query.answer(ok=False, error_message="Заказ не найден, оформите заново.")
        return
    if order["user_id"] != query.from_user.id:
        await query.answer(ok=False, error_message="Заказ оформлен на другой аккаунт.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, service: OrderService) -> None:
    sp = message.successful_payment
    order_id = order_id_from_payload(sp.invoice_payload or "")
    if not order_id:
        log.warning("Оплата с неизвестным payload: %s", sp.invoice_payload)
        return
    charge_id = sp.telegram_payment_charge_id or ""
    try:
        await service.mark_paid_external(order_id, charge_id)
    except ServiceError as exc:
        log.warning("Оплата заказа %s: %s", order_id, exc)
        return
    # ID платежа нужен для возврата через refundStarPayment.
    log.info("Заказ %s оплачен, charge_id=%s", order_id, charge_id)
    await message.answer(
        "Платёж получен. Код операции: <code>%s</code>" % texts.e(charge_id)
    )
