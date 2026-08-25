import discord
from discord.ext import commands

from echo.core.bot import Bot

class Chat(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        if self.bot.user is None: return
        if self.bot.user not in message.mentions: return
        
        content = message.content.replace(self.bot.user.mention, "").strip()
        if not content: return
        
        try:
            async with message.channel.typing():
                result = await self.bot.services.ai.chat(
                    user_id=message.author.id,
                    user_message=content,
                )
            
            await message.channel.send(result.dialogue)
            
        except Exception:
            await message.channel.send("抱歉，剛才好像出了點問題……")

async def setup(bot: Bot):
    await bot.add_cog(Chat(bot))