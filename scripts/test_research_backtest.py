#!/usr/bin/env python3
"""
Test script for the research-backed daily trigger backtest.

Usage:
    # Default run
    python scripts/test_research_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 --run-name "research-v1"

    # Custom max hold periods
    python scripts/test_research_backtest.py \
      --start-date 2016-01-01 --end-date 2026-01-01 \
      --max-hold-mr 5 --max-hold-momentum 20

    # Summarize existing run
    python scripts/test_research_backtest.py \
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
from eiqora_v2.live.backtest_daily_research import (
    run_research_backtest,
    MEAN_REVERSION_TRIGGERS,
    MOMENTUM_TRIGGERS,
)


def summarize_run(run_id: str) -> None:
    """Display a summary of research backtest results."""
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

            # Extract research stats from parameters
            params = parameters or {}
            research_stats = params.get("research_stats", {})
            regime_breakdown = research_stats.get("regime_breakdown", [])
            ftd_checked = research_stats.get("ftd_checked", "N/A")
            ftd_filtered = research_stats.get("ftd_filtered", "N/A")
            stock_universe = research_stats.get("stock_universe", "N/A")
            skipped_etf_index = research_stats.get("skipped_etf_index", "N/A")
            exit_params = research_stats.get("exit_params", {})

            print()
            print("RESEARCH-BACKED DAILY BACKTEST RESULTS")
            print("=" * 60)
            print()
            print(f"Run Name: {backtest_run_name}")
            print(f"Period:   {start_date} to {end_date}")
            print("-" * 60)

            # Universe & Regime Stats
            print()
            print("UNIVERSE & REGIME STATS")
            print("=" * 48)
            print(f"   Stock Universe     : {stock_universe} symbols (ETFs/indexes excluded)")
            print(f"   Skipped (ETF/Idx)  : {skipped_etf_index}")

            if regime_breakdown:
                for rb in regime_breakdown:
                    regime = rb.get("regime", "?")
                    bars = rb.get("bars", 0)
                    label = {
                        "RANGE": "mean-reversion mode",
                        "TREND_UP": "all triggers mode",
                        "TREND_DOWN": "skipped - long-only",
                        "UNKNOWN": "all triggers mode",
                    }.get(regime, "")
                    print(f"   {regime:<18}: {bars:>8,} bars ({label})")
            print()

            # Exit parameters
            mr_params = exit_params.get("mean_reversion", {})
            mo_params = exit_params.get("momentum", {})
            if mr_params or mo_params:
                print("EXIT PARAMETERS")
                print("=" * 48)
                if mr_params:
                    print(
                        f"   Mean-Reversion    : SL={mr_params.get('sl_mult', '?')}x ATR, "
                        f"TP={mr_params.get('tp_mult', '?')}x ATR, "
                        f"Max Hold={mr_params.get('max_hold', '?')} days"
                    )
                if mo_params:
                    print(
                        f"   Momentum          : SL={mo_params.get('sl_mult', '?')}x ATR, "
                        f"TP={mo_params.get('tp_mult', '?')}x ATR, "
                        f"Max Hold={mo_params.get('max_hold', '?')} days"
                    )
                print()

            # Trade statistics
            print("TRADE STATISTICS")
            print("=" * 48)
            print(f"   Signals Detected  : {total:,}")
            print(f"   Trades Executed   : {executed:,}")
            if wr is not None:
                print(f"   Win Rate          : {float(wr):.1f}%")
            else:
                print("   Win Rate          : N/A")
            if avg_pnl is not None:
                print(f"   Avg Gain per Trade: {float(avg_pnl):.2f}%")
            else:
                print("   Avg Gain per Trade: N/A")
            print()

            # Capital performance
            print("CAPITAL PERFORMANCE (Fixed $500/trade)")
            print("=" * 48)
            print(f"   Starting Capital  : ${start_val:,.2f}")
            print(f"   Final Capital     : ${final_val:,.2f}")
            print(f"   Net Profit/Loss   : ${profit_loss:+,.2f}")
            if total_return is not None:
                print(f"   Return on Capital : {float(total_return):+.1f}%")
            else:
                print("   Return on Capital : N/A")
            if max_dd is not None:
                print(f"   Max Drawdown      : {float(max_dd):.1f}%")
            else:
                print("   Max Drawdown      : N/A")
            print()

            print("BEST & WORST TRIGGERS")
            print(f"   Best Performer    : {best}")
            print(f"   Worst Performer   : {worst}")
            print()
            print("-" * 60)

            # FTD filter stats
            print()
            print("FTD FILTER")
            print("=" * 48)
            if isinstance(ftd_checked, int) and ftd_checked > 0:
                pct = ftd_filtered / ftd_checked * 100
                print(f"   Checked           : {ftd_checked:,}")
                print(f"   Filtered          : {ftd_filtered:,} ({pct:.1f}%)")
            else:
                print(f"   Checked           : {ftd_checked}")
                print(f"   Filtered          : {ftd_filtered}")
            print()

            # Regime breakdown
            if regime_breakdown:
                print("REGIME PERFORMANCE")
                print("=" * 70)
                print(
                    f"{'Regime':<14} {'Bars':<10} {'Trades':<9} "
                    f"{'Win%':<8} {'Est. PnL':<12}"
                )
                print("-" * 70)
                for rb in regime_breakdown:
                    regime = rb.get("regime", "?")
                    bars = rb.get("bars", 0)
                    trades_count = rb.get("trades", 0)
                    rwin = rb.get("win_rate", 0.0)
                    est_pnl = rb.get("est_pnl", 0.0)
                    if trades_count > 0:
                        win_str = f"{rwin:.1f}%"
                        pnl_str = f"${est_pnl:+,.0f}"
                    else:
                        win_str = "N/A"
                        pnl_str = "$0 (sat out)" if regime == "TREND_DOWN" else "$0"
                    print(
                        f"{regime:<14} {bars:<10,} {trades_count:<9,} "
                        f"{win_str:<8} {pnl_str:<12}"
                    )
                print("=" * 70)
                print()

            # Detailed trigger breakdown from JSON columns
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

    # Split triggers by category
    mr_details = [
        d for d in details if d["trigger_type"] in MEAN_REVERSION_TRIGGERS
    ]
    mo_details = [
        d for d in details if d["trigger_type"] in MOMENTUM_TRIGGERS
    ]
    other_details = [
        d for d in details
        if d["trigger_type"] not in MEAN_REVERSION_TRIGGERS
        and d["trigger_type"] not in MOMENTUM_TRIGGERS
    ]

    COST_PER_TRADE = 2.50

    def _print_trigger_table(title: str, trigger_list: list[dict]) -> None:
        if not trigger_list:
            return
        trigger_list.sort(key=lambda x: x.get("total_pnl_pct", 0), reverse=True)
        print(f"TRIGGER PERFORMANCE ({title})")
        print("=" * 85)
        print(
            f"{'Trigger':<35} {'Trades':<8} {'Win%':<8} "
            f"{'Avg%':<8} {'Est. Net PnL':<12}"
        )
        print("-" * 85)
        for d in trigger_list:
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

    _print_trigger_table("Mean-Reversion", mr_details)
    _print_trigger_table("Momentum", mo_details)
    if other_details:
        _print_trigger_table("Other", other_details)

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
    print("      Mean-reversion: SL/TP=2x ATR, max 7 days. Momentum: SL=1.5x, TP=3x ATR, max 30 days.")
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Research-backed daily trigger backtest"
    )
    parser.add_argument(
        "--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, required=True, help="End date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--run-name", type=str, default="research-test",
        help="Name for this run",
    )
    parser.add_argument(
        "--starting-capital", type=float, default=10000.0,
        help="Starting capital (default: 10000)",
    )
    parser.add_argument(
        "--max-hold-mr", type=int, default=7,
        help="Max hold days for mean-reversion triggers (default: 7)",
    )
    parser.add_argument(
        "--max-hold-momentum", type=int, default=30,
        help="Max hold days for momentum triggers (default: 30)",
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

        print(f"Running research-backed daily backtest from {args.start_date} to {args.end_date}...")
        print(f"Parameters: $500/trade, stocks only (no ETFs/indexes)")
        print(f"Exit: MR=2x ATR SL/TP {args.max_hold_mr}d hold, Momentum=1.5x/3x ATR {args.max_hold_momentum}d hold")
        if not args.no_filter:
            print("Excluding weak triggers: daily_breakout, daily_macd_crossover")
        print()

        run_id = asyncio.run(
            run_research_backtest(
                args.start_date,
                args.end_date,
                args.run_name,
                args.starting_capital,
                excluded,
                max_hold_mr=args.max_hold_mr,
                max_hold_momentum=args.max_hold_momentum,
            )
        )
        print(f"Run complete: {run_id}")

    if run_id:
        summarize_run(str(run_id))
    else:
        print("No run ID provided. Use --run-id to summarize an existing run.")


if __name__ == "__main__":
    main()
