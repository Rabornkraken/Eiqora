"""
Hourly Indicators Pipeline.

Calculates technical indicators from hourly OHLCV data and stores in market_bar_hourly table.
Based on money_flow_indicators.py but optimized for hourly timeframe.

Indicators calculated:
- RSI (14 periods)
- MACD (12, 26, 9)
- VWAP
- CMF (20 periods)
- MFI (14 periods)
- Volume Z-score (20-hour basis)
-Trend direction
- Support/Resistance levels
"""

import argparse
import logging
import os
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from data_collection.db.connection import get_connection
from data_collection.common.db import notify_ingest

NOTIFY_CHANNEL = os.getenv("EIQORA_INGEST_CHANNEL", "eiqora_ingest")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index (RSI).
    
    RSI ranges from 0-100:
    - > 70 = Overbought
    - < 30 = Oversold
    """
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> dict:
    """
    Calculate MACD (Moving Average Convergence Divergence).
    
    Returns dict with:
    - macd_line: MACD line (fast EMA - slow EMA)
    - macd_signal: Signal line (9-period EMA of MACD)
    - macd_histogram: Histogram (MACD - Signal)
    """
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    
    macd_line = ema_fast - ema_slow
    macd_signal = macd_line.ewm(span=signal, adjust=False).mean()
    macd_histogram = macd_line - macd_signal
    
    return {
        'macd_line': macd_line,
        'macd_signal': macd_signal,
        'macd_histogram': macd_histogram,
    }


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calculate Volume-Weighted Average Price (VWAP).
    
    VWAP = Cumulative(Typical Price * Volume) / Cumulative(Volume)
    Resets at start of each session (if needed for multi-day data).
    """
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    
    # For intraday, calculate cumulative VWAP
    # Reset cumsum if we detect a date change (optional)
    df_copy = df.copy()
    df_copy['tp_volume'] = typical_price * df['volume']
    
    vwap = df_copy['tp_volume'].cumsum() / df['volume'].cumsum()
    
    return vwap


def calculate_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Money Flow Index (MFI).
    
    Volume-weighted RSI: 0-100 scale
    - > 80 = Overbought
    - < 20 = Oversold
    """
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    money_flow = typical_price * df['volume']
    
    positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0)
    negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0)
    
    positive_mf = positive_flow.rolling(window=period).sum()
    negative_mf = negative_flow.rolling(window=period).sum()
    
    money_ratio = positive_mf / negative_mf.replace(0, 1e-10)
    mfi = 100 - (100 / (1 + money_ratio))
    
    return mfi


def calculate_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Calculate Chaikin Money Flow (CMF).
    
    Measures buying/selling pressure:
    - > 0 = Buying pressure
    - < 0 = Selling pressure
    - Strong signals: |CMF| > 0.25
    """
    price_range = df['high'] - df['low']
    price_range = price_range.replace(0, 1e-10)
    
    mf_multiplier = ((df['close'] - df['low']) - (df['high'] - df['close'])) / price_range
    mf_volume = mf_multiplier * df['volume']
    
    cmf = mf_volume.rolling(window=period).sum() / df['volume'].rolling(window=period).sum()
    
    return cmf


