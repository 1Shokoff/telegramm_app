"""Что внутри .ipa: описание для каталога и иконка для витрины.

    python tools/ipa_scan.py <папка с .ipa>                 — таблица и JSON
    python tools/ipa_scan.py <папка> --icons webapp/icons   — ещё и иконки

Архив целиком не распаковывается: читаем Info.plist и одну картинку.

Иконки в сборках лежат в формате Apple (чанк CgBI): каналы переставлены
местами, альфа вмножена в цвет, а поток сжат без заголовка zlib. Браузер
такой PNG не открывает, поэтому мы его пересобираем. Pillow для этого не
нужен — хватает zlib из стандартной библиотеки.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import struct
import sys
import zipfile
import zlib
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ------------------------------------------------------------------ PNG

def chunks(data: bytes):
    pos = len(PNG_MAGIC)
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        yield kind, body
        pos += 12 + length


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    """Снимает построчные фильтры PNG (типы 0–4)."""
    stride = width * bpp
    out = bytearray(stride * height)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        prev = out[(y - 1) * stride:y * stride] if y else bytearray(stride)
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            if ftype == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ftype == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ftype == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                c = prev[x - bpp] if x >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
            elif ftype != 0:
                raise ValueError("неизвестный фильтр строки: %d" % ftype)
        out[y * stride:(y + 1) * stride] = line
    return out


def from_apple_png(data: bytes) -> bytes:
    """PNG из сборки → обычный PNG. Не трогает картинки без чанка CgBI."""
    parts = list(chunks(data))
    if not any(kind == b"CgBI" for kind, _ in parts):
        return data

    header = next(body for kind, body in parts if kind == b"IHDR")
    width, height, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", header[:13])
    if depth != 8 or color != 6 or interlace:
        raise ValueError("ожидали 8-битный RGBA без чересстрочности")

    packed = b"".join(body for kind, body in parts if kind == b"IDAT")
    raw = zlib.decompressobj(-zlib.MAX_WBITS).decompress(packed)
    pixels = unfilter(raw, width, height, 4)

    # Каналы в сборке идут как BGRA, а цвет умножен на прозрачность.
    for i in range(0, len(pixels), 4):
        b, g, r, a = pixels[i], pixels[i + 1], pixels[i + 2], pixels[i + 3]
        if a and a != 255:
            r = min(255, r * 255 // a)
            g = min(255, g * 255 // a)
            b = min(255, b * 255 // a)
        pixels[i], pixels[i + 1], pixels[i + 2] = r, g, b

    stride = width * 4
    body = bytearray()
    for y in range(height):
        body.append(0)  # фильтр «как есть»
        body += pixels[y * stride:(y + 1) * stride]

    return (
        PNG_MAGIC
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(body), 9))
        + chunk(b"IEND", b"")
    )


def png_size(data: bytes) -> tuple[int, int]:
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def main_color(png: bytes) -> str:
    """Самый частый цвет иконки — им красится плитка в витрине.

    Берём только непрозрачные пиксели и огрубляем цвет до шага 16, иначе
    градиент рассыпается на тысячи почти одинаковых оттенков.
    """
    header = next(body for kind, body in chunks(png) if kind == b"IHDR")
    width, height, depth, color, _comp, _filt, interlace = struct.unpack(">IIBBBBB", header[:13])
    if depth != 8 or color != 6 or interlace:
        # Палитра и оттенки серого встречаются редко; цвет плитки зададим руками.
        return ""
    packed = b"".join(body for kind, body in chunks(png) if kind == b"IDAT")
    pixels = unfilter(zlib.decompress(packed), width, height, 4)

    counts: dict[tuple[int, int, int], int] = {}
    for i in range(0, len(pixels), 4):
        if pixels[i + 3] < 200:
            continue
        key = (pixels[i] >> 4, pixels[i + 1] >> 4, pixels[i + 2] >> 4)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    r, g, b = max(counts, key=lambda k: counts[k])
    return "#%02X%02X%02X" % (r * 17, g * 17, b * 17)


# ------------------------------------------------------------------ .ipa

def app_info(zf: zipfile.ZipFile) -> tuple[str, dict]:
    for name in zf.namelist():
        parts = name.split("/")
        if len(parts) == 3 and parts[0] == "Payload" and parts[2] == "Info.plist":
            return name.rsplit("/", 1)[0] + "/", plistlib.loads(zf.read(name))
    raise LookupError("Info.plist приложения не найден")


def icon_names(info: dict) -> list[str]:
    """Имена иконок, объявленные самим приложением."""
    names: list[str] = []
    icons = info.get("CFBundleIcons") or {}
    primary = icons.get("CFBundlePrimaryIcon") or {}
    names += primary.get("CFBundleIconFiles") or []
    if primary.get("CFBundleIconName"):
        names.append(primary["CFBundleIconName"])
    names += info.get("CFBundleIconFiles") or []
    return names


def best_icon(zf: zipfile.ZipFile, app_dir: str, info: dict) -> str:
    """Самый крупный файл иконки: сначала объявленные, потом похожие по имени."""
    root = [
        n for n in zf.namelist()
        if n.startswith(app_dir) and "/" not in n[len(app_dir):] and n.lower().endswith(".png")
    ]
    declared = [p.lower() for p in icon_names(info)]
    picked = [n for n in root if any(n[len(app_dir):].lower().startswith(p) for p in declared)]
    if not picked:
        picked = [n for n in root if "appicon" in n[len(app_dir):].lower()]
    if not picked:
        return ""
    return max(picked, key=lambda n: zf.getinfo(n).file_size)


def version_from_name(stem: str) -> str:
    """Версия из имени файла: «patched-MAX [26.17.3]» → 26.17.3."""
    found = re.search(r"\[([^\]]+)\]", stem)
    return found.group(1).strip() if found else ""


def title_from_name(stem: str) -> str:
    """Имя файла без приставки патчера и номера версии."""
    clean = re.sub(r"^(patched|pathced)-", "", stem, flags=re.IGNORECASE)
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", clean).strip()


def scan(path: Path, icons_dir: Path | None) -> dict:
    row: dict = {
        "file": path.name,
        "title": title_from_name(path.stem),
        "version": version_from_name(path.stem),
        "sizeMb": round(path.stat().st_size / 1024 / 1024, 1),
    }
    with zipfile.ZipFile(path) as zf:
        app_dir, info = app_info(zf)
        row.update(
            appName=info.get("CFBundleDisplayName") or info.get("CFBundleName", ""),
            bundle=info.get("CFBundleIdentifier", ""),
            bundleVersion=info.get("CFBundleShortVersionString", ""),
            minOs=info.get("MinimumOSVersion", ""),
        )
        if not row["version"]:
            row["version"] = row["bundleVersion"]

        name = best_icon(zf, app_dir, info)
        row["icon"] = name.rsplit("/", 1)[-1] if name else ""
        if name and icons_dir:
            png = from_apple_png(zf.read(name))
            width, height = png_size(png)
            target = icons_dir / (path.stem + ".png")
            target.write_bytes(png)
            row["iconPx"] = "%dx%d" % (width, height)
            row["iconFile"] = target.name
            row["color"] = main_color(png)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Разбор .ipa для каталога")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--icons", type=Path, help="куда класть иконки")
    parser.add_argument("--json", action="store_true", help="печатать JSON вместо таблицы")
    args = parser.parse_args()

    if args.icons:
        args.icons.mkdir(parents=True, exist_ok=True)

    rows = []
    for path in sorted(args.folder.glob("*.ipa")):
        try:
            rows.append(scan(path, args.icons))
        except Exception as exc:  # noqa: BLE001 — чужие архивы, причина важнее типа
            rows.append({"file": path.name, "error": "%s: %s" % (type(exc).__name__, exc)})

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            if "error" in row:
                print("%-22s ОШИБКА %s" % (row["file"][:22], row["error"]))
                continue
            print("%-22s %-10s %7.1f МБ  iOS %-5s %s %s" % (
                row["title"][:22], row["version"][:10], row["sizeMb"],
                row["minOs"], row.get("iconPx", "—"), row.get("iconFile", ""),
            ))
    total = sum(r.get("sizeMb", 0) for r in rows)
    print("\nфайлов: %d, суммарно: %.1f МБ" % (len(rows), total), file=sys.stderr)


if __name__ == "__main__":
    main()
