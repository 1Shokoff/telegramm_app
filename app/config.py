from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import catalog, const

PAYMENT_MODES = ("manual", "demo", "stars")

ROOT = Path(__file__).resolve().parent.parent

# Сроки акции считаем по Москве. Перехода на летнее время там нет с 2014 года,
# поэтому хватает постоянного смещения — без зависимости от tzdata в образе.
MSK = timezone(timedelta(hours=3))

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def today_msk() -> date:
    return datetime.now(MSK).date()


def load_env_file(name: str = ".env") -> None:
    """Запуск без Docker: подхватываем .env из корня проекта.

    Уже заданные переменные окружения приоритетнее файла — так же,
    как это делает docker compose.
    """
    path = ROOT / name
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


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


def _date(name: str) -> date | None:
    """Дата из .env: 30.11.2026 или 2026-11-30. Пусто — None, опечатка — остановка с объяснением."""
    raw = _s(name)
    if not raw:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise SystemExit(f"{name}={raw!r}: дата пишется как 30.11.2026")


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
    supplier_ids: tuple[int, ...]
    # Аккаунты команды, которые проверяют бота: их заказы — тестовые.
    test_ids: tuple[int, ...]
    support_username: str
    webapp_url: str
    # Обычная цена. Пока идёт акция, покупатель платит promo_price_rub.
    price_rub: int
    promo_price_rub: int
    promo_until: date | None
    promo_label: str
    price_stars: int
    product_title: str
    product_subtitle: str
    payment_mode: str
    payment_details: str
    pay_phone: str
    pay_bank: str
    pay_bank_icon: str
    pay_card: str
    pay_name: str
    pay_amount: str
    telegram_proxy: str
    bot_username: str
    legal_name: str
    legal_inn: str
    legal_address: str
    contact_email: str
    contact_phone: str
    doc_urls: tuple[tuple[str, str], ...]
    order_prefix: str
    db_path: str
    ipa_dir: Path
    host: str
    port: int
    init_data_ttl: int
    dev_mode: bool
    log_level: str

    @property
    def webapp_enabled(self) -> bool:
        return self.webapp_url.startswith("https://")

    @property
    def udid_guide_url(self) -> str:
        """Картинка-инструкция лежит в статике Mini App; без домена её нет."""
        if not self.webapp_enabled:
            return ""
        return self.webapp_url + "/static/img/udid-guide.webp"

    def is_seller(self, tg_id: int | None) -> bool:
        return tg_id is not None and tg_id in self.seller_ids

    def is_supplier(self, tg_id: int | None) -> bool:
        return tg_id is not None and tg_id in self.supplier_ids

    def is_tester(self, tg_id: int | None) -> bool:
        return tg_id is not None and tg_id in self.test_ids

    def role_of(self, tg_id: int | None) -> str:
        """Роль аккаунта. Продавец сильнее поставщика: его права включают чужие."""
        if self.is_seller(tg_id):
            return const.ROLE_SELLER
        if self.is_supplier(tg_id):
            return const.ROLE_SUPPLIER
        return const.ROLE_BUYER

    def promo_active(self, today: date | None = None) -> bool:
        """Акция идёт: цена по акции ниже обычной и последний день ещё не прошёл (по Москве)."""
        if not (0 < self.promo_price_rub < self.price_rub):
            return False
        if self.promo_until is None:
            return True
        return (today or today_msk()) <= self.promo_until

    @property
    def current_price(self) -> int:
        """Цена, которую покупатель платит сегодня: по акции, пока она идёт, потом — обычная."""
        return self.promo_price_rub if self.promo_active() else self.price_rub

    @property
    def promo_percent(self) -> int:
        return round((self.price_rub - self.promo_price_rub) * 100 / self.price_rub) if self.price_rub else 0

    @property
    def promo_title(self) -> str:
        """Подпись скидки: PROMO_LABEL, а без неё — «Скидка 50%»."""
        return self.promo_label or "Скидка %d%%" % self.promo_percent

    @property
    def promo_until_text(self) -> str:
        """«до 30 ноября» — или пусто, если срок не задан."""
        if self.promo_until is None:
            return ""
        return "до %d %s" % (self.promo_until.day, MONTHS_GENITIVE[self.promo_until.month - 1])

    def amount_text(self, price_rub: int) -> str:
        """Сумма к переводу: значение из .env важнее цены заказа."""
        if self.pay_amount:
            return self.pay_amount
        return "{:,} ₽".format(price_rub).replace(",", " ")

    @property
    def requisites(self) -> tuple[dict, ...]:
        """Реквизиты перевода строками: подпись, значение, иконка, копировать ли.

        Порядок такой же, как в витрине и в сообщении бота, чтобы покупатель
        видел одно и то же в обоих местах.
        """
        rows: list[dict] = []
        if self.pay_phone:
            rows.append({
                "key": "phone", "label": "Телефон для СБП",
                "value": self.pay_phone, "icon": "sbp", "copy": True,
            })
        if self.pay_bank:
            rows.append({
                "key": "bank", "label": "Банк получателя",
                "value": self.pay_bank,
                "icon": self.pay_bank_icon or catalog.bank_icon(self.pay_bank),
                "copy": False,
            })
        if self.pay_name:
            rows.append({
                "key": "name", "label": "Получатель",
                "value": self.pay_name, "icon": "", "copy": True,
            })
        if self.pay_card:
            rows.append({
                "key": "card", "label": "Номер карты",
                "value": self.pay_card, "icon": "", "copy": True,
            })
        return tuple(rows)

    def doc_path(self, key: str) -> str:
        """Адрес документа внутри витрины: свой текст или ссылка из .env."""
        override = dict(self.doc_urls).get(key, "")
        if override:
            return override
        if (ROOT / "webapp" / "legal" / ("%s.txt" % key)).is_file():
            return "/docs/" + key
        return ""

    def doc_url(self, key: str) -> str:
        """Та же ссылка, но абсолютная — для сообщений бота."""
        path = self.doc_path(key)
        if not path or path.startswith("http"):
            return path
        return (self.webapp_url + path) if self.webapp_enabled else ""

    @property
    def legal_line(self) -> str:
        """Строка с реквизитами продавца — она же подвал витрины."""
        parts = [self.legal_name]
        if self.legal_inn:
            parts.append("ИНН " + self.legal_inn)
        if self.legal_address:
            parts.append(self.legal_address)
        return " · ".join(p for p in parts if p)


