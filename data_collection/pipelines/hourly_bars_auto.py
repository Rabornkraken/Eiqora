"""
Wrapper for hourly_bars pipeline - fetches universe symbols.
Used by scheduler for automatic hourly data collection.
"""

import logging
from datetime import date, datetime, timedelta

from data_collection.pipelines import hourly_bars
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
    """Run hourly bars collection for all universe symbols."""
    # Get universe
    symbols = get_universe_symbols()
    
    if not symbols:
        logger.warning("No symbols in universe, skipping")
        return
    
    # Collect only the most recent hourly bar
    end_date = date.today()
    start_date = end_date - timedelta(days=1)
    
    logger.info(f"Collecting hourly bars: {start_date} to {end_date}")
    
    total = hourly_bars.run(
        tickers=symbols,
        start_date=start_date,
        end_date=end_date,
        latest_only=True,
    )
    
    logger.info(f"✅ Collected {total} hourly bars")


if __name__ == "__main__":
    main()
