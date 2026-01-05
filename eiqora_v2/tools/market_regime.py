"""
Market Regime tool.
Calculates macro risk regime from Yield/Bond ETF price vectors.
"""
from datetime import datetime, date
from typing import Any
import pandas as pd
from eiqora_v2.tools.prices import get_prices

async def get_market_regime(asof_time: datetime) -> dict[str, Any]:
    """
    Determine market regime using bond ETFs as proxy for rates.
    IEF = 7-10 Year Treasury Bond (Inverse of Yield)
    SHY = 1-3 Year Treasury Bond (Inverse of 2Y/Fed Expectations)
    TLT = 20+ Year Treasury Bond (Long Duration Volatility)
    
    Returns:
        dict: {
            "mode": "RISK_ON" | "RISK_OFF" | "CAUTIOUS",
            "risk_multiplier": float (1.0 = full risk, 0.0 = no risk),
            "yield_trend": "RISING" | "FALLING",
            "details": {...}
        }
    """
    # Fetch data (trailing 60 days)
    # Note: get_prices returns empty list if sufficient data isn't there
    ief_prices = await get_prices("IEF", 60, asof_time)
    tlt_prices = await get_prices("TLT", 60, asof_time)

    # Defaults if data missing (e.g. at start of backtest)
    if len(ief_prices) < 20 or len(tlt_prices) < 20:
        return {
            "mode": "NEUTRAL", 
            "risk_multiplier": 1.0, 
            "yield_trend": "FLAT",
            "reason": "Insufficient Macro Data"
        }

    # Process IEF (10Y Proxy)
    # Price and Yield are INVERSELY correlated.
    # IEF Price DOWN = Yields UP (Bad for stocks)
    # IEF Price UP = Yields DOWN (Good for stocks)
    ief_df = pd.DataFrame(ief_prices)
    ief_close = ief_df["close"].astype(float)
    ief_ma20 = ief_close.rolling(20).mean().iloc[-1]
    ief_current = ief_close.iloc[-1]
    
    # Process TLT (Volatility Proxy)
    tlt_df = pd.DataFrame(tlt_prices)
    tlt_close = tlt_df["close"].astype(float)
    tlt_returns = tlt_close.pct_change()
    tlt_vol = tlt_returns.rolling(20).std().iloc[-1] * (252 ** 0.5) # Annualized Volatility

    # Logic:
    
    # 1. Yield Trend
    # If IEF Price < MA20, bond prices are falling, so Yields are RISING.
    # Rising yields are generally a headwind for equities (especially growth).
    yield_trend = "RISING" if ief_current < ief_ma20 else "FALLING"

    # 2. Risk Mode Logic
    # High Bond Volatility (> 20%) is structurally dangerous for risk assets.
    # Rising Yields acts as a brake on valuation.
    
    mode = "RISK_ON"
    risk_multiplier = 1.0
    
    if tlt_vol > 0.20:
        # Extreme bond volatility - stay out
        mode = "RISK_OFF"
        risk_multiplier = 0.0
    elif yield_trend == "RISING":
        # Yields represent a headwind
        mode = "CAUTIOUS"
        risk_multiplier = 0.5
    else:
        # Falling yields + Stable bonds = Green light
        mode = "RISK_ON"
        risk_multiplier = 1.0

    return {
        "mode": mode,
        "risk_multiplier": risk_multiplier,
        "yield_trend": yield_trend,
        "bond_volatility": float(tlt_vol),
        "details": {
            "ief_current": float(ief_current),
            "ief_ma20": float(ief_ma20),
            "tlt_volatility": float(tlt_vol)
        }
    }
