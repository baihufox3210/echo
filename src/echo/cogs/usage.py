import logging
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from echo.core.bot import Bot
from echo.core.embeds import EMBED_COLOR, usage_lines

logger = logging.getLogger(__name__)

PERIODS = (("今日", 1), ("近 7 天", 7), ("近 30 天", 30), ("累計", None))


class Usage(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.store = bot.services.usage

    @app_commands.command(name="usage", description="查看 AI 使用量")
    @app_commands.describe(scope="查看自己或所有使用者（後者限 Bot 擁有者）")
    async def usage(self, interaction: discord.Interaction, scope: Literal["我的", "全部"] = "我的"):
        await interaction.response.defer(ephemeral=True)

        if scope == "全部":
            if not await self.bot.is_owner(interaction.user):
                await interaction.followup.send("你沒有權限查看所有使用者的用量。", ephemeral=True)
                return
            embed = await self._global_embed()
        else:
            embed = await self._user_embed(interaction.user)

        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _user_embed(self, user: discord.abc.User) -> discord.Embed:
        embed = discord.Embed(title="▤ 我的使用量", color=EMBED_COLOR)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        for label, days in PERIODS:
            summary = await self.store.summary(user_id=user.id, days=days)
            embed.add_field(name=label, value=usage_lines(summary), inline=False)
        return embed

    async def _global_embed(self) -> discord.Embed:
        embed = discord.Embed(title="▤ 全體使用量", color=EMBED_COLOR)
        for label, days in PERIODS:
            summary = await self.store.summary(days=days)
            embed.add_field(name=label, value=usage_lines(summary), inline=False)

        top = await self.store.top_users(days=30)
        if top:
            # 嵌入訊息中的提及不會 ping 使用者
            ranking = "\n".join(
                f"{index}. <@{user_id}> · {summary.total_tokens:,} tokens（{summary.messages} 則）"
                for index, (user_id, summary) in enumerate(top, start=1)
            )
            embed.add_field(name="近 30 天用量排行", value=ranking[:1024], inline=False)
        return embed


async def setup(bot: Bot) -> None:
    await bot.add_cog(Usage(bot))
