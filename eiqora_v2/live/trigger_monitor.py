"""
Trigger Monitor for detecting trade signals.

Monitors watchlist candidates for:
- News events (sentiment > threshold)
- Earnings releases (within window)
- SEC 8-K filings
- Hourly technical triggers (breakout, bounce, volume)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.prices import get_hourly_indicators
from eiqora_v2.tools.events import check_macro_blackout

_logger = logging.getLogger(__name__)


class Trigger:
    """Represents a detected trigger event."""
    
    def __init__(
        self,
        symbol: str,
        trigger_type: str,
        priority: Literal["HIGH", "MEDIUM", "LOW"],
        details: dict[str, Any],
        detected_at: datetime,
    ):
        self.symbol = symbol
        self.trigger_type = trigger_type
        self.priority = priority
        self.details = details
        self.detected_at = detected_at
    
    def __repr__(self):
        return f"Trigger({self.symbol}, {self.trigger_type}, {self.priority})"


class TriggerMonitor:
    """
    Monitors watchlist for triggering events.
    
    Trigger types:
    - earnings_release: Earnings within 24h window
    - sec_8k: New 8-K filing  
    - news_sentiment: High sentiment news
    - hourly_breakout: Price > 20-bar high
    - hourly_bounce: RSI oversold reversal
    - volume_surge: Hourly volume > 2x average
    """
    
    def __init__(self):
        pass
    
    async def get_watchlist(self, scan_date: datetime) -> list[str]:
        """Get current watchlist symbols."""
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT symbol FROM watchlist 
                WHERE scan_date = $1::date
                ORDER BY total_score DESC
            """, scan_date.date())
            return [r["symbol"] for r in rows]
    
    async def check_earnings_trigger(
        self,
        symbol: str,
        check_time: datetime,
    ) -> Trigger | None:
        """Check for earnings release within 24h."""
        try:
            start_time = check_time - timedelta(hours=24)
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT earnings_date, fiscal_quarter, eps_actual, eps_est
                    FROM earnings_event
                    WHERE symbol = $1
                      AND earnings_date BETWEEN $2 AND $3
                    ORDER BY earnings_date DESC
                    LIMIT 1
                """, symbol, start_time, check_time)
                
                if row:
                    return Trigger(
                        symbol=symbol,
                        trigger_type="earnings_release",
                        priority="HIGH",
                        details={
                            "earnings_date": str(row["earnings_date"]),
                            "fiscal_quarter": row["fiscal_quarter"],
                            "eps_actual": row["eps_actual"],
                            "eps_estimate": row["eps_est"],
                            "beat": row["eps_actual"] > row["eps_est"] if row["eps_actual"] and row["eps_est"] else None,
                        },
                        detected_at=check_time,
                    )
        except Exception as e:
            _logger.warning(f"{symbol}: Earnings trigger check failed - {e}")
        return None
    
    async def check_sec_8k_trigger(
        self,
        symbol: str,
        check_time: datetime,
    ) -> Trigger | None:
        """Check for new 8-K filing within 48h."""
        try:
            start_time = check_time - timedelta(hours=48)
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
                    return Trigger(
                        symbol=symbol,
                        trigger_type="sec_8k",
                        priority="HIGH",
                        details={
                            "filed_at": str(row["filed_at"]),
                            "form_type": row["form_type"],
                            "description": row["description"],
                        },
                        detected_at=check_time,
                    )
        except Exception as e:
            _logger.warning(f"{symbol}: SEC 8-K trigger check failed - {e}")
        return None
    
    async def check_news_trigger(
        self,
        symbol: str,
        check_time: datetime,
        sentiment_threshold: float = 2.0,  # FinBERT Tone threshold (-10 to +10)
    ) -> Trigger | None:
        """Check for high-sentiment news within 24h (using YFinance clean news)."""
        try:
            start_time = check_time - timedelta(hours=24)
            async with get_connection() as conn:
                # Query YFinance news with FinBERT score
                row = await conn.fetchrow("""
                    SELECT yn.published_at, yn.title, nr.score as sentiment
                    FROM yfinance_news yn
                    JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
                    WHERE yn.ticker = $1
                      AND yn.published_at BETWEEN $2 AND $3
                      AND nr.score > $4
                    ORDER BY nr.score DESC
                    LIMIT 1
                """, symbol, start_time, check_time, sentiment_threshold)
                
                if row:
                    return Trigger(
                        symbol=symbol,
                        trigger_type="news_sentiment",
                        priority="MEDIUM",
                        details={
                            "published_at": str(row["published_at"]),
                            "title": row["title"],
                            "sentiment": float(row["sentiment"]),
                        },
                        detected_at=check_time,
                    )
        except Exception as e:
            _logger.debug(f"{symbol}: News trigger check failed - {e}")
        return None
    
    async def check_hourly_technical_triggers(
        self,
        symbol: str,
        check_time: datetime,
    ) -> list[Trigger]:
        """Check for hourly technical triggers."""
        triggers = []
        
        try:
            indicators = await get_hourly_indicators(symbol, check_time.date(), check_time)
            if indicators.get("error"):
                return triggers
            
            state_tags = indicators.get("state_tags", [])
            
            # Volume surge
            if "HOURLY_VOLUME_SPIKE" in state_tags:
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="volume_surge",
                    priority="LOW",
                    details={
                        "volume": indicators.get("volume"),
                        "avg_volume": indicators.get("avg_volume_20"),
                    },
                    detected_at=check_time,
                ))
            
            # RSI oversold bounce
            if "HOURLY_OVERSOLD" in state_tags:
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="hourly_bounce",
                    priority="MEDIUM",
                    details={
                        "rsi": indicators.get("rsi8"),
                        "intraday_trend": indicators.get("intraday_trend"),
                    },
                    detected_at=check_time,
                ))
            
            # Near VWAP support
            vwap_dist = indicators.get("vwap_distance_pct", 0)
            if -1.5 < vwap_dist < 0.5:
                intraday_trend = indicators.get("intraday_trend")
                if intraday_trend == "UP":
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="vwap_support",
                        priority="MEDIUM",
                        details={
                            "vwap_distance_pct": vwap_dist,
                            "current_price": indicators.get("close"),
                            "vwap": indicators.get("vwap"),
                        },
                        detected_at=check_time,
                    ))
                    
        except Exception as e:
            _logger.debug(f"{symbol}: Hourly technical trigger check failed - {e}")
        
        return triggers
    
    async def scan_watchlist(
        self,
        check_time: datetime | None = None,
    ) -> list[Trigger]:
        """
        Scan entire watchlist for triggers.
        
        Returns:
            List of triggers sorted by priority
        """
        if check_time is None:
            check_time = datetime.now(timezone.utc)
        
        # MACRO SAFEGUARD: Check for upcoming high-impact events
        is_blackout, event_name = await check_macro_blackout(check_time, hours_ahead=6)
        if is_blackout:
            _logger.warning(
                f"⚠️  MACRO BLACKOUT: Skipping trigger scan due to upcoming {event_name}"
            )
            return []  # No triggers during blackout
        
        # Get watchlist
        watchlist = await self.get_watchlist(check_time)
        if not watchlist:
            _logger.info(f"No watchlist found for {check_time.date()}")
            return []
        
        _logger.info(f"Scanning {len(watchlist)} watchlist symbols for triggers...")
        
        all_triggers = []
        
        for symbol in watchlist:
            # Check all trigger types
            earnings = await self.check_earnings_trigger(symbol, check_time)
            if earnings:
                all_triggers.append(earnings)
            
            sec_8k = await self.check_sec_8k_trigger(symbol, check_time)
            if sec_8k:
                all_triggers.append(sec_8k)
            
            news = await self.check_news_trigger(symbol, check_time)
            if news:
                all_triggers.append(news)
            
            hourly = await self.check_hourly_technical_triggers(symbol, check_time)
            all_triggers.extend(hourly)
        
        # Sort by priority
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        all_triggers.sort(key=lambda t: priority_order[t.priority])
        
        _logger.info(f"Found {len(all_triggers)} triggers")
        return all_triggers


async def main():
    """Test trigger monitor."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    monitor = TriggerMonitor()
    
    test_time = datetime(2024, 12, 17, 14, 0, tzinfo=timezone.utc)
    
    # Test on a specific symbol
    print(f"\nTesting trigger detection on {test_time}")
    print("="*60)
    
    for symbol in ["AAPL", "NVDA", "META"]:
        print(f"\n{symbol}:")
        
        earnings = await monitor.check_earnings_trigger(symbol, test_time)
        print(f"  Earnings: {earnings}")
        
        sec_8k = await monitor.check_sec_8k_trigger(symbol, test_time)
        print(f"  SEC 8-K: {sec_8k}")
        
        hourly = await monitor.check_hourly_technical_triggers(symbol, test_time)
        print(f"  Hourly: {hourly}")


if __name__ == "__main__":
    asyncio.run(main())