def load_config() -> Config:
    load_env_file()

    token = _s("BOT_TOKEN")
    if not token or ":" not in token:
        raise SystemExit("BOT_TOKEN не задан или выглядит некорректно. Заполните .env")

    sellers = _ids("SELLER_IDS")
    if not sellers:
        raise SystemExit("SELLER_IDS не задан: без него некому подтверждать заказы")

    # Поставщиков может не быть вовсе — роль необязательная.
    suppliers = _ids("SUPPLIER_IDS")

    mode = _s("PAYMENT_MODE", "manual").lower()
    if mode not in PAYMENT_MODES:
        raise SystemExit(f"PAYMENT_MODE={mode!r}: допустимо {', '.join(PAYMENT_MODES)}")

    stars = _i("PRICE_STARS", 0)
    if mode == "stars" and stars <= 0:
        raise SystemExit("PAYMENT_MODE=stars требует PRICE_STARS > 0")

    price = _i("PRICE_RUB", 3000)
    promo = _i("PROMO_PRICE_RUB", 0)
    if promo and promo >= price:
        raise SystemExit(
            f"PROMO_PRICE_RUB={promo} не ниже PRICE_RUB={price}: в PRICE_RUB — обычная цена, "
            "в PROMO_PRICE_RUB — цена по акции"
        )

    proxy = _s("TELEGRAM_PROXY")
    if proxy and "://" not in proxy:
        raise SystemExit(
            "TELEGRAM_PROXY должен начинаться со схемы: "
            "socks5://host:port, http://host:port или с логином socks5://user:pass@host:port"
        )

    return Config(
        bot_token=token,
        seller_ids=sellers,
        supplier_ids=suppliers,
        test_ids=_ids("TEST_IDS"),
        support_username=_s("SUPPORT_USERNAME").lstrip("@"),
        webapp_url=_s("WEBAPP_URL").rstrip("/"),
        price_rub=price,
        promo_price_rub=promo,
        promo_until=_date("PROMO_UNTIL"),
        promo_label=_s("PROMO_LABEL"),
        price_stars=stars,
        product_title=_s("PRODUCT_TITLE", "Доступ для iPhone"),
        product_subtitle=_s("PRODUCT_SUBTITLE", "Весь каталог приложений"),
        payment_mode=mode,
        payment_details=_multiline("PAYMENT_DETAILS"),
        pay_phone=_s("PAY_PHONE"),
        pay_bank=_s("PAY_BANK"),
        pay_bank_icon=_s("PAY_BANK_ICON"),
        pay_card=_s("PAY_CARD"),
        pay_name=_s("PAY_NAME"),
        pay_amount=_s("PAY_AMOUNT"),
        telegram_proxy=proxy,
        bot_username=_s("BOT_USERNAME").lstrip("@"),
        legal_name=_s("LEGAL_NAME"),
        legal_inn=_s("LEGAL_INN"),
        legal_address=_s("LEGAL_ADDRESS"),
        contact_email=_s("CONTACT_EMAIL"),
        contact_phone=_s("CONTACT_PHONE"),
        doc_urls=tuple(
            (key, _s(key.upper() + "_URL"))
            for key, _title, _hint in const.DOCUMENTS
        ),
        order_prefix=_s("ORDER_PREFIX", "NP"),
        db_path=_s("DB_PATH", "/data/bot.sqlite3"),
        ipa_dir=Path(_s("IPA_DIR") or (ROOT / "files")),
        host=_s("HOST", "0.0.0.0"),
        port=_i("PORT", 8080),
        init_data_ttl=_i("INIT_DATA_TTL", 86400),
        dev_mode=_b("DEV_MODE", False),
        log_level=_s("LOG_LEVEL", "INFO").upper(),
    )
