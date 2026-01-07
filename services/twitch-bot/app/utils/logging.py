import logging
import twitchio

def setup_logging(level=logging.INFO):
    twitchio.utils.setup_logging(level=level)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
