from discord import Intents
from discord.ext import commands
import os

intents = Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
bot.run(TOKEN)
