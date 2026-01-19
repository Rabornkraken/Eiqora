"""
Hourly Technical Indicators Tools.

Provides functions to fetch pre-calculated hourly indicators from database
and score hourly technical setup (0-1 scale) for intraday trigger analysis.
"""

import logging
from datetime import datetime
from typing import Any

from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)


async def get_hourly_indicators(symbol: str, asof_time: datetime) -> dict[str, Any]:
    """
    Fetch pre-calculated hourly technical indicators from database.
    
    Queries the market_bar_hourly table for the latest hourly bar
    at or before the specified time.
    
    Args:
        symbol: Stock symbol
        asof_time: Time to query (gets latest bar <= this time)
    
    Returns:
        dict with hourly indicators:
        - rsi_14: RSI value (0-100)
        - macd_histogram, macd_line, macd_signal: MACD components
        - vwap: Volume-Weighted Average Price
        - mfi_14: Money Flow Index (0-100)
        - cmf_20: Chaikin Money Flow (-1 to 1)
        - volume_z_20h: Volume Z-score
        - trend_direction: 'UPTREND', 'DOWNTREND', or 'SIDEWAYS'
        - support_level, resistance_level: Key price levels
        - current_price: Close price of the bar
        - error: Error message if data unavailable
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        datetime,
                        close,
                        rsi_14,
                        macd_histogram,
                        macd_line,
                        macd_signal,
                        vwap,
                        mfi_14,
                        cmf_20,
                        volume_z_20h,
                        trend_direction,
                        support_level,
                        resistance_level
                    FROM market_bar_hourly
                    WHERE symbol = %s
                      AND datetime <= %s
                      AND rsi_14 IS NOT NULL  -- Ensure indicators are calculated
                    ORDER BY datetime DESC
                    LIMIT 1
                """, (symbol, asof_time))
                
                row = cur.fetchone()
                
                if not row:
                    logger.warning(f"No hourly indicators for {symbol} at {asof_time}")
                    return {"error": "No hourly data available"}
                
                return {
                    "datetime": row[0],
                    "current_price": float(row[1]) if row[1] else None,
                    "rsi_14": float(row[2]) if row[2] else None,
                    "macd_histogram": float(row[3]) if row[3] else None,
                    "macd_line": float(row[4]) if row[4] else None,
                    "macd_signal": float(row[5]) if row[5] else None,
                    "vwap": float(row[6]) if row[6] else None,
                    "mfi_14": float(row[7]) if row[7] else None,
                    "cmf_20": float(row[8]) if row[8] else None,
                    "volume_z_20h": float(row[9]) if row[9] else None,
                    "trend_direction": row[10],
                    "support_level": float(row[11]) if row[11] else None,
                    "resistance_level": float(row[12]) if row[12] else None,
                }
    
    except Exception as e:
        logger.error(f"Error fetching hourly indicators for {symbol}: {e}")
        return {"error": str(e)}


async def score_hourly_technicals(
    symbol: str,
    asof_time: datetime,
) -> tuple[float, dict[str, float]]:
    """
    Score hourly technical setup on 0-1 scale.
    
    Uses pre-calculated indicators from database to evaluate
    intraday technical strength. Separate from daily scoring
    to capture intraday reversals and momentum.
    
    Args:
        symbol: Stock symbol
        asof_time: Time to evaluate
    
    Returns:
        (score 0-1.0, breakdown dict)
        
    Scoring Components (0-1 scale):
    - Hourly Trend: 25%
    - Hourly RSI: 20%
    - Volume Surge: 20%
    - VWAP Position: 15%
    - Hourly CMF: 10%
    - MACD: 10%
    
    Threshold for analysis gate: 0.60
    """
    indicators = await get_hourly_indicators(symbol, asof_time)
    
    if indicators.get("error"):
        logger.debug(f"{symbol}: {indicators['error']}")
        return 0.0, {"error": "No hourly data"}
    
    scores: dict[str, float] = {}
    total = 0.0
    
    def add_score(label: str, value: float) -> None:
        nonlocal total
        if value:
            scores[label] = value
            total += value
    
    # Hourly Trend (25%)
    trend = indicators.get("trend_direction")
    if trend == "UPTREND":
        add_score("hourly_trend_up", 0.25)
    elif trend == "DOWNTREND":
        add_score("hourly_trend_down", -0.10)  # Negative for downtrend
    elif trend == "SIDEWAYS":
        add_score("hourly_trend_sideways", 0.10)
    
    # Hourly RSI (20%)
    rsi = indicators.get("rsi_14")
    if rsi is not None:
        if rsi < 30:
            # Oversold on hourly = potential bounce
            add_score("hourly_rsi_oversold", 0.10)
        elif 30 <= rsi < 40:
            add_score("hourly_rsi_recovery", 0.15)
        elif 40 <= rsi <= 60:
            add_score("hourly_rsi_neutral", 0.20)
        elif 60 < rsi <= 70:
            add_score("hourly_rsi_momentum", 0.15)
        elif rsi > 70:
            add_score("hourly_rsi_overbought", 0.05)
    
    # Volume Surge (20%)
    vol_z = indicators.get("volume_z_20h")
    if vol_z is not None:
        if vol_z > 2.5:
            add_score("hourly_volume_spike", 0.20)
        elif vol_z > 1.5:
            add_score("hourly_volume_elevated", 0.15)
        elif vol_z > 0.5:
            add_score("hourly_volume_above_avg", 0.10)
        elif vol_z < -1.0:
            add_score("hourly_volume_low", -0.05)
    
    # VWAP Position (15%)
    current_price = indicators.get("current_price")
    vwap = indicators.get("vwap")
    if current_price and vwap:
        vwap_diff = (current_price - vwap) / vwap
        if vwap_diff > 0.005:  # >0.5% above VWAP
            add_score("hourly_above_vwap", 0.15)
        elif vwap_diff > 0:
            add_score("hourly_near_vwap_above", 0.10)
        elif vwap_diff > -0.005:
            add_score("hourly_near_vwap_below", 0.05)
        else:
            # Below VWAP not inherently bad for reversals
            add_score("hourly_below_vwap", 0.02)
    
    # Hourly CMF (10%)
    cmf = indicators.get("cmf_20")
    if cmf is not None:
        if cmf > 0.15:
            add_score("hourly_cmf_accumulation", 0.10)
        elif cmf > 0.05:
            add_score("hourly_cmf_slight_accum", 0.07)
        elif cmf > -0.05:
            add_score("hourly_cmf_neutral", 0.05)
        elif cmf < -0.15:
            add_score("hourly_cmf_distribution", -0.05)
    
    # MACD (10%)
    macd_hist = indicators.get("macd_histogram")
    if macd_hist is not None:
        if macd_hist > 0.5:
            add_score("hourly_macd_strong_bull", 0.10)
        elif macd_hist > 0:
            add_score("hourly_macd_bullish", 0.07)
        elif macd_hist < -0.5:
            add_score("hourly_macd_bearish", -0.03)
        else:
            add_score("hourly_macd_weak_bear", 0.02)
    
    # Clamp to 0-1 range
    total = max(min(total, 1.0), 0.0)
    
    return total, scores
