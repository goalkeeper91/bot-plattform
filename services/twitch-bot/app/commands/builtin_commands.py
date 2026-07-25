"""
Built-in Twitch chat commands: !uptime, !title, !game, !followage, !shoutout.

Registered as a normal twitchio Component (@commands.command), which means
they're dispatched via bot.process_commands() - CustomCommands.handle_message()
only calls process_commands() when no custom command with the same trigger
matched first, so a streamer's own custom command always wins over these
built-ins with no extra code needed here.
"""
import logging
import os
import time
from datetime import datetime, timezone

import aiohttp
from twitchio.ext import commands

from app.utils.helix_client import HelixClient, HelixInsufficientScopeError

LOGGER = logging.getLogger("BuiltinCommands")

COOLDOWN_SECONDS = 10
RECONNECT_HINT = (
    "❌ Diese Funktion braucht eine erneute Twitch-Anmeldung mit erweiterten "
    "Rechten - bitte im Dashboard Twitch neu verbinden."
)

# !points/!top talk directly to the Go backend's Loyalty tables - no Helix
# call and no broadcaster token needed, unlike every other command in this
# file, since Go itself already fetches chatters + credits points on its own
# scheduler tick (see loyalty_service.go). Same BACKEND_BASE_URL/X-Bot-Secret
# pattern as automod.py's calls to the Go backend.
BACKEND_BASE_URL = "http://backend-go:8080"


