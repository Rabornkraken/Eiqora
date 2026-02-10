#!/usr/bin/env python3
"""
Test script for the confluence-filtered daily trigger backtest.

Usage:
    # Default (confluence=2, max_hold=20)
    python scripts/test_confluence_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 --run-name "confluence-v1"

    # Strict confluence
    python scripts/test_confluence_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 --min-confluence 3

    # No confluence (baseline with new triggers + max hold only)
    python scripts/test_confluence_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 --min-confluence 0

    # Custom max hold
    python scripts/test_confluence_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 --max-hold-days 10

    # Summarize existing run
    python scripts/test_confluence_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 --run-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.db.connection import get_connection
from eiqora_v2.live.backtest_daily_confluence import run_daily_confluence_backtest


def summarize_run(run_id: str) -> None:
    """Display a summary of confluence backtest results."""
    print(f"\nAnalysis for Run ID: {run_id}")
    print("=" * 60)

    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Fetch high-level run metrics
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

            (
                total, executed, wr, total_pnl, avg_pnl, best, worst,
                start_cap, final_cap, total_return, max_dd,
                start_date, end_date, backtest_run_name, parameters,
            ) = run_metrics

            start_val = float(start_cap) if start_cap else 10000.0
            final_val = float(final_cap) if final_cap else start_val
            profit_loss = final_val - start_val

            # Extract confluence stats from parameters
            params = parameters or {}
            confluence_stats = params.get("confluence_stats", {})
            triggers_detected = confluence_stats.get("triggers_detected", "N/A")
            confluence_passed = confluence_stats.get("confluence_passed", "N/A")
            confluence_filtered = confluence_stats.get("confluence_filtered", "N/A")
            min_confluence = confluence_stats.get("min_confluence", params.get("min_confluence", "N/A"))
            max_hold_days = confluence_stats.get("max_hold_days", params.get("max_hold_days", "N/A"))

            print()
            print("CONFLUENCE-FILTERED DAILY BACKTEST RESULTS")
            print("=" * 60)
            print()
            print(f"Run Name: {backtest_run_name}")
            print(f"Period:   {start_date} to {end_date}")
            print("-" * 60)

            # Confluence filter stats
            print()
            print("CONFLUENCE FILTER STATS")
            print("=" * 48)
            if isinstance(triggers_detected, int):
                print(f"   Triggers Detected  : {triggers_detected:,}")
                print(f"   Passed Confluence  : {confluence_passed:,}  ({confluence_passed / triggers_detected * 100:.1f}%)" if triggers_detected > 0 else f"   Passed Confluence  : {confluence_passed:,}")
                print(f"   Filtered Out       : {confluence_filtered:,}  ({confluence_filtered / triggers_detected * 100:.1f}%)" if triggers_detected > 0 else f"   Filtered Out       : {confluence_filtered:,}")
            else:
                print(f"   Triggers Detected  : {triggers_detected}")
                print(f"   Passed Confluence  : {confluence_passed}")
                print(f"   Filtered Out       : {confluence_filtered}")
            print(f"   Min Confluence     : {min_confluence} of 4")
            print(f"   Max Hold Days      : {max_hold_days}")
            print()

            # Trade statistics
            print("TRADE STATISTICS")
            print(f"   Signals Detected  : {total:,}")
            print(f"   Trades Executed   : {executed:,}")
            print(f"   Win Rate          : {float(wr):.1f}%" if wr is not None else "   Win Rate          : N/A")
            print(f"   Avg Gain per Trade: {float(avg_pnl):.2f}%" if avg_pnl is not None else "   Avg Gain per Trade: N/A")
            print()

            # Capital performance
            print("CAPITAL PERFORMANCE (Fixed $500/trade)")
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

            # Detailed breakdown from JSON columns
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

    # Trigger performance breakdown
    print()
    print("TRIGGER PERFORMANCE BREAKDOWN")
    print("=" * 85)
    print(f"{'Trigger':<35} {'Trades':<8} {'Win%':<8} {'Avg%':<8} {'Est. Net PnL':<12}")
    print("-" * 85)

    details.sort(key=lambda x: x.get("total_pnl_pct", 0), reverse=True)

    COST_PER_TRADE = 2.50

    for d in details:
        gross_profit = d["count"] * 500.0 * (d["avg_pnl_pct"] / 100.0)
        total_costs = d["count"] * COST_PER_TRADE
        net_profit = gross_profit - total_costs
        profit_str = f"${net_profit:+,.0f}"
        print(
            f"{d['trigger_type']:<35} "
            f"{d['count']:<8} "
            f"{d['win_rate']:<7.1f}% "
            f"{d['avg_pnl_pct']:+<7.2f}% "
            f"{profit_str:<12}"
        )

    print("=" * 85)
    print()

    # Yearly performance
    if yearly:
        print("YEARLY PERFORMANCE")
        print("=" * 85)
        print(
            f"{'Year':<6} {'Trades':<8} {'Win%':<8} {'Avg%':<8} "
            f"{'Start Cap':<12} {'End Cap':<12} {'Return':<10}"
        )
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

    print("NOTE: Capital performance uses fixed $500/trade position sizing.")
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Confluence-filtered daily trigger backtest"
    )
    parser.add_argument(
        "--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, required=True, help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--run-name", type=str, default="confluence-test",
        help="Name for this run",
    )
    parser.add_argument(
        "--sl-mult", type=float, default=1.5,
        help="Stop loss ATR multiplier (default: 1.5)",
    )
    parser.add_argument(
        "--tp-mult", type=float, default=3.0,
        help="Take profit ATR multiplier (default: 3.0)",
    )
    parser.add_argument(
        "--starting-capital", type=float, default=10000.0,
        help="Starting capital (default: 10000)",
    )
    parser.add_argument(
        "--min-confluence", type=int, default=2,
        help="Minimum confluence score 0-4 (default: 2)",
    )
    parser.add_argument(
        "--max-hold-days", type=int, default=20,
        help="Max holding period in days, 0=unlimited (default: 20)",
    )
    parser.add_argument(
        "--no-filter", action="store_true",
        help="Include all triggers (don't exclude weak ones)",
    )
    parser.add_argument(
        "--run-id", type=str,
        help="Summarize existing run ID instead of running new backtest",
    )
    parser.add_argument(
        "--no-run", action="store_true",
        help="Skip run and only summarize (requires --run-id)",
    )
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

        max_hold = args.max_hold_days if args.max_hold_days > 0 else None

        print(f"Running confluence-filtered daily backtest from {args.start_date} to {args.end_date}...")
        print(f"Parameters: SL={args.sl_mult}x ATR, TP={args.tp_mult}x ATR, $500/trade")
        print(f"Confluence: min_score={args.min_confluence}/4, max_hold={max_hold} days")
        if not args.no_filter:
            print("Excluding weak triggers: daily_breakout, daily_macd_crossover")
        print()

        run_id = asyncio.run(
            run_daily_confluence_backtest(
                args.start_date,
                args.end_date,
                args.run_name,
                args.sl_mult,
                args.tp_mult,
                args.starting_capital,
                excluded,
                min_confluence=args.min_confluence,
                max_hold_days=max_hold,
            )
        )
        print(f"Run complete: {run_id}")

    if run_id:
        summarize_run(str(run_id))
    else:
        print("No run ID provided. Use --run-id to summarize an existing run.")


if __name__ == "__main__":
    main()
