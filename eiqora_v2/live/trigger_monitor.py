"""
Trigger Monitor for detecting trade signals.

Monitors watchlist candidates for:
- News events (sentiment > threshold)
- Earnings releases (within window)
- SEC 8-K filings
- Hourly technical triggers (breakout, bounce, volume)
- Second-order triggers (bad news no drop, sector laggard, vol compression)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Literal

from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.prices import (
    get_hourly_indicators,
    get_return_metrics,
    get_prices,
    get_price_levels,
    get_indicators,
)
from eiqora_v2.tools.events import check_macro_blackout
from eiqora_v2.config.universe import get_sector_etf

_logger = logging.getLogger(__name__)

SECOND_ORDER_TRIGGERS = {
    "bad_news_no_drop",
    "sector_laggard",
    "volatility_compression",
}
FUNDAMENTAL_OVERRIDE_TRIGGERS = {
    "earnings_release",
    "sec_8k",
}
OVERRIDE_TECH_TRIGGERS = {
    "hourly_bounce",
    "hourly_breakout",
    "vwap_support",
    "volume_surge",
}
OVERRIDE_TECH_SCORE = 0.85


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
    - bad_news_no_drop: Negative news with flat/green price action
    - sector_laggard: Sector up, ticker underperforms
    - volatility_compression: NR7 + low 5d range
    """
    
    def __init__(self):
        pass
    
    async def get_watchlist(self, scan_date: datetime) -> list[dict[str, Any]]:
        """Get current watchlist symbols and scores."""
        async with get_connection() as conn:
            rows = await conn.fetch("""
                SELECT symbol, technical_score, profile_score 
                FROM daily_watchlist 
                WHERE scan_date = $1::date
                ORDER BY total_score DESC
            """, scan_date.date())
            return [dict(r) for r in rows]
    
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
        sentiment_threshold: float = 4.0,  # FinBERT Tone threshold (-10 to +10)
    ) -> Trigger | None:
        """Check for high-sentiment news within 24h (using YFinance clean news)."""
        try:
            # Check for news in the last 4 hours (freshness check)
            start_time = check_time - timedelta(hours=4)
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

    async def _get_hourly_bars_between(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[dict[str, Any]]:
        from datetime import timezone as dt_timezone

        start_ts = start_time
        end_ts = end_time
        if start_ts.tzinfo is not None:
            start_ts = start_ts.astimezone(dt_timezone.utc).replace(tzinfo=None)
        if end_ts.tzinfo is not None:
            end_ts = end_ts.astimezone(dt_timezone.utc).replace(tzinfo=None)

        async with get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT datetime, open, high, low, close, volume
                FROM market_bar_hourly
                WHERE symbol = $1
                  AND datetime >= $2
                  AND datetime <= $3
                ORDER BY datetime ASC
                """,
                symbol,
                start_ts,
                end_ts,
            )
        return [dict(r) for r in rows]

    async def check_bad_news_no_drop_trigger(
        self,
        symbol: str,
        check_time: datetime,
        sentiment_threshold: float = -4.0,
        lookback_hours: int = 48,
        max_drop_pct: float = -0.003,
    ) -> Trigger | None:
        """Detect negative news that failed to push price down."""
        try:
            start_time = check_time - timedelta(hours=lookback_hours)
            async with get_connection() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT yn.published_at, yn.title, yn.news_id, yn.doc_id, nr.score
                    FROM yfinance_news yn
                    JOIN yfinance_news_relevance nr ON yn.doc_id = nr.doc_id
                    WHERE yn.ticker = $1
                      AND yn.published_at BETWEEN $2 AND $3
                      AND nr.score <= $4
                    ORDER BY yn.published_at DESC
                    LIMIT 1
                    """,
                    symbol,
                    start_time,
                    check_time,
                    sentiment_threshold,
                )

            if not row:
                return None

            published_at = row["published_at"]
            if not published_at:
                return None

            published_at_ts = published_at
            if published_at_ts.tzinfo is not None:
                published_at_ts = published_at_ts.astimezone(timezone.utc).replace(tzinfo=None)

            bars = await self._get_hourly_bars_between(symbol, published_at_ts, check_time)
            bars = [b for b in bars if b.get("datetime") and b["datetime"] >= published_at_ts]
            if len(bars) < 2:
                return None

            first_close = float(bars[0].get("close") or 0)
            last_close = float(bars[-1].get("close") or 0)
            if first_close <= 0 or last_close <= 0:
                return None

            pct_change = (last_close - first_close) / first_close
            if pct_change < max_drop_pct:
                return None

            return Trigger(
                symbol=symbol,
                trigger_type="bad_news_no_drop",
                priority="HIGH",
                details={
                    "published_at": str(published_at),
                    "title": row["title"],
                    "news_id": row["news_id"],
                    "doc_id": row["doc_id"],
                    "sentiment": float(row["score"]),
                    "price_change_pct": round(pct_change, 4),
                },
                detected_at=check_time,
            )
        except Exception as e:
            _logger.debug(f"{symbol}: bad_news_no_drop trigger check failed - {e}")
        return None

    async def check_sector_laggard_trigger(
        self,
        symbol: str,
        check_time: datetime,
        sector_min_ret_20d: float = 0.025,
        lag_gap_20d: float = 0.04,
    ) -> Trigger | None:
        """Detect sector strength while ticker lags."""
        try:
            sector_etf = get_sector_etf(symbol)
            symbol_metrics = await get_return_metrics(symbol, 120, check_time)
            sector_metrics = await get_return_metrics(sector_etf, 120, check_time)

            if symbol_metrics.get("error") or sector_metrics.get("error"):
                return None

            symbol_ret_20d = symbol_metrics.get("ret_20d")
            sector_ret_20d = sector_metrics.get("ret_20d")
            if symbol_ret_20d is None or sector_ret_20d is None:
                return None

            rel_gap = sector_ret_20d - symbol_ret_20d
            if sector_ret_20d < sector_min_ret_20d or rel_gap < lag_gap_20d:
                return None

            return Trigger(
                symbol=symbol,
                trigger_type="sector_laggard",
                priority="MEDIUM",
                details={
                    "sector_etf": sector_etf,
                    "sector_ret_20d": round(sector_ret_20d, 4),
                    "symbol_ret_20d": round(symbol_ret_20d, 4),
                    "relative_gap": round(rel_gap, 4),
                },
                detected_at=check_time,
            )
        except Exception as e:
            _logger.debug(f"{symbol}: sector_laggard trigger check failed - {e}")
        return None

    async def check_volatility_compression_trigger(
        self,
        symbol: str,
        check_time: datetime,
        avg_range_5d_max: float = 0.022,
        min_price: float = 5.0,
        min_avg_volume_20: float = 500_000,
    ) -> Trigger | None:
        """Detect NR7 + low 5d range compression."""
        try:
            prices = await get_prices(symbol, 30, check_time)
            if len(prices) < 8:
                return None

            ranges = []
            volumes = []
            for row in prices[-20:]:
                high = float(row.get("high") or 0)
                low = float(row.get("low") or 0)
                close = float(row.get("close") or 0)
                volume = float(row.get("volume") or 0)
                if close <= 0 or low <= 0:
                    continue
                ranges.append((high - low) / close)
                volumes.append(volume)

            if len(ranges) < 8:
                return None

            last_range = ranges[-1]
            last_close = float(prices[-1].get("close") or 0)
            avg_range_5d = sum(ranges[-5:]) / 5
            nr7 = last_range <= min(ranges[-7:]) * 1.01
            avg_vol_20 = sum(volumes[-20:]) / max(1, len(volumes[-20:]))

            if last_close < min_price or avg_vol_20 < min_avg_volume_20:
                return None
            if not nr7 or avg_range_5d > avg_range_5d_max:
                return None

            return Trigger(
                symbol=symbol,
                trigger_type="volatility_compression",
                priority="MEDIUM",
                details={
                    "nr7": nr7,
                    "last_range_pct": round(last_range, 4),
                    "avg_range_5d": round(avg_range_5d, 4),
                    "avg_volume_20": int(avg_vol_20),
                },
                detected_at=check_time,
            )
        except Exception as e:
            _logger.debug(f"{symbol}: volatility_compression trigger check failed - {e}")
        return None

    async def _get_relative_strength(
        self,
        symbol: str,
        check_time: datetime,
    ) -> dict[str, Any]:
        sector_etf = get_sector_etf(symbol)
        symbol_metrics = await get_return_metrics(symbol, 120, check_time)
        if symbol_metrics.get("error"):
            return {}

        benchmarks = {
            "SPY": await get_return_metrics("SPY", 120, check_time),
            sector_etf: await get_return_metrics(sector_etf, 120, check_time),
            "QQQ": await get_return_metrics("QQQ", 120, check_time),
        }

        def _pair(benchmark: str, bench_metrics: dict[str, Any]) -> dict[str, Any]:
            if bench_metrics.get("error"):
                return {"benchmark": benchmark, "data_quality": "MISSING"}
            sym_20 = symbol_metrics.get("ret_20d")
            sym_60 = symbol_metrics.get("ret_60d")
            bench_20 = bench_metrics.get("ret_20d")
            bench_60 = bench_metrics.get("ret_60d")
            return {
                "benchmark": benchmark,
                "symbol_ret_20d": sym_20,
                "benchmark_ret_20d": bench_20,
                "rel_ret_20d": sym_20 - bench_20 if sym_20 is not None and bench_20 is not None else None,
                "symbol_ret_60d": sym_60,
                "benchmark_ret_60d": bench_60,
                "rel_ret_60d": sym_60 - bench_60 if sym_60 is not None and bench_60 is not None else None,
            }

        return {
            "vs_spy": _pair("SPY", benchmarks["SPY"]),
            "vs_sector": _pair(sector_etf, benchmarks[sector_etf]),
            "vs_qqq": _pair("QQQ", benchmarks["QQQ"]),
        }

    def _apply_analysis_gate(self, trigger: Trigger, technical_score: float | None) -> bool:
        if trigger.trigger_type in SECOND_ORDER_TRIGGERS:
            trigger.details["analysis_gate"] = True
            trigger.details["analysis_gate_reason"] = "second_order"
            return True
        if trigger.trigger_type in FUNDAMENTAL_OVERRIDE_TRIGGERS:
            trigger.details["analysis_gate"] = True
            trigger.details["analysis_gate_reason"] = "fundamental_override"
            return True
        if trigger.trigger_type in OVERRIDE_TECH_TRIGGERS:
            if technical_score is not None and technical_score >= OVERRIDE_TECH_SCORE:
                trigger.details["analysis_gate"] = True
                trigger.details["analysis_gate_reason"] = f"override_tech_score_{OVERRIDE_TECH_SCORE:.2f}"
                return True
            trigger.details["analysis_gate"] = False
            trigger.details["analysis_gate_reason"] = "override_threshold_not_met"
            return False

        trigger.details["analysis_gate"] = False
        trigger.details["analysis_gate_reason"] = "simple_trigger"
        return False
    
    async def check_hourly_technical_triggers(
        self,
        symbol: str,
        check_time: datetime,
    ) -> list[Trigger]:
        """Check for hourly technical triggers."""
        triggers = []
        
        try:
            daily = await get_indicators(symbol, 60, check_time)
            if daily.get("error"):
                return triggers

            indicators = await get_hourly_indicators(symbol, check_time.date(), check_time)
            if indicators.get("error"):
                return triggers
            
            state_tags = indicators.get("state_tags", [])
            rel_strength = await self._get_relative_strength(symbol, check_time)
            rel_spy = rel_strength.get("vs_spy") if rel_strength else {}
            rel_sector = rel_strength.get("vs_sector") if rel_strength else {}

            ma20_state = (daily.get("trend") or {}).get("ma20")
            ma50_state = (daily.get("trend") or {}).get("ma50")
            adx14 = float(daily.get("adx14") or 0)
            rsi14 = float(daily.get("rsi14") or 0)
            daily_price = float(daily.get("current_price") or 0)
            intraday_trend = indicators.get("intraday_trend")
            rsi_hourly = indicators.get("rsi_hourly", 50)
            
            # Volume surge (>3x average to be significant)
            volume_profile = indicators.get("volume_profile", {})
            vol_ratio = volume_profile.get("current_vs_avg", 0)
            if (
                "HOURLY_VOLUME_SPIKE" in state_tags
                and vol_ratio > 3.0
                and ma20_state == "ABOVE"
                and ma50_state == "ABOVE"
                and rsi14 >= 50
                and adx14 >= 18
                and rel_spy.get("rel_ret_20d", 0) >= 0
            ):
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="volume_surge",
                    priority="LOW",
                    details={
                        "current_hour_volume": volume_profile.get("current_hour"),
                        "avg_per_hour": volume_profile.get("avg_per_hour"),
                        "ratio": f"{vol_ratio:.1f}x",
                        "rel_strength": rel_strength,
                    },
                    detected_at=check_time,
                ))
            
            # RSI oversold bounce
            if (
                "HOURLY_OVERSOLD" in state_tags
                and ma20_state == "ABOVE"
                and rsi14 >= 35
                and intraday_trend == "UP"
                and rel_spy.get("rel_ret_20d", 0) >= -0.01
            ):
                # Require some sign of reversal or at least not crashing?
                # For now just keep it but ensure priority is managed
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="hourly_bounce",
                    priority="MEDIUM",
                    details={
                        "rsi": indicators.get("rsi_hourly"),
                        "intraday_trend": indicators.get("intraday_trend"),
                        "current_price": indicators.get("current_price"),
                        "bar_time": indicators.get("bar_time"),
                        "rel_strength": rel_strength,
                    },
                    detected_at=check_time,
                ))
            
            # Near VWAP support (Tighter range: -0.8% to +0.2%)
            vwap_dist = indicators.get("vwap_distance_pct", 0)
            # Ensure not overbought (RSI < 60)
            rsi = indicators.get("rsi_hourly", 50)
            
            if (
                -0.6 < vwap_dist < 0.2
                and rsi < 65
                and ma20_state == "ABOVE"
                and ma50_state == "ABOVE"
                and rsi14 >= 40
                and rel_spy.get("rel_ret_20d", 0) >= 0
                and rel_sector.get("rel_ret_20d", 0) >= 0
                and intraday_trend == "UP"
            ):
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="vwap_support",
                    priority="MEDIUM",
                    details={
                        "vwap_distance_pct": vwap_dist,
                        "current_price": indicators.get("current_price"),
                        "vwap": indicators.get("vwap"),
                        "bar_time": indicators.get("bar_time"),
                        "rel_strength": rel_strength,
                    },
                    detected_at=check_time,
                ))

            # Hourly breakout vs daily levels
            levels = await get_price_levels(symbol, 60, check_time)
            high_20d = float(levels.get("high_20d") or 0)
            high_60d = float(levels.get("high_60d") or 0)
            current_price = float(indicators.get("current_price") or 0)
            breakout_level = max(high_20d, high_60d)

            trend_ok = ma20_state == "ABOVE" and ma50_state == "ABOVE"
            rel_ok = (
                rel_spy.get("rel_ret_20d", 0) >= 0
                and rel_sector.get("rel_ret_20d", 0) >= 0
            )
            if (
                breakout_level > 0
                and current_price > breakout_level * 1.001
                and vol_ratio > 2.0
                and rsi_hourly >= 55
                and intraday_trend == "UP"
                and adx14 >= 20
                and daily_price >= 5.0
                and (trend_ok or rel_ok)
            ):
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="hourly_breakout",
                    priority="HIGH",
                    details={
                        "breakout_level": round(breakout_level, 4),
                        "current_price": current_price,
                        "volume_ratio": round(vol_ratio, 2),
                        "rsi_hourly": rsi_hourly,
                        "trend_ok": trend_ok,
                        "rel_ok": rel_ok,
                        "rel_strength": rel_strength,
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
        
        # Get watchlist items (with scores)
        watchlist_items = await self.get_watchlist(check_time)
        if not watchlist_items:
            _logger.info(f"No watchlist found for {check_time.date()}")
            return []
        
        _logger.info(f"Scanning {len(watchlist_items)} watchlist symbols for triggers...")
        
        all_triggers = []
        gated_triggers = []
        suppressed = 0
        
        for item in watchlist_items:
            symbol = item["symbol"]
            tech_score = item["technical_score"]
            profile_score = item["profile_score"]
            
            # Helper to attach scores
            def attach_scores(t: Trigger | None):
                if t:
                    t.details["technical_score"] = tech_score
                    t.details["profile_score"] = profile_score
                    return t
                return None
            
            symbol_triggers = []

            # Check all trigger types
            earnings = await self.check_earnings_trigger(symbol, check_time)
            if attach_scores(earnings):
                symbol_triggers.append(earnings)
            
            sec_8k = await self.check_sec_8k_trigger(symbol, check_time)
            if attach_scores(sec_8k):
                symbol_triggers.append(sec_8k)
            
            news = await self.check_news_trigger(symbol, check_time)
            if attach_scores(news):
                symbol_triggers.append(news)
            
            hourly_triggers = await self.check_hourly_technical_triggers(symbol, check_time)
            for t in hourly_triggers:
                attach_scores(t)
            symbol_triggers.extend(hourly_triggers)

            # Second-order triggers
            bad_news = await self.check_bad_news_no_drop_trigger(symbol, check_time)
            if attach_scores(bad_news):
                symbol_triggers.append(bad_news)

            sector_laggard = await self.check_sector_laggard_trigger(symbol, check_time)
            if attach_scores(sector_laggard):
                symbol_triggers.append(sector_laggard)

            compression = await self.check_volatility_compression_trigger(symbol, check_time)
            if attach_scores(compression):
                symbol_triggers.append(compression)

            # Apply analysis gate per trigger
            for trigger in symbol_triggers:
                tech_score = trigger.details.get("technical_score") if trigger.details else None
                if self._apply_analysis_gate(trigger, tech_score):
                    gated_triggers.append(trigger)
                else:
                    suppressed += 1

            all_triggers.extend(symbol_triggers)
        
        # Sort by priority
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        gated_triggers.sort(key=lambda t: priority_order[t.priority])
        
        # Filter out already processed triggers
        # This prevents re-alerting on the same news/level if we've already analyzed it
        from eiqora_v2.live.analysis_logger import is_trigger_processed
        
        new_triggers = []
        for t in gated_triggers:
            # Check if we've processed this exact trigger detail before
            if not await is_trigger_processed(t.details, t.symbol):
                new_triggers.append(t)
            else:
                _logger.debug(f"Skipping known trigger: {t.symbol} {t.trigger_type}")
        
        _logger.info(
            "Found %s new triggers (filtered from %s, suppressed=%s)",
            len(new_triggers),
            len(gated_triggers),
            suppressed,
        )
        return new_triggers


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
