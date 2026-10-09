"""Каталог витрины: товары собираются из файлов .ipa, уточнения — в catalog.toml.

Товар — это сборка <слаг>-<версия>.ipa в папке IPA_DIR. Название, версия,
минимальная iOS и иконка берутся из самой сборки. Файл catalog.toml рядом со
сборками может поправить любое поле: раздел [слаг] действует на все версии
приложения, раздел ["файл.ipa"] — только на один файл.

Положили сборку — товар появился, убрали — пропал. Папка и catalog.toml
перечитываются на каждом запросе, перезапуск не нужен.
"""
from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from html import escape
from pathlib import Path

from . import ipa

log = logging.getLogger(__name__)

ICONS_DIR = Path(__file__).resolve().parent.parent / "webapp" / "icons"
# Порядок = приоритет: свой PNG перекроет SVG.
ICON_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg", ".svg")
ICON_TYPES = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}

OVERRIDES_FILE = "catalog.toml"
CATALOG_NAME = "Весь каталог"
OTHER_CATEGORY = "Другое"
# Порядок фильтров, если catalog.toml его не задаёт. Прочие категории идут
# за ними по алфавиту, «Другое» — последней.
DEFAULT_CATEGORIES = ("Банки", "Общение", "Медиа", "Нейросети", "Сервисы")
FEATURED_COUNT = 6
NO_ORDER = 1_000_000

# Поля раздела в catalog.toml и их типы.
APP_KEYS: dict[str, type] = {
    "name": str,
    "category": str,
    "desc": str,
    "icon": str,
    "color": str,
    "dark": bool,
    "version": str,
    "min_ios": str,
    "order": int,
    "hidden": bool,
    "price": int,
}
TOP_KEYS = ("categories", "featured")

COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
# Плитка видна, только когда иконки нет. Цвет выбирается по слагу, чтобы не
# прыгал от запроса к запросу.
PALETTE = ("#2F6FED", "#21A038", "#7B61FF", "#E8553E", "#0FA3B1", "#D6457B", "#3D5A80", "#B7791F")

_folder: Path | None = None

# catalog.toml: что прочитали и с какого состояния файла. При ошибке в файле
# витрина работает на последнем удачном прочтении, а не падает.
_overrides: dict = {"stamp": None, "data": {"top": {}, "apps": {}}}
_built: dict = {"key": None, "apps": []}
_warned: set[str] = set()


def _warn_once(message: str, *args) -> None:
    text = message % args if args else message
    if text not in _warned:
        _warned.add(text)
        log.warning(text)


def use_folder(folder: Path | None) -> None:
    """Папка со сборками. Её задают сервер Mini App и сервис заказов при старте."""
    global _folder
    _folder = folder


def folder() -> Path | None:
    return _folder


# ------------------------------------------------------------- catalog.toml


def _clean_app(section: str, raw: dict) -> dict:
    out: dict = {}
    for key, value in raw.items():
        kind = APP_KEYS.get(key)
        if kind is None:
            _warn_once("catalog.toml: неизвестное поле «%s» в разделе [%s] — пропущено", key, section)
            continue
        # bool — подкласс int в Python: order = true не должен пройти как число.
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            hint = " — пишите в кавычках" if kind is str else ""
            _warn_once("catalog.toml: [%s] %s = %r — неверный тип%s, поле пропущено", section, key, value, hint)
            continue
        if key == "color" and not COLOR_RE.fullmatch(value):
            _warn_once("catalog.toml: [%s] color = %r — нужен вид \"#21A038\", поле пропущено", section, value)
            continue
        if key == "icon" and (Path(value).name != value or "\\" in value
                              or Path(value).suffix.lower() not in ICON_EXTENSIONS):
            _warn_once("catalog.toml: [%s] icon = %r — нужно имя картинки рядом, например \"sber.png\"", section, value)
            continue
        out[key] = value
    return out


def _parse_overrides(text: str) -> dict:
    data = tomllib.loads(text)
    top: dict = {}
    apps: dict = {}
    for key, value in data.items():
        if isinstance(value, dict):
            apps[key] = _clean_app(key, value)
        elif key in TOP_KEYS:
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                top[key] = list(value)
            else:
                _warn_once("catalog.toml: %s должен быть списком строк — пропущено", key)
        else:
            _warn_once("catalog.toml: неизвестный параметр «%s» — пропущено", key)
    return {"top": top, "apps": apps}


