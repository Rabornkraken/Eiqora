"""
Watchlist builder pipeline - runs candidate selector.
Called by scheduler daily at 8 AM (after market data is fresh).
"""
import logging
import asyncio
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)


def run_build_watchlist():
    """Build daily watchlist using candidate selector."""
    from eiqora_v2.live.candidate_selector import CandidateSelector
    
    async def build():
        try:
            _logger.info("Building daily watchlist...")
            selector = CandidateSelector(threshold=0.60)
            scan_time = datetime.now(timezone.utc)
            
            watchlist = await selector.build_watchlist(scan_time)
            await selector.save_watchlist(watchlist, scan_time.date())
            
            _logger.info(f"✅ Watchlist built: {len(watchlist)} candidates")
            
            # Log top 5
            for candidate in watchlist[:5]:
                _logger.info(f"  {candidate['symbol']}: {candidate['total_score']:.2f}")
                
        except Exception as e:
            _logger.error(f"Failed to build watchlist: {e}", exc_info=True)
    
    asyncio.run(build())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_build_watchlist()
