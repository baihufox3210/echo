import logging
import time
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
from echo.ai.models import AIResponse, CharacterState, ChatMessage, TokenUsage
from echo.ai.research import build_research_tool, finalize_without_tools
from echo.ai.output import response_text, salvage_response
from echo.ai.streaming import DialogueStream
from echo.ai.tools import (
    ApiClient,
    WebSearch,
    build_attachment_tool,
    build_calculator_tool,
    build_channel_tools,
    build_fun_tools,
    build_knowledge_tools,
    build_memory_tool,
    build_note_tools,
    build_reminder_tools,
    build_time_tools,
    build_weather_tool,
)
from echo.core.config import Settings
from echo.core.errors import AIOverloadedError, AIServiceError
from echo.memory.long_term import LongTermMemory
from echo.storage.notes import NoteStore
from echo.storage.reminders import ReminderStore

logger = logging.getLogger(__name__)

set_tracing_disabled(True)

TOOL_INSTRUCTIONS = """\
# 工具使用規則
- 你可以在一次回覆中多次、連續或同時呼叫多個工具，直到資訊足夠再作答。不需要工具時直接回答。
- 工具只是幫助你，回覆仍要保持角色口吻，不要提及工具名稱。

## 時間與計算
- 確認日期時間用 get_current_time（可指定其他時區）；跨時區換算用 convert_time。
- 數字計算、換算用 calculate，不要心算；貨幣換算用 convert_currency。
- 玩遊戲、擲骰或幫忙做決定時，用 roll_dice 或 random_pick，結果以工具實際回傳為準。

## 記憶與關心
- 對方透露稱呼、生日、喜好、近況、重要約定等值得長期記住的事時，用 remember_note 記下；資訊改變時覆蓋，對方要求忘記時用 forget_note。
- 「關於對方的筆記」會出現在系統訊息中，請自然地運用，不要逐條複述。
- 對方提到過去的事而目前資訊不足時，用 recall_memory 回想。
- 對方要求「提醒我…」時，用 set_reminder 設定（time 使用預設時區，或用 minutes_from_now）；查詢或取消用 list_reminders、cancel_reminder。不可主動替對方設定提醒。

## 對話情境
- 在群組中，對方提到「上面」、「剛剛大家說的」或你需要更多前後文時，用 read_channel_history。
- 想表達情緒時，可以用 add_reaction 對對方的訊息加上 1～2 個表情反應（不要每次都用）。
- 使用者上傳的檔案若內容被截斷，用 read_attachment 讀取後續內容。

## 查詢資訊
- 新聞、時事、價格、賽事、版本更新等即時資訊，或你不確定、需要查證時，先查再答，不可憑記憶編造。
  - 天氣：get_weather。
  - 百科知識（人物、地點、作品、概念）：wikipedia。
  - 一般問題：web_search，可使用進階條件：time_range（day／week／month）、category（news、it、science、videos）、site、exclude_sites、exact_phrase、exclude_words、filetype、language、page。
  - 需要多角度或中英文並查：multi_search。
  - 複雜、需要比較或深入查證的問題：deep_research。
  - 搜尋摘要不足時，用 web_fetch 閱讀 1～3 個最相關的網址；長文件可依提示用 start 分段讀取。
  - 第一次結果不理想時，更換關鍵字、語言或條件再查。
- 依據網路資料回答時，以角色口吻自然轉述重點，並把實際引用的網址填入 sources 欄位，不要在 dialogue 中貼網址。
- 查詢失敗或查無資料時，誠實表示找不到，不要捏造內容。
"""

class InvalidOutputError(Exception):
    pass


