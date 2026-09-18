from __future__ import annotations

import asyncio
import logging
import os
import signal
import sqlite3

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
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
    BotCommand(command="imei", description="Где найти IMEI"),
    BotCommand(command="help", description="Помощь"),
    BotCommand(command="forget", description="Удалить мои данные"),
]

SELLER_COMMANDS = BUYER_COMMANDS + [
    BotCommand(command="orders", description="Открытые заказы"),
    BotCommand(command="find", description="Найти заказ"),
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

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    service = OrderService(bot, cfg)
    dp = build_dispatcher(cfg, service)

    app = build_app(cfg, bot, service)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    log.info("HTTP слушает %s:%s, Mini App: %s", cfg.host, cfg.port, cfg.webapp_url or "выключен")

    me = await bot.get_me()
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
