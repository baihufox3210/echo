import asyncio
import logging

from echo.core.application import Application
from echo.core.config import get_settings


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "openai", "discord.gateway", "discord.http"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    await Application(settings=settings).run()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
