import logging
from twitchio.ext import commands

LOGGER = logging.getLogger("EventSubBot")


class EventSubBot(commands.AutoBot):
    def __init__(self, *, twitch_config, db_pool, subscriptions, startup_tokens):
        self.db = db_pool
        self.twitch_config = twitch_config
        self._startup_tokens = startup_tokens

        super().__init__(
            client_id=twitch_config.client_id,
            client_secret=twitch_config.client_secret,
            bot_id=twitch_config.bot_id,
            owner_id=twitch_config.owner_id,
            prefix=twitch_config.prefix,
            subscriptions=subscriptions,
            force_subscribe=True,
        )

    async def setup_hook(self) -> None:
        LOGGER.info(f"Lade {len(self._startup_tokens)} Tokens aus der Datenbank...")
        for access, refresh in self._startup_tokens:
            try:
                await self.add_token(access, refresh)
            except Exception as exc:
                LOGGER.error(f"Konnte Token nicht laden: {exc}")

    async def event_ready(self) -> None:
        LOGGER.info(f"EventSubBot online als {self.user.name}")

    async def event_eventsub_subscription_error(self, payload):
        LOGGER.error(
            f"Subscription Fehler: {payload.reason} | Type: {payload.type}"
        )

    async def event_eventsub_subscription_allowed(self, payload):
        LOGGER.info(
            f"Subscription ERFOLGREICH: "
            f"{payload.type} für Channel {payload.broadcaster_user_id}"
        )
