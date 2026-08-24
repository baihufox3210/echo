from echo.ai.models import ChatMessage
import aiosqlite


class ShortTermMemory:
    def __init__(self, database_path: str = "data/memory.db", max_messages: int = 20):
        self.database_path = database_path
        self.max_messages = max_messages

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS short_term_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_short_term_memory_user
                ON short_term_memory(user_id, id)
                """
            )

            await db.commit()

    async def add(self, user_id: int, role: str, content: str) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                INSERT INTO short_term_memory
                (user_id, role, content)
                VALUES (?, ?, ?)
                """,
                (user_id, role, content),
            )

            await db.execute(
                """
                DELETE FROM short_term_memory
                WHERE user_id = ?
                AND id NOT IN (
                    SELECT id
                    FROM short_term_memory
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (
                    user_id,
                    user_id,
                    self.max_messages,
                ),
            )

            await db.commit()

    async def get(self, user_id: int) -> list[ChatMessage]:
        async with aiosqlite.connect(self.database_path) as db:
            cursor = await db.execute(
                """
                SELECT role, content
                FROM short_term_memory
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (user_id,),
            )

            rows = await cursor.fetchall()

        return [
            ChatMessage(
                role=row[0],
                content=row[1],
            )
            for row in rows
        ]

    async def clear(self, user_id: int) -> None:
        async with aiosqlite.connect(self.database_path) as db:
            await db.execute(
                """
                DELETE FROM short_term_memory
                WHERE user_id = ?
                """,
                (user_id,),
            )

            await db.commit()