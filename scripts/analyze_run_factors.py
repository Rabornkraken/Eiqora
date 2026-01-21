#!/usr/bin/env python3
"""
Analyze backtest results to find correlations between trigger details and performance.
"""
import sys
import pandas as pd
import logging
from pathlib import Path
from collections import defaultdict

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.db.connection import get_connection

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

def analyze_run(run_id: str):
    logger.info(f"Analyzing Run: {run_id}")
    
    conn = get_connection()
    try:
        # Fetch data
        query = """
            SELECT 
                trigger_type,
                outcome,
                realized_pnl_pct,
                trigger_detail
            FROM trigger_backtest_result
            WHERE run_id = %s
              AND outcome IN ('TP_HIT', 'SL_HIT')
        """
        df = pd.read_sql(query, conn, params=(run_id,))
        
        if df.empty:
            logger.info("No executed trades found for this run.")
            return

        logger.info(f"Loaded {len(df)} trades.")
        
        # Flatten trigger_detail JSON into columns
        # This allows us to pivot on specific detail fields
        details_df = pd.json_normalize(df['trigger_detail'])
        df = pd.concat([df.drop(columns=['trigger_detail']), details_df], axis=1)
        
        # Define numeric columns to analyze (common across triggers)
        # We look for columns that actually exist in the data
        potential_factors = [
            'rsi_hourly', 'volume_z', 'volume_ratio', 'vwap_distance_pct',
            'cmf_20', 'mfi_14', 'technical_score', 'sentiment',
            'ma20_state', 'trend_direction' # Categorical
        ]
        
        factors = [c for c in potential_factors if c in df.columns]
        
        for trigger_type in df['trigger_type'].unique():
            print(f"\n{'='*60}")
            print(f"ANALYSIS: {trigger_type}")
            print(f"{'='*60}")
            
            subset = df[df['trigger_type'] == trigger_type].copy()
            total_trades = len(subset)
            base_wr = len(subset[subset['outcome'] == 'TP_HIT']) / total_trades * 100
            print(f"Base Win Rate: {base_wr:.2f}% ({total_trades} trades)")
            
            for factor in factors:
                if factor not in subset.columns or subset[factor].isnull().all():
                    continue
                
                print(f"\n--- Factor: {factor} ---")
                
                # Check if numeric or categorical
                if pd.api.types.is_numeric_dtype(subset[factor]):
                    # Quintile analysis
                    try:
                        subset['bin'] = pd.qcut(subset[factor], q=4, duplicates='drop')
                        stats = subset.groupby('bin', observed=False).agg({
                            'outcome': lambda x: (x == 'TP_HIT').mean() * 100,
                            'realized_pnl_pct': 'mean',
                            'trigger_type': 'count'
                        })
                        stats.columns = ['Win Rate %', 'Avg PnL %', 'Count']
                        print(stats)
                    except Exception as e:
                        print(f"(Could not bin numeric data: {e})")
                else:
                    # Categorical analysis
                    stats = subset.groupby(factor).agg({
                        'outcome': lambda x: (x == 'TP_HIT').mean() * 100,
                        'realized_pnl_pct': 'mean',
                        'trigger_type': 'count'
                    })
                    stats.columns = ['Win Rate %', 'Avg PnL %', 'Count']
                    print(stats)

    except Exception as e:
        logger.error(f"Analysis failed: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_run_factors.py <run_id>")
        sys.exit(1)
    analyze_run(sys.argv[1])
