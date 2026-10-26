"""Промокоды: правила, проверка полей, расчёт цены и действия продавца.

Промокоды создают, правят и удаляют продавцы (SELLER_IDS): в Mini App —
раздел «Промокоды» во вкладке «Продавец», в боте — /promos и /promo КОД.
Витрина промокоды не показывает: публикуем их сами, где нужно.

Покупатель вводит промокод при оформлении и на шаге оплаты — в Mini App и
в боте; само применение — OrderService.apply_promo. Во время акции промокод
считается от цены по акции: 3 000 ₽ − 50% = 1 500 ₽, промокод 20% → 1 200 ₽.
Бесплатных промокодов нет: процент — до 99, скидка в рублях — меньше цены.

Применение занимается сразу, как покупатель применил код, и возвращается,
если он убрал промокод или заказ отменили до оплаты. Применённый промокод
остаётся за заказом, даже если его потом выключили или срок вышел.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import date, datetime

from . import const, db
from .config import Config, today_msk
from .texts import money, plural

PERCENT = "percent"
RUB = "rub"
KINDS = (PERCENT, RUB)
MAX_PERCENT = 99
# Больше этого в лимитах не бывает — защита от лишнего нуля.
LIMIT_MAX = 1_000_000
NOTE_LIMIT = 300
CODE_MIN, CODE_MAX = 4, 32

# Статус не хранится — его дают выключатель, даты и счётчик применений.
ACTIVE = "active"
SCHEDULED = "scheduled"
EXPIRED = "expired"
EXHAUSTED = "exhausted"
DISABLED = "disabled"
STATUSES = {
    ACTIVE: "Активен",
    SCHEDULED: "Ещё не начался",
    EXPIRED: "Истёк",
    EXHAUSTED: "Исчерпан",
    DISABLED: "Выключен",
}

EVENT_TITLES = {
    "promo_created": "Создан",
    "promo_updated": "Изменён",
    "promo_enabled": "Включён",
    "promo_disabled": "Выключен",
    "promo_applied": "Применён к заказу",
    "promo_released": "Применение возвращено",
}

# Что видит покупатель, когда промокод не применился.
NOT_FOUND = "Такого промокода нет — проверьте, как он написан."
PER_USER = "Вы уже использовали этот промокод."
TOO_BIG = "Промокод не подходит к цене этого заказа."
REFUSALS = {
    DISABLED: "Промокод не действует.",
    EXPIRED: "Срок действия промокода истёк.",
    EXHAUSTED: "Промокод закончился: все применения уже использованы.",
}

# Случайный код вида IAPKI-7KQT2X-C9S9WL. Без 0/O и 1/I/L: их путают,
# когда переписывают код с картинки или из сторис.
PREFIX = "IAPKI"
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 2, 6

_CODE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_SPACE = re.compile(r"[\s​-‍⁠﻿]+")
_DASHES = str.maketrans({ch: "-" for ch in "‐‑‒–—―−﹣－"})
# Русские буквы, которые не отличить от латинских, — для поиска по коду.
_LOOKALIKES = str.maketrans("АВЕКМНОРСТХУ", "ABEKMHOPCTXY")


class PromoError(ValueError):
    """Промокод не сохранить — текст показываем продавцу."""


class PromoNotFound(PromoError):
    pass


# ---------------------------------------------------------------- код


def generate() -> str:
    groups = ("".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN)) for _ in range(GROUPS))
    return "-".join((PREFIX, *groups))


def _tidy(raw: str) -> str:
    return _SPACE.sub("", raw or "").translate(_DASHES).upper()


def normalize(raw: str) -> str:
    """Код для поиска: без пробелов, заглавными, русские двойники — латиницей."""
    return _tidy(raw).translate(_LOOKALIKES)


def looks_like_code(raw: str) -> bool:
    """Сообщение похоже на промокод, а не на вопрос продавцу: одно «слово» из латиницы и цифр."""
    code = normalize(raw)
    return CODE_MIN <= len(code) <= CODE_MAX and bool(_CODE.match(code))


def refusal(promo: dict, today: date | None = None) -> str | None:
    """Почему промокод сейчас не применить; None — применить можно."""
    st = status(promo, today)
    if st == SCHEDULED:
        return "Промокод начнёт действовать %s." % _day(promo["starts_on"])
    return REFUSALS.get(st)


def clean_code(raw: object) -> str:
    """Код нового промокода. Кириллицу не переводим молча: продавец сам решит, как назвать."""
    code = _tidy(str(raw or ""))
    if not code:
        raise PromoError("Укажите код или нажмите «Сгенерировать».")
    if re.search("[А-ЯЁ]", code):
        raise PromoError("Код пишется латиницей: русские буквы покупатель наберёт по-разному.")
    if not (CODE_MIN <= len(code) <= CODE_MAX) or not _CODE.match(code):
        raise PromoError(
            "Код — от %d до %d знаков: латинские буквы, цифры и дефис между ними." % (CODE_MIN, CODE_MAX)
        )
    return code


# ------------------------------------------------------------- поля


def _number(value: object, name: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise PromoError("%s — целое число." % name)
    try:
        return int(str(value).strip())
    except ValueError:
        raise PromoError("%s — целое число." % name)


def _date(value: object, name: str) -> str | None:
    """Дата из формы: 2026-11-30 (поле даты) или 30.11.2026. В базе — ISO."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    raise PromoError("%s: дата пишется как 30.11.2026." % name)


