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
        List of price bar dicts with keys: date, open, high, low, close, volume, vwap, mfi_14, cmf_20, obv, ad_line
    """
    async with get_connection() as conn:
        rows = await conn.fetch("""
            SELECT date, open, high, low, close, volume, vwap,
                   mfi_14, cmf_20, obv, ad_line
            FROM market_bar_daily
            WHERE symbol = $1
              AND date <= $2::date
              AND date >= ($2::date - interval '1 day' * $3)
            ORDER BY date ASC
        """, symbol, asof_time, window_days)
        
        return [dict(r) for r in rows]


async def get_return_metrics(
    symbol: str,
    window_days: int,
    asof_time: datetime,
) -> dict[str, Any]:
    """
    Compute basic return metrics from price data.

    Returns:
        Dict with ret_20d, ret_60d, last_date, data_points.
    """
    prices = await get_prices(symbol, window_days, asof_time)
    if len(prices) < 20:
        return {
            "error": "Insufficient data",
            "data_points": len(prices),
        }

    df = pd.DataFrame(prices)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    close = df["close"].astype(float)
    last_date = df.index.max().date() if len(df.index) > 0 else None
    current_price = float(close.iloc[-1])

    ret_5d = float((close.iloc[-1] / close.iloc[-5] - 1)) if len(close) >= 5 else None
    ret_20d = float((close.iloc[-1] / close.iloc[-20] - 1)) if len(close) >= 20 else None
    ret_60d = float((close.iloc[-1] / close.iloc[-60] - 1)) if len(close) >= 60 else None

    return {
        "current_price": current_price,
        "ret_5d": ret_5d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "last_date": last_date,
        "data_points": len(prices),
    }


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
    # Use a wider calendar window to reliably capture 200 trading days.
    lookback_days = max(window_days + 210, 320)
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
    last_date = df.index.max().date() if len(df.index) > 0 else None
    
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
    
    # === ADVANCED INDICATORS ===
    
    # RSI (Relative Strength Index) - 14 period
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi14 = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
    
    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_histogram = macd_line - macd_signal
    macd = {
        "line": float(macd_line.iloc[-1]),
        "signal": float(macd_signal.iloc[-1]),
        "histogram": float(macd_histogram.iloc[-1]),
        "bullish_cross": bool(macd_line.iloc[-1] > macd_signal.iloc[-1] and macd_line.iloc[-2] <= macd_signal.iloc[-2]) if len(macd_line) > 2 else False,
        "bearish_cross": bool(macd_line.iloc[-1] < macd_signal.iloc[-1] and macd_line.iloc[-2] >= macd_signal.iloc[-2]) if len(macd_line) > 2 else False,
    }
    
    # Bollinger Bands (20, 2)
    bb_sma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_upper = bb_sma + (bb_std * 2)
    bb_lower = bb_sma - (bb_std * 2)
    bollinger = {
        "upper": float(bb_upper.iloc[-1]),
        "middle": float(bb_sma.iloc[-1]),
        "lower": float(bb_lower.iloc[-1]),
        "width": float((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / bb_sma.iloc[-1]) if bb_sma.iloc[-1] > 0 else 0,
        "price_position": "UPPER" if current_price > bb_upper.iloc[-1] else "LOWER" if current_price < bb_lower.iloc[-1] else "MIDDLE",
    }
    
    # ADX (Average Directional Index) - 14 period
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    
    tr_smooth = tr.ewm(span=14, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / tr_smooth)
    minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / tr_smooth)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=14, adjust=False).mean()
    adx14 = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 25.0
    
    # Stochastic (14, 3)
    lowest_low = low.rolling(14).min()
    highest_high = high.rolling(14).max()
    stoch_k = 100 * ((close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan))
    stoch_d = stoch_k.rolling(3).mean()
    stochastic = {
        "k": float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50.0,
        "d": float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50.0,
        "overbought": bool(stoch_k.iloc[-1] > 80) if not pd.isna(stoch_k.iloc[-1]) else False,
        "oversold": bool(stoch_k.iloc[-1] < 20) if not pd.isna(stoch_k.iloc[-1]) else False,
    }
    
    # OBV (On-Balance Volume) with trend
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    obv_ma20 = obv.rolling(20).mean()
    obv_current = float(obv.iloc[-1])
    obv_ma = float(obv_ma20.iloc[-1]) if not pd.isna(obv_ma20.iloc[-1]) else obv_current
    obv_trend = "RISING" if obv_current > obv_ma else "FALLING"
    obv_data = {
        "current": obv_current,
        "ma20": obv_ma,
        "trend": obv_trend,
    }
    
    # Momentum (returns)
    ret_5d = float((close.iloc[-1] / close.iloc[-5] - 1)) if len(close) >= 5 else None
    ret_20d = float((close.iloc[-1] / close.iloc[-20] - 1)) if len(close) >= 20 else None
    ret_60d = float((close.iloc[-1] / close.iloc[-60] - 1)) if len(close) >= 60 else None
    
    # Volume z-score
    vol_mean = volume.rolling(20).mean().iloc[-1]
    vol_std = volume.rolling(20).std().iloc[-1]
    volume_z_20d = float((volume.iloc[-1] - vol_mean) / vol_std) if vol_std > 0 else 0.0
    
    # State tags (enhanced with new indicators)
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
    
    # New state tags from advanced indicators
    if rsi14 > 70:
        state_tags.append("OVERBOUGHT_RSI")
    elif rsi14 < 30:
        state_tags.append("OVERSOLD_RSI")
    
    if adx14 > 25:
        state_tags.append("STRONG_TREND")
    elif adx14 < 15:
        state_tags.append("WEAK_TREND")
    
    if macd["bullish_cross"]:
        state_tags.append("MACD_BULLISH_CROSS")
    elif macd["bearish_cross"]:
        state_tags.append("MACD_BEARISH_CROSS")
    
    # Extract money flow indicators from latest bar (if available)
    latest_bar = prices[-1]
    mfi_14 = float(latest_bar.get('mfi_14')) if latest_bar.get('mfi_14') is not None else None
    cmf_20 = float(latest_bar.get('cmf_20')) if latest_bar.get('cmf_20') is not None else None
    obv_db = float(latest_bar.get('obv')) if latest_bar.get('obv') is not None else None
    ad_line = float(latest_bar.get('ad_line')) if latest_bar.get('ad_line') is not None else None
    
    # Money flow state tags
    if mfi_14 is not None:
        if mfi_14 > 80:
            state_tags.append("MFI_OVERBOUGHT")
        elif mfi_14 < 20:
            state_tags.append("MFI_OVERSOLD")
    
    if cmf_20 is not None:
        if cmf_20 > 0.15:
            state_tags.append("STRONG_ACCUMULATION")
        elif cmf_20 < -0.15:
            state_tags.append("STRONG_DISTRIBUTION")
    
    return {
        "current_price": current_price,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "trend": trend,
        "rv20": rv20,
        "atr14": atr14,
        # New indicators
        "rsi14": rsi14,
        "macd": macd,
        "bollinger": bollinger,
        "adx14": adx14,
        "stochastic": stochastic,
        "obv": obv_data,
        # Money Flow Indicators (from DB)
        "mfi_14": mfi_14,
        "cmf_20": cmf_20,
        "obv_db": obv_db,
        "ad_line": ad_line,
        # Momentum
        "ret_5d": ret_5d,
        "ret_20d": ret_20d,
        "ret_60d": ret_60d,
        "volume_z_20d": volume_z_20d,
        "state_tags": state_tags,
        "data_points": len(prices),
        "last_date": last_date,
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


async def get_hourly_bars(
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """
    Fetch hourly OHLCV bars from market_bar_hourly table.
    
    Used by BacktestEngine for precise TP/SL checking.
    
    Args:
        symbol: Stock ticker symbol
        start_date: Start date (inclusive)
        end_date: End date (inclusive)
    
    Returns:
        List of hourly bar dicts with keys: timestamp, open, high, low, close, volume
    """
    async with get_connection() as conn:
        try:
            rows = await conn.fetch("""
                SELECT timestamp, open, high, low, close, volume
                FROM market_bar_hourly
                WHERE symbol = $1
                  AND timestamp::date >= $2
                  AND timestamp::date <= $3
                ORDER BY timestamp ASC
            """, symbol, start_date, end_date)
            
            return [dict(r) for r in rows]
        except Exception:
            # Table may not exist
            return []


async def get_hourly_indicators(
    symbol: str,
    check_date: date,
    asof_time: datetime,
) -> dict[str, Any]:
    """
    Get hourly indicators for precise entry timing.
    
    Multi-timeframe approach:
    - Daily indicators (get_indicators) → Trend/setup identification
    - Hourly indicators (this function) → Entry timing
    
    Computes:
    - VWAP (Volume Weighted Average Price)
    - Hourly RSI (8-period for faster signals)
    - Volume profile
    - Intraday trend
    
    Args:
        symbol: Stock ticker
        check_date: Date to analyze
        asof_time: Point-in-time reference
    
    Returns:
        Dict with hourly indicators for entry timing
    """
    from datetime import timedelta, timezone
    
    async with get_connection() as conn:
        # Get the most recent hourly bars (capped at asof_time)
        if asof_time.tzinfo is None:
            asof_ts = asof_time.replace(tzinfo=timezone.utc)
        else:
            asof_ts = asof_time.astimezone(timezone.utc)
        
        try:
            rows = await conn.fetch("""
                SELECT datetime, open, high, low, close, volume
                FROM market_bar_hourly
                WHERE symbol = $1
                  AND datetime <= $2
                ORDER BY datetime DESC
                LIMIT 200
            """, symbol, asof_ts)
            
            if len(rows) < 8:
                return {"error": "Insufficient hourly data", "data_points": len(rows)}
            
            rows = list(reversed(rows))
            df = pd.DataFrame([dict(r) for r in rows])
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.set_index("datetime").sort_index()
            
            close = df["close"].astype(float)
            high = df["high"].astype(float)
            low = df["low"].astype(float)
            volume = df["volume"].astype(float)
            
            # Get the most recent trading day in the data (not necessarily check_date)
            latest_ts = df.index.max()
            if asof_ts - latest_ts > timedelta(hours=2):
                return {
                    "error": "Stale hourly data",
                    "data_points": len(rows),
                    "bar_time": latest_ts.isoformat(),
                }
            latest_date = latest_ts.date()
            today_bars = df[df.index.date == latest_date]
            
            if len(today_bars) == 0:
                return {"error": "No hourly data for latest date", "data_points": 0}
            
            current_price = float(close.iloc[-1])
            
            # === VWAP (Volume Weighted Average Price) ===
            today_close = today_bars["close"].astype(float)
            today_volume = today_bars["volume"].astype(float)
            today_typical = (today_bars["high"].astype(float) + today_bars["low"].astype(float) + today_bars["close"].astype(float)) / 3
            
            vwap = (today_typical * today_volume).sum() / today_volume.sum() if today_volume.sum() > 0 else current_price
            vwap_distance_pct = (current_price - vwap) / vwap if vwap > 0 else 0
            
            # === Hourly RSI (8-period for faster signals) ===
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(8).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(8).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            rsi_hourly = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
            
            # === Volume Profile (today's accumulation) ===
            total_volume_today = float(today_volume.sum())
            avg_volume_today = float(today_volume.mean())
            current_hour_volume = float(today_volume.iloc[-1])
            
            volume_profile = {
                "total": total_volume_today,
                "avg_per_hour": avg_volume_today,
                "current_hour": current_hour_volume,
                "current_vs_avg": current_hour_volume / avg_volume_today if avg_volume_today > 0 else 1.0,
            }
            
            # === Intraday Trend ===
            if len(today_bars) >= 3:
                today_ema3 = today_close.ewm(span=3, adjust=False).mean()
                intraday_trend = "UP" if today_close.iloc[-1] > today_ema3.iloc[-1] else "DOWN"
            else:
                intraday_trend = "NEUTRAL"
            
            # === Hourly High/Low Range ===
            today_high = float(today_bars["high"].max())
            today_low = float(today_bars["low"].min())
            price_range_pct = (today_high - today_low) / today_low if today_low > 0 else 0
            
            # Position within today's range
            if today_high > today_low:
                position_in_range = (current_price - today_low) / (today_high - today_low)
            else:
                position_in_range = 0.5
            
            # === State Tags ===
            state_tags = []
            
            if vwap_distance_pct > 0.01:
                state_tags.append("ABOVE_VWAP")
            elif vwap_distance_pct < -0.01:
                state_tags.append("BELOW_VWAP")
            
            if rsi_hourly > 70:
                state_tags.append("HOURLY_OVERBOUGHT")
            elif rsi_hourly < 30:
                state_tags.append("HOURLY_OVERSOLD")
            
            if volume_profile["current_vs_avg"] > 2.0:
                state_tags.append("HOURLY_VOLUME_SPIKE")
            
            if position_in_range > 0.8:
                state_tags.append("NEAR_HOD")  # Near high of day
            elif position_in_range < 0.2:
                state_tags.append("NEAR_LOD")  # Near low of day
            
            return {
                "current_price": current_price,
                "bar_time": latest_ts.isoformat(),
                "vwap": float(vwap),
                "vwap_distance_pct": vwap_distance_pct * 100,
                "rsi_hourly": rsi_hourly,
                "volume_profile": volume_profile,
                "intraday_trend": intraday_trend,
                "today_high": today_high,
                "today_low": today_low,
                "price_range_pct": price_range_pct * 100,
                "position_in_range": position_in_range,  # 0-1, where 1 is at high
                "state_tags": state_tags,
                "bars_today": len(today_bars),
                "data_points": len(rows),
            }
            
        except Exception as e:
            return {"error": str(e), "data_points": 0}
