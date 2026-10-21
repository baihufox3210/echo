from dataclasses import dataclass
from datetime import UTC, datetime

from echo.storage.database import Database


@dataclass(slots=True)
class Reminder:
    id: int
    user_id: int
    channel_id: int
    due_at: datetime
    content: str


def _row_to_reminder(row) -> Reminder:
    return Reminder(row[0], row[1], row[2], datetime.fromisoformat(row[3]), row[4])


class ReminderStore:
    """提醒排程；時間一律以 UTC ISO 字串保存。"""

    def __init__(self, database: Database):
        self.database = database

    async def add(self, user_id: int, channel_id: int, due_at: datetime, content: str) -> int:
        async with self.database.transaction() as db:
            cursor = await db.execute(
                "INSERT INTO reminders (user_id, channel_id, due_at, content) VALUES (?, ?, ?, ?)",
                (user_id, channel_id, due_at.astimezone(UTC).isoformat(timespec="seconds"), content),
            )
            return cursor.lastrowid

    async def list_user(self, user_id: int) -> list[Reminder]:
        rows = await self.database.fetchall(
            "SELECT id, user_id, channel_id, due_at, content FROM reminders WHERE user_id = ? ORDER BY due_at",
            (user_id,),
        )
        return [_row_to_reminder(row) for row in rows]

    async def count_user(self, user_id: int) -> int:
        row = await self.database.fetchone("SELECT COUNT(*) FROM reminders WHERE user_id = ?", (user_id,))
        return row[0]

    async def cancel(self, user_id: int, reminder_id: int) -> bool:
        async with self.database.transaction() as db:
            cursor = await db.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)
            )
            return cursor.rowcount > 0

    async def pop_due(self, now: datetime | None = None, limit: int = 50) -> list[Reminder]:
        """取出並刪除已到期的提醒。"""
        now = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        async with self.database.transaction() as db:
            async with db.execute(
                "SELECT id, user_id, channel_id, due_at, content FROM reminders WHERE due_at <= ? ORDER BY due_at LIMIT ?",
                (now, limit),
            ) as cursor:
                rows = await cursor.fetchall()
            await db.executemany("DELETE FROM reminders WHERE id = ?", [(row[0],) for row in rows])
        return [_row_to_reminder(row) for row in rows]

    async def delete_user(self, user_id: int) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))

    async def delete_all(self) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM reminders")
