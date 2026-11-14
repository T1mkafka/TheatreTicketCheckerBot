"""Хранилище: подписчики и последнее состояние каждого показа (SQLite)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    joined_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS states (
    target_key TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    name  TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена: вызовите connect()")
        return self._conn

    # ---------- Подписчики ----------

    async def add_subscriber(self, chat_id: int, username: str | None, first_name: str | None) -> None:
        await self.conn.execute(
            """
            INSERT INTO subscribers (chat_id, username, first_name, active, joined_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                active = 1,
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (chat_id, username, first_name, _now()),
        )
        await self.conn.commit()

    async def remove_subscriber(self, chat_id: int) -> None:
        await self.conn.execute(
            "UPDATE subscribers SET active = 0 WHERE chat_id = ?", (chat_id,)
        )
        await self.conn.commit()

    async def deactivate_subscriber(self, chat_id: int) -> None:
        """Отключает подписчика (например, если бот заблокирован)."""
        await self.remove_subscriber(chat_id)

    async def is_subscribed(self, chat_id: int) -> bool:
        async with self.conn.execute(
            "SELECT active FROM subscribers WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row and row["active"])

    async def subscribers(self) -> list[int]:
        async with self.conn.execute(
            "SELECT chat_id FROM subscribers WHERE active = 1"
        ) as cursor:
            rows = await cursor.fetchall()
        return [row["chat_id"] for row in rows]

    async def subscribers_count(self) -> int:
        return len(await self.subscribers())

    # ---------- Состояния ----------

    async def get_state(self, target_key: str) -> Optional[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT data FROM states WHERE target_key = ?", (target_key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["data"])
        except (ValueError, TypeError):
            return None

    async def set_state(self, target_key: str, state: dict[str, Any]) -> None:
        await self.conn.execute(
            """
            INSERT INTO states (target_key, data, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(target_key) DO UPDATE SET
                data = excluded.data,
                updated_at = excluded.updated_at
            """,
            (target_key, json.dumps(state, ensure_ascii=False), _now()),
        )
        await self.conn.commit()

    async def all_states(self) -> dict[str, dict[str, Any]]:
        async with self.conn.execute("SELECT target_key, data FROM states") as cursor:
            rows = await cursor.fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            try:
                result[row["target_key"]] = json.loads(row["data"])
            except (ValueError, TypeError):
                continue
        return result

    # ---------- Служебные значения ----------

    async def get_meta(self, name: str) -> Optional[str]:
        async with self.conn.execute(
            "SELECT value FROM meta WHERE name = ?", (name,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["value"] if row else None

    async def set_meta(self, name: str, value: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO meta (name, value) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET value = excluded.value
            """,
            (name, value),
        )
        await self.conn.commit()
