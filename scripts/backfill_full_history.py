#!/usr/bin/env python3
"""
Backfill full daily price history for all universe symbols.
"""
import sys
import logging
from datetime import date, datetime, timezone
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.pipelines.stooq_daily import backfill

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    end_date = datetime.now(timezone.utc).date()
    start_date = date(1900, 1, 1)
    
    print(f"Starting full history backfill from {start_date} to {end_date}...")
    
    try:
        backfill(start=start_date, end=end_date)
        print("✅ Full history backfill complete.")
    except Exception as e:
        print(f"❌ Backfill failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
