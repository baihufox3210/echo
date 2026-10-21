import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from echo.ai.safety import PromptGuard

logger = logging.getLogger(__name__)

EventCallback = Callable[["AgentEvent"], Awaitable[None]]


@dataclass(slots=True)
class AgentEvent:
    """代理執行過程中的事件，用於即時更新 Discord 訊息。

    kind: tool（呼叫工具）、text（回覆文字更新）、retry（重新執行）
    """

    kind: str
    text: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunContext:
    """單次對話請求的執行狀態，於主代理與子代理之間共用。"""

    user_id: int
    guard: PromptGuard
    tool_budget: int
    attachments: dict[str, str] = field(default_factory=dict)
    seen_urls: set[str] = field(default_factory=set)
    on_event: EventCallback | None = None

    def consume_budget(self) -> bool:
        if self.tool_budget <= 0:
            return False
        self.tool_budget -= 1
        return True

    async def emit(self, event: AgentEvent) -> None:
        if self.on_event is not None:
            try:
                await self.on_event(event)
            except Exception:
                logger.debug("Event callback failed", exc_info=True)


BUDGET_EXHAUSTED = "本次對話的工具使用次數已達上限，請根據目前已取得的資訊直接回答。"
