#!/usr/bin/env python3
"""
Backfill full hourly price history and indicators for all universe symbols.
Fetches data from Jan 1, 2024 (approx limit of YFinance hourly data).
"""
import sys
import logging
from datetime import date, datetime, timezone
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.pipelines import hourly_bars
from data_collection.pipelines import hourly_indicators
from data_collection.db.connection import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def get_universe_symbols() -> list[str]:
    """Get active universe symbols from database."""
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT symbol FROM universe_member WHERE active = true")
            symbols = [row[0] for row in cursor.fetchall()]
    return symbols

def main():
    start_date = date(2024, 1, 1)
    end_date = datetime.now(timezone.utc).date()
    
    logger.info(f"Starting full hourly backfill from {start_date} to {end_date}...")
    
    symbols = get_universe_symbols()
    logger.info(f"Processing {len(symbols)} symbols...")
    
    conn = get_connection()
    total_bars = 0
    total_indicators = 0
    
    try:
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] Processing {symbol}...")
            
            # 1. Fetch and Upsert Bars
            # try:
            #     bars = hourly_bars.fetch_hourly_bars(symbol, start_date, end_date, latest_only=False)
            #     inserted = hourly_bars.upsert_bars(bars)
            #     total_bars += inserted
            #     logger.info(f"  Upserted {inserted} bars")
            # except Exception as e:
            #     logger.error(f"  Failed to fetch bars for {symbol}: {e}")
            #     continue
                
            # 2. Calculate Indicators (Full History)
            try:
                updated = hourly_indicators.calculate_hourly_indicators_for_symbol(
                    conn, symbol, lookback_hours=None
                )
                total_indicators += updated
                logger.info(f"  Calculated indicators for {updated} bars")
            except Exception as e:
                logger.error(f"  Failed to calculate indicators for {symbol}: {e}")
                
    finally:
        conn.close()
        
    logger.info("=" * 60)
    logger.info("✅ Full hourly backfill complete.")
    logger.info(f"Total Bars Processed: {total_bars}")
    logger.info(f"Total Indicators Calculated: {total_indicators}")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
