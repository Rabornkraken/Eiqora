import yfinance as yf
from typing import Dict, Any, List
import pandas as pd
from datetime import datetime

def get_earnings_data(ticker: str) -> Dict[str, Any]:
    """
    Fetches earnings calendar and historical surprise data.
    """
    try:
        stock = yf.Ticker(ticker)
        
        # 1. Upcoming Earnings (The "Calendar")
        # Note: yfinance .calendar structure varies by version, handling broadly
        calendar = stock.calendar
        next_date = "Unknown"
        
        if isinstance(calendar, dict) and "Earnings Date" in calendar:
            # Often a list of dates
            dates = calendar["Earnings Date"]
            if dates:
                next_date = str(dates[0])
        elif isinstance(calendar, pd.DataFrame) and not calendar.empty:
             # Sometimes it's a dataframe
             if 0 in calendar.columns:
                 next_date = str(calendar.iloc[0, 0])

        # 2. Earnings History (The "Track Record")
        # stock.earnings_dates returns a DataFrame with index as timestamps
        # Columns often: ['EPS Estimate', 'Reported EPS', 'Surprise(%)']
        history = stock.earnings_dates
        
        past_surprises = []
        if history is not None and not history.empty:
            # Filter for past dates only
            now = pd.Timestamp.now()
            past_data = history[history.index < now].head(4) # Last 4 quarters
            
            for date, row in past_data.iterrows():
                past_surprises.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "estimate": row.get("EPS Estimate"),
                    "reported": row.get("Reported EPS"),
                    "surprise_pct": row.get("Surprise(%)")
                })

        return {
            "next_earnings_date": next_date,
            "recent_surprises": past_surprises,
            "calendar_data": str(calendar) if calendar is not None else "None"
        }

    except Exception as e:
        return {"error": f"Earnings analysis failed: {str(e)}"}
