from twitchio.ext import commands

class CustomCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def befehl(self, ctx: commands.Context):
        async with self.bot.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT response FROM custom_commands WHERE name = $1",
                ctx.command.name,
            )

        if row:
            await ctx.send(row["response"])
