import logging
import time

from discord.ext import commands, tasks

from echo.core.bot import Bot
from echo.core.errors import EchoError

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30


class Maintenance(commands.Cog):
    """背景維護工作：角色狀態自然衰減與容器健康檢查心跳。"""

    def __init__(self, bot: Bot):
        self.bot = bot
        self.settings = bot.services.settings
        self.heartbeat.start()
        if self.settings.state_decay_enabled:
            self.decay_states.start()

    async def cog_unload(self) -> None:
        self.heartbeat.cancel()
        self.decay_states.cancel()

    @tasks.loop(seconds=HEARTBEAT_INTERVAL)
    async def heartbeat(self) -> None:
        if not self.bot.is_ready() or self.bot.is_closed():
            return
        try:
            self.settings.healthcheck_file.write_text(str(time.time()))
        except OSError:
            logger.warning("Failed to write heartbeat file", exc_info=True)

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
