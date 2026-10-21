import asyncio
import logging
import signal

from echo.core.bot import Bot
from echo.core.config import Settings
from echo.core.services import Services

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.services = Services(settings=settings)
        self.bot = Bot(self.services)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_shutdown, sig)
            except NotImplementedError:
                pass

        await self.services.initialize()
        try:
            async with self.bot:
                await self.bot.start(self.settings.discord_token)
        finally:
            await self.services.close()
            logger.info("Shutdown complete")

    def _request_shutdown(self, sig: signal.Signals) -> None:
        logger.info("Received %s, shutting down", sig.name)
        asyncio.create_task(self.bot.close())
