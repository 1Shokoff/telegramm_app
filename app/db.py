from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import aiosqlite

from . import const

log = logging.getLogger(__name__)

_conn: aiosqlite.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    is_blocked  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT    NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES users(tg_id) ON DELETE CASCADE,
    status       TEXT    NOT NULL,
    price_rub    INTEGER NOT NULL,
    payment_mode TEXT    NOT NULL,
    app_slug     TEXT,
    payment_ref  TEXT,
    device_udid  TEXT,
    instruction  TEXT,
    seller_note  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    paid_at      TEXT,
    udid_at      TEXT,
    installed_at TEXT,
    done_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_user   ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_orders_code   ON orders(code);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    actor      TEXT NOT NULL,
    type       TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id, id);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    author      TEXT    NOT NULL,
    author_id   INTEGER NOT NULL,
    text        TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    seen_buyer  INTEGER NOT NULL DEFAULT 0,
    seen_seller INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_messages_order ON messages(order_id, id);

CREATE TABLE IF NOT EXISTS notify_prefs (
    user_id INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (user_id, kind)
);

CREATE TABLE IF NOT EXISTS notify_order_prefs (
    user_id  INTEGER NOT NULL,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    enabled  INTEGER NOT NULL,
    PRIMARY KEY (user_id, order_id)
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> aiosqlite.Connection:
    if _conn is None:
        raise RuntimeError("База не инициализирована: вызовите db.init()")
    return _conn


async def init(path: str) -> None:
    global _conn
    _conn = await aiosqlite.connect(path)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA journal_mode=WAL")
    await _conn.execute("PRAGMA foreign_keys=ON")
    await _conn.execute("PRAGMA busy_timeout=5000")
    await _conn.executescript(SCHEMA)
    await _conn.commit()
    await _migrate()
    log.info("База готова: %s", path)


async def _migrate() -> None:
    """Базы, созданные до перехода с IMEI на UDID, переименовывают свои колонки.

    CREATE TABLE IF NOT EXISTS существующую таблицу не трогает, поэтому старую
    схему нужно поправить отдельно. Операция идемпотентная.
    """
    columns = {row["name"] for row in await _fetchall("PRAGMA table_info(orders)")}
    renames = (("imei", "device_udid"), ("imei_at", "udid_at"))
    for old, new in renames:
        if old in columns and new not in columns:
            await conn().execute("ALTER TABLE orders RENAME COLUMN %s TO %s" % (old, new))
            log.info("Миграция: колонка %s переименована в %s", old, new)
    await conn().execute("UPDATE orders SET status = ? WHERE status = 'imei'", (const.UDID,))
    # Заказы до выбора отдельных приложений — это заказы всего каталога (NULL).
    if "app_slug" not in columns:
        await conn().execute("ALTER TABLE orders ADD COLUMN app_slug TEXT")
        log.info("Миграция: добавлена колонка orders.app_slug")
    await conn().commit()


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def _fetchone(sql: str, args: Iterable[Any] = ()) -> dict | None:
    async with conn().execute(sql, tuple(args)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _fetchall(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    async with conn().execute(sql, tuple(args)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- users


async def upsert_user(tg_id: int, username: str | None, first_name: str | None) -> None:
    ts = now()
    await conn().execute(
        """
        INSERT INTO users (tg_id, username, first_name, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tg_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name,
            updated_at = excluded.updated_at
        """,
        (tg_id, username, first_name, ts, ts),
    )
    await conn().commit()


async def get_user(tg_id: int) -> dict | None:
    return await _fetchone("SELECT * FROM users WHERE tg_id = ?", (tg_id,))


async def set_blocked(tg_id: int, blocked: bool) -> None:
    await conn().execute(
        "UPDATE users SET is_blocked = ?, updated_at = ? WHERE tg_id = ?",
        (1 if blocked else 0, now(), tg_id),
    )
    await conn().commit()


# --------------------------------------------------------------- orders


async def create_order(
    user_id: int, price_rub: int, payment_mode: str, prefix: str, app_slug: str | None = None
) -> dict:
    ts = now()
    cur = await conn().execute(
        """
        INSERT INTO orders (code, user_id, status, price_rub, payment_mode, app_slug, created_at, updated_at)
        VALUES ('', ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, const.NEW, price_rub, payment_mode, app_slug, ts, ts),
    )
    order_id = int(cur.lastrowid)
    code = "%s-%04d" % (prefix, order_id)
    await conn().execute("UPDATE orders SET code = ? WHERE id = ?", (code, order_id))
    await conn().commit()
    await add_event(order_id, "user:%d" % user_id, "created", None)
    order = await get_order(order_id)
    assert order is not None
    return order


async def set_order_product(order_id: int, app_slug: str | None, price_rub: int) -> dict | None:
    """Смена товара у неоплаченного заказа — покупатель передумал до оплаты."""
    await conn().execute(
        "UPDATE orders SET app_slug = ?, price_rub = ?, updated_at = ? WHERE id = ?",
        (app_slug, price_rub, now(), order_id),
    )
    await conn().commit()
    await add_event(order_id, "system", "product", app_slug or "catalog")
    return await get_order(order_id)


async def get_order(order_id: int) -> dict | None:
    return await _fetchone("SELECT * FROM orders WHERE id = ?", (order_id,))


async def get_order_by_code(code: str) -> dict | None:
    return await _fetchone("SELECT * FROM orders WHERE code = ? COLLATE NOCASE", (code,))


async def get_active_order(user_id: int) -> dict | None:
    """Последний незакрытый заказ пользователя, иначе последний вообще."""
    marks = ",".join("?" * len(const.OPEN_STATUSES))
    row = await _fetchone(
        "SELECT * FROM orders WHERE user_id = ? AND status IN (%s) ORDER BY id DESC LIMIT 1" % marks,
        (user_id, *const.OPEN_STATUSES),
    )
    if row:
        return row
    return await _fetchone(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    )


async def list_user_orders(user_id: int, limit: int = 20) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )


async def list_orders(
    status: str | None = None, query: str | None = None, limit: int = 50, offset: int = 0
) -> list[dict]:
    sql = (
        "SELECT o.*, u.username, u.first_name "
        "FROM orders o LEFT JOIN users u ON u.tg_id = o.user_id WHERE 1 = 1"
    )
    args: list[Any] = []
    if status == "open":
        sql += " AND o.status IN (%s)" % ",".join("?" * len(const.OPEN_STATUSES))
        args += list(const.OPEN_STATUSES)
    elif status:
        sql += " AND o.status = ?"
        args.append(status)
    if query:
        like = "%" + query.strip() + "%"
        sql += (
            " AND (o.code LIKE ? OR o.device_udid LIKE ? OR u.username LIKE ?"
            " OR CAST(o.user_id AS TEXT) LIKE ?)"
        )
        args += [like, like, like, like]
    sql += " ORDER BY o.id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return await _fetchall(sql, args)


async def status_counts() -> dict[str, int]:
    rows = await _fetchall("SELECT status, COUNT(*) AS n FROM orders GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


_TIMESTAMP_FIELD = {
    const.PAID: "paid_at",
    const.UDID: "udid_at",
    const.INSTALLED: "installed_at",
    const.DONE: "done_at",
}


async def set_status(
    order_id: int, status: str, actor: str, detail: str | None = None
) -> dict | None:
    ts = now()
    field = _TIMESTAMP_FIELD.get(status)
    sql = "UPDATE orders SET status = ?, updated_at = ?"
    args: list[Any] = [status, ts]
    if field:
        # COALESCE: первая отметка времени не переписывается при повторном переходе.
        sql += ", %s = COALESCE(%s, ?)" % (field, field)
        args.append(ts)
    sql += " WHERE id = ?"
    args.append(order_id)
    await conn().execute(sql, args)
    await conn().commit()
    await add_event(order_id, actor, "status:" + status, detail)
    return await get_order(order_id)


async def set_payment_ref(order_id: int, ref: str) -> None:
    await conn().execute(
        "UPDATE orders SET payment_ref = ?, updated_at = ? WHERE id = ?", (ref, now(), order_id)
    )
    await conn().commit()


async def set_udid(order_id: int, udid: str, actor: str) -> dict | None:
    await conn().execute(
        "UPDATE orders SET device_udid = ?, updated_at = ? WHERE id = ?", (udid, now(), order_id)
    )
    await conn().commit()
    return await set_status(order_id, const.UDID, actor, "UDID получен")


async def set_instruction(order_id: int, text: str, actor: str) -> dict | None:
    await conn().execute(
        "UPDATE orders SET instruction = ?, updated_at = ? WHERE id = ?", (text, now(), order_id)
    )
    await conn().commit()
    return await set_status(order_id, const.DONE, actor, "инструкция отправлена")


async def set_note(order_id: int, note: str) -> None:
    await conn().execute(
        "UPDATE orders SET seller_note = ?, updated_at = ? WHERE id = ?", (note, now(), order_id)
    )
    await conn().commit()


async def purge_user(user_id: int) -> int:
    """Удаляет пользователя вместе с заказами — по команде /forget.

    Заказы, сообщения и настройки по заказам уходят каскадом, общие
    настройки уведомлений связаны с пользователем только по id — их чистим сами.
    """
    cur = await conn().execute("DELETE FROM users WHERE tg_id = ?", (user_id,))
    await conn().execute("DELETE FROM notify_prefs WHERE user_id = ?", (user_id,))
    await conn().commit()
    return cur.rowcount or 0


# --------------------------------------------------------------- events


async def add_event(order_id: int | None, actor: str, type_: str, detail: str | None) -> None:
    await conn().execute(
        "INSERT INTO events (order_id, actor, type, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (order_id, actor, type_, detail, now()),
    )
    await conn().commit()


async def list_events(order_id: int, limit: int = 50) -> list[dict]:
    return await _fetchall(
        "SELECT * FROM events WHERE order_id = ? ORDER BY id DESC LIMIT ?", (order_id, limit)
    )


# ------------------------------------------------------------- переписка

BUYER = "buyer"
SELLER = "seller"


def _seen_column(viewer: str) -> str:
    return "seen_buyer" if viewer == BUYER else "seen_seller"


async def add_message(order_id: int, author: str, author_id: int, text: str) -> dict:
    """Своё сообщение автор видел сразу, второй стороне оно придёт непрочитанным."""
    ts = now()
    cur = await conn().execute(
        """
        INSERT INTO messages (order_id, author, author_id, text, created_at, seen_buyer, seen_seller)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order_id,
            author,
            author_id,
            text,
            ts,
            1 if author == BUYER else 0,
            1 if author == SELLER else 0,
        ),
    )
    await conn().execute(
        "UPDATE orders SET updated_at = ? WHERE id = ?", (ts, order_id)
    )
    await conn().commit()
    row = await _fetchone("SELECT * FROM messages WHERE id = ?", (int(cur.lastrowid),))
    assert row is not None
    return row


async def list_messages(order_id: int, limit: int = 200) -> list[dict]:
    rows = await _fetchall(
        "SELECT * FROM messages WHERE order_id = ? ORDER BY id DESC LIMIT ?", (order_id, limit)
    )
    return list(reversed(rows))


async def mark_seen(order_id: int, viewer: str) -> None:
    column = _seen_column(viewer)
    await conn().execute(
        "UPDATE messages SET %s = 1 WHERE order_id = ? AND %s = 0" % (column, column),
        (order_id,),
    )
    await conn().commit()


async def unread_count(order_id: int, viewer: str) -> int:
    row = await _fetchone(
        "SELECT COUNT(*) AS n FROM messages WHERE order_id = ? AND %s = 0" % _seen_column(viewer),
        (order_id,),
    )
    return int(row["n"]) if row else 0


# ----------------------------------------------------------- уведомления


async def get_notify_prefs(user_id: int) -> dict[str, bool]:
    """Только явно изменённые виды; остальные включены по умолчанию."""
    rows = await _fetchall("SELECT kind, enabled FROM notify_prefs WHERE user_id = ?", (user_id,))
    return {r["kind"]: bool(r["enabled"]) for r in rows}


async def set_notify_pref(user_id: int, kind: str, enabled: bool) -> None:
    await conn().execute(
        """
        INSERT INTO notify_prefs (user_id, kind, enabled) VALUES (?, ?, ?)
        ON CONFLICT(user_id, kind) DO UPDATE SET enabled = excluded.enabled
        """,
        (user_id, kind, 1 if enabled else 0),
    )
    await conn().commit()


async def get_order_notify(user_id: int, order_id: int) -> bool | None:
    """True/False — заказ настроен отдельно, None — действуют общие настройки."""
    row = await _fetchone(
        "SELECT enabled FROM notify_order_prefs WHERE user_id = ? AND order_id = ?",
        (user_id, order_id),
    )
    return bool(row["enabled"]) if row else None


async def set_order_notify(user_id: int, order_id: int, enabled: bool | None) -> None:
    if enabled is None:
        await conn().execute(
            "DELETE FROM notify_order_prefs WHERE user_id = ? AND order_id = ?",
            (user_id, order_id),
        )
    else:
        await conn().execute(
            """
            INSERT INTO notify_order_prefs (user_id, order_id, enabled) VALUES (?, ?, ?)
            ON CONFLICT(user_id, order_id) DO UPDATE SET enabled = excluded.enabled
            """,
            (user_id, order_id, 1 if enabled else 0),
        )
    await conn().commit()


async def order_notify_map(user_id: int) -> dict[int, bool]:
    rows = await _fetchall(
        "SELECT order_id, enabled FROM notify_order_prefs WHERE user_id = ?", (user_id,)
    )
    return {int(r["order_id"]): bool(r["enabled"]) for r in rows}


async def unread_by_order(viewer: str) -> dict[int, int]:
    """Сколько непрочитанных в каждом заказе — для счётчиков в списке продавца."""
    rows = await _fetchall(
        "SELECT order_id, COUNT(*) AS n FROM messages WHERE %s = 0 GROUP BY order_id"
        % _seen_column(viewer)
    )
    return {int(r["order_id"]): int(r["n"]) for r in rows}
