"""Что внутри .ipa: описание для каталога и иконка для витрины.

    python tools/ipa_scan.py <папка с .ipa>                 — таблица и JSON
    python tools/ipa_scan.py <папка> --icons webapp/icons   — ещё и иконки

Архив целиком не распаковывается: читаем Info.plist и одну картинку.

Иконки в сборках лежат в формате Apple (чанк CgBI), браузер их не открывает —
пересборку в обычный PNG делает app/bundle.py, тот же код, что у витрины.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.bundle import app_info, best_icon, from_apple_png, main_color, png_size  # noqa: E402


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
