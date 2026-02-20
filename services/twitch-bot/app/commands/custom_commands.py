import logging
import time
from twitchio.ext import commands
from twitchio import ChatMessage

LOGGER = logging.getLogger("CustomCommands")


class CustomCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot

        # {channel_id: {trigger: {response, cooldown}}}
        self.commands_cache: dict[str, dict[str, dict]] = {}

        # {twitch_user_id: internal_channel_id}
        self.channel_map: dict[str, str] = {}

        # {f"{channel_id}:{trigger}": last_used_timestamp}
        self.cooldowns: dict[str, float] = {}

    async def component_load(self):
        await self.reload_commands()

    # ==========================================================
    # Cache Reload
    # ==========================================================

    async def reload_commands(self, channel_id: str | None = None):
        LOGGER.info("🔄 Lade Custom Commands aus DB...")

        try:
            async with self.bot.db.acquire() as conn:
                query = """
                        SELECT cc.channel_id, \
                               cc.trigger, \
                               cc.response, \
                               cc.cooldown, \
                               tc.twitch_user_id
                        FROM twitch_chat_commands cc
                                 JOIN twitch_channels tc ON cc.channel_id::bigint = tc.id
                        WHERE cc.enabled = true \
                        """

                if channel_id:
                    query += " AND cc.channel_id = $1"
                    rows = await conn.fetch(query, channel_id)
                else:
                    rows = await conn.fetch(query)

            self.commands_cache.clear()
            self.channel_map.clear()

            for row in rows:
                ch_id = str(row["channel_id"])
                trigger = row["trigger"].lower()

                self.commands_cache.setdefault(ch_id, {})[trigger] = {
                    "response": row["response"],
                    "cooldown": row["cooldown"] or 0,
                }

                self.channel_map[str(row["twitch_user_id"])] = ch_id

            LOGGER.info(
                "✅ %d Channels, %d Commands geladen",
                len(self.commands_cache),
                sum(len(v) for v in self.commands_cache.values()),
            )

        except Exception:
            LOGGER.exception("❌ Fehler beim Laden der Custom Commands")

    # ==========================================================
    # Message Handling
    # ==========================================================

    async def handle_message(self, message: ChatMessage):
        """Handler für eingehende Chat-Nachrichten"""
        LOGGER.debug("📨 CustomCommands.handle_message aufgerufen")

        bot_id = str(self.bot.twitch_config.bot_id)
        if str(message.chatter.id) == bot_id:
            return

        prefix = self.bot.twitch_config.prefix
        if not message.text.startswith(prefix):
            await self.bot.process_commands(message)
            return

        trigger = message.text[len(prefix):].split()[0].lower()
        broadcaster_id = str(message.broadcaster.id)

        channel_id = self.channel_map.get(broadcaster_id)

        is_custom_command = False
        if channel_id:
            cmd_data = self.commands_cache.get(channel_id, {}).get(trigger)
            if cmd_data:
                is_custom_command = True

                cooldown_key = f"{channel_id}:{trigger}"
                now = time.time()

                last_used = self.cooldowns.get(cooldown_key)
                if last_used and now - last_used < cmd_data["cooldown"]:
                    LOGGER.debug("⏱️ Cooldown active for !%s", trigger)
                    return

                try:
                    # ✅ Erstelle Context aus Message und nutze ctx.send()
                    ctx = self.bot.get_context(message)

                    if ctx:
                        await ctx.send(cmd_data["response"])

                        self.cooldowns[cooldown_key] = now

                        # ✅ Track successful command execution
                        if hasattr(self.bot, 'stats') and self.bot.stats:
                            await self.bot.stats.track_command(
                                command_name=trigger,
                                channel_id=channel_id,
                                success=True
                            )

                        LOGGER.info(
                            "✅ Custom Command | channel=%s | !%s",
                            message.broadcaster.name,
                            trigger,
                        )
                    else:
                        LOGGER.error("❌ Konnte keinen Context erstellen")

                        if hasattr(self.bot, 'stats') and self.bot.stats:
                            await self.bot.stats.track_command(
                                command_name=trigger,
                                channel_id=channel_id,
                                success=False
                            )

                except Exception:
                    LOGGER.exception("❌ Fehler beim Senden des Custom Commands")

                    # ✅ Track failed command
                    if hasattr(self.bot, 'stats') and self.bot.stats:
                        await self.bot.stats.track_command(
                            command_name=trigger,
                            channel_id=channel_id,
                            success=False
                        )

                return

        await self.bot.process_commands(message)