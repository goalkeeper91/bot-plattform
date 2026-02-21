import asyncio
import logging
from typing import Any
from datetime import datetime, timezone

from twitchio.ext import commands
from twitchio import authentication, eventsub, ChatMessage

from app.commands.admin_commands import AdminCommands
from app.listeners.redis_listener import RedisHandler
from app.senders.redis_sender import RedisSender
from app.utils.bot_announcer import BotAnnouncer
from app.utils.bot_statistics import BotStatistics

LOGGER = logging.getLogger("EventSubChatDebugBot")


class EventSubChatDebugBot(commands.Bot):
    def __init__(self, *, twitch_config, db_pool, crypto, **kwargs: Any):
        self.announcer = None
        self.stats = None
        self.status_sender = None
        self.redis_handler = None
        self.db = db_pool
        self.crypto = crypto
        self.twitch_config = twitch_config
        self._subscribed_channels: set[str] = set()
        self._token_owner: dict[str, str] = {}
        self.custom_commands = None
        self.start_time = datetime.now(timezone.utc)
        self._is_running = False  # ✅ Initial False

        super().__init__(
            client_id=twitch_config.client_id,
            client_secret=twitch_config.client_secret,
            bot_id=twitch_config.bot_id,
            owner_id=twitch_config.owner_id,
            prefix=twitch_config.prefix,
            case_insensitive=True,
            **kwargs,
        )

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def subscribed_channels(self) -> list[str]:
        return list(self._subscribed_channels)

    def get_uptime(self) -> str:
        if not self._is_running:
            return "0s"
        delta = datetime.now(timezone.utc) - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes}m {seconds}s"

    def get_uptime_seconds(self) -> int:
        if not self._is_running:
            return 0
        delta = datetime.now(timezone.utc) - self.start_time
        return int(delta.total_seconds())

    async def setup_hook(self) -> None:
        LOGGER.info("🔧 setup_hook gestartet")

        from app.commands.custom_commands import CustomCommands
        from app.commands.vote_commands import VoteCommands

        self.custom_commands = CustomCommands(self)
        await self.add_component(self.custom_commands)

        await self.add_component(AdminCommands(self))

        await self.add_component(VoteCommands(self))
        LOGGER.info("✅ Vote Commands Component geladen")

        self.announcer = BotAnnouncer(self)
        LOGGER.info("✅ Bot Announcer initialisiert")

        self.redis_handler = RedisHandler(self)
        await self.redis_handler.start()

        for i in range(10):
            client = self.redis_handler.get_redis_client()
            if client is not None:
                self.status_sender = RedisSender(client)
                self.stats = BotStatistics(client, self)
                await self.stats.start()  # ✅ Starte Cleanup Task
                LOGGER.info("✅ Redis Sender & Statistics erfolgreich initialisiert")
                break
            await asyncio.sleep(0.5)
        else:
            LOGGER.warning("⚠️ Redis Client nicht rechtzeitig bereit. Status-Updates deaktiviert.")

        # Tokens und Channels aus DB laden
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    t.twitch_user_id, 
                    t.access_token, 
                    t.refresh_token, 
                    u.is_bot
                FROM twitch_auth_tokens t
                JOIN users u ON t.twitch_user_id = u.twitch_id
            """)

        for row in rows:
            user_id = str(row["twitch_user_id"])
            is_bot = row["is_bot"]

            access = self.crypto.decrypt(row["access_token"])
            refresh = self.crypto.decrypt(row["refresh_token"])

            if not access or not refresh:
                LOGGER.warning("⚠️ Ungültiges Token für user_id=%s", user_id)
                continue

            await self.add_token(access, refresh)
            self._token_owner[access] = user_id

            if is_bot:
                LOGGER.info("🤖 Bot-Token geladen: %s", user_id)
                continue

            # Chat Message Subscription
            LOGGER.info("📡 Subscribe ChatMessage | broadcaster=%s", user_id)

            if user_id in self._subscribed_channels:
                LOGGER.info("ℹ️ Channel %s bereits subscribed", user_id)
                continue

            self._subscribed_channels.add(user_id)

            chat = eventsub.ChatMessageSubscription(
                broadcaster_user_id=user_id,
                user_id=str(self.twitch_config.bot_id),
            )

            await self.subscribe_websocket(
                chat,
                token_for=str(self.twitch_config.bot_id),
            )

    async def event_ready(self) -> None:
        LOGGER.info("✅ Bot bereit: %s (%s)", self.user.name, self.user.id)

        # ✅ Bot ist jetzt running (auch beim Container-Start)
        if not self._is_running:
            self._is_running = True
            self.start_time = datetime.now(timezone.utc)
            LOGGER.info("✅ Bot Status: RUNNING")

        # ✅ Starte Heartbeat wenn noch nicht gestartet
        if self.status_sender and (not hasattr(self.status_sender, '_heartbeat_task') or
                                   not self.status_sender._heartbeat_task or
                                   self.status_sender._heartbeat_task.done()):
            await self.status_sender.start_heartbeat(self)
            LOGGER.info("✅ Heartbeat gestartet (event_ready)")

        # EventSub Subscriptions erstellen
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT t.twitch_user_id
                FROM twitch_auth_tokens t
                JOIN users u ON t.twitch_user_id = u.twitch_id
                WHERE u.is_bot = FALSE
            """)

        for row in rows:
            user_id = str(row["twitch_user_id"])

            if user_id in self._subscribed_channels:
                LOGGER.info("ℹ️ Channel %s bereits subscribed", user_id)
                continue

            self._subscribed_channels.add(user_id)

            try:
                chat = eventsub.ChatMessageSubscription(
                    broadcaster_user_id=user_id,
                    user_id=str(self.twitch_config.bot_id),
                )

                await self.subscribe_websocket(
                    chat,
                    token_for=str(self.twitch_config.bot_id),
                )
                LOGGER.info("📡 EventSub Subscription erstellt für: %s", user_id)
            except Exception as e:
                LOGGER.error("❌ Fehler bei Subscription für %s: %s", user_id, e)
                self._subscribed_channels.discard(user_id)

    async def event_message(self, message: ChatMessage) -> None:
        LOGGER.info(
            "💬 CHAT MESSAGE | channel=%s | user=%s | text=%s",
            message.broadcaster.name,
            message.chatter.name,
            message.text,
        )

        # ✅ Tracke JEDE Message!
        if self.stats:
            await self.stats.track_message(
                user_id=message.chatter.id,
                channel_id=message.broadcaster.id
            )

        if self.custom_commands:
            await self.custom_commands.handle_message(message)

    async def event_token_refreshed(self, payload):
        LOGGER.info("🔄 Token refreshed")
        user_id = self._token_owner.get(payload.token)

        if not user_id:
            LOGGER.warning("⚠️ Token refresh ohne bekannten Owner")
            return

        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE twitch_auth_tokens
                SET access_token  = $1,
                    refresh_token = $2,
                    updated_at    = NOW()
                WHERE twitch_user_id = $3
            """,
                               self.crypto.encrypt(payload.token),
                               self.crypto.encrypt(payload.refresh_token),
                               user_id)

        self._token_owner[payload.token] = user_id

    async def event_eventsub_subscription_allowed(self, payload):
        LOGGER.info(
            "✅ EventSub erlaubt | type=%s | broadcaster=%s",
            payload.type,
            payload.broadcaster_user_id,
        )

    async def event_eventsub_subscription_error(self, payload):
        LOGGER.error(
            "❌ EventSub FEHLER | type=%s | reason=%s",
            payload.type,
            payload.reason,
        )

    async def event_oauth_authorized(
            self,
            payload: authentication.UserTokenPayload,
    ) -> None:
        LOGGER.info("🔐 OAuth autorisiert für user_id=%s", payload.user_id)

        await self.add_token(payload.access_token, payload.refresh_token)

        if payload.user_id == self.twitch_config.bot_id:
            return

        self._token_owner[payload.access_token] = payload.user_id
        self._subscribed_channels.add(payload.user_id)

        chat = eventsub.ChatMessageSubscription(
            broadcaster_user_id=payload.user_id,
            user_id=self.twitch_config.bot_id,
        )

        await self.subscribe_websocket(
            chat,
            token_for=str(self.twitch_config.bot_id),
        )

    async def join_channel_by_id(self, twitch_user_id: str):
        LOGGER.info("➕ Join Channel via Redis: %s", twitch_user_id)

        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT access_token, refresh_token
                FROM twitch_auth_tokens
                WHERE twitch_user_id = $1
            """, twitch_user_id)

        if not row:
            LOGGER.warning("❌ Kein Token für %s gefunden", twitch_user_id)
            return

        access = self.crypto.decrypt(row["access_token"])
        refresh = self.crypto.decrypt(row["refresh_token"])

        await self.add_token(access, refresh)

        if twitch_user_id in self._subscribed_channels:
            LOGGER.info("ℹ️ Channel %s bereits subscribed (Redis)", twitch_user_id)
            return

        self._subscribed_channels.add(twitch_user_id)

        chat = eventsub.ChatMessageSubscription(
            broadcaster_user_id=str(twitch_user_id),
            user_id=str(self.twitch_config.bot_id),
        )

        await self.subscribe_websocket(
            chat,
            token_for=str(self.twitch_config.bot_id),
        )

        LOGGER.info("✅ Channel erfolgreich subscribed: %s", twitch_user_id)

    async def reload_commands(self, twitch_user_id: str = None):
        LOGGER.info("🔄 Reloading commands für user_id=%s", twitch_user_id or "ALL")

        try:
            if hasattr(self.custom_commands, 'reload_commands'):
                await self.custom_commands.reload_commands(twitch_user_id)
                LOGGER.info("✅ Commands erfolgreich neu geladen")
            else:
                LOGGER.error("❌ CustomCommands hat keine reload_commands Methode!")
        except Exception as e:
            LOGGER.error("❌ Fehler beim Reload: %s", e, exc_info=True)

    async def start_bot(self):
        if self._is_running:
            LOGGER.info("⚠️ Bot läuft bereits.")
            return

        try:
            self._is_running = True
            self.start_time = datetime.now(timezone.utc)  # ✅ Reset Uptime

            if self.status_sender:
                if not hasattr(self.status_sender, '_heartbeat_task') or \
                        not self.status_sender._heartbeat_task or \
                        self.status_sender._heartbeat_task.done():
                    await self.status_sender.start_heartbeat(self)
                    LOGGER.info("✅ Heartbeat gestartet (start_bot)")
                else:
                    LOGGER.info("ℹ️ Heartbeat läuft bereits")
            else:
                LOGGER.warning("⚠️ Status Sender nicht verfügbar - kein Heartbeat")

            # Twitch Connection starten
            connection_method = getattr(self, "connect")
            asyncio.create_task(connection_method())

            LOGGER.info("✅ Bot-Verbindung wird im Hintergrund aufgebaut...")
        except Exception as e:
            self._is_running = False
            LOGGER.error("❌ Fehler beim Starten der Verbindung: %s", e)

    async def stop_bot(self):
        if not self._is_running:
            LOGGER.info("ℹ️ Bot ist bereits gestoppt.")
            return

        self._is_running = False

        if self.status_sender:
            await self.status_sender.stop_heartbeat()
            LOGGER.info("✅ Heartbeat gestoppt")

        # Twitch Shards schließen
        shards = getattr(self, "shards", {})
        if shards:
            LOGGER.info(f"🔌 Schließe {len(shards)} aktive(n) Shard(s)...")
            for shard_id, shard in shards.items():
                await shard.close()
                LOGGER.info(f"✅ Shard {shard_id} geschlossen.")
        else:
            LOGGER.warning("⚠️ Keine aktiven Shards zum Schließen gefunden.")

        LOGGER.info("✅ Alle Twitch-Verbindungen getrennt. Redis-Listener bleibt aktiv.")

    async def close(self):
        LOGGER.info("🛑 Kompletter Shutdown des Containers...")
        self._is_running = False

        if self.status_sender:
            await self.status_sender.stop_heartbeat()

        if self.redis_handler:
            await self.redis_handler.stop()

        await super().close()