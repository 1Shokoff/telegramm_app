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
        "LEGAL_NAME": "Самозанятый Тестов Т. Т.",
        "LEGAL_INN": "123456789012",
        "CONTACT_EMAIL": "help@example.com",
        "LOG_LEVEL": "WARNING",
    }
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import (  # noqa: E402
    AnswerCallbackQuery,
    EditMessageText,
    SendDocument,
    SendMessage,
    SendPhoto,
)
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    Document,
    FSInputFile,
    Message,
    PhotoSize,
    Update,
    User,
)

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
        self.reject_photos = False

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if isinstance(method, SendDocument):
            return make_message(chat_id=method.chat_id, text=method.caption or "", from_bot=True)
        if isinstance(method, SendPhoto):
            if self.reject_photos:
                raise TelegramBadRequest(method=method, message="Bad Request: wrong file")
            return make_message(
                chat_id=method.chat_id,
                text=method.caption or "",
                from_bot=True,
                photo=[PhotoSize(file_id="photo-file-id", file_unique_id="u1", width=1254, height=1254)],
            )
        if isinstance(method, (SendMessage, EditMessageText)):
            return make_message(
                chat_id=getattr(method, "chat_id", BUYER),
                text=getattr(method, "text", ""),
                from_bot=True,
            )
        return True

    def texts_to(self, chat_id: int) -> list[str]:
        """Тексты сообщений и подписи к картинкам."""
        out = []
        for m in self.calls:
            if getattr(m, "chat_id", None) != chat_id:
                continue
            if isinstance(m, (SendMessage, EditMessageText)):
                out.append(m.text)
            elif isinstance(m, SendPhoto):
                out.append(m.caption or "")
        return out

    def photos_to(self, chat_id: int) -> list[SendPhoto]:
        return [m for m in self.calls if isinstance(m, SendPhoto) and m.chat_id == chat_id]

    def reset(self) -> None:
        self.calls.clear()


def make_message(chat_id: int, text: str, from_bot: bool = False, photo: list | None = None) -> Message:
    counter["id"] += 1
    if photo:
        return Message(
            message_id=counter["id"],
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="private"),
            from_user=User(id=0, is_bot=True, first_name="Бот"),
            photo=photo,
            caption=text,
        )
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


