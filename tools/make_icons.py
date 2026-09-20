"""Генерирует набор иконок каталога: python tools/make_icons.py

Рисует по файлу на каждое приложение из app/catalog.py — плитка фирменного
цвета со знаком категории. Это собственная графика, а не логотипы брендов:
если есть права на настоящие логотипы, просто положите свои файлы
webapp/icons/<slug>.png — они перекроют сгенерированные.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import catalog  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp", "icons")
SIZE = 128

# Знак категории: путь рисуется в квадрате 48×48 с началом в (40, 40).
GLYPHS = {
    "Банки": (
        '<rect x="40" y="46" width="48" height="36" rx="6" fill="none" stroke="{ink}" stroke-width="6"/>'
        '<path d="M40 60 H88" stroke="{ink}" stroke-width="6"/>'
        '<path d="M50 72 H62" stroke="{ink}" stroke-width="6" stroke-linecap="round"/>'
    ),
    "Общение": (
        '<path d="M42 46h44a4 4 0 0 1 4 4v26a4 4 0 0 1-4 4H62l-14 10V80h-6a4 4 0 0 1-4-4V50a4 4 0 0 1 4-4z"'
        ' fill="none" stroke="{ink}" stroke-width="6" stroke-linejoin="round"/>'
    ),
    "Нейросети": (
        '<path d="M64 38 L71 57 L90 64 L71 71 L64 90 L57 71 L38 64 L57 57 Z"'
        ' fill="{ink}"/>'
    ),
    "Покупки": (
        '<path d="M42 54h44l-4 32a4 4 0 0 1-4 4H50a4 4 0 0 1-4-4z"'
        ' fill="none" stroke="{ink}" stroke-width="6" stroke-linejoin="round"/>'
        '<path d="M54 58V48a10 10 0 0 1 20 0v10" fill="none" stroke="{ink}"'
        ' stroke-width="6" stroke-linecap="round"/>'
    ),
    "Сервисы": (
        '<circle cx="64" cy="64" r="24" fill="none" stroke="{ink}" stroke-width="6"/>'
        '<path d="M64 46 L72 64 L64 82 L56 64 Z" fill="{ink}"/>'
    ),
    "Медиа": (
        '<circle cx="64" cy="64" r="24" fill="none" stroke="{ink}" stroke-width="6"/>'
        '<path d="M58 53 L80 64 L58 75 Z" fill="{ink}"/>'
    ),
}


def icon_svg(app: dict) -> str:
    ink = "#0b0b0c" if app.get("dark") else "#ffffff"
    glyph = GLYPHS[app["category"]].format(ink=ink)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">'
        '<rect width="%d" height="%d" rx="28" fill="%s"/>'
        "%s"
        "</svg>\n" % (SIZE, SIZE, SIZE, SIZE, SIZE, SIZE, app["color"], glyph)
    )


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    written = 0
    for app in catalog.APPS:
        path = os.path.join(OUT, "%s.svg" % app["slug"])
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(icon_svg(app))
        written += 1
    print("Готово: %d иконок в %s" % (written, OUT))


if __name__ == "__main__":
    main()
