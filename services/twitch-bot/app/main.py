import asyncio
import logging
import twitchio

from app.config import load_db_config, load_twitch_config
from app.utils.db import create_db_pool
from app.utils.crypto import CryptoUtils
from app.bots.chat_bot import EventSubChatDebugBot

LOGGER = logging.getLogger("Main")


def main():
    twitchio.utils.setup_logging(level=logging.DEBUG)

    async def runner():
        db_config = load_db_config()
        twitch_config = load_twitch_config()
        db_pool = await create_db_pool(db_config)
        crypto = CryptoUtils(twitch_config.secret_key)

        async with EventSubChatDebugBot(
            twitch_config=twitch_config,
            db_pool=db_pool,
            crypto=crypto,
        ) as bot:
            await bot.start()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOGGER.warning("🛑 Shutdown")


if __name__ == "__main__":
    main()