def file_update(chat_id: int, *, photo: bool, name: str = "") -> Update:
    """Покупатель прислал чек: картинкой или файлом."""
    counter["id"] += 1
    common = dict(
        message_id=counter["id"],
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        from_user=User(id=chat_id, is_bot=False, first_name="Тест", username="user%d" % chat_id),
    )
    if photo:
        message = Message(
            photo=[PhotoSize(file_id="receipt-photo", file_unique_id="r1", width=1280, height=720)],
            **common,
        )
    else:
        message = Message(
            document=Document(file_id="receipt-doc", file_unique_id="r2", file_name=name),
            **common,
        )
    counter["id"] += 1
    return Update(update_id=counter["id"], message=message)


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
    check("/start отвечает витриной", any("iApki" in t for t in bot.texts_to(BUYER)))
    photos = bot.photos_to(BUYER)
    check(
        "приветствие — картинка с подписью и кнопками",
        len(photos) == 1 and isinstance(photos[0].photo, FSInputFile) and photos[0].reply_markup is not None,
    )
    check("приветствие не дублируется текстом", not any(isinstance(m, SendMessage) for m in bot.calls))

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/start"))
    photos = bot.photos_to(BUYER)
    check("повторный /start шлёт картинку по file_id", len(photos) == 1 and photos[0].photo == "photo-file-id")

    bot.reset()
    bot.reject_photos = True
    await dp.feed_update(bot, message_update(BUYER, "/start"))
    bot.reject_photos = False
    check(
        "картинку не приняли — приветствие уходит текстом",
        any(isinstance(m, SendMessage) and "iApki" in m.text for m in bot.calls),
    )

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/about"))
    about = "\n".join(bot.texts_to(BUYER))
    check("/about показывает реквизиты", "ИНН 123456789012" in about, about[:120])
    check("/about показывает контакты", "help@example.com" in about)
    check("/about перечисляет документы",
          "Публичная оферта" in about and "/docs/offer" in about, about[-200:])

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/help"))
    check(
        "из помощи есть переход в «О нас»",
        any(
            isinstance(m, SendMessage) and m.reply_markup
            and any("nav:about" == b.callback_data for row in m.reply_markup.inline_keyboard for b in row)
            for m in bot.calls
        ),
    )

    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "nav:buy_confirm"))
    order = await db.get_active_order(BUYER)
    check("заказ создан кнопкой", order is not None and order["status"] == const.NEW)
    payment_text = "\n".join(bot.texts_to(BUYER))
    check("реквизиты показаны", "Оплата заказа" in payment_text)
    check("сумма к оплате в реквизитах", "Сумма к оплате" in payment_text, payment_text[:160])
    check("продавцу пришло уведомление", any("Новый заказ" in t for t in bot.texts_to(SELLER)))

    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:claim:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("«Я оплатил» меняет статус", order["status"] == const.PAYMENT_CHECK)
    check("продавец видит заявку", any("заявил оплату" in t for t in bot.texts_to(SELLER)))

    print("\nПродавец")
    bot.reset()
    await dp.feed_update(bot, file_update(BUYER, photo=True))
    check(
        "чек картинкой ушёл продавцу",
        any(
            isinstance(m, SendPhoto) and m.chat_id == SELLER and "Чек по заказу" in (m.caption or "")
            for m in bot.calls
        ),
    )
    check("покупателю подтвердили чек", any("отправлен продавцу" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, file_update(BUYER, photo=False, name="chek.pdf"))
    check(
        "чек файлом ушёл продавцу",
        any(
            isinstance(m, SendDocument) and m.chat_id == SELLER and "chek.pdf" in (m.caption or "")
            for m in bot.calls
        ),
    )

    bot.reset()
    await dp.feed_update(bot, callback_update(BUYER, "o:payok:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("покупатель не подтверждает оплату сам", order["status"] == const.PAYMENT_CHECK)

    await dp.feed_update(bot, callback_update(SELLER, "o:payok:%d" % order["id"]))
    order = await db.get_order(order["id"])
    check("продавец подтвердил оплату", order["status"] == const.PAID)
    check("у покупателя запросили UDID", any("UDID" in t for t in bot.texts_to(BUYER)))

    print("\nUDID текстом в чат")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "12345"))
    order = await db.get_order(order["id"])
    check("короткий текст не принят за номер", order["device_udid"] is None)
    check("есть напоминание про 40 символов", any("40" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "2b6f0cc904d137be2e17 30235f5664094b831186"))
    order = await db.get_order(order["id"])
    check("UDID принят", order["device_udid"] == "2b6f0cc904d137be2e1730235f5664094b831186"
          and order["status"] == const.UDID)
    check("продавец получил UDID", any("Получен UDID" in t for t in bot.texts_to(SELLER)))

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

    print("\nПереписка")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "А когда примерно будет готово?"))
    check("вопрос ушёл продавцу", any("когда примерно" in t for t in bot.texts_to(SELLER)))
    check("покупателю подтверждение", any("Отправлено продавцу" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, callback_update(SELLER, "o:chat:%d" % order["id"]))
    await dp.feed_update(bot, message_update(SELLER, "Сегодня вечером будет готово"))
    check("ответ продавца дошёл", any("Сегодня вечером" in t for t in bot.texts_to(BUYER)))

    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/chat %s" % order["code"]))
    check("/chat показывает переписку",
          any("когда примерно" in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/reply %s Ответ командой" % order["code"]))
    check("/reply доставлен", any("Ответ командой" in t for t in bot.texts_to(BUYER)))

    print("\nКоманды продавца")
    bot.reset()
    await dp.feed_update(bot, message_update(SELLER, "/orders all"))
    check("/orders работает", any("Заказы" in t or "заказы" in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/find %s" % order["code"]))
    check("/find находит заказ", any(order["code"] in t for t in bot.texts_to(SELLER)))
    await dp.feed_update(bot, message_update(SELLER, "/stats"))
    check("/stats отвечает", any("статус" in t.lower() for t in bot.texts_to(SELLER)))

    print("\nУведомления")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/notify"))
    check("/notify показывает настройки",
          any("Уведомления" in t for t in bot.texts_to(BUYER)))
    await dp.feed_update(bot, callback_update(BUYER, "nt:%s" % const.NOTIFY_STATUS))
    prefs = await db.get_notify_prefs(BUYER)
    check("кнопка выключает вид", prefs.get(const.NOTIFY_STATUS) is False)
    await dp.feed_update(bot, callback_update(BUYER, "nt:%s" % const.NOTIFY_STATUS))
    prefs = await db.get_notify_prefs(BUYER)
    check("повторное нажатие включает обратно", prefs.get(const.NOTIFY_STATUS) is True)

    print("\nУдаление данных")
    bot.reset()
    await dp.feed_update(bot, message_update(BUYER, "/forget"))
    check("данные удалены", await db.get_user(BUYER) is None)
    check("заказы удалены каскадом", await db.get_active_order(BUYER) is None)
    check("настройки уведомлений удалены", await db.get_notify_prefs(BUYER) == {})

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
