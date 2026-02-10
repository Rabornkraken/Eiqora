#!/usr/bin/env python3
"""
Backfill Swing Trading Indicators.

Calculates and persists missing swing indicators to market_bar_daily:
- ibs, williams_r_2, rsi_2, cumulative_rsi2
- close_n_day_low_7, close_n_day_high_7
- td_setup_buy_count

Only processes rows that are missing these indicators.

Usage:
    python scripts/backfill_swing_indicators.py
    python scripts/backfill_swing_indicators.py --symbol AAPL
    python scripts/backfill_swing_indicators.py --start-date 2020-01-01
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Indicator calculations (duplicated from pipeline for standalone use)
# ---------------------------------------------------------------------------

def calculate_ibs(df: pd.DataFrame) -> pd.Series:
    """Internal Bar Strength: (close - low) / (high - low)."""
    hl_range = df['high'] - df['low']
    return (df['close'] - df['low']) / hl_range.replace(0, 1e-10)


def calculate_williams_r(df: pd.DataFrame, period: int = 2) -> pd.Series:
    """Williams %R with given lookback period."""
    hh = df['high'].rolling(window=period).max()
    ll = df['low'].rolling(window=period).min()
    return ((hh - df['close']) / (hh - ll).replace(0, 1e-10)) * -100


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index."""
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calculate_rsi_2(df: pd.DataFrame) -> pd.Series:
    """RSI with period=2."""
    return calculate_rsi(df, period=2)


def calculate_cumulative_rsi2(df: pd.DataFrame) -> pd.Series:
    """Cumulative RSI(2): rsi2[today] + rsi2[yesterday]."""
    rsi2 = calculate_rsi(df, period=2)
    return rsi2 + rsi2.shift(1)


def calculate_close_n_day_extremes(df: pd.DataFrame, period: int = 7) -> dict:
    """Check if close is at N-day low or high."""
    rolling_min = df['close'].rolling(window=period).min()
    rolling_max = df['close'].rolling(window=period).max()
    return {
        'low': df['close'] <= rolling_min,
        'high': df['close'] >= rolling_max,
    }


def calculate_td_setup_buy_count(df: pd.DataFrame) -> pd.Series:
    """TD Sequential buy setup count (consecutive closes < close[4 bars ago], 0-9)."""
    result = pd.Series(0, index=df.index, dtype='int16')
    close = df['close'].values
    count = 0
    for i in range(4, len(close)):
        if pd.notna(close[i]) and pd.notna(close[i - 4]) and close[i] < close[i - 4]:
            count += 1
            if count > 9:
                count = 9
        else:
            count = 0
        result.iloc[i] = count
    return result


# ---------------------------------------------------------------------------
# Backfill logic
# ---------------------------------------------------------------------------

def backfill_symbol(conn, symbol: str, start_date: str | None = None) -> int:
    """Backfill swing indicators for a single symbol."""
    query = """
        SELECT date, open, high, low, close, volume
        FROM market_bar_daily
        WHERE symbol = %s
    """
    params = [symbol]

    if start_date:
        query += " AND date >= %s"
        params.append(start_date)

    query += " ORDER BY date ASC"

    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()

        if len(rows) < 10:
            logger.debug(f"{symbol}: Insufficient data ({len(rows)} rows)")
            return 0

        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Calculate indicators
        df['ibs'] = calculate_ibs(df)
        df['williams_r_2'] = calculate_williams_r(df, period=2)
        df['rsi_2'] = calculate_rsi_2(df)
        df['cumulative_rsi2'] = calculate_cumulative_rsi2(df)

        extremes = calculate_close_n_day_extremes(df, period=7)
        df['close_n_day_low_7'] = extremes['low']
        df['close_n_day_high_7'] = extremes['high']

        df['td_setup_buy_count'] = calculate_td_setup_buy_count(df)

        # Build updates
        update_query = """
            UPDATE market_bar_daily
            SET ibs = %s,
                williams_r_2 = %s,
                rsi_2 = %s,
                cumulative_rsi2 = %s,
                close_n_day_low_7 = %s,
                close_n_day_high_7 = %s,
                td_setup_buy_count = %s
            WHERE symbol = %s AND date = %s
        """

        updates = []
        for _, row in df.iterrows():
            if pd.notna(row['ibs']):
                updates.append((
                    float(row['ibs']),
                    float(row['williams_r_2']) if pd.notna(row['williams_r_2']) else None,
                    float(row['rsi_2']) if pd.notna(row['rsi_2']) else None,
                    float(row['cumulative_rsi2']) if pd.notna(row['cumulative_rsi2']) else None,
                    bool(row['close_n_day_low_7']) if pd.notna(row['close_n_day_low_7']) else None,
                    bool(row['close_n_day_high_7']) if pd.notna(row['close_n_day_high_7']) else None,
                    int(row['td_setup_buy_count']) if pd.notna(row['td_setup_buy_count']) else None,
                    symbol,
                    row['date'],
                ))

        if updates:
            cur.executemany(update_query, updates)
            conn.commit()
            return len(updates)

        return 0


def get_symbols_needing_backfill(conn) -> list[str]:
    """Get symbols that have rows missing swing indicators."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT symbol
            FROM market_bar_daily
            WHERE ibs IS NULL
            ORDER BY symbol
        """)
        return [row[0] for row in cur.fetchall()]


def run(symbol: str | None = None, start_date: str | None = None) -> None:
    """Run backfill for all symbols or a specific one."""
    conn = get_connection()

    try:
        if symbol:
            symbols = [symbol]
        else:
            symbols = get_symbols_needing_backfill(conn)

        if not symbols:
            logger.info("No symbols need backfill - all swing indicators populated!")
            return

        logger.info(f"Backfilling {len(symbols)} symbols...")
        logger.info("Indicators: ibs, williams_r_2, rsi_2, cumulative_rsi2, "
                     "close_n_day_low_7, close_n_day_high_7, td_setup_buy_count")

        total_updated = 0
        for i, sym in enumerate(symbols, 1):
            try:
                updated = backfill_symbol(conn, sym, start_date)
                total_updated += updated

                if i % 10 == 0 or i == len(symbols):
                    logger.info(f"Progress: {i}/{len(symbols)} symbols ({total_updated:,} rows updated)")

            except Exception as e:
                logger.error(f"{sym}: Failed - {e}")
                continue

        logger.info(f"Complete! {total_updated:,} rows updated across {len(symbols)} symbols")

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill swing trading indicators")
    parser.add_argument("--symbol", type=str, help="Process single symbol only")
    parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()

    run(symbol=args.symbol, start_date=args.start_date)


if __name__ == "__main__":
    main()
