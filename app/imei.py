from __future__ import annotations

import re

TEST_IMEI = "490154203237518"
_DIGITS = re.compile(r"\D+")


def normalize(raw: str) -> str:
    """Оставляет только цифры: покупатель может вставить номер с пробелами и дефисами."""
    return _DIGITS.sub("", raw or "")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def validate(raw: str) -> tuple[str | None, str | None]:
    """Возвращает (нормализованный IMEI, текст ошибки)."""
    digits = normalize(raw)
    if not digits:
        return None, "Отправьте номер IMEI — 15 цифр."
    if len(digits) == 14:
        return None, "Это 14 цифр. Нужен полный IMEI из 15 цифр."
    if len(digits) == 16 or len(digits) == 17:
        return None, "Похоже, это EID или серийный номер. Нужен IMEI из 15 цифр."
    if len(digits) != 15:
        return None, f"В номере {len(digits)} цифр, а нужно 15."
    if not luhn_ok(digits):
        return None, "Номер не проходит проверку контрольной суммы. Сверьте цифры."
    return digits, None


def mask(imei: str | None) -> str:
    if not imei:
        return "—"
    return "•" * (len(imei) - 4) + imei[-4:]
