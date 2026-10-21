from echo.ai.models import ChatMessage
from echo.storage.database import Database


class ShortTermMemory:
    def __init__(self, database: Database, max_messages: int = 20):
        self.database = database
        self.max_messages = max_messages

    async def add(self, user_id: int, messages: list[ChatMessage]) -> None:
        async with self.database.transaction() as db:
            await db.executemany(
                "INSERT INTO short_term_memory (user_id, role, content) VALUES (?, ?, ?)",
                [(user_id, message.role, message.content) for message in messages],
            )
            await db.execute(
                """
                DELETE FROM short_term_memory
                WHERE user_id = ?
                AND id NOT IN (
                    SELECT id FROM short_term_memory
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                """,
                (user_id, user_id, self.max_messages),
            )

    async def get(self, user_id: int) -> list[ChatMessage]:
        rows = await self.database.fetchall(
            """
            SELECT role, content FROM (
                SELECT id, role, content FROM short_term_memory
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
            ) ORDER BY id ASC
            """,
            (user_id, self.max_messages),
        )
        return [ChatMessage(role=row[0], content=row[1]) for row in rows]

    async def clear(self, user_id: int) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM short_term_memory WHERE user_id = ?", (user_id,))

    async def clear_all(self) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM short_term_memory")
