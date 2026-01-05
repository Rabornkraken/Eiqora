"""
Test the live pre-market scanner.

Usage:
    python eiqora_v2/live/test_scanner.py
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

from eiqora_v2.live.scanner import LiveScanner
from eiqora_v2.live.signals import SignalManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

async def test_scanner():
    """Test the live scanner end-to-end."""
    print("\n" + "="*60)
    print("LIVE PRE-MARKET SCANNER TEST")
    print("="*60 + "\n")
    
    # Initialize
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    scanner = LiveScanner(db_url=db_url)
    signal_manager = SignalManager(db_url=db_url)
    
    # Test 1: Load universe
    print("Test 1: Loading universe...")
    symbols = scanner.load_universe()
    print(f"✅ Loaded {len(symbols)} symbols")
    print(f"   Sample: {symbols[:5]}")
    
    # Test 2: Scan for triggers
    print("\nTest 2: Scanning for triggers...")
    scan_date = datetime.now(timezone.utc).date()
    triggers = await scanner.scan_all_tickers(scan_date)
    print(f"✅ Found {len(triggers)} triggers")
    
    if triggers:
        print(f"   Sample triggers:")
        for trigger in triggers[:3]:
            print(f"   - {trigger.ticker}: {trigger.type}")
    
    # Test 3: Generate trade signals
    print("\nTest 3: Generating trade signals...")
    signals = await scanner.generate_trade_signals(scan_date)
    print(f"✅ Generated {len(signals)} GO signals")
    
    # Test 4: Store signals
    if signals:
        print("\nTest 4: Storing signals to database...")
        signal_ids = signal_manager.store_signals(signals)
        print(f"✅ Stored {len(signal_ids)} signals")
        
        # Test 5: Send notifications
        print("\nTest 5: Sending notifications...")
        signal_manager.send_notifications(signals)
        
        # Test 6: Generate report
        print("\nTest 6: Generating daily report...")
        report = signal_manager.generate_daily_report(scan_date)
        print(report)
    else:
        print("\n⚠️  No GO signals today - normal for calm markets")
    
    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(test_scanner())
