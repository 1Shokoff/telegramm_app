from __future__ import annotations

import os
from dataclasses import dataclass

PAYMENT_MODES = ("manual", "demo", "stars")


def _s(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _i(name: str, default: int) -> int:
    raw = _s(name)
    try:
        return int(raw)
    except ValueError:
        return default


def _b(name: str, default: bool = False) -> bool:
    raw = _s(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "да"}


def _multiline(name: str) -> str:
    """В .env перенос строки задаётся как \\n — разворачиваем его в настоящий."""
    return _s(name).replace("\\n", "\n")


def _ids(name: str) -> tuple[int, ...]:
    out: list[int] = []
    for chunk in _s(name).replace(";", ",").split(","):
        chunk = chunk.strip().lstrip("@")
        if chunk.lstrip("-").isdigit():
            out.append(int(chunk))
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class Config:
    bot_token: str
    seller_ids: tuple[int, ...]
    support_username: str
    webapp_url: str
    price_rub: int
    price_stars: int
    product_title: str
    product_subtitle: str
    payment_mode: str
    payment_details: str
    instruction_template: str
    order_prefix: str
    db_path: str
    host: str
    port: int
    init_data_ttl: int
    dev_mode: bool
    log_level: str

    @property
    def webapp_enabled(self) -> bool:
        return self.webapp_url.startswith("https://")

    def is_seller(self, tg_id: int | None) -> bool:
        return tg_id is not None and tg_id in self.seller_ids


def load_config() -> Config:
    token = _s("BOT_TOKEN")
    if not token or ":" not in token:
        raise SystemExit("BOT_TOKEN не задан или выглядит некорректно. Заполните .env")

    sellers = _ids("SELLER_IDS")
    if not sellers:
        raise SystemExit("SELLER_IDS не задан: без него некому подтверждать заказы")

    mode = _s("PAYMENT_MODE", "manual").lower()
    if mode not in PAYMENT_MODES:
        raise SystemExit(f"PAYMENT_MODE={mode!r}: допустимо {', '.join(PAYMENT_MODES)}")

    stars = _i("PRICE_STARS", 0)
    if mode == "stars" and stars <= 0:
        raise SystemExit("PAYMENT_MODE=stars требует PRICE_STARS > 0")

    return Config(
        bot_token=token,
        seller_ids=sellers,
        support_username=_s("SUPPORT_USERNAME").lstrip("@"),
        webapp_url=_s("WEBAPP_URL").rstrip("/"),
        price_rub=_i("PRICE_RUB", 3000),
        price_stars=stars,
        product_title=_s("PRODUCT_TITLE", "Доступ для iPhone"),
        product_subtitle=_s("PRODUCT_SUBTITLE", "Весь каталог приложений"),
        payment_mode=mode,
        payment_details=_multiline("PAYMENT_DETAILS"),
        instruction_template=_multiline("INSTRUCTION_TEMPLATE"),
        order_prefix=_s("ORDER_PREFIX", "NP"),
        db_path=_s("DB_PATH", "/data/bot.sqlite3"),
        host=_s("HOST", "0.0.0.0"),
        port=_i("PORT", 8080),
        init_data_ttl=_i("INIT_DATA_TTL", 86400),
        dev_mode=_b("DEV_MODE", False),
        log_level=_s("LOG_LEVEL", "INFO").upper(),
    )
