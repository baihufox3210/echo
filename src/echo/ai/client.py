import logging

from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel, set_tracing_disabled

from echo.ai.tools import get_current_time

from echo.ai.models import ChatMessage, AIResponse
from echo.core.config import Settings
from echo.core.errors import AIServiceError

logger = logging.getLogger(__name__)

set_tracing_disabled(True)

class AIClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=60.0,
        )

        self.model = settings.model
        
        self.agent_model = OpenAIChatCompletionsModel(
            model=self.model,
            openai_client=self.client
        )
        
        self.agent = Agent(
            name="Echo",
            instructions=(
                "你是一個友善的 AI 助手。"
                "當使用者需要即時資訊時，請使用可用的工具。"
            ),
            model=self.agent_model,
            tools=[
                get_current_time
            ],
            output_type=AIResponse
        )

    async def chat(self, messages: list[ChatMessage]) -> AIResponse:
        logger.debug(
            "AI request sent: model=%s, message_count=%d",
            self.model,
            len(messages),
        )

        try:
            result = await Runner.run(
                starting_agent=self.agent,
                input=[
                    {"role": message.role, "content": message.content}
                    for message in messages
                ]
            )
            
            # response = await self.client.beta.chat.completions.parse(
            #     model=self.model,
            #     messages=[
            #         {"role": message.role, "content": message.content}
            #         for message in messages
            #     ],
            #     response_format=AIResponse,
            # )
            
            # parsed = response.choices[0].message.parsed
            
            parsed = result.final_output
                        
            if parsed is None:
                raise ValueError("AI response did not contain a parsed result")
            return parsed
        
        except Exception as error:
            logger.error("AI service error: %s", error.__class__.__name__)
            raise AIServiceError("AI service request failed") from error

    async def close(self) -> None:
        await self.client.close()