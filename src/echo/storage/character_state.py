import asyncio
import json
from pathlib import Path

from echo.ai.models import CharacterState
from echo.core.errors import StorageError

class CharacterStateStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        
    def _load_all(self) -> dict[str, dict]:
        if not self.path.exists(): return {}
        
        try:
            with self.path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as error:
            raise StorageError("Character state could not be loaded") from error
        
    def _save_all(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except (OSError, TypeError, ValueError) as error:
            raise StorageError("Character state could not be saved") from error
            
    async def get(self, user_id: int) -> CharacterState:
        async with self._lock:
            data = self._load_all()
            state = data.get(str(user_id))
            
            if state is None:
                return CharacterState(
                    affection=0,
                    mood=50,
                    stamina=100,
                    trust=0
                )
                
            try:
                return CharacterState.model_validate(state)
            except ValueError as error:
                raise StorageError("Character state is invalid") from error
    
    async def save(self, user_id: int, state: CharacterState) -> None:
        async with self._lock:
            data = self._load_all()
            data[str(user_id)] = state.model_dump()
            self._save_all(data=data)

    async def delete_user(self, user_id: int) -> None:
        async with self._lock:
            data = self._load_all()
            data.pop(str(user_id), None)
            self._save_all(data=data)

    async def delete_all(self) -> None:
        async with self._lock:
            self._save_all(data={})