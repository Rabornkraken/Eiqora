"""
Reset Account Data Script

⚠️  WARNING: This script clears ALL trading data including LIVE and BACKTEST data.

Clears:
- ALL positions (live and backtest)
- ALL account snapshots
- ALL account states  
- ALL analysis logs
- ALL backtest runs

Usage:
    python -m eiqora_v2.live.reset_backtest_account
"""

import asyncio
import logging
from eiqora_v2.tools.db import get_connection

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def reset_all_accounts():
    """Reset ALL account data (live and backtest)."""
    logger.info("=" * 60)
    logger.info("⚠️  FULL ACCOUNT RESET - LIVE AND BACKTEST")
    logger.info("=" * 60)
    
    async with get_connection() as conn:
        # Get counts before deletion
        logger.info("\nCurrent data:")
        snap_count_before = await conn.fetchval("SELECT COUNT(*) FROM account_snapshot")
        state_count_before = await conn.fetchval("SELECT COUNT(*) FROM account_state")
        pos_count_before = await conn.fetchval("SELECT COUNT(*) FROM position")
        backtest_pos_count_before = await conn.fetchval("SELECT COUNT(*) FROM backtest_position")
        ana_count_before = await conn.fetchval("SELECT COUNT(*) FROM analysis_log")
        backtest_ana_count_before = await conn.fetchval("SELECT COUNT(*) FROM backtest_analysis")
        run_count_before = await conn.fetchval("SELECT COUNT(*) FROM backtest_run")
        
        logger.info(f"  account_snapshot (all): {snap_count_before}")
        logger.info(f"  account_state (all): {state_count_before}")
        logger.info(f"  position (live): {pos_count_before}")
        logger.info(f"  backtest_position: {backtest_pos_count_before}")
        logger.info(f"  analysis_log (live): {ana_count_before}")
        logger.info(f"  backtest_analysis: {backtest_ana_count_before}")
        logger.info(f"  backtest_run: {run_count_before}")
        
        # Delete data
        logger.info("\n⚠️  Deleting ALL account data...")
        
        await conn.execute("DELETE FROM account_snapshot")
        logger.info("  ✓ Cleared ALL account_snapshot")
        
        await conn.execute("DELETE FROM account_state")
        logger.info("  ✓ Cleared ALL account_state")
        
        await conn.execute("DELETE FROM position")
        logger.info("  ✓ Cleared ALL position (live)")
        
        await conn.execute("DELETE FROM backtest_position")
        logger.info("  ✓ Cleared backtest_position")
        
        await conn.execute("DELETE FROM analysis_log")
        logger.info("  ✓ Cleared ALL analysis_log (live)")
        
        await conn.execute("DELETE FROM backtest_analysis")
        logger.info("  ✓ Cleared backtest_analysis")
        
        await conn.execute("DELETE FROM backtest_run")
        logger.info("  ✓ Cleared backtest_run")
        
        # Verify deletion
        logger.info("\nVerifying deletion...")
        snap_count_after = await conn.fetchval("SELECT COUNT(*) FROM account_snapshot")
        state_count_after = await conn.fetchval("SELECT COUNT(*) FROM account_state")
        pos_count_after = await conn.fetchval("SELECT COUNT(*) FROM position")
        backtest_pos_count_after = await conn.fetchval("SELECT COUNT(*) FROM backtest_position")
        ana_count_after = await conn.fetchval("SELECT COUNT(*) FROM analysis_log")
        backtest_ana_count_after = await conn.fetchval("SELECT COUNT(*) FROM backtest_analysis")
        run_count_after = await conn.fetchval("SELECT COUNT(*) FROM backtest_run")
        
        logger.info(f"  account_snapshot (all): {snap_count_after}")
        logger.info(f"  account_state (all): {state_count_after}")
        logger.info(f"  position (live): {pos_count_after}")
        logger.info(f"  backtest_position: {backtest_pos_count_after}")
        logger.info(f"  analysis_log (live): {ana_count_after}")
        logger.info(f"  backtest_analysis: {backtest_ana_count_after}")
        logger.info(f"  backtest_run: {run_count_after}")
        
        # Summary
        total_deleted = (
            snap_count_before - snap_count_after +
            state_count_before - state_count_after +
            pos_count_before - pos_count_after +
            backtest_pos_count_before - backtest_pos_count_after +
            ana_count_before - ana_count_after +
            backtest_ana_count_before - backtest_ana_count_after +
            run_count_before - run_count_after
        )
        
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ RESET COMPLETE - Deleted {total_deleted} total records")
        logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(reset_all_accounts())
