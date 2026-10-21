import asyncio
import logging
from collections.abc import Coroutine
from zoneinfo import ZoneInfo

from echo.ai.client import AIClient
from echo.ai.models import AIResponse, CharacterState, ChatMessage
from echo.ai.tools import now_text
from echo.core.concurrency import KeyedLock
from echo.core.config import Settings
from echo.core.errors import EchoError
from echo.memory.long_term import LongTermMemory
from echo.memory.short_term import ShortTermMemory
from echo.prompts.loader import PromptLoader
from echo.storage.character_state import CharacterStateStore

logger = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT = 30.0


class AIService:
    """組合 Prompt、記憶與角色狀態並呼叫 AI。

    不同使用者的請求可並行處理；同一使用者的請求依序處理，
    以確保對話紀錄與角色狀態不會互相覆蓋。
    """

    def __init__(
        self,
        client: AIClient,
        prompt_loader: PromptLoader,
        memory: ShortTermMemory,
        history: LongTermMemory,
        state_store: CharacterStateStore,
        settings: Settings,
    ):
        self.client = client
        self.prompt_loader = prompt_loader
        self.memory = memory
        self.history = history
        self.state_store = state_store
        self.settings = settings

        self.user_lock = KeyedLock()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._background: set[asyncio.Task] = set()
        self._tz = ZoneInfo(settings.timezone)

    async def chat(self, user_id: int, user_message: str) -> AIResponse:
        async with self.user_lock(user_id):
            messages = await self._build_messages(user_id, user_message)

            async with self._semaphore:
                result = await self.client.chat(messages=messages)

            exchange = [
                ChatMessage(role="user", content=user_message),
                ChatMessage(role="assistant", content=result.dialogue),
            ]
            try:
                await self.memory.add(user_id=user_id, messages=exchange)
                await self.state_store.save(user_id=user_id, state=result.state)
            except EchoError:
                logger.exception("Failed to persist conversation for user %d", user_id)

        # 長期記憶寫入較慢（需由記憶服務抽取重點），於背景執行以免拖慢回覆
        if self.history.enabled:
            self._spawn(self._remember(user_id, exchange))

        return result

    async def _build_messages(self, user_id: int, user_message: str) -> list[ChatMessage]:
        short_term, long_term, state = await asyncio.gather(
            self.memory.get(user_id=user_id),
            self._search_long_term(user_id, user_message),
            self.state_store.get(user_id=user_id),
        )

        memory_prompt = "\n".join(f"- {item}" for item in long_term) or "目前沒有相關的長期記憶。"

        system_prompt = "\n\n".join([
            self.prompt_loader.load_system_prompt(),
            self._render_state_prompt(state),
            f"目前時間：{now_text(self._tz)}",
            f"相關長期記憶：\n{memory_prompt}",
        ])

        return [
            ChatMessage(role="system", content=system_prompt),
            *short_term,
            ChatMessage(role="user", content=user_message),
        ]

    def _render_state_prompt(self, state: CharacterState) -> str:
        prompt = self.prompt_loader.load_character_state_prompt()
        values = state.model_dump()

        for key, value in values.items():
            prompt = prompt.replace(f"{{{key}}}", str(value))

        current = "\n".join(f"- {key}：{value}" for key, value in values.items())
        return f"{prompt}\n\n目前角色狀態：\n{current}"

    async def _search_long_term(self, user_id: int, query: str) -> list[str]:
        if not self.history.enabled:
            return []

        try:
            return await self.history.search(query=query, user_id=user_id, top_k=self.settings.mem0_top_k)
        except EchoError:
            logger.warning("Long-term memory search failed; continuing without it")
            return []

    async def _remember(self, user_id: int, exchange: list[ChatMessage]) -> None:
        try:
            await self.history.add(messages=exchange, user_id=user_id)
        except EchoError:
            logger.warning("Failed to store long-term memory for user %d", user_id)

    def _spawn(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def close(self) -> None:
        if self._background:
            logger.info("Waiting for %d background tasks", len(self._background))
            _, pending = await asyncio.wait(self._background, timeout=SHUTDOWN_TIMEOUT)
            for task in pending:
                task.cancel()

        await self.client.close()
