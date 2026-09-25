"""Документы витрины: оферта, политика, возврат, согласие.

Тексты лежат в webapp/legal/<ключ>.txt и правятся без пересборки образа —
папка webapp примонтирована в контейнер.

Разметка простая, чтобы файл можно было править без знания HTML:
    # Заголовок          — заголовок страницы
    ## Раздел            — подзаголовок
    - пункт              — список (весь абзац из таких строк)
    пустая строка        — новый абзац

Подстановки вида {{inn}} заполняются из .env. Если значение не задано,
на его месте видно, чего не хватает, — так незаполненный документ
не выглядит готовым.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from . import const
from .config import Config

LEGAL_DIR = Path(__file__).resolve().parent.parent / "webapp" / "legal"

TITLES = {key: title for key, title, _hint in const.DOCUMENTS}


def path_of(key: str) -> Path:
    return LEGAL_DIR / ("%s.txt" % key)


def exists(key: str) -> bool:
    return key in TITLES and path_of(key).is_file()


def available() -> list[tuple[str, str, str]]:
    """Документы, для которых есть текст: (ключ, заголовок, пояснение)."""
    return [(key, title, hint) for key, title, hint in const.DOCUMENTS if exists(key)]


def _missing(what: str) -> str:
    return "[укажите %s]" % what


def _tokens(cfg: Config, updated: str) -> dict[str, str]:
    return {
        "legal_name": cfg.legal_name or _missing("наименование продавца"),
        "inn": cfg.legal_inn or _missing("ИНН"),
        "address": cfg.legal_address or _missing("город"),
        "email": cfg.contact_email or _missing("почту"),
        "phone": cfg.contact_phone or _missing("телефон"),
        "support": ("@" + cfg.support_username) if cfg.support_username else _missing("аккаунт Telegram"),
        "price": "%s ₽" % "{:,}".format(cfg.price_rub).replace(",", " "),
        "site": cfg.webapp_url or _missing("адрес сайта"),
        "updated": updated,
    }


def _fill(text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        text = text.replace("{{%s}}" % key, value)
    return text


def to_html(text: str) -> str:
    """Текст документа → HTML. Ничего, кроме наших же файлов, сюда не попадает."""
    out: list[str] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        if lines[0].startswith("# "):
            out.append("<h1>%s</h1>" % escape(lines[0][2:]))
            lines = lines[1:]
            if not lines:
                continue
        if lines[0].startswith("## "):
            out.append("<h2>%s</h2>" % escape(lines[0][3:]))
            lines = lines[1:]
            if not lines:
                continue
        if all(line.startswith("- ") for line in lines):
            items = "".join("<li>%s</li>" % escape(line[2:]) for line in lines)
            out.append("<ul>%s</ul>" % items)
            continue
        out.append("<p>%s</p>" % "<br>".join(escape(line) for line in lines))
    return "\n".join(out)


def document(key: str, cfg: Config) -> tuple[str, str] | None:
    """Заголовок и готовый HTML документа; None — текста нет."""
    if not exists(key):
        return None
    path = path_of(key)
    raw = path.read_text(encoding="utf-8")
    updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%d.%m.%Y")
    return TITLES[key], to_html(_fill(raw, _tokens(cfg, updated)))
