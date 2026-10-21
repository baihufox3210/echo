import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from echo.ai.models import CharacterState
from echo.core.errors import StorageError
from echo.storage.database import Database

logger = logging.getLogger(__name__)

DEFAULT_STATE = CharacterState(affection=0, mood=50, stamina=100, trust=0)


class CharacterStateStore:
    def __init__(self, database: Database, legacy_json_path: Path | None = None):
        self.database = database
        self.legacy_json_path = legacy_json_path

    async def initialize(self) -> None:
        await self._migrate_legacy_json()

    async def get(self, user_id: int) -> CharacterState:
        row = await self.database.fetchone(
            "SELECT affection, mood, stamina, trust FROM character_state WHERE user_id = ?",
            (user_id,),
        )
        if row is None:
            return DEFAULT_STATE.model_copy()

        try:
            return CharacterState(affection=row[0], mood=row[1], stamina=row[2], trust=row[3])
        except ValueError as error:
            raise StorageError("Character state is invalid") from error

    async def save(self, user_id: int, state: CharacterState) -> None:
        async with self.database.transaction() as db:
            await db.execute(
                """
                INSERT INTO character_state
                    (user_id, affection, mood, stamina, trust, updated_at, last_interaction_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    affection = excluded.affection,
                    mood = excluded.mood,
                    stamina = excluded.stamina,
                    trust = excluded.trust,
                    updated_at = excluded.updated_at,
                    last_interaction_at = excluded.last_interaction_at
                """,
                (user_id, state.affection, state.mood, state.stamina, state.trust),
            )

    async def last_interaction(self, user_id: int) -> datetime | None:
        row = await self.database.fetchone(
            "SELECT COALESCE(last_interaction_at, updated_at) FROM character_state WHERE user_id = ?",
            (user_id,),
        )
        if row is None or row[0] is None:
            return None
        return datetime.fromisoformat(row[0]).replace(tzinfo=UTC)

    async def decay(self, after_days: int, amount: int) -> int:
        """長時間未互動的使用者每天降低好感度，回傳受影響的人數。"""
        async with self.database.transaction() as db:
            cursor = await db.execute(
                """
                UPDATE character_state
                SET affection = MAX(0, affection - ?),
                    last_decay_at = date('now')
                WHERE affection > 0
                AND julianday('now') - julianday(COALESCE(last_interaction_at, updated_at)) >= ?
                AND (last_decay_at IS NULL OR last_decay_at < date('now'))
                """,
                (amount, after_days),
            )
            return cursor.rowcount

    async def delete_user(self, user_id: int) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM character_state WHERE user_id = ?", (user_id,))

    async def delete_all(self) -> None:
        async with self.database.transaction() as db:
            await db.execute("DELETE FROM character_state")

    async def _migrate_legacy_json(self) -> None:
        """將舊版 JSON 角色狀態匯入 SQLite，完成後將檔案改名保留。"""
        path = self.legacy_json_path
        if path is None or not path.is_file():
            return

        try:
            data: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("Legacy character state could not be read: %s", path)
            return

        rows = []
        for user_id, raw in data.items():
            try:
                state = CharacterState.model_validate(raw)
                rows.append((int(user_id), state.affection, state.mood, state.stamina, state.trust))
            except ValueError:
                logger.warning("Skipped invalid legacy state for user %s", user_id)

        async with self.database.transaction() as db:
            await db.executemany(
                """
                INSERT OR IGNORE INTO character_state (user_id, affection, mood, stamina, trust)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

        path.rename(path.with_suffix(path.suffix + ".migrated"))
        logger.info("Migrated %d character states from %s", len(rows), path)
