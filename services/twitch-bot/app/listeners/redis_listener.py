import asyncio
import json
import logging
import redis.asyncio as redis
from app.config import load_redis_config

LOGGER = logging.getLogger("RedisHandler")


class RedisHandler:
    def __init__(self, bot):
        self.bot = bot
        self.redis_config = load_redis_config()
        self.redis_host = self.redis_config.host
        self.port = self.redis_config.port
        self.channel = self.redis_config.channel
        self._task = None
        self._running = False
        self._redis = None  # ✅ HINZUGEFÜGT
        self._pubsub = None  # ✅ HINZUGEFÜGT

    def get_redis_client(self):
        return self._redis

    async def start(self):
        if self._task:
            return

        self._running = True
        self._task = asyncio.create_task(self._listen())
        LOGGER.info("📡 Redis Listener gestartet")

    async def stop(self):
        self._running = False

        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()

        if self._redis:
            await self._redis.close()

        if self._task:
            self._task.cancel()

        LOGGER.info("🛑 Redis Listener gestoppt")

    async def _listen(self):
        while self._running:
            try:
                self._redis = redis.Redis(
                    host=self.redis_config.host,
                    port=self.redis_config.port,
                    decode_responses=True,
                )

                self._pubsub = self._redis.pubsub()
                await self._pubsub.subscribe(self.redis_config.channel)

                LOGGER.info("✅ Redis subscribed: %s", self.redis_config.channel)

                async for message in self._pubsub.listen():
                    if not self._running:
                        break

                    if message["type"] != "message":
                        continue

                    await self._handle_message(message["data"])

            except asyncio.CancelledError:
                break

            except Exception as e:
                LOGGER.error("❌ Redis Fehler: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def _handle_message(self, raw: str):
        LOGGER.info("📨 Redis Nachricht: %s", raw)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            LOGGER.warning("⚠️ Ungültiges Redis-Payload: %s", raw)
            return

        event = payload.get("type")
        twitch_id = payload.get("twitch_user_id")

        if not event:
            LOGGER.warning("⚠️ Unvollständiges Payload (kein type): %s", payload)
            return

        if event == "BOT_ANNOUNCE":
            message = payload.get("message")

            if not message:
                LOGGER.warning("⚠️ BOT_ANNOUNCE ohne message")
                return

            if hasattr(self.bot, 'announcer'):
                await self.bot.announcer.announce(message, twitch_id)
            else:
                LOGGER.error("❌ Bot Announcer nicht initialisiert!")
            return

        if event == "JOIN_CHANNEL":
            if not twitch_id:
                LOGGER.warning("⚠️ JOIN_CHANNEL ohne twitch_user_id")
                return
            await self.bot.join_channel_by_id(twitch_id)

        elif event == "REFRESH_COMMANDS":
            # ✅ twitch_id ist optional für global reload
            await self.bot.reload_commands(twitch_id)

        elif event == "REFRESH_AUTOMOD":
            await self.bot.reload_automod(twitch_id)

        elif event == "STOP_BOT":
            LOGGER.info("🛑 Stop-Befehl erhalten. Trenne von Twitch...")
            await self.bot.stop_bot()

        elif event == "START_BOT":
            LOGGER.info("🚀 Start-Befehl erhalten. Verbinde mit Twitch...")
            await self.bot.start_bot()

        elif event == "RESTART_BOT":
            LOGGER.info("🔄 Restart-Befehl erhalten...")
            await self.bot.stop_bot()
            await asyncio.sleep(2)
            await self.bot.start_bot()

        else:
            LOGGER.warning("⚠️ Unbekanntes Redis-Event: %s", event)