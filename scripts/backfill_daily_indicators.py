#!/usr/bin/env python3
"""
Backfill Daily Technical Indicators.

Calculates and persists missing indicators to market_bar_daily:
- stochastic_k, stochastic_d
- support_level, resistance_level
- volume_z_20

Only processes rows that are missing these indicators.

Usage:
    python scripts/backfill_daily_indicators.py
    python scripts/backfill_daily_indicators.py --symbol AAPL
    python scripts/backfill_daily_indicators.py --start-date 2020-01-01
"""

import argparse
import logging
import sys
from datetime import datetime
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


def calculate_stochastic(df: pd.DataFrame, period=14, smooth_k=3) -> dict:
    """Calculate Stochastic Oscillator (%K and %D)."""
    low_min = df['low'].rolling(window=period).min()
    high_max = df['high'].rolling(window=period).max()

    k_percent = 100 * ((df['close'] - low_min) / (high_max - low_min).replace(0, 1e-10))
    d_percent = k_percent.rolling(window=smooth_k).mean()

    return {'k': k_percent, 'd': d_percent}


def calculate_support_resistance(df: pd.DataFrame, period=20) -> dict:
    """Calculate dynamic Support and Resistance levels."""
    support = df['low'].shift(1).rolling(window=period).min()
    resistance = df['high'].shift(1).rolling(window=period).max()

    return {'support': support, 'resistance': resistance}


def calculate_volume_z_score(df: pd.DataFrame, period=20) -> pd.Series:
    """Calculate Volume Z-Score."""
    mean = df['volume'].rolling(window=period).mean()
    std = df['volume'].rolling(window=period).std()
    z_score = (df['volume'] - mean) / std.replace(0, 1e-10)
    return z_score


def backfill_symbol(conn, symbol: str, start_date: str | None = None) -> int:
    """Backfill indicators for a single symbol."""

    # Fetch all historical data for this symbol
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

        if len(rows) < 50:
            logger.debug(f"{symbol}: Insufficient data ({len(rows)} rows)")
            return 0

        # Convert to DataFrame
        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])

        # Convert to numeric
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Calculate indicators
        stoch = calculate_stochastic(df)
        df['stochastic_k'] = stoch['k']
        df['stochastic_d'] = stoch['d']

        sr = calculate_support_resistance(df)
        df['support_level'] = sr['support']
        df['resistance_level'] = sr['resistance']

        df['volume_z_20'] = calculate_volume_z_score(df)

        # Build updates (only for rows with valid indicators)
        update_query = """
            UPDATE market_bar_daily
            SET stochastic_k = %s,
                stochastic_d = %s,
                support_level = %s,
                resistance_level = %s,
                volume_z_20 = %s
            WHERE symbol = %s AND date = %s
        """

        updates = []
        for _, row in df.iterrows():
            # Only update if we have valid values
            if pd.notna(row['stochastic_k']) and pd.notna(row['support_level']):
                updates.append((
                    float(row['stochastic_k']),
                    float(row['stochastic_d']) if pd.notna(row['stochastic_d']) else None,
                    float(row['support_level']),
                    float(row['resistance_level']) if pd.notna(row['resistance_level']) else None,
                    float(row['volume_z_20']) if pd.notna(row['volume_z_20']) else None,
                    symbol,
                    row['date']
                ))

        if updates:
            cur.executemany(update_query, updates)
            conn.commit()
            return len(updates)

        return 0


def get_symbols_needing_backfill(conn) -> list[str]:
    """Get symbols that have rows missing indicators."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT symbol
            FROM market_bar_daily
            WHERE stochastic_k IS NULL
               OR support_level IS NULL
               OR volume_z_20 IS NULL
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
            logger.info("No symbols need backfill - all indicators populated!")
            return

        logger.info(f"Backfilling {len(symbols)} symbols...")
        logger.info("Indicators: stochastic_k/d, support/resistance, volume_z_20")

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
    parser = argparse.ArgumentParser(description="Backfill daily technical indicators")
    parser.add_argument("--symbol", type=str, help="Process single symbol only")
    parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    args = parser.parse_args()

    run(symbol=args.symbol, start_date=args.start_date)


if __name__ == "__main__":
    main()
