"""Аналитика продаж для продавца: бот (/stats) и Mini App считают здесь.

Продажа — заказ с подтверждённой оплатой (paid_at), дата продажи — дата
подтверждения. Не считаются тестовые заказы (TEST_IDS) и демо-оплата.
Заказ, отменённый после оплаты, — тоже продажа, но показывается отдельной
строкой: деньги по нему, скорее всего, вернули.

Выручка — сумма цен заказов: цена хранится в заказе на момент оплаты, поэтому
смена PRICE_RUB и акции историю не меняет.

Границы периодов — по Москве; в базе время в UTC, поэтому дни переводим здесь.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from . import const, db
from .config import MONTHS_GENITIVE, MSK, today_msk

# Ключ периода → название. «custom» — свои даты.
PERIODS = {
    "today": "Сегодня",
    "7d": "7 дней",
    "30d": "30 дней",
    "month": "Этот месяц",
    "all": "Всё время",
}

# Дольше этого — график по месяцам, иначе столбики не разглядеть.
DAILY_LIMIT = 62

# Раньше этой даты аналитики нет. Опечатка в годе (0202 вместо 2026) иначе
# давала график на восемнадцать веков — 22 тысячи столбиков вешали витрину,
# а год 0001 ронял сервер.
MIN_DATE = date(2020, 1, 1)

# Продажа: оплата подтверждена, заказ не тестовый и не демо.
_SALE = "paid_at IS NOT NULL AND is_test = 0 AND payment_mode != 'demo'"


class PeriodError(ValueError):
    """Период не разобрать — текст ошибки показываем продавцу."""


@dataclass(frozen=True)
class Period:
    key: str
    title: str
    start: date | None  # первый день, включительно
    end: date | None  # последний день, включительно

    @property
    def bounds(self) -> tuple[str | None, str | None]:
        """Границы в формате базы (UTC ISO): [начало первого дня, начало дня после последнего)."""
        return _utc(self.start), _utc(self.end + timedelta(days=1) if self.end else None)

    @property
    def dates_text(self) -> str:
        if not self.start:
            return "за всё время"
        if self.start == self.end:
            return _day(self.start)
        return "%s — %s" % (_day(self.start), _day(self.end))


def _utc(day: date | None) -> str | None:
    if day is None:
        return None
    moment = datetime.combine(day, time(0, 0), tzinfo=MSK).astimezone(timezone.utc)
    return moment.isoformat(timespec="seconds")


def _day(day: date) -> str:
    return "%d %s" % (day.day, MONTHS_GENITIVE[day.month - 1])


def _ddmmyyyy(day: date) -> str:
    # Вручную, а не strftime: год 202 тот печатает то «202», то «0202».
    return "%02d.%02d.%04d" % (day.day, day.month, day.year)


def check_day(day: date, today: date | None = None) -> date:
    """День, за который бывает аналитика: с MIN_DATE по сегодня (по Москве)."""
    today = today or today_msk()
    if day < MIN_DATE:
        raise PeriodError(
            "Дата %s раньше %s — проверьте год." % (_ddmmyyyy(day), _ddmmyyyy(MIN_DATE))
        )
    if day > today:
        raise PeriodError("Дата %s ещё не наступила." % _ddmmyyyy(day))
    return day


def period(key: str = "all", date_from: date | None = None, date_to: date | None = None,
           today: date | None = None) -> Period:
    """Период по ключу или по своим датам (обе включительно).

    Свои даты — с MIN_DATE по сегодня; конец в будущем обрезается до сегодня.
    """
    today = today or today_msk()
    if key == "custom":
        if not date_from and not date_to:
            raise PeriodError("Укажите хотя бы одну дату периода.")
        for day in (date_from, date_to):
            if day is not None and day < MIN_DATE:
                check_day(day, today)
        # Начало проверяем до перестановки: «с» в будущем без «по» иначе
        # молча превратилось бы в «сегодня».
        if date_from is not None:
            check_day(date_from, today)
        start = date_from or date_to
        end = date_to or today
        if start > end:
            start, end = end, start
        check_day(start, today)
        return Period("custom", "Свои даты", start, min(end, today))
    if key == "today":
        return Period(key, PERIODS[key], today, today)
    if key == "7d":
        return Period(key, PERIODS[key], today - timedelta(days=6), today)
    if key == "30d":
        return Period(key, PERIODS[key], today - timedelta(days=29), today)
    if key == "month":
        return Period(key, PERIODS[key], today.replace(day=1), today)
    if key == "all":
        return Period(key, PERIODS[key], None, None)
    raise PeriodError("Неизвестный период: %s" % key)


_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?$")
_PAIR = re.compile(r"^(\d{1,2}\.\d{1,2}(?:\.\d{4})?)\s*-\s*(\d{1,2}\.\d{1,2}(?:\.\d{4})?)$")


def parse_date(raw: str, today: date | None = None) -> date:
    """«20.10.2026», «20.10» (текущий год) или «2026-10-20»."""
    raw = raw.strip()
    try:
        if "-" in raw and len(raw) == 10:
            return date.fromisoformat(raw)
        match = _DATE.match(raw)
        if match:
            year = int(match.group(3)) if match.group(3) else (today or today_msk()).year
            return date(year, int(match.group(2)), int(match.group(1)))
    except ValueError:
        pass
    raise PeriodError("Дата пишется как 20.10.2026: «%s» не разобрать." % raw)


# Слова, которыми продавец задаёт период в /stats.
_WORDS = {
    "today": "today", "сегодня": "today",
    "7d": "7d", "7": "7d", "неделя": "7d",
    "30d": "30d", "30": "30d",
    "month": "month", "месяц": "month",
    "all": "all", "всё": "all", "все": "all",
}


def period_from_text(text: str, today: date | None = None) -> Period:
    """Аргумент /stats: слово периода или даты «01.10-20.10», «01.10.2026-20.10.2026»."""
    raw = (text or "").strip().lower()
    if not raw:
        return period("all", today=today)
    if raw in _WORDS:
        return period(_WORDS[raw], today=today)
    # Разделитель дат: «..», тире, « - » или дефис между «дд.мм». Дефисы внутри
    # ISO-даты (2026-10-01) разделителем не считаются.
    if ".." in raw:
        parts = raw.split("..")
    elif "—" in raw or "–" in raw:
        parts = re.split(r"[—–]", raw)
    elif " - " in raw:
        parts = raw.split(" - ")
    else:
        pair = _PAIR.match(raw)
        parts = [pair.group(1), pair.group(2)] if pair else [raw]
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) == 2:
        return period("custom", parse_date(parts[0], today), parse_date(parts[1], today), today=today)
    if len(parts) == 1:
        day = parse_date(parts[0], today)
        return period("custom", day, day, today=today)
    raise PeriodError("Период не разобрать: «%s»." % text)


def _range(column: str, bounds: tuple[str | None, str | None]) -> tuple[str, list]:
    sql, args = "", []
    start, end = bounds
    if start:
        sql += " AND %s >= ?" % column
        args.append(start)
    if end:
        sql += " AND %s < ?" % column
        args.append(end)
    return sql, args


def _percent(part: int, total: int) -> int:
    return round(part * 100 / total) if total else 0


async def report(p: Period) -> dict:
    """Продажи, выручка и заказы по статусам за период."""
    bounds = p.bounds
    paid_sql, paid_args = _range("paid_at", bounds)

    sales = await db._fetchone(
        "SELECT COUNT(*) AS n, COALESCE(SUM(price_rub), 0) AS rub FROM orders WHERE %s%s" % (_SALE, paid_sql),
        paid_args,
    )
    cancelled = await db._fetchone(
        "SELECT COUNT(*) AS n, COALESCE(SUM(price_rub), 0) AS rub FROM orders "
        "WHERE %s AND status = ?%s" % (_SALE, paid_sql),
        [const.CANCELLED, *paid_args],
    )
    count, revenue = int(sales["n"]), int(sales["rub"])
    lost_count, lost_revenue = int(cancelled["n"]), int(cancelled["rub"])

    # Заказы, оформленные за период, по текущему статусу — воронка периода.
    created_sql, created_args = _range("created_at", bounds)
    rows = await db._fetchall(
        "SELECT status, paid_at IS NOT NULL AS was_paid, COUNT(*) AS n FROM orders "
        "WHERE is_test = 0 AND payment_mode != 'demo'%s GROUP BY status, was_paid" % created_sql,
        created_args,
    )
    by_status: dict[str, int] = {}
    created = paid_of_created = 0
    for row in rows:
        n = int(row["n"])
        created += n
        if row["was_paid"]:
            paid_of_created += n
        key = row["status"]
        if key == const.CANCELLED:
            key = "cancelled_paid" if row["was_paid"] else const.CANCELLED
        by_status[key] = by_status.get(key, 0) + n

    titles = dict(const.TITLES)
    titles[const.CANCELLED] = "Отменён до оплаты"
    titles["cancelled_paid"] = "Отменено после оплаты"
    order = [s for s in const.TITLES if s != const.CANCELLED] + [const.CANCELLED, "cancelled_paid"]
    statuses = [
        {"status": s, "title": titles[s], "count": by_status[s], "percent": _percent(by_status[s], created)}
        for s in order if by_status.get(s)
    ]

    return {
        "period": {
            "key": p.key,
            "title": p.title,
            "from": p.start.isoformat() if p.start else None,
            "to": p.end.isoformat() if p.end else None,
            "datesText": p.dates_text,
        },
        "sales": {"count": count, "revenue": revenue, "average": round(revenue / count) if count else 0},
        "cancelledAfterPaid": {"count": lost_count, "revenue": lost_revenue},
        "net": {"count": count - lost_count, "revenue": revenue - lost_revenue},
        "created": created,
        "conversion": _percent(paid_of_created, created),
        "statuses": statuses,
        "series": await _series(p, paid_sql, paid_args),
    }


_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
MONTHS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)
# Больше этого списком не показываем — для отчёта хватает, телефон не задыхается.
SALES_LIMIT = 500


async def sales(p: Period | None = None, day: date | None = None, month: str | None = None) -> dict:
    """Продажи списком: за период, за день или за месяц графика (по Москве).

    Те же заказы, что в карточках «Продажи» и «Выручка», включая отменённые
    после оплаты. Свежие сверху.
    """
    if day is not None:
        check_day(day)
        title, dates = "Продажи за %s" % _day(day), _ddmmyyyy(day)
        bounds = (_utc(day), _utc(day + timedelta(days=1)))
    elif month is not None:
        match = _MONTH.match(month)
        if not match or not 1 <= int(match.group(2)) <= 12:
            raise PeriodError("Месяц пишется как 2026-10: «%s» не разобрать." % month)
        start = date(int(match.group(1)), int(match.group(2)), 1)
        if start < MIN_DATE.replace(day=1) or start > today_msk():
            raise PeriodError("Месяц %s вне аналитики — проверьте год." % month)
        title, dates = "Продажи за %s %d" % (MONTHS[start.month - 1], start.year), ""
        bounds = (_utc(start), _utc((start + timedelta(days=32)).replace(day=1)))
    else:
        p = p or period("all")
        title, dates = "Продажи · %s" % p.title, p.dates_text
        bounds = p.bounds
    paid_sql, paid_args = _range("o.paid_at", bounds)
    rows = await db._fetchall(
        "SELECT o.*, u.username, u.first_name FROM orders o LEFT JOIN users u ON u.tg_id = o.user_id "
        "WHERE o.paid_at IS NOT NULL AND o.is_test = 0 AND o.payment_mode != 'demo'%s "
        "ORDER BY o.paid_at DESC, o.id DESC LIMIT %d" % (paid_sql, SALES_LIMIT),
        paid_args,
    )
    return {
        "title": title,
        "datesText": dates,
        "count": len(rows),
        "revenue": sum(int(r["price_rub"]) for r in rows),
        "rows": rows,
    }


async def _series(p: Period, paid_sql: str, paid_args: list) -> dict:
    """Продажи по дням (или по месяцам для длинного периода) — для графика."""
    start, end = p.start, p.end
    if start is None:
        # «Всё время»: от первой продажи до сегодня.
        first = await db._fetchone("SELECT MIN(paid_at) AS first FROM orders WHERE %s" % _SALE)
        if not first or not first["first"]:
            return {"unit": "day", "points": []}
        start = datetime.fromisoformat(first["first"]).astimezone(MSK).date()
        end = today_msk()
    unit = "day" if (end - start).days + 1 <= DAILY_LIMIT else "month"
    # В базе UTC: «+3 hours» переводит момент оплаты в московские сутки.
    bucket = "date(paid_at, '+3 hours')" if unit == "day" else "strftime('%Y-%m', paid_at, '+3 hours')"
    rows = await db._fetchall(
        "SELECT %s AS bucket, COUNT(*) AS n, COALESCE(SUM(price_rub), 0) AS rub FROM orders "
        "WHERE %s%s GROUP BY bucket ORDER BY bucket" % (bucket, _SALE, paid_sql),
        paid_args,
    )
    found = {row["bucket"]: (int(row["n"]), int(row["rub"])) for row in rows}
    # Пустые дни и месяцы тоже нужны: без них график врёт о темпе продаж.
    labels = []
    if unit == "day":
        day = start
        while day <= end:
            labels.append(day.isoformat())
            day += timedelta(days=1)
    else:
        month = start.replace(day=1)
        while month <= end:
            labels.append(month.strftime("%Y-%m"))
            month = (month + timedelta(days=32)).replace(day=1)
    points = [{"label": k, "count": found.get(k, (0, 0))[0], "revenue": found.get(k, (0, 0))[1]} for k in labels]
    return {"unit": unit, "points": points}
