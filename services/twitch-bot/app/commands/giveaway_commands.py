"""
Giveaway Commands: !giveaway start/draw/status are ! commands (mod/
broadcaster addressing the bot directly), but entering a giveaway is NOT a !
command - viewers type a streamer-chosen codeword plainly in chat, exactly
like a classic Twitch keyword giveaway. That means entry can't go through
twitchio's command dispatcher; it's checked against every chat message in
check_message (mirrors AutomodFilter.check_message), using an in-memory
cache of {broadcaster_id: lowercased_keyword} kept in sync by this bot's own
!giveaway start/draw calls (no other code path can open/close a giveaway,
see Giveaways Teil 1's "dashboard stays read-only" decision) - no periodic
reload needed, only a one-time warmup on bot startup in case of a restart
mid-giveaway (see reload_open_giveaways).

Each backend call still goes straight to the Go backend's bot-internal
/api/bot/giveaways/* endpoints (no n8n relay, unlike the older Vote system),
same X-Bot-Secret pattern as automod.py and the Loyalty commands in
builtin_commands.py.
"""
import logging
import os
import time

import aiohttp
from twitchio.ext import commands

LOGGER = logging.getLogger("GiveawayCommands")

BACKEND_BASE_URL = "http://backend-go:8080"

COOLDOWN_SECONDS = 5


class GiveawayCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot
        self.internal_secret = os.environ.get("BOT_INTERNAL_SECRET", "")
        self.cooldowns: dict[str, float] = {}
        # {broadcaster_id: lowercased keyword} - only channels with a
        # currently open giveaway have an entry.
        self.open_giveaways: dict[str, str] = {}

    @staticmethod
    def _is_mod_or_broadcaster(ctx: commands.Context) -> bool:
        return ctx.chatter.moderator or ctx.chatter.broadcaster

    def _on_cooldown(self, broadcaster_id: str, command: str) -> bool:
        key = f"{broadcaster_id}:{command}"
        now = time.time()
        last_used = self.cooldowns.get(key)
        if last_used and now - last_used < COOLDOWN_SECONDS:
            return True
        self.cooldowns[key] = now
        return False

    async def _post(self, path: str, payload: dict) -> tuple[int, dict | None]:
        if not self.internal_secret:
            LOGGER.warning("⚠️ BOT_INTERNAL_SECRET nicht gesetzt - Giveaway-Commands bleiben deaktiviert")
            return 0, None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f"{BACKEND_BASE_URL}{path}",
                        json=payload,
                        headers={"X-Bot-Secret": self.internal_secret},
                        timeout=aiohttp.ClientTimeout(total=8),
                ) as response:
                    data = None
                    try:
                        data = await response.json()
                    except Exception:
                        pass
                    return response.status, data
        except Exception:
            LOGGER.exception("❌ Fehler beim Giveaway-Backend-Aufruf (%s)", path)
            return 0, None

    async def _get(self, path: str, params: dict) -> tuple[int, dict | None]:
        if not self.internal_secret:
            LOGGER.warning("⚠️ BOT_INTERNAL_SECRET nicht gesetzt - Giveaway-Commands bleiben deaktiviert")
            return 0, None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"{BACKEND_BASE_URL}{path}",
                        params=params,
                        headers={"X-Bot-Secret": self.internal_secret},
                        timeout=aiohttp.ClientTimeout(total=8),
                ) as response:
                    data = None
                    try:
                        data = await response.json()
                    except Exception:
                        pass
                    return response.status, data
        except Exception:
            LOGGER.exception("❌ Fehler beim Giveaway-Backend-Aufruf (%s)", path)
            return 0, None

    async def reload_open_giveaways(self):
        """Warmt den Codewort-Cache beim Bot-Start auf - fängt nur den Fall
        ab, dass der Bot neu startet, während ein Giveaway noch offen ist.
        Danach hält !giveaway start/draw den Cache selbst aktuell."""
        status, data = await self._get("/api/bot/giveaways/open", {})
        if status != 200 or data is None:
            LOGGER.warning("⚠️ Konnte offene Giveaways beim Start nicht laden (status=%s)", status)
            return

        self.open_giveaways = {
            str(g["user_twitch_id"]): g["keyword"].lower() for g in data
        }
        LOGGER.info("✅ Giveaway-Codewörter geladen für %d Kanal/Kanäle", len(self.open_giveaways))

    async def refresh_single(self, broadcaster_id: str):
        """Aktualisiert den Cache-Eintrag für EINEN Kanal - ausgelöst durch
        das REFRESH_GIVEAWAY-Redis-Signal, das jetzt sowohl von Chat-Commands
        als auch vom Dashboard gesendet wird (Giveaways Teil 3 macht das
        Dashboard zu einem zweiten Writer). Nutzt den bereits bestehenden
        /api/bot/giveaways/status-Endpoint, kein neuer Go-Endpoint nötig."""
        status, data = await self._get("/api/bot/giveaways/status", {"broadcaster_id": broadcaster_id})
        if status != 200 or data is None:
            LOGGER.warning("⚠️ Konnte Giveaway-Status für %s nicht aktualisieren (status=%s)", broadcaster_id, status)
            return

        giveaway = data.get("giveaway")
        if giveaway:
            self.open_giveaways[broadcaster_id] = giveaway["keyword"].lower()
        else:
            self.open_giveaways.pop(broadcaster_id, None)

    async def check_message(self, message) -> bool:
        """Prüft JEDE Chat-Nachricht gegen das offene Codewort des Kanals -
        kein !-Präfix, exaktes Match (Groß-/Kleinschreibung wird ignoriert).
        Gibt True zurück, wenn die Nachricht als Teilnahme gewertet wurde."""
        bot_id = str(self.bot.twitch_config.bot_id)
        if str(message.chatter.id) == bot_id:
            return False

        broadcaster_id = str(message.broadcaster.id)
        keyword = self.open_giveaways.get(broadcaster_id)
        if not keyword:
            return False

        if message.text.strip().lower() != keyword:
            return False

        status, data = await self._post(
            "/api/bot/giveaways/enter",
            {
                "broadcaster_id": broadcaster_id,
                "viewer_twitch_id": str(message.chatter.id),
                "viewer_login": message.chatter.name,
                "is_subscriber": message.chatter.subscriber,
            },
        )
        if status == 404:
            # Cache war stale (Giveaway wurde z.B. extern geschlossen) -
            # lokal ebenfalls aufräumen, kein weiterer Versuch nötig.
            self.open_giveaways.pop(broadcaster_id, None)
            return False
        if status != 200 or data is None:
            return False

        if data.get("entered"):
            await self._send_to_broadcaster(message.broadcaster.name, f"✅ {message.chatter.name} ist dabei!")
        # Wiederholtes Tippen des Codeworts ohne neue Eintragung bleibt
        # bewusst still - kein Bestätigungs-Spam.
        return True

    async def _send_to_broadcaster(self, broadcaster_name: str, text: str):
        """check_message operates on a raw ChatMessage, not a commands.Context,
        so there's no ctx.send() available - same channel-lookup pattern as
        BotAnnouncer._send_to_channel."""
        for ch in self.bot.connected_channels:
            if ch.name.lower() == broadcaster_name.lower():
                await ch.send(text)
                return
        LOGGER.warning("⚠️ Konnte Giveaway-Bestätigung nicht senden - Kanal nicht verbunden: %s", broadcaster_name)

    @commands.command(name="giveaway")
    async def giveaway(self, ctx: commands.Context):
        """
        !giveaway start <codewort> [subs]   - Giveaway starten (Mod/Broadcaster)
        !giveaway draw                      - Gewinner ziehen (Mod/Broadcaster)
        !giveaway cancel                    - Ohne Gewinner beenden (Mod/Broadcaster)
        !giveaway status                    - Teilnehmerzahl anzeigen

        Teilnehmen ist KEIN !-Befehl - Zuschauer tippen einfach das Codewort.
        """
        parts = ctx.message.text.split()
        subcommand = parts[1].lower() if len(parts) > 1 else None

        if subcommand == "start":
            await self._start(ctx, parts[2:])
        elif subcommand == "draw":
            await self._draw(ctx)
        elif subcommand == "cancel":
            await self._cancel(ctx)
        elif subcommand == "status":
            await self._status(ctx)
        else:
            await ctx.send("Usage: !giveaway start <codewort> [subs] | !giveaway draw | !giveaway cancel | !giveaway status")

    async def _start(self, ctx: commands.Context, args: list[str]):
        if not self._is_mod_or_broadcaster(ctx):
            return

        if not args:
            await ctx.send("❌ Usage: !giveaway start <codewort> [subs]")
            return

        keyword = args[0]
        sub_bonus = "subs" in args[1:]
        broadcaster_id = str(ctx.channel.id)

        status, data = await self._post(
            "/api/bot/giveaways/start",
            {"broadcaster_id": broadcaster_id, "keyword": keyword, "sub_bonus": sub_bonus},
        )
        if status == 409:
            await ctx.send("❌ Es läuft bereits ein Giveaway - erst mit !giveaway draw beenden.")
            return
        if status == 400:
            await ctx.send("❌ Codewort muss zwischen 2 und 50 Zeichen lang sein.")
            return
        if status != 200 or data is None:
            await ctx.send("❌ Konnte das Giveaway nicht starten.")
            return

        self.open_giveaways[broadcaster_id] = data["keyword"].lower()

        hint = " Abonnenten bekommen doppelte Lose!" if sub_bonus else ""
        await ctx.send(f"🎉 Giveaway gestartet! Tippt \"{data['keyword']}\" in den Chat zum Teilnehmen.{hint}")

    async def _draw(self, ctx: commands.Context):
        if not self._is_mod_or_broadcaster(ctx):
            return

        broadcaster_id = str(ctx.channel.id)
        if self._on_cooldown(broadcaster_id, "draw"):
            return

        status, data = await self._post("/api/bot/giveaways/draw", {"broadcaster_id": broadcaster_id})
        if status == 409:
            await ctx.send("❌ Kein offenes Giveaway oder noch keine Teilnehmer.")
            return
        if status != 200 or data is None:
            await ctx.send("❌ Konnte keinen Gewinner ziehen.")
            return

        self.open_giveaways.pop(broadcaster_id, None)

        winner = data.get("winner_login", "?")
        await ctx.send(f"🎉 Der Gewinner ist @{winner}! Herzlichen Glückwunsch!")

    async def _cancel(self, ctx: commands.Context):
        if not self._is_mod_or_broadcaster(ctx):
            return

        broadcaster_id = str(ctx.channel.id)

        status, data = await self._post("/api/bot/giveaways/cancel", {"broadcaster_id": broadcaster_id})
        if status == 409:
            await ctx.send("❌ Kein offenes Giveaway.")
            return
        if status != 200 or data is None:
            await ctx.send("❌ Konnte das Giveaway nicht abbrechen.")
            return

        self.open_giveaways.pop(broadcaster_id, None)
        await ctx.send("🚫 Giveaway abgebrochen.")

    async def _status(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        status, data = await self._get("/api/bot/giveaways/status", {"broadcaster_id": broadcaster_id})
        if status != 200 or data is None:
            await ctx.send("❌ Konnte den Giveaway-Status nicht abrufen.")
            return

        giveaway = data.get("giveaway")
        if not giveaway:
            await ctx.send("Kein Giveaway aktiv.")
            return

        entry_count = data.get("entry_count", 0)
        await ctx.send(f"🎁 Giveaway läuft mit Codewort \"{giveaway['keyword']}\"! {entry_count} Teilnehmer bisher.")
