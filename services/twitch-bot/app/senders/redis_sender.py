import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.bots.chat_bot import EventSubChatDebugBot

LOGGER = logging.getLogger("RedisSender")


class RedisSender:
    def __init__(self, redis_client):
        self.redis = redis_client
        self._heartbeat_task = None
        self._is_running = False

    async def start_heartbeat(self, bot: "EventSubChatDebugBot"):
        """Startet den Heartbeat-Task"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            LOGGER.warning("⚠️ Heartbeat läuft bereits")
            return

        self._is_running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(bot))
        LOGGER.info("✅ Heartbeat gestartet")

    async def stop_heartbeat(self):
        """Stoppt den Heartbeat-Task"""
        self._is_running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        LOGGER.info("🛑 Heartbeat gestoppt")

    async def _heartbeat_loop(self, bot: "EventSubChatDebugBot"):
        """Sendet regelmäßig Bot-Status an Redis"""
        while self._is_running:
            try:
                await self._send_status_update(bot)
                await asyncio.sleep(30)  # Alle 30 Sekunden
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("❌ Heartbeat Fehler: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def _send_status_update(self, bot: "EventSubChatDebugBot"):
        """Sendet aktuellen Bot-Status UND Stats an Redis"""
        try:
            stats_data = {}
            if hasattr(bot, 'stats') and bot.stats:
                stats_data = await bot.stats.get_stats()

            channel_names = await self._get_channel_names(bot, bot.subscribed_channels)

            status_data = {
                "running": bot.is_running,
                "uptime": bot.get_uptime(),
                "uptime_seconds": bot.get_uptime_seconds(),
                "active_channels": channel_names,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            }

            full_stats = {
                "active_chatters": stats_data.get("active_chatters", 0),
                "commands": stats_data.get("commands", {}),
                "messages": stats_data.get("messages", {}),
                "top_commands": stats_data.get("top_commands", []),
            }

            pipe = self.redis.pipeline()

            pipe.set(
                "bot:status",
                json.dumps(status_data),
                ex=90
            )

            pipe.set(
                "bot:stats:data",
                json.dumps(full_stats),
                ex=90
            )

            await pipe.execute()

            LOGGER.info(
                "📡 Status & Stats gesendet: running=%s, chatters=%d, msgs=%d, cmds=%d",
                status_data["running"],
                full_stats["active_chatters"],
                full_stats["messages"].get("total", 0),
                full_stats["commands"].get("total", 0),
            )

        except Exception as e:
            LOGGER.error("❌ Fehler beim Senden: %s", e, exc_info=True)

    async def _get_channel_names(self, bot: "EventSubChatDebugBot", user_ids: list[str]) -> list[str]:
        """
        Wandelt Twitch User IDs in Usernames um
        """
        if not user_ids:
            return []

        channel_names = []

        try:
            async with bot.db.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT username 
                    FROM users 
                    WHERE twitch_id = ANY($1)
                    AND is_bot = FALSE
                """, user_ids)

                channel_names = [row["username"] for row in rows]

        except Exception as e:
            LOGGER.warning("⚠️ Fehler beim Laden von Usernames: %s", e)
            channel_names = user_ids

        return channel_names