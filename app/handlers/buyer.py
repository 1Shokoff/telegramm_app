from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message

from .. import catalog, const, db, keyboards, texts, udid as udid_mod
from ..config import ROOT, Config
from ..service import OrderService, ServiceError

log = logging.getLogger(__name__)
router = Router(name="buyer")

WELCOME_PHOTO = ROOT / "app" / "assets" / "welcome.jpg"
# После первой загрузки Telegram отдаёт file_id — дальше шлём по нему, файл не грузим заново.
_photo_ids: dict[str, str] = {}


async def _remember(message: Message) -> None:
    user = message.from_user
    if user:
        await db.upsert_user(user.id, user.username, user.first_name)


async def _show_order(target: Message, cfg: Config, order: dict) -> None:
    await target.answer(texts.buyer_order_card(order), reply_markup=keyboards.buyer_order_kb(cfg, order))


async def _send_welcome(message: Message, text: str, kb: InlineKeyboardMarkup) -> None:
    """Приветствие — картинка с подписью. Нет файла или Telegram её не принял — просто текст."""
    photo = _photo_ids.get("welcome")
    if photo is None and WELCOME_PHOTO.is_file():
        photo = FSInputFile(WELCOME_PHOTO)
    if photo is None:
        await message.answer(text, reply_markup=kb)
        return
    try:
        sent = await message.answer_photo(photo, caption=text, reply_markup=kb)
    except TelegramAPIError as exc:
        log.warning("Приветствие ушло без картинки: %s", exc)
        _photo_ids.pop("welcome", None)
        await message.answer(text, reply_markup=kb)
        return
    if sent.photo:
        _photo_ids["welcome"] = sent.photo[-1].file_id


@router.message(CommandStart())
async def cmd_start(message: Message, cfg: Config) -> None:
    await _remember(message)
    order = await db.get_active_order(message.from_user.id)
    has_open = bool(order and order["status"] in const.OPEN_STATUSES)
    await _send_welcome(
        message,
        texts.welcome(message.from_user.first_name, cfg.price_rub, catalog.count()),
        keyboards.start_kb(cfg, has_open),
    )


@router.message(Command("order"))
async def cmd_order(message: Message, cfg: Config) -> None:
    await _remember(message)
    order = await db.get_active_order(message.from_user.id)
    if not order:
        await message.answer("Заказов пока нет. /start — оформить.")
        return
    await _show_order(message, cfg, order)


@router.message(Command("udid"))
async def cmd_udid(message: Message, cfg: Config) -> None:
    await message.answer(texts.udid_request(cfg.udid_guide_url) + "\n\n" + texts.PRIVACY)


@router.message(Command("help"))
async def cmd_help(message: Message, cfg: Config) -> None:
    await message.answer(
        texts.help_text(cfg.support_username, cfg.webapp_enabled),
        reply_markup=keyboards.help_kb(),
    )


@router.message(Command("about"))
async def cmd_about(message: Message, cfg: Config) -> None:
    await message.answer(texts.about(cfg), disable_web_page_preview=True)


async def _notify_screen(user_id: int, cfg: Config) -> tuple[str, InlineKeyboardMarkup]:
    """Экран настроек: общие переключатели плюс режим для текущего заказа."""
    is_seller = cfg.is_seller(user_id)
    items = const.notifications_for(is_seller)
    prefs = await db.get_notify_prefs(user_id)

    order = await db.get_active_order(user_id)
    mode = const.ORDER_NOTIFY_DEFAULT
    if order:
        override = await db.get_order_notify(user_id, order["id"])
        if override is True:
            mode = const.ORDER_NOTIFY_ON
        elif override is False:
            mode = const.ORDER_NOTIFY_OFF

    return (
        texts.notify_screen(items, prefs, order, mode),
        keyboards.notify_kb(items, prefs, order, mode),
    )


@router.message(Command("notify"))
async def cmd_notify(message: Message, cfg: Config) -> None:
    await _remember(message)
    text, kb = await _notify_screen(message.from_user.id, cfg)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("nt:"))
