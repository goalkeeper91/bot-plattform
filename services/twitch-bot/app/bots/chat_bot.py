import logging
from typing import Any

from twitchio.ext import commands
from twitchio import authentication, eventsub, ChatMessage

from app.commands.admin_commands import AdminCommands
from app.listeners.redis_listener import RedisHandler

LOGGER = logging.getLogger("EventSubChatDebugBot")


class EventSubChatDebugBot(commands.Bot):
    def __init__(self, *, twitch_config, db_pool, crypto, **kwargs: Any):
        self.db = db_pool
        self.crypto = crypto
        self.twitch_config = twitch_config
        self._subscribed_channels: set[str] = set()
        self._token_owner: dict[str, str] = {}
        self.custom_commands = None
        self.redis_handler = None

        super().__init__(
            client_id=twitch_config.client_id,
            client_secret=twitch_config.client_secret,
            bot_id=twitch_config.bot_id,
            owner_id=twitch_config.owner_id,
            prefix=twitch_config.prefix,
            case_insensitive=True,
            **kwargs,
        )

    async def setup_hook(self) -> None:
        LOGGER.info("🔧 setup_hook gestartet")

        from app.commands.custom_commands import CustomCommands

        # ✅ Speichere Referenz direkt für späteren Zugriff
        self.custom_commands = CustomCommands(self)
        await self.add_component(self.custom_commands)

        await self.add_component(AdminCommands(self))

        self.redis_handler = RedisHandler(self)
        await self.redis_handler.start()

        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                                    SELECT twitch_user_id, access_token, refresh_token
                                    FROM twitch_auth_tokens
                                    WHERE token_owner IN ('BOT', 'USER')
                                    """)

        for row in rows:
            user_id = str(row["twitch_user_id"])
            access = self.crypto.decrypt(row["access_token"])
            refresh = self.crypto.decrypt(row["refresh_token"])

            if not access or not refresh:
                LOGGER.warning("⚠️ Ungültiges Token für user_id=%s", user_id)
                continue

            await self.add_token(access, refresh)
            self._token_owner[access] = user_id

            if user_id == str(self.twitch_config.bot_id):
                continue
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

    async def event_message(self, message: ChatMessage) -> None:
        LOGGER.info(
            "💬 CHAT MESSAGE | channel=%s | user=%s | text=%s",
            message.broadcaster.name,
            message.chatter.name,
            message.text,
        )

        if self.custom_commands:
            await self.custom_commands.handle_message(message)
        #await self.process_commands(message)

    async def event_token_refreshed(self, token, refresh):
        LOGGER.info("🔄 Token refreshed")
        user_id = self._token_owner.get(token)

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
                               self.crypto.encrypt(token),
                               self.crypto.encrypt(refresh),
                               user_id)

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
        LOGGER.info(
            "🔄 Reload Commands aufgerufen | user_id=%s | subscribed=%s",
            twitch_user_id,
            twitch_user_id in self._subscribed_channels if twitch_user_id else "ALL"
        )

        try:
            if hasattr(self.custom_commands, 'reload_commands'):
                await self.custom_commands.reload_commands(twitch_user_id)
                LOGGER.info("✅ Commands erfolgreich neu geladen")
            else:
                LOGGER.error("❌ CustomCommands hat keine reload_commands Methode!")
        except Exception as e:
            LOGGER.error("❌ Fehler beim Reload: %s", e, exc_info=True)

    async def close(self):
        LOGGER.info("🛑 Bot shutdown")
        await self.redis_handler.stop()
        await super().close()