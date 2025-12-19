import yfinance as yf
import pandas as pd
from typing import Dict, Any
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator
from ta.volatility import BollingerBands

def get_technical_indicators(ticker: str) -> Dict[str, Any]:
    """
    Calculates key technical indicators for a given ticker using the 'ta' library.
    Returns a dictionary with the latest values.
    """
    try:
        # 1. Fetch Data
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y")
        
        if df.empty:
            return {"error": "No price data found"}
            
        close = df["Close"]
        
        # 2. Calculate Indicators
        
        # RSI (14)
        rsi_ind = RSIIndicator(close=close, window=14)
        rsi = rsi_ind.rsi().iloc[-1]
        
        # MACD (12, 26, 9)
        macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        macd = macd_ind.macd().iloc[-1]
        macd_signal = macd_ind.macd_signal().iloc[-1]
        macd_hist = macd_ind.macd_diff().iloc[-1]
        
        # Bollinger Bands (20, 2)
        bb_ind = BollingerBands(close=close, window=20, window_dev=2)
        bb_upper = bb_ind.bollinger_hband().iloc[-1]
        bb_lower = bb_ind.bollinger_lband().iloc[-1]
        
        # SMA 50 & 200
        sma_50 = SMAIndicator(close=close, window=50).sma_indicator().iloc[-1]
        sma_200 = SMAIndicator(close=close, window=200).sma_indicator().iloc[-1]
        
        # 3. Helper for clean output
        def clean(val):
            return round(float(val), 2) if pd.notna(val) else None

        return {
            "current_price": clean(close.iloc[-1]),
            "rsi_14": clean(rsi),
            "macd": clean(macd),
            "macd_signal": clean(macd_signal),
            "macd_hist": clean(macd_hist),
            "bb_upper": clean(bb_upper),
            "bb_lower": clean(bb_lower),
            "sma_50": clean(sma_50),
            "sma_200": clean(sma_200),
            "trend_signal": _determine_trend(clean(close.iloc[-1]), clean(sma_50), clean(sma_200))
        }
    except Exception as e:
        return {"error": f"Technical analysis failed: {str(e)}"}

def _determine_trend(price, sma50, sma200) -> str:
    """Simple logic to determine the trend."""
    if not price or not sma50 or not sma200:
        return "Unknown"
    
    if price > sma50 > sma200:
        return "Strong Uptrend"
    elif price < sma50 < sma200:
        return "Strong Downtrend"
    elif price > sma200:
        return "Uptrend (Above SMA200)"
    elif price < sma200:
        return "Downtrend (Below SMA200)"
    else:
        return "Neutral/Choppy"