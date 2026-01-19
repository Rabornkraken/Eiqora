"""
Backfill Hourly Indicators.

One-time script to calculate hourly indicators for existing historical data.
Useful after schema migration or for backtesting.

Usage:
    python -m data_collection.migrations.backfill_hourly_indicators --days 60
"""

import argparse
import logging
from datetime import datetime, timedelta

from data_collection.db.connection import get_connection
from data_collection.pipelines.hourly_indicators import calculate_hourly_indicators_for_symbol

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def backfill_hourly_indicators(days_back: int = 60, symbols_limit: int | None = None) -> None:
    """
    Backfill hourly indicators for historical data.
    
    Args:
        days_back: Number of days of history to backfill (default 60)
        symbols_limit: Limit number of symbols (for testing)
    """
    conn = get_connection()
    
    try:
        # Get all symbols with hourly data
        with conn.cursor() as cur:
            cutoff_date = datetime.now() - timedelta(days=days_back)
            
            cur.execute("""
                SELECT DISTINCT symbol
                FROM market_bar_hourly
                WHERE datetime >= %s
                ORDER BY symbol
            """ + (f" LIMIT {symbols_limit}" if symbols_limit else ""), (cutoff_date,))
            
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"=" * 80)
        logger.info(f"BACKFILLING HOURLY INDICATORS")
        logger.info(f"=" * 80)
        logger.info(f"Symbols: {len(symbols)}")
        logger.info(f"Lookback: {days_back} days ({days_back * 24} hours)")
        logger.info(f"Started: {datetime.now()}")
        logger.info(f"=" * 80)
        
        total_updated = 0
        failed = 0
        
        for i, symbol in enumerate(symbols, 1):
            try:
                # Use large lookback to capture all historical data
                lookback_hours = days_back * 24 + 100  # Extra buffer for calculation periods
                
                updated = calculate_hourly_indicators_for_symbol(conn, symbol, lookback_hours)
                total_updated += updated
                
                if updated > 0:
                    logger.info(f"[{i}/{len(symbols)}] {symbol}: ✅ {updated} bars")
                else:
                    logger.debug(f"[{i}/{len(symbols)}] {symbol}: No updates (insufficient data)")
                
                # Progress update every 10 symbols
                if i % 10 == 0:
                    logger.info(f"Progress: {i}/{len(symbols)} symbols | {total_updated} bars updated | {failed} failed")
            
            except Exception as e:
                failed += 1
                logger.error(f"[{i}/{len(symbols)}] {symbol}: ❌ {e}")
                continue
        
        logger.info(f"=" * 80)
        logger.info(f"BACKFILL COMPLETE")
        logger.info(f"=" * 80)
        logger.info(f"Total symbols processed: {len(symbols)}")
        logger.info(f"Total bars updated: {total_updated:,}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Success rate: {((len(symbols) - failed) / len(symbols) * 100):.1f}%")
        logger.info(f"Completed: {datetime.now()}")
        logger.info(f"=" * 80)
    
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill hourly technical indicators")
    parser.add_argument("--days", type=int, default=60, help="Days of history to backfill (default 60)")
    parser.add_argument("--limit", type=int, help="Limit number of symbols (for testing)")
    
    args = parser.parse_args()
    
    backfill_hourly_indicators(days_back=args.days, symbols_limit=args.limit)
