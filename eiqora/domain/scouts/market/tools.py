import yfinance as yf
from typing import Dict, Any

def get_stock_info(ticker: str) -> Dict[str, Any]:
    """Fetches fundamental data for a given ticker."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # Filter for key metrics to save context window
        key_metrics = {
            "currentPrice": info.get("currentPrice"),
            "marketCap": info.get("marketCap"),
            "trailingPE": info.get("trailingPE"),
            "forwardPE": info.get("forwardPE"),
            "dividendYield": info.get("dividendYield"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "recommendationKey": info.get("recommendationKey"),
            "targetMeanPrice": info.get("targetMeanPrice"),
            "ebitda": info.get("ebitda"),
            "revenueGrowth": info.get("revenueGrowth"),
            "profitMargins": info.get("profitMargins"),
        }
        return key_metrics
    except Exception as e:
        return {"error": f"Failed to fetch info for {ticker}: {str(e)}"}

def get_stock_history(ticker: str, period: str = "1mo") -> str:
    """Fetches historical price data summary."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            return "No historical data found."
        
        # Return a simplified CSV string
        return hist.to_csv(index=True)
    except Exception as e:
        return f"Error fetching history: {str(e)}"
