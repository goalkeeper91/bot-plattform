import asyncpg
from app.config import DatabaseConfig


async def create_db_pool(config: DatabaseConfig) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        min_size=1,
        max_size=10,
    )
