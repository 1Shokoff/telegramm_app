from __future__ import annotations

from pathlib import Path

ICONS_DIR = Path(__file__).resolve().parent.parent / "webapp" / "icons"
# Порядок = приоритет: свой PNG перекроет сгенерированный SVG.
ICON_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg", ".svg")

# Каталог для витрины Mini App и списка в чате.
#   slug  — идентификатор, он же имя файла иконки: webapp/icons/<slug>.*
#   color — фон плитки и сгенерированной иконки
#   dark  — True, если поверх цвета нужен чёрный знак (жёлтые/светлые плитки)
#
# В webapp/icons/ лежат оригинальные иконки и фирменные значки.
# Источники сохранены в webapp/icons/sources.json. PNG имеет приоритет.

CATEGORIES = ("Банки", "Общение", "Медиа", "Нейросети", "Сервисы")

#   desc  — описание услуги по этому приложению: его видит покупатель
#           в витрине и проверяет модерация платёжной системы
#
# Версия, размер и минимальная iOS здесь не хранятся: их читает app/ipa.py
# прямо из файла сборки, иначе они разъехались бы с тем, что лежит на сервере.
APPS = [
    {"slug": "sber", "name": "СберБанк", "category": "Банки", "color": "#21A038",
     "desc": "Счета и карты, переводы по номеру телефона, оплата услуг и QR-кодов."},
    {"slug": "tbank", "name": "Т-Банк", "category": "Банки", "color": "#FFDD2D", "dark": True,
     "desc": "Карты и счета, переводы, инвестиции и оплата услуг."},
    {"slug": "ozon-bank", "name": "Ozon Банк", "category": "Банки", "color": "#0055FF",
     "desc": "Карта и счёт Ozon, переводы, оплата покупок и кэшбэк."},
    {"slug": "mts-money", "name": "МТС Деньги", "category": "Банки", "color": "#7B61FF",
     "desc": "Карты и счета МТС, переводы и платежи. "
             "На экране телефона приложение называется «Пять монет»."},
    {"slug": "max", "name": "MAX", "category": "Общение", "color": "#7A5CFF",
     "desc": "Мессенджер MAX: переписка, звонки, каналы и чат-боты."},
    {"slug": "vk-messenger", "name": "VK Мессенджер", "category": "Общение", "color": "#0077FF",
     "desc": "Переписка и звонки ВКонтакте — без ленты и новостей."},
    {"slug": "vk-video", "name": "VK Видео", "category": "Медиа", "color": "#FF2244",
     "desc": "Ролики, трансляции и подписки ВКонтакте."},
    {"slug": "yandex-music", "name": "Яндекс Музыка", "category": "Медиа", "color": "#111111",
     "desc": "Музыка и подкасты, в том числе без интернета."},
    {"slug": "spotify", "name": "Spotify", "category": "Медиа", "color": "#1DB954",
     "desc": "Музыка и подкасты, плейлисты и офлайн-режим."},
    {"slug": "aldente", "name": "Al dente", "category": "Нейросети", "color": "#121212",
     "desc": "Чат с нейросетью: ответы на вопросы, тексты и перевод. Сторонняя сборка."},
    {"slug": "yandex", "name": "Яндекс", "category": "Сервисы", "color": "#FF3311",
     "desc": "Поиск, Алиса, погода и новости в одном приложении."},
    {"slug": "yandex-go", "name": "Яндекс Go", "category": "Сервисы", "color": "#FFEE00", "dark": True,
     "desc": "Такси, доставка и каршеринг в одном приложении."},
    {"slug": "cloud-mail", "name": "Облако Mail.ru", "category": "Сервисы", "color": "#0077FF",
     "desc": "Хранилище для фото и документов с автозагрузкой с телефона."},
    {"slug": "yota", "name": "Yota", "category": "Сервисы", "color": "#2BB2E5",
     "desc": "Личный кабинет Yota: баланс, тариф и управление номером."},
    {"slug": "v2guard", "name": "V2Guard", "category": "Сервисы", "color": "#0B0B0B",
     "desc": "Клиент V2Ray: подключение к своему серверу по своим настройкам."},
]

# Плитки в шапке витрины.
FEATURED = ("sber", "tbank", "max", "yandex", "spotify", "vk-video")


def _letter(name: str) -> str:
    return name[0].upper()


CATALOG_NAME = "Весь каталог"


# Как покупатели называют банки в реквизитах — приводим к имени иконки.
# Значки банков лежат в webapp/icons/ независимо от каталога: получать
# перевод можно на банк, приложения которого мы не продаём.
BANK_ALIASES = {
    "тинькофф": "tbank",
    "тинькоф": "tbank",
    "сбер": "sber",
    "альфа": "alfa",
    "райф": "raiffeisen",
    "промсвязьбанк": "psb",
    "газпром": "gazprombank",
    "совком": "sovcombank",
    "озон": "ozon-bank",
    "мтс": "mts-money",
}


def _bank_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def bank_icon(name: str) -> str:
    """Слаг иконки банка по его названию в реквизитах; пусто — иконки нет."""
    key = _bank_key(name)
    if not key:
        return ""
    for alias, slug in BANK_ALIASES.items():
        if alias in key:
            return slug
    for app in APPS:
        if app["category"] == "Банки" and _bank_key(app["name"]) == key:
            return app["slug"]
    return ""


def get_app(slug: str | None) -> dict | None:
    if not slug:
        return None
    return next((a for a in APPS if a["slug"] == slug), None)


def app_price(app: dict | None, default: int) -> int:
    """Своя цена приложения — ключ price в APPS; без него действует общая."""
    if app and app.get("price"):
        return int(app["price"])
    return default


def product_name(slug: str | None) -> str:
    app = get_app(slug)
    return app["name"] if app else CATALOG_NAME


def icon_file(slug: str) -> str:
    """Имя файла картинки для слага — любого, не только из каталога.

    Значки банков нужны странице оплаты даже тогда, когда приложений
    этого банка в каталоге нет.
    """
    if not slug or not ICONS_DIR.is_dir():
        return ""
    for ext in ICON_EXTENSIONS:
        if (ICONS_DIR / (slug + ext)).is_file():
            return slug + ext
    return ""


def icon_files() -> dict[str, str]:
    """Какая картинка лежит для каждого приложения каталога."""
    found: dict[str, str] = {}
    for app in APPS:
        name = icon_file(app["slug"])
        if name:
            found[app["slug"]] = name
    return found


def public_catalog(default_price: int) -> list[dict]:
    icons = icon_files()
    return [
        {
            "slug": a["slug"],
            "name": a["name"],
            "category": a["category"],
            "color": a["color"],
            "dark": bool(a.get("dark")),
            "letter": _letter(a["name"]),
            "icon": icons.get(a["slug"]),
            "desc": a.get("desc", ""),
            "price": app_price(a, default_price),
        }
        for a in APPS
    ]


def as_text(links: dict[str, str] | None = None) -> str:
    """Каталог для чата бота — по категориям.

    links: слаг → адрес сборки. Для таких приложений название становится
    ссылкой на скачивание, остальные идут обычным текстом.
    """
    links = links or {}
    lines: list[str] = []
    for cat in CATEGORIES:
        names = [
            '<a href="%s">%s</a>' % (links[a["slug"]], a["name"])
            if a["slug"] in links else a["name"]
            for a in APPS if a["category"] == cat
        ]
        if names:
            lines.append(f"<b>{cat}</b>\n" + " · ".join(names))
    return "\n\n".join(lines)


def count() -> int:
    return len(APPS)
