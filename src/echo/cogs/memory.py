import logging

import discord
from discord import app_commands
from discord.ext import commands

from echo.core.bot import Bot
from echo.core.errors import EchoError

logger = logging.getLogger(__name__)


async def is_bot_owner(interaction: discord.Interaction) -> bool:
    return await interaction.client.is_owner(interaction.user)


class Memory(commands.GroupCog, group_name="memory", group_description="管理 Echo 的記憶"):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.services = bot.services
        super().__init__()

    @app_commands.command(name="status", description="查看角色目前對你的狀態")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        state = await self.services.state.get(user_id=interaction.user.id)
        history = await self.services.memory.get(user_id=interaction.user.id)

        embed = discord.Embed(title="角色狀態", color=discord.Color.pink())
        embed.add_field(name="好感度", value=f"{state.affection} / 100")
        embed.add_field(name="信任度", value=f"{state.trust} / 100")
        embed.add_field(name="心情", value=f"{state.mood} / 100")
        embed.add_field(name="體力", value=f"{state.stamina} / 100")
        embed.set_footer(text=f"短期記憶：{len(history)} 則訊息")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="delete", description="刪除你的所有記憶與角色狀態")
    async def delete(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id

        # 等待該使用者進行中的對話結束，避免刪除後又被寫回
        async with self.services.ai.user_lock(user_id):
            await self.services.history.delete_user(user_id=user_id)
            await self.services.memory.clear(user_id=user_id)
            await self.services.state.delete_user(user_id=user_id)

        await interaction.followup.send(f"已刪除 {interaction.user.display_name} 的所有記憶。", ephemeral=True)

    @app_commands.command(name="delete_all", description="刪除所有使用者的記憶（限 Bot 擁有者）")
    @app_commands.check(is_bot_owner)
    async def delete_all(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        await self.services.history.delete_all()
        await self.services.memory.clear_all()
        await self.services.state.delete_all()

        await interaction.followup.send("已刪除所有使用者的記憶。", ephemeral=True)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = "你沒有權限執行這個指令。"
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(error.original, EchoError):
            logger.error("Memory command failed: %s", error.original)
            message = "操作失敗，請稍後再試。"
        else:
            logger.exception("Unexpected memory command error", exc_info=error)
            message = "執行指令時發生錯誤，請稍後再試。"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: Bot):
    await bot.add_cog(Memory(bot))
