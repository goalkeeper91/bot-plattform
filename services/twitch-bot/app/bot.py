from twitchio.ext import commands

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(token="YOUR_TWITCH_TOKEN", prefix="!", initial_channels=["#channel"])

    async def event_ready(self):
        print(f"Twitch Bot ready!")

bot = Bot()
bot.run()
