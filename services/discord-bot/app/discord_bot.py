from discord.ext import commands

bot = commands.Bot(command_prefix="!")

@bot.event
async def on_ready():
    print(f"Discord Bot ready! Logged in as {bot.user}")

bot.run("YOUR_DISCORD_TOKEN")
