import json
import logging
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
                INSERT INTO character_state (user_id, affection, mood, stamina, trust, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id) DO UPDATE SET
                    affection = excluded.affection,
                    mood = excluded.mood,
                    stamina = excluded.stamina,
                    trust = excluded.trust,
                    updated_at = excluded.updated_at
                """,
                (user_id, state.affection, state.mood, state.stamina, state.trust),
            )

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
