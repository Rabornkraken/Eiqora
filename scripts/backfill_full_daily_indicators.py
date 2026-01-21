#!/usr/bin/env python3
"""
Backfill daily technical indicators for the full history of all symbols.
"""
import sys
import logging
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.pipelines import daily_technical_indicators
from data_collection.db.connection import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def main():
    logger.info("Starting full daily indicator backfill...")
    
    conn = get_connection()
    try:
        # Get all symbols with daily data
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT symbol FROM market_bar_daily ORDER BY symbol")
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"Processing {len(symbols)} symbols...")
        
        total_updated = 0
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] Calculating indicators for {symbol}...")
            try:
                # Pass lookback_days=None to process full history
                updated = daily_technical_indicators.calculate_all_indicators_for_symbol(
                    conn, symbol, lookback_days=None
                )
                total_updated += updated
            except Exception as e:
                logger.error(f"Failed to calculate indicators for {symbol}: {e}")
                
        logger.info("=" * 60)
        logger.info("✅ Full daily indicator backfill complete.")
        logger.info(f"Total Rows Updated: {total_updated}")
        logger.info("=" * 60)
        
    finally:
        conn.close()

if __name__ == "__main__":
    main()
