"""
Analyst Ratings Pipeline.
Fetches upgrades, downgrades, and price target changes from Yahoo Finance.
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
import pandas as pd
import yfinance as yf

from data_collection.common.db import notify_ingest
from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)
NOTIFY_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")


def get_active_symbols(conn, limit: int | None = None) -> list[str]:
    """Get active symbols from universe."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT symbol FROM universe_member WHERE active = true ORDER BY symbol")
        symbols = [row[0] for row in cursor.fetchall()]
    return symbols[:limit] if limit else symbols


def _parse_row(symbol: str, date_idx: datetime, row: pd.Series) -> dict | None:
    """Parse a single row from yfinance dataframe."""
    try:
        # Convert date to timezone-aware if naive
        if date_idx.tzinfo is None:
            rating_date = date_idx.replace(tzinfo=timezone.utc)
        else:
            rating_date = date_idx

        # Extract fields safely
        firm = str(row.get('Firm', '')).strip()
        if not firm:
            return None

        # Clean strings
        action = str(row.get('Action', '')).strip().lower()
        if action == 'nan': action = None
        
        to_grade = str(row.get('ToGrade', '')).strip()
        if to_grade == 'nan': to_grade = None
        
        from_grade = str(row.get('FromGrade', '')).strip()
        if from_grade == 'nan': from_grade = None
        
        pt_action = str(row.get('priceTargetAction', '')).strip()
        if pt_action == 'nan': pt_action = None
        
        # Parse Price Target
        current_pt = row.get('currentPriceTarget')
        price_target = float(current_pt) if pd.notnull(current_pt) else None
        
        return {
            'symbol': symbol,
            'firm': firm,
            'rating_date': rating_date,
            'action': action,
            'to_grade': to_grade,
            'from_grade': from_grade,
            'price_target': price_target,
            'price_target_action': pt_action,
            'source': 'yfinance'
        }
    except Exception as e:
        logger.debug(f"Error parsing row for {symbol}: {e}")
        return None


def fetch_ratings(symbol: str, lookback_days: int = 7) -> list[dict]:
    """Fetch recent ratings for a symbol."""
    try:
        ticker = yf.Ticker(symbol)
        upgrades = ticker.upgrades_downgrades
        
        if upgrades is None or upgrades.empty:
            return []
            
        # Filter by date
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        # Ensure index is datetime and localized
        if upgrades.index.tz is None:
            upgrades.index = upgrades.index.tz_localize('UTC')
        
        recent = upgrades[upgrades.index >= cutoff]
        
        results = []
        for date_idx, row in recent.iterrows():
            parsed = _parse_row(symbol, date_idx, row)
            if parsed:
                results.append(parsed)
                
        return results
        
    except Exception as e:
        logger.warning(f"Failed to fetch ratings for {symbol}: {e}")
        return []


def upsert_ratings(conn, ratings: list[dict]) -> int:
    """Insert ratings into database, skipping duplicates."""
    if not ratings:
        return 0
        
    inserted = 0
    with conn.cursor() as cursor:
        for r in ratings:
            try:
                cursor.execute("""
                    INSERT INTO analyst_rating 
                    (symbol, firm, rating_date, action, to_grade, from_grade, 
                     price_target, price_target_action, source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, firm, rating_date) DO NOTHING
                """, (
                    r['symbol'], r['firm'], r['rating_date'], r['action'],
                    r['to_grade'], r['from_grade'], r['price_target'],
                    r['price_target_action'], r['source']
                ))
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.error(f"Error inserting rating for {r['symbol']}: {e}")
                
    conn.commit()
    return inserted


def run(limit: int | None = None, days: int = 7) -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    conn = get_connection()
    try:
        symbols = get_active_symbols(conn, limit)
        logger.info(f"Fetching analyst ratings for {len(symbols)} symbols (last {days} days)")
        
        total_new = 0
        
        for i, symbol in enumerate(symbols):
            ratings = fetch_ratings(symbol, lookback_days=days)
            if ratings:
                new_count = upsert_ratings(conn, ratings)
                total_new += new_count
                if new_count > 0:
                    logger.info(f"{symbol}: Inserted {new_count} new ratings")
            
            # Progress log every 20
            if (i + 1) % 20 == 0:
                logger.info(f"Processed {i + 1}/{len(symbols)} symbols")

        logger.info(f"Run complete. Total new ratings: {total_new}")
        
        if total_new > 0:
            notify_ingest(
                conn,
                NOTIFY_CHANNEL,
                {
                    "source": "analyst_rating",
                    "new_count": total_new,
                }
            )
            conn.commit()

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
    parser.add_argument("--limit", type=int, help="Limit number of symbols")
    parser.add_argument("--days", type=int, default=7, help="Lookback days")
    args = parser.parse_args()
    
    run(limit=args.limit, days=args.days)
