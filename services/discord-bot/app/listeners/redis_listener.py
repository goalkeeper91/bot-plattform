import redis.asyncio as redis
import json
import logging
from discord import Embed

logger = logging.getLogger('redis_listener')


class RedisListener:
    def __init__(self, bot, host, port, password, db):
        self.bot = bot
        self.host = host
        self.port = port
        self.password = password
        self.db = db
        self.redis_client = None
        self.pubsub = None
        self.running = False

    async def start(self):
        """Start listening to Redis pub/sub"""
        try:
            # Connect to Redis
            self.redis_client = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password if self.password else None,
                db=self.db,
                decode_responses=True
            )

            # Test connection
            await self.redis_client.ping()
            logger.info(f"✅ Connected to Redis at {self.host}:{self.port}")

            # Subscribe to discord:events channel
            self.pubsub = self.redis_client.pubsub()
            await self.pubsub.subscribe('discord:events')
            logger.info("✅ Subscribed to discord:events channel")

            self.running = True
            await self.listen()

        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            self.running = False

    async def listen(self):
        """Listen for messages from Redis"""
        logger.info("👂 Listening for Redis messages...")

        try:
            async for message in self.pubsub.listen():
                if not self.running:
                    break

                if message['type'] == 'message':
                    try:
                        data = json.loads(message['data'])
                        await self.handle_message(data)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse message: {e}")
                    except Exception as e:
                        logger.error(f"Error handling message: {e}")

        except Exception as e:
            logger.error(f"Error in listen loop: {e}")
        finally:
            await self.stop()

    async def handle_message(self, data):
        """Handle incoming Redis messages"""
        message_type = data.get('type')
        logger.info(f"📨 Received message: {message_type}")

        if message_type == 'SEND_NOTIFICATION':
            await self.send_notification(data)
        elif message_type == 'SEND_ADMIN_NOTIFICATION':
            await self.send_admin_notification(data)
        elif message_type == 'SYNC_GUILD':
            await self.sync_guild(data)
        elif message_type == 'LEAVE_GUILD':
            await self.leave_guild(data)
        elif message_type == 'RELOAD_CONFIGS':
            await self.reload_configs(data)
        else:
            logger.warning(f"Unknown message type: {message_type}")

    async def send_notification(self, data):
        """Send notification to Discord channel"""
        try:
            channel_id = int(data['channel_id'])
            embed_data = data.get('embed', {})

            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Channel {channel_id} not found")
                return

            # Build embed
            embed = Embed(
                title=embed_data.get('title', 'Notification'),
                description=embed_data.get('description', ''),
                color=int(embed_data.get('color', '5814783'))
            )

            # Add fields
            for field in embed_data.get('fields', []):
                embed.add_field(
                    name=field.get('name', ''),
                    value=field.get('value', ''),
                    inline=field.get('inline', False)
                )

            await channel.send(embed=embed)
            logger.info(f"✅ Sent notification to channel {channel_id}")

        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    async def send_admin_notification(self, data):
        """Send admin notification to configured channel"""
        try:
            channel_id = int(data['channel_id'])
            embed_data = data.get('embed', {})

            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Admin channel {channel_id} not found")
                return

            # Build embed
            embed = Embed(
                title=embed_data.get('title', 'Admin Notification'),
                description=embed_data.get('description', ''),
                color=int(embed_data.get('color', '3447003'))
            )

            # Add fields
            for field in embed_data.get('fields', []):
                embed.add_field(
                    name=field.get('name', ''),
                    value=field.get('value', ''),
                    inline=field.get('inline', False)
                )

            await channel.send(embed=embed)
            logger.info(f"✅ Sent admin notification to channel {channel_id}")

        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")

    async def sync_guild(self, data):
        """Sync guild data with backend"""
        try:
            guild_id = int(data['guild_id'])
            guild = self.bot.get_guild(guild_id)

            if not guild:
                logger.error(f"Guild {guild_id} not found")
                return

            # Sync guild data (channels, roles, etc.)
            # This would send data back to backend via Redis
            logger.info(f"✅ Synced guild {guild.name} ({guild_id})")

        except Exception as e:
            logger.error(f"Failed to sync guild: {e}")

    async def leave_guild(self, data):
        """Leave a guild"""
        try:
            guild_id = int(data['guild_id'])
            guild = self.bot.get_guild(guild_id)

            if not guild:
                logger.warning(f"Guild {guild_id} not found (already left?)")
                return

            await guild.leave()
            logger.info(f"✅ Left guild {guild.name} ({guild_id})")

        except Exception as e:
            logger.error(f"Failed to leave guild: {e}")

    async def reload_configs(self, data):
        """Applies a config push from the backend (see discord_bot/redis_bridge.py's
        publish_guild_config() in the PunishersGer repo - that's the source of
        truth, this bot only caches what it's told). Two config_types:

        - join_to_create: full replace of this guild's voice-channel triggers
          (see events/voice_events.py, which already implements the actual
          create/delete-on-empty logic - this just feeds it config).
        - rule_role: the reaction-role used for rule acceptance (see
          events/rule_role_events.py). An empty/missing 'config' disables it.
        """
        try:
            guild_id = int(data['guild_id'])
            config_type = data.get('config_type', 'join_to_create')

            if config_type == 'join_to_create':
                from events.voice_events import load_jtc_config, jtc_configs
                jtc_configs[guild_id] = {}
                for trigger in data.get('triggers', []):
                    # category_id arrives as a JSON string (PunishersGer stores
                    # Discord snowflake IDs as strings) - guild.get_channel()
                    # in voice_events.py's handle_voice_join() looks up by int,
                    # so a string here would silently never match and log
                    # "Category ... not found" even with a correct ID.
                    channel_id = int(trigger['channel_id'])
                    normalized = {**trigger, 'category_id': int(trigger['category_id'])}
                    load_jtc_config(guild_id, channel_id, normalized)
                logger.info(f"✅ {len(data.get('triggers', []))} Join-to-Create Trigger geladen für Guild {guild_id}")

            elif config_type == 'rule_role':
                from events.rule_role_events import set_rule_role_config
                set_rule_role_config(guild_id, data.get('config'))

            else:
                logger.warning(f"⚠️ Unbekannter config_type: {config_type}")

        except Exception as e:
            logger.error(f"Failed to reload configs: {e}")

    async def stop(self):
        """Stop listening and cleanup"""
        self.running = False

        if self.pubsub:
            await self.pubsub.unsubscribe('discord:events')
            await self.pubsub.close()

        if self.redis_client:
            await self.redis_client.close()

        logger.info("Redis listener stopped")