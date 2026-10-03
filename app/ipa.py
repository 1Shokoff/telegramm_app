"""Сборки .ipa, лежащие на сервере: версия, размер и минимальная iOS.

Файл называется `<слаг>-<версия>.ipa` — например `sber-17.6.1.ipa`. Версию
берём из имени: её ведёт владелец, и она совпадает с тем, что он выкладывает
в канал. Если версии в имени нет, читаем её из самой сборки.

Ничего не хранится в коде: положили новый файл — витрина показывает новую
версию и размер, убрали файл — кнопка скачивания пропала.
"""
from __future__ import annotations

import logging
import plistlib
import zipfile
from pathlib import Path

from . import catalog

log = logging.getLogger(__name__)

# Разбор Info.plist стоит чтения оглавления архива, поэтому результат
# запоминаем. Ключ включает размер и время — подменили файл, прочитаем заново.
_cache: dict[tuple[str, int, int], dict] = {}

# Папка читается на каждый запрос витрины — про лишний файл ругаемся один раз.
_warned: set[str] = set()


def _slug_and_version(stem: str) -> tuple[str, str]:
    """«ozon-bank-19.27.0» → («ozon-bank», «19.27.0»); «sber» → («sber», «»)."""
    head, sep, tail = stem.rpartition("-")
    if sep and tail[:1].isdigit():
        return head, tail
    return stem, ""


def _from_build(path: Path) -> dict:
    """Что сборка говорит о себе сама: версия и минимальная iOS."""
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                parts = name.split("/")
                if len(parts) == 3 and parts[0] == "Payload" and parts[2] == "Info.plist":
                    info = plistlib.loads(zf.read(name))
                    return {
                        "version": str(info.get("CFBundleShortVersionString", "")),
                        "minOs": str(info.get("MinimumOSVersion", "")),
                    }
    except (OSError, zipfile.BadZipFile, plistlib.InvalidFileException) as exc:
        log.warning("не прочитали сборку %s: %s", path.name, exc)
    return {"version": "", "minOs": ""}


def _describe(path: Path, slug: str, version: str) -> dict:
    stat = path.stat()
    key = (path.name, stat.st_size, int(stat.st_mtime))
    build = _cache.get(key)
    if build is None:
        build = _from_build(path)
        _cache[key] = build
    return {
        "slug": slug,
        "path": path,
        "file": path.name,
        "version": version or build["version"],
        "minOs": build["minOs"],
        "size": stat.st_size,
        "updated": int(stat.st_mtime),
    }


def entries(folder: Path | None) -> dict[str, dict]:
    """Сборки по слагам каталога. Для одного слага берём самую свежую."""
    found: dict[str, dict] = {}
    if not folder or not folder.is_dir():
        return found
    known = {app["slug"] for app in catalog.APPS}
    for path in sorted(folder.glob("*.ipa")):
        slug, version = _slug_and_version(path.stem)
        if slug not in known:
            if path.name not in _warned:
                _warned.add(path.name)
                log.warning("файл %s не совпал ни с одним приложением каталога", path.name)
            continue
        item = _describe(path, slug, version)
        current = found.get(slug)
        if not current or item["updated"] > current["updated"]:
            found[slug] = item
    return found


def find(folder: Path | None, slug: str) -> dict | None:
    return entries(folder).get(slug)


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


def links(cfg) -> dict[str, str]:
    """Полные адреса сборок для сообщений бота: слаг → ссылка.

    Без домена ссылку дать некуда — возвращаем пусто.
    """
    if not cfg.webapp_enabled:
        return {}
    return {
        slug: "%s/ipa/%s" % (cfg.webapp_url.rstrip("/"), slug)
        for slug in entries(cfg.ipa_dir)
    }


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
