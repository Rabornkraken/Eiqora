#!/usr/bin/env python3
"""
Database migration: Add capital tracking columns to trigger_backtest_run table.
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_collection.db.connection import get_connection

def run_migration():
    """Add capital tracking columns."""
    print("🔄 Running migration: add_capital_tracking_columns")
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                print("  Adding columns to trigger_backtest_run...")
                cur.execute("""
                    ALTER TABLE trigger_backtest_run
                    ADD COLUMN IF NOT EXISTS starting_capital NUMERIC(12, 2),
                    ADD COLUMN IF NOT EXISTS final_capital NUMERIC(12, 2),
                    ADD COLUMN IF NOT EXISTS total_return_pct NUMERIC(8, 2),
                    ADD COLUMN IF NOT EXISTS max_drawdown_pct NUMERIC(8, 2);
                """)
                conn.commit()
                
                # Verify columns exist
                cur.execute("""
                    SELECT column_name, data_type
                    FROM information_schema.columns 
                    WHERE table_name = 'trigger_backtest_run' 
                    AND column_name IN ('starting_capital', 'final_capital', 'total_return_pct', 'max_drawdown_pct')
                    ORDER BY column_name;
                """)
                columns = cur.fetchall()
                
                print(f"\n✅ Migration complete! Added {len(columns)} columns:")
                for col_name, col_type in columns:
                    print(f"   - {col_name} ({col_type})")
                    
                return True
                
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)
