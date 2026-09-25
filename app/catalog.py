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

CATEGORIES = ("Банки", "Общение", "Нейросети", "Покупки", "Сервисы", "Медиа")

#   desc  — описание услуги по этому приложению: его видит покупатель
#           в витрине и проверяет модерация платёжной системы
APPS = [
    {"slug": "sber", "name": "СберБанк", "category": "Банки", "color": "#21A038",
     "desc": "Счета и карты, переводы по номеру телефона, оплата услуг и QR-кодов."},
    {"slug": "alfa", "name": "Альфа-Банк", "category": "Банки", "color": "#EF3124",
     "desc": "Карты и счета, переводы, платежи и кэшбэк."},
    {"slug": "vk", "name": "ВКонтакте", "category": "Общение", "color": "#0077FF",
     "desc": "Лента, сообщения, сообщества, музыка и клипы."},
    {"slug": "chatgpt", "name": "ChatGPT", "category": "Нейросети", "color": "#10A37F",
     "desc": "Ответы на вопросы, тексты, перевод и разбор фотографий."},
    {"slug": "claude", "name": "Claude", "category": "Нейросети", "color": "#D97757",
     "desc": "Помощник для длинных текстов, документов и кода."},
    {"slug": "avito", "name": "Авито", "category": "Покупки", "color": "#00AAFF",
     "desc": "Объявления рядом с вами, переписка с продавцами и доставка."},
    {"slug": "tbank", "name": "Т-Банк", "category": "Банки", "color": "#FFDD2D", "dark": True,
     "desc": "Карты и счета, переводы, инвестиции и платежи."},
    {"slug": "vtb", "name": "ВТБ", "category": "Банки", "color": "#0A2896",
     "desc": "Карты, вклады, переводы и оплата услуг."},
    {"slug": "gazprombank", "name": "Газпромбанк", "category": "Банки", "color": "#0079C8",
     "desc": "Счета и карты, переводы и платежи."},
    {"slug": "psb", "name": "ПСБ", "category": "Банки", "color": "#EE7203",
     "desc": "Карты и счета, переводы, платежи и вклады."},
    {"slug": "sovcombank", "name": "Совкомбанк", "category": "Банки", "color": "#E3001B",
     "desc": "Карты рассрочки, переводы и оплата услуг."},
    {"slug": "raiffeisen", "name": "Райффайзен", "category": "Банки", "color": "#FEE600", "dark": True,
     "desc": "Счета и карты, переводы и платежи."},
    {"slug": "telegram", "name": "Telegram", "category": "Общение", "color": "#2AABEE",
     "desc": "Мессенджер: чаты, каналы, звонки и файлы."},
    {"slug": "deepseek", "name": "DeepSeek", "category": "Нейросети", "color": "#4D6BFE",
     "desc": "Нейросеть для вопросов, текстов и кода."},
    {"slug": "perplexity", "name": "Perplexity", "category": "Нейросети", "color": "#20808D",
     "desc": "Поиск с ответами нейросети и ссылками на источники."},
    {"slug": "ozon", "name": "Ozon", "category": "Покупки", "color": "#005BFF",
     "desc": "Заказы, отслеживание доставки и пункты выдачи."},
    {"slug": "wildberries", "name": "Wildberries", "category": "Покупки", "color": "#CB11AB",
     "desc": "Покупки, примерка, возвраты и пункты выдачи."},
    {"slug": "yandex-market", "name": "Яндекс Маркет", "category": "Покупки", "color": "#FC3F1D",
     "desc": "Заказы и доставка, сравнение цен и отзывы."},
    {"slug": "gosuslugi", "name": "Госуслуги", "category": "Сервисы", "color": "#0D4CD3",
     "desc": "Документы, выплаты, штрафы и запись в ведомства."},
    {"slug": "rzd", "name": "РЖД Пассажирам", "category": "Сервисы", "color": "#E21A1A",
     "desc": "Билеты на поезда, расписание и электронная регистрация."},
    {"slug": "2gis", "name": "2ГИС", "category": "Сервисы", "color": "#19AA1E",
     "desc": "Карты и справочник организаций, работают без интернета."},
    {"slug": "yandex-go", "name": "Яндекс Go", "category": "Сервисы", "color": "#FFCC00", "dark": True,
     "desc": "Такси, доставка и каршеринг в одном приложении."},
    {"slug": "yandex-music", "name": "Яндекс Музыка", "category": "Медиа", "color": "#FFCC00", "dark": True,
     "desc": "Музыка и подкасты, в том числе без интернета."},
    {"slug": "kinopoisk", "name": "Кинопоиск", "category": "Медиа", "color": "#FF5500",
     "desc": "Фильмы и сериалы, подборки, оценки и расписание кино."},
]

# Плитки в шапке витрины.
FEATURED = ("sber", "alfa", "vk", "chatgpt", "claude", "avito")


def _letter(name: str) -> str:
    return name[0].upper()


CATALOG_NAME = "Весь каталог"


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


def icon_files() -> dict[str, str]:
    """Какая картинка лежит для каждого слага. Файл можно добавить на лету."""
    found: dict[str, str] = {}
    if not ICONS_DIR.is_dir():
        return found
    names = {p.name for p in ICONS_DIR.iterdir() if p.is_file()}
    for app in APPS:
        for ext in ICON_EXTENSIONS:
            candidate = app["slug"] + ext
            if candidate in names:
                found[app["slug"]] = candidate
                break
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


def as_text() -> str:
    """Каталог для чата бота — сгруппирован по категориям."""
    lines: list[str] = []
    for cat in CATEGORIES:
        names = [a["name"] for a in APPS if a["category"] == cat]
        if names:
            lines.append(f"<b>{cat}</b>\n" + " · ".join(names))
    return "\n\n".join(lines)


def count() -> int:
    return len(APPS)
