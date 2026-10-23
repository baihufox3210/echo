"""AI API 請求節流：所有模型請求（主代理、研究子代理、備援模型）共用。

- 每分鐘請求上限（可選）：送出前排隊，避免撞到供應商的 RPM 額度。
- 429 冷卻：任一請求收到 429 後，所有請求暫停一段時間（優先採用 Retry-After），
  避免多位使用者與 SDK 的自動重試在額度用盡時持續打 API，讓情況更糟。

以 httpx event hook 掛在 OpenAI 客戶端底層，因此 SDK 的自動重試也會遵守冷卻。
"""

import asyncio
import email.utils
import logging
import time
from collections import deque
from contextvars import ContextVar

import httpx

from echo.ai.context import AgentEvent, RunContext

logger = logging.getLogger(__name__)

WINDOW = 60.0
MAX_COOLDOWN = 60.0
# 等待超過此秒數時通知使用者「排隊中」，避免短暫等待也跳出提示
NOTIFY_AFTER = 1.0

# 目前正在執行的對話，用於在排隊時通知該使用者（Runner 會將 contextvar 帶入子任務）
current_run: ContextVar[RunContext | None] = ContextVar("current_run", default=None)


def parse_retry_after(value: str | None) -> float | None:
    """解析 Retry-After 標頭（秒數或 HTTP 日期）。"""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


class ApiThrottle:
    def __init__(self, requests_per_minute: int = 0, cooldown: float = 10.0):
        self.requests_per_minute = requests_per_minute
        self.cooldown = cooldown
        self._cooldown_until = 0.0
        self._recent: deque[float] = deque()
        # 序列化排程，確保每分鐘上限計算正確，且等待中的請求依序送出
        self._lock = asyncio.Lock()

    def _wait_time(self, now: float) -> float:
        wait = self._cooldown_until - now
        if self.requests_per_minute > 0:
            while self._recent and now - self._recent[0] >= WINDOW:
                self._recent.popleft()
            if len(self._recent) >= self.requests_per_minute:
                wait = max(wait, WINDOW - (now - self._recent[0]))
        return wait

    async def before_request(self, request: httpx.Request) -> None:
        # 在排隊前先通知：前面的請求可能正持有鎖並等待中
        if (wait := self._wait_time(time.monotonic())) >= NOTIFY_AFTER or self._lock.locked():
            logger.info("Throttling AI request (estimated wait %.1fs)", max(wait, 0))
            if (run := current_run.get()) is not None:
                await run.emit(AgentEvent("tool", "◷ AI 忙線中，稍候自動重試…"))

        async with self._lock:
            while (wait := self._wait_time(time.monotonic())) > 0:
                await asyncio.sleep(wait)
            if self.requests_per_minute > 0:
                self._recent.append(time.monotonic())

    async def after_response(self, response: httpx.Response) -> None:
        if response.status_code != 429:
            return
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        delay = min(retry_after if retry_after is not None else self.cooldown, MAX_COOLDOWN)
        until = time.monotonic() + delay
        if until > self._cooldown_until:
            self._cooldown_until = until
            logger.warning(
                "AI API rate limited (429); pausing all AI requests for %.0fs%s",
                delay, "" if retry_after is not None else " (no Retry-After header)",
            )

    def http_client(self) -> httpx.AsyncClient:
        """建立掛有節流 hook 的 httpx 客戶端，保留 OpenAI SDK 的預設連線設定。"""
        from openai import DefaultAsyncHttpxClient

        return DefaultAsyncHttpxClient(
            event_hooks={"request": [self.before_request], "response": [self.after_response]},
        )
