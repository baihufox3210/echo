from openai import AsyncOpenAI

from echo.ai.models import ChatMessage
from echo.core.config import Settings


class AIClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            timeout=60.0,
        )

        self.model = settings.gemini_model

    async def chat(self, messages: list[ChatMessage]) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
        )

        return response.choices[0].message.content or ""