async def cb_notify_toggle(call: CallbackQuery, cfg: Config) -> None:
    kind = call.data.split(":", 1)[1]
    allowed = {key for key, _t, _h in const.notifications_for(cfg.is_seller(call.from_user.id))}
    if kind not in allowed:
        await call.answer()
        return

    prefs = await db.get_notify_prefs(call.from_user.id)
    enabled = not prefs.get(kind, True)
    await db.set_notify_pref(call.from_user.id, kind, enabled)

    text, kb = await _notify_screen(call.from_user.id, cfg)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:  # текст не изменился — Telegram отвечает ошибкой
        pass
    await call.answer(
        "%s: %s" % (const.notification_title(kind), "включены" if enabled else "выключены")
    )


@router.callback_query(F.data.startswith("nto:"))
async def cb_notify_order(call: CallbackQuery, cfg: Config) -> None:
    try:
        order_id = int(call.data.split(":", 1)[1])
    except ValueError:
        await call.answer()
        return

    order = await db.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if not cfg.is_seller(call.from_user.id) and order["user_id"] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return

    # По кругу: общие настройки → не беспокоить → всегда уведомлять.
    current = await db.get_order_notify(call.from_user.id, order_id)
    nxt = {None: False, False: True, True: None}[current]
    await db.set_order_notify(call.from_user.id, order_id, nxt)

    mode = const.ORDER_NOTIFY_DEFAULT
    if nxt is True:
        mode = const.ORDER_NOTIFY_ON
    elif nxt is False:
        mode = const.ORDER_NOTIFY_OFF

    text, kb = await _notify_screen(call.from_user.id, cfg)
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await call.answer("Заказ %s: %s" % (order["code"], const.ORDER_NOTIFY_TITLES[mode]))


@router.message(Command("forget"))
async def cmd_forget(message: Message) -> None:
    removed = await db.purge_user(message.from_user.id)
    if removed:
        await message.answer("Ваши заказы и UDID удалены из базы бота.")
    else:
        await message.answer("В базе нет ваших данных.")


# ------------------------------------------------------------------ навигация


@router.callback_query(F.data == "nav:catalog")
async def nav_catalog(call: CallbackQuery) -> None:
    await call.message.answer(
        "<b>Каталог · %d приложений</b>\n\n%s" % (catalog.count(), catalog.as_text())
    )
    await call.answer()


@router.callback_query(F.data == "nav:help")
async def nav_help(call: CallbackQuery, cfg: Config) -> None:
    await call.message.answer(
        texts.help_text(cfg.support_username, cfg.webapp_enabled),
        reply_markup=keyboards.help_kb(),
    )
    await call.answer()


@router.callback_query(F.data == "nav:about")
async def nav_about(call: CallbackQuery, cfg: Config) -> None:
    await call.message.answer(texts.about(cfg), disable_web_page_preview=True)
    await call.answer()


@router.callback_query(F.data == "nav:udid_help")
async def nav_udid_help(call: CallbackQuery, cfg: Config) -> None:
    await call.message.answer(texts.udid_request(cfg.udid_guide_url))
    await call.answer()


@router.callback_query(F.data == "nav:chat")
async def nav_chat(call: CallbackQuery, cfg: Config) -> None:
    rows = []
    wa = keyboards.webapp_button(cfg, "Открыть переписку")
    if wa:
        rows.append([wa])
    await call.message.answer(
        "<b>Связь с продавцом</b>\n\n" + texts.CHAT_HINT_BUYER,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None,
    )
    await call.answer()


@router.callback_query(F.data == "nav:notify")
async def nav_notify(call: CallbackQuery, cfg: Config) -> None:
    text, kb = await _notify_screen(call.from_user.id, cfg)
    await call.message.answer(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "nav:terms")
async def nav_terms(call: CallbackQuery, cfg: Config) -> None:
    await call.message.answer(
        "<b>Условия заказа</b>\n\n"
        "После оплаты вы указываете UDID устройства. Продавец выполнит установку сертификата "
        "и подготовит инструкцию. В цену входит установка любых приложений каталога "
        "на одно устройство.\n\n"
        + texts.PRIVACY
        + "\n\n"
        + texts.CONSENT_NOTE
        + texts.legal_footer(cfg),
        disable_web_page_preview=True,
    )
    await call.answer()