def overrides() -> dict:
    """Уточнения из catalog.toml: {"top": {...}, "apps": {раздел: {...}}}."""
    path = _folder / OVERRIDES_FILE if _folder else None
    if not path or not path.is_file():
        _overrides["stamp"] = None
        _overrides["data"] = {"top": {}, "apps": {}}
        return _overrides["data"]
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    if stamp != _overrides["stamp"]:
        _overrides["stamp"] = stamp
        try:
            _overrides["data"] = _parse_overrides(path.read_text(encoding="utf-8-sig"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
            # Остаётся прошлое удачное прочтение: витрина не должна падать из-за опечатки.
            _warn_once("catalog.toml не прочитан, работаем по прошлой версии: %s", exc)
    return _overrides["data"]


# ------------------------------------------------------------------ товары


def _palette(slug: str) -> str:
    return PALETTE[hashlib.md5(slug.encode()).digest()[0] % len(PALETTE)]


def _is_light(color: str) -> bool:
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.299 * r + 0.587 * g + 0.114 * b > 170


def icon_file(slug: str) -> str:
    """Картинка из webapp/icons для слага — любого, не только из каталога.

    Значки банков нужны странице оплаты даже тогда, когда приложений
    этого банка в каталоге нет.
    """
    if not slug or not ICONS_DIR.is_dir():
        return ""
    for ext in ICON_EXTENSIONS:
        if (ICONS_DIR / (slug + ext)).is_file():
            return slug + ext
    return ""


def _icon_source(slug: str, override: str, entry: dict) -> tuple[str, Path | None, int] | None:
    """Откуда брать иконку: своя картинка → сборка → webapp/icons → нет (буква)."""
    if override:
        for base in (_folder, ICONS_DIR):
            if base and (base / override).is_file():
                path = base / override
                return "file", path, int(path.stat().st_mtime)
        _warn_once("catalog.toml: [%s] icon = %r — файла нет ни рядом со сборками, ни в webapp/icons", slug, override)
    if entry.get("iconMember"):
        return "build", None, entry["updated"]
    repo = icon_file(slug)
    if repo:
        path = ICONS_DIR / repo
        return "file", path, int(path.stat().st_mtime)
    return None


def _category_rank(categories: list[str]):
    order = {name: i for i, name in enumerate(categories)}

    def rank(name: str) -> tuple:
        if name == OTHER_CATEGORY and name not in order:
            return (2, 0, name)
        if name in order:
            return (0, order[name], name)
        return (1, 0, name.casefold())

    return rank


def _make_app(entry: dict, fields: dict) -> dict:
    slug = entry["slug"]
    build = dict(entry)
    if fields.get("version"):
        build["version"] = fields["version"]
    if fields.get("min_ios"):
        build["minOs"] = fields["min_ios"]
    name = (fields.get("name") or entry["name"] or slug).strip()
    color = fields.get("color") or _palette(slug)
    icon = _icon_source(slug, fields.get("icon", ""), entry)
    app = {
        "slug": slug,
        "name": name,
        "category": (fields.get("category") or OTHER_CATEGORY).strip(),
        "desc": (fields.get("desc") or "").strip(),
        "color": color,
        "dark": fields["dark"] if "dark" in fields else _is_light(color),
        "letter": name[:1].upper() or "?",
        "order": fields.get("order", NO_ORDER),
        "hidden": bool(fields.get("hidden")),
        "build": build,
        "icon": icon,
        "iconUrl": "/icons/%s?v=%d" % (slug, icon[2]) if icon else "",
    }
    if "price" in fields:
        app["price"] = fields["price"]
    return app


def _images_stamp() -> tuple:
    """Картинки рядом со сборками: подложили новую — каталог пересобирается."""
    if not _folder or not _folder.is_dir():
        return ()
    stamp = []
    for path in sorted(_folder.iterdir()):
        if path.suffix.lower() in ICON_EXTENSIONS and path.is_file():
            stat = path.stat()
            stamp.append((path.name, stat.st_size, stat.st_mtime_ns))
    return tuple(stamp)


def _all_apps() -> list[dict]:
    """Все товары, включая скрытые, в порядке витрины."""
    builds = ipa.entries(_folder)
    ov = overrides()
    key = (
        tuple((e["file"], e["size"], e["updated"]) for e in builds.values()),
        _overrides["stamp"],
        _images_stamp(),
    )
    if key == _built["key"]:
        return _built["apps"]

    sections = ov["apps"]
    apps = []
    for slug, entry in builds.items():
        fields = dict(sections.get(slug, {}))
        fields.update(sections.get(entry["file"], {}))
        apps.append(_make_app(entry, fields))

    rank = _category_rank(ov["top"].get("categories") or list(DEFAULT_CATEGORIES))
    apps.sort(key=lambda a: (rank(a["category"]), a["order"], a["name"].casefold()))

    known = set(builds) | {e["file"] for e in builds.values()}
    for section in sections:
        if section not in known:
            _warn_once("catalog.toml: раздел [%s] — такой сборки в папке нет", section)

    _built["key"] = key
    _built["apps"] = apps
    return apps


def apps() -> list[dict]:
    """Товары витрины в порядке показа, без скрытых."""
    return [a for a in _all_apps() if not a["hidden"]]


def get_app(slug: str | None) -> dict | None:
    if not slug:
        return None
    return next((a for a in apps() if a["slug"] == slug), None)


def count() -> int:
    return len(apps())


def categories() -> list[str]:
    """Категории, в которых есть товары, — в порядке фильтров витрины."""
    seen: list[str] = []
    for app in apps():
        if app["category"] not in seen:
            seen.append(app["category"])
    return seen


def featured() -> list[str]:
    """Плитки в шапке: из catalog.toml, иначе первые товары каталога."""
    visible = [a["slug"] for a in apps()]
    wanted = overrides()["top"].get("featured")
    if wanted:
        return [slug for slug in wanted if slug in visible]
    return visible[:FEATURED_COUNT]


def app_price(app: dict | None, default: int) -> int:
    """Своя цена приложения — поле price в catalog.toml; без него действует общая."""
    if app and app.get("price"):
        return int(app["price"])
    return default


def product_name(slug: str | None) -> str:
    """Название для заказа. Сборку могли убрать — тогда берём имя из catalog.toml."""
    if not slug:
        return CATALOG_NAME
    app = get_app(slug)
    if app:
        return app["name"]
    return overrides()["apps"].get(slug, {}).get("name") or slug


def public_catalog(default_price: int) -> list[dict]:
    return [
        {
            "slug": a["slug"],
            "name": a["name"],
            "category": a["category"],
            "color": a["color"],
            "dark": a["dark"],
            "letter": a["letter"],
            "iconUrl": a["iconUrl"],
            "desc": a["desc"],
            "price": app_price(a, default_price),
            "build": ipa.public(a["build"]),
        }
        for a in apps()
    ]


def icon_bytes(slug: str) -> tuple[bytes, str] | None:
    """Картинка товара для /icons/<слаг>: байты и тип. Пересборка PNG — тяжёлая,
    поэтому сервер зовёт это из отдельного потока."""
    app = get_app(slug)
    if not app or not app["icon"]:
        return None
    kind, path, _stamp = app["icon"]
    if kind == "build":
        data = ipa.icon_png(app["build"])
        return (data, "image/png") if data else None
    try:
        return path.read_bytes(), ICON_TYPES.get(path.suffix.lower(), "application/octet-stream")
    except OSError:
        return None


def download_links(cfg) -> dict[str, str]:
    """Полные адреса сборок для сообщений бота: слаг → ссылка.

    Без домена ссылку дать некуда — возвращаем пусто.
    """
    if not cfg.webapp_enabled:
        return {}
    base = cfg.webapp_url.rstrip("/")
    return {a["slug"]: "%s/ipa/%s" % (base, a["slug"]) for a in apps()}


def as_text(links: dict[str, str] | None = None) -> str:
    """Каталог для чата бота — по категориям.

    links: слаг → адрес сборки. Для таких приложений название становится
    ссылкой на скачивание, остальные идут обычным текстом.
    """
    links = links or {}
    lines: list[str] = []
    for cat in categories():
        names = [
            '<a href="%s">%s</a>' % (links[a["slug"]], escape(a["name"], quote=False))
            if a["slug"] in links else escape(a["name"], quote=False)
            for a in apps() if a["category"] == cat
        ]
        if names:
            lines.append("<b>%s</b>\n" % escape(cat, quote=False) + " · ".join(names))
    return "\n\n".join(lines)


# ------------------------------------------------------------- значки банков

# Как покупатели называют банки в реквизитах — приводим к имени иконки.
# Значки банков лежат в webapp/icons/ независимо от каталога: получать
# перевод можно на банк, приложения которого мы не продаём.
BANK_ALIASES = {
    "тинькофф": "tbank",
    "тинькоф": "tbank",
    "тбанк": "tbank",
    "сбер": "sber",
    "альфа": "alfa",
    "райф": "raiffeisen",
    "промсвязьбанк": "psb",
    "газпром": "gazprombank",
    "совком": "sovcombank",
    "втб": "vtb",
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
    return ""
