"""
Backfill options data from DoltHub volatility_history table.

Uses pre-aggregated IV data from volatility_history table which contains
daily IV snapshots (current, week ago, month ago, year high/low).

Usage:
    python backfill_options_volatility.py --start-date 2025-11-01 --end-date 2026-01-10
"""

import argparse
import subprocess
import logging
from datetime import datetime, timedelta
import sys

sys.path.insert(0, '/Users/pan/Documents/Github/Eiqora')

from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DOLT_DB_PATH = "/Users/pan/Documents/Github/Eiqora/options"


def query_dolt(sql: str) -> list:
    """Execute SQL query on Dolt database."""
    cmd = ["dolt", "sql", "-q", sql, "-r", "csv"]
    result = subprocess.run(
        cmd,
        cwd=DOLT_DB_PATH,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise Exception(f"Dolt query failed: {result.stderr}")
    
    # Parse CSV output (skip header)
    lines = result.stdout.strip().split('\n')
    if len(lines) < 2:  # Header + data
        return []
    
    rows = []
    for line in lines[1:]:  # Skip header
        if line.strip():
            cells = line.split(',')
            rows.append(cells)
    
    return rows


def get_volatility_data(symbol: str, date: str) -> dict | None:
    """Get volatility data for a symbol on a specific date."""
    
    query = f"""
    SELECT iv_current, hv_current
    FROM volatility_history
    WHERE act_symbol = '{symbol}'
      AND date = '{date}'
    """
    
    try:
        rows = query_dolt(query)
        
        if not rows or not rows[0][0]:
            return None
        
        iv_current = float(rows[0][0]) if rows[0][0] and rows[0][0] != 'NULL' else None
        hv_current = float(rows[0][1]) if rows[0][1] and rows[0][1] != 'NULL' else None
        
        if iv_current is None:
            return None
        
        return {
            'symbol': symbol,
            'date': date,
            'atm_iv': iv_current,
            'realized_vol': hv_current
        }
    
    except Exception as e:
        logger.debug(f"Error fetching volatility for {symbol} on {date}: {e}")
        return None


def backfill_volatility(start_date: str, end_date: str, symbols: list[str]) -> None:
    """Backfill volatility data for date range."""
    
    logger.info(f"Backfilling volatility from {start_date} to {end_date}")
    logger.info(f"Symbols: {len(symbols)}")
    
    conn = get_connection()
    
    # Generate date range
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    current = start
    dates = []
    while current <= end:
        # Skip weekends
        if current.weekday() < 5:
            dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    
    logger.info(f"Processing {len(dates)} trading days")
    
    total_inserted = 0
    
    for date in dates:
        day_count = 0
        logger.info(f"\nProcessing {date}...")
        
        for symbol in symbols:
            vol_data = get_volatility_data(symbol, date)
            
            if not vol_data:
                continue
            
            # Update or insert to database
            with conn.cursor() as cur:
                try:
                    cur.execute("""
                        INSERT INTO options_daily_summary (
                            symbol, date, atm_iv
                        ) VALUES (%s, %s, %s)
                        ON CONFLICT (symbol, date) DO UPDATE SET
                            atm_iv = EXCLUDED.atm_iv,
                            collected_at = NOW()
                    """, (
                        vol_data['symbol'], vol_data['date'],
                        vol_data['atm_iv']
                    ))
                    conn.commit()
                    day_count += 1
                    total_inserted += 1
                    
                    iv_str = f"{vol_data['atm_iv']*100:.1f}%" if vol_data['atm_iv'] else "N/A"
                    logger.info(f"  ✓ {symbol}: IV={iv_str}")
                
                except Exception as e:
                    logger.error(f"  ✗ {symbol}: Database update failed - {e}")
        
        logger.info(f"  {day_count} symbols updated for {date}")
    
    conn.close()
    
    logger.info(f"\n✅ Backfill complete: {total_inserted} records inserted/updated")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    # Get universe symbols
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM universe_member ORDER BY symbol")
        symbols = [row[0] for row in cur.fetchall()]
    conn.close()
    
backfill_volatility(args.start_date, args.end_date, symbols)
