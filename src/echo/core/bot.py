import os, discord, traceback
from discord.ext import commands

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()

        super().__init__(
            command_prefix = "♡",
            intents = intents
        )
        
    async def setup_hook(self):
        await self.load_extensions()
        await self.tree.sync()
    
    async def load_extensions(self):
        base_dir = os.path.dirname(os.path.dirname(__file__))
        cogs_dir = os.path.join(base_dir, "cogs")
        
        target_dir = cogs_dir if os.path.exists(cogs_dir) else "./cogs"
        
        if not os.path.exists(target_dir):
            print(f"找不到 cogs 目錄: {target_dir}")
            return
        
        for root, _, files in os.walk(target_dir):
            for file in files:
                if file.endswith(".py"):
                    rel_path = os.path.relpath(os.path.join(root, file), ".")
                    extension = rel_path.replace(os.sep, ".")[:-3]
                    
                    try: await super().load_extension(extension)
                    except Exception as e: print("Extension Load Failed", traceback.format_exc())
    
    async def on_ready(self): pass
    async def close(self): await super().close()