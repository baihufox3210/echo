import asyncio

from echo.core.application import Application
from echo.core.config import get_settings

async def main():
    settings = get_settings()
    app = Application(settings=settings)
    
    await app.initialize()
    await app.bot.start(settings.discord_token)
    
if __name__ == "__main__":
    asyncio.run(main())