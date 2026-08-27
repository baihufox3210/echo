import traceback

from openai import AsyncOpenAI

from echo.ai.models import ChatMessage, AIResponse
from echo.core.config import Settings


class AIClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=60.0,
        )

        self.model = settings.model

    async def chat(self, messages: list[ChatMessage]) -> AIResponse:
        print(
            f"[OpenAI] sending request: base_url={self.client.base_url}, "
            f"model={self.model}, messages={len(messages)}",
            flush=True,
        )

        try:
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
        except Exception as error:
            print(
                f"[OpenAI] request failed: {type(error).__name__}: {error}",
                flush=True,
            )
            traceback.print_exc()
            raise
        
        return response.choices[0].message.parsed