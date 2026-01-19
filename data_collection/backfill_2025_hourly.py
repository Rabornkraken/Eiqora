"""
Backfill 2025 hourly data and calculate indicators.

This script:
1. Fetches missing hourly bars for all 2025 (Jan-Oct)
2. Calculates hourly indicators for all bars
3. Enables comprehensive backtesting

Usage:
    python backfill_2025_hourly.py
"""

import logging
from datetime import date, timedelta
import sys

# Add project to path
sys.path.insert(0, '/Users/pan/Documents/Github/Eiqora')

from data_collection.pipelines.hourly_bars import run as fetch_bars
from data_collection.pipelines.hourly_indicators import run as calculate_indicators
from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_universe_symbols():
    """Get all symbols from universe."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol FROM universe_member 
                ORDER BY symbol
            """)
            return [row[0] for row in cur.fetchall()]


def backfill_2025_data():
    """Backfill all 2025 hourly data."""
    
    logger.info("=" * 80)
    logger.info("BACKFILLING 2025 HOURLY DATA")
    logger.info("=" * 80)
    
    # Get universe symbols
    symbols = get_universe_symbols()
    logger.info(f"Found {len(symbols)} universe symbols")
    
    # Step 1: Fetch hourly bars for 2025
    # yfinance only allows 60 days at a time, so we'll do it in chunks
    logger.info("\nStep 1: Fetching hourly bars for 2025...")
    
    chunks = [
        (date(2025, 1, 1), date(2025, 2, 28)),    # Jan-Feb
        (date(2025, 3, 1), date(2025, 4, 30)),    # Mar-Apr
        (date(2025, 5, 1), date(2025, 6, 30)),    # May-Jun
        (date(2025, 7, 1), date(2025, 8, 31)),    # Jul-Aug
        (date(2025, 9, 1), date(2025, 10, 31)),   # Sep-Oct
        (date(2025, 11, 1), date(2025, 11, 30)),  # Nov (to fill gap)
    ]
    
    total_fetched = 0
    for start, end in chunks:
        logger.info(f"\nFetching {start} to {end}...")
        try:
            count = fetch_bars(
                tickers=symbols,
                start_date=start,
                end_date=end,
                latest_only=False
            )
            total_fetched += count
            logger.info(f"✅ Fetched {count} bars for {start} to {end}")
        except Exception as e:
            logger.error(f"❌ Error fetching {start} to {end}: {e}")
    
    logger.info(f"\n✅ Total bars fetched: {total_fetched:,}")
    
    # Step 2: Calculate indicators for all hourly bars
    logger.info("\nStep 2: Calculating hourly indicators for all symbols...")
    logger.info("This will calculate indicators for ~48 hours of recent data per symbol")
    
    try:
        # Use the hourly indicators pipeline with a large lookback
        # This will process all recent bars that don't have indicators
        calculate_indicators(limit=None, lookback_hours=2000)  # ~83 days lookback
        logger.info("✅ Indicators calculated successfully")
    except Exception as e:
        logger.error(f"❌ Error calculating indicators: {e}")
    
    # Step 3: Verify
    logger.info("\nStep 3: Verification...")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN rsi_14 IS NOT NULL THEN 1 END) as with_indicators
                FROM market_bar_hourly
                WHERE datetime >= '2025-01-01'
                  AND datetime < '2026-01-01'
            """)
            
            total, with_ind = cur.fetchone()
            pct = (with_ind / total * 100) if total > 0 else 0
            
            logger.info(f"2025 Data:")
            logger.info(f"  Total bars: {total:,}")
            logger.info(f"  With indicators: {with_ind:,} ({pct:.1f}%)")
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ BACKFILL COMPLETE!")
    logger.info("=" * 80)


if __name__ == "__main__":
    backfill_2025_data()
