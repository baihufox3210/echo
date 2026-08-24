from echo.ai.client import AIClient
from echo.ai.models import ChatMessage

from echo.memory.short_term import ShortTermMemory

from echo.prompts.loader import PromptLoader

class AIService:
    def __init__(self, client: AIClient, prompt_loader: PromptLoader, memory: ShortTermMemory):
        self.client = client
        self.prompt_loader = prompt_loader
        
        self.memory = memory
        
    async def chat(self, user_id: int, user_message: str) -> str:
        system_prompt = self.prompt_loader.load_system_prompt()
        history = await self.memory.get(user_id=user_id)
        
        messages = [
            ChatMessage(role="system", content=system_prompt), *history,
            ChatMessage(role="user", content=user_message)
        ]
        
        response =  await self.client.chat(messages=messages)
        
        await self.memory.add(user_id=user_id, role="user", content=user_message)
        await self.memory.add(user_id=user_id, role="assistant", content=response)
        
        return response