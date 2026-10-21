import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from echo.core.errors import StorageError

SCHEMA = """
CREATE TABLE IF NOT EXISTS short_term_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_short_term_memory_user
ON short_term_memory(user_id, id);

CREATE TABLE IF NOT EXISTS character_state (
    user_id INTEGER PRIMARY KEY,
    affection INTEGER NOT NULL,
    mood INTEGER NOT NULL,
    stamina INTEGER NOT NULL,
    trust INTEGER NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """共用的 SQLite 連線；寫入以交易包裝並序列化。"""

    def __init__(self, path: Path):
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._db = await aiosqlite.connect(self.path)
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")
            await self._db.execute("PRAGMA busy_timeout=5000")
            await self._db.executescript(SCHEMA)
            await self._db.commit()
        except aiosqlite.Error as error:
            raise StorageError("Database could not be initialized") from error

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise StorageError("Database is not connected")
        return self._db

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        try:
            async with self.db.execute(sql, params) as cursor:
                return list(await cursor.fetchall())
        except aiosqlite.Error as error:
            raise StorageError("Database query failed") from error

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        try:
            async with self.db.execute(sql, params) as cursor:
                return await cursor.fetchone()
        except aiosqlite.Error as error:
            raise StorageError("Database query failed") from error

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._write_lock:
            try:
                yield self.db
                await self.db.commit()
            except aiosqlite.Error as error:
                await self.db.rollback()
                raise StorageError("Database write failed") from error
            except BaseException:
                await self.db.rollback()
                raise

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
