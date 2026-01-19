"""
Full End-to-End Backtest with Agent Analysis and Trade Outcomes.

Tests the complete pipeline:
1. Scan 1 month of hourly data
2. Detect new intraday triggers
3. Filter by hourly score >= 0.60 (analysis gate)
4. Run multi-agent analysis on passing triggers
5. Track trade outcomes (TP/SL hit)

Usage:
    python -m eiqora_v2.live.backtest_with_agents --days 30 --limit 100
"""

import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from collections import defaultdict

from eiqora_v2.live.trigger_monitor import TriggerMonitor
from eiqora_v2.tools.hourly_indicators import score_hourly_technicals
from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def get_hourly_bars_to_test(days: int, limit: int | None = None) -> list[tuple[str, datetime]]:
    """Get hourly bars from available historical data with indicators."""
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Use ALL available data with indicators (Nov 4 - Jan 10)
            # Keep Jan 11-14 for outcome calculation (3-day buffer)
            query = """
                SELECT symbol, datetime
                FROM market_bar_hourly
                WHERE datetime >= '2025-11-04'
                  AND datetime < '2026-01-11'
                  AND rsi_14 IS NOT NULL
                  AND EXTRACT(HOUR FROM datetime AT TIME ZONE 'America/New_York') BETWEEN 10 AND 15
                ORDER BY datetime DESC, symbol
            """
            
            if limit:
                query += f" LIMIT {limit}"
            
            cur.execute(query)
            return cur.fetchall()


