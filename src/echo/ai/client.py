from openai import AsyncOpenAI

from echo.ai.models import ChatMessage, AIResponse
from echo.core.config import Settings


class AIClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.gemini_api_key,
            base_url=settings.gemini_base_url,
            timeout=60.0,
        )

        self.model = settings.gemini_model

    async def chat(self, messages: list[ChatMessage]) -> AIResponse:
        response = await self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {
                    "role": message.role,
                    "content": message.content,
                }
                for message in messages
            ],
            response_format=AIResponse
        )
        
        return response.choices[0].message.parsed