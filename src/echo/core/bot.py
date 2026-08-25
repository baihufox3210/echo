import os
import traceback

import discord
from discord.ext import commands

from echo.core.services import Services


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
            print(f"[Bot] 找不到 cogs 目錄: {cogs_dir}")
            return

        for root, _, files in os.walk(cogs_dir):
            for file in files:
                if not file.endswith(".py"):
                    continue

                rel_path = os.path.relpath(os.path.join(root, file), ".")
                extension = rel_path.replace(os.sep, ".")[:-3]

                try:
                    await super().load_extension(extension)
                    print(f"[Bot] Loaded: {extension}")
                    
                except Exception:
                    print(f"[Bot] Extension Load Failed: {extension}")
                    print(traceback.format_exc())

    async def on_ready(self):
        print("=" * 40)
        print("Discord Bot 啟動成功")
        print(f"User : {self.user}")
        print(f"ID   : {self.user.id}")
        print("=" * 40)