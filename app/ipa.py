"""Сборки .ipa, лежащие на сервере: из них собирается витрина.

Файл называется `<слаг>-<версия>.ipa` — например `sber-17.6.1.ipa`. Слаг —
ключ товара: латиница в нижнем регистре, цифры и дефисы. Версию берём из
имени: её ведёт владелец, и она совпадает с тем, что он выкладывает в канал.
Если версии в имени нет, читаем её из самой сборки.

Название, минимальную iOS и иконку отдаёт сама сборка (app/bundle.py).
Что из этого показать покупателю, решает app/catalog.py.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import bundle

log = logging.getLogger(__name__)

# Слаг попадает в адреса /ipa/<слаг> и /icons/<слаг>, поэтому только безопасные символы.
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

# Разбор архива стоит чтения оглавления, а иконка — ещё и пересборки PNG,
# поэтому результат запоминаем. Ключ включает размер и время — подменили
# файл, прочитаем заново.
_cache: dict[tuple[str, int, int], dict] = {}
_icons: dict[tuple[str, int, int], bytes] = {}

# Папка читается на каждый запрос витрины — про лишний файл ругаемся один раз.
_warned: set[str] = set()


def _warn_once(key: str, message: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(message, *args)


def _slug_and_version(stem: str) -> tuple[str, str]:
    """«ozon-bank-19.27.0» → («ozon-bank», «19.27.0»); «sber» → («sber», «»)."""
    head, sep, tail = stem.rpartition("-")
    if sep and tail[:1].isdigit():
        return head, tail
    return stem, ""


def _key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (path.name, stat.st_size, int(stat.st_mtime))


def _from_build(path: Path) -> dict:
    key = _key(path)
    meta = _cache.get(key)
    if meta is None:
        try:
            meta = bundle.describe(path)
        except Exception as exc:  # noqa: BLE001 — чужой архив: причина важнее типа
            log.warning("не прочитали сборку %s: %s", path.name, exc)
            meta = {"name": "", "version": "", "minOs": "", "icon": ""}
        _cache[key] = meta
    return meta


def _describe(path: Path, slug: str, version: str) -> dict:
    stat = path.stat()
    meta = _from_build(path)
    return {
        "slug": slug,
        "path": path,
        "file": path.name,
        "name": meta["name"],
        "version": version or meta["version"],
        "minOs": meta["minOs"],
        "iconMember": meta["icon"],
        "size": stat.st_size,
        "updated": int(stat.st_mtime),
    }


def entries(folder: Path | None) -> dict[str, dict]:
    """Все сборки папки по слагам. Для одного слага берём самую свежую."""
    found: dict[str, dict] = {}
    if not folder or not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.ipa")):
        if not path.is_file():
            continue
        slug, version = _slug_and_version(path.stem)
        if not SLUG_RE.fullmatch(slug):
            _warn_once(
                path.name,
                "файл %s пропущен: имя должно быть <слаг>-<версия>.ipa, "
                "слаг — латиница в нижнем регистре, цифры и дефисы",
                path.name,
            )
            continue
        item = _describe(path, slug, version)
        current = found.get(slug)
        if not current or item["updated"] > current["updated"]:
            found[slug] = item
    return found


def icon_png(entry: dict) -> bytes | None:
    """Иконка сборки обычным PNG; None — в сборке её нет или она не читается."""
    member = entry.get("iconMember")
    if not member:
        return None
    key = _key(entry["path"])
    cached = _icons.get(key)
    if cached is None:
        try:
            cached = bundle.icon_png(entry["path"], member)
        except Exception as exc:  # noqa: BLE001
            _warn_once("icon:" + entry["file"], "не достали иконку из %s: %s", entry["file"], exc)
            cached = b""
        _icons[key] = cached
    return cached or None


def size_text(size: int) -> str:
    """Размер человеку: 262,4 МБ."""
    mb = size / 1024 / 1024
    if mb >= 1024:
        return ("%.1f ГБ" % (mb / 1024)).replace(".", ",")
    return ("%.1f МБ" % mb).replace(".", ",")


def download_name(entry: dict) -> str:
    """Имя файла для браузера: слаг и версия, без пробелов и кириллицы."""
    if entry["version"]:
        return "%s-%s.ipa" % (entry["slug"], entry["version"])
    return "%s.ipa" % entry["slug"]


def public(entry: dict) -> dict:
    """Что витрина показывает о сборке."""
    return {
        "version": entry["version"],
        "minOs": entry["minOs"],
        "size": entry["size"],
        "sizeText": size_text(entry["size"]),
        "url": "/ipa/" + entry["slug"],
        "updated": entry["updated"],
    }
