from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup

from . import catalog, const, db, keyboards, platega as platega_mod, promocodes, texts, udid as udid_mod
from .config import Config

log = logging.getLogger(__name__)

# Подбор промокодов: столько неудачных попыток за окно — дальше ждать.
PROMO_TRIES = 10
PROMO_WINDOW = 15 * 60


class ServiceError(Exception):
    """Ошибка бизнес-правила: текст можно показывать пользователю."""


# Ссылка на чек: банки отдают чек страницей, покупатели присылают её в чат.
LINK_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
LINK_LIMIT = 2000


def find_link(text: str | None) -> str:
    """Первая ссылка в сообщении без хвостовой пунктуации; пусто — ссылки нет."""
    match = LINK_RE.search(text or "")
    return match.group(0).rstrip(".,;:!?»)") if match else ""


@dataclass
class Receipt:
    """Подтверждение оплаты: фото, файл или ссылка на чек."""

    title: str               # имя файла или сама ссылка — для продавца и журнала
    file: object = None      # file_id из бота или BufferedInputFile из витрины
    kind: str = "document"   # photo, video или document — как переслать продавцу
    url: str = ""


class OrderService:
    """Один слой правил для чата и Mini App, чтобы они не разъезжались."""

    def __init__(self, bot: Bot, cfg: Config) -> None:
        self.bot = bot
        self.cfg = cfg
        # Каталог собирается из сборок в папке — сервису он нужен для заказов и текстов.
        catalog.use_folder(cfg.ipa_dir)
        # Применение и возврат промокода — по одному за раз: бот и витрина работают
        # в одном процессе, и двойное нажатие не должно занять два применения.
        self._promo_lock = asyncio.Lock()
        self._promo_fails: dict[int, deque] = defaultdict(deque)
        # Platega есть, когда в .env её ключи; режим оплаты при этом может быть и «перевод».
        self.platega = (
            platega_mod.Platega(cfg.platega_merchant_id, cfg.platega_secret, cfg.platega_url)
            if cfg.platega_enabled else None
        )
        # Сюда Platega возвращает покупателя после оплаты; main уточняет его после get_me.
        self.bot_username = cfg.bot_username
        # Ссылка и сверка — по одной на заказ и на транзакцию: двойное нажатие
        # не создаст две транзакции, уведомление и опрос не подтвердят заказ дважды.
        self._pay_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ------------------------------------------------------------ отправка

    async def allowed(self, user_id: int, kind: str | None, order_id: int | None) -> bool:
        """Настройка по конкретному заказу сильнее общей, по умолчанию всё включено."""
        if kind is None:
            return True
        if order_id is not None:
            override = await db.get_order_notify(user_id, order_id)
            if override is not None:
                return override
        prefs = await db.get_notify_prefs(user_id)
        return prefs.get(kind, True)

    async def send(
        self,
        chat_id: int,
        text: str,
        kb: InlineKeyboardMarkup | None = None,
        kind: str | None = None,
        order_id: int | None = None,
        effect: str | None = None,
    ) -> bool:
        if not await self.allowed(chat_id, kind, order_id):
            log.info("Уведомление %s для %s отключено настройками", kind, chat_id)
            return False
        extra = {"message_effect_id": effect} if effect else {}
        try:
            await self.bot.send_message(chat_id, text, reply_markup=kb, **extra)
            return True
        except TelegramForbiddenError:
            log.info("Пользователь %s заблокировал бота", chat_id)
            await db.set_blocked(chat_id, True)
        except TelegramBadRequest as exc:
            if effect:
                # Эффект — украшение: если Telegram его не принял, шлём без него.
                log.info("Эффект %s не принят (%s), отправляем без него", effect, exc)
                return await self.send(chat_id, text, kb, order_id=order_id)
            log.warning("Не отправили сообщение %s: %s", chat_id, exc)
        return False

    async def notify_sellers(
        self,
        text: str,
        kb: InlineKeyboardMarkup | None = None,
        kind: str | None = None,
        order_id: int | None = None,
    ) -> None:
        recipients, mark = await self._staff(self.cfg.seller_ids, order_id)
        for seller_id in recipients:
            await self.send(seller_id, mark + text, kb, kind=kind, order_id=order_id)

    async def _staff(self, ids: tuple[int, ...], order_id: int | None) -> tuple[list[int], str]:
        """Кому из продавцов или поставщиков писать о заказе и с какой пометкой.

        Тестовый заказ (его создал кто-то из TEST_IDS) не должен топить настоящие
        уведомления: о нём пишем только самому тестировщику — если он продавец
        или поставщик, он проверит и эту сторону. Остальные видят такой заказ
        в списках с пометкой 🧪.
        """
        order = await db.get_order(order_id) if order_id is not None else None
        if not order or not order.get("is_test"):
            return list(ids), ""
        return [i for i in ids if i == order["user_id"]], texts.TEST_MARK + "\n"

    async def notify_suppliers(
        self,
        text: str,
        kb: InlineKeyboardMarkup | None = None,
        kind: str | None = None,
        order_id: int | None = None,
    ) -> None:
        """Пишем каждому из SUPPLIER_IDS.

        Продавец, записанный и в поставщики, получает оба сообщения: свою
        карточку и строку поставщика. Иначе владелец, у которого один аккаунт
        на обе роли, не видел бы уведомлений поставщика вообще.
        """
        recipients, mark = await self._staff(self.cfg.supplier_ids, order_id)
        for supplier_id in recipients:
            await self.send(supplier_id, mark + text, kb, kind=kind, order_id=order_id)

    async def push_order_to_suppliers(self, order: dict, kind: str) -> None:
        full = await self._with_user(order)
        await self.notify_suppliers(
            texts.supplier_new_order(full),
            keyboards.supplier_order_kb(order),
            kind=kind,
            order_id=order["id"],
        )

    async def push_order_to_sellers(self, order: dict, header: str, kind: str) -> None:
        full = await self._with_user(order)
        text = header + "\n\n" + texts.seller_order_card(full)
        await self.notify_sellers(
            text, keyboards.seller_order_kb(order), kind=kind, order_id=order["id"]
        )

    async def _with_user(self, order: dict) -> dict:
        if "username" in order:
            return order
        user = await db.get_user(order["user_id"]) or {}
        merged = dict(order)
        merged["username"] = user.get("username")
        merged["first_name"] = user.get("first_name")
        return merged

    async def notify_buyer(self, order: dict, text: str, kind: str, effect: str | None = None) -> None:
        await self.send(
            order["user_id"],
            text,
            keyboards.buyer_order_kb(self.cfg, order),
            kind=kind,
            order_id=order["id"],
            effect=effect,
        )

    # ------------------------------------------------------------- заказы

    async def get_or_create_order(
        self, user_id: int, app_slug: str | None = None
    ) -> tuple[dict, bool]:
        """Возвращает (заказ, создан_ли). Один открытый заказ на пользователя.

        app_slug — конкретное приложение из каталога, None — весь каталог.
        Неоплаченный заказ перенастраивается на новый выбор; заказ, по которому
        уже заявлена оплата, не трогаем — деньги могли уйти за другой товар.
        """
        if app_slug is not None and catalog.get_app(app_slug) is None:
            raise ServiceError("Такого приложения нет в каталоге.")
        price = catalog.app_price(catalog.get_app(app_slug), self.cfg.current_price)

        existing = await db.get_active_order(user_id)
        if existing and existing["status"] in const.OPEN_STATUSES:
            if existing["status"] == const.NEW and existing.get("app_slug") != app_slug:
                existing = await db.set_order_product(existing["id"], app_slug, price)
            # Цена неоплаченного заказа — текущая, вместе с его промокодом.
            return await self.fresh_price(existing), False

        order = await db.create_order(
            user_id=user_id,
            price_rub=price,
            payment_mode=self.cfg.payment_mode,
            prefix=self.cfg.order_prefix,
            app_slug=app_slug,
            is_test=self.cfg.is_tester(user_id),
        )
        # Согласие с офертой и обработкой данных фиксируем вместе с заказом:
        # так обещано в самих документах, и это пригодится при споре.
        await db.add_event(
            order["id"],
            "user:%d" % user_id,
            "consent",
            "оферта, политика и согласие на обработку данных",
        )
        await self.push_order_to_sellers(order, "🆕 <b>Новый заказ</b>", const.NOTIFY_NEW_ORDER)
        await self.push_order_to_suppliers(order, const.NOTIFY_NEW_ORDER)
        return order, True

    async def fresh_price(self, order: dict | None) -> dict | None:
        """Неоплаченный заказ без чека всегда стоит по текущей цене.

        Цена меняется сменой PRICE_RUB или началом и концом акции, а заказ
        хранит цену на момент оформления. Пока по заказу не заявлена оплата,
        покупатель платит столько, сколько стоит сейчас; заказ с чеком не трогаем —
        деньги уже могли уйти.
        """
        if not order or order["status"] != const.NEW:
            return order
        if order["payment_mode"] != self.cfg.payment_mode:
            # PAYMENT_MODE переключили (Platega ↔ перевод с чеком) — неоплаченный
            # заказ платится уже новым способом. Выданную ссылку Platega всё равно
            # можно оплатить: уведомление о ней примем.
            order = await db.set_payment_mode(order["id"], self.cfg.payment_mode) or order
        base = self._base_price(order)
        if base is None:
            return order  # сборку убрали — цену не угадываем
        price, discount = base, 0
        if order.get("promo_id"):
            # Применённый промокод остаётся за заказом, даже если его выключили
            # или срок вышел; пересчитывается только цена, от которой он считается.
            promo = await db.get_promocode(order["promo_id"])
            final = promocodes.final_price(base, promo) if promo else None
            if final:
                price, discount = final, base - final
            else:
                async with self._promo_lock:
                    released = await db.release_promocode(order, "system", "скидка не меньше новой цены", base)
                log.info("Заказ %s: промокод %s снят — скидка не меньше цены", order["code"], order["promo_code"])
                return released or order
        if price == order["price_rub"] and discount == order.get("promo_discount", 0):
            return order
        updated = await db.set_order_product(order["id"], order.get("app_slug"), price, discount)
        log.info("Заказ %s: цена %s → %s", order["code"], order["price_rub"], price)
        return updated or order

    def _base_price(self, order: dict) -> int | None:
        """Цена товара заказа сегодня — без промокода, но с акцией. None — товара больше нет."""
        slug = order.get("app_slug")
        app = catalog.get_app(slug) if slug else None
        if slug and app is None:
            return None
        return catalog.app_price(app, self.cfg.current_price)

    # ------------------------------------------------------------ промокод

    def _promo_wait(self, user_id: int) -> None:
        """Подбор кодов перебором: после PROMO_TRIES неудач — пауза."""
        fails = self._promo_fails[user_id]
        cutoff = time.monotonic() - PROMO_WINDOW
        while fails and fails[0] < cutoff:
            fails.popleft()
        if len(fails) >= PROMO_TRIES:
            minutes = max(1, int((fails[0] - cutoff) // 60) + 1)
            raise ServiceError("Слишком много попыток. Попробуйте ещё раз через %s." % texts.plural(
                minutes, "минуту", "минуты", "минут"))

    def _promo_refuse(self, user_id: int, reason: str) -> ServiceError:
        self._promo_fails[user_id].append(time.monotonic())
        return ServiceError(reason)

    async def _usable_promo(self, user_id: int, raw: str, base: int, order_id: int | None = None) -> dict:
        """Промокод, который покупатель может применить к цене base, — или понятный отказ."""
        if self.cfg.payment_mode == "stars":
            raise ServiceError("Промокоды действуют при оплате переводом.")
        self._promo_wait(user_id)
        if not (raw or "").strip():
            raise ServiceError("Введите промокод.")
        promo = await promocodes.find(raw)
        if not promo:
            raise self._promo_refuse(user_id, promocodes.NOT_FOUND)
        reason = promocodes.refusal(promo)
        if reason:
            raise self._promo_refuse(user_id, reason)
        if promo["per_user"] is not None and await db.promo_uses_by_user(promo["id"], user_id, order_id) >= promo["per_user"]:
            raise self._promo_refuse(user_id, promocodes.PER_USER)
        if not promocodes.final_price(base, promo):
            raise self._promo_refuse(user_id, promocodes.TOO_BIG)
        return promo

    async def check_promo(self, user_id: int, raw: str, app_slug: str | None = None) -> dict:
        """Проверка на экране оформления, пока заказа ещё нет: что станет с ценой.

        Применение не занимается — его займёт оформление заказа с этим кодом.
        """
        if app_slug is not None and catalog.get_app(app_slug) is None:
            raise ServiceError("Такого приложения нет в каталоге.")
        base = catalog.app_price(catalog.get_app(app_slug), self.cfg.current_price)
        current = await db.get_active_order(user_id)
        own = current["id"] if current and current["status"] in const.OPEN_STATUSES else None
        promo = await self._usable_promo(user_id, raw, base, own)
        final = promocodes.final_price(base, promo)
        assert final
        return {"promo": promo, "base": base, "price": final, "discount": base - final}

    async def apply_promo(self, order_id: int, raw: str, user_id: int) -> tuple[dict, str]:
        """Покупатель применяет промокод к неоплаченному заказу. Возвращает заказ и ответ ему."""
        async with self._promo_lock:
            order = await self._load(order_id)
            if order["user_id"] != user_id:
                raise ServiceError("Это не ваш заказ.")
            if order["status"] == const.PAYMENT_CHECK:
                raise ServiceError("Чек уже у продавца — промокод к этому заказу не применить.")
            if order["status"] != const.NEW:
                raise ServiceError("Промокод применяется до оплаты заказа.")
            base = self._base_price(order)
            if base is None:
                raise ServiceError("Товара из заказа больше нет — оформите заказ заново.")
            same = await promocodes.find(raw)
            if same and order.get("promo_id") == same["id"]:
                # Уже за заказом — даже если с тех пор промокод выключили или срок вышел.
                return order, "Промокод %s уже применён." % same["code"]
            promo = await self._usable_promo(user_id, raw, base, order_id)
            final = promocodes.final_price(base, promo)
            assert final
            if not await db.hold_promocode(order, promo, final, base - final, "user:%d" % user_id):
                # Последнее применение успели занять, пока покупатель вводил код.
                fresh = await db.get_promocode(promo["id"])
                if fresh and fresh["per_user"] is not None and await db.promo_uses_by_user(
                        promo["id"], user_id, order_id) >= fresh["per_user"]:
                    raise self._promo_refuse(user_id, promocodes.PER_USER)
                raise self._promo_refuse(user_id, promocodes.REFUSALS[promocodes.EXHAUSTED])
            updated = await self._load(order_id)
        log.info("Заказ %s: применён промокод %s, %s → %s", order["code"], promo["code"], base, final)
        return updated, "Промокод %s применён: скидка %s учтена. К оплате — %s." % (
            promo["code"], texts.money(base - final), texts.money(final))

    async def remove_promo(self, order_id: int, user_id: int) -> dict:
        """Покупатель убрал промокод до оплаты: применение возвращается, цена — без него."""
        async with self._promo_lock:
            order = await self._load(order_id)
            if order["user_id"] != user_id:
                raise ServiceError("Это не ваш заказ.")
            if order["status"] != const.NEW:
                raise ServiceError("Чек уже у продавца — промокод не убрать.")
            if not order.get("promo_id"):
                return order
            base = self._base_price(order)
            released = await db.release_promocode(
                order, "user:%d" % user_id, "покупатель убрал промокод",
                base if base is not None else order["price_rub"] + order["promo_discount"],
            )
        assert released
        return released

    async def _load(self, order_id: int) -> dict:
        order = await db.get_order(order_id)
        if not order:
            raise ServiceError("Заказ не найден.")
        return order

    async def submit_receipt(self, order_id: int, actor_id: int, *receipts: Receipt) -> tuple[dict, bool]:
        """Чек об оплате от покупателя: фото, файл, несколько файлов или ссылка.

        Для неоплаченного заказа в режиме manual чек и есть заявка об оплате:
        без чека заказ на проверку не уходит. Продавцу приходит одно сообщение —
        чек, карточка заказа и кнопки «Оплата получена» / «Отклонить». Ещё один
        чек к заказу, который уже на проверке, просто пересылается продавцу.

        Файл у себя не храним: из бота пересылаем по file_id, из витрины —
        отдаём Telegram байтами. Хранилище чеков — переписка продавца.

        Возвращает заказ и признак, что оплата заявлена именно этим чеком.
        """
        order = await self._load(order_id)
        if order["user_id"] != actor_id:
            raise ServiceError("Это не ваш заказ.")
        if order["status"] not in const.OPEN_STATUSES:
            raise ServiceError("Заказ закрыт — чек уже не нужен.")
        if not receipts:
            raise ServiceError(texts.RECEIPT_REQUIRED)
        for receipt in receipts:
            if receipt.url:
                if len(receipt.url) > LINK_LIMIT or not LINK_RE.fullmatch(receipt.url):
                    raise ServiceError("Ссылка на чек должна начинаться с https:// или http://.")
            elif receipt.file is None:
                raise ServiceError(texts.RECEIPT_REQUIRED)

        actor = "user:%d" % actor_id
        claimed = order["status"] == const.NEW and order["payment_mode"] == "manual"
        if claimed:
            # Цена фиксируется в момент, когда заявлена оплата: продавец сверяет именно её.
            await self.fresh_price(order)
            updated = await db.set_status(order_id, const.PAYMENT_CHECK, actor, "покупатель приложил чек")
            assert updated
            order = updated

        full = await self._with_user(order)
        if claimed:
            header = "💳 <b>Покупатель оплатил и приложил чек</b>"
            kind = const.NOTIFY_PAYMENT
        else:
            header = "🧾 <b>Покупатель прислал ещё один чек</b>"
            kind = const.NOTIFY_CHAT
        kb = keyboards.seller_order_kb(order)
        recipients, mark = await self._staff(self.cfg.seller_ids, order_id)
        header = mark + header
        sent = 0
        for seller_id in recipients:
            if not await self.allowed(seller_id, kind, order_id):
                continue
            if len(receipts) == 1:
                delivered = await self._send_receipt(seller_id, full, header, receipts[0], kb)
            else:
                delivered = await self._send_receipts(seller_id, full, header, receipts, kb)
            if delivered:
                sent += 1

        for receipt in receipts:
            note = "🔗 Ссылка на чек: %s" % receipt.url if receipt.url else "🧾 Чек об оплате: %s" % receipt.title
            await db.add_message(order_id, db.BUYER, actor_id, note)
            await db.add_event(order_id, actor, "receipt", receipt.url or receipt.title)
        if not sent:
            log.warning("Чек по заказу %s никому не доставлен", order["code"])
        return order, claimed

    async def _send_receipts(
        self, seller_id: int, order: dict, header: str, receipts: tuple[Receipt, ...], kb: InlineKeyboardMarkup
    ) -> bool:
        """Несколько файлов чека: сначала файлы, следом карточка заказа с кнопками —
        продавец решает, когда всё уже перед глазами."""
        delivered = 0
        for index, receipt in enumerate(receipts, start=1):
            caption = texts.receipt_part_caption(order, index, len(receipts), receipt.title)
            if await self._send_receipt(seller_id, order, header, receipt, None, caption=caption):
                delivered += 1
        summary = texts.receipt_summary(order, header, len(receipts))
        return await self._deliver(seller_id, self.bot.send_message, seller_id, summary, reply_markup=kb) and delivered > 0

    async def _deliver(self, seller_id: int, method, *args, **kwargs) -> bool:
        """Отправка продавцу с одним повтором после «слишком часто» от Telegram.

        Пачка файлов — это десяток сообщений подряд, и Telegram может попросить
        подождать. Ошибка отправки не должна обрывать запрос: заказ уже на проверке.
        """
        for attempt in (1, 2):
            try:
                await method(*args, **kwargs)
                return True
            except TelegramRetryAfter as exc:
                if attempt == 2:
                    log.warning("Telegram просит подождать с отправкой продавцу %s: %s", seller_id, exc)
                    return False
                await asyncio.sleep(exc.retry_after)
            except TelegramForbiddenError:
                await db.set_blocked(seller_id, True)
                return False
            except (TelegramBadRequest, TelegramNetworkError, TelegramAPIError) as exc:
                log.warning("Не отправили чек продавцу %s: %s", seller_id, exc)
                return False
        return False

    async def _send_receipt(
        self,
        seller_id: int,
        order: dict,
        header: str,
        receipt: Receipt,
        kb: InlineKeyboardMarkup | None,
        caption: str | None = None,
    ) -> bool:
        """Файл уходит с короткой подписью — у Telegram на неё лимит 1024 знака;
        ссылка — обычным сообщением с полной карточкой заказа."""
        if receipt.url:
            text = texts.receipt_link_message(order, header, receipt.url)
            return await self._deliver(seller_id, self.bot.send_message, seller_id, text, reply_markup=kb)
        if caption is None:
            caption = texts.receipt_caption(order, header, receipt.title)
        try:
            if receipt.kind == "photo":
                await self.bot.send_photo(seller_id, receipt.file, caption=caption, reply_markup=kb)
                return True
        except TelegramBadRequest:
            pass  # картинку не взяли как фото (размер, пропорции) — отправим файлом
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
        except TelegramForbiddenError:
            await db.set_blocked(seller_id, True)
            return False
        except (TelegramNetworkError, TelegramAPIError) as exc:
            log.warning("Не отправили чек продавцу %s: %s", seller_id, exc)
            return False
        send = self.bot.send_video if receipt.kind == "video" else self.bot.send_document
        return await self._deliver(seller_id, send, seller_id, receipt.file, caption=caption, reply_markup=kb)

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
            "✅ <b>Оплата получена</b>\n\n" + texts.udid_request(self.cfg.udid_guide_url),
            keyboards.buyer_order_kb(self.cfg, updated),
            kind=const.NOTIFY_STATUS,
            order_id=order_id,
            effect=const.EFFECT_CONFETTI,
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
            keyboards.payment_kb(order_id, updated["payment_mode"], keyboards.held_promo(updated)),
            kind=const.NOTIFY_STATUS,
            order_id=order_id,
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
        await self.push_order_to_sellers(updated, "📱 <b>Получен UDID</b>", const.NOTIFY_UDID)
        return updated

    async def send_instruction(self, order_id: int, actor: str) -> dict:
        """Кнопка «Сертификат установлен»: заказ закрывается и уходит инструкция.

        Инструкция одна на всех — шаги лежат в `app/instruction.py`, поэтому
        писать продавцу нечего и промежуточный статус заказу не нужен.
        Статус INSTALLED встречается только у заказов, заведённых до этого.
        """
        order = await self._load(order_id)
        if order["status"] not in (const.UDID, const.INSTALLED):
            raise ServiceError("Инструкция отправляется после получения UDID.")

        updated = await db.set_status(
            order_id, const.DONE, actor, "сертификат установлен, инструкция отправлена"
        )
        assert updated
        await self.notify_buyer(
            updated,
            texts.instruction_message(updated, self.cfg.webapp_enabled),
            const.NOTIFY_STATUS,
            effect=const.EFFECT_CONFETTI,
        )
        return updated

    async def cancel(self, order_id: int, actor: str, by_seller: bool, actor_id: int | None = None) -> dict:
        order = await self._load(order_id)
        if not by_seller and actor_id is not None and order["user_id"] != actor_id:
            raise ServiceError("Это не ваш заказ.")
        if order["status"] == const.CANCELLED:
            raise ServiceError("Заказ уже отменён.")
        # Выполненный заказ покупатель вправе закрыть сам — это освобождает место
        # под новый. Заказ в работе останавливает только продавец.
        if not by_seller and order["status"] not in (const.NEW, const.PAYMENT_CHECK, const.DONE):
            raise ServiceError("Заказ в работе отменяет продавец — напишите ему в переписке.")

        if not by_seller and order["status"] == const.DONE:
            # Услуга оказана и оплачена: для продавца заказ остаётся выполненным,
            # покупатель лишь убирает его со своего экрана.
            if order.get("closed_at"):
                raise ServiceError("Заказ уже закрыт.")
            closed = await db.close_order(order_id, actor)
            assert closed
            await self.push_order_to_sellers(closed, "🗂 <b>Покупатель закрыл заказ</b>", const.NOTIFY_STATUS)
            return closed

        was_done = order["status"] == const.DONE
        updated = await db.set_status(
            order_id, const.CANCELLED, actor, "закрыт" if was_done else "отменён"
        )
        assert updated
        if updated.get("promo_id") and not updated.get("paid_at"):
            # Отменён до оплаты — применение промокода возвращается в лимит.
            async with self._promo_lock:
                updated = await db.release_promocode(updated, actor, "заказ отменён до оплаты") or updated
        if by_seller:
            await self.send(
                updated["user_id"],
                "Заказ <b>%s</b> %s. Новый можно оформить командой /start."
                % (texts.e(updated["code"]), "закрыт продавцом" if was_done else "отменён продавцом"),
                kind=const.NOTIFY_STATUS,
                order_id=order_id,
            )
        else:
            await self.push_order_to_sellers(updated, "🚫 <b>Покупатель отменил заказ</b>", const.NOTIFY_STATUS)
        return updated

    # ------------------------------------------------------------- Platega

    def _return_url(self) -> str:
        """Куда Platega возвращает покупателя: в чат бота, там придёт подтверждение."""
        if self.bot_username:
            return "https://t.me/%s" % self.bot_username
        return self.cfg.webapp_url or "https://t.me"

    async def payment_link(self, order_id: int, user_id: int) -> dict:
        """Ссылка на оплату в Platega. Живая ссылка на ту же сумму выдаётся снова;
        истекла или цена с тех пор изменилась (промокод, акция) — новая транзакция."""
        if self.platega is None:
            raise ServiceError("Онлайн-оплата сейчас выключена — напишите продавцу.")
        async with self._pay_locks["order:%d" % order_id]:
            order = await self._load(order_id)
            if order["user_id"] != user_id:
                raise ServiceError("Это не ваш заказ.")
            order = await self.fresh_price(order) or order
            if order["status"] != const.NEW:
                raise ServiceError("Заказ уже оплачен." if order.get("paid_at") else "Заказ закрыт — оформите новый.")
            if order["payment_mode"] != platega_mod.PROVIDER:
                raise ServiceError("Этот заказ оплачивается переводом — реквизиты на экране оплаты.")

            last = await db.last_payment(order_id)
            # Минута в запасе: покупатель не должен попасть на страницу, которая
            # закроется, пока он открывает банк.
            fresh_until = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(timespec="seconds")
            if (last and last["status"] == platega_mod.PENDING and last["amount"] == order["price_rub"]
                    and (last["expires_at"] or "") > fresh_until):
                return {"url": last["url"], "amount": last["amount"], "expiresAt": last["expires_at"]}

            user = await db.get_user(user_id) or {}
            name = "@" + user["username"] if user.get("username") else (user.get("first_name") or "")
            try:
                link = await self.platega.create(
                    amount=order["price_rub"],
                    description="Заказ %s · %s" % (order["code"], catalog.product_name(order.get("app_slug"))),
                    order_id=order["code"],
                    payload=str(order["id"]),
                    user_id=user_id,
                    user_name=name,
                    return_url=self._return_url(),
                    failed_url=self._return_url(),
                )
            except platega_mod.PlategaError as exc:
                log.warning("Platega: ссылка для заказа %s не создана: %s", order["code"], exc)
                raise ServiceError("Платёжный сервис не ответил — попробуйте через минуту.")
            payment = await db.create_payment(
                order_id, platega_mod.PROVIDER, link.transaction_id, order["price_rub"], link.status,
                link.url, link.expires_at.isoformat(timespec="seconds"),
            )
        log.info("Заказ %s: ссылка Platega %s на %s ₽", order["code"], link.transaction_id, order["price_rub"])
        return {"url": payment["url"], "amount": payment["amount"], "expiresAt": payment["expires_at"]}

    async def check_payment(self, order_id: int, user_id: int | None = None) -> dict:
        """«Я оплатил — проверить»: спрашиваем Platega сами, не дожидаясь уведомления."""
        order = await self._load(order_id)
        if user_id is not None and order["user_id"] != user_id:
            raise ServiceError("Это не ваш заказ.")
        if self.platega is not None:
            for payment in await db.order_payments(order_id, platega_mod.PENDING):
                try:
                    await self.sync_payment(payment)
                except platega_mod.PlategaError as exc:
                    log.warning("Platega: проверка %s не удалась: %s", payment["external_id"], exc)
                    raise ServiceError("Не получилось связаться с платёжным сервисом — попробуйте через минуту.")
        return await self._load(order_id)

    async def sync_payment(self, payment: dict) -> dict | None:
        """Сверяет транзакцию со статусом в Platega и делает выводы для заказа.

        Статус берём только у самой Platega — уведомлению без подписи верим лишь
        как поводу спросить. PlategaError — Platega не ответила, попробуем позже.
        """
        assert self.platega is not None
        async with self._pay_locks["tx:" + payment["external_id"]]:
            current = await db.get_payment(payment["external_id"]) or payment
            found = await self.platega.status(current["external_id"])
            if found.status == current["status"]:
                return None
            await db.set_payment_status(current["id"], found.status)
            order = await db.get_order(current["order_id"]) if current["order_id"] else None
            if order is None:
                log.warning("Platega: транзакция %s (%s) без заказа", current["external_id"], found.status)
                if found.status == platega_mod.CONFIRMED:
                    await self.notify_sellers(
                        "⚠️ <b>Оплата через Platega по удалённому заказу</b>\nТранзакция <code>%s</code> · %s"
                        % (texts.e(current["external_id"]), texts.money(round(found.amount or current["amount"]))),
                        kind=const.NOTIFY_PAYMENT,
                    )
                return None
            if found.status == platega_mod.CONFIRMED:
                return await self._platega_paid(order, current, found)
            if found.status in platega_mod.REFUNDS:
                await self.platega_refund(order, current["external_id"], found.status, None)
            elif found.status == platega_mod.CANCELED:
                await db.add_event(order["id"], "platega", "payment_canceled", current["external_id"])
            return order

    async def _platega_paid(self, order: dict, payment: dict, found: platega_mod.Status) -> dict:
        paid = round(found.amount) if found.amount else payment["amount"]
        await db.add_event(order["id"], "platega", "payment_confirmed", "%s · %d ₽" % (payment["external_id"], paid))
        if order["status"] not in (const.NEW, const.PAYMENT_CHECK):
            if order["status"] == const.CANCELLED and not order.get("paid_at"):
                # Ссылку оплатили уже после отмены заказа — деньги пришли, решает продавец.
                await self.push_order_to_sellers(
                    order,
                    "⚠️ <b>Оплата по отменённому заказу</b> — %s через Platega.\n"
                    "Верните деньги в кабинете Platega или восстановите заказ с покупателем." % texts.money(paid),
                    const.NOTIFY_PAYMENT,
                )
            return order
        if paid < order["price_rub"]:
            # Ссылку выдали до того, как цена выросла (например, убрали промокод).
            await self.push_order_to_sellers(
                order,
                "⚠️ <b>Оплачено меньше цены заказа</b>: %s через Platega вместо %s.\n"
                "Проверьте и подтвердите оплату вручную." % (texts.money(paid), texts.money(order["price_rub"])),
                const.NOTIFY_PAYMENT,
            )
            return order
        updated = await self.confirm_payment(order["id"], "platega", ref=payment["external_id"])
        await self.push_order_to_sellers(
            updated, "💳 <b>Оплата получена через Platega</b> · %s" % texts.money(paid), const.NOTIFY_PAYMENT
        )
        log.info("Заказ %s оплачен через Platega: %s", order["code"], payment["external_id"])
        return updated

    async def platega_refund(self, order: dict, transaction_id: str, status: str, amount: float | None) -> None:
        """Деньги вернули покупателю (из кабинета Platega или банк оспорил платёж).
        Заказ сам не трогаем — продавцу сообщение, решает он."""
        what = "частичный возврат" if status == platega_mod.PARTIALLY_REFUNDED else "возврат"
        sum_text = " · %s" % texts.money(round(amount)) if amount else ""
        await db.add_event(order["id"], "platega", "refund", "%s %s%s" % (transaction_id, what, sum_text))
        await self.push_order_to_sellers(
            order, "💸 <b>Platega: %s по заказу</b>%s" % (what, sum_text), const.NOTIFY_PAYMENT
        )

    async def poll_payments(self, window_minutes: int = 40) -> int:
        """Подстраховка уведомлений: статус свежих неоплаченных ссылок спрашиваем сами."""
        if self.platega is None:
            return 0
        since = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat(timespec="seconds")
        changed = 0
        for payment in await db.pending_payments(since):
            try:
                if await self.sync_payment(payment) is not None:
                    changed += 1
            except platega_mod.PlategaError as exc:
                log.warning("Platega: опрос %s не удался: %s", payment["external_id"], exc)
                break  # Platega недоступна — остальные спросим в следующий раз
            except Exception:
                log.exception("Platega: опрос %s", payment["external_id"])
        return changed

    # ----------------------------------------------------------- автоотмена

    async def expire_unpaid(self, moment: datetime | None = None) -> dict:
        """Неоплаченный заказ без изменений ORDER_TTL_DAYS дней отменяется сам.

        За сутки до отмены покупателю приходит напоминание. Об отмене пишем
        только тем, кому напоминали: заказ, зависший ещё до автоотмены (или пока
        бот не работал), уходит молча — через 20 дней сообщение скорее удивит.
        Применение промокода при отмене возвращается в лимит.
        """
        ttl = self.cfg.order_ttl_days
        done = {"reminded": 0, "cancelled": 0}
        if ttl <= 0:
            return done
        moment = moment or datetime.now(timezone.utc)
        cancel_before = (moment - timedelta(days=ttl)).isoformat(timespec="seconds")
        remind_before = (moment - timedelta(days=ttl - 1)).isoformat(timespec="seconds")
        days = texts.plural(ttl, "день", "дня", "дней")
        for order in await db.stale_unpaid(remind_before):
            try:
                if order["updated_at"] < cancel_before:
                    expired = await db.expire_order(order["id"], cancel_before, "автоотмена: нет оплаты %s" % days)
                    if not expired:
                        continue
                    done["cancelled"] += 1
                    if expired.get("promo_id"):
                        async with self._promo_lock:
                            await db.release_promocode(expired, "system", "заказ не оплачен — автоотмена")
                    if order["reminded"] and expired["user_id"] != db.ANON_USER_ID:
                        await self.send(expired["user_id"], texts.order_expired(expired, ttl),
                                        kind=const.NOTIFY_STATUS, order_id=expired["id"])
                elif not order["reminded"]:
                    await db.add_event(order["id"], "system", "expire_reminder", "напоминание: завтра автоотмена")
                    done["reminded"] += 1
                    if order["user_id"] != db.ANON_USER_ID:
                        await self.send(order["user_id"], texts.order_expire_soon(order),
                                        keyboards.buyer_order_kb(self.cfg, order),
                                        kind=const.NOTIFY_STATUS, order_id=order["id"])
            except Exception:
                # Сбой отправки одному покупателю не должен остановить остальных.
                log.exception("Автоотмена: заказ %s", order["code"])
        if done["cancelled"] or done["reminded"]:
            log.info("Автоотмена: отменено %(cancelled)d, напомнили %(reminded)d", done)
        return done

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
                kind=const.NOTIFY_CHAT,
                order_id=order_id,
            )
        else:
            full = await self._with_user(order)
            await self.notify_sellers(
                texts.chat_from_buyer(full, body),
                keyboards.seller_chat_kb(order["id"]),
                kind=const.NOTIFY_CHAT,
                order_id=order_id,
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
        await self.push_order_to_sellers(updated, "💰 <b>Оплата прошла</b>", const.NOTIFY_PAYMENT)
        return updated
