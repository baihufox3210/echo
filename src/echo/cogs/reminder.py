import discord
from discord import app_commands
from discord.ext import commands

from echo.core.bot import Bot
from echo.core.embeds import EMBED_COLOR


class Reminder(commands.GroupCog, group_name="reminder", group_description="管理提醒（也可以直接請 Echo 幫你設定）"):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.store = bot.services.reminders
        super().__init__()

    @app_commands.command(name="list", description="查看你的提醒")
    async def list_reminders(self, interaction: discord.Interaction):
        reminders = await self.store.list_user(interaction.user.id)
        embed = discord.Embed(title="◷ 我的提醒", color=EMBED_COLOR)
        if not reminders:
            embed.description = "目前沒有任何提醒。和 Echo 說「提醒我…」就能設定喔。"
        for reminder in reminders[:25]:
            embed.add_field(
                name=f"#{reminder.id} · {discord.utils.format_dt(reminder.due_at, 'f')}（{discord.utils.format_dt(reminder.due_at, 'R')}）",
                value=reminder.content[:1024],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="cancel", description="取消提醒")
    @app_commands.describe(reminder_id="提醒編號（可用 /reminder list 查詢）")
    async def cancel(self, interaction: discord.Interaction, reminder_id: int):
        if await self.store.cancel(interaction.user.id, reminder_id):
            message = f"已取消提醒 #{reminder_id}。"
        else:
            message = f"找不到提醒 #{reminder_id}。"
        await interaction.response.send_message(message, ephemeral=True)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Reminder(bot))
