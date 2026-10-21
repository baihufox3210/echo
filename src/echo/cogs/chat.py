import logging
import re

import discord
from discord.ext import commands

from echo.core.bot import Bot
from echo.core.concurrency import RateLimiter
from echo.core.errors import EchoError
from echo.core.text import split_message

logger = logging.getLogger(__name__)

EMPTY_MESSAGE_PLACEHOLDER = "（呼喚了你，但沒有說話）"


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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self._should_respond(message):
            return

        content = self._clean_content(message) or EMPTY_MESSAGE_PLACEHOLDER

        if len(content) > self.settings.max_message_length:
            await self._reply(message, f"訊息太長了（上限 {self.settings.max_message_length} 字）。")
            return

        retry_after = self.rate_limiter.hit(message.author.id)
        if retry_after:
            logger.warning("Rate limit exceeded for user %d", message.author.id)
            await self._reply(message, f"請求過於頻繁，請 {retry_after:.0f} 秒後再試。", delete_after=10)
            return

        try:
            async with message.channel.typing():
                result = await self.bot.services.ai.chat(
                    user_id=message.author.id,
                    user_message=content,
                )
        except EchoError:
            logger.exception("Chat service failed")
            await self._reply(message, "抱歉，服務暫時無法回應，請稍後再試。")
            return
        except Exception:
            logger.exception("Unexpected chat error")
            await self._reply(message, "抱歉，剛才好像出了點問題……")
            return

        await self._reply(message, result.dialogue)

    async def _reply(self, message: discord.Message, text: str, delete_after: float | None = None) -> None:
        """以「回覆」形式傳送訊息，但不 ping 原作者或任何人。"""
        try:
            for index, chunk in enumerate(split_message(text)):
                await message.channel.send(
                    chunk,
                    reference=message.to_reference(fail_if_not_exists=False) if index == 0 else None,
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                    suppress_embeds=True,
                    delete_after=delete_after,
                )
        except discord.HTTPException:
            logger.exception("Failed to send reply in channel %s", message.channel.id)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Chat(bot))
