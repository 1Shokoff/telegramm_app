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

from aiohttp import FormData  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import auth, bundle, catalog, const, db, ipa as ipa_mod, udid as udid_mod  # noqa: E402
from app.api import build_app  # noqa: E402
from app.config import load_config  # noqa: E402
from app.service import OrderService, Receipt, ServiceError  # noqa: E402

SPACED_UDID = "2b6f0cc904d137be2e17 30235f5664094b831186"

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


def test_udid() -> None:
    print("\nUDID")
    check("тестовый номер валиден", udid_mod.validate(udid_mod.TEST_UDID)[0] == udid_mod.TEST_UDID)
    check("пробелы вычищаются", udid_mod.validate(SPACED_UDID)[0] == udid_mod.TEST_UDID)
    check("верхний регистр приводится", udid_mod.validate(udid_mod.TEST_UDID.upper())[0] == udid_mod.TEST_UDID)
    check("39 символов отклоняются", udid_mod.validate(udid_mod.TEST_UDID[:-1])[0] is None)
    check("недопустимые символы отклоняются", udid_mod.validate("z" * 40)[0] is None)
    check("IMEI распознаётся отдельно", "IMEI" in (udid_mod.validate("490154203237518")[1] or ""))
    check("новый формат распознаётся", "25" in (udid_mod.validate("00008030-001C2D3E1E88802E")[1] or ""))
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
        order["id"], BUYER, Receipt(title="перевод.jpg", file="photo-id", is_photo=True)
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
        await svc.submit_udid(order["id"], "123", BUYER)
        check("короткий UDID отклонён", False)
    except ServiceError:
        check("короткий UDID отклонён", True)

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

    closed = await svc.cancel(order["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)
    check("выполненный заказ закрывается покупателем", closed["status"] == const.CANCELLED)

    try:
        await svc.cancel(order["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)
        check("повторное закрытие отклоняется", False)
    except ServiceError:
        check("повторное закрытие отклоняется", True)

    events = await db.list_events(order["id"], limit=5)
    check("в истории видно, что заказ закрыт, а не отменён",
          any(ev["detail"] == "закрыт" for ev in events))


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

    await svc.submit_receipt(order["id"], BUYER, Receipt(title="чек.jpg", file="photo-id", is_photo=True))
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

        form = FormData()
        form.add_field("file", b"MZ", filename="virus.exe", content_type="application/x-msdownload")
        res = await client.post(
            "/api/order/receipt?orderId=%d" % order["id"], headers=buyer_headers, data=form
        )
        check("чужой тип файла отклонён", res.status == 400)

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
            "/api/order/udid/check", headers=buyer_headers, json={"udid": "4901542032375"}
        )
        check("проверка номера ловит ошибку", (await res.json())["valid"] is False)

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
    await svc.submit_udid(order["id"], udid_mod.TEST_UDID, SUPPLIER_BUYER)

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
                "firstName", "username", "createdAt", "udid",
            },
            str(sorted(row)),
        )
        check("статус заказа виден", row["statusTitle"] == const.TITLES[const.UDID], row["statusTitle"])
        check("UDID отдаётся целиком", row["udid"] == udid_mod.TEST_UDID)
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
    test_auth()
    await test_service(cfg, bot, svc)
    await test_api(cfg, bot, svc)
    await test_chat(cfg, bot, svc)
    await test_notify(cfg, bot, svc)
    await test_close_and_reorder(cfg, bot, svc)
    await test_app_orders(cfg, bot, svc)
    await test_supplier(cfg, bot, svc)
    await test_ipa(cfg, bot, svc)

    await db.close()

    print("")
    if failures:
        print("ПРОВАЛЕНО: %d — %s" % (len(failures), ", ".join(failures)))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
