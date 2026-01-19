"""
Test the live pre-market scanner.

Usage:
    python eiqora_v2/live/test_scanner.py
"""

import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from eiqora_v2.live.scanner import LiveScanner
from eiqora_v2.live.signals import SignalManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

@pytest.mark.asyncio
async def test_scanner():
    """Test the live scanner end-to-end."""
    print("\n" + "="*60)
    print("LIVE PRE-MARKET SCANNER TEST")
    print("="*60 + "\n")
    
    # Initialize
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    scanner = LiveScanner(db_url=db_url)
    signal_manager = SignalManager()
    
    # Test 1: Load universe
    print("Test 1: Loading universe...")
    symbols = scanner.load_universe()
    print(f"✅ Loaded {len(symbols)} symbols")
    print(f"   Sample: {symbols[:5]}")
    
    # Test 2: Scan for candidates
    print("\nTest 2: Scanning for candidates...")
    scan_date = datetime.now(ZoneInfo("America/New_York")).date()
    candidates = await scanner.scan_with_quality_filter(scan_date)
    print(f"✅ Found {len(candidates)} candidates")
    
    if candidates:
        print("   Sample candidates:")
        for symbol, score, _ in candidates[:3]:
            print(f"   - {symbol}: score={score:.2f}")

    if os.getenv("EIQORA_RUN_LLM_TESTS") != "1":
        pytest.skip("LLM-backed scan disabled (set EIQORA_RUN_LLM_TESTS=1 to enable).")
    
    # Test 3: Generate trade signals
    print("\nTest 3: Generating trade signals...")
    signals = await scanner.generate_trade_signals(scan_date)
    print(f"✅ Generated {len(signals)} GO signals")
    
    # Test 4: Store signals
    if signals:
        print("\nTest 4: Storing signals to database...")
        signal_ids = await signal_manager.store_signals(signals)
        print(f"✅ Stored {len(signal_ids)} signals")
        
        # Test 5: Send notifications
        print("\nTest 5: Sending notifications...")
        signal_manager.send_notifications(signals)
        
        # Test 6: Generate report
        print("\nTest 6: Generating daily report...")
        report = await signal_manager.generate_daily_report(scan_date)
        print(report)
    else:
        print("\n⚠️  No GO signals today - normal for calm markets")
    
    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(test_scanner())
