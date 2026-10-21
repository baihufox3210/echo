from echo.storage.database import Database


class NoteStore:
    """AI 主動記下的使用者重點（喜好、生日、稱呼等），每次對話都會提供給 AI。"""

    def __init__(self, database: Database):
        self.database = database

    async def set(self, user_id: int, key: str, value: str) -> None:
        async with self.database.transaction() as db:
            await db.execute(
                """
                INSERT INTO user_notes (user_id, key, value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (user_id, key, value),
            )

    async def get_all(self, user_id: int) -> dict[str, str]:
        rows = await self.database.fetchall(
            "SELECT key, value FROM user_notes WHERE user_id = ? ORDER BY updated_at", (user_id,)
        )
        return {row[0]: row[1] for row in rows}

    async def count(self, user_id: int) -> int:
        row = await self.database.fetchone("SELECT COUNT(*) FROM user_notes WHERE user_id = ?", (user_id,))
        return row[0]

    async def delete(self, user_id: int, key: str) -> bool:
        async with self.database.transaction() as db:
            cursor = await db.execute("DELETE FROM user_notes WHERE user_id = ? AND key = ?", (user_id, key))
            return cursor.rowcount > 0

    async def delete_user(self, user_id: int) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM user_notes WHERE user_id = ?", (user_id,))

    async def delete_all(self) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM user_notes")
