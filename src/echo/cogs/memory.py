import discord
from discord import app_commands
from discord.ext import commands

from echo.memory.long_term import LongTermMemory

async def is_bot_owner(interaction: discord.Interaction) -> bool:
    return await interaction.client.is_owner(interaction.user)


class Memory(commands.GroupCog, group_name="memory"):
    def __init__(self, bot: commands.Bot, history: LongTermMemory):
        self.bot = bot
        self.history = history
        
        super().__init__()
        
    @app_commands.command(name="delete", description="刪除記憶")
    async def delete(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        result = await self.history.delete_user(user_id=interaction.user.id)
        
        await interaction.followup.send(
            f"已刪除 {interaction.user.name} 所有的記憶。\n`{result.get('message', '完成')}`",
            ephemeral=True
        )
        
    @app_commands.command(name="delete_all", description="刪除所有使用者的記憶")
    @app_commands.check(is_bot_owner)
    async def delete_all(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        result = await self.history.delete_all()
        if result.get("error"):
            await interaction.followup.send(
                "刪除所有使用者的記憶失敗，請稍後再試。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"已刪除所有使用者的記憶。\n"
            f"`{result.get('message', '完成')}`",
            ephemeral=False,
        )

    @delete_all.error
    async def delete_all_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "你沒有權限執行這個指令。",
                ephemeral=True,
            )
            return

        if not interaction.response.is_done():
            await interaction.response.send_message(
                "執行指令時發生錯誤，請稍後再試。",
                ephemeral=True,
            )
        
async def setup(bot: commands.Bot):
    await bot.add_cog(
        Memory(
            bot=bot,
            history=bot.services.history,
        )
    )