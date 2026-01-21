"""
Comprehensive Daily Technical Indicators Pipeline.

Calculates and persists ALL technical indicators to market_bar_daily:
- RSI (14, 20)
- MACD (12, 26, 9)
- Bollinger Bands (20, 2 std)
- ATR (14)
- ADX (14)
- MFI, CMF, OBV, A/D Line

Usage:
    python daily_technical_indicators.py --lookback 365
"""

import argparse
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, '/Users/pan/Documents/Github/Eiqora')

from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index."""
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta <0, 0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> dict:
    """Calculate MACD indicators."""
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    return {
        'macd': macd,
        'macd_signal': macd_signal,
        'macd_hist': macd_hist
    }


def calculate_bollinger_bands(df: pd.DataFrame, period=20, std_dev=2) -> dict:
    """Calculate Bollinger Bands."""
    middle = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    width = (upper - lower) / middle
    return {
        'bb_upper': upper,
        'bb_middle': middle,
        'bb_lower': lower,
        'bb_width': width
    }


def calculate_atr(df: pd.DataFrame, period=14) -> pd.Series:
    """Calculate Average True Range."""
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift())
    low_close = abs(df['low'] - df['close'].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr


def calculate_adx(df: pd.DataFrame, period=14) -> dict:
    """Calculate ADX (Average Directional Index)."""
    # Calculate directional movement
    high_diff = df['high'].diff()
    low_diff = -df['low'].diff()
    
    plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    
    # Calculate ATR for normalization
    atr = calculate_atr(df, period)
    
    # Calculate directional indicators
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
    
    # Calculate DX and ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 1e-10)
    adx = dx.rolling(window=period).mean()
    
    return {
        'adx': adx,
        'plus_di': plus_di,
        'minus_di': minus_di
    }


def calculate_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Money Flow Index."""
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
    """Calculate Chaikin Money Flow."""
    price_range = df['high'] - df['low']
    price_range = price_range.replace(0, 1e-10)
    mf_multiplier = ((df['close'] - df['low']) - (df['high'] - df['close'])) / price_range
    mf_volume = mf_multiplier * df['volume']
    cmf = mf_volume.rolling(window=period).sum() / df['volume'].rolling(window=period).sum()
    return cmf


def calculate_obv(df: pd.DataFrame) -> pd.Series:
    """Calculate On-Balance Volume."""
    direction = np.sign(df['close'].diff())
    obv = (direction * df['volume']).fillna(0).cumsum()
    return obv


def calculate_ad_line(df: pd.DataFrame) -> pd.Series:
    """Calculate Accumulation/Distribution Line."""
    price_range = df['high'] - df['low']
    price_range = price_range.replace(0, 1e-10)
    clv = ((df['close'] - df['low']) - (df['high'] - df['close'])) / price_range
    ad_line = (clv * df['volume']).fillna(0).cumsum()
    return ad_line


