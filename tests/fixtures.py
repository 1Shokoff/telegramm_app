"""Сборки-пустышки для тестов: витрина собирается из них так же, как из настоящих."""
from __future__ import annotations

import plistlib
import shutil
import struct
import tempfile
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Каталог из catalog.example.toml: слаг → версия, как файлы лежат на сервере.
BUILDS = {
    "sber": "17.6.1",
    "tbank": "6.13.5",
    "ozon-bank": "19.27.0",
    "mts-money": "2.0.0",
    "max": "26.17.3",
    "vk-messenger": "6.9.5",
    "vk-video": "7.8.5",
    "yandex-music": "792",
    "spotify": "9.1.66",
    "aldente": "4.00.0",
    "yandex": "26.6.6.489",
    "yandex-go": "700.152.0",
    "cloud-mail": "9.92.0",
    "yota": "13.90",
    "v2guard": "1.0.8",
}


def _chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def apple_png(width: int = 4, height: int = 4, rgba: tuple = (33, 160, 56, 255)) -> bytes:
    """Иконка в формате Apple: чанк CgBI, каналы BGRA, поток без заголовка zlib."""
    r, g, b, a = rgba
    raw = (b"\x00" + bytes([b, g, r, a]) * width) * height
    packer = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    data = packer.compress(raw) + packer.flush()
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"CgBI", b"\x50\x00\x20\x06")
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", data)
        + _chunk(b"IEND", b"")
    )


def make_ipa(
    path: Path,
    *,
    name: str = "Test",
    ru_name: str = "",
    version: str = "1.0.0",
    min_os: str = "16.0",
    icon: bool = False,
    filler: int = 2048,
) -> None:
    """Сборка-пустышка: Info.plist, по желанию русское название и иконка."""
    info = {
        "CFBundleIdentifier": "ru.test.app",
        "CFBundleDisplayName": name,
        "CFBundleShortVersionString": version,
        "MinimumOSVersion": min_os,
    }
    if icon:
        info["CFBundleIcons"] = {"CFBundlePrimaryIcon": {"CFBundleIconFiles": ["AppIcon60x60"]}}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Payload/Test.app/Info.plist", plistlib.dumps(info))
        zf.writestr("Payload/Test.app/Test", b"x" * filler)
        if ru_name:
            # Так локализацию кладёт Xcode: текстовый .strings в UTF-16 с BOM.
            text = '/* InfoPlist.strings */\n"CFBundleDisplayName" = "%s";\n' % ru_name
            zf.writestr("Payload/Test.app/ru.lproj/InfoPlist.strings", text.encode("utf-16"))
        if icon:
            zf.writestr("Payload/Test.app/AppIcon60x60@2x.png", apple_png())


def catalog_folder() -> Path:
    """Папка как на сервере: 15 сборок каталога и catalog.toml из примера."""
    folder = Path(tempfile.mkdtemp())
    for slug, version in BUILDS.items():
        make_ipa(folder / ("%s-%s.ipa" % (slug, version)), name=slug, version=version)
    shutil.copy(ROOT / "catalog.example.toml", folder / "catalog.toml")
    return folder
