"""Собирает ассеты бренда из исходного логотипа: python tools/make_brand.py

Исходник — webapp/brand/logo.svg (знак iA и надпись iApki на салатовом).
Из него получаются:
  icon.svg   — только знак, почти без полей: шапка Mini App и иконка вкладки;
  avatar.svg — знак с широкими полями: Telegram обрезает аватар в круг,
               и углы квадрата не должны попасть под обрезку.

Пути не перерисовываются: знак вырезается из исходника рамкой viewBox,
а контуры надписи (они ниже знака) просто отбрасываются.
"""
from __future__ import annotations

import os
import re

BRAND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webapp", "brand")
SOURCE = os.path.join(BRAND, "logo.svg")

# Знак занимает примерно x 342..912, y 197..766 на холсте 1254×1254.
ICON_CENTER = (627, 481)
ICON_SIDE = 570
# Всё, что начинается ниже этой линии, — буквы надписи iApki.
WORDMARK_TOP = 790

_SUBPATH = re.compile(r"M\s*[\d.]+,([\d.]+)[^M]*")


def mark_only(path_data: str) -> str:
    """Оставляет подконтуры знака, выбрасывая подконтуры надписи."""
    kept = [m.group(0) for m in _SUBPATH.finditer(path_data) if float(m.group(1)) < WORDMARK_TOP]
    return " ".join(part.strip() for part in kept)


def crop(svg: str, side: float, title: str, radius: float = 0) -> str:
    cx, cy = ICON_CENTER
    x, y = cx - side / 2, cy - side / 2
    svg = re.sub(
        r"<svg[^>]*>",
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
        'viewBox="%g %g %g %g">' % (x, y, side, side),
        svg,
        count=1,
    )
    svg = re.sub(r"<title>.*?</title>", "<title>%s</title>" % title, svg, count=1)
    # Фон растягиваем на всю рамку, чтобы у обрезки не было прозрачных краёв.
    # radius — скругление салатовой подложки: у иконки вкладки браузера
    # квадратные углы выглядят чужеродно, у аватара их скрывает круглая обрезка.
    corner = ' rx="%g"' % radius if radius else ""
    svg = re.sub(
        r'<rect[^>]*fill="#CBFA2D"\s*/>',
        '<rect x="%g" y="%g" width="%g" height="%g"%s fill="#CBFA2D"/>'
        % (x, y, side, side, corner),
        svg,
        count=1,
    )
    return re.sub(
        r'd="([^"]*)"',
        lambda m: 'd="%s"' % mark_only(m.group(1)),
        svg,
    )


def main() -> None:
    with open(SOURCE, encoding="utf-8") as fh:
        source = fh.read()

    outputs = {
        # Поля ~2,5%: как у brand.png, салатовая подложка повторяет
        # скругление самого знака — иначе во вкладке браузера торчат углы.
        "icon.svg": crop(source, ICON_SIDE * 1.05, "iApki", radius=ICON_SIDE * 0.27),
        # Поля под круглую обрезку: квадрат со скруглением вписывается в круг.
        "avatar.svg": crop(source, ICON_SIDE * 1.42, "iApki — аватар бота"),
    }
    for name, svg in outputs.items():
        with open(os.path.join(BRAND, name), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(svg if svg.endswith("\n") else svg + "\n")
        print("%-11s %6d байт" % (name, len(svg.encode("utf-8"))))


if __name__ == "__main__":
    main()
