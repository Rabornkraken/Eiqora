#!/usr/bin/env python3
"""
Test script for daily trigger backtest.

Similar to test_trigger_backtest.py but uses daily bars instead of hourly.
"""

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
from eiqora_v2.live.backtest_daily_triggers import (
    run_daily_backtest,
    MAX_POSITIONS_DEFAULT,
    MAX_HOLD_DAYS_DEFAULT,
)


def summarize_run(run_id: str) -> None:
    """Display a summary of backtest results."""
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
                    max_drawdown_pct,
                    start_date,
                    end_date,
                    run_name,
                    parameters
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
             start_cap, final_cap, total_return, max_dd,
             start_date, end_date, backtest_run_name, parameters) = run_metrics

            # Calculate profit/loss in dollars
            start_val = float(start_cap) if start_cap else 10000.0
            final_val = float(final_cap) if final_cap else start_val
            profit_loss = final_val - start_val

            # Extract portfolio management params
            params = parameters or {}
            max_positions = params.get("max_positions", "N/A")
            max_hold_days = params.get("max_hold_days", "N/A")
            skipped_no_capital = params.get("skipped_no_capital", 0)
            skipped_max_positions = params.get("skipped_max_positions", 0)
            skipped_already_in_position = params.get("skipped_already_in_position", 0)
            max_concurrent = params.get("max_concurrent", "N/A")
            avg_concurrent = params.get("avg_concurrent", "N/A")

            # Count SKIPPED results
            cur.execute(
                """
                SELECT COUNT(*)
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome = 'SKIPPED'
                """,
                (run_id,),
            )
            skipped_total = cur.fetchone()[0] or 0

            print()
            print("DAILY TRIGGER BACKTEST RESULTS SUMMARY")
            print("=" * 60)
            print()
            print(f"Run Name: {backtest_run_name}")
            print(f"Period:   {start_date} to {end_date}")
            print("-" * 60)
            print()
            print("TRADE STATISTICS")
            print(f"   Signals Detected  : {total:,}")
            print(f"   Trades Executed   : {executed:,}")
            print(f"   Trades Skipped    : {skipped_total:,}")
            print(f"   Win Rate          : {float(wr):.1f}%" if wr is not None else "   Win Rate          : N/A")
            print(f"   Avg Gain per Trade: {float(avg_pnl):.2f}%" if avg_pnl is not None else "   Avg Gain per Trade: N/A")
            print()
            print("PORTFOLIO MANAGEMENT")
            print(f"   Max Positions     : {max_positions}")
            print(f"   Max Hold Days     : {max_hold_days}")
            print(f"   Peak Concurrent   : {max_concurrent}")
            print(f"   Avg Concurrent    : {avg_concurrent}")
            print(f"   Skip: No Capital  : {skipped_no_capital:,}")
            print(f"   Skip: Max Slots   : {skipped_max_positions:,}")
            print(f"   Skip: Already In  : {skipped_already_in_position:,}")
            print()
            print("CAPITAL PERFORMANCE (5% of capital/trade)")
            print(f"   Starting Capital  : ${start_val:,.2f}")
            print(f"   Final Capital     : ${final_val:,.2f}")
            print(f"   Net Profit/Loss   : ${profit_loss:+,.2f}")
            print(f"   Return on Capital : {float(total_return):+.1f}%" if total_return is not None else "   Return on Capital : N/A")
            print(f"   Max Drawdown      : {float(max_dd):.1f}%" if max_dd is not None else "   Max Drawdown      : N/A")
            print()
            print("BEST & WORST TRIGGERS")
            print(f"   Best Performer    : {best}")
            print(f"   Worst Performer   : {worst}")
            print()
            print("-" * 60)

            # 2. Detailed Breakdown (from JSON columns)
            cur.execute(
                """
                SELECT trigger_details, yearly_performance
                FROM trigger_backtest_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
            details = row[0] if row and row[0] else []
            yearly = row[1] if row and row[1] else []

    print()
    print("TRIGGER PERFORMANCE BREAKDOWN")
    print("=" * 85)
    print(f"{'Trigger':<35} {'Trades':<8} {'Win%':<8} {'Avg%':<8} {'Est. Net PnL':<12}")
    print("-" * 85)

    # Sort by Total PnL (descending)
    details.sort(key=lambda x: x.get('total_pnl_pct', 0), reverse=True)

    for d in details:
        print(f"{d['trigger_type']:<35} {d['count']:<8} {d['win_rate']:<7.1f}% {d['avg_pnl_pct']:+<7.2f}%")

    print("=" * 85)
    print()

    # Yearly Performance
    if yearly:
        print("YEARLY PERFORMANCE")
        print("=" * 85)
        print(f"{'Year':<6} {'Trades':<8} {'Win%':<8} {'Avg%':<8} {'Start Cap':<12} {'End Cap':<12} {'Return':<10}")
        print("-" * 85)

        for y in yearly:
            print(
                f"{y['year']:<6} "
                f"{y['trades']:<8} "
                f"{y['win_rate']:<7.1f}% "
                f"{y['avg_pnl_pct']:+<7.2f}% "
                f"${y['start_capital']:>10,.0f} "
                f"${y['end_capital']:>10,.0f} "
                f"{y['return_pct']:+.1f}%"
            )

        print("=" * 85)
        print()

    print("NOTE: Capital performance uses 5% of current capital per trade.")
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Daily trigger backtest test script")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--run-name", type=str, default="daily-test", help="Name for this run")
    parser.add_argument("--sl-mult", type=float, default=1.5, help="Stop loss ATR multiplier (default: 1.5)")
    parser.add_argument("--tp-mult", type=float, default=3.0, help="Take profit ATR multiplier (default: 3.0)")
    parser.add_argument("--starting-capital", type=float, default=10000.0, help="Starting capital (default: 10000)")
    parser.add_argument("--max-positions", type=int, default=MAX_POSITIONS_DEFAULT,
                        help=f"Max concurrent positions (default: {MAX_POSITIONS_DEFAULT})")
    parser.add_argument("--max-hold-days", type=int, default=MAX_HOLD_DAYS_DEFAULT,
                        help=f"Force-close NO_HIT after N days (default: {MAX_HOLD_DAYS_DEFAULT}, 0=unlimited)")
    parser.add_argument("--no-filter", action="store_true", help="Include all triggers (don't exclude weak ones)")
    parser.add_argument("--run-id", type=str, help="Summarize existing run id instead of running new backtest")
    parser.add_argument("--no-run", action="store_true", help="Skip run and only summarize (requires --run-id)")
    args = parser.parse_args()

    # Configure logging
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    run_id = args.run_id
    if not run_id and not args.no_run:
        excluded = set() if args.no_filter else None  # None = use defaults

        print(f"Running daily backtest from {args.start_date} to {args.end_date}...")
        print(f"Parameters: SL={args.sl_mult}x ATR, TP={args.tp_mult}x ATR, 5% capital/trade")
        print(f"Portfolio:  max_positions={args.max_positions}, max_hold_days={args.max_hold_days}")
        if not args.no_filter:
            print("Excluding weak triggers: daily_breakout, daily_macd_crossover")
        print()

        run_id = asyncio.run(
            run_daily_backtest(
                args.start_date,
                args.end_date,
                args.run_name,
                args.sl_mult,
                args.tp_mult,
                args.starting_capital,
                excluded,
                max_positions=args.max_positions,
                max_hold_days=args.max_hold_days,
            )
        )
        print(f"Run complete: {run_id}")

    if run_id:
        summarize_run(str(run_id))
    else:
        print("No run ID provided. Use --run-id to summarize an existing run.")


if __name__ == "__main__":
    main()
