import pytest

from echo.core.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        discord_token="token",
        base_url="http://localhost:1/v1",
        api_key="key",
        model="model",
        memory_database_path=tmp_path / "memory.db",
        character_state_path=tmp_path / "character_state.json",
        mem0_base_url="",
        searxng_url="http://searxng.test",
        search_cache_ttl=60,
    )
