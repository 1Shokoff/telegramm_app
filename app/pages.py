"""Обычные HTML-страницы витрины — без Telegram и без JavaScript.

Нужны для двух вещей: по прямой ссылке их открывает покупатель из чата бота
и модератор платёжной системы, а подвал с реквизитами виден на каждой из них.
Каталог и раздел «О нас» живут в Mini App, она же открывается в браузере.
"""
from __future__ import annotations

from html import escape

from . import const, legal
from .config import Config


def _e(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def footer(cfg: Config) -> str:
    """Подвал: реквизиты, контакты и ссылки на документы — на каждой странице."""
    links = ['<a href="/">О сервисе</a>']
    for key, title, _hint in const.DOCUMENTS:
        path = cfg.doc_path(key)
        if path:
            links.append('<a href="%s">%s</a>' % (_e(path), _e(title)))

    contacts = []
    if cfg.contact_email:
        contacts.append('<a href="mailto:%s">%s</a>' % (_e(cfg.contact_email), _e(cfg.contact_email)))
    if cfg.contact_phone:
        contacts.append(_e(cfg.contact_phone))
    if cfg.support_username:
        contacts.append(
            '<a href="https://t.me/%s">@%s</a>' % (_e(cfg.support_username), _e(cfg.support_username))
        )

    parts = ['<footer class="footer">']
    parts.append('<div class="footer-links">%s</div>' % "".join(links))
    if contacts:
        parts.append('<div class="footer-line">%s</div>' % " · ".join(contacts))
    parts.append(
        '<div class="footer-line legal">%s</div>'
        % (_e(cfg.legal_line) if cfg.legal_line else "Реквизиты продавца появятся здесь до открытия продаж.")
    )
    parts.append("</footer>")
    return "".join(parts)


def shell(cfg: Config, title: str, body: str, version: str = "") -> str:
    """Каркас страницы: та же тема и тот же подвал, что в витрине."""
    suffix = ("?v=" + version) if version else ""
    return (
        '<!DOCTYPE html>\n<html lang="ru">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<meta name="color-scheme" content="dark light">\n'
        '<meta name="theme-color" content="#0b0d09">\n'
        "<title>%s · iApki</title>\n"
        '<link rel="icon" type="image/svg+xml" href="/static/brand/icon.svg">\n'
        '<link rel="stylesheet" href="/static/styles.css%s">\n'
        "</head>\n<body>\n"
        '<div class="shell">\n'
        '<header class="topbar">\n'
        '<a class="brand" href="/">\n'
        '<span class="brand-icon"><img src="/static/brand/icon.svg" alt="" width="44" height="44"></span>\n'
        '<span class="brand-text"><span class="brand-title">iApki</span>'
        '<span class="brand-sub">%s</span></span>\n'
        "</a>\n</header>\n"
        '<main class="view doc">\n%s\n</main>\n'
        "%s\n</div>\n</body>\n</html>\n"
    ) % (_e(title), suffix, _e(title), body, footer(cfg))


def document_page(key: str, cfg: Config, version: str = "") -> str | None:
    found = legal.document(key, cfg)
    if not found:
        return None
    title, html = found
    back = '<a class="backlink" href="/docs">← Все документы</a>'
    return shell(cfg, title, back + html, version)


def documents_page(cfg: Config, version: str = "") -> str:
    items = []
    for key, title, hint in legal.available():
        items.append(
            '<a class="doc-row" href="/docs/%s">'
            '<span class="doc-text"><span class="doc-title">%s</span>'
            '<span class="doc-hint">%s</span></span>'
            '<span class="doc-flag open">↗</span></a>' % (_e(key), _e(title), _e(hint))
        )
    body = (
        "<h1>Документы</h1>"
        '<p class="muted">Условия работы сервиса iApki, порядок возврата '
        "и обработка персональных данных.</p>"
        '<div class="card tight">%s</div>' % "".join(items)
    )
    return shell(cfg, "Документы", body, version)
