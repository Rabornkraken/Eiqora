"""
Wrapper for hourly_bars pipeline - fetches universe symbols.
Used by scheduler for automatic hourly data collection.
"""

import logging
import os
from datetime import date, timedelta

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


def _get_latest_hourly_bar_date() -> date | None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT MAX(datetime)::date FROM market_bar_hourly")
            row = cursor.fetchone()
            return row[0] if row and row[0] else None


def main():
    """Run hourly bars collection for all universe symbols."""
    symbols = get_universe_symbols()

    if not symbols:
        logger.warning("No symbols in universe, skipping")
        return

    end_date = date.today()
    catchup_days = int(os.getenv("HOURLY_CATCHUP_DAYS", "3"))
    latest_only = True
    start_date = end_date - timedelta(days=1)

    if catchup_days > 0:
        latest_db_date = _get_latest_hourly_bar_date()
        if latest_db_date and latest_db_date < end_date:
            start_date = max(end_date - timedelta(days=catchup_days), latest_db_date)
            latest_only = False
            logger.info(
                "Hourly catch-up enabled: %s to %s (days=%s)",
                start_date,
                end_date,
                catchup_days,
            )
        else:
            logger.info("Hourly catch-up not needed; running latest-only")

    logger.info(f"Collecting hourly bars: {start_date} to {end_date}")

    total = hourly_bars.run(
        tickers=symbols,
        start_date=start_date,
        end_date=end_date,
        latest_only=latest_only,
    )

    logger.info(f"✅ Collected {total} hourly bars")


if __name__ == "__main__":
    main()
