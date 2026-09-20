from __future__ import annotations

import re

LENGTH = 40

# Пример из документации Apple: это хеш, а не идентификатор чьего-то устройства.
TEST_UDID = "2b6f0cc904d137be2e1730235f5664094b831186"

_SPACE = re.compile(r"\s+")
_LEGACY = re.compile(r"[0-9a-f]{40}")
# iPhone X и новее сообщают UDID из 25 символов: 8 hex, дефис, 16 hex.
_MODERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{16}")
_IMEI = re.compile(r"[0-9]{15}")


def normalize(raw: str) -> str:
    """Убирает пробелы и приводит к нижнему регистру: номер часто копируют с переносами."""
    return _SPACE.sub("", raw or "").lower()


def validate(raw: str) -> tuple[str | None, str | None]:
    """Возвращает (нормализованный UDID, текст ошибки)."""
    value = normalize(raw)
    if not value:
        return None, "Отправьте UDID — 40 символов."
    if _LEGACY.fullmatch(value):
        return value, None
    if _MODERN.fullmatch(value):
        return None, (
            "Это UDID нового формата из 25 символов. "
            "Для заказа нужен 40-значный — пришлите его или напишите в помощь."
        )
    if _IMEI.fullmatch(value):
        return None, "Это IMEI из 15 цифр. Нужен UDID из 40 символов."
    if len(value) != LENGTH:
        return None, "В номере %d символов, а нужно 40." % len(value)
    return None, "В UDID допустимы только цифры и латинские буквы от a до f."


def looks_like_attempt(raw: str) -> bool:
    """Похоже ли сообщение на попытку прислать номер, а не на вопрос продавцу.

    Нужно, чтобы на шаге ввода UDID обычный текст уходил в переписку,
    а не получал в ответ «в номере 12 символов».
    """
    value = normalize(raw)
    if len(value) < 20:
        return False
    body = value.replace("-", "")
    return all(ch in "0123456789abcdef" for ch in body)


def mask(udid: str | None) -> str:
    """Короткий вид для покупателя: 40 символов целиком показывать незачем."""
    if not udid:
        return "—"
    if len(udid) <= 12:
        return udid
    return "%s…%s" % (udid[:6], udid[-4:])
