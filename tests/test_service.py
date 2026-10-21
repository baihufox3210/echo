import asyncio

from echo.ai.models import AIResponse, CharacterState
from echo.ai.service import AIService
from echo.memory.long_term import LongTermMemory
from echo.memory.short_term import ShortTermMemory
from echo.prompts.loader import PromptLoader
from echo.storage.character_state import CharacterStateStore
from echo.storage.database import Database
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompt"


class FakeClient:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def chat(self, messages):
        self.calls.append(messages)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return AIResponse(
            dialogue=f"reply to {messages[-1].content}",
            action="smile",
            state=CharacterState(affection=5, mood=60, stamina=90, trust=1),
        )

    async def close(self):
        pass


async def test_chat_runs_users_in_parallel_and_persists(settings):
    db = Database(settings.memory_database_path)
    await db.connect()
    client = FakeClient()
    service = AIService(
        client=client,
        prompt_loader=PromptLoader(PROMPT_DIR),
        memory=ShortTermMemory(db),
        history=LongTermMemory(None),
        state_store=CharacterStateStore(db),
        settings=settings,
    )

    await asyncio.gather(*(service.chat(user_id=i, user_message=f"hi {i}") for i in range(5)))
    assert client.max_active > 1

    await service.chat(user_id=0, user_message="again")
    last = client.calls[-1]
    assert "目前角色狀態" in last[0].content and "affection：5" in last[0].content
    assert [m.content for m in last[1:]] == ["hi 0", "reply to hi 0", "again"]

    await service.close()
    await db.close()
