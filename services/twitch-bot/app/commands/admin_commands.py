from twitchio.ext import commands

class AdminCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send("pong 🏓")

    @commands.command(name="reload")
    async def reload(self, ctx: commands.Context):
        if ctx.author.id != int(self.bot.owner_id):
            return
        await ctx.send("Reload noch nicht implementiert")
