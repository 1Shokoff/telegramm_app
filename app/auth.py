from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class AuthError(Exception):
    """initData не прошла проверку — запрос отклоняем."""


@dataclass(frozen=True)
class WebAppUser:
    id: int
    first_name: str
    username: str | None
    language_code: str | None


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def check_init_data(init_data: str, bot_token: str, ttl: int = 86400) -> WebAppUser:
    """Проверяет подпись Telegram Mini App и возвращает пользователя.

    Порядок по документации Telegram: все поля кроме hash сортируются
    по ключу, склеиваются через \\n и подписываются HMAC-SHA256.
    """
    if not init_data:
        raise AuthError("пустая initData")

    # strict_parsing=False: чужие/новые поля не должны ломать вход, они всё равно в подписи.
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    if not received:
        raise AuthError("нет поля hash")

    check_string = "\n".join("%s=%s" % (k, pairs[k]) for k in sorted(pairs))
    expected = hmac.new(_secret_key(bot_token), check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("подпись не совпала")

    if ttl > 0:
        try:
            auth_date = int(pairs.get("auth_date", "0"))
        except ValueError:
            raise AuthError("некорректный auth_date")
        if auth_date <= 0 or time.time() - auth_date > ttl:
            raise AuthError("подпись устарела, перезапустите приложение")

    raw_user = pairs.get("user")
    if not raw_user:
        raise AuthError("нет данных пользователя")
    try:
        data = json.loads(raw_user)
        user_id = int(data["id"])
    except (ValueError, KeyError, TypeError):
        raise AuthError("не разобрали user")

    return WebAppUser(
        id=user_id,
        first_name=str(data.get("first_name") or ""),
        username=data.get("username"),
        language_code=data.get("language_code"),
    )
