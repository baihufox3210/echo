import json

from echo.ai.models import CharacterState, ChatMessage
from echo.memory.short_term import ShortTermMemory
from echo.storage.character_state import CharacterStateStore
from echo.storage.database import Database


async def test_short_term_memory_keeps_latest_messages(tmp_path):
    db = Database(tmp_path / "m.db")
    await db.connect()
    memory = ShortTermMemory(db, max_messages=4)

    for i in range(3):
        await memory.add(1, [ChatMessage("user", f"u{i}"), ChatMessage("assistant", f"a{i}")])

    messages = await memory.get(1)
    assert [m.content for m in messages] == ["u1", "a1", "u2", "a2"]
    assert await memory.get(2) == []
    await db.close()


async def test_character_state_roundtrip_and_legacy_migration(tmp_path):
    legacy = tmp_path / "character_state.json"
    legacy.write_text(json.dumps({"42": {"affection": 10, "mood": 20, "stamina": 30, "trust": 40}}))

    db = Database(tmp_path / "m.db")
    await db.connect()
    store = CharacterStateStore(db, legacy_json_path=legacy)
    await store.initialize()

    assert (await store.get(42)).affection == 10
    assert not legacy.exists()

    await store.save(42, CharacterState(affection=99, mood=1, stamina=2, trust=3))
    assert (await store.get(42)).affection == 99

    await store.delete_user(42)
    assert (await store.get(42)).affection == 0
    await db.close()
