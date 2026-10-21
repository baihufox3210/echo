import logging

import discord
from discord.ext import commands

from echo.core.services import Services

logger = logging.getLogger(__name__)

EXTENSIONS = ("echo.cogs.chat", "echo.cogs.memory")


class Bot(commands.Bot):
    def __init__(self, services: Services):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            # 預設不 ping 任何人，包括 @everyone、身分組與被回覆的使用者
            allowed_mentions=discord.AllowedMentions.none(),
        )

        self.services = services

    async def setup_hook(self) -> None:
        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Extension loaded: %s", extension)
            except commands.ExtensionError:
                logger.exception("Extension load failed: %s", extension)

        if self.services.settings.sync_commands:
            synced = await self.tree.sync()
            logger.info("Synced %d application commands", len(synced))

    async def on_ready(self) -> None:
        logger.info("Discord bot ready: %s (ID: %s), guilds=%d", self.user, self.user.id, len(self.guilds))
