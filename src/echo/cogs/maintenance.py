import logging
import time
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import commands, tasks

from echo.core.bot import Bot
from echo.core.errors import EchoError

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30
REMINDER_INTERVAL = 20
LATE_THRESHOLD = timedelta(minutes=5)


class Maintenance(commands.Cog):
    """背景維護工作：提醒派送、角色狀態自然衰減與容器健康檢查心跳。"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = bot.services.settings
        self.heartbeat.start()
        self.deliver_reminders.start()
        if self.settings.state_decay_enabled:
            self.decay_states.start()

    async def cog_unload(self) -> None:
        self.heartbeat.cancel()
        self.deliver_reminders.cancel()
        self.decay_states.cancel()

    @tasks.loop(seconds=HEARTBEAT_INTERVAL)
    async def heartbeat(self) -> None:
        if not self.bot.is_ready() or self.bot.is_closed():
            return
        try:
            self.settings.healthcheck_file.write_text(str(time.time()))
        except OSError:
            logger.warning("Failed to write heartbeat file", exc_info=True)

    @tasks.loop(seconds=REMINDER_INTERVAL)
    async def deliver_reminders(self) -> None:
        try:
            reminders = await self.bot.services.reminders.pop_due()
        except EchoError:
            logger.exception("Failed to load due reminders")
            return

        for reminder in reminders:
            content = f"◷ {reminder.content}"
            if datetime.now(UTC) - reminder.due_at > LATE_THRESHOLD:
                content += f"\n-# 原定 {discord.utils.format_dt(reminder.due_at, 'f')}，因 Bot 離線延遲送達"
            await self._send_reminder(reminder.user_id, reminder.channel_id, content)

    async def _send_reminder(self, user_id: int, channel_id: int, content: str) -> None:
        ping = self.settings.reminder_ping
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            await channel.send(
                f"<@{user_id}> {content}" if ping else content,
                # 只 ping 設定提醒的本人；提醒內容即使含 @everyone 也不會生效
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, replied_user=False, users=[discord.Object(user_id)]
                ) if ping else discord.AllowedMentions.none(),
            )
            return
        except discord.HTTPException:
            logger.warning("Reminder channel %s unavailable; falling back to DM", channel_id)

        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(content, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Failed to deliver reminder to user %s", user_id)

    @deliver_reminders.before_loop
    async def before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def decay_states(self) -> None:
        try:
            affected = await self.bot.services.state.decay(
                after_days=self.settings.state_decay_after_days,
                amount=self.settings.state_decay_amount,
            )
        except EchoError:
            logger.exception("Character state decay failed")
            return
        if affected:
            logger.info("Decayed affection for %d inactive users", affected)

    @decay_states.before_loop
    async def before_decay(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: Bot) -> None:
    await bot.add_cog(Maintenance(bot))
