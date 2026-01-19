"""
Historical Backtest for Intraday Triggers.

Tests the 5 new intraday triggers on historical hourly data to validate:
1. Trigger detection logic
2. Hourly technical scoring
3. Analysis gate behavior
4. Trigger quality and frequency

Usage:
    python -m eiqora_v2.live.test_intraday_triggers --symbols AAPL,NVDA,META --days 7
"""

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import Any

import pytest
from eiqora_v2.live.trigger_monitor import TriggerMonitor, HOURLY_INTRADAY_TRIGGERS
from eiqora_v2.tools.hourly_indicators import score_hourly_technicals
from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
pytestmark = pytest.mark.skipif(
    os.getenv("EIQORA_RUN_INTRADAY_TESTS") != "1",
    reason="Intraday trigger backtest disabled (set EIQORA_RUN_INTRADAY_TESTS=1).",
)


async def get_historical_hours(symbols: list[str], days: int) -> list[tuple[str, datetime]]:
    """
    Get list of (symbol, datetime) pairs for testing.
    
    Fetches hourly bar times from last N days during market hours.
    """
    test_times = []
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            for symbol in symbols:
                cur.execute("""
                    SELECT DISTINCT datetime
                    FROM market_bar_hourly
                    WHERE symbol = %s
                      AND datetime >= NOW() - INTERVAL '%s days'
                      AND EXTRACT(HOUR FROM datetime AT TIME ZONE 'America/New_York') BETWEEN 10 AND 16
                      AND rsi_14 IS NOT NULL  -- Ensure indicators calculated
                    ORDER BY datetime DESC
                """, (symbol, days))
                
                hours = cur.fetchall()
                for (dt,) in hours:
                    test_times.append((symbol, dt))
    
    logger.info(f"Found {len(test_times)} historical hours to test across {len(symbols)} symbols")
    return test_times


async def test_triggers_at_time(
    monitor: TriggerMonitor,
    symbol: str,
    check_time: datetime
) -> dict[str, Any]:
    """
    Test trigger detection and scoring for a specific symbol at a specific time.
    
    Returns:
        dict with triggers, scores, and analysis results
    """
    try:
        # Detect triggers
        triggers = await monitor.check_hourly_technical_triggers(symbol, check_time)
        
        # Calculate hourly technical score
        hourly_score, hourly_breakdown = await score_hourly_technicals(symbol, check_time)
        
        # Filter to new intraday triggers
        new_triggers = [
            t for t in triggers 
            if t.trigger_type in {
                "vwap_reclaim", "hourly_rsi_divergence", "intraday_consolidation_break",
                "opening_range_breakout", "hourly_money_flow_surge"
            }
        ]
        
        # Check what would pass analysis gate
        passed_gate = [t for t in new_triggers if hourly_score >= 0.60]
        suppressed = [t for t in new_triggers if hourly_score < 0.60]
        
        return {
            "symbol": symbol,
            "check_time": check_time,
            "hourly_score": hourly_score,
            "hourly_breakdown": hourly_breakdown,
            "triggers_detected": len(new_triggers),
            "triggers_passed": len(passed_gate),
            "triggers_suppressed": len(suppressed),
            "trigger_types": [t.trigger_type for t in new_triggers],
            "passed_types": [t.trigger_type for t in passed_gate],
            "triggers": new_triggers,
        }
    
    except Exception as e:
        logger.error(f"Error testing {symbol} at {check_time}: {e}")
        return {
            "symbol": symbol,
            "check_time": check_time,
            "error": str(e),
        }


async def run_backtest(symbols: list[str], days: int) -> dict[str, Any]:
    """
    Run historical backtest on intraday triggers.
    
    Returns summary statistics and examples.
    """
    logger.info("=" * 80)
    logger.info("INTRADAY TRIGGER HISTORICAL BACKTEST")
    logger.info("=" * 80)
    logger.info(f"Symbols: {', '.join(symbols)}")
    logger.info(f"Lookback: {days} days")
    logger.info("=" * 80)
    
    # Get test times
    test_times = await get_historical_hours(symbols, days)
    
    if not test_times:
        logger.error("No historical data found!")
        return {}
    
    # Initialize monitor
    monitor = TriggerMonitor()
    
    # Run tests
    results = []
    for i, (symbol, check_time) in enumerate(test_times):
        if i % 50 == 0:
            logger.info(f"Progress: {i}/{len(test_times)} hours tested...")
        
        result = await test_triggers_at_time(monitor, symbol, check_time)
        if not result.get("error"):
            results.append(result)
    
    # Aggregate statistics
    stats = analyze_results(results)
    
    # Print summary
    print_summary(stats, results)
    
    return stats


