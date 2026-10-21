import logging
import re
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from echo.ai.attachments import IncomingFile
from echo.ai.context import ChannelMessage, ChatOrigin
from echo.core.bot import Bot
from echo.core.concurrency import RateLimiter
from echo.core.embeds import reply_embed
from echo.core.errors import EchoError
from echo.core.reply import ReplyStreamer, send_reply

logger = logging.getLogger(__name__)

EMPTY_MESSAGE_PLACEHOLDER = "（呼喚了你，但沒有說話）"
ATTACHMENT_ONLY_PLACEHOLDER = "（傳送了附件）"
HISTORY_MESSAGE_CHARS = 500
QUOTED_MESSAGE_CHARS = 1500


class Chat(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = bot.services.settings
        self.rate_limiter = RateLimiter(
            limit=self.settings.rate_limit_per_user,
            window=self.settings.rate_limit_window,
        )
        self.tz = ZoneInfo(self.settings.timezone)

    def _should_respond(self, message: discord.Message) -> bool:
        me = self.bot.user
        if me is None or message.author.bot or message.webhook_id is not None:
            return False

        if message.guild is None:
            return self.settings.respond_to_dm

        if me in message.mentions and re.search(rf"<@!?{me.id}>", message.content):
            return True

        if self.settings.respond_to_replies and message.reference is not None:
            replied = message.reference.resolved
            return isinstance(replied, discord.Message) and replied.author.id == me.id

        return False

    def _clean_content(self, message: discord.Message) -> str:
        me = self.bot.user
        content = re.sub(rf"<@!?{me.id}>", "", message.content)

        # 將其他使用者的提及轉為名稱，讓 AI 看得懂
        for user in message.mentions:
            if user.id != me.id:
                content = re.sub(rf"<@!?{user.id}>", f"@{user.display_name}", content)

        return content.strip()

    @staticmethod
    def _incoming_files(message: discord.Message) -> list[IncomingFile]:
        return [
            IncomingFile(
                filename=attachment.filename,
                content_type=attachment.content_type,
                size=attachment.size,
                read=attachment.read,
            )
            for attachment in message.attachments
        ]

    def _origin(self, message: discord.Message) -> ChatOrigin:
        me = self.bot.user

        async def read_history(limit: int) -> list[ChannelMessage]:
            history = [m async for m in message.channel.history(limit=limit, before=message)]
            history.reverse()
            return [
                ChannelMessage(
                    author=m.author.display_name,
                    content=self._describe(m, HISTORY_MESSAGE_CHARS),
                    created_at=m.created_at.astimezone(self.tz).strftime("%m-%d %H:%M"),
                    is_bot=m.author.id == me.id,
                )
                for m in history
            ]

        return ChatOrigin(
            channel_id=message.channel.id,
            is_dm=message.guild is None,
            read_history=read_history,
        )

    @staticmethod
    def _describe(message: discord.Message, limit: int) -> str:
        content = message.clean_content.strip()
        if len(content) > limit:
            content = content[:limit] + "…"
        if message.attachments:
            content += f"［附件：{'、'.join(a.filename for a in message.attachments)}］"
        return content or "（無文字內容）"

    def _quoted(self, message: discord.Message) -> str | None:
        """使用者回覆其他訊息時，提供被回覆的內容作為前後文。"""
        if message.reference is None or not isinstance(message.reference.resolved, discord.Message):
            return None
        replied = message.reference.resolved
        author = "你自己" if replied.author.id == self.bot.user.id else replied.author.display_name
        return f"{author}：{self._describe(replied, QUOTED_MESSAGE_CHARS)}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self._should_respond(message):
            return

        files = self._incoming_files(message)
        content = self._clean_content(message) or (
            ATTACHMENT_ONLY_PLACEHOLDER if files else EMPTY_MESSAGE_PLACEHOLDER
        )

        if len(content) > self.settings.max_message_length:
            await self._notify(message, f"訊息太長了（上限 {self.settings.max_message_length} 字）。")
            return

        retry_after = self.rate_limiter.hit(message.author.id)
        if retry_after:
            logger.warning("Rate limit exceeded for user %d", message.author.id)
            await self._notify(message, f"請求過於頻繁，請 {retry_after:.0f} 秒後再試。", delete_after=10)
            return

        streamer = ReplyStreamer(message, interval=self.settings.stream_edit_interval)
        try:
            async with message.channel.typing():
                result = await self.bot.services.ai.chat(
                    user_id=message.author.id,
                    user_message=content,
                    files=files,
                    on_event=streamer.handle,
                    origin=self._origin(message),
                    quoted=self._quoted(message),
                )
        except EchoError:
            logger.exception("Chat service failed")
            await streamer.finish("抱歉，服務暫時無法回應，請稍後再試。")
            return
        except Exception:
            logger.exception("Unexpected chat error")
            await streamer.finish("抱歉，剛才好像出了點問題……")
            return

        await streamer.finish(
            result.response.dialogue,
            embed=reply_embed(result, show_state=self.settings.show_state_embed),
        )

        for emoji in result.reactions:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                logger.debug("Failed to add reaction %s", emoji)

    @staticmethod
    async def _notify(message: discord.Message, text: str, delete_after: float | None = None) -> None:
        try:
            await send_reply(message, text, delete_after=delete_after)
        except discord.HTTPException:
            logger.warning("Failed to send notice in channel %s", message.channel.id)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Chat(bot))
