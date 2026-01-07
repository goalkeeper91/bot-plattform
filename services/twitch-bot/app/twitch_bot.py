import logging
import asyncpg
from twitchio.ext import commands

LOGGER = logging.getLogger("Bot")


class Bot(commands.AutoBot):
    def __init__(self, *, db: asyncpg.Pool, twitch_config):
        self.db = db

        super().__init__(
            client_id=twitch_config.client_id,
            client_secret=twitch_config.client_secret,
            bot_id=twitch_config.bot_id,
            owner_id=twitch_config.owner_id,
            prefix=twitch_config.prefix,
            conduit_id="5122675d-de27-41f0-9d17-c1b2029c38bd"
        )

    async def setup_hook(self) -> None:
        # Commands
        from app.commands.admin_commands import AdminCommands
        from app.commands.custom_commands import CustomCommands

        # Listener
        from app.listeners.twitch_events import TwitchEvents

        await self.add_component(AdminCommands(self))
        await self.add_component(CustomCommands(self))
        await self.add_component(TwitchEvents(self))

        LOGGER.info("Components geladen")

    async def load_tokens_from_db(self):
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT access_token, refresh_token FROM twitch_auth_tokens"
            )

        LOGGER.info("Lade %s Tokens", len(rows))

        for row in rows:
            try:
                await self.add_token(row["access_token"], row["refresh_token"])
            except Exception:
                LOGGER.exception("Token konnte nicht geladen werden")
