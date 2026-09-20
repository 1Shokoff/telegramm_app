from __future__ import annotations

import asyncio
import logging
import os
import signal
import sqlite3

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    MenuButtonCommands,
    MenuButtonWebApp,
    WebAppInfo,
)
from aiohttp import web

from . import db
from .api import build_app
from .config import Config, load_config
from .handlers import buyer, payments, seller
from .service import OrderService

log = logging.getLogger("apps-bot")

BUYER_COMMANDS = [
    BotCommand(command="start", description="Витрина и новый заказ"),
    BotCommand(command="order", description="Мой заказ"),
    BotCommand(command="udid", description="Где найти UDID"),
    BotCommand(command="notify", description="Настройка уведомлений"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="forget", description="Удалить мои данные"),
]

SELLER_COMMANDS = BUYER_COMMANDS + [
    BotCommand(command="orders", description="Открытые заказы"),
    BotCommand(command="find", description="Найти заказ"),
    BotCommand(command="chat", description="Переписка по заказу"),
    BotCommand(command="reply", description="Ответить покупателю"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="sellerhelp", description="Команды продавца"),
]


async def setup_profile(bot: Bot, cfg: Config) -> None:
    await bot.set_my_commands(BUYER_COMMANDS, scope=BotCommandScopeDefault())
    for seller_id in cfg.seller_ids:
        try:
            await bot.set_my_commands(SELLER_COMMANDS, scope=BotCommandScopeChat(chat_id=seller_id))
        except Exception as exc:  # продавец ещё не писал боту
            log.warning("Не задали команды для продавца %s: %s", seller_id, exc)

    if cfg.webapp_enabled:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Каталог", web_app=WebAppInfo(url=cfg.webapp_url))
        )
    else:
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


def mask_proxy(url: str) -> str:
    """Прячет пароль прокси, чтобы он не утёк в логи."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":", 1)[0]
    return "%s://%s:***@%s" % (scheme, user, host) if user else "%s://***@%s" % (scheme, host)


def make_bot(cfg: Config) -> Bot:
    session = None
    if cfg.telegram_proxy:
        # Нужен aiohttp-socks — он в requirements.txt.
        session = AiohttpSession(proxy=cfg.telegram_proxy)
        log.info("Telegram через прокси %s", mask_proxy(cfg.telegram_proxy))
    return Bot(
        cfg.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def connect_telegram(bot: Bot, cfg: Config, attempts: int = 5):
    """get_me с повторами: короткий обрыв сети не должен ронять весь сервис.

    Ошибки прокси aiohttp-socks наследуются прямо от Exception и мимо
    TelegramNetworkError, поэтому ловим широко — на старте всё равно все
    варианты сводятся к «связи с Telegram нет».
    """
    delay = 3.0
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            return await bot.get_me()
        except TelegramUnauthorizedError:
            raise SystemExit(
                "Telegram отклонил токен: проверьте BOT_TOKEN в .env "
                "(взять заново: @BotFather → /mybots → API Token)."
            )
        except Exception as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
            log.warning("Нет связи с Telegram, попытка %d из %d — %s", attempt, attempts, last)
            if attempt < attempts:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)

    if cfg.telegram_proxy:
        hint = (
            "Прокси %s не пропустил запрос. Проверьте его с сервера:\n"
            "  curl -4 -m 15 -x %s -o /dev/null -w '%%{http_code}\\n' https://api.telegram.org/\n"
            "Вместо *** подставьте настоящий пароль. Любой код ответа (даже 404) "
            "означает, что прокси рабочий; 000 — нет."
            % (mask_proxy(cfg.telegram_proxy), mask_proxy(cfg.telegram_proxy))
        )
    else:
        hint = (
            "Проверьте с сервера: curl -4 -m 10 https://api.telegram.org/\n"
            "Если ответа нет, хостинг блокирует Telegram — пропишите прокси в .env:\n"
            "  TELEGRAM_PROXY=socks5://user:pass@host:port"
        )

    raise SystemExit("Не удалось связаться с api.telegram.org (%s).\n%s" % (last, hint))


def build_dispatcher(cfg: Config, service: OrderService) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Доступно во всех хэндлерах как аргументы cfg / service.
    dp["cfg"] = cfg
    dp["service"] = service
    dp.include_router(seller.setup(cfg))
    dp.include_router(payments.router)
    dp.include_router(buyer.router)
    return dp


async def run() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    try:
        os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)
        await db.init(cfg.db_path)
    except (sqlite3.OperationalError, OSError) as exc:
        raise SystemExit(
            "Не удалось открыть базу %s: %s\n"
            "Чаще всего это права на каталог: внутри контейнера процесс работает "
            "под пользователем app (uid 10001). Проверьте, что том с базой доступен "
            "ему на запись — в docker-compose.yml для этого используется именованный "
            "том bot_data, а не папка с хоста." % (cfg.db_path, exc)
        )

    bot = make_bot(cfg)
    service = OrderService(bot, cfg)
    dp = build_dispatcher(cfg, service)

    app = build_app(cfg, bot, service)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    log.info("HTTP слушает %s:%s, Mini App: %s", cfg.host, cfg.port, cfg.webapp_url or "выключен")

    me = await connect_telegram(bot, cfg)
    log.info("Бот @%s запущен, режим оплаты: %s", me.username, cfg.payment_mode)
    await setup_profile(bot, cfg)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    polling = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )
    waiter = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        log.info("Останавливаемся...")
        waiter.cancel()
        if not polling.done():
            await dp.stop_polling()
            await asyncio.gather(polling, return_exceptions=True)
        await runner.cleanup()
        await bot.session.close()
        await db.close()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
