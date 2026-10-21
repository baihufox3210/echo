from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Discord
    discord_token: str = Field(..., min_length=1)
    respond_to_dm: bool = True
    respond_to_replies: bool = True
    sync_commands: bool = True

    # AI
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    # 主要模型過載（429／5xx）時改用的備援模型，留空則不使用
    fallback_model: str | None = None
    model_temperature: float | None = Field(default=None, ge=0, le=2)
    # 單次回覆的最大輸出 token 數；過小會導致長回覆被截斷
    max_output_tokens: int | None = Field(default=4096, ge=256)
    ai_timeout: float = Field(default=90.0, gt=0)
    ai_max_retries: int = Field(default=2, ge=0)
    ai_max_turns: int = Field(default=10, ge=1)
    ai_max_runs: int = Field(default=2, ge=1, le=5)
    tool_call_budget: int = Field(default=24, ge=1)
    enable_research: bool = True
    # 以逗號分隔要停用的工具名稱，例如 "get_weather,add_reaction"
    disabled_tools: str = ""
    reminder_ping: bool = True
    research_max_turns: int = Field(default=12, ge=2)

    # 回覆呈現
    stream_responses: bool = True
    # 串流時要求 API 回傳 token 用量（stream_options.include_usage）；代理不支援時可關閉
    stream_include_usage: bool = True
    stream_edit_interval: float = Field(default=1.2, ge=0.5)
    show_state_embed: bool = False

    # 附件
    enable_vision: bool = True
    max_attachments: int = Field(default=4, ge=0, le=10)
    attachment_max_bytes: int = Field(default=8_000_000, ge=10_000)
    attachment_inline_chars: int = Field(default=6_000, ge=500)

    # 角色狀態自然衰減（長時間未互動時好感度下降）
    state_decay_enabled: bool = True
    state_decay_after_days: int = Field(default=3, ge=1)
    state_decay_amount: int = Field(default=1, ge=1, le=100)

    healthcheck_file: Path = Path("/tmp/echo-heartbeat")

    # 併發與流量限制
    max_concurrent_requests: int = Field(default=8, ge=1)
    rate_limit_per_user: int = Field(default=3, ge=1)
    rate_limit_window: float = Field(default=60.0, gt=0)
    max_message_length: int = Field(default=4000, ge=1)

    # 儲存
    memory_database_path: Path = Path("data/memory.db")
    character_state_path: Path = Path("data/character_state.json")
    short_term_max_messages: int = Field(default=20, ge=0)
    prompt_dir: Path = Path("prompt")

    # 長期記憶（留空則停用）
    mem0_base_url: str | None = None
    mem0_timeout: float = Field(default=60.0, gt=0)
    mem0_top_k: int = Field(default=10, ge=1)

    # 網路搜尋
    searxng_url: str | None = Field(default="http://127.0.0.1:8080")
    search_timeout: float = Field(default=20.0, gt=0)
    search_max_results: int = Field(default=5, ge=1, le=20)
    search_language: str = "zh-TW"
    search_cache_ttl: float = Field(default=300.0, ge=0)
    fetch_max_chars: int = Field(default=12_000, ge=1_000)
    fetch_max_bytes: int = Field(default=3_000_000, ge=10_000)
    fetch_allow_private: bool = False

    timezone: str = "Asia/Taipei"
    log_level: str = "INFO"

    @property
    def disabled_tool_names(self) -> set[str]:
        return {name.strip() for name in self.disabled_tools.split(",") if name.strip()}

    @field_validator("mem0_base_url", "searxng_url", "fallback_model", mode="before")
    @classmethod
    def empty_as_none(cls, v: str | None) -> str | None:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("base_url", "mem0_base_url", "searxng_url")
    @classmethod
    def validate_urls(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(("http://", "https://")):
            raise ValueError("URLs must start with http:// or https://")
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
