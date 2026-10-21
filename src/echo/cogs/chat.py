import logging
import re

import discord
from discord.ext import commands

from echo.ai.attachments import IncomingFile
from echo.ai.context import ChatOrigin
from echo.core.bot import Bot
from echo.core.concurrency import RateLimiter
from echo.core.embeds import reply_embed
from echo.core.errors import AIOverloadedError, EchoError
from echo.core.reply import ReplyStreamer, send_reply

logger = logging.getLogger(__name__)

EMPTY_MESSAGE_PLACEHOLDER = "（呼喚了你，但沒有說話）"
ATTACHMENT_ONLY_PLACEHOLDER = "（傳送了附件）"
QUOTED_MESSAGE_CHARS = 1500


class Chat(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = bot.services.settings
        self.rate_limiter = RateLimiter(
            limit=self.settings.rate_limit_per_user,
            window=self.settings.rate_limit_window,
        )

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

    @staticmethod
    def _origin(message: discord.Message) -> ChatOrigin:
        return ChatOrigin(channel_id=message.channel.id, is_dm=message.guild is None)

    @staticmethod
    def _describe(message: discord.Message, limit: int) -> str:
        content = message.clean_content.strip()
        if len(content) > limit:
            content = content[:limit] + "…"
        if message.attachments:
            content += f"［附件：{'、'.join(a.filename for a in message.attachments)}］"
        return content or "（無文字內容）"

    def _quoted(self, message: discord.Message) -> str | None:
        """使用者回覆某則訊息時，提供被回覆的內容作為前後文，並標明訊息屬於誰。"""
        if message.reference is None or not isinstance(message.reference.resolved, discord.Message):
            return None
        replied = message.reference.resolved
        content = self._describe(replied, QUOTED_MESSAGE_CHARS)

        if replied.author.id != self.bot.user.id:
            return f"其他使用者「{replied.author.display_name}」的訊息：{content}"

        target = self._reply_target(replied)
        if target is None or target == message.author.id:
            return f"你先前傳給這位使用者的訊息：{content}"
        return f"你先前回覆「其他使用者」的訊息（不是與這位使用者的對話）：{content}"

    def _reply_target(self, bot_message: discord.Message) -> int | None:
        """找出 Bot 的某則訊息原本是回覆給哪位使用者；查不到時回傳 None。"""
        reference = bot_message.reference
        if reference is None or reference.message_id is None:
            return None
        original = reference.resolved if isinstance(reference.resolved, discord.Message) else discord.utils.get(
            self.bot.cached_messages, id=reference.message_id
        )
        return original.author.id if original is not None else None

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
        except AIOverloadedError:
            logger.warning("AI service overloaded; request from user %d dropped", message.author.id)
            await streamer.finish("AI 服務現在太忙了，請過一下再試一次。")
            return
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
