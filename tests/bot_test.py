"""Проверка хэндлеров бота без сети: python -m tests.bot_test

Апдейты скармливаются диспетчеру напрямую, а Bot подменён заглушкой,
которая запоминает вызовы API вместо запросов в Telegram.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone

TOKEN = "123456:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
BUYER = 777
SELLER = 999

os.environ.update(
    {
        "BOT_TOKEN": TOKEN,
        "SELLER_IDS": str(SELLER),
        "WEBAPP_URL": "https://example.com",
        "PAYMENT_MODE": "manual",
        "PAYMENT_DETAILS": "СБП: +7 900 000-00-00",
        "PRICE_RUB": "3000",
        "LOG_LEVEL": "WARNING",
    }
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update, User  # noqa: E402

from app import const, db  # noqa: E402
from app.config import load_config  # noqa: E402
from app.main import build_dispatcher  # noqa: E402
from app.service import OrderService  # noqa: E402

failures: list[str] = []
counter = {"id": 0}


def check(name: str, condition: bool, extra: str = "") -> None:
    if condition:
        print("  ok   %s" % name)
    else:
        failures.append(name)
        print("  FAIL %s %s" % (name, extra))


class StubBot(Bot):
    """Перехватывает исходящие вызовы Telegram API."""

    def __init__(self) -> None:
        super().__init__(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.calls: list[object] = []

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return make_message(
                chat_id=getattr(method, "chat_id", BUYER),
                text=getattr(method, "text", ""),
                from_bot=True,
            )
        return True

    def texts_to(self, chat_id: int) -> list[str]:
        return [
            m.text for m in self.calls
            if isinstance(m, (SendMessage, EditMessageText)) and getattr(m, "chat_id", None) == chat_id
        ]

    def reset(self) -> None:
        self.calls.clear()


def make_message(chat_id: int, text: str, from_bot: bool = False) -> Message:
    counter["id"] += 1
    return Message(
        message_id=counter["id"],
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(
            id=0 if from_bot else chat_id,
            is_bot=from_bot,
            first_name="Бот" if from_bot else "Тест",
            username=None if from_bot else "user%d" % chat_id,
        ),
        text=text,
    )


def message_update(chat_id: int, text: str) -> Update:
    counter["id"] += 1
    return Update(update_id=counter["id"], message=make_message(chat_id, text))


def callback_update(chat_id: int, data: str) -> Update:
    counter["id"] += 1
    return Update(
        update_id=counter["id"],
        callback_query=CallbackQuery(
            id="cb%d" % counter["id"],
            from_user=User(id=chat_id, is_bot=False, first_name="Тест", username="user%d" % chat_id),
            chat_instance="chat%d" % chat_id,
            data=data,
            message=make_message(chat_id, "предыдущее сообщение"),
        ),
    )


async def main() -> int:
    cfg = load_config()
    tmp = tempfile.mkdtemp()
    await db.init(os.path.join(tmp, "bot.sqlite3"))

    bot = StubBot()
    service = OrderService(bot, cfg)
    dp = build_dispatcher(cfg, service)

    used = dp.resolve_used_update_types()
    print("Диспетчер")
    check("подписка на message", "message" in used)
    check("подписка на callback_query", "callback_query" in used)
    check("подписка на pre_checkout_query", "pre_checkout_query" in used, str(used))

    print("\nПокупатель")
    await dp.feed_update(bot, message_update(BUYER, "/start"))
    check("/start отвечает витриной", any("Все приложения" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "nav:buy_confirm"))
    order = await db.get_active_order(BUYER)
    check("заказ создан кнопкой", order is not None and order["status"] == const.NEW)
    check("реквизиты показаны", any("Оплата заказа" in t for t in bot.texts_to(BUYER)))
    check("продавцу пришло уведомление", any("Новый заказ" in t for t in bot.texts_to(SELLER)))

    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:claim:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("«Я оплатил» меняет статус", order["status"] == const.PAYMENT_CHECK)
    check("продавец видит заявку", any("заявил оплату" in t for t in bot.texts_to(SELLER)))

    print("\nПродавец")
    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:payok:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("покупатель не подтверждает оплату сам", order["status"] == const.PAYMENT_CHECK)

    await dp.feed_update(bot, callback_update(SELLER, "o:payok:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("продавец подтвердил оплату", order["status"] == const.PAID)
    check("у покупателя запросили IMEI", any("IMEI" in t for t in bot.texts_to(BUYER)))

    print("\nIMEI текстом в чат")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "12345"))
    order = await db.get_order(order["id"])
    check("мусор не принимается", order["imei"] is None)
    check("подсказка про 15 цифр", any("15" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "490154 203237518"))
    order = await db.get_order(order["id"])
    check("IMEI принят", order["imei"] == "490154203237518" and order["status"] == const.IMEI)
    check("продавец получил IMEI", any("Получен IMEI" in t for t in bot.texts_to(SELLER)))

    print("\nЗавершение заказа")
    bot.reset()
    await dp.feed_update(bot, callback_update(SELLER, "o:installed:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("сертификат отмечен", order["status"] == const.INSTALLED)

    await dp.feed_update(bot, callback_update(SELLER, "o:instr:%d" % order["id"]))
    await dp.feed_update(bot, message_update(SELLER, "Откройте Настройки → Профиль и доверьте сертификат"))
    order = await db.get_order(order["id"])
    check("инструкция отправлена", order["status"] == const.DONE)
    check("покупатель получил текст", any("Настройки" in t for t in bot.texts_to(BUYER)))

    print("\nКоманды продавца")
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/orders all"))
    check("/orders работает", any("Заказы" in t or "заказы" in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/find %s" % order["code"]))
    check("/find находит заказ", any(order["code"] in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/stats"))
    check("/stats отвечает", any("статус" in t.lower() for t in bot.texts_to(SELLER)))

    print("\nУдаление данных")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/forget"))
    check("данные удалены", await db.get_user(BUYER) is None)
    check("заказы удалены каскадом", await db.get_active_order(BUYER) is None)

    await db.close()
    await bot.session.close()

    print("")
    if failures:
        print("ПРОВАЛЕНО: %d — %s" % (len(failures), ", ".join(failures)))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
