"""
Hourly OHLCV data collection pipeline using yfinance.
For backtest purposes - collects hourly bars.
"""

import argparse
import logging
import os
from datetime import date, datetime, timedelta

import psycopg
import yfinance as yf

logger = logging.getLogger(__name__)


def get_db_url() -> str:
    """Get database URL from environment."""
    url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    return url.replace("postgresql+psycopg://", "postgresql://")


def fetch_hourly_bars(ticker: str, start_date: date, end_date: date) -> list[dict]:
    """Fetch hourly bars from yfinance."""
    logger.info(f"Fetching hourly data for {ticker}: {start_date} to {end_date}")
    
    try:
        t = yf.Ticker(ticker)
        
        # yfinance only allows ~60 days of hourly data at a time
        # Need to fetch in chunks if period is longer
        all_bars = []
        current_start = start_date
        
        while current_start < end_date:
            chunk_end = min(current_start + timedelta(days=59), end_date)
            
            hist = t.history(
                start=current_start.isoformat(),
                end=(chunk_end + timedelta(days=1)).isoformat(),
                interval="1h"
            )
            
            for idx, row in hist.iterrows():
                bar = {
                    'symbol': ticker,
                    'datetime': idx.to_pydatetime(),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': int(row['Volume']) if row['Volume'] else 0,
                }
                all_bars.append(bar)
            
            current_start = chunk_end + timedelta(days=1)
        
        logger.info(f"  {ticker}: {len(all_bars)} hourly bars")
        return all_bars
        
    except Exception as e:
        logger.error(f"Error fetching {ticker}: {e}")
        return []


def upsert_bars(bars: list[dict]) -> int:
    """Insert or update hourly bars in database."""
    if not bars:
        return 0
    
    db_url = get_db_url()
    with psycopg.connect(db_url) as conn:
        inserted = 0
        with conn.cursor() as cursor:
            for bar in bars:
                try:
                    cursor.execute("""
                        INSERT INTO market_bar_hourly 
                            (symbol, datetime, open, high, low, close, volume, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'yfinance')
                        ON CONFLICT (symbol, datetime) 
                        DO UPDATE SET 
                            open = EXCLUDED.open,
                            high = EXCLUDED.high,
                            low = EXCLUDED.low,
                            close = EXCLUDED.close,
                            volume = EXCLUDED.volume
                    """, (
                        bar['symbol'],
                        bar['datetime'],
                        bar['open'],
                        bar['high'],
                        bar['low'],
                        bar['close'],
                        bar['volume'],
                    ))
                    inserted += 1
                except Exception as e:
                    logger.error(f"Error inserting bar: {e}")
        conn.commit()
        return inserted


def run(
    tickers: list[str],
    start_date: date,
    end_date: date,
):
    """Main pipeline entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s'
    )
    
    logger.info(f"Collecting hourly bars for {len(tickers)} tickers")
    logger.info(f"Period: {start_date} to {end_date}")
    
    total_inserted = 0
    
    for ticker in tickers:
        bars = fetch_hourly_bars(ticker, start_date, end_date)
        inserted = upsert_bars(bars)
        total_inserted += inserted
        logger.info(f"  {ticker}: inserted {inserted} bars")
    
    logger.info(f"Total: {total_inserted} hourly bars collected")
    return total_inserted


def main():
    parser = argparse.ArgumentParser(description="Hourly OHLCV data collection")
    parser.add_argument("--tickers", nargs="+", required=True, help="Tickers to collect")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    run(
        tickers=args.tickers,
        start_date=date.fromisoformat(args.start),
        end_date=date.fromisoformat(args.end),
    )


if __name__ == "__main__":
    main()