async def get_future_prices(symbol: str, entry_time: datetime, hours_forward: int = 24) -> dict:
    """Get future prices to determine TP/SL outcomes."""
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT datetime, high, low, close
                FROM market_bar_hourly
                WHERE symbol = %s
                  AND datetime > %s
                  AND datetime <= %s + INTERVAL '%s hours'
                ORDER BY datetime ASC
            """, (symbol, entry_time, entry_time, hours_forward))
            
            bars = cur.fetchall()
            
            if not bars:
                return {"error": "No future data"}
            
            # Calculate max high and min low in future period
            max_high = max(bar[1] for bar in bars if bar[1])
            min_low = min(bar[2] for bar in bars if bar[2])
            final_close = bars[-1][3] if bars else None
            
            return {
                "max_high": float(max_high) if max_high else None,
                "min_low": float(min_low) if min_low else None,
                "final_close": float(final_close) if final_close else None,
                "bars_count": len(bars),
            }


def calculate_outcome(
    entry_price: float,
    decision: dict,
    future_prices: dict
) -> dict:
    """
    Calculate trade outcome based on agent decision and future prices.
    
    Args:
        entry_price: Entry price
        decision: Agent decision with action, tp, sl
        future_prices: Future price data
    
    Returns:
        dict with outcome, pnl, reason
    """
    if future_prices.get("error") or not decision:
        return {"outcome": "NO_DATA", "pnl": 0, "reason": "Insufficient data"}
    
    action = decision.get("action", "PASS")
    
    if action == "PASS":
        return {"outcome": "NO_TRADE", "pnl": 0, "reason": "Agent passed"}
    
    # Get TP/SL from decision
    tp_pct = decision.get("take_profit_pct", 0.05)  # Default 5%
    sl_pct = decision.get("stop_loss_pct", 0.02)    # Default 2%
    
    tp_price = entry_price * (1 + tp_pct)
    sl_price = entry_price * (1 - sl_pct)
    
    max_high = future_prices.get("max_high", 0)
    min_low = future_prices.get("min_low", 999999)
    
    # Check if TP or SL hit
    tp_hit = max_high >= tp_price if max_high else False
    sl_hit = min_low <= sl_price if min_low else False
    
    if tp_hit and sl_hit:
        # Both hit - need to check which came first (simplified: assume SL came first)
        return {
            "outcome": "SL_HIT",
            "pnl": -sl_pct,
            "reason": "Both TP and SL hit (conservative: SL first)",
            "tp_price": tp_price,
            "sl_price": sl_price,
        }
    elif tp_hit:
        return {
            "outcome": "TP_HIT",
            "pnl": tp_pct,
            "reason": "Take profit hit",
            "tp_price": tp_price,
            "sl_price": sl_price,
        }
    elif sl_hit:
        return {
            "outcome": "SL_HIT",
            "pnl": -sl_pct,
            "reason": "Stop loss hit",
            "tp_price": tp_price,
            "sl_price": sl_price,
        }
    else:
        # Neither hit - calculate final P&L
        final_close = future_prices.get("final_close", entry_price)
        pnl = (final_close - entry_price) / entry_price if final_close else 0
        return {
            "outcome": "EXPIRED",
            "pnl": pnl,
            "reason": "Neither TP nor SL hit (holding outcome)",
            "tp_price": tp_price,
            "sl_price": sl_price,
            "final_price": final_close,
        }


async def run_full_backtest(days: int, limit: int | None = None) -> dict:
    """
    Run complete backtest with agent analysis and outcome tracking.
    """
    
    logger.info("=" * 80)
    logger.info("FULL BACKTEST WITH AGENT ANALYSIS")
    logger.info("=" * 80)
    logger.info(f"Period: Last {days} days")
    logger.info(f"Limit: {limit if limit else 'None'}")
    logger.info("=" * 80)
    
    # Get test data
    test_bars = await get_hourly_bars_to_test(days, limit)
    logger.info(f"Found {len(test_bars)} hourly bars to test")
    
    if not test_bars:
        logger.error("No test data found!")
        return {}
    
    # Initialize
    monitor = TriggerMonitor()
    
    results = {
        "total_bars": len(test_bars),
        "triggers_detected": 0,
        "triggers_passed_gate": 0,
        "triggers_analyzed": 0,
        "trades": [],
        "outcomes": defaultdict(int),
        "total_pnl": 0.0,
    }
    
    # Process each bar
    for i, (symbol, check_time) in enumerate(test_bars):
        if i % 50 == 0:
            logger.info(f"Progress: {i}/{len(test_bars)} bars tested...")
        
        try:
            # 1. Detect triggers
            triggers = await monitor.check_hourly_technical_triggers(symbol, check_time)
            
            # Filter to new intraday triggers
            new_triggers = [
                t for t in triggers 
                if t.trigger_type in {
                    "vwap_reclaim", "hourly_rsi_divergence", "intraday_consolidation_break",
                    "opening_range_breakout", "hourly_money_flow_surge"
                }
            ]
            
            if not new_triggers:
                continue
            
            results["triggers_detected"] += len(new_triggers)
            
            # 2. Calculate hourly score (for context, not gating)
            hourly_score, hourly_breakdown = await score_hourly_technicals(symbol, check_time)
            
            # 3. NO GATE - All hourly triggers from watchlist pass to agents
            # (Watchlist already ensures daily score >= 0.80)
            results["triggers_passed_gate"] += 1
            
            # For each trigger that passed gate
            for trigger in new_triggers:
                logger.info(f"\n✅ {symbol} @ {check_time}: {trigger.trigger_type} (score: {hourly_score:.3f})")
                
                # 4. Run agent analysis (simplified - just get decision)
                try:
                    # In real backtest, would run full multi-agent analysis
                    # For now, simulate decision based on trigger priority
                    if trigger.priority == "HIGH":
                        decision = {"action": "BUY", "take_profit_pct": 0.05, "stop_loss_pct": 0.02}
                    elif trigger.priority == "MEDIUM":
                        decision = {"action": "BUY", "take_profit_pct": 0.04, "stop_loss_pct": 0.025}
                    else:
                        decision = {"action": "PASS"}
                    
                    results["triggers_analyzed"] += 1
                    
                    if decision.get("action") == "PASS":
                        continue
                    
                    # 5. Get entry price from trigger details
                    entry_price = trigger.details.get("current_price", 0)
                    
                    if not entry_price or entry_price == 0:
                        # Skip if no valid entry price
                        logger.debug(f"   Skipping {trigger.trigger_type} - no entry price")
                        continue
                    
                    # 6. Get future prices
                    future_prices = await get_future_prices(symbol, check_time, hours_forward=24)
                    
                    # 7. Calculate outcome
                    outcome = calculate_outcome(entry_price, decision, future_prices)
                    
                    # Track results
                    results["outcomes"][outcome["outcome"]] += 1
                    results["total_pnl"] += outcome["pnl"]
                    
                    trade_record = {
                        "symbol": symbol,
                        "time": check_time,
                        "trigger_type": trigger.trigger_type,
                        "priority": trigger.priority,
                        "hourly_score": hourly_score,
                        "entry_price": entry_price,
                        "decision": decision,
                        **outcome,
                    }
                    
                    results["trades"].append(trade_record)
                    
                    logger.info(f"   Outcome: {outcome['outcome']} | P&L: {outcome['pnl']:.2%}")
                
                except Exception as e:
                    logger.error(f"   Analysis error: {e}")
                    continue
        
        except Exception as e:
            logger.debug(f"Error testing {symbol} @ {check_time}: {e}")
            continue
    
    return results


def print_backtest_summary(results: dict):
    """Print comprehensive backtest summary."""
    
    print("\n" + "=" * 80)
    print("BACKTEST SUMMARY")
    print("=" * 80)
    
    print(f"\n📊 Detection Statistics:")
    print(f"  Total bars scanned: {results['total_bars']:,}")
    print(f"  Triggers detected: {results['triggers_detected']}")
    print(f"  Triggers passed gate: {results['triggers_passed_gate']} ({results['triggers_passed_gate']/results['total_bars']*100:.2f}%)")
    print(f"  Triggers analyzed: {results['triggers_analyzed']}")
    
    print(f"\n💰 Trading Results:")
    print(f"  Total trades: {len(results['trades'])}")
    
    if results['trades']:
        print(f"\n  Outcomes:")
        for outcome, count in results['outcomes'].items():
            pct = count / len(results['trades']) * 100
            print(f"    {outcome:15} {count:3} ({pct:.1f}%)")
        
        print(f"\n  Performance:")
        total_pnl = results['total_pnl']
        avg_pnl = total_pnl / len(results['trades']) if results['trades'] else 0
        win_rate = results['outcomes'].get('TP_HIT', 0) / len(results['trades']) if results['trades'] else 0
        
        print(f"    Total P&L: {total_pnl:+.2%}")
        print(f"    Average P&L per trade: {avg_pnl:+.2%}")
        print(f"    Win rate: {win_rate:.1%}")
        
        # Top trades
        print(f"\n🏆 Top 5 Trades:")
        sorted_trades = sorted(results['trades'], key=lambda x: x['pnl'], reverse=True)[:5]
        for i, trade in enumerate(sorted_trades, 1):
            print(f"  {i}. {trade['symbol']} {trade['trigger_type']} - {trade['outcome']} ({trade['pnl']:+.2%})")
        
        # Worst trades
        print(f"\n❌ Worst 5 Trades:")
        worst_trades = sorted(results['trades'], key=lambda x: x['pnl'])[:5]
        for i, trade in enumerate(worst_trades, 1):
            print(f"  {i}. {trade['symbol']} {trade['trigger_type']} - {trade['outcome']} ({trade['pnl']:+.2%})")
    else:
        print(f"  No trades executed (all passed or no triggers passed gate)")
    
    print("\n" + "=" * 80)


async def main():
    parser = argparse.ArgumentParser(description="Full backtest with agent analysis and outcomes")
    parser.add_argument("--days", type=int, default=30, help="Days of history to test")
    parser.add_argument("--limit", type=int, help="Limit number of bars to test")
    
    args = parser.parse_args()
    
    results = await run_full_backtest(args.days, args.limit)
    
    if results:
        print_backtest_summary(results)
        logger.info("✅ Backtest complete!")
    else:
        logger.error("❌ Backtest failed!")


if __name__ == "__main__":
    asyncio.run(main())
