import logging

from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel, set_tracing_disabled

from echo.ai.tools import get_current_time, web_search, web_fetch, cleanup_web_search, set_web_search_settings


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
        self.settings = settings
        
        set_web_search_settings(settings)
        
        self.agent_model = OpenAIChatCompletionsModel(
            model=self.model,
            openai_client=self.client
        )
        
        self.agent = Agent(
            name="Echo",
            instructions=(
                "你是一個友善的 AI 助手。\n"
                "你可以使用以下工具：\n"
                "1. get_current_time：取得目前時間（台灣時區）\n"
                "2. web_search：搜尋網際網路上的最新資訊\n"
                "3. web_fetch：讀取指定 URL 的網頁內容\n\n"
                "當使用者詢問最新新聞、即時資訊、近期事件或需要查證的資訊時，"
                "請優先使用網絡搜尋工具。\n"
                "如果搜尋結果摘要不夠詳細，可以使用 web_fetch 讀取完整網頁。"
            ),
            model=self.agent_model,
            tools=[
                get_current_time,
                web_search,
                web_fetch,
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
            
            parsed = result.final_output
                        
            if parsed is None:
                raise ValueError("AI response did not contain a parsed result")
            return parsed
        
        except Exception as error:
            logger.error("AI service error: %s", error.__class__.__name__)
            raise AIServiceError("AI service request failed") from error

    async def close(self) -> None:
        await self.client.close()
        await cleanup_web_search()