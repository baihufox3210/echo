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


async def test_usage_store_and_state_decay(tmp_path):
    from echo.ai.models import TokenUsage
    from echo.storage.usage import UsageStore

    db = Database(tmp_path / "m.db")
    await db.connect()
    usage = UsageStore(db)
    await usage.record(1, TokenUsage(requests=2, input_tokens=100, output_tokens=50))
    await usage.record(1, TokenUsage(requests=1, input_tokens=10, output_tokens=5))
    await usage.record(2, TokenUsage(requests=1, input_tokens=1, output_tokens=1))

    mine = await usage.summary(user_id=1, days=1)
    assert (mine.messages, mine.requests, mine.total_tokens) == (2, 3, 165)
    assert (await usage.top_users(days=30))[0][0] == 1

    store = CharacterStateStore(db)
    await store.save(1, CharacterState(affection=10, mood=50, stamina=50, trust=50))
    await store.save(2, CharacterState(affection=10, mood=50, stamina=50, trust=50))
    assert await store.last_interaction(1) is not None

    async with db.transaction() as conn:
        await conn.execute("UPDATE character_state SET last_interaction_at = datetime('now', '-5 days') WHERE user_id = 1")

    assert await store.decay(after_days=3, amount=2) == 1
    assert await store.decay(after_days=3, amount=2) == 0  # 每天最多一次
    assert (await store.get(1)).affection == 8
    assert (await store.get(2)).affection == 10
    await db.close()
