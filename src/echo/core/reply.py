import asyncio
import logging

import discord

from echo.ai.context import AgentEvent
from echo.core.text import DISCORD_MESSAGE_LIMIT, split_message

logger = logging.getLogger(__name__)

STREAM_PREVIEW_LIMIT = DISCORD_MESSAGE_LIMIT - 100
CURSOR = " ▌"


async def send_reply(
    source: discord.Message,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    reference: bool = True,
    delete_after: float | None = None,
) -> discord.Message:
    """以「回覆」形式傳送，但不 ping 原作者或任何人。"""
    return await source.channel.send(
        content,
        embed=embed,
        reference=source.to_reference(fail_if_not_exists=False) if reference else None,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
        delete_after=delete_after,
    )


class ReplyStreamer:
    """將代理的執行進度（工具狀態、逐步生成的文字）即時編輯到同一則回覆。

    更新以固定間隔合併送出，避免觸發 Discord 的編輯頻率限制。
    """

    def __init__(self, source: discord.Message, interval: float):
        self.source = source
        self.interval = interval
        self.message: discord.Message | None = None
        self._status = ""
        self._text = ""
        self._dirty = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._inflight: asyncio.Future | None = None
        self._closed = False

    async def handle(self, event: AgentEvent) -> None:
        if self._closed:
            return

        match event.kind:
            case "tool":
                self._status = event.text
            case "text":
                self._text = event.text
            case "retry":
                self._text = ""
                self._status = "🔄 重新整理回覆中…"

        self._dirty.set()
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    def _render(self) -> str:
        if self._text:
            text = self._text
            if len(text) > STREAM_PREVIEW_LIMIT:
                text = text[:STREAM_PREVIEW_LIMIT] + "…"
            return text + CURSOR
        return self._status or "💭 思考中…"

    async def _run(self) -> None:
        while not self._closed:
            await self._dirty.wait()
            self._dirty.clear()
            # shield：即使串流被結束，進行中的傳送仍會完成，避免重複傳送訊息
            self._inflight = asyncio.ensure_future(self._push(self._render()))
            await asyncio.shield(self._inflight)
            await asyncio.sleep(self.interval)

    async def _push(self, content: str) -> None:
        try:
            if self.message is None:
                self.message = await send_reply(self.source, content)
            else:
                await self.message.edit(content=content)
        except discord.HTTPException:
            logger.warning("Failed to update streaming reply", exc_info=True)

    async def _stop(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._inflight is not None:
            await asyncio.gather(self._inflight, return_exceptions=True)

    async def finish(self, text: str, embed: discord.Embed | None = None) -> None:
        """送出最終回覆：角色對話為第一則（過長時分段），資訊卡另成獨立的第二則訊息。"""
        await self._stop()
        chunks = split_message(text) or ["……"]

        try:
            for index, chunk in enumerate(chunks):
                if index == 0 and self.message is not None:
                    await self.message.edit(content=chunk, embed=None)
                else:
                    await send_reply(self.source, chunk, reference=index == 0)

            if embed is not None:
                await send_reply(self.source, None, embed=embed, reference=False)
        except discord.HTTPException:
            logger.exception("Failed to send reply in channel %s", self.source.channel.id)
