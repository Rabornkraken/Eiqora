"""
Price data tools.
Fetches OHLCV data and computes technical indicators from market_bar_daily table.
"""

from datetime import datetime, date
from typing import Any

import numpy as np
import pandas as pd

from eiqora_v2.tools.db import get_connection


async def get_prices(
    symbol: str,
    window_days: int,
    asof_time: datetime,
) -> list[dict[str, Any]]:
    """
    Fetch daily OHLCV bars from market_bar_daily.
    
    Args:
        symbol: Stock ticker symbol
        window_days: Number of trading days to fetch
        asof_time: Point-in-time reference (no data after this time)
    
    Returns:
        List of price bar dicts with keys: date, open, high, low, close, volume, vwap
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT date, open, high, low, close, volume, vwap
            FROM market_bar_daily
            WHERE symbol = $1
              AND date <= $2::date
              AND date >= ($2::date - interval '1 day' * $3)
            ORDER BY date ASC
        """, symbol, asof_time, window_days)
        
        return [dict(r) for r in rows]


async def get_indicators(
    symbol: str,
    window_days: int,
    asof_time: datetime,
) -> dict[str, Any]:
    """
    Compute technical indicators from price data.
    
    Computes:
    - Moving averages: MA20, MA50, MA200
    - Volatility: RV20 (20-day realized volatility), ATR14
    - Momentum: 20d return, 60d return
    - Volume: 20d volume z-score
    - Trend status: above/below each MA
    
    Args:
        symbol: Stock ticker symbol
        window_days: Base window (extra lookback added for MA computation)
        asof_time: Point-in-time reference
    
    Returns:
        Dict with computed indicators
    """
    # Fetch extra days for MA lookback
    lookback_days = window_days + 210  # Extra for MA200
    prices = await get_prices(symbol, lookback_days, asof_time)
    
    if len(prices) < 20:
        return {
            "error": "Insufficient data",
            "data_points": len(prices),
        }
    
    df = pd.DataFrame(prices)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    
    # Current price
    current_price = float(close.iloc[-1])
    
    # Moving averages
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    
    # Trend status
    trend = {
        "ma20": "ABOVE" if current_price > ma20 else "BELOW",
        "ma50": "ABOVE" if ma50 and current_price > ma50 else "BELOW" if ma50 else None,
        "ma200": "ABOVE" if ma200 and current_price > ma200 else "BELOW" if ma200 else None,
    }
    
    # Realized volatility (20-day)
    log_returns = np.log(close / close.shift(1))
    rv20 = float(log_returns.rolling(20).std().iloc[-1])
    
    # ATR14 (Average True Range)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr14 = float(tr.rolling(14).mean().iloc[-1])
    
    # Momentum (returns)
    ret_20d = float((close.iloc[-1] / close.iloc[-20] - 1)) if len(close) >= 20 else None
    ret_60d = float((close.iloc[-1] / close.iloc[-60] - 1)) if len(close) >= 60 else None
    
    # Volume z-score
    vol_mean = volume.rolling(20).mean().iloc[-1]
    vol_std = volume.rolling(20).std().iloc[-1]
    volume_z_20d = float((volume.iloc[-1] - vol_mean) / vol_std) if vol_std > 0 else 0.0
    
    # State tags
    state_tags = []
    if trend["ma20"] == "ABOVE" and trend["ma50"] == "ABOVE":
        state_tags.append("UPTREND")
    elif trend["ma20"] == "BELOW" and trend["ma50"] == "BELOW":
        state_tags.append("DOWNTREND")
    else:
        state_tags.append("MIXED")
    
    if rv20 > 0.03:
        state_tags.append("HIGH_VOL")
    elif rv20 < 0.015:
        state_tags.append("LOW_VOL")
    
    if volume_z_20d > 2.0:
        state_tags.append("HIGH_VOLUME")
    
    return {
        "current_price": current_price,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "trend": trend,
        "rv20": rv20,
        "atr14": atr14,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "volume_z_20d": volume_z_20d,
        "state_tags": state_tags,
        "data_points": len(prices),
    }


async def get_price_levels(
    symbol: str,
    window_days: int,
    asof_time: datetime,
) -> dict[str, Any]:
    """
    Get key price levels for chart analysis.
    
    Returns:
    - Recent high/low (20d, 60d)
    - Support/resistance levels
    - Yesterday's OHLC
    """
    prices = await get_prices(symbol, window_days + 10, asof_time)
    
    if len(prices) < 2:
        return {"error": "Insufficient data"}
    
    df = pd.DataFrame(prices)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    
    # Yesterday's bar
    yesterday = prices[-1]
    
    # Recent ranges
    high_20d = float(high.tail(20).max())
    low_20d = float(low.tail(20).min())
    high_60d = float(high.tail(60).max()) if len(high) >= 60 else high_20d
    low_60d = float(low.tail(60).min()) if len(low) >= 60 else low_20d
    
    return {
        "yesterday": {
            "date": yesterday["date"],
            "open": float(yesterday["open"]),
            "high": float(yesterday["high"]),
            "low": float(yesterday["low"]),
            "close": float(yesterday["close"]),
        },
        "high_20d": high_20d,
        "low_20d": low_20d,
        "high_60d": high_60d,
        "low_60d": low_60d,
        "range_20d_pct": (high_20d - low_20d) / low_20d,
    }
