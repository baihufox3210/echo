import logging
import time

import discord
from discord.ext import commands

from echo.core.bot import Bot
from echo.core.errors import EchoError

logger = logging.getLogger(__name__)


class Chat(commands.Cog):
    MAX_MESSAGE_LENGTH = 8000
    RATE_LIMIT_PER_USER = 3
    RATE_LIMIT_WINDOW = 60
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.user_request_times = {}
    
    def _validate_message(self, content: str) -> bool:
        """Validate message content."""
        if len(content) > self.MAX_MESSAGE_LENGTH:
            return False
        return bool(content.strip())
    
    def _check_rate_limit(self, user_id: int) -> bool:
        """Check rate limit for user."""
        now = time.time()
        
        if user_id not in self.user_request_times:
            self.user_request_times[user_id] = []
        
        self.user_request_times[user_id] = [
            t for t in self.user_request_times[user_id]
            if now - t < self.RATE_LIMIT_WINDOW
        ]
        
        if len(self.user_request_times[user_id]) >= self.RATE_LIMIT_PER_USER:
            return False
        
        self.user_request_times[user_id].append(now)
        return True
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        if self.bot.user is None: return
        if self.bot.user not in message.mentions: return
        
        content = message.content.replace(self.bot.user.mention, "").strip()
        
        if not self._validate_message(content):
            await message.channel.send("訊息過長或內容無效。")
            return
        
        if not self._check_rate_limit(message.author.id):
            await message.channel.send("請求過於頻繁，請稍候再試。")
            logger.warning("Rate limit exceeded for user %d", message.author.id)
            return
        
        try:
            async with message.channel.typing():
                result = await self.bot.services.ai.chat(
                    user_id=message.author.id,
                    user_message=content,
                )
            
            await message.channel.send(result.dialogue)
            
        except EchoError:
            logger.exception("Chat service failed")
            await message.channel.send("抱歉，服務暫時無法回應，請稍後再試。")
        except Exception:
            logger.exception("Unexpected chat error")
            await message.channel.send("抱歉，剛才好像出了點問題……")

async def setup(bot: Bot):
    await bot.add_cog(Chat(bot))