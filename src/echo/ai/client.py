import logging
from dataclasses import dataclass, field

import openai
from agents import (
    Agent,
    AgentsException,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunResult,
    RunResultStreaming,
    Runner,
    set_tracing_disabled,
)
from agents.run_error_handlers import RunErrorHandlerInput, RunErrorHandlerResult
from openai import AsyncOpenAI

from echo.ai.context import AgentEvent, RunContext
from echo.ai.models import AIResponse, ChatMessage, TokenUsage
from echo.ai.research import build_research_tool, finalize_without_tools
from echo.ai.streaming import PartialFieldExtractor
from echo.ai.tools import (
    WebSearch,
    build_attachment_tool,
    build_calculator_tool,
    build_memory_tool,
    build_time_tool,
)
from echo.core.config import Settings
from echo.core.errors import AIServiceError
from echo.memory.long_term import LongTermMemory

logger = logging.getLogger(__name__)

set_tracing_disabled(True)

TOOL_INSTRUCTIONS = """\
# 工具使用規則
- 你可以在一次回覆中多次、連續或同時呼叫多個工具，直到資訊足夠再作答。
- 需要確認日期或時間時，使用 get_current_time；涉及數字計算、換算時使用 calculate，不要心算。
- 對方提到過去的事、約定或喜好，而目前提供的長期記憶不足時，使用 recall_memory 回想。
- 使用者上傳的檔案若內容被截斷，使用 read_attachment 讀取後續內容。
- 新聞、時事、天氣、價格、賽事、版本更新等即時資訊，或你不確定、需要查證時，先搜尋，不可憑記憶編造。
  - 簡單問題：web_search。可使用進階條件：time_range（day／week／month）、category（news、it、science、videos）、site 限定網站、exclude_sites、exact_phrase、exclude_words、filetype、language、page。
  - 需要多角度或中英文並查：multi_search。
  - 複雜、需要比較或深入查證的問題：deep_research。
  - 搜尋摘要不足時，用 web_fetch 閱讀 1～3 個最相關的網址；長文件可依提示用 start 分段讀取。
  - 第一次結果不理想時，更換關鍵字、語言或條件再查。
- 依據網路資料回答時，以角色口吻自然轉述重點，並把實際引用的網址填入 sources 欄位，不要在 dialogue 中貼網址。
- 搜尋失敗或查無資料時，誠實表示找不到，不要捏造內容。
- 不需要工具時直接回答。
"""

class InvalidOutputError(Exception):
    pass


RETRYABLE_ERRORS = (
    InvalidOutputError,
    AgentsException,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


@dataclass(slots=True)
class RunOutcome:
    response: AIResponse
    usage: TokenUsage
    tools_used: list[str] = field(default_factory=list)


class AIClient:
    def __init__(self, settings: Settings, history: LongTermMemory):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.ai_timeout,
            max_retries=settings.ai_max_retries,
        )
        self.web = WebSearch(settings)
        model = OpenAIChatCompletionsModel(model=settings.model, openai_client=self.client)

        time_tool = build_time_tool(settings.timezone)
        web_tools = self.web.build_tools()
        tools = [time_tool, build_calculator_tool(), *web_tools, build_attachment_tool()]
        if history.enabled:
            tools.append(build_memory_tool(history, settings.mem0_top_k))
        if settings.enable_research and self.web.enabled:
            tools.append(build_research_tool(model, [time_tool, *web_tools], settings.research_max_turns))

        self.agent = Agent[RunContext](
            name="Echo",
            instructions=TOOL_INSTRUCTIONS,
            model=model,
            model_settings=ModelSettings(
                temperature=settings.model_temperature,
                parallel_tool_calls=True,
            ),
            tools=tools,
            output_type=AIResponse,
        )

    async def chat(self, messages: list[ChatMessage], context: RunContext, stream: bool = False) -> RunOutcome:
        """執行代理；可重試的錯誤會重新執行，最多 ai_max_runs 次。"""
        has_images = any(m.images for m in messages)

        for attempt in range(1, self.settings.ai_max_runs + 1):
            try:
                outcome = await self._run(messages, context, stream)
            except openai.BadRequestError as error:
                # 模型不支援圖片時，移除圖片後再試一次
                if has_images and attempt < self.settings.ai_max_runs:
                    logger.warning("Request with images rejected, retrying without images")
                    messages = [ChatMessage(m.role, m.content + ("\n（附加的圖片無法讀取）" if m.images else "")) for m in messages]
                    has_images = False
                    await context.emit(AgentEvent("retry"))
                    continue
                logger.error("AI request rejected: %s", error)
                raise AIServiceError("AI service rejected the request") from error
            except RETRYABLE_ERRORS as error:
                logger.warning("AI run %d/%d failed: %s: %s", attempt, self.settings.ai_max_runs, error.__class__.__name__, error)
                if attempt >= self.settings.ai_max_runs:
                    raise AIServiceError("AI service request failed") from error
                await context.emit(AgentEvent("retry"))
                continue
            except Exception as error:
                logger.exception("Unexpected AI error")
                raise AIServiceError("AI service request failed") from error

            return outcome

        raise AIServiceError("AI service request failed")

    async def _run(self, messages: list[ChatMessage], context: RunContext, stream: bool) -> RunOutcome:
        logger.debug("AI request: model=%s messages=%d stream=%s", self.settings.model, len(messages), stream)
        inputs = [m.to_input() for m in messages]
        options = {
            "context": context,
            "max_turns": self.settings.ai_max_turns,
            "error_handlers": {"max_turns": self._on_max_turns},
        }

        if stream:
            result = Runner.run_streamed(self.agent, inputs, **options)
            await self._consume_stream(result, context)
        else:
            result = await Runner.run(self.agent, inputs, **options)

        parsed = result.final_output
        if not isinstance(parsed, AIResponse) or not parsed.dialogue.strip():
            raise InvalidOutputError("AI response did not contain a valid result")

        usage = result.context_wrapper.usage
        return RunOutcome(
            response=parsed,
            usage=TokenUsage(usage.requests, usage.input_tokens, usage.output_tokens),
            tools_used=self._tools_used(result),
        )

    @staticmethod
    async def _consume_stream(result: RunResultStreaming, context: RunContext) -> None:
        extractor = PartialFieldExtractor("dialogue")
        async for event in result.stream_events():
            if event.type != "raw_response_event":
                continue
            match getattr(event.data, "type", None):
                case "response.created":
                    extractor.reset()
                case "response.output_text.delta":
                    if text := extractor.feed(event.data.delta):
                        await context.emit(AgentEvent("text", text))

    @staticmethod
    def _tools_used(result: RunResult | RunResultStreaming) -> list[str]:
        names = []
        for item in result.new_items:
            if item.type == "tool_call_item":
                names.append(getattr(item.raw_item, "name", None) or "tool")
        return names

    async def _on_max_turns(self, data: RunErrorHandlerInput[RunContext]) -> RunErrorHandlerResult:
        logger.info("Max turns reached; finalizing without tools")
        return await finalize_without_tools(self.agent, data)

    async def close(self) -> None:
        await self.client.close()
        await self.web.close()
