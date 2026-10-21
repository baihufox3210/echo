from echo.ai.client import AIClient
from echo.ai.service import AIService
from echo.core.config import Settings
from echo.memory.long_term import LongTermMemory
from echo.memory.short_term import ShortTermMemory
from echo.prompts.loader import PromptLoader
from echo.storage.character_state import CharacterStateStore
from echo.storage.database import Database
from echo.storage.usage import UsageStore


class Services:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(path=settings.memory_database_path)

        self.memory = ShortTermMemory(database=self.database, max_messages=settings.short_term_max_messages)
        self.history = LongTermMemory(base_url=settings.mem0_base_url, timeout=settings.mem0_timeout)
        self.state = CharacterStateStore(database=self.database, legacy_json_path=settings.character_state_path)
        self.usage = UsageStore(database=self.database)
        self.prompts = PromptLoader(prompt_dir=settings.prompt_dir)

        self.ai = AIService(
            client=AIClient(settings=settings, history=self.history),
            prompt_loader=self.prompts,
            memory=self.memory,
            history=self.history,
            state_store=self.state,
            usage_store=self.usage,
            settings=settings,
        )

    async def initialize(self) -> None:
        await self.database.connect()
        await self.state.initialize()
        # 啟動時即驗證 Prompt 檔案存在，避免執行中才發現錯誤
        self.prompts.load_system_prompt()
        self.prompts.load_character_state_prompt()

    async def close(self) -> None:
        await self.ai.close()
        await self.history.close()
        await self.database.close()
