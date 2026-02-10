#!/usr/bin/env python3
"""
Test script for regime-filtered daily trigger backtest.

Runs the regime backtest and displays results with regime breakdown.
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
from eiqora_v2.live.backtest_daily_regime import run_regime_backtest


def summarize_run(run_id: str) -> None:
    """Display a summary of regime backtest results."""
    print(f"\nAnalysis for Run ID: {run_id}")
    print("=" * 60)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Fetch High-Level Run Metrics
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
                    run_name
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
             start_date, end_date, backtest_run_name) = run_metrics

            start_val = float(start_cap) if start_cap else 10000.0
            final_val = float(final_cap) if final_cap else start_val
            profit_loss = final_val - start_val

            print()
            print("REGIME-FILTERED DAILY BACKTEST RESULTS SUMMARY")
            print("=" * 60)
            print()
            print(f"Run Name: {backtest_run_name}")
            print(f"Period:   {start_date} to {end_date}")
            print("-" * 60)
            print()
            print("TRADE STATISTICS")
            print(f"   Signals Detected  : {total:,}")
            print(f"   Trades Executed   : {executed:,}")
            print(f"   Win Rate          : {float(wr):.1f}%" if wr is not None else "   Win Rate          : N/A")
            print(f"   Avg Gain per Trade: {float(avg_pnl):.2f}%" if avg_pnl is not None else "   Avg Gain per Trade: N/A")
            print()
            print("CAPITAL PERFORMANCE (Regime-Based: BULL=$500, SIDEWAYS=$250, BEAR=$0)")
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
                SELECT trigger_details, yearly_performance, parameters
                FROM trigger_backtest_run
                WHERE run_id = %s
                """,
                (run_id,),
            )
            row = cur.fetchone()
            details = row[0] if row and row[0] else []
            yearly = row[1] if row and row[1] else []
            params = row[2] if row and row[2] else {}
            regime_breakdown = params.get("regime_breakdown", []) if isinstance(params, dict) else []

    # Regime Breakdown
    if regime_breakdown:
        print()
        print("REGIME BREAKDOWN")
        print("=" * 60)
        print(f"{'Regime':<12} {'Days':<8} {'Trades':<9} {'Win%':<8} {'Est. PnL':<12}")
        print("-" * 60)

        for rb in regime_breakdown:
            regime = rb.get("regime", "?")
            days = rb.get("days", 0)
            trades_count = rb.get("trades", 0)
            rwin = rb.get("win_rate", 0.0)
            est_pnl = rb.get("est_pnl", 0.0)

            if trades_count > 0:
                win_str = f"{rwin:.1f}%"
                pnl_str = f"${est_pnl:+,.0f}"
            else:
                win_str = "N/A"
                pnl_str = "$0 (sat out)"

            print(f"{regime:<12} {days:<8,} {trades_count:<9,} {win_str:<8} {pnl_str:<12}")

        print("=" * 60)
        print()

    print()
    print("TRIGGER PERFORMANCE BREAKDOWN")
    print("=" * 85)
    print(f"{'Trigger':<35} {'Trades':<8} {'Win%':<8} {'Avg%':<8} {'Est. Net PnL':<12}")
    print("-" * 85)

    # Sort by Total PnL (descending)
    details.sort(key=lambda x: x.get('total_pnl_pct', 0), reverse=True)

    COST_PER_TRADE = 2.50

    for d in details:
        gross_profit = d['count'] * 500.0 * (d['avg_pnl_pct'] / 100.0)
        total_costs = d['count'] * COST_PER_TRADE
        net_profit = gross_profit - total_costs

        profit_str = f"${net_profit:+,.0f}"
        print(f"{d['trigger_type']:<35} {d['count']:<8} {d['win_rate']:<7.1f}% {d['avg_pnl_pct']:+<7.2f}% {profit_str:<12}")

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

    print("NOTE: Capital performance uses regime-based position sizing (BULL=$500, SIDEWAYS=$250, BEAR=$0).")
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Regime-filtered daily trigger backtest test script")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--run-name", type=str, default="regime-test", help="Name for this run")
    parser.add_argument("--sl-mult", type=float, default=1.5, help="Stop loss ATR multiplier (default: 1.5)")
    parser.add_argument("--tp-mult", type=float, default=3.0, help="Take profit ATR multiplier (default: 3.0)")
    parser.add_argument("--starting-capital", type=float, default=10000.0, help="Starting capital (default: 10000)")
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
        excluded = set() if args.no_filter else None

        print(f"Running regime-filtered backtest from {args.start_date} to {args.end_date}...")
        print(f"Parameters: SL={args.sl_mult}x ATR, TP={args.tp_mult}x ATR, regime-based sizing")
        if not args.no_filter:
            print("Excluding weak triggers: daily_breakout, daily_macd_crossover")
        print()

        run_id = asyncio.run(
            run_regime_backtest(
                args.start_date,
                args.end_date,
                args.run_name,
                args.sl_mult,
                args.tp_mult,
                args.starting_capital,
                excluded,
            )
        )
        print(f"Run complete: {run_id}")

    if run_id:
        summarize_run(str(run_id))
    else:
        print("No run ID provided. Use --run-id to summarize an existing run.")


if __name__ == "__main__":
    main()
