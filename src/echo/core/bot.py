import os
import logging

import discord
from discord.ext import commands

from echo.core.services import Services

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"chat", "memory"}


class Bot(commands.Bot):
    def __init__(self, services: Services):
        super().__init__(
            command_prefix="♡",
            intents=discord.Intents.all(),
        )

        self.services = services

    async def setup_hook(self):
        await self.load_extensions()
        await self.tree.sync()

    async def load_extensions(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        cogs_dir = os.path.join(base_dir, "cogs")

        if not os.path.exists(cogs_dir):
            logger.info("Cogs directory not found: %s", cogs_dir)
            return

        for file in os.listdir(cogs_dir):
            if not file.endswith(".py") or file.startswith("_"):
                continue

            extension_name = file[:-3]

            if extension_name not in ALLOWED_EXTENSIONS:
                logger.warning("Extension blocked (not in whitelist): %s", extension_name)
                continue

            extension = f"echo.cogs.{extension_name}"

            try:
                await super().load_extension(extension)
                logger.info("Extension loaded: %s", extension)
            except Exception as e:
                logger.error("Extension load failed: %s - %s", extension, e.__class__.__name__)

    async def on_ready(self):
        print("=" * 40)
        print("Discord Bot 啟動成功")
        print(f"User : {self.user}")
        print(f"ID   : {self.user.id}")
        print("=" * 40)