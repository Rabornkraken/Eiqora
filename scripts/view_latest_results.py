#!/usr/bin/env python3
import sys
import os
from decimal import Decimal

# Add root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_collection.db.connection import get_connection

def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Get latest run with capital metrics
            cur.execute("""
                SELECT run_id, run_name, started_at, completed_at, 
                       starting_capital, final_capital, total_return_pct, max_drawdown_pct
                FROM trigger_backtest_run
                ORDER BY started_at DESC LIMIT 1
            """)
            row = cur.fetchone()
            if not row:
                print("No runs found.")
                return

            run_id, name, start, end, start_cap, final_cap, ret_pct, dd_pct = row
            
            print(f"\n📊 RESULTS FOR RUN: {name}")
            print(f"   ID: {run_id}")
            print(f"   Started: {start}")
            
            if not end:
                print("   Status: ⏳ IN PROGRESS (Stats incomplete)")
            else:
                print(f"   Status: ✅ COMPLETED at {end}")
            
            if start_cap:
                start_val = float(start_cap)
                final_val = float(final_cap) if final_cap else start_val
                pnl = final_val - start_val
                print(f"\n💰 CAPITAL PERFORMANCE (Net of Fees)")
                print(f"   Start:     ${start_val:,.2f}")
                print(f"   Final:     ${final_val:,.2f}")
                print(f"   Net PnL:   ${pnl:+,.2f}")
                if ret_pct is not None: print(f"   Return:    {float(ret_pct):+.2f}%")
                if dd_pct is not None: print(f"   Max Drawdown: {float(dd_pct):.2f}%")
            
            # Get trigger breakdown
            cur.execute("SELECT trigger_details FROM trigger_backtest_run WHERE run_id=%s", (run_id,))
            details_row = cur.fetchone()
            details = details_row[0] if details_row and details_row[0] else []
            
            if not details:
                if not end:
                    print("\n   [!] Backtest is still running. Detailed stats generally appear at completion.")
                else:
                    print("\n   [!] No detail records found.")
                return

            print("\n📈 TRIGGER BREAKDOWN (Sorted by Net Profit)")
            print(f"{'Trigger':<35} {'Count':<8} {'Win%':<8} {'Avg%':<8} {'Est. Net PnL':<15}")
            print("-" * 80)
            
            # Calculate Net PnL for sorting
            # Assumptions: Fixed $500 size, Costs $2.50/trade
            FIXED_SIZE = 500.0
            COST_PER_TRADE = 2.50
            
            enriched = []
            for d in details:
                count = d['count']
                avg_pct = d['avg_pnl_pct']
                gross = count * FIXED_SIZE * (avg_pct / 100.0)
                costs = count * COST_PER_TRADE
                net = gross - costs
                d['net_pnl'] = net
                enriched.append(d)
                
            # Sort by Net PnL (descending)
            enriched.sort(key=lambda x: x['net_pnl'], reverse=True)
            
            total_est_pnl = 0
            for d in enriched:
                total_est_pnl += d['net_pnl']
                print(f"{d['trigger_type']:<35} {d['count']:<8} {d['win_rate']:<8} {d['avg_pnl_pct']:<8} ${d['net_pnl']:+,.0f}")
                
            print("-" * 80)
            print(f"Total Est Net PnL from Triggers: ${total_est_pnl:,.0f}")
            print(f"Note: Costs = $2.50 per trade ($2 comm + $0.50 slippage)")

if __name__ == "__main__":
    main()
