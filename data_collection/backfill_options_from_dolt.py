"""
Backfill options data from DoltHub options database.

Calculates daily put/call ratios and IV metrics from raw option chains.

Usage:
    python backfill_options_from_dolt.py --start-date 2025-11-01 --end-date 2026-01-10
"""

import argparse
import subprocess
import logging
from datetime import datetime, timedelta
from collections import defaultdict
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
    cmd = ["dolt", "sql", "-q", sql]
    result = subprocess.run(
        cmd,
        cwd=DOLT_DB_PATH,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise Exception(f"Dolt query failed: {result.stderr}")
    
    # Parse tab-separated output (skip header)
    lines = result.stdout.strip().split('\n')
    if len(lines) < 3:  # Header + separator + data
        return []
    
    # Skip header lines (usually first 3 lines with box drawing)
    data_lines = [l for l in lines if not l.startswith('+') and not l.startswith('|') or '|' in l[1:]]
    
    rows = []
    for line in data_lines[1:]:  # Skip first line (header)
        if line.strip() and not line.startswith('+'):
            # Split by | and clean
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if cells and cells[0]:  # Has data
                rows.append(cells)
    
    return rows


def calculate_options_metrics(symbol: str, date: str) -> dict | None:
    """Calculate options metrics for a symbol on a specific date."""
    
    # Query Dolt for options data
    query = f"""
    SELECT call_put, strike, vol, delta, bid, ask
    FROM option_chain
    WHERE act_symbol = '{symbol}'
      AND date = '{date}'
      AND expiration >= DATE_ADD('{date}', INTERVAL 7 DAY)
      AND expiration <= DATE_ADD('{date}', INTERVAL 45 DAY)
    """
    
    try:
        rows = query_dolt(query)
        
        if not rows:
            return None
        
        calls = []
        puts = []
        
        for row in rows:
            call_put, strike, vol, delta, bid, ask = row
            
            try:
                strike_f = float(strike)
                vol_f = float(vol) if vol and vol != 'NULL' else None
                delta_f = float(delta) if delta and delta != 'NULL' else None
                bid_f = float(bid) if bid and bid != 'NULL' else None
                ask_f = float(ask) if ask and ask != 'NULL' else None
                
                option = {
                    'strike': strike_f,
                    'vol': vol_f,
                    'delta': delta_f,
                    'bid': bid_f,
                    'ask': ask_f
                }
                
                if call_put == 'Call':
                    calls.append(option)
                else:
                    puts.append(option)
            except (ValueError, TypeError):
                continue
        
        if not calls or not puts:
            return None
        
        # Calculate metrics
        num_calls = len(calls)
        num_puts = len(puts)
        pcr = num_puts / num_calls if num_calls > 0 else None
        
        # Find ATM IV (closest to delta=0.5 for calls, -0.5 for puts)
        atm_calls = [c for c in calls if c['vol'] and c['delta'] and 0.4 < c['delta'] < 0.6]
        atm_iv = sum(c['vol'] for c in atm_calls) / len(atm_calls) if atm_calls else None
        
        return {
            'symbol': symbol,
            'date': date,
            'put_call_ratio_volume': pcr,  # Using contract count as proxy
 'put_call_ratio_oi': pcr,
            'total_call_volume': num_calls,
            'total_put_volume': num_puts,
            'atm_iv': atm_iv,
        }
    
    except Exception as e:
        logger.error(f"Error calculating metrics for {symbol} on {date}: {e}")
        return None


def backfill_options(start_date: str, end_date: str, symbols: list[str]) -> None:
    """Backfill options data for date range."""
    
    logger.info(f"Backfilling options from {start_date} to {end_date}")
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
        logger.info(f"\nProcessing {date}...")
        
        for symbol in symbols:
            metrics = calculate_options_metrics(symbol, date)
            
            if not metrics:
                continue
            
            # Insert to database
            with conn.cursor() as cur:
                try:
                    cur.execute("""
                        INSERT INTO options_daily_summary (
                            symbol, date,
                            put_call_ratio_volume, put_call_ratio_oi,
                            total_call_volume, total_put_volume,
                            atm_iv
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, date) DO UPDATE SET
                            put_call_ratio_volume = EXCLUDED.put_call_ratio_volume,
                            atm_iv = EXCLUDED.atm_iv
                    """, (
                        metrics['symbol'], metrics['date'],
                        metrics['put_call_ratio_volume'], metrics['put_call_ratio_oi'],
                        metrics['total_call_volume'], metrics['total_put_volume'],
                        metrics['atm_iv']
                    ))
                    conn.commit()
                    total_inserted += 1
                    
                    pcr_str = f"{metrics['put_call_ratio_volume']:.2f}" if metrics['put_call_ratio_volume'] else "N/A"
                    iv_str = f"{metrics['atm_iv']*100:.1f}%" if metrics['atm_iv'] else "N/A"
                    logger.info(f"  ✓ {symbol}: PCR={pcr_str}, IV={iv_str}")
                
                except Exception as e:
                    logger.error(f"  ✗ {symbol}: Database insert failed - {e}")
    
    conn.close()
    
    logger.info(f"\n✅ Backfill complete: {total_inserted} records inserted")


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
    
    backfill_options(args.start_date, args.end_date, symbols)
