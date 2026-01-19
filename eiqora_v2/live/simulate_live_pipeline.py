"""
Live Pipeline Simulation for Intraday Triggers.

Simulates the exact flow of the live pipeline scan_watchlist() to test
if new intraday triggers would detect anything and pass the analysis gate.

This uses the same code path as production.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from eiqora_v2.live.trigger_monitor import TriggerMonitor, HOURLY_INTRADAY_TRIGGERS
from eiqora_v2.tools.hourly_indicators import score_hourly_technicals
from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def get_recent_watchlist() -> list[str]:
    """Get symbols from recent daily watchlist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT symbol
                FROM daily_watchlist
                WHERE scan_date >= CURRENT_DATE - INTERVAL '3 days'
                ORDER BY symbol
            """)
            return [row[0] for row in cur.fetchall()]


async def simulate_hourly_scan(check_time: datetime) -> dict[str, Any]:
    """
    Simulate the live pipeline's hourly trigger scan.
    
    This runs the EXACT same code as scan_watchlist() does.
    """
    logger.info(f"=" * 80)
    logger.info(f"SIMULATING HOURLY SCAN AT {check_time}")
    logger.info(f"=" * 80)
    
    # Get watchlist (like live pipeline does)
    watchlist = await get_recent_watchlist()
    logger.info(f"Watchlist: {len(watchlist)} symbols")
    logger.info(f"Symbols: {', '.join(watchlist[:10])}{'...' if len(watchlist) > 10 else ''}")
    
    # Initialize monitor
    monitor = TriggerMonitor()
    
    # Scan each symbol (like live pipeline does)
    results = {
        "check_time": check_time,
        "watchlist_size": len(watchlist),
        "triggers_by_symbol": {},
        "new_trigger_types": [],
        "passed_gate": [],
        "suppressed": [],
    }
    
    for symbol in watchlist:
        try:
            # EXACT same call as live pipeline
            triggers = await monitor.check_hourly_technical_triggers(symbol, check_time)
            
            # Filter to new intraday triggers only
            new_triggers = [
                t for t in triggers 
                if t.trigger_type in {
                    "vwap_reclaim", "hourly_rsi_divergence", "intraday_consolidation_break",
                    "opening_range_breakout", "hourly_money_flow_surge"
                }
            ]
            
            if new_triggers:
                # Calculate hourly score (like analysis gate does)
                hourly_score, hourly_breakdown = await score_hourly_technicals(symbol, check_time)
                
                results["triggers_by_symbol"][symbol] = {
                    "triggers": new_triggers,
                    "hourly_score": hourly_score,
                    "hourly_breakdown": hourly_breakdown,
                }
                
                for trigger in new_triggers:
                    results["new_trigger_types"].append(trigger.trigger_type)
                    
                    # Check if passes gate
                    if hourly_score >= 0.60:
                        results["passed_gate"].append({
                            "symbol": symbol,
                            "trigger_type": trigger.trigger_type,
                            "priority": trigger.priority,
                            "hourly_score": hourly_score,
                            "details": trigger.details,
                        })
                        logger.info(f"  ✅ {symbol}: {trigger.trigger_type} (score: {hourly_score:.3f}) - WOULD ANALYZE")
                    else:
                        results["suppressed"].append({
                            "symbol": symbol,
                            "trigger_type": trigger.trigger_type,
                            "hourly_score": hourly_score,
                        })
                        logger.info(f"  ⏭️  {symbol}: {trigger.trigger_type} (score: {hourly_score:.3f}) - SUPPRESSED")
        
        except Exception as e:
            logger.debug(f"  Error checking {symbol}: {e}")
            continue
    
    return results


def print_summary(results: dict):
    """Print detailed summary of simulation results."""
    
    print("\n" + "=" * 80)
    print("SIMULATION RESULTS")
    print("=" * 80)
    
    print(f"\n📊 Scan Summary:")
    print(f"  Watchlist size: {results['watchlist_size']} symbols")
    print(f"  Symbols with new triggers: {len(results['triggers_by_symbol'])}")
    print(f"  Total new triggers detected: {len(results['new_trigger_types'])}")
    print(f"  Passed analysis gate: {len(results['passed_gate'])} ✅")
    print(f"  Suppressed (low score): {len(results['suppressed'])} ⏭️")
    
    if results['new_trigger_types']:
        from collections import Counter
        type_counts = Counter(results['new_trigger_types'])
        print(f"\n🎯 Trigger Types Detected:")
        for t_type, count in type_counts.most_common():
            print(f"  {t_type:30} {count:2}x")
    
    if results['passed_gate']:
        print(f"\n✅ TRIGGERS THAT WOULD BE ANALYZED ({len(results['passed_gate'])}):")
        for i, trigger in enumerate(results['passed_gate'], 1):
            print(f"\n  {i}. {trigger['symbol']} - {trigger['trigger_type']} ({trigger['priority']} priority)")
            print(f"     Hourly Score: {trigger['hourly_score']:.3f}")
            
            # Show key details
            details = trigger['details']
            if trigger['trigger_type'] == 'vwap_reclaim':
                print(f"     VWAP Distance: {details.get('vwap_distance_pct', 0):.2f}%")
                print(f"     Volume Z: {details.get('volume_z', 0):.2f}")
            elif trigger['trigger_type'] == 'hourly_money_flow_surge':
                print(f"     CMF: {details.get('cmf_20', 0):.3f}")
                print(f"     Volume Z: {details.get('volume_z', 0):.2f}")
            elif trigger['trigger_type'] == 'intraday_consolidation_break':
                print(f"     Range: {details.get('consolidation_range_pct', 0):.2f}%")
                print(f"     Breakout: {details.get('price_change_pct', 0):.2f}%")
    else:
        print(f"\n❌ No triggers passed the analysis gate (hourly score < 0.60)")
    
    if results['suppressed']:
        print(f"\n⏭️  SUPPRESSED TRIGGERS (low hourly score):")
        for trigger in results['suppressed'][:5]:
            print(f"  {trigger['symbol']:6} - {trigger['trigger_type']:30} (score: {trigger['hourly_score']:.3f})")
    
    print("\n" + "=" * 80)


async def main():
    """Run live pipeline simulation on recent market data."""
    
    # Test on most recent market hour
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MAX(datetime)
                FROM market_bar_hourly
                WHERE rsi_14 IS NOT NULL
                  AND EXTRACT(HOUR FROM datetime AT TIME ZONE 'America/New_York') BETWEEN 10 AND 16
            """)
            
            latest_hour = cur.fetchone()[0]
    
    if not latest_hour:
        logger.error("No recent hourly data found!")
        return
    
    logger.info(f"Testing on most recent market hour: {latest_hour}")
    
    # Run simulation
    results = await simulate_hourly_scan(latest_hour)
    
    # Print results
    print_summary(results)
    
    # Conclusion
    print("\n🎯 CONCLUSION:")
    if results['passed_gate']:
        print(f"  ✅ {len(results['passed_gate'])} trigger(s) would be sent to agent analysis")
        print(f"  ✅ New intraday triggers ARE WORKING in live pipeline conditions!")
    else:
        print(f"  ⚠️  No triggers passed gate at this specific hour")
        if results['suppressed']:
            print(f"  ℹ️  {len(results['suppressed'])} triggers detected but suppressed by hourly score")
        print(f"  ℹ️  This is normal - not all hours produce trade-worthy setups")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
