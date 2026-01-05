"""
VIX and Volatility Index Collection Pipeline.
Fetches VIX, VVIX, and other volatility indicators using yfinance.
"""

import asyncio
import logging
from datetime import datetime, timedelta

import yfinance as yf

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)

# Volatility symbols to track
VOL_SYMBOLS = [
    '^VIX',      # CBOE Volatility Index
    '^VVIX',     # VIX of VIX
    '^MOVE',     # Bond volatility (may not work in yfinance)
]


async def upsert_vix_bar(symbol: str, bar_date, open_p, high, low, close, volume) -> bool:
    """Insert VIX bar into market_bar_daily."""
    async with get_connection() as conn:
        try:
            # Use a special symbol format for indices
            db_symbol = symbol.replace('^', 'IDX_')
            
            await conn.execute("""
                INSERT INTO market_bar_daily (symbol, date, open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'yfinance')
                ON CONFLICT (symbol, date) 
                DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high, 
                              low = EXCLUDED.low, close = EXCLUDED.close
            """, db_symbol, bar_date, open_p, high, low, close, int(volume) if volume else 0)
            return True
        except Exception as e:
            logger.error(f"Error inserting {symbol} bar: {e}")
            return False


async def fetch_and_store_vix(symbol: str, days: int = 30) -> int:
    """Fetch VIX data and store in database."""
    logger.info(f"Fetching {symbol}...")
    
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=f"{days}d")
        
        if hist.empty:
            logger.warning(f"No data for {symbol}")
            return 0
        
        stored = 0
        for idx, row in hist.iterrows():
            bar_date = idx.date()
            success = await upsert_vix_bar(
                symbol=symbol,
                bar_date=bar_date,
                open_p=float(row['Open']),
                high=float(row['High']),
                low=float(row['Low']),
                close=float(row['Close']),
                volume=row.get('Volume', 0),
            )
            if success:
                stored += 1
        
        logger.info(f"Stored {stored} bars for {symbol}")
        return stored
        
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return 0


async def run(days: int = 30):
    """Main pipeline entry point."""
    logger.info(f"Fetching volatility indices (last {days} days)...")
    
    total = 0
    for symbol in VOL_SYMBOLS:
        count = await fetch_and_store_vix(symbol, days)
        total += count
    
    logger.info(f"Total bars stored: {total}")
    
    # Log latest values
    async with get_connection() as conn:
        latest = await conn.fetch("""
            SELECT symbol, date, close
            FROM market_bar_daily
            WHERE symbol LIKE 'IDX_%'
            ORDER BY date DESC
            LIMIT 5
        """)
        logger.info("Latest values:")
        for r in latest:
            logger.info(f"  {r['symbol']} ({r['date']}): {r['close']:.2f}")


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    asyncio.run(run())


if __name__ == '__main__':
    main()
