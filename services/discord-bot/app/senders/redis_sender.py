import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger('redis_sender')

HEARTBEAT_INTERVAL_SECONDS = 30
STATUS_KEY = "discord_bot:status"
STATUS_TTL_SECONDS = 90  # >2x the heartbeat interval, so a crashed bot reads as offline soon.


class RedisSender:
    """Publishes a periodic online/uptime heartbeat so other services (e.g.
    the PunishersGer admin dashboard) can tell whether this bot is running,
    without needing a direct connection to it."""

    def __init__(self, host, port, password, db):
        self.host = host
        self.port = port
        self.password = password
        self.db = db
        self.redis_client = None
        self._task = None
        self._running = False
        self._start_time = None

    async def start(self, bot):
        if self._task:
            return

        self.redis_client = redis.Redis(
            host=self.host,
            port=self.port,
            password=self.password if self.password else None,
            db=self.db,
            decode_responses=True,
        )
        self._start_time = datetime.now(timezone.utc)
        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop(bot))
        logger.info("✅ Heartbeat gestartet")

    async def stop(self):
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self.redis_client:
            await self.redis_client.close()

        logger.info("🛑 Heartbeat gestoppt")

    async def _heartbeat_loop(self, bot):
        while self._running:
            try:
                await self._send_status(bot)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Heartbeat Fehler: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

    async def _send_status(self, bot):
        uptime_seconds = int((datetime.now(timezone.utc) - self._start_time).total_seconds())
        status = {
            "online": True,
            "guild_count": len(bot.guilds),
            "uptime_seconds": uptime_seconds,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis_client.set(STATUS_KEY, json.dumps(status), ex=STATUS_TTL_SECONDS)
