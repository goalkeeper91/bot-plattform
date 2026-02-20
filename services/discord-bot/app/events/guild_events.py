import logging
import redis.asyncio as redis
import json
import os

logger = logging.getLogger('guild_events')


def setup_guild_events(bot):
    """Setup guild join/leave event handlers"""

    # Redis configuration
    REDIS_HOST = os.getenv("REDIS_HOST", "redis")
    REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
    REDIS_DB = int(os.getenv("REDIS_DB", "0"))

    async def publish_to_backend(message):
        """Publish event to backend via Redis"""
        try:
            redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD if REDIS_PASSWORD else None,
                db=REDIS_DB,
                decode_responses=True
            )

            await redis_client.publish('discord:status', json.dumps(message))
            await redis_client.close()
            logger.info(f"✅ Published event to backend: {message['type']}")

        except Exception as e:
            logger.error(f"Failed to publish event: {e}")

    @bot.event
    async def on_guild_join(guild):
        """Called when bot joins a guild"""
        logger.info(f"🎉 Joined guild: {guild.name} (ID: {guild.id})")

        # Prepare guild data
        message = {
            'type': 'GUILD_JOINED',
            'guild_id': guild.id,
            'guild_name': guild.name,
            'owner_id': guild.owner_id,
            'member_count': guild.member_count,
            'icon_url': str(guild.icon.url) if guild.icon else None
        }

        # Publish to backend
        await publish_to_backend(message)

        # Send welcome message to system channel
        if guild.system_channel:
            try:
                await guild.system_channel.send(
                    "👋 Thanks for adding me! Use `/help` to get started."
                )
            except:
                pass

    @bot.event
    async def on_guild_remove(guild):
        """Called when bot leaves/is removed from a guild"""
        logger.info(f"👋 Left guild: {guild.name} (ID: {guild.id})")

        # Notify backend
        message = {
            'type': 'GUILD_LEFT',
            'guild_id': guild.id
        }

        await publish_to_backend(message)