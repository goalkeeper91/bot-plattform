"""
Automod: deletes chat messages containing blocked words/phrases, disallowed
links, or generic spam patterns (excessive caps/symbols/emotes, repeated
messages), and issues an escalating timeout on the sender. Mods/broadcaster
are always exempt, plus optionally VIPs, a manually-curated trusted-user
list, and - since Loyalty/Watchtime - viewers who've crossed the channel's
Regulars points threshold. The Go backend merges Regulars into exempt_users
at response time (see GetAllEnabledSettingsForBot), so this file's exemption
check never changed - only reload_settings needed a periodic timer (see
start/stop below) so a newly-crossed threshold doesn't sit stale until the
streamer happens to touch Automod's own settings. Settings are cached in
memory per channel (see reload_settings) so the actual per-message check
never does I/O - only an actual violation (rare) triggers Helix calls + a
small Go-backend call for the escalation counter and violation log.
"""
import asyncio
import logging
import os
import re
import time

import aiohttp

from app.utils.helix_client import HelixClient, HelixInsufficientScopeError

LOGGER = logging.getLogger("AutomodFilter")

BACKEND_BASE_URL = "http://backend-go:8080"

# Fixed thresholds for the generic spam filters - not configurable in v1
# (same simplification as the escalation tiers). The emote count is the one
# exception: emote spam in the right moment (an esports highlight, a hype
# train) can be a legitimate reaction rather than spam, so streamers set
# their own threshold per channel (automod_settings.emote_threshold) - this
# is only the fallback if a cached settings row somehow lacks the field.
CAPS_MIN_LENGTH = 10
CAPS_MIN_RATIO = 0.7
SYMBOL_MIN_LENGTH = 6
SYMBOL_MIN_RATIO = 0.5
DEFAULT_EMOTE_THRESHOLD = 6
REPETITION_MIN_COUNT = 3
REPETITION_WINDOW_SECONDS = 30

# How often settings are re-polled even without a REFRESH_AUTOMOD signal -
# needed because Regulars status can change (a viewer crosses the Loyalty
# points threshold) independently of the streamer ever touching Automod's
# own settings.
RELOAD_INTERVAL_SECONDS = 300

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
_ALNUM_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)


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
        # {f"{channel_id}:{chatter_id}": [(timestamp, normalized_text), ...]}
        # In-memory only - a repetition streak is inherently short-lived,
        # unlike the durable escalation counter kept in Postgres.
        self.recent_messages: dict[str, list[tuple[float, str]]] = {}
        self._reload_task = None

    async def start(self):
        """Startet den periodischen Settings-Reload (siehe RELOAD_INTERVAL_SECONDS)."""
        if not self._reload_task:
            self._reload_task = asyncio.create_task(self._reload_loop())
            LOGGER.info("✅ Automod periodischer Reload gestartet")

    async def stop(self):
        """Stoppt den periodischen Settings-Reload."""
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            LOGGER.info("🛑 Automod periodischer Reload gestoppt")

    async def _reload_loop(self):
        while True:
            try:
                await asyncio.sleep(RELOAD_INTERVAL_SECONDS)
                await self.reload_settings()
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error("❌ Periodischer Automod-Reload fehlgeschlagen: %s", e, exc_info=True)

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

        if settings.get("exempt_vips") and getattr(message.chatter, "vip", False):
            return False

        exempt_users = {u.lower() for u in (settings.get("exempt_users") or [])}
        if message.chatter.name.lower() in exempt_users:
            return False

        reason = self._find_violation(message, settings)
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
        timeout_seconds = await self._record_violation(
            broadcaster_id, offender_id, message.chatter.name, reason, message.text[:300]
        )

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

    def _find_violation(self, message, settings: dict) -> str | None:
        text = message.text
        lowered = text.lower()

        for word in settings.get("blocked_words") or []:
            if word and word.lower() in lowered:
                return f"blocked word: {word}"

        if settings.get("link_filter_enabled"):
            allowed = {d.lower() for d in (settings.get("allowed_domains") or [])}
            for domain in _extract_link_domains(text):
                if domain not in allowed:
                    return f"disallowed link: {domain}"

        if settings.get("caps_filter_enabled") and self._is_caps_spam(text):
            return "excessive caps"

        if settings.get("symbol_filter_enabled") and self._is_symbol_spam(text):
            return "excessive symbols"

        if settings.get("emote_filter_enabled"):
            threshold = settings.get("emote_threshold") or DEFAULT_EMOTE_THRESHOLD
            if len(getattr(message, "emotes", []) or []) > threshold:
                return "emote spam"

        if settings.get("repetition_filter_enabled") and self._is_repetition(message):
            return "message repetition"

        return None

    @staticmethod
    def _is_caps_spam(text: str) -> bool:
        letters = [c for c in text if c.isalpha()]
        if len(text) < CAPS_MIN_LENGTH or len(letters) < CAPS_MIN_LENGTH:
            return False
        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        return upper_ratio >= CAPS_MIN_RATIO

    @staticmethod
    def _is_symbol_spam(text: str) -> bool:
        if len(text) < SYMBOL_MIN_LENGTH:
            return False
        symbol_count = len(_ALNUM_PATTERN.findall(text))
        return (symbol_count / len(text)) >= SYMBOL_MIN_RATIO

    def _is_repetition(self, message) -> bool:
        key = f"{message.broadcaster.id}:{message.chatter.id}"
        normalized = message.text.strip().lower()
        now = time.monotonic()

        history = [
            (ts, txt) for ts, txt in self.recent_messages.get(key, [])
            if now - ts <= REPETITION_WINDOW_SECONDS
        ]
        history.append((now, normalized))
        self.recent_messages[key] = history

        matching = sum(1 for _, txt in history if txt == normalized)
        return matching >= REPETITION_MIN_COUNT

    async def _record_violation(
            self, broadcaster_id: str, offender_id: str, offender_name: str, reason: str, message_excerpt: str
    ) -> int:
        default_timeout = 10
        if not self.internal_secret:
            return default_timeout

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                        f"{BACKEND_BASE_URL}/api/bot/automod-violation",
                        headers={"X-Bot-Secret": self.internal_secret},
                        json={
                            "broadcaster_twitch_id": broadcaster_id,
                            "offender_twitch_id": offender_id,
                            "offender_name": offender_name,
                            "reason": reason,
                            "message_excerpt": message_excerpt,
                        },
                        timeout=aiohttp.ClientTimeout(total=8),
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return data.get("timeout_seconds", default_timeout)
        except Exception as e:
            LOGGER.error("❌ Fehler beim Erfassen des Automod-Verstoßes: %s", e)
            return default_timeout
