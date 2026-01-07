import asyncio
from app.config import load_db_config, load_twitch_config
from app.utils.db import create_db_pool
from app.utils.logging import setup_logging
from app.twitch_bot import Bot


async def main():
    setup_logging()

    db_config = load_db_config()
    twitch_config = load_twitch_config()

    db_pool = await create_db_pool(db_config)

    async with Bot(db=db_pool, twitch_config=twitch_config) as bot:
        await bot.load_tokens_from_db()
        await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
