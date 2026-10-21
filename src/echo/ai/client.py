import logging

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from openai import AsyncOpenAI

from echo.ai.models import AIResponse, ChatMessage
from echo.ai.tools import WebSearch, build_time_tool
from echo.core.config import Settings
from echo.core.errors import AIServiceError

logger = logging.getLogger(__name__)

set_tracing_disabled(True)

TOOL_INSTRUCTIONS = """\
# 工具使用規則
- 需要確認日期或時間時，使用 get_current_time。
- 使用者詢問新聞、時事、天氣、價格、賽事、版本更新等即時資訊，或你不確定答案、需要查證時，先使用 web_search，不可憑記憶編造。
- 查詢近期事件時設定 time_range（day／week／month），新聞類設定 category 為 news。
- 搜尋結果不夠詳細時，挑選 1～2 個最相關的網址以 web_fetch 讀取全文。
- 若第一次搜尋沒有理想結果，可改用不同關鍵字（例如中英文互換）再搜尋一次。
- 依據搜尋結果回答時，以角色的口吻自然轉述重點，並在回覆末尾以「來源：」列出實際引用的網址（最多 3 個）。
- 搜尋失敗或查無資料時，誠實表示找不到，不要捏造內容。
- 不需要工具時直接回答。
"""


class AIClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.ai_timeout,
            max_retries=settings.ai_max_retries,
        )
        self.web = WebSearch(settings)

        self.agent = Agent(
            name="Echo",
            instructions=TOOL_INSTRUCTIONS,
            model=OpenAIChatCompletionsModel(model=settings.model, openai_client=self.client),
            model_settings=ModelSettings(temperature=settings.model_temperature),
            tools=[build_time_tool(settings.timezone), *self.web.build_tools()],
            output_type=AIResponse,
        )

    async def chat(self, messages: list[ChatMessage]) -> AIResponse:
        logger.debug("AI request sent: model=%s, message_count=%d", self.settings.model, len(messages))

        try:
            result = await Runner.run(
                starting_agent=self.agent,
                input=[{"role": m.role, "content": m.content} for m in messages],
                max_turns=self.settings.ai_max_turns,
            )
        except Exception as error:
            logger.error("AI service error: %s: %s", error.__class__.__name__, error)
            raise AIServiceError("AI service request failed") from error

        parsed = result.final_output
        if not isinstance(parsed, AIResponse) or not parsed.dialogue.strip():
            raise AIServiceError("AI response did not contain a valid result")

        return parsed

    async def close(self) -> None:
        await self.client.close()
        await self.web.close()
