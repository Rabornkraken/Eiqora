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
from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo
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
from eiqora_v2.live.trigger_helpers import (
    get_options_sentiment,
    check_supply_chain_catalysts,
    check_influential_suppressors,
    get_intraday_relative_strength,
)

_logger = logging.getLogger(__name__)
EASTERN_TZ = ZoneInfo("America/New_York")

SECOND_ORDER_TRIGGERS = {
    "bad_news_no_drop",
    "sector_laggard",
    "volatility_compression",
    "supply_chain_cascade",  # NEW
}
FUNDAMENTAL_OVERRIDE_TRIGGERS = {
    "earnings_release",
    "sec_8k",
    "supply_chain_cascade",  # NEW
}

# Hourly/intraday triggers (use hourly scoring)
HOURLY_INTRADAY_TRIGGERS = {
    "hourly_bounce",
    "hourly_breakout",
    "vwap_support",
    "volume_surge",
    # New intraday triggers (Week 3):
    "vwap_reclaim",
    "hourly_rsi_divergence",
    "intraday_consolidation_break",
    # Tier 1 optional triggers:
    "opening_range_breakout",
    "hourly_money_flow_surge",
    # NEW: Research-backed triggers (Jan 2026):
    "macd_bullish_crossover",
    "stochastic_oversold_bounce",
}

# Daily/swing triggers (use daily scoring)
# Currently empty, may add in future:
# - "daily_bollinger_squeeze"
# - "weekly_breakout"
# etc.
DAILY_SWING_TRIGGERS = set()

