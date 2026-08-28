from echo.ai.client import AIClient
from echo.ai.models import ChatMessage, AIResponse

from echo.memory.long_term import LongTermMemory
from echo.memory.short_term import ShortTermMemory

from echo.storage.character_state import CharacterStateStore

from echo.prompts.loader import PromptLoader

class AIService:
    def __init__(self, client: AIClient, prompt_loader: PromptLoader, memory: ShortTermMemory, history: LongTermMemory, state_store: CharacterStateStore):
        self.client = client
        self.prompt_loader = prompt_loader
        
        self.memory = memory
        self.history = history
        self.state_store = state_store
        
    async def chat(self, user_id: int, user_message: str) -> AIResponse:
        system_prompt = self.prompt_loader.load_system_prompt()
        state_prompt = self.prompt_loader.load_character_state_prompt()
        
        short_term = await self.memory.get(user_id=user_id)
        long_term = await self.history.search(query=user_message, user_id=user_id, top_k=10)
        
        state = await self.state_store.get(user_id=user_id)
        
        memories = long_term.get("results", [])
        
        memory_prompt = "\n".join(
            f"- {item['memory']}"
            for item in memories
            if item.get("memory")
        )
        
        if not memory_prompt:
            memory_prompt = "目前沒有相關的長期記憶。"
        
        state_prompt = state_prompt.format(
            affection=state.affection,
            mood=state.mood,
            stamina=state.stamina,
            trust=state.trust
        )

        messages = [
            ChatMessage(
                role="system",
                content=(
                    f"{system_prompt}\n\n"
                    f"{state_prompt}\n\n"
                    f"相關長期記憶：\n"
                    f"{memory_prompt}"
                ),
            ), *short_term,
            ChatMessage(role="user", content=user_message)
        ]
        
        result =  await self.client.chat(messages=messages)
        
        await self.memory.add(user_id=user_id, role="user", content=user_message)
        await self.memory.add(user_id=user_id, role="assistant", content=result.dialogue)
        
        await self.history.add(
            messages=[
                ChatMessage(
                    role="user",
                    content=user_message,
                ),
                ChatMessage(
                    role="assistant",
                    content=result.dialogue,
                )
            ],
            user_id=user_id
        )
        
        await self.state_store.save(user_id=user_id, state=result.state)
        
        return result