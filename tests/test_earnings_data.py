
import yfinance as yf
from datetime import datetime
import pandas as pd

def test_fetch_earnings(symbol):
    print(f"Fetching earnings data for {symbol}...")
    ticker = yf.Ticker(symbol)
    
    # 1. Estimates from Calendar
    try:
        cal = ticker.calendar
        print("\nCalendar Data:")
        print(cal)
    except Exception as e:
        print(f"Error fetching calendar: {e}")

    # 2. Historical from Income Statement
    try:
        # q_stmt = ticker.quarterly_income_stmt
        # Use income_stmt (annual) or quarterly_income_stmt
        q_stmt = ticker.quarterly_income_stmt
        if not q_stmt.empty:
            print("\nQuarterly Income Statement (Top 5 rows):")
            print(q_stmt.head())
            
            # Extract specific fields
            latest_date = q_stmt.columns[0]
            print(f"\nLatest Quarter: {latest_date}")
            
            total_rev = q_stmt.loc['Total Revenue', latest_date] if 'Total Revenue' in q_stmt.index else None
            # EPS might be separate or in financials
            print(f"Total Revenue: {total_rev}")
            
    except Exception as e:
        print(f"Error fetching income stmt: {e}")

if __name__ == "__main__":
    test_fetch_earnings("NVDA")
