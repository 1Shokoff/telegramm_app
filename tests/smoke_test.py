"""Сквозная проверка без Telegram: python -m tests.smoke_test

Прогоняет весь путь заказа (оплата → UDID → сертификат → инструкция)
через сервисный слой и через HTTP-API Mini App с настоящей подписью initData.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import pathlib
import plistlib
import sys
import tempfile
import time
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

TOKEN = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
BUYER = 777
SELLER = 999
SUPPLIER = 888
# Отдельный покупатель для проверок поставщика: у основного своя история заказов.
SUPPLIER_BUYER = 555

os.environ.update(
    {
        "BOT_TOKEN": TOKEN,
        "SELLER_IDS": str(SELLER),
        "SUPPLIER_IDS": str(SUPPLIER),
        "WEBAPP_URL": "https://example.com",
        "PAYMENT_MODE": "manual",
        "PAYMENT_DETAILS": "Переводите только по СБП",
        "PAY_PHONE": "+7 900 000-00-00",
        "PAY_BANK": "Сбербанк",
        "PAY_NAME": "Тестов Т. Т.",
        "PAY_CARD": "2202 2000 0000 0000",
        "PRICE_RUB": "3000",
        "ORDER_PREFIX": "NP",
        "LEGAL_NAME": "Самозанятый Тестов Т. Т.",
        "LEGAL_INN": "123456789012",
        "CONTACT_EMAIL": "help@example.com",
        "OFFER_URL": "https://example.com/offer",
        "DEV_MODE": "0",
        "LOG_LEVEL": "WARNING",
    }
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests import fixtures  # noqa: E402

# Витрина собирается из сборок в папке — тестам нужна своя папка с каталогом.
os.environ["IPA_DIR"] = str(fixtures.catalog_folder())

from aiogram.exceptions import TelegramRetryAfter  # noqa: E402
from aiogram.methods import SendDocument  # noqa: E402
from aiohttp import FormData  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import analytics, auth, bundle, catalog, const, db, ipa as ipa_mod, keyboards, legal, texts, udid as udid_mod  # noqa: E402
from app.api import build_app, order_public  # noqa: E402
from app.config import MSK, load_config, today_msk  # noqa: E402
from app.service import OrderService, Receipt, ServiceError  # noqa: E402

SPACED_UDID = "2b6f0cc904d137be2e17 30235f5664094b831186"
# Новый формат (iPhone XS и новее): 25 символов с дефисом, буквы заглавные.
MODERN_UDID = "00008150-000914312E78C01C"

failures: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    if condition:
        print("  ok   %s" % name)
    else:
        failures.append(name)
        print("  FAIL %s %s" % (name, extra))


class FakeBot:
    """Заглушка вместо aiogram.Bot: складывает сообщения в список."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.files: list[tuple[int, str, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))
        return None

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.files.append((chat_id, "photo", caption or ""))
        return None

    async def send_document(self, chat_id, document, caption=None, reply_markup=None):
        self.files.append((chat_id, "document", caption or ""))
        return None

    def to(self, chat_id: int) -> list[str]:
        return [text for cid, text in self.sent if cid == chat_id]