def analyze_results(results: list[dict]) -> dict[str, Any]:
    """Aggregate and analyze backtest results."""
    
    total_hours = len(results)
    
    # Trigger statistics
    hours_with_triggers = sum(1 for r in results if r["triggers_detected"] > 0)
    total_triggers = sum(r["triggers_detected"] for r in results)
    triggers_passed = sum(r["triggers_passed"] for r in results)
    triggers_suppressed = sum(r["triggers_suppressed"] for r in results)
    
    # By type
    trigger_type_counts = Counter()
    trigger_type_passed = Counter()
    
    for r in results:
        for t_type in r["trigger_types"]:
            trigger_type_counts[t_type] += 1
        for t_type in r["passed_types"]:
            trigger_type_passed[t_type] += 1
    
    # Hourly score distribution
    scores = [r["hourly_score"] for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0
    scores_above_threshold = sum(1 for s in scores if s >= 0.60)
    
    # Find best examples (high score + trigger detected)
    examples = sorted(
        [r for r in results if r["triggers_detected"] > 0],
        key=lambda x: x["hourly_score"],
        reverse=True
    )[:10]
    
    return {
        "total_hours": total_hours,
        "hours_with_triggers": hours_with_triggers,
        "trigger_rate": hours_with_triggers / total_hours if total_hours else 0,
        "total_triggers": total_triggers,
        "triggers_passed": triggers_passed,
        "triggers_suppressed": triggers_suppressed,
        "pass_rate": triggers_passed / total_triggers if total_triggers else 0,
        "trigger_type_counts": dict(trigger_type_counts),
        "trigger_type_passed": dict(trigger_type_passed),
        "avg_hourly_score": avg_score,
        "scores_above_threshold": scores_above_threshold,
        "threshold_rate": scores_above_threshold / total_hours if total_hours else 0,
        "top_examples": examples,
    }


def print_summary(stats: dict, results: list[dict]):
    """Print formatted summary of backtest results."""
    
    print("\n" + "=" * 80)
    print("BACKTEST RESULTS SUMMARY")
    print("=" * 80)
    
    print(f"\n📊 Overall Statistics:")
    print(f"  Total hours tested: {stats['total_hours']:,}")
    print(f"  Hours with triggers: {stats['hours_with_triggers']:,} ({stats['trigger_rate']:.1%})")
    print(f"  Total triggers detected: {stats['total_triggers']:,}")
    print(f"  Triggers passed gate: {stats['triggers_passed']:,} ({stats['pass_rate']:.1%})")
    print(f"  Triggers suppressed: {stats['triggers_suppressed']:,}")
    
    print(f"\n🎯 Trigger Detection by Type:")
    for t_type, count in sorted(stats['trigger_type_counts'].items(), key=lambda x: -x[1]):
        passed = stats['trigger_type_passed'].get(t_type, 0)
        pass_pct = (passed / count * 100) if count else 0
        print(f"  {t_type:30} {count:4} detected | {passed:4} passed ({pass_pct:.0f}%)")
    
    print(f"\n📈 Hourly Score Analysis:")
    print(f"  Average hourly score: {stats['avg_hourly_score']:.3f}")
    print(f"  Hours above threshold (≥0.60): {stats['scores_above_threshold']:,} ({stats['threshold_rate']:.1%})")
    
    print(f"\n🌟 Top 5 Examples (Highest Scores with Triggers):")
    for i, ex in enumerate(stats['top_examples'][:5], 1):
        print(f"\n  {i}. {ex['symbol']} @ {ex['check_time']}")
        print(f"     Hourly Score: {ex['hourly_score']:.3f}")
        print(f"     Triggers: {', '.join(ex['trigger_types'])}")
        print(f"     Top breakdown: {sorted(ex['hourly_breakdown'].items(), key=lambda x: -x[1])[:3]}")
    
    print("\n" + "=" * 80)


async def main():
    """Run backtest with command-line arguments."""
    parser = argparse.ArgumentParser(description="Backtest new intraday triggers on historical data")
    parser.add_argument("--symbols", type=str, default="AAPL,NVDA,META", help="Comma-separated symbols")
    parser.add_argument("--days", type=int, default=7, help="Days of history to test")
    
    args = parser.parse_args()
    
    symbols = args.symbols.split(",")
    
    stats = await run_backtest(symbols, args.days)
    
    if stats:
        logger.info("✅ Backtest complete!")
    else:
        logger.error("❌ Backtest failed!")


if __name__ == "__main__":
    asyncio.run(main())
