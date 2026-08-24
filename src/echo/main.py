from echo.core.bot import Bot
from echo.core.config import get_settings

bot = Bot()
settings = get_settings()

if __name__ == "__main__":
    bot.run(settings.discord_token)