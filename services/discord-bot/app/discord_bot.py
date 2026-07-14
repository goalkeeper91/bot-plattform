import discord
from discord import Intents
from discord.ext import commands
import os
import asyncio
import logging

from listeners.redis_listener import RedisListener
from senders.redis_sender import RedisSender
from events.guild_events import setup_guild_events
from events.voice_events import setup_voice_events
from events.rule_role_events import setup_rule_role_events

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/logs/discord_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('discord_bot')

intents = Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

redis_listener = None
redis_sender = None


@bot.event
async def on_ready():
    logger.info(f"✅ Bot logged in as {bot.user}")
    logger.info(f"✅ Connected to {len(bot.guilds)} guilds")

    # Start Redis listener
    global redis_listener, redis_sender
    redis_listener = RedisListener(
        bot=bot,
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB
    )

    asyncio.create_task(redis_listener.start())
    logger.info("✅ Redis listener started")

    # Start heartbeat sender (lets other services know the bot is online)
    redis_sender = RedisSender(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB
    )
    await redis_sender.start(bot)


@bot.event
async def on_close():
    logger.info("Bot shutting down...")
    if redis_listener:
        await redis_listener.stop()
    if redis_sender:
        await redis_sender.stop()


setup_guild_events(bot)
setup_voice_events(bot)
setup_rule_role_events(bot)


def main():
    """Main entry point"""
    TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not TOKEN:
        logger.error("❌ DISCORD_BOT_TOKEN not set!")
        exit(1)

    logger.info("🚀 Starting Discord bot...")
    bot.run(TOKEN)


if __name__ == "__main__":
    main()