def calculate_volume_z_score(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Calculate Volume Z-score.
    
    Measures how many standard deviations current volume is from average.
    - > 2.0 = Significantly high volume
    - < -1.0 = Significantly low volume
    """
    volume_mean = df['volume'].rolling(window=period).mean()
    volume_std = df['volume'].rolling(window=period).std()
    
    z_score = (df['volume'] - volume_mean) / volume_std.replace(0, 1e-10)
    
    return z_score


def detect_trend(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Detect trend direction using Hull Moving Average (HMA).
    
    Returns: 'UPTREND', 'DOWNTREND', or 'SIDEWAYS'
    """
    # Simplified: Use EMA slope
    ema = df['close'].ewm(span=period, adjust=False).mean()
    slope = ema.diff(5)  # Look at 5-bar slope
    
    trend = pd.Series('SIDEWAYS', index=df.index)
    trend[slope > df['close'] * 0.002] = 'UPTREND'  # >0.2% slope
    trend[slope < -df['close'] * 0.002] = 'DOWNTREND'  # <-0.2% slope
    
    return trend


def find_support_resistance(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Find recent support and resistance levels.
    
    Uses rolling min/max of lows/highs.
    """
    support = df['low'].rolling(window=lookback).min()
    resistance = df['high'].rolling(window=lookback).max()
    
    return {
        'support': support,
        'resistance': resistance,
    }


def calculate_hourly_indicators_for_symbol(conn, symbol: str, lookback_hours: int | None = 48) -> int:
    """
    Calculate hourly indicators for a single symbol.
    
    Args:
        conn: Database connection
        symbol: Stock symbol
        lookback_hours: Hours of history to process (default 48). If None or 0, process all history.
    
    Returns: Number of rows updated
    """
    query = """
        SELECT datetime, open, high, low, close, volume
        FROM market_bar_hourly
        WHERE symbol = %s
    """
    params = [symbol]
    
    if lookback_hours:
        query += " AND datetime >= %s"
        cutoff_time = datetime.now() - timedelta(hours=lookback_hours)
        params.append(cutoff_time)
        
    query += " ORDER BY datetime ASC"
    
    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        
        if len(rows) < 30:  # Need minimum data
            logger.debug(f"{symbol}: Insufficient hourly data ({len(rows)} bars)")
            return 0
        
        # Convert to DataFrame
        df = pd.DataFrame(rows, columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
        
        # Convert to numeric
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Calculate all indicators
        df['rsi_14'] = calculate_rsi(df, period=14)
        
        macd = calculate_macd(df)
        df['macd_line'] = macd['macd_line']
        df['macd_signal'] = macd['macd_signal']
        df['macd_histogram'] = macd['macd_histogram']
        
        df['vwap'] = calculate_vwap(df)
        df['mfi_14'] = calculate_mfi(df, period=14)
        df['cmf_20'] = calculate_cmf(df, period=20)
        df['volume_z_20h'] = calculate_volume_z_score(df, period=20)
        df['trend_direction'] = detect_trend(df, period=20)
        
        levels = find_support_resistance(df, lookback=20)
        df['support_level'] = levels['support']
        df['resistance_level'] = levels['resistance']
        
        # Update database
        update_query = """
            UPDATE market_bar_hourly
            SET rsi_14 = %s,
                macd_line = %s,
                macd_signal = %s,
                macd_histogram = %s,
                vwap = %s,
                mfi_14 = %s,
                cmf_20 = %s,
                volume_z_20h = %s,
                trend_direction = %s,
                support_level = %s,
                resistance_level = %s
            WHERE symbol = %s AND datetime = %s
        """
        
        updates = []
        for _, row in df.iterrows():
            if pd.notna(row['rsi_14']):  # Only update if indicators calculated
                updates.append((
                    float(row['rsi_14']) if pd.notna(row['rsi_14']) else None,
                    float(row['macd_line']) if pd.notna(row['macd_line']) else None,
                    float(row['macd_signal']) if pd.notna(row['macd_signal']) else None,
                    float(row['macd_histogram']) if pd.notna(row['macd_histogram']) else None,
                    float(row['vwap']) if pd.notna(row['vwap']) else None,
                    float(row['mfi_14']) if pd.notna(row['mfi_14']) else None,
                    float(row['cmf_20']) if pd.notna(row['cmf_20']) else None,
                    float(row['volume_z_20h']) if pd.notna(row['volume_z_20h']) else None,
                    row['trend_direction'] if pd.notna(row['trend_direction']) else None,
                    float(row['support_level']) if pd.notna(row['support_level']) else None,
                    float(row['resistance_level']) if pd.notna(row['resistance_level']) else None,
                    symbol,
                    row['datetime']
                ))
        
        if updates:
            cur.executemany(update_query, updates)
            conn.commit()
            logger.info(f"{symbol}: Updated {len(updates)} hourly bars with indicators")
            return len(updates)
        
        return 0


def run(limit: int | None = None, lookback_hours: int = 48) -> None:
    """
    Calculate hourly indicators for all active symbols.
    
    Args:
        limit: Limit number of symbols (for testing)
        lookback_hours: Hours of history to process (default 48)
    """
    conn = get_connection()
    
    try:
        # Get symbols with recent hourly data
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol
                FROM market_bar_hourly
                WHERE datetime >= NOW() - INTERVAL '7 days'
                ORDER BY symbol
            """ + (f" LIMIT {limit}" if limit else ""))
            
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"Calculating hourly indicators for {len(symbols)} symbols (lookback={lookback_hours}h)")
        
        total_updated = 0
        for i, symbol in enumerate(symbols, 1):
            try:
                updated = calculate_hourly_indicators_for_symbol(conn, symbol, lookback_hours)
                total_updated += updated
                
                if i % 10 == 0:
                    logger.info(f"Progress: {i}/{len(symbols)} symbols ({total_updated} bars updated)")
            
            except Exception as e:
                logger.error(f"{symbol}: Failed - {e}")
                continue
        
        logger.info(f"✅ Hourly indicators complete: {total_updated} bars updated across {len(symbols)} symbols")

        if total_updated:
            try:
                notify_ingest(
                    conn,
                    NOTIFY_CHANNEL,
                    {
                        "source": "market_bar_hourly",
                        "latest_at": datetime.utcnow().isoformat(),
                        "total_updated": total_updated,
                    },
                )
                conn.commit()
                logger.info("Sent ingest notification for hourly indicators")
            except Exception as exc:
                logger.warning(f"Failed to notify ingest channel: {exc}")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate hourly technical indicators")
    parser.add_argument("--limit", type=int, help="Limit number of symbols")
    parser.add_argument("--lookback", type=int, default=48, help="Hours of history to process")
    
    args = parser.parse_args()
    run(limit=args.limit, lookback_hours=args.lookback)