OVERLOAD_ERRORS = (openai.RateLimitError, openai.InternalServerError)

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
    def __init__(
        self,
        settings: Settings,
        history: LongTermMemory,
        notes: NoteStore,
        reminders: ReminderStore,
    ):
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.ai_timeout,
            max_retries=settings.ai_max_retries,
        )
        self.web = WebSearch(settings)
        self.api = ApiClient(timeout=settings.search_timeout)
        model = OpenAIChatCompletionsModel(model=settings.model, openai_client=self.client)

        disabled = settings.disabled_tool_names

        def enabled(candidates: list) -> list:
            return [tool for tool in candidates if tool.name not in disabled]

        time_tools = build_time_tools(settings.timezone)
        web_tools = self.web.build_tools()
        knowledge_tools = [build_weather_tool(self.api), *build_knowledge_tools(self.api)]
        tools = [
            *time_tools,
            build_calculator_tool(),
            *build_fun_tools(),
            *web_tools,
            *knowledge_tools,
            build_attachment_tool(),
            *build_note_tools(notes),
            *build_reminder_tools(reminders, settings.timezone),
            *build_channel_tools(),
        ]
        if history.enabled:
            tools.append(build_memory_tool(history, settings.mem0_top_k))
        if settings.enable_research and self.web.enabled:
            research_tools = enabled([*time_tools, *web_tools, *knowledge_tools])
            tools.append(build_research_tool(model, research_tools, settings.research_max_turns))
        tools = enabled(tools)

        self.agent = Agent[RunContext](
            name="Echo",
            instructions=TOOL_INSTRUCTIONS,
            model=model,
            model_settings=ModelSettings(
                temperature=settings.model_temperature,
                max_tokens=settings.max_output_tokens,
                parallel_tool_calls=True,
            ),
            tools=tools,
            output_type=AIResponse,
        )
        self.fallback_agent = (
            self.agent.clone(
                name="Echo (fallback)",
                model=OpenAIChatCompletionsModel(model=settings.fallback_model, openai_client=self.client),
            )
            if settings.fallback_model
            else None
        )

    async def chat(self, messages: list[ChatMessage], context: RunContext, stream: bool = False) -> RunOutcome:
        """執行代理；可重試的錯誤會重新執行，最多 ai_max_runs 次。

        主要模型過載時若設定了備援模型，會改用備援模型再試（額外多一次機會）。
        """
        has_images = any(m.images for m in messages)
        agent = self.agent
        max_runs = self.settings.ai_max_runs
        attempt = 0

        while attempt < max_runs:
            attempt += 1
            try:
                return await self._run(agent, messages, context, stream)
            except openai.BadRequestError as error:
                # 模型不支援圖片時，移除圖片後再試一次
                if has_images and attempt < max_runs:
                    logger.warning("Request with images rejected, retrying without images: %s", error)
                    messages = [ChatMessage(m.role, m.content + ("\n（附加的圖片無法讀取）" if m.images else "")) for m in messages]
                    has_images = False
                    await context.emit(AgentEvent("retry"))
                    continue
                logger.error("AI request rejected: %s", error)
                raise AIServiceError("AI service rejected the request") from error
            except RETRYABLE_ERRORS as error:
                logger.warning(
                    "AI run %d/%d failed (model=%s): %s: %s",
                    attempt, max_runs, agent.model.model, error.__class__.__name__, error,
                )
                overloaded = isinstance(error, OVERLOAD_ERRORS)
                if overloaded and self.fallback_agent is not None and agent is not self.fallback_agent:
                    logger.warning("Switching to fallback model %s", self.settings.fallback_model)
                    agent = self.fallback_agent
                    max_runs = max(max_runs, attempt + 1)
                elif attempt >= max_runs:
                    if overloaded:
                        raise AIOverloadedError("AI service is overloaded") from error
                    raise AIServiceError("AI service request failed") from error
                await context.emit(AgentEvent("retry"))
            except Exception as error:
                logger.exception("Unexpected AI error")
                raise AIServiceError("AI service request failed") from error

        raise AIServiceError("AI service request failed")

    async def _run(
        self,
        agent: Agent[RunContext],
        messages: list[ChatMessage],
        context: RunContext,
        stream: bool,
    ) -> RunOutcome:
        logger.debug("AI request: model=%s messages=%d stream=%s", agent.model.model, len(messages), stream)
        inputs = [m.to_input() for m in messages]

        async def on_max_turns(data: RunErrorHandlerInput[RunContext]) -> RunErrorHandlerResult:
            logger.info("Max turns reached; finalizing without tools")
            return await finalize_without_tools(agent, data)

        options = {
            "context": context,
            "max_turns": self.settings.ai_max_turns,
            "error_handlers": {
                "max_turns": on_max_turns,
                "invalid_final_output": self._on_invalid_output,
            },
        }

        if stream:
            result = Runner.run_streamed(agent, inputs, **options)
            await self._consume_stream(result, context)
        else:
            result = await Runner.run(agent, inputs, **options)

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
        dialogue = DialogueStream()
        started = time.monotonic()
        first_delta: float | None = None
        deltas = 0

        async for event in result.stream_events():
            if event.type != "raw_response_event":
                continue
            match getattr(event.data, "type", None):
                case "response.created":
                    dialogue.reset()
                case "response.output_text.delta":
                    deltas += 1
                    if first_delta is None:
                        first_delta = time.monotonic() - started
                    if text := dialogue.feed(event.data.delta):
                        await context.emit(AgentEvent("text", text))

        # 用於判斷 API 是否真的有串流：deltas 很少代表上游一次性回傳整段內容
        logger.info(
            "Stream finished: deltas=%d chars=%d first_delta=%.1fs total=%.1fs",
            deltas, len(dialogue.buffer),
            first_delta if first_delta is not None else -1, time.monotonic() - started,
        )

    @staticmethod
    async def _on_invalid_output(data: RunErrorHandlerInput[RunContext]) -> RunErrorHandlerResult | None:
        """模型輸出不符合 JSON schema 時，嘗試寬鬆解析，避免整個請求失敗。"""
        text = response_text(data.run_data.raw_responses)
        preview = text[:300].replace("\n", "\\n")
        context = data.context.context
        fallback_state = context.fallback_state or CharacterState(affection=0, mood=50, stamina=100, trust=0)

        salvaged = salvage_response(text, fallback_state)
        if salvaged is None:
            logger.warning("Invalid model output could not be salvaged: %r", preview)
            return None

        logger.warning("Model output did not match schema; salvaged it. Raw output: %r", preview)
        return RunErrorHandlerResult(final_output=salvaged)

    @staticmethod
    def _tools_used(result: RunResult | RunResultStreaming) -> list[str]:
        names = []
        for item in result.new_items:
            if item.type == "tool_call_item":
                names.append(getattr(item.raw_item, "name", None) or "tool")
        return names

    async def close(self) -> None:
        await self.client.close()
        await self.web.close()
        await self.api.close()