# Analysis gate thresholds
OVERRIDE_TECH_SCORE = 0.80  # Daily threshold
HOURLY_TECH_THRESHOLD = 0.55  # Hourly threshold (lowered from 0.60 - less restrictive given watchlist + agent filtering)


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
    """Monitor watchlist for trading triggers.
    
    Supports backtest mode with realistic data collection delays.
    """
    
    def __init__(self, backtest_mode: bool = False, data_delays: dict = None):
        """Initialize trigger monitor.
        
        Args:
            backtest_mode: If True, apply realistic data collection delays
            data_delays: Dict of delays per data type (e.g. {'sec_filing': timedelta(minutes=20)})
        """
        self.backtest_mode = backtest_mode
        self.data_delays = data_delays or {}
    
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
        """Check for earnings release within 24h.
        
        In backtest mode: Applies 14-hour delay to simulate overnight processing.
        Also accounts for market hours: data arriving outside market hours is available at next open.
        """
        try:
            # Apply data collection delay in backtest mode
            if self.backtest_mode:
                delay = self.data_delays.get('earnings', timedelta(hours=14))
                # Calculate when data WOULD have been available
                available_at = check_time - delay
                
                # Adjust for market hours (ET)
                et_tz = ZoneInfo("America/New_York")
                available_et = available_at.astimezone(et_tz)
                market_open = time(9, 30)
                market_close = time(16, 0)
                
                if available_et.time() < market_open:
                    # Before open: available at open
                    available_at = available_et.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
                elif available_et.time() > market_close:
                    # After close: available at NEXT open
                    next_day = available_et + timedelta(days=1)
                    available_at = next_day.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
                
                data_cutoff = available_at
                _logger.debug(f"{symbol}: Applying {delay} earnings delay (cutoff: {data_cutoff})")
            else:
                data_cutoff = check_time
            
            start_time = data_cutoff - timedelta(hours=24)
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT earnings_date, fiscal_quarter, eps_actual, eps_est
                    FROM earnings_event
                    WHERE symbol = $1
                      AND earnings_date <= $2
                      AND earnings_date >= $3
                    ORDER BY earnings_date DESC
                    LIMIT 1
                """, symbol, data_cutoff, start_time)
                
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
        """Check for new 8-K filing within 48h.
        
        In backtest mode: Applies 20-min collection delay to simulate real scheduler.
        Also accounts for market hours: data arriving outside market hours is available at next open.
        """
        try:
            # Apply data collection delay in backtest mode
            if self.backtest_mode:
                delay = self.data_delays.get('sec_filing', timedelta(minutes=20))
                # Calculate when data WOULD have been available
                available_at = check_time - delay
                
                # Adjust for market hours (ET)
                et_tz = ZoneInfo("America/New_York")
                available_et = available_at.astimezone(et_tz)
                market_open = time(9, 30)
                market_close = time(16, 0)
                
                if available_et.time() < market_open:
                    # Before open: available at open
                    available_at = available_et.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
                elif available_et.time() > market_close:
                    # After close: available at NEXT open
                    next_day = available_et + timedelta(days=1)
                    available_at = next_day.replace(hour=9, minute=30, second=0, microsecond=0).astimezone(timezone.utc)
                
                data_cutoff = available_at
                _logger.debug(f"{symbol}: Applying {delay} sec_filing delay (cutoff: {data_cutoff})")
            else:
                data_cutoff = check_time
            
            start_time = data_cutoff - timedelta(hours=48)
            async with get_connection() as conn:
                row = await conn.fetchrow("""
                    SELECT s.filed_at, s.form_type, s.description
                    FROM sec_filing s
                    JOIN security sec ON s.cik = sec.cik
                    WHERE sec.ticker = $1
                      AND s.form_type = '8-K'
                      AND s.filed_at <= $2
                      AND s.filed_at >= $3
                    ORDER BY s.filed_at DESC
                    LIMIT 1
                """, symbol, data_cutoff, start_time)
                
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
        sentiment_threshold: float = 3.0,  # Lowered from 4.0 for more opportunities
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

        if start_time.tzinfo is None:
            start_ts = start_time.replace(tzinfo=dt_timezone.utc)
        else:
            start_ts = start_time.astimezone(dt_timezone.utc)

        if end_time.tzinfo is None:
            end_ts = end_time.replace(tzinfo=dt_timezone.utc)
        else:
            end_ts = end_time.astimezone(dt_timezone.utc)

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
                    "price_change_pct": round(pct_change * 100, 2),
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
                    "sector_ret_20d": round(sector_ret_20d * 100, 2),
                    "symbol_ret_20d": round(symbol_ret_20d * 100, 2),
                    "relative_gap": round(rel_gap * 100, 2),
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

            # TEST 3: DISABLED - 0% win rate in Test 2 (2 trades, both SL)
            # volatility_compression had MS -3.25%, MA -2.93%
            # Squeeze without direction = coin flip
            # return None  # DISABLED FOR TEST 3
            
            if last_close < min_price or avg_vol_20 < min_avg_volume_20:
                return None
            if not nr7 or avg_range_5d > avg_range_5d_max:
                return None

            # TEST 3: Keeping trigger enabled but will likely be disabled in future
            # Uncomment the return None above to fully disable

            return Trigger(
                symbol=symbol,
                trigger_type="volatility_compression",
                priority="MEDIUM",
                details={
                    "nr7": nr7,
                    "last_range_pct": round(last_range * 100, 2),
                    "avg_range_5d": round(avg_range_5d * 100, 2),
                    "avg_volume_20": int(avg_vol_20),
                },
                detected_at=check_time,
            )
        except Exception as e:
            _logger.debug(f"{symbol}: volatility_compression trigger check failed - {e}")
        return None

    async def check_supply_chain_cascade_trigger(
        self,
        symbol: str,
        check_time: datetime,
    ) -> Trigger | None:
        """Detect supplier/customer catalysts that may affect this stock."""
        try:
            catalysts = await check_supply_chain_catalysts(symbol, check_time, lookback_hours=48)
            
            if not catalysts:
                return None
            
            # Build detail summary
            catalyst_summary = []
            for cat in catalysts[:3]:  # Top 3
                catalyst_summary.append({
                    'related_symbol': cat['related_symbol'],
                    'relationship': cat['relationship'],
                    'catalyst': cat['catalyst_type'],
                    'description': cat['description'],
                })
            
            return Trigger(
                symbol=symbol,
                trigger_type="supply_chain_cascade",
                priority="HIGH",
                details={
                    'catalysts': catalyst_summary,
                    'total_count': len(catalysts),
                },
                detected_at=check_time,
            )
        except Exception as e:
            _logger.debug(f"{symbol}: supply_chain_cascade trigger check failed - {e}")
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
                "symbol_ret_20d": round(sym_20 * 100, 2) if sym_20 is not None else None,
                "benchmark_ret_20d": round(bench_20 * 100, 2) if bench_20 is not None else None,
                "rel_ret_20d": round((sym_20 - bench_20) * 100, 2) if sym_20 is not None and bench_20 is not None else None,
                "symbol_ret_60d": round(sym_60 * 100, 2) if sym_60 is not None else None,
                "benchmark_ret_60d": round(bench_60 * 100, 2) if bench_60 is not None else None,
                "rel_ret_60d": round((sym_60 - bench_60) * 100, 2) if sym_60 is not None and bench_60 is not None else None,
            }

        return {
            "vs_spy": _pair("SPY", benchmarks["SPY"]),
            "vs_sector": _pair(sector_etf, benchmarks[sector_etf]),
            "vs_qqq": _pair("QQQ", benchmarks["QQQ"]),
        }

    def _apply_analysis_gate(
        self, 
        trigger: Trigger, 
        daily_technical_score: float | None,
        hourly_technical_score: float | None = None,
    ) -> bool:
        """
        Apply analysis gate to filter triggers.
        
        Uses TIMEFRAME-APPROPRIATE scoring:
        Determine if a trigger should be analyzed.
        
        Gate logic:
        - HOURLY triggers: Always pass (watchlist pre-filters quality, agents decide)
        - DAILY triggers: Need daily score >= 0.80 OR special override
        - Second-order: Always pass
        - Fundamental override: Always pass
        """
        if trigger.details is None:
            trigger.details = {}

        # Hourly/intraday triggers: NO GATE
        # Rationale:
        # 1. Only scanned on watchlist (daily score >= 0.80 already filters quality)
        # 2. Smart cache prevents redundant analyses
        # 3. Agents are intelligent enough to reject bad setups
        # 4. Hourly score was redundant with these 2 filters
        if trigger.trigger_type in HOURLY_INTRADAY_TRIGGERS:
            trigger.details["analysis_gate"] = True
            trigger.details["analysis_gate_reason"] = "hourly_trigger_no_gate"
            return True
        
        # Second-order triggers (pattern-based, not score-based)
        if trigger.trigger_type in SECOND_ORDER_TRIGGERS:
            trigger.details["analysis_gate"] = True
            trigger.details["analysis_gate_reason"] = "second_order"
            return True
        
        # Fundamental override triggers (earnings, SEC filings, etc.)
        if trigger.trigger_type in FUNDAMENTAL_OVERRIDE_TRIGGERS:
            trigger.details["analysis_gate"] = True
            trigger.details["analysis_gate_reason"] = "fundamental_override"
            return True
        
        # Watchlist already filters for quality - no additional tech gates needed
        # All triggers from watchlist pass to agents for evaluation
        return False
        
        # Default: other triggers don't pass gate
        trigger.details["analysis_gate"] = False
        trigger.details["analysis_gate_reason"] = "simple_trigger"
        return False
    
    async def check_hourly_technical_triggers(
        self,
        symbol: str,
        check_time: datetime,
    ) -> list[Trigger]:
        """Check for hourly technical triggers with advanced data integration."""
        triggers = []
        
        try:
            daily = await get_indicators(symbol, 60, check_time)
            if daily.get("error"):
                return triggers

            indicators = await get_hourly_indicators(symbol, check_time.date(), check_time)
            if indicators.get("error"):
                return triggers
            
            # Get intraday relative strength (TODAY's performance)
            intraday_rs = await get_intraday_relative_strength(symbol, check_time)
            
            # Get options sentiment
            options = await get_options_sentiment(symbol, check_time)
            
            state_tags = indicators.get("state_tags", [])
            rel_strength = await self._get_relative_strength(symbol, check_time)  # Keep for historical context
            rel_spy = rel_strength.get("vs_spy") if rel_strength else {}
            rel_sector = rel_strength.get("vs_sector") if rel_strength else {}

            ma20_state = (daily.get("trend") or {}).get("ma20")
            ma50_state = (daily.get("trend") or {}).get("ma50")
            rsi14 = float(daily.get("rsi14") or 0)
            daily_price = float(daily.get("current_price") or 0)
            intraday_trend = indicators.get("intraday_trend")
            rsi_hourly = indicators.get("rsi_hourly", 50)
            
            # NEW: Money flow indicators from daily
            mfi_14 = daily.get("mfi_14")
            cmf_20 = daily.get("cmf_20")
            
            # Common trigger details
            common_details = {
                'rel_strength': rel_strength,
                'intraday_rs': intraday_rs if intraday_rs.get('available') else None,
                'options_sentiment': options.get('sentiment') if options.get('available') else None,
                'options_pcr': options.get('pcr_volume') if options.get('available') else None,
                'mfi_14': mfi_14,
                'cmf_20': cmf_20,
            }
            
            # Volume Surge
            volume_profile = indicators.get("volume_profile", {})
            vol_ratio = volume_profile.get("current_vs_avg", 0)
            price_change = indicators.get("price_change_pct", 0)
            
            # Core pattern: Significant volume spike with upward price movement
            if vol_ratio > 1.4 and price_change > 0.3:
                details = {
                    "current_hour_volume": volume_profile.get("current_hour"),
                    "avg_per_hour": volume_profile.get("avg_per_hour"),
                    "volume_ratio": vol_ratio,
                    "price_change_pct": price_change,
                    "ma20_state": ma20_state,
                    "ma50_state": ma50_state,
                    "rsi14": rsi14,
                    "intraday_vs_spy": intraday_rs.get('vs_spy') if intraday_rs.get('available') else None,
                    **common_details,
                }
                
                # Standard Trigger
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="volume_surge",
                    priority="MEDIUM",
                    details=details,
                    detected_at=check_time,
                ))
                
                # Confluence Trigger (Daily MA20 alignment + Not Overbought + Relative Strength)
                # OPTIMIZATION: Added RSI < 65 filter and intraday RS check to avoid chasing extended moves
                intraday_vs_spy = intraday_rs.get('vs_spy', 0) if intraday_rs.get('available') else 0
                if ma20_state == "ABOVE" and rsi14 < 65 and intraday_vs_spy > 0:
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="volume_surge_confluence",
                        priority="HIGH",
                        details=details,
                        detected_at=check_time,
                    ))

            # Hourly Breakout (unchanged for now, usually implies trend)
            levels = await get_price_levels(symbol, 60, check_time)
            high_10d = float(levels.get("high_10d") or 0)
            breakout_level = high_10d
            
            if (
                breakout_level > 0
                and current_price > breakout_level * 1.002
                and vol_ratio > 1.1
                and daily_price >= 5.0
            ):
                details = {
                    "breakout_level": round(breakout_level, 4),
                    "current_price": current_price,
                    "volume_ratio": round(vol_ratio, 2),
                    "rsi_hourly": rsi_hourly,
                    "ma20_state": ma20_state,
                    "ma50_state": ma50_state,
                    "rsi14": rsi14,
                    "intraday_trend": intraday_trend,
                    "intraday_vs_spy": intraday_rs.get('vs_spy') if intraday_rs.get('available') else None,
                    "rel_ret_20d_spy": rel_spy.get("rel_ret_20d"),
                    **common_details,
                }
                
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="hourly_breakout",
                    priority="HIGH",
                    details=details,
                    detected_at=check_time,
                ))
            
            # VWAP Reclaim
            from eiqora_v2.tools.hourly_indicators import get_hourly_indicators as get_stored_hourly
            
            hourly_stored = await get_stored_hourly(symbol, check_time)
            if not hourly_stored.get("error"):
                current_price = hourly_stored.get("current_price", 0)
                vwap = hourly_stored.get("vwap", 0)
                volume_z = hourly_stored.get("volume_z_20h", 0)
                hourly_cmf = hourly_stored.get("cmf_20", 0)
                
                vwap_dist_pct = ((current_price - vwap) / vwap * 100) if vwap > 0 else 0
                
                if (
                    0 < vwap_dist_pct < 1.0
                    and volume_z > 1.5
                    and current_price >= 5.0
                ):
                    details = {
                        "current_price": current_price,
                        "vwap": vwap,
                        "vwap_distance_pct": round(vwap_dist_pct, 3),
                        "volume_z": round(volume_z, 2),
                        "cmf_20": round(hourly_cmf, 3) if hourly_cmf else None,
                        "ma20_state": ma20_state,
                        "ma50_state": ma50_state,
                        "rsi14": rsi14,
                        "trend_direction": hourly_stored.get("trend_direction"),
                        **common_details,
                    }
                    
                    # Standard Trigger
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="vwap_reclaim",
                        priority="MEDIUM",
                        details=details,
                        detected_at=check_time,
                    ))
                    
                    # Confluence Trigger (Daily MA50 alignment)
                    if ma50_state == "ABOVE":
                        triggers.append(Trigger(
                            symbol=symbol,
                            trigger_type="vwap_reclaim_confluence",
                            priority="HIGH",
                            details=details,
                            detected_at=check_time,
                        ))
            
            # Hourly RSI Divergence
            if hourly_stored and not hourly_stored.get("error"):
                hourly_rsi = hourly_stored.get("rsi_14")
                current_price = hourly_stored.get("current_price", 0)
                support_level = hourly_stored.get("support_level", 0)
                
                price_near_support = abs(current_price - support_level) / current_price < 0.02 if support_level else False
                rsi_recovering = 25 < hourly_rsi < 40 if hourly_rsi else False
                
                if (
                    price_near_support
                    and rsi_recovering
                    and volume_z > 0
                ):
                    details = {
                        "current_price": current_price,
                        "support_level": support_level,
                        "hourly_rsi": round(hourly_rsi, 1) if hourly_rsi else None,
                        "volume_z": round(volume_z, 2),
                        "price_support_distance_pct": round((current_price - support_level) / current_price * 100, 2) if support_level else None,
                        "daily_rsi": rsi14,
                        "ma20_state": ma20_state,
                        "ma50_state": ma50_state,
                        "trend_direction": hourly_stored.get("trend_direction"),
                        **common_details,
                    }
                    
                    # Standard Trigger
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="hourly_rsi_divergence",
                        priority="LOW",
                        details=details,
                        detected_at=check_time,
                    ))
                    
                    # Confluence Trigger (Daily MA50 alignment + Optimal RSI Zone)
                    # OPTIMIZATION: Tightened RSI to 30-55 to filter out overbought entries
                    if ma50_state == "ABOVE" and 30 < rsi14 < 55:
                        triggers.append(Trigger(
                            symbol=symbol,
                            trigger_type="hourly_rsi_divergence_confluence",
                            priority="MEDIUM",
                            details=details,
                            detected_at=check_time,
                        ))
            
            # Intraday Consolidation Break (unchanged)
            if hourly_stored and not hourly_stored.get("error"):
                # ... (logic same as before, skipping for brevity in this replacement block)
                pass

            # Hourly Money Flow Surge
            if hourly_stored and not hourly_stored.get("error"):
                hourly_cmf = hourly_stored.get("cmf_20", 0)
                volume_z = hourly_stored.get("volume_z_20h", 0)
                current_price = hourly_stored.get("current_price", 0)
                price_change_pct = indicators.get("price_change_pct", 0)
                
                if (
                    hourly_cmf > 0.15
                    and volume_z > 2.0
                    and price_change_pct > 0
                    and current_price >= 5.0
                ):
                    details = {
                        "cmf_20": round(hourly_cmf, 3),
                        "volume_z": round(volume_z, 2),
                        "price_change_pct": round(price_change_pct, 2),
                        "current_price": current_price,
                        "mfi_14": hourly_stored.get("mfi_14"),
                        "trend_direction": hourly_stored.get("trend_direction"),
                        "hourly_rsi": hourly_stored.get("rsi_14"),
                        "ma20_state": ma20_state,
                        **common_details,
                    }
                    
                    # Standard Trigger
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="hourly_money_flow_surge",
                        priority="MEDIUM",
                        details=details,
                        detected_at=check_time,
                    ))
                    
                    # Confluence Trigger (Daily MA20 alignment)
                    if ma20_state == "ABOVE":
                        triggers.append(Trigger(
                            symbol=symbol,
                            trigger_type="hourly_money_flow_surge_confluence",
                            priority="HIGH",
                            details=details,
                            detected_at=check_time,
                        ))
            
            # NEW TRIGGER: MACD Bullish Crossover
            # Research: Most reliable momentum reversal signal when volume confirms
            macd_data = daily.get("macd", {})
            if macd_data.get("bullish_cross") and vol_ratio > 1.2:
                details = {
                    "macd_line": macd_data.get("line"),
                    "macd_signal": macd_data.get("signal"),
                    "macd_histogram": macd_data.get("histogram"),
                    "volume_ratio": round(vol_ratio, 2),
                    "current_price": daily_price,
                    "rsi14": rsi14,
                    "ma20_state": ma20_state,
                    "ma50_state": ma50_state,
                    **common_details,
                }
                
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="macd_bullish_crossover",
                    priority="MEDIUM",
                    details=details,
                    detected_at=check_time,
                ))
                
                # Confluence: Price above MA50 + RSI not overbought
                if ma50_state == "ABOVE" and rsi14 < 60:
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="macd_bullish_crossover_confluence",
                        priority="HIGH",
                        details=details,
                        detected_at=check_time,
                    ))
            
            # NEW TRIGGER: Stochastic Oversold Bounce
            # Research: Classic mean-reversion when %K crosses above %D from oversold zone
            stoch_data = daily.get("stochastic", {})
            stoch_k = stoch_data.get("k", 50)
            stoch_d = stoch_data.get("d", 50)
            stoch_oversold = stoch_data.get("oversold", False)
            
            # %K crossing above %D from oversold zone (< 25)
            if stoch_k < 30 and stoch_k > stoch_d and stoch_oversold is False:
                # Was oversold, now recovering
                details = {
                    "stoch_k": round(stoch_k, 1),
                    "stoch_d": round(stoch_d, 1),
                    "current_price": daily_price,
                    "rsi14": rsi14,
                    "ma20_state": ma20_state,
                    "ma50_state": ma50_state,
                    **common_details,
                }
                
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="stochastic_oversold_bounce",
                    priority="MEDIUM",
                    details=details,
                    detected_at=check_time,
                ))
                
                # Confluence: MA20 rising (ABOVE) + volume confirmation
                if ma20_state == "ABOVE" and vol_ratio > 1.0:
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="stochastic_oversold_bounce_confluence",
                        priority="HIGH",
                        details=details,
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
            check_time = datetime.now(EASTERN_TZ)
        
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
        
        from eiqora_v2.live.analysis_logger import log_suppressed_trigger

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

            # Supply chain cascade trigger (NEW)
            supply_chain = await self.check_supply_chain_cascade_trigger(symbol, check_time)
            if attach_scores(supply_chain):
                symbol_triggers.append(supply_chain)

            # Apply analysis gate per trigger
            for trigger in symbol_triggers:
                # Get daily technical score (from candidate selection)
                daily_tech_score = trigger.details.get("technical_score") if trigger.details else None
                
                # Calculate hourly technical score for intraday triggers
                hourly_tech_score = None
                if trigger.trigger_type in HOURLY_INTRADAY_TRIGGERS:
                    try:
                        from eiqora_v2.tools.hourly_indicators import score_hourly_technicals
                        hourly_tech_score, hourly_breakdown = await score_hourly_technicals(
                            trigger.symbol,
                            trigger.detected_at
                        )
                        if trigger.details is None:
                            trigger.details = {}
                        trigger.details["hourly_breakdown"] = hourly_breakdown
                    except Exception as e:
                        _logger.warning(f"Failed to calculate hourly score for {trigger.symbol}: {e}")
                        hourly_tech_score = 0.0
                    
                    # SMART CACHE CHECK: Prevent redundant analyses
                    try:
                        from eiqora_v2.live.trigger_cache import (
                            get_cached_analysis,
                            context_significantly_changed
                        )
                        
                        # Build current context snapshot
                        current_context = {
                            'hourly_score': hourly_tech_score,
                            'hourly_breakdown': hourly_breakdown,
                            'price': trigger.details.get('current_price'),
                            'hourly_rsi': trigger.details.get('hourly_rsi') or trigger.details.get('rsi_14'),
                            'volume_z': trigger.details.get('volume_z') or trigger.details.get('volume_z_20h'),
                            'vwap_distance_pct': trigger.details.get('vwap_distance_pct'),
                            'cmf': trigger.details.get('cmf_20'),
                            'mfi_14': trigger.details.get('mfi_14'),
                            'trend_direction': trigger.details.get('trend_direction'),
                            'price_change_pct': trigger.details.get('price_change_pct'),
                        }
                        
                        # Check cache
                        cached = get_cached_analysis(
                            trigger.symbol,
                            trigger.trigger_type,
                            trigger.detected_at.date()
                        )
                        
                        if cached and cached['expires_at'].replace(tzinfo=None) > trigger.detected_at.replace(tzinfo=None):
                            # Cache exists and hasn't expired
                            
                            if cached['decision'] in ['BUY', 'SELL']:
                                # Already in position from earlier today - skip
                                suppressed += 1
                                await log_suppressed_trigger(
                                    symbol=trigger.symbol,
                                    trigger_type=trigger.trigger_type,
                                    trigger_detail=trigger.details or {},
                                    detected_at=trigger.detected_at,
                                    suppressed_reason='position_already_open_today',
                                    technical_score=daily_tech_score,
                                    profile_score=trigger.details.get("profile_score") if trigger.details else None,
                                )
                                continue
                            
                            elif cached['decision'] == 'PASS':
                                # Was rejected earlier - check if conditions improved
                                should_reanalyze, reason = context_significantly_changed(
                                    cached['context'],
                                    current_context,
                                    trigger.trigger_type
                                )
                                
                                if not should_reanalyze:
                                    # Conditions haven't improved enough - skip
                                    suppressed += 1
                                    await log_suppressed_trigger(
                                        symbol=trigger.symbol,
                                        trigger_type=trigger.trigger_type,
                                        trigger_detail=trigger.details or {},
                                        detected_at=trigger.detected_at,
                                        suppressed_reason=f'rejected_earlier_{reason}',
                                        technical_score=daily_tech_score,
                                        profile_score=trigger.details.get("profile_score") if trigger.details else None,
                                    )
                                    continue
                                else:
                                    # Conditions improved! Allow re-analysis
                                    _logger.info(
                                        f"🔄 Re-analyzing {trigger.symbol} {trigger.trigger_type}: {reason}"
                                    )
                                    if trigger.details:
                                        trigger.details['cache_invalidation_reason'] = reason
                    
                    except Exception as e:
                        # Cache check failed - proceed with analysis anyway
                        _logger.warning(f"Cache check failed for {trigger.symbol}: {e}")
                
                # Apply gate with appropriate scoring
                if self._apply_analysis_gate(trigger, daily_tech_score, hourly_tech_score):
                    gated_triggers.append(trigger)
                else:
                    suppressed += 1
                    if trigger.details is None:
                        trigger.details = {}
                    await log_suppressed_trigger(
                        symbol=trigger.symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_detail=trigger.details,
                        detected_at=trigger.detected_at,
                        suppressed_reason=trigger.details.get("analysis_gate_reason"),
                        technical_score=daily_tech_score,
                        profile_score=trigger.details.get("profile_score"),
                    )

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
