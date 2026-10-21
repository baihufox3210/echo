from dataclasses import dataclass

from echo.ai.models import TokenUsage
from echo.storage.database import Database


@dataclass(slots=True)
class UsageSummary:
    messages: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageStore:
    """依使用者與日期（UTC）累計 AI token 用量。"""

    def __init__(self, database: Database):
        self.database = database

    async def record(self, user_id: int, usage: TokenUsage) -> None:
        async with self.database.transaction() as db:
            await db.execute(
                """
                INSERT INTO token_usage (user_id, day, requests, input_tokens, output_tokens, messages)
                VALUES (?, date('now'), ?, ?, ?, 1)
                ON CONFLICT(user_id, day) DO UPDATE SET
                    requests = requests + excluded.requests,
                    input_tokens = input_tokens + excluded.input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens,
                    messages = messages + 1
                """,
                (user_id, usage.requests, usage.input_tokens, usage.output_tokens),
            )

    async def summary(self, user_id: int | None = None, days: int | None = None) -> UsageSummary:
        conditions, params = [], []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if days is not None:
            conditions.append("day > date('now', ?)")
            params.append(f"-{days} days")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        row = await self.database.fetchone(
            f"""
            SELECT COALESCE(SUM(messages), 0), COALESCE(SUM(requests), 0),
                   COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
            FROM token_usage {where}
            """,
            tuple(params),
        )
        return UsageSummary(*row)

    async def top_users(self, days: int, limit: int = 10) -> list[tuple[int, UsageSummary]]:
        rows = await self.database.fetchall(
            """
            SELECT user_id, SUM(messages), SUM(requests), SUM(input_tokens), SUM(output_tokens)
            FROM token_usage
            WHERE day > date('now', ?)
            GROUP BY user_id
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
            LIMIT ?
            """,
            (f"-{days} days", limit),
        )
        return [(row[0], UsageSummary(*row[1:])) for row in rows]

    async def delete_user(self, user_id: int) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM token_usage WHERE user_id = ?", (user_id,))
