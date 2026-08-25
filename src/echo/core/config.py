from pathlib import Path

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_token: str

    gemini_base_url: str
    gemini_api_key: str
    gemini_model: str
    
    memory_database_path: Path = Path("data/memory.db")
    character_state_path: Path = Path("data/character_state.json")
    prompt_dir: Path = Path("prompt")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()