class BuiltinCommands(commands.Component):
    def __init__(self, bot):
        self.bot = bot
        self.helix = HelixClient(bot.twitch_config.client_id)
        self.internal_secret = os.environ.get("BOT_INTERNAL_SECRET", "")
        # {f"{broadcaster_id}:{command}": last_used_timestamp}
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

    def _broadcaster_token(self, broadcaster_id: str) -> str | None:
        return self.bot.broadcaster_tokens.get(str(broadcaster_id))

    @staticmethod
    def _format_duration(seconds: float) -> str:
        hours, remainder = divmod(int(seconds), 3600)
        minutes, sec = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m {sec}s"
        if minutes:
            return f"{minutes}m {sec}s"
        return f"{sec}s"

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    async def _send_reconnect_hint(self, ctx: commands.Context):
        await ctx.send(RECONNECT_HINT)

    async def _loyalty_get(self, path: str, params: dict) -> dict | None:
        if not self.internal_secret:
            LOGGER.warning("⚠️ BOT_INTERNAL_SECRET nicht gesetzt - Loyalty-Commands bleiben deaktiviert")
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"{BACKEND_BASE_URL}{path}",
                        params=params,
                        headers={"X-Bot-Secret": self.internal_secret},
                        timeout=aiohttp.ClientTimeout(total=8),
                ) as response:
                    response.raise_for_status()
                    return await response.json()
        except Exception:
            LOGGER.exception("❌ Fehler beim Loyalty-Backend-Aufruf (%s)", path)
            return None

    @commands.command(name="uptime")
    async def uptime(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        if self._on_cooldown(broadcaster_id, "uptime"):
            return

        token = self._broadcaster_token(broadcaster_id)
        if not token:
            await ctx.send("❌ Kein Twitch-Token für diesen Channel gefunden.")
            return

        try:
            stream = await self.helix.get_streams(broadcaster_id, token)
        except HelixInsufficientScopeError:
            await self._send_reconnect_hint(ctx)
            return
        except Exception:
            LOGGER.exception("❌ Fehler bei !uptime")
            await ctx.send("❌ Konnte den Stream-Status nicht abrufen.")
            return

        if not stream:
            await ctx.send(f"📴 {ctx.channel.name} ist gerade offline.")
            return

        delta = datetime.now(timezone.utc) - self._parse_timestamp(stream["started_at"])
        await ctx.send(f"🔴 Live seit {self._format_duration(delta.total_seconds())}")

    @commands.command(name="title")
    async def title(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        token = self._broadcaster_token(broadcaster_id)
        if not token:
            await ctx.send("❌ Kein Twitch-Token für diesen Channel gefunden.")
            return

        parts = ctx.message.text.split(maxsplit=1)
        new_title = parts[1] if len(parts) > 1 else None

        if new_title is None:
            if self._on_cooldown(broadcaster_id, "title"):
                return
            try:
                info = await self.helix.get_channel_info(broadcaster_id, token)
            except HelixInsufficientScopeError:
                await self._send_reconnect_hint(ctx)
                return
            except Exception:
                LOGGER.exception("❌ Fehler bei !title (lesen)")
                await ctx.send("❌ Konnte den Titel nicht abrufen.")
                return

            await ctx.send(f"📝 Titel: {info['title'] if info else 'Unbekannt'}")
            return

        if not self._is_mod_or_broadcaster(ctx):
            return

        try:
            await self.helix.modify_channel_info(broadcaster_id, token, title=new_title)
        except HelixInsufficientScopeError:
            await self._send_reconnect_hint(ctx)
            return
        except Exception:
            LOGGER.exception("❌ Fehler bei !title (setzen)")
            await ctx.send("❌ Konnte den Titel nicht ändern.")
            return

        LOGGER.info("✅ Titel geändert von %s in %s: %s", ctx.author.name, ctx.channel.name, new_title)
        await ctx.send(f"✅ Titel geändert: {new_title}")

    @commands.command(name="game")
    async def game(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        token = self._broadcaster_token(broadcaster_id)
        if not token:
            await ctx.send("❌ Kein Twitch-Token für diesen Channel gefunden.")
            return

        parts = ctx.message.text.split(maxsplit=1)
        new_game = parts[1] if len(parts) > 1 else None

        if new_game is None:
            if self._on_cooldown(broadcaster_id, "game"):
                return
            try:
                info = await self.helix.get_channel_info(broadcaster_id, token)
            except HelixInsufficientScopeError:
                await self._send_reconnect_hint(ctx)
                return
            except Exception:
                LOGGER.exception("❌ Fehler bei !game (lesen)")
                await ctx.send("❌ Konnte das Spiel nicht abrufen.")
                return

            game_name = info["game_name"] if info and info.get("game_name") else "Kein Spiel gesetzt"
            await ctx.send(f"🎮 Spiel: {game_name}")
            return

        if not self._is_mod_or_broadcaster(ctx):
            return

        try:
            game_data = await self.helix.get_games(new_game, token)
            if not game_data:
                await ctx.send(f'❌ Spiel "{new_game}" wurde auf Twitch nicht gefunden.')
                return
            await self.helix.modify_channel_info(broadcaster_id, token, game_id=game_data["id"])
        except HelixInsufficientScopeError:
            await self._send_reconnect_hint(ctx)
            return
        except Exception:
            LOGGER.exception("❌ Fehler bei !game (setzen)")
            await ctx.send("❌ Konnte das Spiel nicht ändern.")
            return

        LOGGER.info("✅ Spiel geändert von %s in %s: %s", ctx.author.name, ctx.channel.name, game_data["name"])
        await ctx.send(f"✅ Spiel geändert: {game_data['name']}")

    @commands.command(name="followage")
    async def followage(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        if self._on_cooldown(broadcaster_id, "followage"):
            return

        token = self._broadcaster_token(broadcaster_id)
        if not token:
            await ctx.send("❌ Kein Twitch-Token für diesen Channel gefunden.")
            return

        parts = ctx.message.text.split(maxsplit=1)
        target_login = parts[1].lstrip("@").strip() if len(parts) > 1 else None

        try:
            if target_login:
                user = await self.helix.get_users(target_login, token)
                if not user:
                    await ctx.send(f'❌ User "{target_login}" nicht gefunden.')
                    return
                target_id, target_name = user["id"], user["display_name"]
            else:
                target_id, target_name = str(ctx.chatter.id), ctx.chatter.name

            follower = await self.helix.get_channel_follower(broadcaster_id, token, target_id)
        except HelixInsufficientScopeError:
            await self._send_reconnect_hint(ctx)
            return
        except Exception:
            LOGGER.exception("❌ Fehler bei !followage")
            await ctx.send("❌ Konnte den Follow-Status nicht abrufen.")
            return

        if not follower:
            await ctx.send(f"❌ {target_name} folgt {ctx.channel.name} nicht.")
            return

        delta = datetime.now(timezone.utc) - self._parse_timestamp(follower["followed_at"])
        await ctx.send(f"💜 {target_name} folgt seit {self._format_duration(delta.total_seconds())}")

    @commands.command(name="shoutout", aliases=["so"])
    async def shoutout(self, ctx: commands.Context):
        if not self._is_mod_or_broadcaster(ctx):
            return

        broadcaster_id = str(ctx.channel.id)
        token = self._broadcaster_token(broadcaster_id)
        if not token:
            await ctx.send("❌ Kein Twitch-Token für diesen Channel gefunden.")
            return

        parts = ctx.message.text.split(maxsplit=1)
        if len(parts) < 2:
            await ctx.send("❌ Usage: !shoutout <username>")
            return

        target_login = parts[1].lstrip("@").strip()

        try:
            user = await self.helix.get_users(target_login, token)
            if not user:
                await ctx.send(f'❌ User "{target_login}" nicht gefunden.')
                return
            info = await self.helix.get_channel_info(user["id"], token)
        except HelixInsufficientScopeError:
            await self._send_reconnect_hint(ctx)
            return
        except Exception:
            LOGGER.exception("❌ Fehler bei !shoutout")
            await ctx.send("❌ Konnte den Shoutout nicht erstellen.")
            return

        game_name = info["game_name"] if info and info.get("game_name") else None
        game_suffix = f" — zuletzt bei {game_name}" if game_name else ""
        await ctx.send(
            f"🎉 Schaut mal bei {user['display_name']} vorbei{game_suffix}! "
            f"https://twitch.tv/{user['login']}"
        )

    @commands.command(name="points")
    async def points(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        if self._on_cooldown(broadcaster_id, "points"):
            return

        parts = ctx.message.text.split(maxsplit=1)
        target_login = parts[1].lstrip("@").strip() if len(parts) > 1 else ctx.chatter.name

        data = await self._loyalty_get(
            "/api/bot/loyalty/points",
            {"broadcaster_id": broadcaster_id, "viewer_login": target_login},
        )
        if data is None:
            await ctx.send("❌ Konnte die Punkte nicht abrufen.")
            return

        await ctx.send(f"🏆 {target_login} hat {data['points']} {data['points_name']}.")

    @commands.command(name="top")
    async def top(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        if self._on_cooldown(broadcaster_id, "top"):
            return

        data = await self._loyalty_get(
            "/api/bot/loyalty/leaderboard",
            {"broadcaster_id": broadcaster_id, "limit": 5},
        )
        if data is None:
            await ctx.send("❌ Konnte die Bestenliste nicht abrufen.")
            return

        entries = data.get("entries") or []
        if not entries:
            await ctx.send("Noch keine Einträge in der Bestenliste.")
            return

        ranking = ", ".join(
            f"{i}. {entry['viewer_login']} ({entry['points']})"
            for i, entry in enumerate(entries, start=1)
        )
        await ctx.send(f"🏆 Top {data['points_name']}: {ranking}")

    @commands.command(name="regulars")
    async def regulars(self, ctx: commands.Context):
        broadcaster_id = str(ctx.channel.id)
        if self._on_cooldown(broadcaster_id, "regulars"):
            return

        data = await self._loyalty_get(
            "/api/bot/loyalty/regulars",
            {"broadcaster_id": broadcaster_id},
        )
        if data is None:
            await ctx.send("❌ Konnte die Regulars nicht abrufen.")
            return

        logins = data.get("logins") or []
        if not logins:
            await ctx.send("Noch keine Regulars.")
            return

        await ctx.send(f"⭐ Regulars: {', '.join(logins)}")
