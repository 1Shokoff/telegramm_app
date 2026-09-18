"""Локальный предпросмотр Mini App без Telegram и без токена.

    python tools/preview.py          → http://127.0.0.1:8099

Поднимается только веб-часть: оплата в режиме demo, вход по DEV_MODE,
сообщения «в Telegram» печатаются в консоль. Удобно править webapp/ и обновлять страницу.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "123456:PREVIEWPREVIEWPREVIEWPREVIEWPREVIEW")
os.environ.setdefault("SELLER_IDS", "1")
os.environ.setdefault("WEBAPP_URL", "https://preview.local")
os.environ.setdefault("PAYMENT_MODE", "demo")
os.environ.setdefault("DEV_MODE", "1")
os.environ.setdefault("DB_PATH", os.path.join(os.path.dirname(__file__), "preview.sqlite3"))
os.environ.setdefault("LOG_LEVEL", "WARNING")

from aiohttp import web  # noqa: E402

from app import db  # noqa: E402
from app.api import build_app  # noqa: E402
from app.config import load_config  # noqa: E402
from app.service import OrderService  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.getenv("PREVIEW_PORT", "8099"))


class ConsoleBot:
    async def send_message(self, chat_id, text, reply_markup=None):
        print("\n[в Telegram → %s]\n%s\n" % (chat_id, text))


async def main() -> None:
    cfg = load_config()
    await db.init(cfg.db_path)
    app = build_app(cfg, ConsoleBot(), OrderService(ConsoleBot(), cfg))

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    print("Предпросмотр: http://%s:%d  (Ctrl+C — выход)" % (HOST, PORT))
    print("Режим оплаты: demo · вход как пользователь DEV_MODE (продавец тоже он)")
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