# Ключ из витрины → колонка в базе.
_KEYS = {
    "kind": "kind", "value": "value", "startsOn": "starts_on", "endsOn": "ends_on",
    "maxUses": "max_uses", "perUser": "per_user", "enabled": "enabled", "note": "note",
}
_DEFAULTS = {"per_user": 1, "enabled": 1}


def clean(data: dict, cfg: Config, old: dict | None = None, today: date | None = None) -> dict:
    """Поля из формы → значения для базы.

    old — промокод до правки: чего нет в запросе, берём из него. Срок, который
    уже прошёл, не задать, но правка заметки у истёкшего промокода не мешает.
    """
    today = today or today_msk()

    def raw(key: str):
        column = _KEYS[key]
        if key in data:
            return data[key]
        if old is not None:
            return old[column]
        return _DEFAULTS.get(column)

    kind = str(raw("kind") or "")
    if kind not in KINDS:
        raise PromoError("Выберите, в чём скидка: в процентах или в рублях.")
    value = _number(raw("value"), "Скидка")
    if value is None:
        raise PromoError("Укажите размер скидки.")
    if kind == PERCENT and not 1 <= value <= MAX_PERCENT:
        raise PromoError("Скидка в процентах — от 1 до %d: бесплатных промокодов нет." % MAX_PERCENT)
    if kind == RUB and not 1 <= value < cfg.current_price:
        raise PromoError(
            "Скидка в рублях — меньше цены, а сейчас она %s: бесплатных промокодов нет."
            % money(cfg.current_price)
        )

    starts_on = _date(raw("startsOn"), "Начало")
    ends_on = _date(raw("endsOn"), "Окончание")
    if starts_on and ends_on and ends_on < starts_on:
        raise PromoError("Окончание раньше начала — проверьте даты.")
    if ends_on and ends_on < today.isoformat() and (old is None or ends_on != old["ends_on"]):
        raise PromoError("Дата окончания уже прошла.")

    max_uses = _number(raw("maxUses"), "Всего применений")
    per_user = _number(raw("perUser"), "На одного покупателя")
    for number, name in ((max_uses, "Всего применений"), (per_user, "На одного покупателя")):
        if number is not None and not 1 <= number <= LIMIT_MAX:
            raise PromoError("%s — от 1 до %d или пусто, если без лимита." % (name, LIMIT_MAX))
    if max_uses is not None and per_user is not None and per_user > max_uses:
        raise PromoError("На одного покупателя не может быть больше применений, чем всего.")
    used = old["used"] if old else 0
    if max_uses is not None and max_uses < used:
        raise PromoError("Промокод уже применили %s — лимит не может быть меньше." % plural(used, "раз", "раза", "раз"))

    note = str(raw("note") or "").strip()
    if len(note) > NOTE_LIMIT:
        raise PromoError("Заметка — до %d знаков." % NOTE_LIMIT)

    return {
        "kind": kind,
        "value": value,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "max_uses": max_uses,
        "per_user": per_user,
        "enabled": 1 if raw("enabled") else 0,
        "note": note or None,
    }


# ------------------------------------------------------- цена и статус


def discount(price: int, kind: str, value: int) -> int:
    """Сколько рублей снимает промокод. Проценты округляем до рубля."""
    if kind == PERCENT:
        return (price * value + 50) // 100
    return value


def final_price(price: int, promo: dict) -> int | None:
    """Цена с промокодом. None — промокод снял бы всю цену, а бесплатных нет."""
    rest = price - discount(price, promo["kind"], promo["value"])
    return rest if rest > 0 else None


