"""
Money Flow Indicators Pipeline.

Calculates MFI, CMF, OBV, and A/D Line from existing OHLCV data.
Updates market_bar_daily table with calculated indicators.
"""

import argparse
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def calculate_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Money Flow Index (MFI).
    
    MFI is a volume-weighted RSI that ranges from 0-100.
    > 80 = Overbought, < 20 = Oversold
    """
    # Typical Price
    typical_price = (df['high'] + df['low'] + df['close']) / 3
    
    # Raw Money Flow
    money_flow = typical_price * df['volume']
    
    # Positive and Negative Money Flow
    positive_flow = money_flow.where(typical_price > typical_price.shift(1), 0)
    negative_flow = money_flow.where(typical_price < typical_price.shift(1), 0)
    
    # Money Ratio
    positive_mf = positive_flow.rolling(window=period).sum()
    negative_mf = negative_flow.rolling(window=period).sum()
    
    # Avoid division by zero
    money_ratio = positive_mf / negative_mf.replace(0, 1e-10)
    
    # MFI
    mfi = 100 - (100 / (1 + money_ratio))
    
    return mfi


def calculate_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Calculate Chaikin Money Flow (CMF).
    
    CMF measures buying/selling pressure over a period.
    > 0 = Buying pressure, < 0 = Selling pressure
    Strong signals: |CMF| > 0.25
    """
    # Money Flow Multiplier
    # Check for zero range (high == low)
    price_range = df['high'] - df['low']
    price_range = price_range.replace(0, 1e-10)  # Avoid division by zero
    
    mf_multiplier = ((df['close'] - df['low']) - (df['high'] - df['close'])) / price_range
    
    # Money Flow Volume
    mf_volume = mf_multiplier * df['volume']
    
    # CMF
    cmf = mf_volume.rolling(window=period).sum() / df['volume'].rolling(window=period).sum()
    
    return cmf


def calculate_obv(df: pd.DataFrame) -> pd.Series:
    """
    Calculate On-Balance Volume (OBV).
    
    Cumulative indicator that adds volume on up days and subtracts on down days.
    Rising OBV = Accumulation, Falling OBV = Distribution
    """
    # Direction of price change
    direction = np.sign(df['close'].diff())
    
    # OBV
    obv = (direction * df['volume']).fillna(0).cumsum()
    
    return obv


def calculate_ad_line(df: pd.DataFrame) -> pd.Series:
    """
    Calculate Accumulation/Distribution Line.
    
    Volume-weighted indicator using intraday price position.
    Similar to OBV but uses close location value (CLV).
    """
    # Close Location Value (CLV)
    price_range = df['high'] - df['low']
    price_range = price_range.replace(0, 1e-10)
    
    clv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / price_range
    
    # A/D Line
    ad_line = (clv * df['volume']).fillna(0).cumsum()
    
    return ad_line


def calculate_money_flow_for_symbol(conn, symbol: str, lookback_days: int = 180) -> int:
    """
    Calculate money flow indicators for a single symbol.
    
    Returns: Number of rows updated
    """
    # Fetch historical data
    query = """
        SELECT date, open, high, low, close, volume
        FROM market_bar_daily
        WHERE symbol = %s
          AND date >= %s
        ORDER BY date ASC
    """
    
    cutoff_date = datetime.now() - timedelta(days=lookback_days)
    
    with conn.cursor() as cur:
        cur.execute(query, (symbol, cutoff_date))
        rows = cur.fetchall()
        
        if len(rows) < 30:  # Need minimum data for calculations
            logger.debug(f"{symbol}: Insufficient data ({len(rows)} rows)")
            return 0
        
        # Convert to DataFrame
        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        
        # Convert to numeric (handle any string types)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Calculate indicators
        df['mfi_14'] = calculate_mfi(df, period=14)
        df['cmf_20'] = calculate_cmf(df, period=20)
        df['obv'] = calculate_obv(df)
        df['ad_line'] = calculate_ad_line(df)
        
        # Update database (only rows with calculated values)
        update_query = """
            UPDATE market_bar_daily
            SET mfi_14 = %s,
                cmf_20 = %s,
                obv = %s,
                ad_line = %s
            WHERE symbol = %s AND date = %s
        """
        
        updates = []
        for _, row in df.iterrows():
            if pd.notna(row['mfi_14']):  # Only update if MFI is calculated
                updates.append((
                    float(row['mfi_14']) if pd.notna(row['mfi_14']) else None,
                    float(row['cmf_20']) if pd.notna(row['cmf_20']) else None,
                    int(row['obv']) if pd.notna(row['obv']) else None,
                    int(row['ad_line']) if pd.notna(row['ad_line']) else None,
                    symbol,
                    row['date']
                ))
        
        if updates:
            cur.executemany(update_query, updates)
            conn.commit()
            logger.info(f"{symbol}: Updated {len(updates)} rows with money flow indicators")
            return len(updates)
        
        return 0


def run(limit: int | None = None, lookback_days: int = 180) -> None:
    """
    Calculate money flow indicators for all active symbols.
    
    Args:
        limit: Limit number of symbols to process (for testing)
        lookback_days: Number of days of history to recalculate
    """
    conn = get_connection()
    
    try:
        # Get active symbols
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol
                FROM market_bar_daily
                WHERE date >= NOW() - INTERVAL '30 days'
                ORDER BY symbol
            """ + (f" LIMIT {limit}" if limit else ""))
            
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"Calculating money flow for {len(symbols)} symbols (lookback={lookback_days} days)")
        
        total_updated = 0
        for i, symbol in enumerate(symbols, 1):
            try:
                updated = calculate_money_flow_for_symbol(conn, symbol, lookback_days)
                total_updated += updated
                
                if i % 50 == 0:
                    logger.info(f"Progress: {i}/{len(symbols)} symbols processed ({total_updated} rows updated)")
            
            except Exception as e:
                logger.error(f"{symbol}: Failed to calculate money flow - {e}")
                continue
        
        logger.info(f"✅ Money flow calculation complete: {total_updated} total rows updated across {len(symbols)} symbols")
    
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate money flow indicators")
    parser.add_argument("--limit", type=int, help="Limit number of symbols")
    parser.add_argument("--lookback", type=int, default=180, help="Days of history to recalculate")
    
    args = parser.parse_args()
    run(limit=args.limit, lookback_days=args.lookback)
