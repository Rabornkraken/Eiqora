
import asyncio
import yfinance as yf
import pandas as pd
from datetime import datetime, date
from eiqora_v2.tools.db import get_connection

async def backfill_nvda():
    print("Fetching NVDA data from yfinance...")
    ticker = yf.Ticker("NVDA")
    
    # 1. Get Earnings Dates (EPS)
    try:
        earnings_dates = ticker.earnings_dates
        if earnings_dates is None or earnings_dates.empty:
            print("No earnings dates found via yfinance.")
            return
    except Exception as e:
        print(f"Error fetching earnings dates: {e}")
        return

    # 2. Get Quarterly Financials (Revenue)
    try:
         financials = ticker.quarterly_financials
         if financials.empty:
             print("Warning: financials empty, try income_stmt")
             financials = ticker.quarterly_income_stmt
         # Transpose so dates are index
         financials = financials.T 
    except Exception as e:
        print(f"Error fetching financials: {e}")
        financials = pd.DataFrame()

    print(f"Found {len(earnings_dates)} earnings records.")
    
    rows_to_insert = []
    
    for date_idx, row in earnings_dates.iterrows():
        # date_idx is Timestamp (Report Date)
        if hasattr(date_idx, 'date'):
             report_date = date_idx.date()
        else:
             report_date = date_idx
             
        if isinstance(report_date, datetime):
            report_date = report_date.date()
            
        if report_date > datetime.now().date():
            continue # Skip future
            
        eps_est = row.get('EPS Estimate')
        eps_actual = row.get('Reported EPS')
        
        # Check for NaN and convert
        eps_est = float(eps_est) if pd.notna(eps_est) else None
        eps_actual = float(eps_actual) if pd.notna(eps_actual) else None
        
        revenue_actual = None
        
        if not financials.empty:
            # Find closest fiscal end date BEFORE report date
            # financials index should be DatetimeIndex
            # Convert report_date to timestamp for comparison
            report_ts = pd.Timestamp(report_date)
            
            possible_fiscal_ends = financials.index[financials.index < report_ts]
            if not possible_fiscal_ends.empty:
                # Sort descending to get closest
                possible_fiscal_ends = possible_fiscal_ends.sort_values(ascending=False)
                closest_end = possible_fiscal_ends[0]
                
                # Ensure it's within 3 months (90 days) + buffer
                diff_days = (report_ts - closest_end).days
                if diff_days < 100:
                    try:
                        rev = financials.loc[closest_end].get('Total Revenue')
                        if pd.notna(rev):
                            revenue_actual = float(rev)
                    except:
                        pass
        
        rows_to_insert.append({
            'symbol': 'NVDA',
            'earnings_date': report_date,
            'eps_est': eps_est,
            'eps_actual': eps_actual,
            'revenue_actual': revenue_actual,
            'source': 'yfinance_backfill'
        })
    
    print(f"Prepared {len(rows_to_insert)} rows for insertion.")
    
    # Insert
    async with get_connection() as conn:
        for r in rows_to_insert:
            # print(f"Upserting {r['earnings_date']}...")
            await conn.execute("""
                INSERT INTO earnings_event
                (symbol, earnings_date, eps_est, eps_actual, revenue_actual, source, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (symbol, earnings_date)
                DO UPDATE SET
                    eps_est = COALESCE(EXCLUDED.eps_est, earnings_event.eps_est),
                    eps_actual = COALESCE(EXCLUDED.eps_actual, earnings_event.eps_actual),
                    revenue_actual = COALESCE(EXCLUDED.revenue_actual, earnings_event.revenue_actual),
                    updated_at = now()
            """, r['symbol'], r['earnings_date'], r['eps_est'], r['eps_actual'], r['revenue_actual'], r['source'])
            
        # Run YoY Calculation
        print("Calculating YoY Growth...")
        await conn.execute("""
            UPDATE earnings_event e
            SET revenue_growth_yoy = (
                (e.revenue_actual - prev.revenue_actual) / NULLIF(ABS(prev.revenue_actual), 0) * 100
            )
            FROM earnings_event prev
            WHERE e.symbol = prev.symbol
              AND prev.earnings_date < e.earnings_date - interval '10 months'
              AND prev.earnings_date > e.earnings_date - interval '14 months'
              AND e.revenue_actual IS NOT NULL
              AND prev.revenue_actual IS NOT NULL
              AND e.revenue_growth_yoy IS NULL
        """)

    print("Done.")

if __name__ == "__main__":
    asyncio.run(backfill_nvda())