def make_init_data(user_id: int, name: str = "Тест") -> str:
    payload = {
        "auth_date": str(int(time.time())),
        "query_id": "AAtest",
        "user": json.dumps(
            {"id": user_id, "first_name": name, "username": "u%d" % user_id},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
    check_string = "\n".join("%s=%s" % (k, payload[k]) for k in sorted(payload))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def test_documents(cfg) -> None:
    """Документы правит владелец — проверяем только, что все подстановки известны программе."""
    print("\nДокументы")
    for key, title, _hint in const.DOCUMENTS:
        found = legal.document(key, cfg, "iApkibot")
        check("документ «%s» есть" % title, found is not None)
        if found:
            _title, html = found
            check("в «%s» нет незаполненных {{…}}" % title, "{{" not in html and "}}" not in html)
    _title, html = legal.document("offer", cfg, "iApkibot")
    check("{{bot_link}} — это @ и имя бота", "@iApkibot" in html)


def test_udid() -> None:
    print("\nUDID")
    check("тестовый номер валиден", udid_mod.validate(udid_mod.TEST_UDID)[0] == udid_mod.TEST_UDID)
    check("пробелы вычищаются", udid_mod.validate(SPACED_UDID)[0] == udid_mod.TEST_UDID)
    check("новый формат принимается как есть — с дефисом и регистром",
          udid_mod.validate(MODERN_UDID)[0] == MODERN_UDID)
    check("регистр не меняется",
          udid_mod.validate(udid_mod.TEST_UDID.upper())[0] == udid_mod.TEST_UDID.upper())
    check("длина любая: 39 символов проходят",
          udid_mod.validate(udid_mod.TEST_UDID[:-1])[0] == udid_mod.TEST_UDID[:-1])
    check("короткий номер проходит", udid_mod.validate("00008150")[0] == "00008150")
    check("длиннее 40 — отказ", udid_mod.validate("a" * 41)[0] is None)
    check("чужие символы отклоняются", udid_mod.validate("z" * 40)[0] is None)
    lookalike = MODERN_UDID.replace("E", "\u0415").replace("C", "\u0421")  # кириллические Е и С
    check("кириллические Е и С становятся латинскими", udid_mod.validate(lookalike)[0] == MODERN_UDID)
    lower = udid_mod.TEST_UDID.replace("e", "\u0435").replace("c", "\u0441").replace("b", "\u0432")
    check("строчные а, в, с, е тоже", udid_mod.validate(lower)[0] == udid_mod.TEST_UDID)
    check("кириллица заменяется и в длинном номере",
          udid_mod.validate("\u0410\u0412\u0421\u0415" * 10)[0] == "ABCE" * 10)
    check("номер с кириллицей в чате узнаётся как номер", udid_mod.looks_like_attempt(lookalike))
    check("другие кириллические буквы отклоняются",
          udid_mod.validate(MODERN_UDID.replace("E", "\u0416"))[0] is None)
    for dash, name in (("\u2014", "длинное тире"), ("\u2013", "короткое тире"), ("\u2212", "минус")):
        check("%s → дефис" % name, udid_mod.validate(MODERN_UDID.replace("-", dash))[0] == MODERN_UDID)
    check("невидимые символы из буфера убираются",
          udid_mod.validate("\ufeff" + MODERN_UDID.replace("-", "\u200b-"))[0] == MODERN_UDID)
    check("пустой — отказ", udid_mod.validate("  \n ")[0] is None)
    check("одни дефисы — отказ", udid_mod.validate("---")[0] is None)
    check("новый формат в чате узнаётся как номер", udid_mod.looks_like_attempt(MODERN_UDID))
    check("короткий текст в чате — не номер", not udid_mod.looks_like_attempt("12345"))
    check("маска скрывает номер", udid_mod.mask(udid_mod.TEST_UDID).endswith("1186"))


def test_auth() -> None:
    print("\nПодпись initData")
    user = auth.check_init_data(make_init_data(BUYER), TOKEN, 86400)
    check("валидная подпись принята", user.id == BUYER)

    broken = make_init_data(BUYER)[:-1] + ("0" if make_init_data(BUYER)[-1] != "0" else "1")
    try:
        auth.check_init_data(broken, TOKEN, 86400)
        check("подделка отклонена", False)
    except auth.AuthError:
        check("подделка отклонена", True)

    try:
        auth.check_init_data(make_init_data(BUYER), "999:OTHER", 86400)
        check("чужой токен отклонён", False)
    except auth.AuthError:
        check("чужой токен отклонён", True)


async def test_service(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nПуть заказа через сервис")
    await db.upsert_user(BUYER, "buyer", "Покупатель")

    order, created = await svc.get_or_create_order(BUYER)
    check("заказ создан", created and order["code"] == "NP-0001", order["code"])
    check("продавец уведомлён", any("Новый заказ" in t for t in bot.to(SELLER)))

    again, created2 = await svc.get_or_create_order(BUYER)
    check("второй заказ не плодится", not created2 and again["id"] == order["id"])

    try:
        await svc.submit_udid(order["id"], udid_mod.TEST_UDID, BUYER)
        check("UDID до оплаты не принимается", False)
    except ServiceError:
        check("UDID до оплаты не принимается", True)

    try:
        await svc.submit_receipt(order["id"], BUYER, Receipt(title="без файла"))
        check("без чека оплату не заявить", False)
    except ServiceError:
        check("без чека оплату не заявить", True)
    check("без чека заказ ждёт оплату", (await db.get_order(order["id"]))["status"] == const.NEW)

    bot.sent.clear()
    bot.files.clear()
    order, claimed = await svc.submit_receipt(
        order["id"], BUYER, Receipt(title="перевод.jpg", file="photo-id", kind="photo")
    )
    check("чек заявляет оплату: статус «на проверке»", claimed and order["status"] == const.PAYMENT_CHECK)
    check("продавцу ушёл чек вместе с заявкой",
          any(cid == SELLER and kind == "photo" and "оплатил и приложил чек" in cap
              for cid, kind, cap in bot.files), str(bot.files))

    try:
        await svc.submit_receipt(order["id"], SELLER, Receipt(title="x", url="https://bank.ru/r/1"))
        check("чужой заказ не тронуть", False)
    except ServiceError:
        check("чужой заказ не тронуть", True)

    bot.files.clear()
    order, again = await svc.submit_receipt(order["id"], BUYER, Receipt(title="чек.pdf", file="doc-id"))
    check("ещё один чек к заказу на проверке просто пересылается",
          not again and order["status"] == const.PAYMENT_CHECK
          and any(cid == SELLER and kind == "document" and "ещё один чек" in cap
                  for cid, kind, cap in bot.files))

    order = await svc.reject_payment(order["id"], "seller:%d" % SELLER)
    check("продавец отклонил — заказ снова ждёт чек", order["status"] == const.NEW)
    try:
        await svc.submit_receipt(order["id"], BUYER, Receipt(title="x", url="ftp://bank.ru/r/1"))
        check("ссылка не на сайт отклонена", False)
    except ServiceError:
        check("ссылка не на сайт отклонена", True)
    bot.sent.clear()
    order, claimed = await svc.submit_receipt(
        order["id"], BUYER, Receipt(title="https://bank.ru/r/1?a=1&b=2", url="https://bank.ru/r/1?a=1&b=2")
    )
    check("после отказа нужен новый чек — ссылкой тоже можно",
          claimed and order["status"] == const.PAYMENT_CHECK)
    check("продавцу ушла ссылка на чек, экранированная для Telegram",
          any(cid == SELLER and 'href="https://bank.ru/r/1?a=1&amp;b=2"' in t for cid, t in bot.sent),
          str([t for cid, t in bot.sent if cid == SELLER])[:300])
    events = [e["type"] for e in await db.list_events(order["id"])]
    check("чеки записаны в журнал заказа", events.count("receipt") == 3, str(events))

    order = await svc.confirm_payment(order["id"], "seller:%d" % SELLER)
    check("статус: оплачен", order["status"] == const.PAID)
    check("у покупателя запросили UDID", any("Оплата получена" in t for t in bot.to(BUYER)))

    try:
        await svc.submit_udid(order["id"], "мой номер телефона", BUYER)
        check("UDID с чужими символами отклонён", False)
    except ServiceError:
        check("UDID с чужими символами отклонён", True)

    order = await svc.submit_udid(order["id"], SPACED_UDID, BUYER)
    check("UDID сохранён", order["device_udid"] == udid_mod.TEST_UDID and order["status"] == const.UDID)

    order = await svc.send_instruction(order["id"], "seller:%d" % SELLER)
    check("кнопка продавца сразу закрывает заказ", order["status"] == const.DONE)
    sent = bot.to(BUYER)[-1]
    check("инструкция ушла покупателю", "Инструкция по заказу" in sent)
    check("в инструкции есть шаги шаблона", "Введите свой UDID" in sent, sent[:120])
    check("в инструкции есть боты",
          "@iRegerBot" in sent and "@isignerbot" in sent)
    check("в инструкции сказано, где взять IPA", "Где взять IPA-файлы" in sent)
    check("канал в инструкции — ссылкой", 'href="https://t.me/iAppki">@iAppki</a>' in sent, sent[:200])

    try:
        await svc.send_instruction(order["id"], "seller:%d" % SELLER)
        check("повторная отправка отклонена", False)
    except ServiceError:
        check("повторная отправка отклонена", True)

    events = await db.list_events(order["id"])
    check("история пишется", len(events) >= 5, "событий: %d" % len(events))

    fresh, created3 = await svc.get_or_create_order(BUYER)
    check("после закрытия можно новый заказ", created3 and fresh["code"] == "NP-0002")
    await svc.cancel(fresh["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)


async def test_chat(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nПереписка по заказу")
    order, _ = await svc.get_or_create_order(BUYER)

    bot.sent.clear()
    message = await svc.post_message(order["id"], "Когда будет готово?", BUYER, from_seller=False)
    check("сообщение покупателя сохранено", message["author"] == "buyer")
    check("продавцу пришло уведомление", any("Когда будет готово?" in t for t in bot.to(SELLER)))
    check("у продавца непрочитанное", await db.unread_count(order["id"], db.SELLER) == 1)

    bot.sent.clear()
    await svc.post_message(order["id"], "Завтра к вечеру", SELLER, from_seller=True)
    check("покупателю пришёл ответ", any("Завтра к вечеру" in t for t in bot.to(BUYER)))
    check("у покупателя непрочитанное", await db.unread_count(order["id"], db.BUYER) == 1)

    messages = await svc.read_chat(order["id"], BUYER, as_seller=False)
    check("история из двух сообщений", len(messages) == 2)
    check("после чтения непрочитанных нет", await db.unread_count(order["id"], db.BUYER) == 0)
    check("порядок от старых к новым", messages[0]["text"] == "Когда будет готово?")

    try:
        await svc.post_message(order["id"], "   ", BUYER, from_seller=False)
        check("пустое сообщение отклонено", False)
    except ServiceError:
        check("пустое сообщение отклонено", True)

    try:
        await svc.post_message(order["id"], "x" * 3000, BUYER, from_seller=False)
        check("слишком длинное отклонено", False)
    except ServiceError:
        check("слишком длинное отклонено", True)

    try:
        await svc.read_chat(order["id"], 424242, as_seller=False)
        check("чужую переписку не прочитать", False)
    except ServiceError:
        check("чужую переписку не прочитать", True)

    await svc.cancel(order["id"], "test", by_seller=True)


async def test_notify(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nНастройки уведомлений")
    order, _ = await svc.get_or_create_order(BUYER)

    bot.sent.clear()
    await svc.post_message(order["id"], "Первый вопрос", BUYER, from_seller=False)
    check("по умолчанию уведомление приходит", any("Первый" in t for t in bot.to(SELLER)))

    await db.set_notify_pref(SELLER, const.NOTIFY_CHAT, False)
    bot.sent.clear()
    await svc.post_message(order["id"], "Второй вопрос", BUYER, from_seller=False)
    check("выключенный вид молчит", not any("Второй" in t for t in bot.to(SELLER)))

    await db.set_order_notify(SELLER, order["id"], True)
    bot.sent.clear()
    await svc.post_message(order["id"], "Третий вопрос", BUYER, from_seller=False)
    check("«всегда уведомлять» сильнее общего выключения",
          any("Третий" in t for t in bot.to(SELLER)))

    await db.set_notify_pref(SELLER, const.NOTIFY_CHAT, True)
    await db.set_order_notify(SELLER, order["id"], False)
    bot.sent.clear()
    await svc.post_message(order["id"], "Четвёртый вопрос", BUYER, from_seller=False)
    check("«не беспокоить» сильнее общего включения",
          not any("Четвёртый" in t for t in bot.to(SELLER)))

    await db.set_order_notify(SELLER, order["id"], None)
    bot.sent.clear()
    await svc.post_message(order["id"], "Пятый вопрос", BUYER, from_seller=False)
    check("после сброса снова приходит", any("Пятый" in t for t in bot.to(SELLER)))

    await db.set_notify_pref(BUYER, const.NOTIFY_STATUS, False)
    bot.sent.clear()
    await svc.confirm_payment(order["id"], "test")
    check("покупатель не получил выключенный статус",
          not any("Оплата получена" in t for t in bot.to(BUYER)))

    await db.set_notify_pref(BUYER, const.NOTIFY_STATUS, True)
    check("сообщения переписки не зависят от статуса",
          (await db.get_notify_prefs(BUYER))[const.NOTIFY_STATUS] is True)

    await svc.cancel(order["id"], "test", by_seller=True)


async def test_close_and_reorder(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nЗакрытие заказа и следующий заказ")
    order, _ = await svc.get_or_create_order(BUYER)

    order = await svc.confirm_payment(order["id"], "test")
    try:
        await svc.cancel(order["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)
        check("заказ в работе покупатель не отменяет", False)
    except ServiceError:
        check("заказ в работе покупатель не отменяет", True)

    order = await svc.submit_udid(order["id"], udid_mod.TEST_UDID, BUYER)
    order = await svc.send_instruction(order["id"], "test")
    check("заказ выполнен", order["status"] == const.DONE)

    same, created = await svc.get_or_create_order(BUYER)
    check("выполненный заказ не мешает новому", created and same["id"] != order["id"])
    await svc.cancel(same["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)

    bot.sent.clear()
    closed = await svc.cancel(order["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)
    check("выполненный заказ закрывается покупателем", bool(closed["closed_at"]))
    check("для продавца закрытый заказ остаётся выполненным", closed["status"] == const.DONE)
    check("продавец узнаёт, что покупатель закрыл заказ",
          any("Покупатель закрыл заказ" in t for t in bot.to(SELLER)))
    check("витрина видит, что заказ закрыт", order_public(closed)["closed"])
    labels = [b.text for row in keyboards.buyer_order_kb(cfg, closed).inline_keyboard for b in row]
    check("у закрытого заказа в боте нет кнопки «Закрыть заказ»", "Закрыть заказ" not in labels, str(labels))

    try:
        await svc.cancel(order["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)
        check("повторное закрытие отклоняется", False)
    except ServiceError as exc:
        check("повторное закрытие отклоняется", "уже закрыт" in str(exc), str(exc))

    events = await db.list_events(order["id"], limit=5)
    check("в истории видно, что заказ закрыт, а не отменён",
          any(ev["type"] == "closed" for ev in events)
          and not any(ev["type"] == "status:" + const.CANCELLED for ev in events))

    # Закрытый заказ у покупателя последний — экран заказа уступает оформлению нового.
    fresh, created = await svc.get_or_create_order(BUYER)
    check("после закрытия — новый заказ", created and fresh["id"] != order["id"])
    await svc.cancel(fresh["id"], "test", by_seller=True)


async def test_app_orders(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nЗаказ конкретного приложения")

    bot.sent.clear()
    order, created = await svc.get_or_create_order(BUYER, "spotify")
    check("заказ на приложение создан", created and order["app_slug"] == "spotify")
    check("без своей цены — общая", order["price_rub"] == cfg.price_rub)
    check("продавец видит товар", any("Товар: Spotify" in t for t in bot.to(SELLER)))

    same, created2 = await svc.get_or_create_order(BUYER, "max")
    check("неоплаченный заказ перенастроен на другое приложение",
          not created2 and same["id"] == order["id"] and same["app_slug"] == "max")

    whole, _ = await svc.get_or_create_order(BUYER, None)
    check("можно вернуться ко всему каталогу", whole["app_slug"] is None)

    toml = cfg.ipa_dir / "catalog.toml"
    original = toml.read_text(encoding="utf-8")
    # Заголовок раздела — с начала строки: в шапке файла [sber] встречается в комментарии.
    toml.write_text(original.replace("\n[sber]\n", "\n[sber]\nprice = 990\n", 1), encoding="utf-8")
    try:
        priced, _ = await svc.get_or_create_order(BUYER, "sber")
        check("своя цена из catalog.toml применяется", priced["price_rub"] == 990)
    finally:
        toml.write_text(original, encoding="utf-8")

    try:
        await svc.get_or_create_order(BUYER, "нет-такого")
        check("неизвестное приложение отклонено", False)
    except ServiceError:
        check("неизвестное приложение отклонено", True)

    await svc.submit_receipt(order["id"], BUYER, Receipt(title="чек.jpg", file="photo-id", kind="photo"))
    kept, _ = await svc.get_or_create_order(BUYER, "yota")
    check("заказ с заявленной оплатой не перенастраивается",
          kept["app_slug"] == "sber" and kept["status"] == const.PAYMENT_CHECK)

    await svc.cancel(order["id"], "test", by_seller=True)


async def test_api(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nHTTP API Mini App")
    app = build_app(cfg, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()

    buyer_headers = {"X-Init-Data": make_init_data(BUYER)}
    seller_headers = {"X-Init-Data": make_init_data(SELLER, "Продавец")}

    try:
        res = await client.get("/api/bootstrap")
        check("без подписи — 401", res.status == 401)

        res = await client.get("/api/bootstrap", headers=buyer_headers)
        data = await res.json()
        check("bootstrap отдаёт каталог", len(data["catalog"]) == 15, str(len(data["catalog"])))
        check("цена в витрине", data["product"]["priceText"].startswith("3"))
        check("покупатель не продавец", data["user"]["isSeller"] is False)

        developer = data["instruction"]["developerMode"]
        check("в инструкции первым — режим разработчика",
              developer["title"] == "Сначала включите режим разработчика" and len(developer["steps"]) == 4,
              str(developer)[:200])
        res = await client.get(developer["image"])
        check("картинка режима разработчика отдаётся",
              res.status == 200 and len(await res.read()) > 10_000, developer["image"])

        steps = data["instruction"]["steps"]
        check("шаблон инструкции отдаётся витрине", len(steps) == 10, str(len(steps)))
        check("у шагов есть тексты", all(s["title"] and s["subtitle"] and s["hint"] for s in steps))
        check("у шагов есть кадр переписки",
              [s["image"] for s in steps][:2]
              == ["/static/img/instruction/step-01.webp", "/static/img/instruction/step-02.webp"])
        check("в шаблоне два бота",
              [b["username"] for b in data["instruction"]["bots"]] == ["iRegerBot", "isignerbot"])
        sources = data["instruction"]["sources"]
        check("витрине отдаётся блок про IPA-файлы",
              sources["title"] == "Где взять IPA-файлы?" and "@iAppki" in sources["text"])
        check("у блока есть ссылка на канал", sources["url"] == "https://t.me/iAppki")

        about = data["about"]
        check("в витрине есть реквизиты продавца",
              about["legalName"].startswith("Самозанятый") and about["inn"] == "123456789012")
        check("в витрине есть контакты", about["email"] == "help@example.com")
        docs = {d["key"]: d for d in about["docs"]}
        check("документы перечислены",
              set(docs) == {"offer", "privacy", "refund", "consent"}, str(list(docs)))
        check("ссылка из .env перекрывает свой документ",
              docs["offer"]["url"] == "https://example.com/offer")
        check("свой документ открывается в витрине", docs["privacy"]["url"] == "/docs/privacy")

        res = await client.get("/api/seller/orders", headers=buyer_headers)
        check("панель продавца закрыта", res.status == 403)

        res = await client.post("/api/order/create", headers=buyer_headers)
        order = (await res.json())["order"]
        check("заказ через API создан", order["code"] == "NP-0003", order["code"])

        events = await db.list_events(order["id"])
        check("согласие зафиксировано в журнале",
              any(e["type"] == "consent" for e in events), str([e["type"] for e in events]))

        res = await client.post(
            "/api/order/udid",
            headers=buyer_headers,
            json={"orderId": order["id"], "udid": udid_mod.TEST_UDID},
        )
        check("UDID до оплаты — 400", res.status == 400)

        # «Я оплатил» без чека заказ не двигает — ни старой ручкой, ни новой без ссылки.
        res = await client.post("/api/order/claim", headers=buyer_headers, json={"orderId": order["id"]})
        answer = await res.json()
        check("«Я оплатил» без чека — отказ с объяснением",
              res.status == 400 and "чек" in answer.get("error", ""), str(answer))
        res = await client.post(
            "/api/order/receipt/link", headers=buyer_headers,
            json={"orderId": order["id"], "url": "оплатил, честно"},
        )
        check("текст без ссылки — 400", res.status == 400)
        check("без чека заказ так и ждёт оплату",
              (await db.get_order(order["id"]))["status"] == const.NEW)

        # Чек об оплате: файл уходит продавцу, заявляет оплату и отмечается в переписке.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6360000002000100ff03d20000000049454e44ae426082"
        )
        form = FormData()
        form.add_field("file", png, filename="chek.png", content_type="image/png")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        answer = await res.json()
        check("чек принят витриной и заявил оплату",
              res.status == 200 and answer["claimed"] and answer["order"]["status"] == const.PAYMENT_CHECK,
              str(answer))
        messages = await db.list_messages(order["id"])
        check("чек отмечен в переписке заказа",
              any("Чек об оплате" in m["text"] for m in messages))

        # Чек — любой файл до 15 МБ: формат не проверяется.
        bot.files.clear()
        form = FormData()
        form.add_field("file", os.urandom(12 * 1024 * 1024), filename="чек.zip", content_type="application/zip")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        check("любой формат чека принимается — архив 12 МБ", res.status == 200, str(res.status))
        check("необычный формат уходит продавцу файлом",
              any(cid == SELLER and kind == "document" for cid, kind, _cap in bot.files), str(bot.files))

        form = FormData()
        form.add_field("file", b"\x00" * (15 * 1024 * 1024 + 1), filename="big.mov", content_type="video/quicktime")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        answer = await res.json()
        check("файл больше 15 МБ — отказ с объяснением",
              res.status == 400 and "15 МБ" in answer.get("error", ""), str(answer))

        # Несколько файлов за раз — вместе до 15 МБ.
        bot.files.clear()
        bot.sent.clear()
        form = FormData()
        for index, size_mb in enumerate((1, 1, 2), start=1):
            form.add_field("file", os.urandom(size_mb * 1024 * 1024),
                           filename="часть-%d.pdf" % index, content_type="application/pdf")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        answer = await res.json()
        check("несколько файлов за раз принимаются",
              res.status == 200 and len(answer.get("names", [])) == 3, str(answer)[:200])
        check("продавцу ушли все три файла и следом итог",
              len([1 for cid, _kind, _cap in bot.files if cid == SELLER]) == 3
              and any("3 файла выше" in t for cid, t in bot.sent if cid == SELLER))

        form = FormData()
        for index in range(2):
            form.add_field("file", b"\x00" * (8 * 1024 * 1024),
                           filename="big-%d.bin" % index, content_type="application/octet-stream")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        answer = await res.json()
        check("два файла по 8 МБ вместе больше 15 — отказ",
              res.status == 400 and "вместе больше 15 МБ" in answer.get("error", ""), str(answer))

        form = FormData()
        for index in range(11):
            form.add_field("file", b"x", filename="f%d.txt" % index, content_type="text/plain")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        answer = await res.json()
        check("больше 10 файлов за раз — отказ",
              res.status == 400 and "Не больше 10" in answer.get("error", ""), str(answer))

        res = await client.post(
            "/api/order/receipt/link", headers=buyer_headers,
            json={"orderId": order["id"], "url": "Вот ещё чек: https://bank.ru/r/2."},
        )
        answer = await res.json()
        check("ссылка на чек из текста принята вдогонку",
              res.status == 200 and not answer["claimed"]
              and answer["order"]["status"] == const.PAYMENT_CHECK, str(answer))
        messages = await db.list_messages(order["id"])
        check("ссылка без хвостовой точки в переписке заказа",
              any(m["text"] == "🔗 Ссылка на чек: https://bank.ru/r/2" for m in messages))

        res = await client.post(
            "/api/seller/action",
            headers=seller_headers,
            json={"action": "payok", "orderId": order["id"]},
        )
        seller_view = (await res.json())["order"]
        check("продавец подтвердил оплату", seller_view["status"] == const.PAID)
        check("продавцу виден полный UDID", "udid" in seller_view)

        res = await client.post(
            "/api/order/udid/check", headers=buyer_headers, json={"udid": "49015420Ж32375"}
        )
        check("проверка номера ловит ошибку в символах", (await res.json())["valid"] is False)
        res = await client.post(
            "/api/order/udid/check", headers=buyer_headers, json={"udid": MODERN_UDID}
        )
        check("проверка номера пропускает новый формат", (await res.json())["valid"] is True)

        res = await client.post(
            "/api/order/udid",
            headers=buyer_headers,
            json={"orderId": order["id"], "udid": SPACED_UDID},
        )
        data = (await res.json())["order"]
        check("UDID принят через API", data["status"] == const.UDID)
        check("покупателю UDID замаскирован", data["udidMasked"].endswith("1186") and "udid" not in data)

        res = await client.post(
            "/api/seller/action",
            headers=seller_headers,
            json={"action": "installed", "orderId": order["id"]},
        )
        done = (await res.json())["order"]
        check("кнопка продавца сразу шлёт инструкцию",
              done["status"] == const.DONE and done["instructionReady"] is True)
        check("примечания продавца в заказе нет", "instruction" not in done)

        res = await client.get("/api/seller/orders?status=all", headers=seller_headers)
        data = await res.json()
        check("список заказов продавца", len(data["orders"]) >= 3)

        res = await client.get("/api/seller/orders?q=NP-0003", headers=seller_headers)
        check("поиск по коду", len((await res.json())["orders"]) == 1)

        res = await client.post(
            "/api/chat", headers=buyer_headers,
            json={"orderId": order["id"], "text": "Здравствуйте!"},
        )
        check("покупатель отправил сообщение", res.status == 200)

        res = await client.get("/api/chat?orderId=%d" % order["id"], headers=seller_headers)
        data = await res.json()
        hello = [m for m in data["messages"] if m["text"] == "Здравствуйте!"]
        check("продавец видит переписку", data["role"] == "seller" and len(hello) == 1)
        check("чужое сообщение помечено верно", hello[0]["mine"] is False)

        res = await client.post(
            "/api/chat", headers=seller_headers,
            json={"orderId": order["id"], "text": "Добрый день, уже делаем"},
        )
        check("продавец ответил", (await res.json())["message"]["mine"] is True)

        res = await client.get("/api/chat?orderId=%d" % order["id"], headers=buyer_headers)
        texts_seen = [m["text"] for m in (await res.json())["messages"]]
        check("покупатель видит оба сообщения",
              "Здравствуйте!" in texts_seen and "Добрый день, уже делаем" in texts_seen,
              str(texts_seen))

        res = await client.post(
            "/api/chat", headers=buyer_headers, json={"orderId": 999999, "text": "чужой заказ"}
        )
        check("несуществующий заказ — ошибка", res.status == 400)

        res = await client.get("/api/notify", headers=buyer_headers)
        data = await res.json()
        check("настройки покупателя отдаются",
              len(data["kinds"]) == 2 and all(k["enabled"] for k in data["kinds"]))

        res = await client.post(
            "/api/notify", headers=buyer_headers,
            json={"kind": const.NOTIFY_STATUS, "enabled": False},
        )
        data = await res.json()
        check("переключатель сохраняется",
              any(k["key"] == const.NOTIFY_STATUS and not k["enabled"] for k in data["kinds"]))

        res = await client.post(
            "/api/notify", headers=buyer_headers,
            json={"kind": const.NOTIFY_NEW_ORDER, "enabled": False},
        )
        check("чужой вид уведомления отклонён", res.status == 400)

        res = await client.post(
            "/api/notify/order", headers=buyer_headers,
            json={"orderId": order["id"], "mode": const.ORDER_NOTIFY_OFF},
        )
        check("режим заказа сохраняется",
              (await res.json())["order"]["mode"] == const.ORDER_NOTIFY_OFF)

        res = await client.get("/api/notify", headers=seller_headers)
        check("у продавца свой набор видов", len((await res.json())["kinds"]) == 5)

        await client.post(
            "/api/notify", headers=buyer_headers,
            json={"kind": const.NOTIFY_STATUS, "enabled": True},
        )

        res = await client.get("/api/bootstrap", headers=buyer_headers)
        items = (await res.json())["catalog"]
        check("у приложений в витрине есть цена",
              all(a["price"] and a["priceText"] for a in items))

        res = await client.post("/api/order/create", headers=buyer_headers, json={"app": "spotify"})
        created = (await res.json())["order"]
        check("заказ приложения через API",
              created["app"] == "spotify" and created["productName"] == "Spotify")
        await client.post("/api/order/cancel", headers=buyer_headers, json={"orderId": created["id"]})

        res = await client.post("/api/order/create", headers=buyer_headers, json={"app": "нет"})
        check("неизвестное приложение через API — 400", res.status == 400)

        res = await client.get("/health")
        check("healthcheck отвечает", res.status == 200)

        res = await client.get("/")
        body = await res.text()
        check("index.html отдаётся", res.status == 200 and "iApki" in body)
        check("ссылки на статику помечены версией",
              "app.js?v=" in body and "__V__" not in body, body[:200])
        check("витрина не кэшируется", "no-cache" in res.headers.get("Cache-Control", ""))

        res = await client.get("/static/app.js")
        check("статика отдаётся", res.status == 200)

        # Публичный режим: магазин должен открываться без Telegram —
        # иначе модерация платёжной системы не увидит витрину.
        res = await client.get("/api/public")
        public = await res.json()
        check("витрина открывается без подписи", res.status == 200 and public["public"] is True)
        check("в публичной витрине есть каталог", len(public["catalog"]) == 15,
              str(len(public["catalog"])))
        check("у всех приложений есть описание",
              all(a["desc"] for a in public["catalog"]))
        check("в публичной витрине есть реквизиты",
              public["about"]["legalLine"].startswith("Самозанятый"))
        check("в публичной витрине нет чужих данных", public["order"] is None)

        res = await client.get("/api/doc/refund")
        doc = await res.json()
        check("документ отдаётся витрине",
              res.status == 200 and "<h1>" in doc["html"] and "Возврат" in doc["title"])
        check("реквизиты подставлены в документ", "123456789012" in doc["html"])

        res = await client.get("/api/doc/нет-такого")
        check("неизвестный документ — 404", res.status == 404)

        res = await client.get("/docs/offer")
        page = await res.text()
        check("страница документа открывается", res.status == 200 and "оферта" in page.lower())
        check("в подвале страницы есть реквизиты", "ИНН 123456789012" in page)
        check("в подвале есть ссылки на документы", 'href="/docs/privacy"' in page)

        res = await client.get("/docs")
        check("список документов открывается", res.status == 200)
    finally:
        await client.close()


async def test_supplier(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nПоставщик")
    check(
        "роли разведены",
        (cfg.role_of(SELLER), cfg.role_of(SUPPLIER), cfg.role_of(BUYER))
        == (const.ROLE_SELLER, const.ROLE_SUPPLIER, const.ROLE_BUYER),
    )

    await db.upsert_user(SUPPLIER_BUYER, "petr", "Пётр")
    bot.sent.clear()
    order, created = await svc.get_or_create_order(SUPPLIER_BUYER)
    check("заказ создан", created)
    check(
        "поставщику ушло уведомление одной строкой",
        bot.to(SUPPLIER) == ["Заказ № %s от @petr" % order["code"]],
        str(bot.to(SUPPLIER)),
    )
    check("продавцу ушла полная карточка", any("Новый заказ" in t for t in bot.to(SELLER)))

    await svc.confirm_payment(order["id"], "seller:%d" % SELLER)
    await svc.submit_udid(order["id"], MODERN_UDID, SUPPLIER_BUYER)

    app = build_app(cfg, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()
    supplier_headers = {"X-Init-Data": make_init_data(SUPPLIER, "Поставщик")}
    buyer_headers = {"X-Init-Data": make_init_data(BUYER)}

    try:
        res = await client.get("/api/supplier/orders", headers=supplier_headers)
        rows = (await res.json())["orders"]
        row = next((o for o in rows if o["code"] == order["code"]), None)
        check("поставщик видит заказ с UDID", row is not None, str(rows)[:200])
        check(
            "в заказе только разрешённые поля",
            set(row) == {
                "id", "code", "status", "statusTitle",
                "firstName", "username", "createdAt", "udid", "isTest",
            },
            str(sorted(row)),
        )
        check("статус заказа виден", row["statusTitle"] == const.TITLES[const.UDID], row["statusTitle"])
        check("UDID нового формата дошёл как есть — с дефисом и регистром",
              row["udid"] == MODERN_UDID, row["udid"])
        check("покупатель виден", row["username"] == "petr" and row["firstName"] == "Пётр")

        res = await client.get("/api/supplier/orders", headers=buyer_headers)
        check("покупателю список UDID закрыт", res.status == 403)

        res = await client.get("/api/seller/orders", headers=supplier_headers)
        check("поставщику панель продавца закрыта", res.status == 403)

        res = await client.post(
            "/api/seller/action",
            headers=supplier_headers,
            json={"action": "payok", "orderId": order["id"]},
        )
        check("поставщик не управляет заказами", res.status == 403)

        res = await client.get("/api/bootstrap", headers=supplier_headers)
        user = (await res.json())["user"]
        check(
            "витрина знает поставщика",
            user["isSupplier"] is True and user["isSeller"] is False,
            str(user),
        )

        res = await client.get("/api/notify", headers=supplier_headers)
        kinds = [k["key"] for k in (await res.json())["kinds"]]
        check(
            "поставщику настраиваются только новые заказы",
            kinds == [const.NOTIFY_NEW_ORDER],
            str(kinds),
        )
    finally:
        await client.close()

    # Владелец держит обе роли на одном аккаунте — он должен получать и то, и другое.
    both = OrderService(bot, replace(cfg, supplier_ids=(SELLER,)))
    await db.upsert_user(557, "ivan", "Иван")
    bot.sent.clear()
    order2, _ = await both.get_or_create_order(557)
    to_seller = bot.to(SELLER)
    check(
        "продавец-поставщик получает и карточку, и строку поставщика",
        any("Новый заказ" in t for t in to_seller)
        and ("Заказ № %s от @ivan" % order2["code"]) in to_seller,
        str(to_seller),
    )


class FlakyBot(FakeBot):
    """Первую отправку файла Telegram отклоняет: «слишком часто, подождите»."""

    def __init__(self) -> None:
        super().__init__()
        self.refused = False

    async def send_document(self, chat_id, document, caption=None, reply_markup=None):
        if not self.refused:
            self.refused = True
            raise TelegramRetryAfter(
                method=SendDocument(chat_id=chat_id, document="x"), message="Too Many Requests", retry_after=0
            )
        return await super().send_document(chat_id, document, caption=caption, reply_markup=reply_markup)


async def test_receipt_batch(cfg) -> None:
    print("\nЧек несколькими файлами")
    bot = FlakyBot()
    svc = OrderService(bot, cfg)
    await db.upsert_user(610, "vera", "Вера")
    order, _ = await svc.get_or_create_order(610)
    order, claimed = await svc.submit_receipt(
        order["id"], 610,
        Receipt(title="скрин.png", file="photo-1", kind="photo"),
        Receipt(title="чек.pdf", file="doc-1"),
        Receipt(title="выписка.pdf", file="doc-2"),
    )
    check("несколько файлов сразу заявляют оплату", claimed and order["status"] == const.PAYMENT_CHECK)
    files = [(kind, cap) for cid, kind, cap in bot.files if cid == SELLER]
    check("продавцу пришли все файлы по порядку",
          [kind for kind, _cap in files] == ["photo", "document", "document"], str(files))
    check("у каждого файла подпись «файл N из 3»", all("из 3" in cap for _kind, cap in files))
    check("Telegram попросил подождать — файл всё равно дошёл", bot.refused and len(files) == 3)
    summary = [t for cid, t in bot.sent if cid == SELLER and "оплатил и приложил чек" in t]
    check("следом карточка заказа с итогом",
          len(summary) == 1 and "3 файла выше" in summary[0], str(summary)[:200])
    events = [e for e in await db.list_events(order["id"]) if e["type"] == "receipt"]
    check("каждый файл записан в журнал заказа", len(events) == 3, str(len(events)))


async def test_seller_filters(cfg, bot: FakeBot, svc: OrderService) -> None:
    """Сортировки и фильтры продавца — на своей базе, чтобы считать заказы точно."""
    print("\nПродавец: сортировка и фильтры")
    now = datetime.now(timezone.utc)

    def ago(days: float = 0, hours: float = 0) -> str:
        return (now - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")

    people = [(601, "dima", "Дима"), (602, "anna", "Анна"), (603, "boris", "Борис"), (604, None, "Яна")]
    ids = []
    for uid, username, name in people:
        await db.upsert_user(uid, username, name)
        order, _ = await svc.get_or_create_order(uid)
        ids.append(order["id"])
    a, b, c, d = ids
    # a — ждёт оплаты, b — на проверке, c — ставим сертификат (есть UDID), d — отменён.
    await svc.submit_receipt(b, 602, Receipt(title="чек.jpg", file="f", kind="photo"))
    await svc.submit_receipt(c, 603, Receipt(title="чек.jpg", file="f", kind="photo"))
    await svc.confirm_payment(c, "seller:%d" % SELLER)
    await svc.submit_udid(c, MODERN_UDID, 603)
    await svc.cancel(d, "seller:%d" % SELLER, by_seller=True)
    # Чеки пишутся в переписку непрочитанными — гасим, непрочитанное оставим только у a.
    await db.mark_seen(b, db.SELLER)
    await db.mark_seen(c, db.SELLER)
    await db.add_message(a, db.BUYER, 601, "Когда будет готово?")
    # Время создания и последней активности задаём явно — после всех действий.
    for oid, created, updated in (
        (a, ago(40), ago(hours=1)), (b, ago(3), ago(2)), (c, ago(hours=2), ago(hours=3)), (d, ago(10), ago(9)),
    ):
        await db.conn().execute(
            "UPDATE orders SET created_at = ?, updated_at = ? WHERE id = ?", (created, updated, oid)
        )
    await db.conn().commit()

    app = build_app(cfg, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()
    seller = {"X-Init-Data": make_init_data(SELLER, "Продавец")}

    async def listing(**params):
        res = await client.get("/api/seller/orders", headers=seller, params=params)
        data = await res.json()
        return [o["id"] for o in data.get("orders", [])], data.get("total"), res.status

    try:
        got, total, _ = await listing(status="all")
        check("по умолчанию — новые сверху", got == [d, c, b, a], str(got))
        check("total — сколько подошло всего", total == 4, str(total))
        got, *_ = await listing(status="all", sort="created_asc")
        check("старые сверху", got == [a, b, c, d], str(got))
        got, *_ = await listing(status="all", sort="activity")
        check("последняя активность", got == [a, c, b, d], str(got))
        got, *_ = await listing(status="all", sort="unread")
        check("непрочитанные сверху, дальше новые", got == [a, d, c, b], str(got))
        got, *_ = await listing(status="all", sort="buyer")
        check("покупатель по алфавиту, безымянные в конце", got == [b, c, a, d], str(got))

        got, total, _ = await listing(status="all", st="new,cancelled")
        check("фильтр по статусам внутри «Все»", got == [d, a] and total == 2, str(got))
        got, *_ = await listing(status="open", st="cancelled")
        check("статус не из подгруппы ничего не добавляет", got == [], str(got))
        got, *_ = await listing(status="open")
        check("«Открытые» без отменённых", got == [c, b, a], str(got))

        got, *_ = await listing(status="all", **{"from": ago(7)})
        check("период: 7 дней", got == [c, b], str(got))
        got, *_ = await listing(status="all", **{"from": ago(15), "to": ago(5)})
        check("период: свои даты", got == [d], str(got))
        got, *_ = await listing(status="all", **{"from": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S.000Z")})
        check("граница периода в формате браузера (…Z) понимается", got == [c, b], str(got))

        got, *_ = await listing(status="all", unread="1")
        check("только непрочитанные", got == [a], str(got))
        got, *_ = await listing(status="all", udid="yes")
        check("есть UDID", got == [c], str(got))
        got, *_ = await listing(status="all", udid="no")
        check("нет UDID", got == [d, b, a], str(got))
        got, *_ = await listing(status="all", udid="no", sort="created_asc", st="new,payment_check")
        check("фильтры и сортировка вместе", got == [a, b], str(got))

        _ids, _total, status = await listing(status="all", **{"from": "вчера"})
        check("кривая дата — 400", status == 400)
        _ids, _total, status = await listing(status="all", sort="price")
        check("неизвестная сортировка — 400", status == 400)

        code = (await db.get_order(c))["code"]
        got, *_ = await listing(status="done", q=code, unread="1", udid="no")
        check("поиск идёт мимо фильтров подгруппы", got == [c], str(got))
    finally:
        await client.close()


async def test_migration(tmp: str) -> None:
    print("\nМиграция базы")
    import sqlite3

    path = os.path.join(tmp, "old.sqlite3")
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, user_id INTEGER NOT NULL,
            status TEXT NOT NULL, price_rub INTEGER NOT NULL, payment_mode TEXT NOT NULL,
            app_slug TEXT, payment_ref TEXT, device_udid TEXT, instruction TEXT, seller_note TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, paid_at TEXT, udid_at TEXT,
            installed_at TEXT, done_at TEXT
        );
        INSERT INTO orders (code, user_id, status, price_rub, payment_mode, created_at, updated_at)
        VALUES ('NP-0001', 1, 'done', 3000, 'manual', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00');
        """
    )
    old.commit()
    old.close()

    await db.init(path)
    columns = {row["name"] for row in await db._fetchall("PRAGMA table_info(orders)")}
    check("в старую базу добавлена колонка closed_at", "closed_at" in columns)
    check("и колонка is_test", "is_test" in columns)
    order = await db.get_order(1)
    check("старый выполненный заказ не считается закрытым", order and order["closed_at"] is None)
    await db.close()
    await db.init(path)
    check("повторный запуск миграции не падает", True)
    await db.close()


async def test_test_orders(cfg) -> None:
    """Заказы тестировщиков из TEST_IDS не шумят у продавцов и поставщиков."""
    print("\nТестовые заказы")
    tester = 444  # тестировщик без роли
    cfg = replace(cfg, test_ids=(tester, SUPPLIER))
    bot = FakeBot()
    svc = OrderService(bot, cfg)
    for uid, name in ((tester, "Тестер"), (SUPPLIER, "Поставщик"), (BUYER, "Покупатель")):
        await db.upsert_user(uid, "u%d" % uid, name)

    order, _ = await svc.get_or_create_order(tester)
    check("заказ тестировщика помечен тестовым", order["is_test"] == 1)
    check("продавцу о тестовом заказе не пишем", not bot.to(SELLER), str(bot.to(SELLER))[:200])
    check("поставщику тоже", not bot.to(SUPPLIER), str(bot.to(SUPPLIER))[:200])

    await svc.submit_receipt(order["id"], tester, Receipt(title="чек.pdf", file="doc-1"))
    check("чек по тестовому заказу продавцу не уходит", not [f for f in bot.files if f[0] == SELLER])
    await svc.post_message(order["id"], "Проверка переписки", tester, from_seller=False)
    check("сообщение покупателя-тестировщика продавцу не уходит", not bot.to(SELLER))
    order = await svc.confirm_payment(order["id"], "seller:%d" % SELLER)
    check("покупателю-тестировщику всё приходит как обычно", bool(bot.to(tester)))

    # Поставщик сам тестирует: свою сторону он видит, с пометкой; продавец — нет.
    bot.sent.clear()
    own, _ = await svc.get_or_create_order(SUPPLIER)
    check("заказ поставщика из TEST_IDS — тестовый", own["is_test"] == 1)
    mine = bot.to(SUPPLIER)
    check("тестировщик-поставщик получает уведомление поставщика",
          any("Заказ №" in t for t in mine), str(mine)[:200])
    check("и оно помечено «Тестовый заказ»", all(t.startswith(texts.TEST_MARK) for t in mine if "Заказ №" in t))
    check("продавец о нём не знает", not bot.to(SELLER))

    bot.sent.clear()
    real, _ = await svc.get_or_create_order(BUYER)
    check("заказ обычного покупателя — настоящий", real["is_test"] == 0)
    check("о настоящем заказе продавцу пишем, без пометки",
          any("Новый заказ" in t and texts.TEST_MARK not in t for t in bot.to(SELLER)))
    check("и поставщику", any("Заказ №" in t for t in bot.to(SUPPLIER)))

    counts = await db.status_counts()
    check("счётчики — только настоящие заказы", sum(counts.values()) == 1, str(counts))
    check("тестовые считаются отдельно", await db.count_test_orders() == 2)
    check("в /orders тестовый помечен 🧪", texts.list_label(order, "x").startswith("🧪 "))
    check("настоящий — без пометки", not texts.list_label(real, "x").startswith("🧪"))

    app = build_app(cfg, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()
    seller = {"X-Init-Data": make_init_data(SELLER, "Продавец")}
    try:
        res = await client.get("/api/seller/orders", headers=seller, params={"status": "all"})
        data = await res.json()
        flags = {o["id"]: o["isTest"] for o in data["orders"]}
        check("в списке продавца видны все, тестовые с isTest",
              flags == {order["id"]: True, own["id"]: True, real["id"]: False}, str(flags))
        check("переписка тестового заказа не зажигает общий счётчик", data["unreadTotal"] == 0,
              str(data["unreadTotal"]))
        res = await client.get("/api/seller/orders", headers=seller, params={"status": "all", "test": "hide"})
        data = await res.json()
        check("фильтр «Скрыть тестовые»", [o["id"] for o in data["orders"]] == [real["id"]] and data["total"] == 1,
              str([o["id"] for o in data["orders"]]))
        res = await client.get("/api/bootstrap", headers=seller)
        check("точка на вкладке продавца не горит из-за тестовых", (await res.json())["sellerUnread"] == 0)
    finally:
        await client.close()


async def test_promo(cfg) -> None:
    """Акция: цена по акции до последнего дня, зачёркнутая обычная, пересчёт неоплаченных."""
    print("\nАкция")
    today = today_msk()
    promo = replace(cfg, price_rub=3000, promo_price_rub=1500, promo_until=today + timedelta(days=10),
                    promo_label="-50% в честь открытия")
    ended = replace(promo, promo_until=today - timedelta(days=1))
    endless = replace(promo, promo_until=None, promo_label="")

    check("акция идёт до последнего дня включительно", promo.promo_active(promo.promo_until))
    check("на следующий день — нет", not promo.promo_active(promo.promo_until + timedelta(days=1)))
    check("во время акции платят цену по акции", promo.current_price == 1500)
    check("после акции — снова обычная цена", ended.current_price == 3000 and not ended.promo_active())
    check("без срока акция бессрочная", endless.promo_active() and endless.current_price == 1500)
    check("процент считается сам", promo.promo_percent == 50)
    check("подпись по умолчанию — «Скидка 50%»", endless.promo_title == "Скидка 50%")
    check("срок по-русски", replace(promo, promo_until=date(2026, 11, 30)).promo_until_text == "до 30 ноября")
    check("без акции промо-цены нет", not cfg.promo_active() and cfg.current_price == cfg.price_rub)

    check("цена со скидкой: старая зачёркнута", texts.price_html(promo) == "<s>3 000 ₽</s> <b>1 500 ₽</b>")
    check("после акции зачёркнутой цены нет", texts.price_html(ended) == "<b>3 000 ₽</b>")
    hello = texts.welcome("Наташа", promo, 15)
    check("в приветствии подпись акции и срок",
          "-50% в честь открытия" in hello and promo.promo_until_text in hello and "<s>3 000 ₽</s>" in hello, hello[:200])
    check("после акции приветствие без неё", "в честь открытия" not in texts.welcome("Наташа", ended, 15))
    check("подпись из .env экранируется", "&lt;b&gt;" in texts.promo_line(replace(promo, promo_label="<b>")))
    labels = [b.text for row in keyboards.start_kb(promo, False).inline_keyboard for b in row]
    check("кнопка в приветствии зовёт со скидкой", any("−50%" in t for t in labels), str(labels))
    desc, short = texts.bot_description(promo), texts.bot_short_description(promo)
    check("описание бота с акцией и в лимитах Telegram",
          "-50% в честь открытия" in desc and len(desc) <= 512 and "1 500 ₽" in short and len(short) <= 120, short)
    check("после акции описание без неё", "в честь открытия" not in texts.bot_description(ended))
    long_label = replace(promo, promo_label="Ж" * 600)
    check("длинная подпись не ломает лимиты описания",
          len(texts.bot_description(long_label)) <= 512 and len(texts.bot_short_description(long_label)) <= 120)

    # Неоплаченный заказ по старой цене пересчитывается, заказ с чеком — нет.
    bot = FakeBot()
    old = OrderService(bot, replace(cfg, price_rub=3000))
    svc = OrderService(bot, promo)
    for uid in (701, 702):
        await db.upsert_user(uid, "u%d" % uid, "Покупатель")
    order, _ = await old.get_or_create_order(701)
    check("заказ оформлен по старой цене", order["price_rub"] == 3000)
    fresh = await svc.fresh_price(await db.get_order(order["id"]))
    check("неоплаченный заказ пересчитан на цену акции", fresh["price_rub"] == 1500)
    check("пересчёт сохранён в базе", (await db.get_order(order["id"]))["price_rub"] == 1500)
    check("в реквизитах сказано, что скидка учтена", "Скидка уже учтена" in texts.payment_instructions(fresh, promo))
    paid, _ = await old.get_or_create_order(702)
    await old.submit_receipt(paid["id"], 702, Receipt(title="чек.jpg", file="f", kind="photo"))
    kept = await svc.fresh_price(await db.get_order(paid["id"]))
    check("заказ с чеком хранит цену, по которой заявлена оплата", kept["price_rub"] == 3000)
    back = OrderService(bot, ended)
    again = await back.fresh_price(await db.get_order(order["id"]))
    check("после конца акции неоплаченный заказ стоит обычную цену", again["price_rub"] == 3000)
    fresh = await svc.fresh_price(again)

    app = build_app(promo, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        res = await client.get("/api/bootstrap", headers={"X-Init-Data": make_init_data(701, "Покупатель")})
        data = await res.json()
        product = data["product"]
        check("витрина: цена по акции", product["price"] == 1500 and product["priceText"] == "1 500 ₽")
        check("витрина: акция со старой ценой, скидкой и сроком",
              product["promo"] and product["promo"]["oldPriceText"] == "3 000 ₽"
              and product["promo"]["discountText"] == "1 500 ₽" and product["promo"]["percent"] == 50
              and product["promo"]["untilText"] == promo.promo_until_text, str(product["promo"]))
        check("bootstrap отдаёт заказ уже по текущей цене", data["order"]["price"] == 1500)
        res = await client.get("/api/public")
        public = (await res.json())["product"]
        check("публичная витрина тоже со скидкой", public["promo"] and public["price"] == 1500)
        res = await client.get("/docs/offer")
        check("в оферте цена, которую платят сегодня", "1 500 ₽" in await res.text())
    finally:
        await client.close()

    app = build_app(ended, bot, back)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        res = await client.get("/api/public")
        public = (await res.json())["product"]
        check("после акции витрина без неё", public["promo"] is None and public["price"] == 3000)
    finally:
        await client.close()

    # Опечатки в настройках акции останавливают запуск с понятным текстом.
    saved = {k: os.environ.get(k) for k in ("PRICE_RUB", "PROMO_PRICE_RUB", "PROMO_UNTIL")}
    try:
        os.environ.update({"PRICE_RUB": "3000", "PROMO_PRICE_RUB": "3000", "PROMO_UNTIL": ""})
        try:
            load_config()
            check("цена по акции не ниже обычной — ошибка", False)
        except SystemExit as exc:
            check("цена по акции не ниже обычной — ошибка", "PROMO_PRICE_RUB" in str(exc))
        os.environ.update({"PROMO_PRICE_RUB": "1500", "PROMO_UNTIL": "31/11/2026"})
        try:
            load_config()
            check("кривая дата акции — ошибка", False)
        except SystemExit as exc:
            check("кривая дата акции — ошибка", "PROMO_UNTIL" in str(exc))
        os.environ.update({"PROMO_UNTIL": "2026-11-30"})
        check("дата и в формате 2026-11-30", load_config().promo_until == date(2026, 11, 30))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def test_analytics(cfg) -> None:
    """Продажи — по дате подтверждения оплаты, по Москве; тестовые и демо не считаются."""
    print("\nАналитика")
    today = today_msk()

    def at(day: date, hour: int = 12, minute: int = 0) -> str:
        """Момент по Москве → формат базы (UTC ISO)."""
        moment = datetime.combine(day, datetime.min.time().replace(hour=hour, minute=minute), tzinfo=MSK)
        return moment.astimezone(timezone.utc).isoformat(timespec="seconds")

    async def make(uid, created, paid=None, status=const.NEW, price=1500, test=False, mode="manual"):
        await db.upsert_user(uid, "u%d" % uid, "Покупатель %d" % uid)
        order = await db.create_order(uid, price, mode, "NP", None, is_test=test)
        await db.conn().execute(
            "UPDATE orders SET created_at = ?, paid_at = ?, status = ? WHERE id = ?",
            (created, paid, status, order["id"]),
        )
        await db.conn().commit()
        return order["id"]

    d = lambda n: today - timedelta(days=n)  # noqa: E731
    await make(801, at(d(3)), at(d(2)), const.DONE, 3000)
    await make(802, at(today), at(today), const.UDID, 1500)
    await make(803, at(d(10)), at(d(9)), const.CANCELLED, 1500)  # отменён после оплаты
    await make(804, at(today), None, const.NEW, 1500)
    await make(805, at(d(5)), None, const.CANCELLED, 3000)  # отменён до оплаты
    await make(806, at(today), at(today), const.DONE, 1500, test=True)
    await make(807, at(today), at(today), const.DONE, 1500, mode="demo")
    await make(808, at(d(40)), at(d(40)), const.DONE, 3000)

    r = await analytics.report(analytics.period("today"))
    check("сегодня: одна продажа на 1 500 ₽", (r["sales"]["count"], r["sales"]["revenue"]) == (1, 1500), str(r["sales"]))
    check("тестовые и демо не считаются ни в продажах, ни в заказах", r["created"] == 2, str(r["created"]))
    st = {s["status"]: (s["count"], s["percent"]) for s in r["statuses"]}
    check("статусы за день с долями", st == {const.NEW: (1, 50), const.UDID: (1, 50)}, str(st))
    check("до оплаты дошла половина", r["conversion"] == 50)

    r = await analytics.report(analytics.period("7d"))
    check("7 дней: две продажи на 4 500 ₽", (r["sales"]["count"], r["sales"]["revenue"]) == (2, 4500), str(r["sales"]))
    check("средний чек", r["sales"]["average"] == 2250)
    st = {s["status"]: s["count"] for s in r["statuses"]}
    check("отменённый до оплаты — своей строкой", st.get(const.CANCELLED) == 1 and "cancelled_paid" not in st, str(st))
    check("график по дням — 7 точек, сумма как в продажах",
          r["series"]["unit"] == "day" and len(r["series"]["points"]) == 7
          and sum(p["count"] for p in r["series"]["points"]) == 2, str(r["series"])[:200])

    r = await analytics.report(analytics.period("30d"))
    check("30 дней: три продажи вместе с отменённой после оплаты", r["sales"]["count"] == 3 and r["sales"]["revenue"] == 6000)
    check("отменено после оплаты — отдельно", r["cancelledAfterPaid"] == {"count": 1, "revenue": 1500})
    check("без отменённых", r["net"] == {"count": 2, "revenue": 4500})
    st = {s["status"]: s["title"] for s in r["statuses"]}
    check("в статусах «Отменено после оплаты»", st.get("cancelled_paid") == "Отменено после оплаты", str(st))

    r = await analytics.report(analytics.period("all"))
    check("всё время: четыре продажи на 9 000 ₽", (r["sales"]["count"], r["sales"]["revenue"]) == (4, 9000))
    check("график за всё время — от первой продажи до сегодня",
          r["series"]["points"] and r["series"]["points"][0]["label"] == d(40).isoformat()
          and r["series"]["points"][-1]["label"] == today.isoformat(), str(r["series"])[:120])

    r = await analytics.report(analytics.period("custom", d(10), d(9)))
    check("свои даты", r["sales"]["count"] == 1 and r["period"]["key"] == "custom")

    # Оплата в 00:30 по Москве — это уже сегодня, хотя в UTC ещё вчера.
    before = (await analytics.report(analytics.period("today")))["sales"]["count"]
    await make(809, at(today, 0, 30), at(today, 0, 30), const.DONE, 1500)
    after = (await analytics.report(analytics.period("today")))["sales"]["count"]
    check("сутки считаются по Москве", after == before + 1)

    # Разбор периода в /stats.
    p = analytics.period_from_text
    check("/stats без периода — всё время", p("").key == "all")
    check("слова периода", p("7d").key == "7d" and p("сегодня").key == "today" and p("месяц").key == "month")
    rng = p("01.10-20.10", today=date(2026, 10, 21))
    check("даты без года — текущий год", (rng.start, rng.end) == (date(2026, 10, 1), date(2026, 10, 20)))
    rng = p("01.10.2025-20.10.2025")
    check("даты с годом", (rng.start, rng.end) == (date(2025, 10, 1), date(2025, 10, 20)))
    rng = p("2026-10-01..2026-10-20")
    check("даты ISO", (rng.start, rng.end) == (date(2026, 10, 1), date(2026, 10, 20)))
    check("один день", p("20.10.2026").start == p("20.10.2026").end == date(2026, 10, 20))
    check("перепутанные даты меняются местами", p("20.10.2026-01.10.2026").start == date(2026, 10, 1))
    for bad in ("вчера", "32.10.2026", "01.10-20.10-30.10"):
        try:
            p(bad)
            check("кривой период «%s» — ошибка" % bad, False)
        except analytics.PeriodError:
            check("кривой период «%s» — ошибка" % bad, True)

    # API — только продавцу.
    bot = FakeBot()
    svc = OrderService(bot, cfg)
    app = build_app(cfg, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        url = "/api/seller/analytics"
        res = await client.get(url, params={"period": "30d"}, headers={"X-Init-Data": make_init_data(SELLER, "Продавец")})
        data = await res.json()
        check("продавцу отчёт", res.status == 200 and data["sales"]["count"] == 4, str(data)[:200])
        for who, name in ((801, "покупателю"), (SUPPLIER, "поставщику")):
            res = await client.get(url, headers={"X-Init-Data": make_init_data(who)})
            check("%s аналитика закрыта" % name, res.status == 403, str(res.status))
        res = await client.get(url, params={"period": "custom", "from": "2026-10-01", "to": "2026-10-20"},
                               headers={"X-Init-Data": make_init_data(SELLER, "Продавец")})
        check("свои даты через API", res.status == 200 and (await res.json())["period"]["from"] == "2026-10-01")
        for bad in ({"period": "year"}, {"period": "custom"}, {"period": "custom", "from": "вчера"}):
            res = await client.get(url, params=bad, headers={"X-Init-Data": make_init_data(SELLER, "Продавец")})
            check("кривой период в API — 400 (%s)" % bad, res.status == 400, str(res.status))
    finally:
        await client.close()

    # /forget обезличивает: продажи остаются, данные покупателя — нет.
    before = (await analytics.report(analytics.period("all")))["sales"]
    oid = await make(810, at(today), at(today), const.UDID, 1500)
    await db.set_udid(oid, MODERN_UDID, "user:810")
    await db.set_note(oid, "Позвонить Ивану")
    await db.add_message(oid, db.BUYER, 810, "Мой номер +7 900 111-22-33")
    await db.add_event(oid, "user:810", "receipt", "чек Иванов.pdf")
    await db.set_notify_pref(810, const.NOTIFY_STATUS, False)
    result = await db.forget_user(810)
    order = await db.get_order(oid)
    check("заказ остался и перешёл на удалённого покупателя",
          result == {"user": True, "orders": 1} and order["user_id"] == db.ANON_USER_ID)
    check("UDID и заметка стёрты", order["device_udid"] is None and order["seller_note"] is None)
    check("сумма, даты и статус остались", order["price_rub"] == 1500 and order["paid_at"] and order["status"] == const.UDID)
    check("переписка удалена", await db.list_messages(oid) == [])
    events = await db.list_events(oid)
    check("в журнале нет Telegram ID и записей о чеках",
          all("810" not in (ev["actor"] or "") for ev in events) and not any(ev["type"] == "receipt" for ev in events),
          str([(ev["actor"], ev["type"]) for ev in events]))
    check("в журнале отмечено обезличивание", any(ev["type"] == "forget" for ev in events))
    check("пользователь и его настройки удалены",
          await db.get_user(810) is None and await db.get_notify_prefs(810) == {})
    after = (await analytics.report(analytics.period("all")))["sales"]
    check("продажа осталась в аналитике", after["count"] == before["count"] + 1)
    check("повторный /forget — данных нет", await db.forget_user(810) == {"user": False, "orders": 0})


def bump(path: pathlib.Path, seconds: int) -> None:
    """Сдвигает время файла: бот показывает самую свежую сборку приложения."""
    moment = time.time() + seconds
    os.utime(path, (moment, moment))


async def test_ipa(cfg, bot: FakeBot, svc: OrderService) -> None:
    print("\nВитрина из сборок")
    folder = pathlib.Path(tempfile.mkdtemp())
    fixtures.make_ipa(folder / "sber-17.6.1.ipa", name="Sber", ru_name="Сбербанк", icon=True)
    fixtures.make_ipa(folder / "newapp-2.1.ipa", name="New App")
    fixtures.make_ipa(folder / "Bad Name 1.0.ipa")
    (folder / "readme.txt").write_text("не сборка", encoding="utf-8")

    found = ipa_mod.entries(folder)
    check("сборки найдены, кривое имя пропущено", sorted(found) == ["newapp", "sber"], str(sorted(found)))
    check("версия берётся из имени файла", found["sber"]["version"] == "17.6.1")
    check("минимальная iOS читается из сборки", found["sber"]["minOs"] == "16.0")
    check("размер человеку", ipa_mod.size_text(262_400_000) == "250,2 МБ",
          ipa_mod.size_text(262_400_000))
    check("имя файла для браузера", ipa_mod.download_name(found["sber"]) == "sber-17.6.1.ipa")
    slug, version = ipa_mod._slug_and_version("ozon-bank-19.27.0")
    check("слаг с дефисом разобран", (slug, version) == ("ozon-bank", "19.27.0"))
    slug, version = ipa_mod._slug_and_version("sber")
    check("файл без версии", (slug, version) == ("sber", ""))

    with_files = replace(cfg, ipa_dir=folder)
    app = build_app(with_files, bot, svc)
    client = TestClient(TestServer(app))
    await client.start_server()

    async def showcase() -> dict:
        res = await client.get("/api/public")
        return await res.json()

    try:
        data = await showcase()
        items = {a["slug"]: a for a in data["catalog"]}
        check("товары появились из сборок сами", sorted(items) == ["newapp", "sber"], str(sorted(items)))
        check("русское название из локализации сборки", items["sber"]["name"] == "Сбербанк",
              items["sber"]["name"])
        check("без локализации — название из Info.plist", items["newapp"]["name"] == "New App")
        check("без уточнений категория «Другое»", items["sber"]["category"] == "Другое")
        check("фильтры — только те, где есть товары", data["categories"] == ["Другое"], data["categories"])
        check("версия сборки в витрине", items["sber"]["build"]["version"] == "17.6.1")
        check("иконка из сборки", items["sber"]["iconUrl"].startswith("/icons/sber?v="))
        check("без иконки — буква", items["newapp"]["iconUrl"] == "" and items["newapp"]["letter"] == "N")

        res = await client.get(items["sber"]["iconUrl"])
        body = await res.read()
        check("иконка отдаётся обычным PNG, который откроет браузер",
              res.status == 200 and res.content_type == "image/png"
              and body[:8] == bundle.PNG_MAGIC and b"CgBI" not in body, str(res.status))
        res = await client.get("/icons/newapp")
        check("нет иконки — 404", res.status == 404)

        own_png = bundle.from_apple_png(fixtures.apple_png(rgba=(10, 20, 200, 255)))
        (folder / "newapp.png").write_bytes(own_png)
        (folder / "catalog.toml").write_text("""
featured = ["newapp"]
categories = ["Банки"]

[sber]
name = "СберБанк"
category = "Банки"
desc = "Счета и карты."
version = "17.7"
min_ios = "15.0"

["newapp-2.1.ipa"]
name = "Новое"
icon = "newapp.png"
nmae = "опечатка"
""", encoding="utf-8")

        data = await showcase()
        items = {a["slug"]: a for a in data["catalog"]}
        check("название из catalog.toml", items["sber"]["name"] == "СберБанк")
        check("категория из catalog.toml", items["sber"]["category"] == "Банки")
        check("описание из catalog.toml", items["sber"]["desc"] == "Счета и карты.")
        check("версию можно поправить", items["sber"]["build"]["version"] == "17.7")
        check("минимальную iOS можно поправить", items["sber"]["build"]["minOs"] == "15.0")
        check("раздел по имени файла", items["newapp"]["name"] == "Новое", items["newapp"]["name"])
        check("опечатка в поле не ломает витрину", len(items) == 2)
        check("порядок фильтров из catalog.toml", data["categories"] == ["Банки", "Другое"], data["categories"])
        check("плитки в шапке из catalog.toml", data["featured"] == ["newapp"], data["featured"])

        res = await client.get(items["newapp"]["iconUrl"])
        check("своя картинка вместо иконки", res.status == 200 and await res.read() == own_png)

        res = await client.get("/ipa/sber")
        body = await res.read()
        check("файл отдаётся", res.status == 200 and len(body) > 2000, str(res.status))
        check("браузер сохранит файл с поправленной версией",
              res.headers["Content-Disposition"] == 'attachment; filename="sber-17.7.ipa"',
              res.headers.get("Content-Disposition", ""))
        check("тип — архив", res.headers["Content-Type"] == "application/octet-stream")

        newer = folder / "newapp-2.2.ipa"
        fixtures.make_ipa(newer, name="New App")
        bump(newer, 60)
        data = await showcase()
        items = {a["slug"]: a for a in data["catalog"]}
        check("новая версия вытесняет старую", items["newapp"]["build"]["version"] == "2.2")
        check("раздел файла не действует на другую версию", items["newapp"]["name"] == "New App")

        with open(folder / "catalog.toml", "a", encoding="utf-8") as fh:
            fh.write("\n[newapp]\nhidden = true\n")
        data = await showcase()
        check("hidden убирает товар из витрины", [a["slug"] for a in data["catalog"]] == ["sber"])
        res = await client.get("/ipa/newapp")
        check("скрытый товар не скачать", res.status == 404)

        (folder / "catalog.toml").write_text("[sber\nname = ", encoding="utf-8")
        res = await client.get("/api/public")
        data = await res.json()
        items = {a["slug"]: a for a in data["catalog"]}
        check("ошибка в catalog.toml не роняет витрину — работает прошлая версия",
              res.status == 200 and items.get("sber", {}).get("name") == "СберБанк")

        links = catalog.download_links(with_files)
        check("ссылка для чата бота", links.get("sber") == "https://example.com/ipa/sber", str(links))
        check("в каталоге бота название стало ссылкой",
              '<a href="https://example.com/ipa/sber">СберБанк</a>' in catalog.as_text(links))

        (folder / "sber-17.6.1.ipa").unlink()
        data = await showcase()
        check("убрали сборку — товар пропал", "sber" not in [a["slug"] for a in data["catalog"]])
        check("заказ на убранное приложение помнит название", catalog.product_name("sber") == "СберБанк")
        res = await client.get("/ipa/sber")
        check("без файла — 404", res.status == 404)
        res = await client.get("/ipa/..%2F..%2Fetc%2Fpasswd")
        check("чужой путь не скачать", res.status == 404, str(res.status))
    finally:
        await client.close()
        catalog.use_folder(cfg.ipa_dir)


async def main() -> int:
    cfg = load_config()
    check("режим оплаты из .env", cfg.payment_mode == "manual")

    rows = {row["key"]: row for row in cfg.requisites}
    check("реквизиты перевода разобраны",
          list(rows) == ["phone", "bank", "name", "card"], str(list(rows)))
    check("телефон копируется, банк — нет",
          rows["phone"]["copy"] is True and rows["bank"]["copy"] is False)
    check("иконка банка найдена по названию", rows["bank"]["icon"] == "sber", rows["bank"]["icon"])
    check("иконка СБП у телефона", rows["phone"]["icon"] == "sbp")
    check("сумма к переводу берётся из цены заказа", cfg.amount_text(3000) == "3 000 ₽")

    tmp = tempfile.mkdtemp()
    await db.init(os.path.join(tmp, "test.sqlite3"))

    bot = FakeBot()
    svc = OrderService(bot, cfg)

    test_udid()
    test_documents(cfg)
    test_auth()
    await test_service(cfg, bot, svc)
    await test_api(cfg, bot, svc)
    await test_chat(cfg, bot, svc)
    await test_notify(cfg, bot, svc)
    await test_close_and_reorder(cfg, bot, svc)
    await test_app_orders(cfg, bot, svc)
    await test_supplier(cfg, bot, svc)
    await test_receipt_batch(cfg)
    await test_ipa(cfg, bot, svc)

    # Фильтрам продавца нужна своя база: в общей заказы остальных проверок.
    await db.close()
    await db.init(os.path.join(tmp, "filters.sqlite3"))
    await test_seller_filters(cfg, bot, svc)

    await db.close()
    await db.init(os.path.join(tmp, "testers.sqlite3"))
    await test_test_orders(cfg)
    await db.close()
    await db.init(os.path.join(tmp, "promo.sqlite3"))
    await test_promo(cfg)
    await db.close()
    await db.init(os.path.join(tmp, "analytics.sqlite3"))
    await test_analytics(cfg)
    await db.close()
    await test_migration(tmp)

    print("")
    if failures:
        print("ПРОВАЛЕНО: %d — %s" % (len(failures), ", ".join(failures)))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
