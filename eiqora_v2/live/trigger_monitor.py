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
        """
        try:
            # Apply data collection delay in backtest mode
            if self.backtest_mode:
                delay = self.data_delays.get('earnings', timedelta(hours=14))
                data_cutoff = check_time - delay
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
        """
        try:
            # Apply data collection delay in backtest mode
            if self.backtest_mode:
                delay = self.data_delays.get('sec_filing', timedelta(minutes=20))
                data_cutoff = check_time - delay
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

            # TEST 3: DISABLED - 0% win rate in Test 2 (2 trades, both SL)
            # volatility_compression had MS -3.25%, MA -2.93%
            # Squeeze without direction = coin flip
            return None  # DISABLED FOR TEST 3
            
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
                    "last_range_pct": round(last_range, 4),
                    "avg_range_5d": round(avg_range_5d, 4),
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
            
            # Volume Surge - Simplified to basic pattern detection
            volume_profile = indicators.get("volume_profile", {})
            vol_ratio = volume_profile.get("current_vs_avg", 0)
            price_change = indicators.get("price_change_pct", 0)
            
            # Core pattern: Significant volume spike with upward price movement
            if vol_ratio > 1.4 and price_change > 0.3:  # TEST 2: Lowered to 1.4x based on actual market data (was 2.0x)
                details = {
                    "current_hour_volume": volume_profile.get("current_hour"),
                    "avg_per_hour": volume_profile.get("avg_per_hour"),
                    "volume_ratio": vol_ratio,
                    "price_change_pct": price_change,
                    # Context for agent analysis (not filters)
                    "ma20_state": ma20_state,
                    "ma50_state": ma50_state,
                    "rsi14": rsi14,
                    "intraday_vs_spy": intraday_rs.get('vs_spy') if intraday_rs.get('available') else None,
                    **common_details,
                }
                
                triggers.append(Trigger(
                    symbol=symbol,
                    trigger_type="volume_surge",
                    priority="MEDIUM",  # Upgraded from LOW since less filtered
                    details=details,
                    detected_at=check_time,
                ))
            
            # DISABLED: RSI Oversold Bounce - 100% FAILURE RATE in backtesting
            # Problem: Enters at local highs during pullbacks, catches falling knives
            # All 6 trades hit stop-loss in Jan 1-10 backtest
            # TODO: Rebuild with much stricter conditions OR remove entirely
            # Core pattern: Hourly RSI showing oversold in an uptrending stock
            # vol_ratio = volume_profile.get("current_vs_avg", 0)
            # 
            # uptrend_confirmed = ma20_state == "ABOVE" and ma50_state == "ABOVE"
            # 
            # if False:  # DISABLED - DO NOT RE-ENABLE WITHOUT MAJOR REWORK
            #     # Mean reversion triggers systematically fail
            #     # Need momentum confirmation, not just "stopped falling"
            #     pass
            
            # DISABLED: VWAP support - 100% FAILURE RATE in backtesting  
            # Problem: VWAP is not real support, just average price that resets daily
            # Enters at local highs when price "touches" arbitrary level
            # V trade hit stop-loss immediately after "support" broke
            # TODO: Replace with REAL support levels (swing lows, major MAs, gaps)
            # vwap_dist = indicators.get("vwap_distance_pct", 0)
            # rsi = indicators.get("rsi_hourly", 50)
            # vol_ratio = volume_profile.get("current_vs_avg", 0)
            # 
            # if False:  # DISABLED - DO NOT RE-ENABLE WITHOUT MAJOR REWORK
            #     # Need real structural support, not VWAP
            #     # Need price action confirmation (hammer, engulfing)
            #     # Need 2x volume, not 1x
            #     pass


            # Hourly Breakout - Using 10d high for more frequent triggers
            levels = await get_price_levels(symbol, 60, check_time)
            high_10d = float(levels.get("high_10d") or 0)  # TEST 2: Shorter lookback for more frequent breakouts
            current_price = float(indicators.get("current_price") or 0)
            breakout_level = high_10d  # Changed from max(high_20d, high_60d)
            
            # Core pattern: Price breaking new high with volume support
            # TEST 2: Volume lowered to 1.1x (just above average), 0.2% clearance
            if (
                breakout_level > 0
                and current_price > breakout_level * 1.002  # 0.2% above breakout (was 0.1%)
                and vol_ratio > 1.1  # TEST 2: Lowered from 1.3 to 1.1 - above average volume
                and daily_price >= 5.0  # Minimum price filter
            ):
                details = {
                    "breakout_level": round(breakout_level, 4),
                    "current_price": current_price,
                    "volume_ratio": round(vol_ratio, 2),
                    "rsi_hourly": rsi_hourly,
                    # Context for agent analysis (not filters)
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
            
            # NEW TRIGGER 1: VWAP Reclaim
            # Pattern: Price breaks back above VWAP after being below
            # Significance: VWAP is institutional benchmark, reclaim = buying interest
            from eiqora_v2.tools.hourly_indicators import get_hourly_indicators as get_stored_hourly
            
            hourly_stored = await get_stored_hourly(symbol, check_time)
            if not hourly_stored.get("error"):
                current_price = hourly_stored.get("current_price", 0)
                vwap = hourly_stored.get("vwap", 0)
                volume_z = hourly_stored.get("volume_z_20h", 0)
                hourly_cmf = hourly_stored.get("cmf_20", 0)
                
                # Check if we have history to detect crossover
                # Get previous hour's data (simple check: was price below V WAP before?)
                vwap_dist_pct = ((current_price - vwap) / vwap * 100) if vwap > 0 else 0
                
                # Core pattern:
                # - Currently above VWAP (within 1% - fresh breakout)
                # - Volume confirmation (>1.5x average)
                # - Preferably positive money flow (CMF > 0)
                if (
                    0 < vwap_dist_pct < 1.0  # Just above VWAP (0-1%)
                    and volume_z > 1.5  # Volume surge on breakout
                    and current_price >= 5.0  # Min price filter
                ):
                    details = {
                        "current_price": current_price,
                        "vwap": vwap,
                        "vwap_distance_pct": round(vwap_dist_pct, 3),
                        "volume_z": round(volume_z, 2),
                        "cmf_20": round(hourly_cmf, 3) if hourly_cmf else None,
                        # Context
                        "ma20_state": ma20_state,
                        "ma50_state": ma50_state,
                        "rsi14": rsi14,
                        "trend_direction": hourly_stored.get("trend_direction"),
                        **common_details,
                    }
                    
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="vwap_reclaim",
                        priority="MEDIUM",
                        details=details,
                        detected_at=check_time,
                    ))
            
            # NEW TRIGGER 2: Hourly RSI Divergence
            # Pattern: Price makes new low but RSI doesn't (bullish divergence)
            # Significance: Selling exhaustion, potential reversal
            if hourly_stored and not hourly_stored.get("error"):
                hourly_rsi = hourly_stored.get("rsi_14")
                current_price = hourly_stored.get("current_price", 0)
                support_level = hourly_stored.get("support_level", 0)
                
                # Simplified: Check if near support + RSI not extreme oversold
                # (Full divergence detection would need historical hourly data)
                # For now: detect potential divergence setup
                price_near_support = abs(current_price - support_level) / current_price < 0.02 if support_level else False
                rsi_recovering = 25 < hourly_rsi < 40 if hourly_rsi else False
                
                if (
                    price_near_support
                    and rsi_recovering  # RSI recovering from oversold
                    and volume_z > 0  # Some volume interest
                ):
                    details = {
                        "current_price": current_price,
                        "support_level": support_level,
                        "hourly_rsi": round(hourly_rsi, 1) if hourly_rsi else None,
                        "volume_z": round(volume_z, 2),
                        "price_support_distance_pct": round((current_price - support_level) / current_price * 100, 2) if support_level else None,
                        # Context
                        "daily_rsi": rsi14,
                        "ma20_state": ma20_state,
                        "trend_direction": hourly_stored.get("trend_direction"),
                        **common_details,
                    }
                    
                    # TEST 3: DISABLED - Mean-reversion trigger with mixed results
                    # hourly_rsi_divergence had 33% win rate but still catching bounces
                    # Disabling to test pure momentum/fundamental approach
                    # triggers.append(Trigger(
                    #     symbol=symbol,
                    #     trigger_type="hourly_rsi_divergence",
                    #     priority="LOW",
                    #     details=details,
                    #     detected_at=check_time,
                    # ))
            
            # NEW TRIGGER 3: Intraday Consolidation Break
            # Pattern: Tight hourly range then explosive move
            # Significance: Coiled spring, often leads to sustained move
            if hourly_stored and not hourly_stored.get("error"):
                resistance_level = hourly_stored.get("resistance_level", 0)
                support_level = hourly_stored.get("support_level", 0)
                current_price = hourly_stored.get("current_price", 0)
                volume_z = hourly_stored.get("volume_z_20h", 0)
                
                # Calculate recent range tightness
                range_pct = ((resistance_level - support_level) / current_price * 100) if (resistance_level and support_level and current_price) else 999
                
                # Core pattern:
                # - Tight recent range (<2% from support to resistance)
                # - Breakout with volume (>2x average)
                # - Direction aligns with trend preferred
                price_change_pct = indicators.get("price_change_pct", 0)
                breakout_bar = abs(price_change_pct) > 1.5  # >1.5% move this hour
                
                if (
                    range_pct < 2.0  # Tight consolidation (< 2% range)
                    and breakout_bar  # Explosive move
                    and volume_z > 2.0  # Strong volume
                    and current_price >= 5.0  # Min price
                ):
                    details = {
                        "current_price": current_price,
                        "support_level": support_level,
                        "resistance_level": resistance_level,
                        "consolidation_range_pct": round(range_pct, 2),
                        "price_change_pct": round(price_change_pct, 2),
                        "volume_z": round(volume_z, 2),
                        "breakout_direction": "UP" if price_change_pct > 0 else "DOWN",
                        # Context
                        "trend_direction": hourly_stored.get("trend_direction"),
                        "ma20_state": ma20_state,
                        "hourly_rsi": hourly_stored.get("rsi_14"),
                        **common_details,
                    }
                    
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="intraday_consolidation_break",
                        priority="MEDIUM",
                        details=details,
                        detected_at=check_time,
                    ))
            
            # NEW TRIGGER 4: Opening Range Breakout
            # Pattern: Break of first-hour range (9:30-10:30 ET)
            # Significance: Classic daytrader setup, institutional participation
            if hourly_stored and not hourly_stored.get("error"):
                # Check if we're in the valid time window (10:30 AM - 12:00 PM ET)
                from datetime import time
                import pytz
                
                et_tz = pytz.timezone('America/New_York')
                check_time_et = check_time.astimezone(et_tz)
                current_hour = check_time_et.hour
                current_minute = check_time_et.minute
                
                # Valid window: 10:30 AM - 12:00 PM ET (after first hour, before lunch)
                in_valid_window = (
                    (current_hour == 10 and current_minute >= 30) or
                    (current_hour == 11)
                )
                
                if in_valid_window:
                    # Get today's opening range (would need to track first hour high/low)
                    # For now: use support/resistance levels as proxy for recent range
                    support_level = hourly_stored.get("support_level", 0)
                    resistance_level = hourly_stored.get("resistance_level", 0)
                    current_price = hourly_stored.get("current_price", 0)
                    volume_z = hourly_stored.get("volume_z_20h", 0)
                    
                    # Breakout detection: price above resistance with volume
                    price_above_range = current_price > resistance_level * 1.002 if resistance_level else False
                    
                    if (
                        price_above_range
                        and volume_z > 1.8  # Volume confirmation
                        and current_price >= 5.0  # Min price
                    ):
                        details = {
                            "current_price": current_price,
                            "opening_range_high": resistance_level,  # Proxy
                            "opening_range_low": support_level,  # Proxy
                            "breakout_percent": round((current_price - resistance_level) / resistance_level * 100, 2) if resistance_level else None,
                            "volume_z": round(volume_z, 2),
                            "time_et": check_time_et.strftime("%H:%M"),
                            # Context
                            "hourly_rsi": hourly_stored.get("rsi_14"),
                            "trend_direction": hourly_stored.get("trend_direction"),
                            "ma20_state": ma20_state,
                            **common_details,
                        }
                        
                        triggers.append(Trigger(
                            symbol=symbol,
                            trigger_type="opening_range_breakout",
                            priority="HIGH",  # Classic high-probability setup
                            details=details,
                            detected_at=check_time,
                        ))
            
            # NEW TRIGGER 5: Hourly Money Flow Surge
            # Pattern: CMF spikes from negative to strongly positive in one bar
            # Significance: Institutional accumulation, confirms volume_surge
            if hourly_stored and not hourly_stored.get("error"):
                hourly_cmf = hourly_stored.get("cmf_20", 0)
                volume_z = hourly_stored.get("volume_z_20h", 0)
                current_price = hourly_stored.get("current_price", 0)
                
                # Core pattern:
                # - CMF crosses from negative to strong positive (>0.15)
                # - High volume (confirms institutional activity)
                # - Price ideally rising
                price_change_pct = indicators.get("price_change_pct", 0)
                
                if (
                    hourly_cmf > 0.15  # Strong accumulation
                    and volume_z > 2.0  # High volume
                    and price_change_pct > 0  # Rising price (optional but preferred)
                    and current_price >= 5.0  # Min price
                ):
                    details = {
                        "cmf_20": round(hourly_cmf, 3),
                        "volume_z": round(volume_z, 2),
                        "price_change_pct": round(price_change_pct, 2),
                        "current_price": current_price,
                        # Context
                        "mfi_14": hourly_stored.get("mfi_14"),
                        "trend_direction": hourly_stored.get("trend_direction"),
                        "hourly_rsi": hourly_stored.get("rsi_14"),
                        "ma20_state": ma20_state,
                        **common_details,
                    }
                    
                    triggers.append(Trigger(
                        symbol=symbol,
                        trigger_type="hourly_money_flow_surge",
                        priority="MEDIUM",
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
