"""UDID iPhone: проверяем символы, а не длину.

Форматов несколько: 40 hex-символов у старых iPhone и 25 символов вида
00008150-000914312E78C01C (8 hex, дефис, 16 hex) у iPhone XS и новее.
Встречаются и другие длины, поэтому длина не проверяется — только символы:
цифры, латинские буквы A–F в любом регистре и дефис.

Номер храним ровно таким, каким его прислал покупатель, убрав лишь пробелы
и переносы: дефис и регистр нужны тому, кто регистрирует устройство.
"""
from __future__ import annotations

import re

# Столько вмещает поле ввода в витрине.
LIMIT = 40

# Пример из документации Apple: это хеш, а не идентификатор чьего-то устройства.
TEST_UDID = "2b6f0cc904d137be2e1730235f5664094b831186"

_SPACE = re.compile(r"\s+")
_ALLOWED = re.compile(r"[0-9A-Fa-f-]+")

# В чате бота номер отличаем от вопроса продавцу по виду сообщения. Настоящий
# UDID — 25 или 40 символов; строка короче скорее вопрос, чем номер.
ATTEMPT_MIN = 16


def normalize(raw: str | None) -> str:
    """Убирает пробелы и переносы: номер часто копируют кусками."""
    return _SPACE.sub("", raw or "")


def validate(raw: str | None) -> tuple[str | None, str | None]:
    """Возвращает (UDID как есть, без пробелов; текст ошибки)."""
    value = normalize(raw)
    if not value:
        return None, "Отправьте UDID устройства."
    if not _ALLOWED.fullmatch(value) or not value.strip("-"):
        return None, "В UDID бывают только цифры, латинские буквы от A до F и дефис."
    if len(value) > LIMIT:
        return None, "В номере %d символов — UDID не бывает длиннее %d." % (len(value), LIMIT)
    return value, None


def looks_like_attempt(raw: str | None) -> bool:
    """Похоже ли сообщение на попытку прислать номер, а не на вопрос продавцу.

    Нужно, чтобы на шаге ввода UDID обычный текст уходил в переписку,
    а не получал в ответ «в UDID бывают только цифры».
    """
    value = normalize(raw)
    if len(value) < ATTEMPT_MIN:
        return False
    return bool(_ALLOWED.fullmatch(value))


def mask(udid: str | None) -> str:
    """Короткий вид для покупателя: номер целиком показывать незачем."""
    if not udid:
        return "—"
    if len(udid) <= 12:
        return udid
    return "%s…%s" % (udid[:6], udid[-4:])