def status(promo: dict, today: date | None = None) -> str:
    """Выключен → истёк → исчерпан → ещё не начался → активен."""
    day = (today or today_msk()).isoformat()
    if not promo["enabled"]:
        return DISABLED
    if promo["ends_on"] and promo["ends_on"] < day:
        return EXPIRED
    if promo["max_uses"] is not None and promo["used"] >= promo["max_uses"]:
        return EXHAUSTED
    if promo["starts_on"] and promo["starts_on"] > day:
        return SCHEDULED
    return ACTIVE


# ---------------------------------------------------------------- текст


def _day(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def value_text(promo: dict) -> str:
    return "%d%%" % promo["value"] if promo["kind"] == PERCENT else money(promo["value"])


def period_text(promo: dict) -> str:
    starts, ends = promo["starts_on"], promo["ends_on"]
    if starts and ends:
        return "с %s по %s" % (_day(starts), _day(ends))
    if ends:
        return "до %s включительно" % _day(ends)
    if starts:
        return "с %s, бессрочно" % _day(starts)
    return "бессрочно"


def limit_text(value: int | None) -> str:
    return "без лимита" if value is None else str(value)


def uses_text(promo: dict) -> str:
    if promo["max_uses"] is None:
        return "%d · без лимита" % promo["used"]
    return "%d из %d" % (promo["used"], promo["max_uses"])


def per_user_text(promo: dict) -> str:
    if promo["per_user"] is None:
        return "без ограничений"
    return plural(promo["per_user"], "раз", "раза", "раз")


def describe(fields: dict) -> str:
    """Что задали при создании — первая строка истории."""
    promo = dict(fields, used=0)
    return "скидка %s · %s · всего: %s · на покупателя: %s" % (
        value_text(promo), period_text(promo), limit_text(promo["max_uses"]), per_user_text(promo)
    )


def changes(old: dict, new: dict) -> list[str]:
    """Было → стало по каждому изменённому полю — для истории промокода."""
    after = dict(old, **new)
    out = []
    if value_text(old) != value_text(after):
        out.append("скидка %s → %s" % (value_text(old), value_text(after)))
    if period_text(old) != period_text(after):
        out.append("срок: %s → %s" % (period_text(old), period_text(after)))
    if old["max_uses"] != after["max_uses"]:
        out.append("всего: %s → %s" % (limit_text(old["max_uses"]), limit_text(after["max_uses"])))
    if old["per_user"] != after["per_user"]:
        out.append("на покупателя: %s → %s" % (per_user_text(old), per_user_text(after)))
    if bool(old["enabled"]) != bool(after["enabled"]):
        out.append("включён" if after["enabled"] else "выключен")
    if (old["note"] or "") != (after["note"] or ""):
        out.append("заметка изменена")
    return out


def prices(promo: dict, cfg: Config) -> list[dict]:
    """Сколько заплатит покупатель с промокодом: сейчас, а во время акции — и после неё."""
    rows = [("Сейчас, по акции" if cfg.promo_active() else "Сейчас", cfg.current_price)]
    if cfg.promo_active():
        rows.append(("После акции", cfg.price_rub))
    out = []
    for title, price in rows:
        final = final_price(price, promo)
        out.append({
            "title": title,
            "price": price,
            "priceText": money(price),
            "final": final,
            "finalText": money(final) if final else None,
        })
    return out


def public(promo: dict, cfg: Config, today: date | None = None) -> dict:
    """Промокод для витрины продавца и карточки в боте."""
    st = status(promo, today)
    return {
        "id": promo["id"],
        "code": promo["code"],
        "kind": promo["kind"],
        "value": promo["value"],
        "valueText": value_text(promo),
        "startsOn": promo["starts_on"],
        "endsOn": promo["ends_on"],
        "periodText": period_text(promo),
        "maxUses": promo["max_uses"],
        "perUser": promo["per_user"],
        "used": promo["used"],
        "usesText": uses_text(promo),
        "perUserText": per_user_text(promo),
        "enabled": bool(promo["enabled"]),
        "note": promo["note"],
        "status": st,
        "statusTitle": STATUSES[st],
        "createdAt": promo["created_at"],
        "updatedAt": promo["updated_at"],
        # Применённый промокод только выключают: иначе из заказов пропадёт, откуда скидка.
        "canDelete": promo["used"] == 0,
    }


async def actor_name(actor: str) -> str:
    """seller:123 и user:123 → @username, а если его не знаем — имя или номер."""
    kind, _, raw_id = actor.partition(":")
    if kind not in ("seller", "user") or not raw_id.isdigit():
        return {"system": "бот"}.get(actor, actor)
    if kind == "user" and int(raw_id) == db.ANON_USER_ID:
        return db.ANON_NAME
    user = await db.get_user(int(raw_id)) or {}
    if user.get("username"):
        return "@" + user["username"]
    return user.get("first_name") or ("продавец %s" if kind == "seller" else "покупатель %s") % raw_id


def _buyer(order: dict) -> str:
    if order["user_id"] == db.ANON_USER_ID:
        return db.ANON_NAME
    if order.get("username"):
        return "@" + order["username"]
    return order.get("first_name") or "ID %d" % order["user_id"]


async def details(promo: dict, cfg: Config, today: date | None = None) -> dict:
    """Карточка промокода целиком: цены с ним, заказы, кто создал и история изменений."""
    data = public(promo, cfg, today)
    data["prices"] = prices(promo, cfg)
    data["createdBy"] = await actor_name(promo["created_by"])
    data["orders"] = [
        {
            "id": o["id"],
            "code": o["code"],
            "status": o["status"],
            "statusTitle": const.TITLES.get(o["status"], o["status"]),
            "priceText": money(o["price_rub"]),
            "discountText": money(o["promo_discount"]),
            "buyer": _buyer(o),
            "paid": bool(o["paid_at"]),
        }
        for o in await db.list_promo_orders(promo["id"])
    ]
    names: dict[str, str] = {}
    history = []
    for ev in await db.list_promo_events(promo["id"]):
        if ev["actor"] not in names:
            names[ev["actor"]] = await actor_name(ev["actor"])
        history.append({
            "title": EVENT_TITLES.get(ev["type"], ev["type"]),
            "detail": ev["detail"],
            "actor": names[ev["actor"]],
            "at": ev["created_at"],
        })
    data["history"] = history
    return data


# ------------------------------------------------------------ действия


async def get(promo_id: int) -> dict:
    promo = await db.get_promocode(promo_id)
    if not promo:
        raise PromoNotFound("Промокод не найден — возможно, его уже удалили.")
    return promo


async def find(raw: str) -> dict | None:
    return await db.get_promocode_by_code(normalize(raw)) if raw else None


async def listing(cfg: Config, status_key: str = "all", today: date | None = None) -> dict:
    """Промокоды с отбором по статусу и счётчики для фильтров."""
    items = [public(row, cfg, today) for row in await db.list_promocodes()]
    counts = {key: 0 for key in STATUSES}
    for item in items:
        counts[item["status"]] += 1
    shown = items if status_key == "all" else [item for item in items if item["status"] == status_key]
    return {"promocodes": shown, "counts": counts, "total": len(items)}


async def create(data: dict, cfg: Config, actor: str, today: date | None = None) -> dict:
    code = clean_code(data.get("code"))
    if await db.get_promocode_by_code(code):
        raise PromoError("Промокод %s уже есть — придумайте другой код." % code)
    fields = clean(data, cfg, today=today)
    try:
        return await db.create_promocode(code, fields, actor, describe(fields))
    except sqlite3.IntegrityError:
        # Второй продавец успел создать такой же код между проверкой и записью.
        raise PromoError("Промокод %s уже есть — придумайте другой код." % code)


async def update(promo_id: int, data: dict, cfg: Config, actor: str, today: date | None = None) -> dict:
    old = await get(promo_id)
    if "code" in data and _tidy(str(data["code"] or "")) != old["code"]:
        raise PromoError("Код после создания не меняется — для другого кода создайте новый промокод.")
    fields = clean(data, cfg, old, today)
    changed = {key: value for key, value in fields.items() if old[key] != value}
    if not changed:
        return old
    promo = await db.update_promocode(promo_id, changed, actor, "promo_updated", "; ".join(changes(old, changed)))
    assert promo is not None
    return promo


async def set_enabled(promo_id: int, enabled: bool, actor: str) -> dict:
    old = await get(promo_id)
    if bool(old["enabled"]) == enabled:
        return old
    promo = await db.update_promocode(
        promo_id, {"enabled": int(enabled)}, actor, "promo_enabled" if enabled else "promo_disabled", None
    )
    assert promo is not None
    return promo


async def remove(promo_id: int, actor: str) -> dict:
    """Удаляет неприменённый промокод. Возвращает, каким он был."""
    old = await get(promo_id)
    refused = PromoError("Промокод уже применяли — удалить нельзя, только выключить.")
    if old["used"]:
        raise refused
    if not await db.delete_promocode(promo_id, actor, "%s · скидка %s" % (old["code"], value_text(old))):
        raise refused
    return old
