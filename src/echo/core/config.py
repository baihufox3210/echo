from pathlib import Path

from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    discord_token: str = Field(..., min_length=1)

    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    
    memory_database_path: Path = Path("data/memory.db")
    character_state_path: Path = Path("data/character_state.json")
    prompt_dir: Path = Path("prompt")
    
    mem0_base_url: str = Field(..., min_length=1)
    
    searxng_url: str = Field(default="http://127.0.0.1:8080")
    search_timeout: float = Field(default=20.0)
    search_max_results: int = Field(default=5)
    search_language: str = Field(default="zh-TW")

    @field_validator("base_url", "mem0_base_url")
    @classmethod
    def validate_urls(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URLs must start with http:// or https://")
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()