@router.callback_query(F.data == "nav:buy")
async def nav_buy(call: CallbackQuery, cfg: Config) -> None:
    await call.message.answer(
        "<b>Оформление заказа</b>\n\n"
        "%s\nUDID укажете следующим шагом.\n\n"
        "Цена: <b>%s</b> — установка любых приложений каталога на одно устройство.\n"
        "Оплата: %s\n\n"
        "%s%s"
        % (
            texts.e(cfg.product_title),
            texts.money(cfg.price_rub),
            {"manual": "перевод с подтверждением", "demo": "демо, без списания", "stars": "Telegram Stars"}[
                cfg.payment_mode
            ],
            texts.CONSENT_NOTE,
            texts.legal_footer(cfg),
        ),
        reply_markup=keyboards.confirm_order_kb(),
        disable_web_page_preview=True,
    )
    await call.answer()


@router.callback_query(F.data == "nav:buy_confirm")
async def nav_buy_confirm(call: CallbackQuery, cfg: Config, service: OrderService) -> None:
    await db.upsert_user(call.from_user.id, call.from_user.username, call.from_user.first_name)
    order, created = await service.get_or_create_order(call.from_user.id)

    if not created and order["status"] != const.NEW:
        await call.message.answer(
            "У вас уже есть активный заказ.", reply_markup=keyboards.buyer_order_kb(cfg, order)
        )
        await call.message.answer(texts.buyer_order_card(order))
        await call.answer()
        return

    if cfg.payment_mode == "manual":
        await call.message.answer(
            texts.payment_instructions(order, cfg),
            reply_markup=keyboards.payment_kb(order["id"], cfg.payment_mode),
        )
    elif cfg.payment_mode == "demo":
        await call.message.answer(
            "Заказ <b>%s</b> создан. Режим демонстрации: деньги не списываются."
            % texts.e(order["code"]),
            reply_markup=keyboards.payment_kb(order["id"], cfg.payment_mode),
        )
    else:
        await call.message.answer(
            "Заказ <b>%s</b> создан. Оплата в Telegram Stars." % texts.e(order["code"]),
            reply_markup=keyboards.payment_kb(order["id"], cfg.payment_mode),
        )
    await call.answer()


@router.callback_query(F.data == "nav:order")
async def nav_order(call: CallbackQuery, cfg: Config) -> None:
    order = await db.get_active_order(call.from_user.id)
    if not order:
        await call.answer("Заказов нет", show_alert=True)
        return
    await _show_order(call.message, cfg, order)
    await call.answer()


# -------------------------------------------------------------- действия по заказу


