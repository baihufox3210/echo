from echo.ai.client import AIClient
from echo.ai.service import AIService

from echo.core.config import Settings

from echo.memory.short_term import ShortTermMemory
from echo.memory.long_term import LongTermMemory
from echo.storage.character_state import CharacterStateStore

from echo.prompts.loader import PromptLoader

class Services:
    def __init__(self, settings: Settings):
        self.memory = ShortTermMemory(database_path=settings.memory_database_path)
        self.history = LongTermMemory(base_url=settings.mem0_base_url)
        self.state = CharacterStateStore(path=settings.character_state_path)
        
        self.prompts = PromptLoader(prompt_dir=settings.prompt_dir)
        
        self.ai = AIService(
            client=AIClient(settings=settings),
            prompt_loader=self.prompts,
            memory=self.memory,
            history=self.history,
            state_store=self.state
        )
        
    async def initialize(self):
        await self.memory.initialize()