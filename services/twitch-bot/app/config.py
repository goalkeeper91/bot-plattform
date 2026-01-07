import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TwitchConfig:
    client_id: str
    client_secret: str
    bot_id: str
    owner_id: str
    prefix: str = "!"


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


def load_twitch_config() -> TwitchConfig:
    return TwitchConfig(
        client_id=os.environ["TWITCH_BOT_CLIENT_ID"],
        client_secret=os.environ["TWITCH_BOT_CLIENT_SECRET"],
        bot_id=os.environ["TWITCH_BOT_ID"],
        owner_id=os.environ["TWITCH_OWNER_ID"],
    )


def load_db_config() -> DatabaseConfig:
    return DatabaseConfig(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 5432)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
    )