@router.callback_query(F.data.startswith("o:claim:"))
async def cb_claim(call: CallbackQuery, cfg: Config, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    try:
        order = await service.claim_payment(parsed[1], call.from_user.id)
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await call.message.answer(
        "Спасибо! Заказ <b>%s</b> отправлен на проверку оплаты.\n"
        "Как только продавец подтвердит платёж, попросим UDID." % texts.e(order["code"]),
        reply_markup=keyboards.buyer_order_kb(cfg, order),
    )
    await call.answer("Отправлено продавцу")


@router.callback_query(F.data.startswith("o:demopay:"))
async def cb_demopay(call: CallbackQuery, cfg: Config, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed or cfg.payment_mode != "demo":
        await call.answer()
        return
    order = await db.get_order(parsed[1])
    if not order or order["user_id"] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return
    try:
        await service.confirm_payment(parsed[1], "demo", ref="demo")
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await call.answer("Оплата имитирована")


@router.callback_query(F.data.startswith("o:cancel:"))
async def cb_cancel(call: CallbackQuery, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    try:
        order = await service.cancel(
            parsed[1], "user:%d" % call.from_user.id, by_seller=False, actor_id=call.from_user.id
        )
    except ServiceError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await call.message.answer("Заказ <b>%s</b> отменён." % texts.e(order["code"]))
    await call.answer()


@router.callback_query(F.data.startswith("o:refresh:"))
async def cb_refresh(call: CallbackQuery, cfg: Config) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    order = await db.get_order(parsed[1])
    if not order or order["user_id"] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return
    try:
        await call.message.edit_text(
            texts.buyer_order_card(order), reply_markup=keyboards.buyer_order_kb(cfg, order)
        )
    except Exception:  # текст не изменился — Telegram отвечает ошибкой
        pass
    await call.answer("Обновлено")


@router.callback_query(F.data.startswith("o:help:"))
async def cb_help_order(call: CallbackQuery, cfg: Config, service: OrderService) -> None:
    parsed = keyboards.parse_cb(call.data)
    if not parsed:
        return
    order = await db.get_order(parsed[1])
    if not order or order["user_id"] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return
    await service.push_order_to_sellers(
        order, "🆘 <b>Покупатель просит помощь</b>", const.NOTIFY_HELP
    )
    contact = ("Напишите @%s — ответим." % texts.e(cfg.support_username)) if cfg.support_username else (
        "Продавец получил уведомление и ответит здесь."
    )
    await call.message.answer("Запрос по заказу <b>%s</b> отправлен.\n%s" % (texts.e(order["code"]), contact))
    await call.answer()


# ------------------------------------------------------------------ чек об оплате


async def _receipt(message: Message, cfg: Config, service: OrderService, file, is_photo: bool, title: str) -> None:
    await _remember(message)
    order = await db.get_active_order(message.from_user.id)
    if not order or order["status"] not in const.OPEN_STATUSES:
        await message.answer("Открытого заказа нет. Оформите заказ — и присылайте чек сюда.")
        return
    try:
        await service.attach_receipt(
            order["id"], message.from_user.id, file, is_photo=is_photo, title=title
        )
    except ServiceError as exc:
        await message.answer(texts.e(str(exc)))
        return
    await message.answer(
        "Чек по заказу <b>%s</b> отправлен продавцу." % texts.e(order["code"]),
        reply_markup=keyboards.buyer_order_kb(cfg, order),
    )


@router.message(F.photo)
async def receipt_photo(message: Message, cfg: Config, service: OrderService) -> None:
    photo = message.photo[-1]
    await _receipt(
        message, cfg, service, photo.file_id, True, message.caption or "скриншот перевода"
    )


@router.message(F.document)
async def receipt_document(message: Message, cfg: Config, service: OrderService) -> None:
    await _receipt(
        message, cfg, service, message.document.file_id, False,
        message.document.file_name or message.caption or "файл",
    )


# ------------------------------------------------------------------- UDID текстом


@router.message(StateFilter(None), F.text)
async def free_text(message: Message, cfg: Config, service: OrderService) -> None:
    await _remember(message)
    order = await db.get_active_order(message.from_user.id)
    text = message.text or ""

    # На шаге ввода номер отличаем от вопроса продавцу по виду сообщения.
    if order and order["status"] == const.PAID and udid_mod.looks_like_attempt(text):
        try:
            updated = await service.submit_udid(order["id"], text, message.from_user.id)
        except ServiceError as exc:
            await message.answer("%s\n\n%s" % (texts.e(str(exc)), texts.udid_request(cfg.udid_guide_url)))
            return
        await message.answer(
            texts.udid_accepted(updated), reply_markup=keyboards.buyer_order_kb(cfg, updated)
        )
        return

    if order:
        try:
            await service.post_message(order["id"], text, message.from_user.id, from_seller=False)
        except ServiceError as exc:
            await message.answer(texts.e(str(exc)))
            return
        reply = "Отправлено продавцу по заказу <b>%s</b>. Ответ придёт сюда." % texts.e(order["code"])
        if order["status"] == const.PAID:
            reply += "\n\nЕсли это был UDID — проверьте номер: нужно 40 символов."
        await message.answer(reply, reply_markup=keyboards.buyer_order_kb(cfg, order))
        return

    await message.answer(
        "Не понял сообщение. /start — витрина, /help — помощь.",
        reply_markup=keyboards.start_kb(cfg, False),
    )
