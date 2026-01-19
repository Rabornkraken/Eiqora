"""
Stock Correlations Calculator.
Calculates pairwise correlations from market_bar_daily data.
"""

import logging
from datetime import datetime
import numpy as np

from data_collection.db.connection import get_connection

logger = logging.getLogger(__name__)


def calculate_correlation(conn, symbol_a: str, symbol_b: str, days: int = 90) -> float | None:
    """Calculate correlation coefficient between two stocks."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT a.date, a.close as price_a, b.close as price_b
            FROM market_bar_daily a
            JOIN market_bar_daily b ON a.date = b.date
            WHERE a.symbol = %s AND b.symbol = %s
              AND a.date >= CURRENT_DATE - %s
            ORDER BY a.date
        """, (symbol_a, symbol_b, days))
        
        rows = cursor.fetchall()
        
        if len(rows) < 30:  # Need at least 30 days
            return None
        
        prices_a = np.array([float(r[1]) for r in rows])
        prices_b = np.array([float(r[2]) for r in rows])
        
        # Calculate returns
        returns_a = np.diff(prices_a) / prices_a[:-1]
        returns_b = np.diff(prices_b) / prices_b[:-1]
        
        # Correlation coefficient
        corr = np.corrcoef(returns_a, returns_b)[0, 1]
        
        return float(corr) if not np.isnan(corr) else None


def run(min_correlation: float = 0.5, limit: int | None = None):
    """Calculate correlations for watchlist stocks."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    
    conn = get_connection()
    try:
        # Get active watchlist symbols
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT symbol FROM daily_watchlist 
                WHERE scan_date >= CURRENT_DATE - 7
                ORDER BY symbol
            """)
            symbols = [r[0] for r in cursor.fetchall()]
        
        if limit:
            symbols = symbols[:limit]
        
        logger.info(f"Calculating correlations for {len(symbols)} symbols (min |corr| = {min_correlation})...")
        
        total = 0
        today = datetime.now().date()
        
        # Calculate pairwise correlations
        for i, sym_a in enumerate(symbols):
            for sym_b in symbols[i+1:]:  # Avoid duplicates and self-correlation
                corr = calculate_correlation(conn, sym_a, sym_b)
                
                if corr is None:
                    continue
                
                # Only save significant correlations
                if abs(corr) >= min_correlation:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            INSERT INTO stock_correlations (
                                symbol_a, symbol_b, period_days, correlation_coef, calculated_date
                            ) VALUES (%s, %s, 90, %s, %s)
                            ON CONFLICT (symbol_a, symbol_b, period_days, calculated_date)
                            DO UPDATE SET correlation_coef = EXCLUDED.correlation_coef
                        """, (sym_a, sym_b, corr, today))
                        conn.commit()
                    
                    total += 1
                    logger.info(f"  {sym_a} <-> {sym_b}: {corr:+.3f}")
            
            # Progress log
            if (i + 1) % 5 == 0:
                logger.info(f"Processed {i + 1}/{len(symbols)} symbols")
        
        logger.info(f"Saved {total} significant correlations (|corr| >= {min_correlation})")
    
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='Limit number of symbols')
    parser.add_argument('--min-correlation', type=float, default=0.5, 
                       help='Minimum absolute correlation to save')
    args = parser.parse_args()
    
    run(min_correlation=args.min_correlation, limit=args.limit)
