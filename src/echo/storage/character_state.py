import json
from pathlib import Path

from echo.ai.models import CharacterState

class CharacterStateStore:
    def __init__(self, path: Path):
        self.path = path
        
    def _load_all(self) -> dict[str, dict]:
        if not self.path.exists(): return {}
        
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)
        
    def _save_all(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            
    async def get(self, user_id: int) -> CharacterState:
        data = self._load_all()
        state = data.get(str(user_id))
        
        if state is None:
            return CharacterState(
                affection=50,
                mood=50,
                stamina=100,
                trust=50
            )
            
        return CharacterState.model_validate(state)
    
    async def save(self, user_id: int, state: CharacterState) -> None:
        data = self._load_all()
        data[str(user_id)] = state.model_dump()
        
        self._save_all