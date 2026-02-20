import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, Set

if TYPE_CHECKING:
    from app.bots.chat_bot import EventSubChatDebugBot

LOGGER = logging.getLogger("BotStatistics")


class BotStatistics:
    """
    Tracking System für Bot Statistiken:
    - Commands ausgeführt (gesamt + heute)
    - Nachrichten verarbeitet
    - Active Chatters (unique users in letzten 10 Minuten)
    - Per-Channel Stats optional
    """

    def __init__(self, redis_client, bot: "EventSubChatDebugBot"):
        self.redis = redis_client
        self.bot = bot

        self._active_chatters: Set[str] = set()
        self._chatter_timestamps: Dict[str, datetime] = {}

        self._cleanup_task = None

        LOGGER.info("📊 BotStatistics initialisiert")

    async def start(self):
        """Startet Cleanup Task für active chatters"""
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            LOGGER.info("✅ Statistics Cleanup Task gestartet")

    async def stop(self):
        """Stoppt Cleanup Task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            LOGGER.info("🛑 Statistics Cleanup Task gestoppt")

    async def _cleanup_loop(self):
        """Entfernt inactive chatters alle 60 Sekunden"""
        while True:
            try:
                await asyncio.sleep(60)
                await self._cleanup_inactive_chatters()
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("❌ Cleanup Fehler: %s", e, exc_info=True)

    async def _cleanup_inactive_chatters(self):
        """Entfernt Chatters die >10 Minuten inaktiv sind"""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        inactive = [
            user for user, ts in self._chatter_timestamps.items()
            if ts < cutoff
        ]

        for user in inactive:
            self._active_chatters.discard(user)
            del self._chatter_timestamps[user]

        if inactive:
            LOGGER.debug("🧹 %d inactive chatters entfernt", len(inactive))

    # === Message Tracking ===

    async def track_message(self, user_id: str, channel_id: str = None):
        """
        Trackt eine verarbeitete Nachricht

        Args:
            user_id: Twitch User ID des Chatters
            channel_id: Optional - Channel ID für per-channel stats
        """
        try:
            # Active Chatter tracken
            self._active_chatters.add(user_id)
            self._chatter_timestamps[user_id] = datetime.now(timezone.utc)

            # Nachrichten Counter in Redis
            pipe = self.redis.pipeline()

            # Gesamt Messages
            pipe.incr("bot:stats:messages:total")

            # Messages heute (Key mit Datum)
            today_key = f"bot:stats:messages:today:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            pipe.incr(today_key)
            pipe.expire(today_key, 86400 * 2)  # 2 Tage TTL

            # Optional: Per-Channel
            if channel_id:
                pipe.incr(f"bot:stats:messages:channel:{channel_id}")

            await pipe.execute()

        except Exception as e:
            LOGGER.error("❌ Fehler beim Message Tracking: %s", e)

    # === Command Tracking ===

    async def track_command(self, command_name: str, channel_id: str = None, success: bool = True):
        """
        Trackt einen ausgeführten Command

        Args:
            command_name: Name des Commands (z.B. "!test")
            channel_id: Optional - Channel ID
            success: Ob Command erfolgreich war
        """
        try:
            pipe = self.redis.pipeline()

            # Gesamt Commands
            pipe.incr("bot:stats:commands:total")

            # Commands heute
            today_key = f"bot:stats:commands:today:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            pipe.incr(today_key)
            pipe.expire(today_key, 86400 * 2)

            # Per Command Stats
            pipe.hincrby("bot:stats:commands:by_name", command_name, 1)

            # Success/Failure
            if success:
                pipe.incr("bot:stats:commands:success")
            else:
                pipe.incr("bot:stats:commands:failed")

            # Optional: Per-Channel
            if channel_id:
                pipe.incr(f"bot:stats:commands:channel:{channel_id}")

            await pipe.execute()

            LOGGER.debug("📈 Command tracked: %s (success=%s)", command_name, success)

        except Exception as e:
            LOGGER.error("❌ Fehler beim Command Tracking: %s", e)

    # === Stats Abrufen ===

    async def get_stats(self) -> dict:
        """
        Holt aktuelle Statistiken

        Returns:
            Dict mit allen Stats
        """
        try:
            pipe = self.redis.pipeline()

            # Commands
            pipe.get("bot:stats:commands:total")
            today_key = f"bot:stats:commands:today:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            pipe.get(today_key)
            pipe.get("bot:stats:commands:success")
            pipe.get("bot:stats:commands:failed")

            # Messages
            pipe.get("bot:stats:messages:total")
            today_msg_key = f"bot:stats:messages:today:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            pipe.get(today_msg_key)

            # Top Commands
            pipe.hgetall("bot:stats:commands:by_name")

            results = await pipe.execute()

            # Active Chatters (aus Memory)
            await self._cleanup_inactive_chatters()

            stats = {
                "commands": {
                    "total": int(results[0] or 0),
                    "today": int(results[1] or 0),
                    "success": int(results[2] or 0),
                    "failed": int(results[3] or 0),
                },
                "messages": {
                    "total": int(results[4] or 0),
                    "today": int(results[5] or 0),
                },
                "active_chatters": len(self._active_chatters),
                "top_commands": self._parse_top_commands(results[6]),
            }

            LOGGER.debug("📊 Stats abgerufen: %s", stats)
            return stats

        except Exception as e:
            LOGGER.error("❌ Fehler beim Stats Abrufen: %s", e, exc_info=True)
            return {
                "commands": {"total": 0, "today": 0, "success": 0, "failed": 0},
                "messages": {"total": 0, "today": 0},
                "active_chatters": 0,
                "top_commands": [],
            }

    def _parse_top_commands(self, cmd_data: dict) -> list:
        """Parst Top Commands aus Redis Hash"""
        if not cmd_data:
            return []

        # Konvertiere zu Liste und sortiere
        commands = [
            {"name": k.decode() if isinstance(k, bytes) else k,
             "count": int(v)}
            for k, v in cmd_data.items()
        ]

        # Top 10 nach Count
        commands.sort(key=lambda x: x["count"], reverse=True)
        return commands[:10]

    # === Per-Channel Stats (optional) ===

    async def get_channel_stats(self, channel_id: str) -> dict:
        """
        Holt Stats für einen spezifischen Channel

        Args:
            channel_id: Twitch Channel ID

        Returns:
            Dict mit Channel-spezifischen Stats
        """
        try:
            pipe = self.redis.pipeline()

            pipe.get(f"bot:stats:messages:channel:{channel_id}")
            pipe.get(f"bot:stats:commands:channel:{channel_id}")

            results = await pipe.execute()

            return {
                "channel_id": channel_id,
                "messages": int(results[0] or 0),
                "commands": int(results[1] or 0),
            }

        except Exception as e:
            LOGGER.error("❌ Fehler beim Channel Stats Abrufen: %s", e)
            return {
                "channel_id": channel_id,
                "messages": 0,
                "commands": 0,
            }

    # === Reset Functions ===

    async def reset_daily_stats(self):
        """Löscht die heutigen Stats (z.B. um Mitternacht)"""
        try:
            today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            await self.redis.delete(
                f"bot:stats:commands:today:{today}",
                f"bot:stats:messages:today:{today}"
            )
            LOGGER.info("🔄 Daily Stats zurückgesetzt")
        except Exception as e:
            LOGGER.error("❌ Fehler beim Reset: %s", e)

    async def reset_all_stats(self):
        """Löscht ALLE Stats (VORSICHT!)"""
        try:
            keys = await self.redis.keys("bot:stats:*")
            if keys:
                await self.redis.delete(*keys)
            self._active_chatters.clear()
            self._chatter_timestamps.clear()
            LOGGER.warning("⚠️ ALLE Stats wurden gelöscht!")
        except Exception as e:
            LOGGER.error("❌ Fehler beim kompletten Reset: %s", e)