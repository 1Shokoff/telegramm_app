from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from . import const, db, keyboards, texts, udid as udid_mod
from .config import Config

log = logging.getLogger(__name__)


class ServiceError(Exception):
    """Ошибка бизнес-правила: текст можно показывать пользователю."""


class OrderService:
    """Один слой правил для чата и Mini App, чтобы они не разъезжались."""

    def __init__(self, bot: Bot, cfg: Config) -> None:
        self.bot = bot
        self.cfg = cfg

    # ------------------------------------------------------------ отправка

    async def send(
        self, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None
    ) -> bool:
        try:
            await self.bot.send_message(chat_id, text, reply_markup=kb)
            return True
        except TelegramForbiddenError:
            log.info("Пользователь %s заблокировал бота", chat_id)
            await db.set_blocked(chat_id, True)
        except TelegramBadRequest as exc:
            log.warning("Не отправили сообщение %s: %s", chat_id, exc)
        return False

    async def notify_sellers(self, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
        for seller_id in self.cfg.seller_ids:
            await self.send(seller_id, text, kb)

    async def push_order_to_sellers(self, order: dict, header: str) -> None:
        full = await self._with_user(order)
        text = header + "\n\n" + texts.seller_order_card(full)
        await self.notify_sellers(text, keyboards.seller_order_kb(order))

    async def _with_user(self, order: dict) -> dict:
        if "username" in order:
            return order
        user = await db.get_user(order["user_id"]) or {}
        merged = dict(order)
        merged["username"] = user.get("username")
        merged["first_name"] = user.get("first_name")
        return merged

    async def notify_buyer(self, order: dict, text: str) -> None:
        await self.send(order["user_id"], text, keyboards.buyer_order_kb(self.cfg, order))

    # ------------------------------------------------------------- заказы

    async def get_or_create_order(self, user_id: int) -> tuple[dict, bool]:
        """Возвращает (заказ, создан_ли). Один открытый заказ на пользователя."""
        existing = await db.get_active_order(user_id)
        if existing and existing["status"] in const.OPEN_STATUSES:
            return existing, False

        order = await db.create_order(
            user_id=user_id,
            price_rub=self.cfg.price_rub,
            payment_mode=self.cfg.payment_mode,
            prefix=self.cfg.order_prefix,
        )
        await self.push_order_to_sellers(order, "🆕 <b>Новый заказ</b>")
        return order, True

    async def _load(self, order_id: int) -> dict:
        order = await db.get_order(order_id)
        if not order:
            raise ServiceError("Заказ не найден.")
        return order

    async def claim_payment(self, order_id: int, actor_id: int) -> dict:
        """Покупатель нажал «Я оплатил» (режим manual)."""
        order = await self._load(order_id)
        if order["user_id"] != actor_id:
            raise ServiceError("Это не ваш заказ.")
        if order["status"] == const.PAYMENT_CHECK:
            raise ServiceError("Оплата уже на проверке — ждём продавца.")
        if order["status"] != const.NEW:
            raise ServiceError("Оплата по этому заказу уже подтверждена.")

        updated = await db.set_status(
            order_id, const.PAYMENT_CHECK, "user:%d" % actor_id, "покупатель заявил оплату"
        )
        assert updated
        await self.push_order_to_sellers(updated, "💳 <b>Покупатель заявил оплату</b>")
        return updated

    async def confirm_payment(self, order_id: int, actor: str, ref: str | None = None) -> dict:
        order = await self._load(order_id)
        if order["status"] not in (const.NEW, const.PAYMENT_CHECK):
            raise ServiceError("Заказ уже оплачен.")
        if ref:
            await db.set_payment_ref(order_id, ref)
        updated = await db.set_status(order_id, const.PAID, actor, "оплата подтверждена")
        assert updated
        await self.send(
            updated["user_id"],
            "✅ <b>Оплата получена</b>\n\n" + texts.udid_request(),
            keyboards.buyer_order_kb(self.cfg, updated),
        )
        return updated

    async def reject_payment(self, order_id: int, actor: str) -> dict:
        order = await self._load(order_id)
        if order["status"] != const.PAYMENT_CHECK:
            raise ServiceError("Нечего отклонять: оплата не заявлена.")
        updated = await db.set_status(order_id, const.NEW, actor, "оплата не найдена")
        assert updated
        await self.send(
            updated["user_id"],
            "Платёж по заказу <b>%s</b> пока не найден.\n"
            "Проверьте сумму и комментарий к переводу или напишите в помощь."
            % texts.e(updated["code"]),
            keyboards.payment_kb(order_id, updated["payment_mode"]),
        )
        return updated

    async def submit_udid(self, order_id: int, raw: str, actor_id: int) -> dict:
        order = await self._load(order_id)
        if order["user_id"] != actor_id:
            raise ServiceError("Это не ваш заказ.")
        if order["status"] == const.NEW or order["status"] == const.PAYMENT_CHECK:
            raise ServiceError("Сначала дождитесь подтверждения оплаты.")
        if order["status"] not in (const.PAID, const.UDID):
            raise ServiceError("UDID по этому заказу уже принят в работу.")

        value, error = udid_mod.validate(raw)
        if error or not value:
            raise ServiceError(error or "Некорректный UDID.")

        updated = await db.set_udid(order_id, value, "user:%d" % actor_id)
        assert updated
        await self.push_order_to_sellers(updated, "📱 <b>Получен UDID</b>")
        return updated

    async def mark_installed(self, order_id: int, actor: str) -> dict:
        order = await self._load(order_id)
        if order["status"] != const.UDID:
            raise ServiceError("Ожидался статус «Ставим сертификат».")
        updated = await db.set_status(order_id, const.INSTALLED, actor, "сертификат установлен")
        assert updated
        await self.notify_buyer(
            updated,
            "📲 <b>Сертификат установлен</b>\n\n"
            "Осталось получить инструкцию — пришлём её сюда.",
        )
        return updated

    async def send_instruction(self, order_id: int, text: str, actor: str) -> dict:
        order = await self._load(order_id)
        if order["status"] not in (const.UDID, const.INSTALLED):
            raise ServiceError("Инструкция отправляется после установки сертификата.")
        body = (text or "").strip()
        if len(body) < 5:
            raise ServiceError("Текст инструкции слишком короткий.")

        updated = await db.set_instruction(order_id, body, actor)
        assert updated
        await self.notify_buyer(
            updated,
            "📄 <b>Инструкция по заказу %s</b>\n\n%s" % (texts.e(updated["code"]), texts.e(body)),
        )
        return updated

    async def cancel(self, order_id: int, actor: str, by_seller: bool, actor_id: int | None = None) -> dict:
        order = await self._load(order_id)
        if not by_seller and actor_id is not None and order["user_id"] != actor_id:
            raise ServiceError("Это не ваш заказ.")
        if order["status"] not in const.OPEN_STATUSES:
            raise ServiceError("Заказ уже закрыт.")
        if not by_seller and order["status"] not in (const.NEW, const.PAYMENT_CHECK):
            raise ServiceError("Оплаченный заказ отменяет продавец — напишите в помощь.")

        updated = await db.set_status(order_id, const.CANCELLED, actor, "отменён")
        assert updated
        if by_seller:
            await self.send(
                updated["user_id"],
                "Заказ <b>%s</b> отменён продавцом. Новый можно оформить командой /start."
                % texts.e(updated["code"]),
            )
        else:
            await self.push_order_to_sellers(updated, "🚫 <b>Покупатель отменил заказ</b>")
        return updated

    # ------------------------------------------------------------ переписка

    MESSAGE_LIMIT = 2000

    async def post_message(
        self, order_id: int, text: str, author_id: int, from_seller: bool
    ) -> dict:
        """Сообщение по заказу: сохраняем и дублируем второй стороне в чат бота."""
        order = await self._load(order_id)
        if not from_seller and order["user_id"] != author_id:
            raise ServiceError("Это не ваш заказ.")

        body = (text or "").strip()
        if not body:
            raise ServiceError("Сообщение пустое.")
        if len(body) > self.MESSAGE_LIMIT:
            raise ServiceError("Слишком длинное сообщение — не больше %d символов." % self.MESSAGE_LIMIT)

        author = db.SELLER if from_seller else db.BUYER
        message = await db.add_message(order_id, author, author_id, body)
        await db.add_event(order_id, "%s:%d" % (author, author_id), "message", None)

        if from_seller:
            await self.send(
                order["user_id"],
                texts.chat_from_seller(order, body),
                keyboards.buyer_order_kb(self.cfg, order),
            )
        else:
            full = await self._with_user(order)
            await self.notify_sellers(
                texts.chat_from_buyer(full, body), keyboards.seller_chat_kb(order["id"])
            )
        return message

    async def read_chat(self, order_id: int, viewer_id: int, as_seller: bool) -> list[dict]:
        order = await self._load(order_id)
        if not as_seller and order["user_id"] != viewer_id:
            raise ServiceError("Это не ваш заказ.")
        messages = await db.list_messages(order_id)
        await db.mark_seen(order_id, db.SELLER if as_seller else db.BUYER)
        return messages

    # ------------------------------------------------------------- прочее

    async def mark_paid_external(self, order_id: int, charge_id: str) -> dict:
        """Подтверждение из платёжной системы (Stars) — без участия продавца."""
        updated = await self.confirm_payment(order_id, "payment", ref=charge_id)
        await self.push_order_to_sellers(updated, "💰 <b>Оплата прошла</b>")
        return updated
