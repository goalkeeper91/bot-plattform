"""
Bot Announcer Utility
Handles sending announcements to Twitch chat channels
"""

import logging

LOGGER = logging.getLogger("BotAnnouncer")


class BotAnnouncer:
    """
    Utility class for sending announcements to Twitch chat
    """

    def __init__(self, bot):
        self.bot = bot

    async def announce(self, message: str, channel_id: str = None):
        """
        Announce a message in Twitch chat

        Args:
            message: Message to send
            channel_id: Optional specific Twitch user ID (channel owner)
                       If None, broadcasts to all subscribed channels
        """
        LOGGER.info("📢 Announcing: %s (channel: %s)", message, channel_id or "ALL")

        if channel_id:
            await self._send_to_channel(message, channel_id)
        else:
            await self._broadcast_to_all(message)

    async def _send_to_channel(self, message: str, channel_id: str):
        """Send message to a specific channel"""
        try:
            async with self.bot.db.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT u.username
                    FROM users u
                    WHERE u.twitch_id = $1
                """, channel_id)

            if not row:
                LOGGER.warning("⚠️ Channel not found in database: %s", channel_id)
                return

            username = row["username"]

            # Find channel in connected_channels
            for ch in self.bot.connected_channels:
                if ch.name.lower() == username.lower():
                    await ch.send(message)
                    LOGGER.info("✅ Message sent to channel: %s", username)
                    return

            LOGGER.warning("⚠️ Channel not connected: %s", username)

        except Exception as e:
            LOGGER.error("❌ Error sending to channel %s: %s", channel_id, e, exc_info=True)

    async def _broadcast_to_all(self, message: str):
        """Broadcast message to all subscribed channels"""
        channels_sent = 0

        for ch_id in self.bot._subscribed_channels:
            try:
                async with self.bot.db.acquire() as conn:
                    row = await conn.fetchrow("""
                        SELECT u.username
                        FROM users u
                        WHERE u.twitch_id = $1
                    """, ch_id)

                if not row:
                    continue

                username = row["username"]

                # Find channel in connected_channels
                for ch in self.bot.connected_channels:
                    if ch.name.lower() == username.lower():
                        await ch.send(message)
                        channels_sent += 1
                        break

            except Exception as e:
                LOGGER.error("❌ Error sending to channel %s: %s", ch_id, e)

        LOGGER.info("✅ Message sent to %d channels", channels_sent)