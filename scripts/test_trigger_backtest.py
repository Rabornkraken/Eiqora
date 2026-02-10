#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    prepare_trigger_detail,
)
from eiqora_v2.live import backtest_triggers_only
from eiqora_v2.live.backtest_triggers_only import (
    build_hourly_bars_query,
    run_trigger_only_backtest,
)


def summarize_run(run_id: str) -> None:
    print(f"\nAnalysis for Run ID: {run_id}")
    print("=" * 60)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Fetch High-Level Run Metrics from trigger_backtest_run
            cur.execute(
                """
                SELECT
                    total_triggers,
                    executed_trades,
                    win_rate,
                    total_pnl_pct,
                    avg_pnl_pct,
                    best_trigger_type,
                    worst_trigger_type,
                    starting_capital,
                    final_capital,
                    total_return_pct,
                    max_drawdown_pct
                FROM trigger_backtest_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            run_metrics = cur.fetchone()
            
            if not run_metrics:
                print("No run record found in trigger_backtest_run.")
                return

            (total, executed, wr, total_pnl, avg_pnl, best, worst,
             start_cap, final_cap, total_return, max_dd) = run_metrics
            
            # Calculate profit/loss in dollars
            start_val = float(start_cap) if start_cap else 10000.0
            final_val = float(final_cap) if final_cap else start_val
            profit_loss = final_val - start_val
            
            print()
            print("📊 BACKTEST RESULTS SUMMARY")
            print("=" * 60)
            print()
            print("🎯 TRADE STATISTICS")
            print(f"   Signals Detected  : {total:,}")
            print(f"   Trades Executed   : {executed:,}")
            print(f"   Win Rate          : {float(wr):.1f}%" if wr is not None else "   Win Rate          : N/A")
            print(f"   Avg Gain per Trade: {float(avg_pnl):.2f}%" if avg_pnl is not None else "   Avg Gain per Trade: N/A")
            print()
            print("💰 CAPITAL PERFORMANCE (5% of capital/trade)")
            print(f"   Starting Capital  : ${start_val:,.2f}")
            print(f"   Final Capital     : ${final_val:,.2f}")
            print(f"   Net Profit/Loss   : ${profit_loss:+,.2f}")
            print(f"   Return on Capital : {float(total_return):+.1f}%" if total_return is not None else "   Return on Capital : N/A")
            print(f"   Max Drawdown      : {float(max_dd):.1f}%" if max_dd is not None else "   Max Drawdown      : N/A")
            print()
            print("🏆 BEST & WORST TRIGGERS")
            print(f"   Best Performer    : {best}")
            print(f"   Worst Performer   : {worst}")
            print()
            print("-" * 60)

            # 2. Detailed Breakdown (from JSON column)
            cur.execute(
                """
                SELECT trigger_details
                FROM trigger_backtest_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
            details = row[0] if row and row[0] else []

    print()
    print("📈 TRIGGER PERFORMANCE BREAKDOWN")
    print("=" * 85)
    print(f"{'Trigger':<32} {'Trades':<8} {'Win%':<8} {'Avg%':<8} {'Est. Net PnL':<12}")
    print("-" * 85)
    
    # Sort by Total PnL (descending)
    details.sort(key=lambda x: x.get('total_pnl_pct', 0), reverse=True)
    
    for d in details:
        profit_str = ""
        print(f"{d['trigger_type']:<32} {d['count']:<8} {d['win_rate']:<7.1f}% {d['avg_pnl_pct']:+<7.2f}% {profit_str:<12}")

    print("=" * 85)
    print()
    print("NOTE: Capital performance uses 5% of current capital per trade.")
    print()


def smoke_assertions() -> None:
    entry = 100.0
    atr14 = 2.0
    stop_loss, take_profit = compute_atr_brackets(entry, atr14)
    assert stop_loss == 97.0
    assert take_profit == 106.0

    bar_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [(bar_time, 107.0, 99.0, 106.5)]
    outcome = resolve_outcome(bar_time, entry, stop_loss, take_profit, bars)
    assert outcome["outcome"] == "TP_HIT"

    assert prepare_trigger_detail({"a": 1}) is not None

    query, params = build_hourly_bars_query("2025-05-01", "2025-06-01", None)
    assert "datetime::date >= %s" in query
    assert "datetime::date <= %s" in query
    assert params[0] == "2025-05-01"
    assert params[1] == "2025-06-01"

    assert hasattr(backtest_triggers_only, "format_trigger_log")
    line = backtest_triggers_only.format_trigger_log("AAPL", "vwap_reclaim", bar_time)
    assert "AAPL" in line
    assert "vwap_reclaim" in line


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger-only backtest smoke script")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-name", type=str, default="trigger-only-smoke")
    parser.add_argument("--start-date", type=str)
    parser.add_argument("--end-date", type=str)
    parser.add_argument("--run-id", type=str, help="Summarize existing run id")
    parser.add_argument("--no-run", action="store_true", help="Skip run and only summarize")
    parser.add_argument("--sl-mult", type=float, default=1.5, help="Stop loss ATR multiplier (default: 1.5)")
    parser.add_argument("--tp-mult", type=float, default=3.0, help="Take profit ATR multiplier (default: 3.0)")
    parser.add_argument("--risk-pct", type=float, default=5.0, help="Risk percentage per trade (default: 5.0)")
    args = parser.parse_args()

    smoke_assertions()

    run_id = args.run_id
    if not run_id and not args.no_run:
        run_id = asyncio.run(
            run_trigger_only_backtest(
                args.days,
                args.limit,
                args.run_name,
                args.start_date,
                args.end_date,
                args.sl_mult,
                args.tp_mult,
                10000.0,  # starting_capital
                args.risk_pct,
            )
        )
        print(f"Run complete: {run_id}")

    if run_id:
        summarize_run(run_id)


if __name__ == "__main__":
    main()
