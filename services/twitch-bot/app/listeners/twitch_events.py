import logging
from twitchio.ext import commands

LOGGER = logging.getLogger("Events")

class TwitchEvents(commands.Component):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Ready Event
    async def ready(self):
        LOGGER.info("Bot ist online als %s", self.bot.user.name)

    # Message Event
    async def message(self, message):
        if message.echo:
            return
        await self.bot.invoke(message)
