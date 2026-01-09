#!/usr/bin/env python
"""
Test script to generate signals without live execution.
Shows watchlist → triggers → LLM analysis → signals (dry run).
"""

import asyncio
import logging
from datetime import datetime, timezone
from eiqora_v2.live.candidate_selector import CandidateSelector
from eiqora_v2.live.trigger_monitor import TriggerMonitor
from data_collection.db.connection import get_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

async def main():
    print("="*60)
    print("EIQORA SIGNAL GENERATION TEST")
    print("="*60)
    print()
    
    # Step 1: Build watchlist
    print("[1/3] Building watchlist...")
    selector = CandidateSelector(threshold=0.50)
    watchlist = await selector.build_watchlist(datetime.now(timezone.utc))
    await selector.save_watchlist(watchlist, datetime.now(timezone.utc).date())
    print(f"✅ Built watchlist with {len(watchlist)} candidates")
    
    if len(watchlist) > 0:
        print("\nTop 5 candidates:")
        for candidate in watchlist[:5]:
            print(f"  {candidate['symbol']}: {candidate['total_score']:.2f} "
                  f"(tech: {candidate['technical_score']:.2f}, "
                  f"profile: {candidate['profile_score']:.2f})")
    else:
        print("⚠️  No candidates passed threshold")
        return
    
    print()
    
    # Step 2: Check triggers
    print("[2/3] Scanning for triggers...")
    monitor = TriggerMonitor()
    triggers = await monitor.scan_watchlist()
    
    if not triggers:
        print("ℹ️  No triggers found")
        print("\nNo triggers detected. This is normal if:")
        print("  - No earnings in next 24h")
        print("  - No SEC 8-K filings in last 48h")
        print("  - No high-sentiment news (>2.0) in last 24h")
        print("  - No RSI oversold (<30) on hourly")
        return
    
    print(f"✅ Found {len(triggers)} triggers:")
    for t in triggers:
        print(f"  - {t.symbol}: {t.trigger_type} (priority: {t.priority})")
    
    print()
    
    # Step 3: Show trigger details (but don't run LLM)
    print("[3/3] Trigger details:")
    print()
    for trigger in triggers[:3]:  # Show first 3
        print(f"Symbol: {trigger.symbol}")
        print(f"Type: {trigger.trigger_type}")
        print(f"Priority: {trigger.priority}")
        print(f"Details: {trigger.details}")
        print(f"Detected at: {trigger.detected_at}")
        print("-" * 40)
    
    print()
    print("="*60)
    print("TEST COMPLETE")
    print("="*60)
    print()
    print("To run full LLM analysis on a trigger:")
    print("  python -c \"from eiqora_v2.live.pipeline import LiveTradingPipeline; ...\"")
    print()
    print("Check trade_signal table:")
    print("  SELECT * FROM trade_signal ORDER BY created_at DESC LIMIT 5;")

if __name__ == "__main__":
    asyncio.run(main())
