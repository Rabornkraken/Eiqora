#!/usr/bin/env python3
"""
Database migration: Increase precision for ALL capital tracking columns.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_collection.db.connection import get_connection

def run_migration():
    """Increase all capital column precision."""
    print("🔄 Running migration: increase_all_capital_precision")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                print("  Increasing precision for all capital columns...")
                cur.execute("""
                    ALTER TABLE trigger_backtest_run
                    ALTER COLUMN starting_capital TYPE NUMERIC(20, 2),
                    ALTER COLUMN final_capital TYPE NUMERIC(20, 2),
                    ALTER COLUMN total_return_pct TYPE NUMERIC(12, 2),
                    ALTER COLUMN max_drawdown_pct TYPE NUMERIC(8, 2);
                """)
                conn.commit()
                
                print("\n✅ Migration complete!")
                print("  - starting_capital: up to $1 quadrillion")
                print("  - final_capital: up to $1 quadrillion") 
                print("  - total_return_pct: up to 1 billion %")
                print("  - max_drawdown_pct: up to 1 million %")
                return True
                
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
