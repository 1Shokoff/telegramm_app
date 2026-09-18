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
    payment_ref  TEXT,
    imei         TEXT,
    instruction  TEXT,
    seller_note  TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    paid_at      TEXT,
    imei_at      TEXT,
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
    log.info("База готова: %s", path)


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


async def create_order(user_id: int, price_rub: int, payment_mode: str, prefix: str) -> dict:
    ts = now()
    cur = await conn().execute(
        """
        INSERT INTO orders (code, user_id, status, price_rub, payment_mode, created_at, updated_at)
        VALUES ('', ?, ?, ?, ?, ?, ?)
        """,
        (user_id, const.NEW, price_rub, payment_mode, ts, ts),
    )
    order_id = int(cur.lastrowid)
    code = "%s-%04d" % (prefix, order_id)
    await conn().execute("UPDATE orders SET code = ? WHERE id = ?", (code, order_id))
    await conn().commit()
    await add_event(order_id, "user:%d" % user_id, "created", None)
    order = await get_order(order_id)
    assert order is not None
    return order


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
            " AND (o.code LIKE ? OR o.imei LIKE ? OR u.username LIKE ?"
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
    const.IMEI: "imei_at",
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


async def set_imei(order_id: int, imei: str, actor: str) -> dict | None:
    await conn().execute(
        "UPDATE orders SET imei = ?, updated_at = ? WHERE id = ?", (imei, now(), order_id)
    )
    await conn().commit()
    return await set_status(order_id, const.IMEI, actor, "IMEI получен")


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
    """Удаляет пользователя вместе с заказами — по команде /forget."""
    cur = await conn().execute("DELETE FROM users WHERE tg_id = ?", (user_id,))
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
