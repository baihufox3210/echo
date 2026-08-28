import discord
from discord import app_commands
from discord.ext import commands

from echo.core.errors import EchoError
from echo.memory.long_term import LongTermMemory
from echo.memory.short_term import ShortTermMemory
from echo.storage.character_state import CharacterStateStore

async def is_bot_owner(interaction: discord.Interaction) -> bool:
    return await interaction.client.is_owner(interaction.user)

class Memory(commands.GroupCog, group_name="memory"):
    def __init__(
        self,
        bot: commands.Bot,
        history: LongTermMemory,
        memory: ShortTermMemory,
        state: CharacterStateStore,
    ):
        self.bot = bot
        self.history = history
        self.memory = memory
        self.state = state
        
        super().__init__()
        
    @app_commands.command(name="delete", description="刪除記憶")
    async def delete(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            await self.history.delete_user(user_id=interaction.user.id)
            await self.memory.clear(user_id=interaction.user.id)
            await self.state.delete_user(user_id=interaction.user.id)
        except EchoError:
            await self._send_failure(interaction, "刪除記憶失敗，請稍後再試。")
            return
        
        await interaction.followup.send(
            f"已刪除 {interaction.user.name} 的所有記憶。",
            ephemeral=True
        )
        
    @app_commands.command(name="delete_all", description="刪除所有使用者的記憶")
    @app_commands.check(is_bot_owner)
    async def delete_all(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        try:
            await self.history.delete_all()
            await self.memory.clear_all()
            await self.state.delete_all()
        except EchoError:
            await self._send_failure(
                interaction,
                "刪除所有使用者的記憶失敗，請稍後再試。",
            )
            return

        await interaction.followup.send(
            "已刪除所有使用者的記憶。",
            ephemeral=False,
        )

    async def _send_failure(self, interaction: discord.Interaction, message: str):
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def _handle_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        if isinstance(error, app_commands.CheckFailure):
            await self._send_failure(interaction, "你沒有權限執行這個指令。")
            return

        await self._send_failure(interaction, "執行指令時發生錯誤，請稍後再試。")

    @delete.error
    async def delete_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await self._handle_command_error(interaction, error)

    @delete_all.error
    async def delete_all_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        await self._handle_command_error(interaction, error)
        
async def setup(bot: commands.Bot):
    await bot.add_cog(
        Memory(
            bot=bot,
            history=bot.services.history,
            memory=bot.services.memory,
            state=bot.services.state,
        )
    )