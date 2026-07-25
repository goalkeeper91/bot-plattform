"""
Giveaway Commands: !giveaway start/enter/draw/status. Each command calls the
Go backend's bot-internal /api/bot/giveaways/* endpoints directly (no n8n
relay, unlike the older Vote system) - stateless per call, same
X-Bot-Secret pattern as automod.py and the Loyalty commands in
builtin_commands.py. No settings cache needed since there's nothing to
cache: every call resolves the channel's current open giveaway server-side.
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

    @commands.command(name="giveaway")
    async def giveaway(self, ctx: commands.Context):
        """
        !giveaway start [subs]   - Giveaway starten (Mod/Broadcaster)
        !giveaway [enter]        - Teilnehmen
        !giveaway draw           - Gewinner ziehen (Mod/Broadcaster)
        !giveaway status         - Teilnehmerzahl anzeigen
        """
        parts = ctx.message.text.split()
        subcommand = parts[1].lower() if len(parts) > 1 else "enter"

        if subcommand == "start":
            await self._start(ctx, sub_bonus="subs" in parts[2:])
        elif subcommand == "draw":
            await self._draw(ctx)
        elif subcommand == "status":
            await self._status(ctx)
        elif subcommand == "enter":
            await self._enter(ctx)
        else:
            await self._enter(ctx)

    async def _start(self, ctx: commands.Context, sub_bonus: bool):
        if not self._is_mod_or_broadcaster(ctx):
            return

        broadcaster_id = str(ctx.channel.id)
        status, data = await self._post(
            "/api/bot/giveaways/start",
            {"broadcaster_id": broadcaster_id, "sub_bonus": sub_bonus},
        )
        if status == 409:
            await ctx.send("❌ Es läuft bereits ein Giveaway - erst mit !giveaway draw beenden.")
            return
        if status != 200 or data is None:
            await ctx.send("❌ Konnte das Giveaway nicht starten.")
            return

        hint = " Abonnenten bekommen doppelte Lose!" if sub_bonus else ""
        await ctx.send(f"🎉 Giveaway gestartet! Tippt !giveaway zum Teilnehmen.{hint}")

    async def _enter(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        status, data = await self._post(
            "/api/bot/giveaways/enter",
            {
                "broadcaster_id": broadcaster_id,
                "viewer_twitch_id": str(ctx.chatter.id),
                "viewer_login": ctx.chatter.name,
                "is_subscriber": ctx.chatter.subscriber,
            },
        )
        if status == 404:
            await ctx.send("Kein Giveaway aktiv.")
            return
        if status != 200 or data is None:
            return

        if data.get("entered"):
            await ctx.send(f"✅ {ctx.chatter.name} ist dabei!")
        # Wiederholtes !giveaway ohne neue Eintragung bleibt bewusst still -
        # kein Bestätigungs-Spam bei jedem erneuten Tippen.

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

        winner = data.get("winner_login", "?")
        await ctx.send(f"🎉 Der Gewinner ist @{winner}! Herzlichen Glückwunsch!")

    async def _status(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        status, data = await self._get("/api/bot/giveaways/status", {"broadcaster_id": broadcaster_id})
        if status != 200 or data is None:
            await ctx.send("❌ Konnte den Giveaway-Status nicht abrufen.")
            return

        if not data.get("giveaway"):
            await ctx.send("Kein Giveaway aktiv.")
            return

        entry_count = data.get("entry_count", 0)
        await ctx.send(f"🎁 Giveaway läuft! {entry_count} Teilnehmer bisher.")
