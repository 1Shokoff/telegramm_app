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
import sys
import tempfile
import time
from urllib.parse import urlencode

TOKEN = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
BUYER = 777
SELLER = 999

os.environ.update(
    {
        "BOT_TOKEN": TOKEN,
        "SELLER_IDS": str(SELLER),
        "WEBAPP_URL": "https://example.com",
        "PAYMENT_MODE": "manual",
        "PAYMENT_DETAILS": "СБП: +7 900 000-00-00\\nПолучатель: Тест",
        "INSTRUCTION_TEMPLATE": "Шаг 1\\nШаг 2",
        "PRICE_RUB": "3000",
        "ORDER_PREFIX": "NP",
        "DEV_MODE": "0",
        "LOG_LEVEL": "WARNING",
    }
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import auth, const, db, udid as udid_mod  # noqa: E402
from app.api import build_app  # noqa: E402
from app.config import load_config  # noqa: E402
from app.service import OrderService, ServiceError  # noqa: E402

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

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))
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

    order = await svc.claim_payment(order["id"], BUYER)
    check("статус: оплата на проверке", order["status"] == const.PAYMENT_CHECK)

    try:
        await svc.claim_payment(order["id"], SELLER)
        check("чужой заказ не тронуть", False)
    except ServiceError:
        check("чужой заказ не тронуть", True)

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

    try:
        await svc.send_instruction(order["id"], "текст", "seller:%d" % SELLER)
        order = await db.get_order(order["id"])
        check("инструкция до установки", order["status"] == const.DONE)
    except ServiceError:
        check("инструкция до установки разрешена", True)

    order = await db.set_status(order["id"], const.UDID, "test")
    order = await svc.mark_installed(order["id"], "seller:%d" % SELLER)
    check("статус: сертификат установлен", order["status"] == const.INSTALLED)

    order = await svc.send_instruction(order["id"], "Профиль → Настройки → Основные", "seller:%d" % SELLER)
    check("статус: готово", order["status"] == const.DONE)
    check("инструкция ушла покупателю", any("Инструкция" in t for t in bot.to(BUYER)))

    events = await db.list_events(order["id"])
    check("история пишется", len(events) >= 5, "событий: %d" % len(events))

    fresh, created3 = await svc.get_or_create_order(BUYER)
    check("после закрытия можно новый заказ", created3 and fresh["code"] == "NP-0002")
    await svc.cancel(fresh["id"], "user:%d" % BUYER, by_seller=False, actor_id=BUYER)


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
        check("bootstrap отдаёт каталог", len(data["catalog"]) == 24)
        check("цена в витрине", data["product"]["priceText"].startswith("3"))
        check("покупатель не продавец", data["user"]["isSeller"] is False)

        res = await client.get("/api/seller/orders", headers=buyer_headers)
        check("панель продавца закрыта", res.status == 403)

        res = await client.post("/api/order/create", headers=buyer_headers)
        order = (await res.json())["order"]
        check("заказ через API создан", order["code"] == "NP-0003", order["code"])

        res = await client.post(
            "/api/order/udid",
            headers=buyer_headers,
            json={"orderId": order["id"], "udid": udid_mod.TEST_UDID},
        )
        check("UDID до оплаты — 400", res.status == 400)

        res = await client.post("/api/order/claim", headers=buyer_headers, json={"orderId": order["id"]})
        check("оплата заявлена", (await res.json())["order"]["status"] == const.PAYMENT_CHECK)

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
        check("сертификат отмечен", (await res.json())["order"]["status"] == const.INSTALLED)

        res = await client.post(
            "/api/seller/action",
            headers=seller_headers,
            json={"action": "instruction", "orderId": order["id"], "text": "Готово, профиль установлен"},
        )
        check("инструкция отправлена", (await res.json())["order"]["status"] == const.DONE)

        res = await client.get("/api/seller/orders?status=all", headers=seller_headers)
        data = await res.json()
        check("список заказов продавца", len(data["orders"]) >= 3)

        res = await client.get("/api/seller/orders?q=NP-0003", headers=seller_headers)
        check("поиск по коду", len((await res.json())["orders"]) == 1)

        res = await client.get("/health")
        check("healthcheck отвечает", res.status == 200)

        res = await client.get("/")
        body = await res.text()
        check("index.html отдаётся", res.status == 200 and "Нужные приложения" in body)

        res = await client.get("/static/app.js")
        check("статика отдаётся", res.status == 200)
    finally:
        await client.close()


async def main() -> int:
    cfg = load_config()
    check("режим оплаты из .env", cfg.payment_mode == "manual")
    check("перенос строки в реквизитах", "\n" in cfg.payment_details)
    check("шаблон инструкции разобран", "\n" in cfg.instruction_template)

    tmp = tempfile.mkdtemp()
    await db.init(os.path.join(tmp, "test.sqlite3"))

    bot = FakeBot()
    svc = OrderService(bot, cfg)

    test_udid()
    test_auth()
    await test_service(cfg, bot, svc)
    await test_api(cfg, bot, svc)

    await db.close()

    print("")
    if failures:
        print("ПРОВАЛЕНО: %d — %s" % (len(failures), ", ".join(failures)))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
