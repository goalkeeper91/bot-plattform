"""
Automod: deletes chat messages containing blocked words/phrases or
disallowed links, and issues an escalating timeout on the sender. Settings
are cached in memory per channel (see reload_settings) so the actual
per-message check never does I/O - only an actual violation (rare) triggers
Helix calls + a small Go-backend call for the escalation counter.
"""
import logging
import os
import re

import aiohttp

from app.utils.helix_client import HelixClient, HelixInsufficientScopeError

LOGGER = logging.getLogger("AutomodFilter")

BACKEND_BASE_URL = "http://backend-go:8080"

# Recognizes explicit http(s)/www links anywhere, or a bare "word.tld" where
# tld is a common one - a full precise URL grammar is out of scope for v1
# (see plan: "eigene, unabhängige Prüfung reicht"), this is a good-enough
# heuristic that avoids flagging things like "e.g." or "z.b.".
_KNOWN_TLDS = {
    "com", "net", "org", "tv", "gg", "io", "co", "me", "live", "stream",
    "de", "app", "xyz", "info", "biz", "shop", "store", "online", "tk",
}
_LINK_PATTERN = re.compile(
    r"\b((?:https?://)?(?:www\.)?(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+([a-zA-Z]{2,}))\b",
    re.IGNORECASE,
)


def _extract_link_domains(text: str) -> list[str]:
    domains = []
    for match in _LINK_PATTERN.finditer(text):
        full, tld = match.group(1), match.group(2)
        has_explicit_prefix = full.lower().startswith(("http://", "https://", "www."))
        if not has_explicit_prefix and tld.lower() not in _KNOWN_TLDS:
            continue
        domain = re.sub(r"^https?://", "", full, flags=re.IGNORECASE)
        domain = re.sub(r"^www\.", "", domain, flags=re.IGNORECASE)
        domains.append(domain.lower())
    return domains


class AutomodFilter:
    def __init__(self, bot):
        self.bot = bot
        self.helix = HelixClient(bot.twitch_config.client_id)
        self.internal_secret = os.environ.get("BOT_INTERNAL_SECRET", "")
        # {broadcaster_twitch_id: settings-dict from GET /api/bot/automod-settings}
        self.settings_cache: dict[str, dict] = {}

    async def reload_settings(self, twitch_user_id: str | None = None):
        """Refetches ALL enabled channels' settings from the Go backend and
        replaces the cache wholesale. twitch_user_id is accepted (matching
        reload_commands' signature used by the same Redis-signal dispatch)
        but unused - one small GET for every enabled channel is simpler than
        per-channel fetches, and this only runs on startup + rare settings
        changes, never per chat message."""
        if not self.internal_secret:
            LOGGER.warning("⚠️ BOT_INTERNAL_SECRET nicht gesetzt - Automod bleibt deaktiviert")
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        f"{BACKEND_BASE_URL}/api/bot/automod-settings",
                        headers={"X-Bot-Secret": self.internal_secret},
                        timeout=aiohttp.ClientTimeout(total=8),
                ) as response:
                    response.raise_for_status()
                    settings_list = await response.json()
        except Exception as e:
            LOGGER.error("❌ Fehler beim Laden der Automod-Settings: %s", e)
            return

        self.settings_cache = {s["user_twitch_id"]: s for s in settings_list}
        LOGGER.info("✅ Automod-Settings geladen für %d Channel(s)", len(self.settings_cache))

    async def check_message(self, message) -> bool:
        """Returns True if the message violated automod and was handled
        (deleted + timeout issued) - the caller should stop further
        processing (no command dispatch) for that message."""
        if message.chatter.moderator or message.chatter.broadcaster:
            return False
        if str(message.chatter.id) == str(self.bot.twitch_config.bot_id):
            return False

        broadcaster_id = str(message.broadcaster.id)
        settings = self.settings_cache.get(broadcaster_id)
        if not settings or not settings.get("enabled"):
            return False

        reason = self._find_violation(message.text, settings)
        if reason is None:
            return False

        token = self.bot.broadcaster_tokens.get(broadcaster_id)
        if not token:
            LOGGER.warning("⚠️ Kein Token für Automod-Aktion in Channel %s", broadcaster_id)
            return False

        try:
            await self.helix.delete_message(broadcaster_id, broadcaster_id, message.id, token)
        except HelixInsufficientScopeError:
            LOGGER.warning("⚠️ Automod: fehlender Scope zum Löschen in Channel %s", broadcaster_id)
        except Exception as e:
            LOGGER.error("❌ Automod: Fehler beim Löschen der Nachricht: %s", e)

        offender_id = str(message.chatter.id)
        timeout_seconds = await self._record_violation(broadcaster_id, offender_id)

        try:
            await self.helix.timeout_user(
                broadcaster_id, broadcaster_id, offender_id, timeout_seconds, reason, token
            )
        except HelixInsufficientScopeError:
            LOGGER.warning("⚠️ Automod: fehlender Scope für Timeout in Channel %s", broadcaster_id)
        except Exception as e:
            LOGGER.error("❌ Automod: Fehler beim Timeout: %s", e)

        LOGGER.info(
            "🛡️ Automod-Verstoß | channel=%s | user=%s | reason=%s | timeout=%ds",
            message.broadcaster.name, message.chatter.name, reason, timeout_seconds,
        )
        return True

    @staticmethod
    def _find_violation(text: str, settings: dict) -> str | None:
        lowered = text.lower()

        for word in settings.get("blocked_words") or []:
            if word and word.lower() in lowered:
                return f"blocked word: {word}"

        if settings.get("link_filter_enabled"):
            allowed = {d.lower() for d in (settings.get("allowed_domains") or [])}
            for domain in _extract_link_domains(text):
                if domain not in allowed:
                    return f"disallowed link: {domain}"

        return None

    async def _record_violation(self, broadcaster_id: str, offender_id: str) -> int:
        default_timeout = 10
        if not self.internal_secret:
            return default_timeout

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f"{BACKEND_BASE_URL}/api/bot/automod-violation",
                        headers={"X-Bot-Secret": self.internal_secret},
                        json={"broadcaster_twitch_id": broadcaster_id, "offender_twitch_id": offender_id},
                        timeout=aiohttp.ClientTimeout(total=8),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data.get("timeout_seconds", default_timeout)
        except Exception as e:
            LOGGER.error("❌ Fehler beim Erfassen des Automod-Verstoßes: %s", e)
            return default_timeout
