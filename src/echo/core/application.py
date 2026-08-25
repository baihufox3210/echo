from echo.core.bot import Bot
from echo.core.config import Settings
from echo.core.services import Services

class Application:
    def __init__(self, settings: Settings):
        self.services = Services(settings=settings)
        self.bot = Bot(self.services)
        
    async def initialize(self):
        await self.services.initialize()