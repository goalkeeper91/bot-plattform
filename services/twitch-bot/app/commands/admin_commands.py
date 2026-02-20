import logging
from twitchio.ext import commands

LOGGER = logging.getLogger("AdminCommands")


class AdminCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot

    @commands.Component.guard()
    def is_moderator_or_broadcaster(self, ctx: commands.Context) -> bool:
        return ctx.chatter.moderator or ctx.chatter.broadcaster

    async def get_channel_id(self, broadcaster_id: str) -> int | None:
        try:
            async with self.bot.db.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT id FROM twitch_channels 
                    WHERE twitch_user_id = $1
                """, broadcaster_id)

                if row:
                    return row['id']
                return None
        except Exception as e:
            LOGGER.error("❌ Fehler beim Abrufen der channel_id: %s", e)
            return None

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context):
        await ctx.send("Pong 🏓")

    @commands.command(name="reload")
    async def reload(self, ctx: commands.Context):
        if ctx.author.id != int(self.bot.owner_id):
            return

        await self.bot.reload_commands()
        await ctx.send("✅ Commands wurden neu geladen!")

    # ==========================================================
    # Command Management (Broadcaster + Mods)
    # ==========================================================

    @commands.command(name="addcommand", aliases=["addcmd"])
    async def add_command(self, ctx: commands.Context):
        if not (ctx.chatter.moderator or ctx.chatter.broadcaster):
            return

        parts = ctx.message.content.split(maxsplit=2)

        if len(parts) < 3:
            await ctx.send("❌ Usage: !addcommand <trigger> <response>")
            return

        trigger = parts[1].lower().lstrip('!')
        response = parts[2]

        if not trigger or len(trigger) > 100:
            await ctx.send("❌ Command-Name muss zwischen 1-100 Zeichen sein")
            return

        if not trigger.replace('_', '').replace('-', '').isalnum():
            await ctx.send("❌ Command-Name darf nur Buchstaben, Zahlen, _ und - enthalten")
            return

        channel_id = await self.get_channel_id(str(ctx.channel.id))
        if not channel_id:
            await ctx.send("❌ Channel nicht gefunden!")
            return

        try:
            async with self.bot.db.acquire() as conn:
                exists = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM twitch_chat_commands 
                        WHERE channel_id = $1 AND command = $2
                    )
                """, channel_id, trigger)

                if exists:
                    await ctx.send(f"❌ Command !{trigger} existiert bereits. Nutze !editcommand zum Bearbeiten")
                    return

                await conn.execute("""
                    INSERT INTO twitch_chat_commands (channel_id, trigger, response, cooldown, updated_at)
                    VALUES ($1, $2, $3, 0, NOW())
                """, channel_id, trigger, response)

            LOGGER.info("✅ Command !%s erstellt von %s in %s", trigger, ctx.author.name, ctx.channel.name)

            await self.bot.reload_commands(str(channel_id))

            await ctx.send(f"✅ Command !{trigger} wurde erfolgreich erstellt!")

        except Exception as e:
            LOGGER.error("❌ Fehler beim Erstellen des Commands: %s", e, exc_info=True)
            await ctx.send("❌ Fehler beim Erstellen des Commands")

    @commands.command(name="editcommand", aliases=["editcmd"])
    async def edit_command(self, ctx: commands.Context):
        if not (ctx.chatter.moderator or ctx.chatter.broadcaster):
            return

        parts = ctx.message.content.split(maxsplit=2)

        if len(parts) < 3:
            await ctx.send("❌ Usage: !editcommand <trigger> <neue_response>")
            return

        trigger = parts[1].lower().lstrip('!')
        response = parts[2]

        channel_id = await self.get_channel_id(str(ctx.channel.id))
        if not channel_id:
            await ctx.send("❌ Channel nicht gefunden")
            return

        try:
            async with self.bot.db.acquire() as conn:
                result = await conn.execute("""
                    UPDATE twitch_chat_commands 
                    SET response = $1, updated_at = NOW()
                    WHERE channel_id = $2 AND trigger = $3
                """, response, channel_id, trigger)

            success = result.split()[-1] != '0'

            if success:
                LOGGER.info("✅ Command !%s bearbeitet von %s", trigger, ctx.author.name)
                await self.bot.reload_commands(str(channel_id))
                await ctx.send(f"✅ Command !{trigger} wurde aktualisiert!")
            else:
                await ctx.send(f"❌ Command !{trigger} nicht gefunden")

        except Exception as e:
            LOGGER.error("❌ Fehler beim Bearbeiten: %s", e, exc_info=True)
            await ctx.send("❌ Fehler beim Bearbeiten des Commands")

    @commands.command(name="delcommand", aliases=["deletecommand", "delcmd", "removecmd"])
    async def delete_command(self, ctx: commands.Context):
        if not (ctx.chatter.moderator or ctx.chatter.broadcaster):
            return

        parts = ctx.message.content.split(maxsplit=1)

        if len(parts) < 2:
            await ctx.send("❌ Usage: !delcommand <trigger>")
            return

        trigger = parts[1].lower().lstrip('!')

        channel_id = await self.get_channel_id(str(ctx.channel.id))
        if not channel_id:
            await ctx.send("❌ Channel nicht gefunden")
            return

        try:
            async with self.bot.db.acquire() as conn:
                result = await conn.execute("""
                    DELETE FROM twitch_chat_commands 
                    WHERE channel_id = $1 AND trigger = $2
                """, channel_id, trigger)

            success = result.split()[-1] != '0'

            if success:
                LOGGER.info("✅ Command !%s gelöscht von %s", trigger, ctx.author.name)
                await self.bot.reload_commands(str(channel_id))
                await ctx.send(f"✅ Command !{trigger} wurde gelöscht!")
            else:
                await ctx.send(f"❌ Command !{trigger} nicht gefunden")

        except Exception as e:
            LOGGER.error("❌ Fehler beim Löschen: %s", e, exc_info=True)
            await ctx.send("❌ Fehler beim Löschen des Commands")

    @commands.command(name="togglecommand", aliases=["togglecmd"])
    async def toggle_command(self, ctx: commands.Context):
        if not (ctx.chatter.moderator or ctx.chatter.broadcaster):
            return

        parts = ctx.message.content.split(maxsplit=1)

        if len(parts) < 2:
            await ctx.send("❌ Usage: !togglecommand <trigger>")
            return

        trigger = parts[1].lower().lstrip('!')

        channel_id = await self.get_channel_id(str(ctx.channel.id))
        if not channel_id:
            await ctx.send("❌ Channel nicht gefunden")
            return

        try:
            async with self.bot.db.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT enabled FROM twitch_chat_commands
                    WHERE channel_id = $1 AND trigger = $2
                """, channel_id, trigger)

                if not row:
                    await ctx.send(f"❌ Command !{trigger} nicht gefunden")
                    return

                new_status = not row['enabled']

                await conn.execute("""
                    UPDATE twitch_chat_commands 
                    SET enabled = $1, updated_at = NOW()
                    WHERE channel_id = $2 AND trigger = $3
                """, new_status, channel_id, trigger)

            status_text = "aktiviert" if new_status else "deaktiviert"
            LOGGER.info("✅ Command !%s %s von %s", trigger, status_text, ctx.author.name)

            await self.bot.reload_commands(str(channel_id))
            await ctx.send(f"✅ Command !{trigger} wurde {status_text}!")

        except Exception as e:
            LOGGER.error("❌ Fehler beim Toggle: %s", e, exc_info=True)
            await ctx.send("❌ Fehler beim Umschalten des Commands")

    @commands.command(name="setcooldown", aliases=["cooldown"])
    async def set_cooldown(self, ctx: commands.Context):
        if not (ctx.chatter.moderator or ctx.chatter.broadcaster):
            return

        parts = ctx.message.content.split(maxsplit=2)

        if len(parts) < 3:
            await ctx.send("❌ Usage: !setcooldown <trigger> <sekunden>")
            return

        trigger = parts[1].lower().lstrip('!')

        try:
            cooldown = int(parts[2])
            if cooldown < 0:
                await ctx.send("❌ Cooldown muss 0 oder größer sein")
                return
        except ValueError:
            await ctx.send("❌ Cooldown muss eine Zahl sein")
            return

        channel_id = await self.get_channel_id(str(ctx.channel.id))
        if not channel_id:
            await ctx.send("❌ Channel nicht gefunden")
            return

        try:
            async with self.bot.db.acquire() as conn:
                result = await conn.execute("""
                    UPDATE twitch_chat_commands 
                    SET cooldown = $1, updated_at = NOW()
                    WHERE channel_id = $2 AND trigger = $3
                """, cooldown, channel_id, trigger)

            success = result.split()[-1] != '0'

            if success:
                LOGGER.info("✅ Cooldown für !%s auf %ds gesetzt", trigger, cooldown)
                await self.bot.reload_commands(str(channel_id))
                await ctx.send(f"✅ Cooldown für !{trigger} auf {cooldown} Sekunden gesetzt!")
            else:
                await ctx.send(f"❌ Command !{trigger} nicht gefunden")

        except Exception as e:
            LOGGER.error("❌ Fehler beim Setzen des Cooldowns: %s", e, exc_info=True)
            await ctx.send("❌ Fehler beim Setzen des Cooldowns")

    @commands.command(name="commands", aliases=["cmds", "commandlist"])
    async def list_commands(self, ctx: commands.Context):
        channel_id = await self.get_channel_id(str(ctx.channel.id))
        if not channel_id:
            return

        try:
            async with self.bot.db.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT trigger FROM twitch_chat_commands
                    WHERE channel_id = $1 AND enabled = true
                    ORDER BY trigger
                """, channel_id)

            if not rows:
                await ctx.send("📋 Keine Custom Commands vorhanden")
                return

            commands_list = [f"!{row['command']}" for row in rows]
            commands_str = ", ".join(commands_list)

            await ctx.send(f"📋 Custom Commands: {commands_str}")

        except Exception as e:
            LOGGER.error("❌ Fehler beim Auflisten: %s", e, exc_info=True)
            await ctx.send("❌ Fehler beim Auflisten der Commands")