def calculate_all_indicators_for_symbol(conn, symbol: str, lookback_days: int | None = 365) -> int:
    """Calculate ALL technical indicators for a single symbol."""
    
    # Fetch historical data
    query = """
        SELECT date, open, high, low, close, volume
        FROM market_bar_daily
        WHERE symbol = %s
    """
    params = [symbol]
    
    if lookback_days:
        query += " AND date >= %s"
        cutoff_date = datetime.now() - timedelta(days=lookback_days)
        params.append(cutoff_date)
        
    query += " ORDER BY date ASC"
    
    with conn.cursor() as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        
        if len(rows) < 50:  # Need minimum data
            logger.debug(f"{symbol}: Insufficient data ({len(rows)} rows)")
            return 0
        
        # Convert to DataFrame
        df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        
        # Convert to numeric
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Calculate all indicators
        df['rsi_14'] = calculate_rsi(df, 14)
        df['rsi_20'] = calculate_rsi(df, 20)
        
        macd_data = calculate_macd(df)
        df['macd'] = macd_data['macd']
        df['macd_signal'] = macd_data['macd_signal']
        df['macd_hist'] = macd_data['macd_hist']
        
        bb_data = calculate_bollinger_bands(df, 20)
        df['bb_upper_20'] = bb_data['bb_upper']
        df['bb_middle_20'] = bb_data['bb_middle']
        df['bb_lower_20'] = bb_data['bb_lower']
        df['bb_width'] = bb_data['bb_width']
        
        df['atr_14'] = calculate_atr(df, 14)
        
        adx_data = calculate_adx(df, 14)
        df['adx_14'] = adx_data['adx']
        df['plus_di'] = adx_data['plus_di']
        df['minus_di'] = adx_data['minus_di']
        
        df['mfi_14'] = calculate_mfi(df, 14)
        df['cmf_20'] = calculate_cmf(df, 20)
        df['obv'] = calculate_obv(df)
        df['ad_line'] = calculate_ad_line(df)
        
        # Update database
        update_query = """
            UPDATE market_bar_daily
            SET rsi_14 = %s, rsi_20 = %s,
                macd = %s, macd_signal = %s, macd_hist = %s,
                bb_upper_20 = %s, bb_middle_20 = %s, bb_lower_20 = %s, bb_width = %s,
                atr_14 = %s,
                adx_14 = %s, plus_di = %s, minus_di = %s,
                mfi_14 = %s, cmf_20 = %s, obv = %s, ad_line = %s
            WHERE symbol = %s AND date = %s
        """
        
        updates = []
        for _, row in df.iterrows():
            if pd.notna(row['rsi_14']):  # Only update if indicators calculated
                updates.append((
                    float(row['rsi_14']) if pd.notna(row['rsi_14']) else None,
                    float(row['rsi_20']) if pd.notna(row['rsi_20']) else None,
                    float(row['macd']) if pd.notna(row['macd']) else None,
                    float(row['macd_signal']) if pd.notna(row['macd_signal']) else None,
                    float(row['macd_hist']) if pd.notna(row['macd_hist']) else None,
                    float(row['bb_upper_20']) if pd.notna(row['bb_upper_20']) else None,
                    float(row['bb_middle_20']) if pd.notna(row['bb_middle_20']) else None,
                    float(row['bb_lower_20']) if pd.notna(row['bb_lower_20']) else None,
                    float(row['bb_width']) if pd.notna(row['bb_width']) else None,
                    float(row['atr_14']) if pd.notna(row['atr_14']) else None,
                    float(row['adx_14']) if pd.notna(row['adx_14']) else None,
                    float(row['plus_di']) if pd.notna(row['plus_di']) else None,
                    float(row['minus_di']) if pd.notna(row['minus_di']) else None,
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
            logger.info(f"{symbol}: Updated {len(updates)} rows with all indicators")
            return len(updates)
        
        return 0


def run(lookback_days: int = 365) -> None:
    """Calculate all indicators for all symbols."""
    conn = get_connection()
    
    try:
        # Get all symbols with data
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol
                FROM market_bar_daily
                ORDER BY symbol
            """)
            
            symbols = [row[0] for row in cur.fetchall()]
        
        logger.info(f"Calculating indicators for {len(symbols)} symbols (lookback={lookback_days} days)")
        logger.info("Indicators: RSI, MACD, BB, ATR, ADX, MFI, CMF, OBV, A/D")
        
        total_updated = 0
        for i, symbol in enumerate(symbols, 1):
            try:
                updated = calculate_all_indicators_for_symbol(conn, symbol, lookback_days)
                total_updated += updated
                
                if i % 10 == 0:
                    logger.info(f"Progress: {i}/{len(symbols)} symbols ({total_updated} rows updated)")
            
            except Exception as e:
                logger.error(f"{symbol}: Failed - {e}")
                continue
        
        logger.info(f"\n✅ Complete! {total_updated} total rows updated across {len(symbols)} symbols")
    
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calculate comprehensive daily technical indicators")
    parser.add_argument("--lookback", type=int, default=365, help="Days of history to calculate")
    
    args = parser.parse_args()
    run(lookback_days=args.lookback)
