"""
Wrapper for minute_bars pipeline - fetches universe symbols.
Used by scheduler for automatic 1-minute bar collection during market hours.
"""

import logging

from data_collection.pipelines import minute_bars
from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def get_universe_symbols() -> list[str]:
    """Get active universe symbols from database."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT symbol FROM universe_member WHERE active = true")
            symbols = [row[0] for row in cursor.fetchall()]
    logger.info(f"Got {len(symbols)} symbols from universe")
    return symbols


def main():
    """Run 1-minute bars collection for all universe symbols."""
    symbols = get_universe_symbols()

    if not symbols:
        logger.warning("No symbols in universe, skipping")
        return

    total = minute_bars.run(tickers=symbols)

    logger.info(f"Collected {total} 1-min bars")


if __name__ == "__main__":
    main()
