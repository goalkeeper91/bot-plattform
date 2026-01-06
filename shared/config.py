import os

# ---------------------------
# Discord
# ---------------------------
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_BOT_CLIENT_ID = os.getenv("DISCORD_BOT_CLIENT_ID")  # optional
DISCORD_BOT_CLIENT_SECRET = os.getenv("DISCORD_BOT_CLIENT_SECRET")  # optional

# ---------------------------
# Twitch
# ---------------------------
TWITCH_CHANNEL = os.getenv("TWITCH_CHANNEL")
TWITCH_BOT_CLIENT_ID = os.getenv("TWITCH_BOT_CLIENT_ID")
TWITCH_BOT_CLIENT_SECRET = os.getenv("TWITCH_BOT_CLIENT_SECRET")
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")

# ---------------------------
# Redis
# ---------------------------
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# ---------------------------
# Database
# ---------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")
