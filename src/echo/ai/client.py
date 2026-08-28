import logging

from openai import AsyncOpenAI

from echo.ai.models import ChatMessage, AIResponse
from echo.core.config import Settings
from echo.core.errors import AIServiceError

logger = logging.getLogger(__name__)

class AIClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=60.0,
        )

        self.model = settings.model

    async def chat(self, messages: list[ChatMessage]) -> AIResponse:
        logger.debug(
            "AI request sent: model=%s, message_count=%d",
            self.model,
            len(messages),
        )

        try:
            response = await self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ],
                response_format=AIResponse,
            )
            parsed = response.choices[0].message.parsed
            if parsed is None:
                raise ValueError("AI response did not contain a parsed result")
            return parsed
        except Exception as error:
            logger.error("AI service error: %s", error.__class__.__name__)
            raise AIServiceError("AI service request failed") from error

    async def close(self) -> None:
        await self.client.close()