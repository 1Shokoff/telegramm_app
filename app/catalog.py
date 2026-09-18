from __future__ import annotations

# Каталог для витрины Mini App и списка в чате.
#   slug  — идентификатор, он же имя файла иконки: webapp/icons/<slug>.png
#   color — фон плитки, если картинки нет
#   dark  — True, если поверх цвета нужен чёрный текст (жёлтые/светлые плитки)
#
# Иконки не входят в репозиторий: положите свои PNG 128×128 в webapp/icons/
# и они подхватятся автоматически, без пересборки образа.

CATEGORIES = ("Банки", "Общение", "Нейросети", "Покупки", "Сервисы", "Медиа")

APPS = [
    {"slug": "sber", "name": "СберБанк", "category": "Банки", "color": "#21A038"},
    {"slug": "alfa", "name": "Альфа-Банк", "category": "Банки", "color": "#EF3124"},
    {"slug": "vk", "name": "ВКонтакте", "category": "Общение", "color": "#0077FF"},
    {"slug": "chatgpt", "name": "ChatGPT", "category": "Нейросети", "color": "#0D0D0D"},
    {"slug": "claude", "name": "Claude", "category": "Нейросети", "color": "#D97757"},
    {"slug": "avito", "name": "Авито", "category": "Покупки", "color": "#00AAFF"},
    {"slug": "tbank", "name": "Т-Банк", "category": "Банки", "color": "#FFDD2D", "dark": True},
    {"slug": "vtb", "name": "ВТБ", "category": "Банки", "color": "#0A2896"},
    {"slug": "gazprombank", "name": "Газпромбанк", "category": "Банки", "color": "#0079C8"},
    {"slug": "psb", "name": "ПСБ", "category": "Банки", "color": "#EE7203"},
    {"slug": "sovcombank", "name": "Совкомбанк", "category": "Банки", "color": "#E3001B"},
    {"slug": "raiffeisen", "name": "Райффайзен", "category": "Банки", "color": "#FEE600", "dark": True},
    {"slug": "telegram", "name": "Telegram", "category": "Общение", "color": "#2AABEE"},
    {"slug": "deepseek", "name": "DeepSeek", "category": "Нейросети", "color": "#4D6BFE"},
    {"slug": "perplexity", "name": "Perplexity", "category": "Нейросети", "color": "#20808D"},
    {"slug": "ozon", "name": "Ozon", "category": "Покупки", "color": "#005BFF"},
    {"slug": "wildberries", "name": "Wildberries", "category": "Покупки", "color": "#CB11AB"},
    {"slug": "yandex-market", "name": "Яндекс Маркет", "category": "Покупки", "color": "#FC3F1D"},
    {"slug": "gosuslugi", "name": "Госуслуги", "category": "Сервисы", "color": "#0D4CD3"},
    {"slug": "rzd", "name": "РЖД Пассажирам", "category": "Сервисы", "color": "#E21A1A"},
    {"slug": "2gis", "name": "2ГИС", "category": "Сервисы", "color": "#19AA1E"},
    {"slug": "yandex-go", "name": "Яндекс Go", "category": "Сервисы", "color": "#FFCC00", "dark": True},
    {"slug": "yandex-music", "name": "Яндекс Музыка", "category": "Медиа", "color": "#FFCC00", "dark": True},
    {"slug": "kinopoisk", "name": "Кинопоиск", "category": "Медиа", "color": "#FF5500"},
]

# Плитки в шапке витрины.
FEATURED = ("sber", "alfa", "vk", "chatgpt", "claude", "avito")


def _letter(name: str) -> str:
    return name[0].upper()


def public_catalog() -> list[dict]:
    return [
        {
            "slug": a["slug"],
            "name": a["name"],
            "category": a["category"],
            "color": a["color"],
            "dark": bool(a.get("dark")),
            "letter": _letter(a["name"]),
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
