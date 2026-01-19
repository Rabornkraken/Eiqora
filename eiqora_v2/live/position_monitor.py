"""
Position Monitor for detecting thesis-breaking events on existing positions.

Runs independently of entry scanning to adaptively exit positions when
the original trade thesis is invalidated.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Literal

from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.positions import get_open_positions

_logger = logging.getLogger(__name__)
EASTERN_TZ = ZoneInfo("America/New_York")


class PositionTrigger:
    """Represents a thesis-breaking trigger for an existing position."""
    
    def __init__(
        self,
        symbol: str,
        trigger_type: str,
        severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        details: dict[str, Any],
        detected_at: datetime,
    ):
        self.symbol = symbol
        self.trigger_type = trigger_type
        self.severity = severity
        self.details = details
        self.detected_at = detected_at
    
    def __repr__(self):
        return f"PositionTrigger({self.symbol}, {self.trigger_type}, {self.severity})"


class PositionMonitor:
    """
    Monitors open positions for thesis-breaking events.
    
    Trigger types (only HIGH/CRITICAL):
    - sec_8k_material: New material 8-K filing
    - earnings_miss: Earnings significantly missed estimates
    - major_negative_news: High-impact negative news (sentiment < -0.7)
    """
    
    def __init__(self):
        pass
    
    async def scan_positions(
        self,
        check_time: datetime | None = None,
    ) -> list[PositionTrigger]:
        """
        Scan all open positions for thesis-breaking triggers.
        
        Returns:
            List of position triggers (HIGH/CRITICAL only)
        """
        if check_time is None:
            check_time = datetime.now(EASTERN_TZ)
        
        # Get open positions
        positions = await get_open_positions(asof_time=check_time)
        if not positions:
            _logger.info("No open positions to monitor")
            return []
        
        position_symbols = [p["symbol"] for p in positions]
        _logger.info(f"Monitoring {len(positions)} positions: {', '.join(position_symbols)}")
        
        all_triggers = []
        
        for position in positions:
            symbol = position["symbol"]
            # Auto-exit checks handled in account refresh loop (stop loss, take profit, time stop).
            
            # Check for thesis-breaking events
            sec_trigger = await self.check_sec_8k_trigger(symbol, check_time)
            if sec_trigger:
                all_triggers.append(sec_trigger)
            
            earnings_trigger = await self.check_earnings_miss(symbol, check_time)
            if earnings_trigger:
                all_triggers.append(earnings_trigger)
            
            news_trigger = await self.check_major_negative_news(symbol, check_time)
            if news_trigger:
                all_triggers.append(news_trigger)
            
            # Thesis-breaking event checks
        
        if all_triggers:
            _logger.warning(
                f"\u26a0\ufe0f  Found {len(all_triggers)} thesis-breaking triggers for positions"
            )
        
        return all_triggers
    
    async def check_sec_8k_trigger(
        self,
        symbol: str,
        check_time: datetime,
    ) -> PositionTrigger | None:
        """Check for new material 8-K filing (last 24h)."""
        try:
            start_time = check_time - timedelta(hours=24)
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT s.filed_at, s.form_type, s.description
                    FROM sec_filing s
                    JOIN security sec ON s.cik = sec.cik
                    WHERE sec.ticker = $1
                      AND s.form_type = '8-K'
                      AND s.filed_at BETWEEN $2 AND $3
                    ORDER BY s.filed_at DESC
                    LIMIT 1
                """, symbol, start_time, check_time)
                
                if row:
                    return PositionTrigger(
                        symbol=symbol,
                        trigger_type="sec_8k_material",
                        severity="HIGH",
                        details={
                            "filed_at": str(row["filed_at"]),
                            "description": row["description"],
                        },
                        detected_at=check_time,
                    )
        except Exception as e:
            _logger.warning(f"{symbol}: SEC 8-K check failed - {e}")
        return None
    
    async def check_earnings_miss(
        self,
        symbol: str,
        check_time: datetime,
    ) -> PositionTrigger | None:
        """Check for significant earnings miss (last 48h)."""
        try:
            start_time = check_time - timedelta(hours=48)
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT earnings_date, fiscal_quarter, eps_actual, eps_est,
                           revenue_actual, revenue_est
                    FROM earnings_event
                    WHERE symbol = $1
                      AND earnings_date BETWEEN $2 AND $3
                      AND eps_actual IS NOT NULL
                      AND eps_est IS NOT NULL
                    ORDER BY earnings_date DESC
                    LIMIT 1
                """, symbol, start_time, check_time)
                
                if row:
                    eps_actual = row["eps_actual"]
                    eps_est = row["eps_est"]
                    miss_pct = ((eps_actual - eps_est) / abs(eps_est)) * 100 if eps_est != 0 else 0
                    
                    # Only trigger on significant miss (> 10%)
                    if miss_pct < -10:
                        return PositionTrigger(
                            symbol=symbol,
                            trigger_type="earnings_miss",
                            severity="HIGH",
                            details={
                                "earnings_date": str(row["earnings_date"]),
                                "fiscal_quarter": row["fiscal_quarter"],
                                "eps_actual": eps_actual,
                                "eps_estimate": eps_est,
                                "miss_pct": miss_pct,
                            },
                            detected_at=check_time,
                        )
        except Exception as e:
            _logger.warning(f"{symbol}: Earnings check failed - {e}")
        return None
    
    async def check_major_negative_news(
        self,
        symbol: str,
        check_time: datetime,
    ) -> PositionTrigger | None:
        """Check for major negative news (FinBERT sentiment < -0.7, last 12h)."""
        try:
            start_time = check_time - timedelta(hours=12)
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT yn.published_at, yn.title, nr.score
                    FROM yfinance_news yn
                    JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
                    WHERE yn.ticker = $1
                      AND yn.published_at BETWEEN $2 AND $3
                      AND nr.score IS NOT NULL
                      AND nr.score < -0.7
                    ORDER BY nr.score ASC
                    LIMIT 1
                """, symbol, start_time, check_time)
                
                if row:
                    return PositionTrigger(
                        symbol=symbol,
                        trigger_type="major_negative_news",
                        severity="MEDIUM",  # Not as critical as 8-K or earnings
                        details={
                            "published_at": str(row["published_at"]),
                            "title": row["title"],
                            "sentiment": row["score"],
                        },
                        detected_at=check_time,
                    )
        except Exception as e:
            _logger.debug(f"{symbol}: News check failed - {e}")
        return None


async def main():
    """Test position monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    monitor = PositionMonitor()
    
    test_time = datetime(2024, 12, 20, 14, 0, tzinfo=timezone.utc)
    
    print(f"\n{'='*60}")
    print(f"Position Monitor Test - {test_time}")
    print(f"{'='*60}\n")
    
    triggers = await monitor.scan_positions(test_time)
    
    print(f"\nFound {len(triggers)} thesis-breaking triggers:")
    for t in triggers:
        print(f"  {t}")


if __name__ == "__main__":
    asyncio.run(main())
