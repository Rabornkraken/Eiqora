"""
Candidate Selector for watchlist building.

Combines:
- Daily technicals from get_indicators()
- Weekly profile from ProfileGenerator
- Produces watchlist of ~10-15 candidates for trigger monitoring
"""

import asyncio
import logging
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

from eiqora_v2.services.profile_generator import ProfileGenerator
from eiqora_v2.tools.prices import get_indicators
from eiqora_v2.tools.db import get_connection

_logger = logging.getLogger(__name__)


class CandidateSelector:
    """
    Builds daily watchlist using:
    - Daily technicals (50% weight)
    - Profile quality (50% weight)
    """
    
    def __init__(
        self,
        symbols_file: str = "data_collection/config/symbols.txt",
        threshold: float = 0.70,
    ):
        self.symbols_file = Path(symbols_file)
        self.threshold = threshold
        self.profile_generator = ProfileGenerator()
    
    def load_universe(self) -> list[str]:
        """Load ticker symbols from symbols.txt."""
        if not self.symbols_file.exists():
            raise FileNotFoundError(f"Symbols file not found: {self.symbols_file}")
        
        symbols = []
        with open(self.symbols_file, "r") as f:
            for line in f:
                symbol = line.strip()
                if symbol and not symbol.startswith("#"):
                    symbols.append(symbol)
        
        _logger.info(f"Loaded {len(symbols)} symbols from {self.symbols_file}")
        return symbols
    
    async def score_daily_technicals(
        self,
        symbol: str,
        asof_time: datetime,
    ) -> tuple[float, dict[str, float]]:
        """
        Score daily technicals on 0-1 scale.
        
        Returns:
            (score 0-1.0, breakdown)
        """
        try:
            indicators = await get_indicators(symbol, 60, asof_time)
            if indicators.get("error"):
                return 0.0, {"error": "No data"}
            
            scores: dict[str, float] = {}
            total = 0.0

            def add_score(label: str, value: float) -> None:
                nonlocal total
                if value:
                    scores[label] = value
                    total += value

            state_tags = set(indicators.get("state_tags", []))
            trend = indicators.get("trend", {}) or {}

            # Trend regime (net 0.25)
            if "UPTREND" in state_tags:
                add_score("trend_up", 0.25)
            elif "MIXED" in state_tags:
                add_score("trend_mixed", 0.08)
            elif "DOWNTREND" in state_tags:
                add_score("trend_down", -0.20)

            # MA alignment (net 0.15)
            ma20_state = trend.get("ma20")
            if ma20_state == "ABOVE":
                add_score("ma20_above", 0.05)
            elif ma20_state == "BELOW":
                add_score("ma20_below", -0.05)

            ma50_state = trend.get("ma50")
            if ma50_state == "ABOVE":
                add_score("ma50_above", 0.05)
            elif ma50_state == "BELOW":
                add_score("ma50_below", -0.05)

            ma200_state = trend.get("ma200")
            if ma200_state == "ABOVE":
                add_score("ma200_above", 0.05)
            elif ma200_state == "BELOW":
                add_score("ma200_below", -0.05)

            # Momentum (net 0.15)
            ret_20d = indicators.get("ret_20d")
            if ret_20d is not None:
                if ret_20d > 0.03:
                    add_score("mom_20d_strong", 0.08)
                elif ret_20d > 0:
                    add_score("mom_20d_pos", 0.04)
                elif ret_20d < -0.03:
                    add_score("mom_20d_neg", -0.08)
                elif ret_20d < 0:
                    add_score("mom_20d_weak", -0.04)

            ret_60d = indicators.get("ret_60d")
            if ret_60d is not None:
                if ret_60d > 0.06:
                    add_score("mom_60d_strong", 0.07)
                elif ret_60d > 0:
                    add_score("mom_60d_pos", 0.03)
                elif ret_60d < -0.06:
                    add_score("mom_60d_neg", -0.07)
                elif ret_60d < 0:
                    add_score("mom_60d_weak", -0.03)

            # RSI (net 0.10)
            rsi = indicators.get("rsi14", 50)
            if rsi < 30:
                if "DOWNTREND" in state_tags:
                    add_score("rsi_oversold_downtrend", -0.05)
                else:
                    add_score("rsi_oversold", 0.02)
            elif 30 <= rsi < 40:
                add_score("rsi_recovery", 0.06)
            elif 40 <= rsi <= 60:
                add_score("rsi_neutral", 0.08)
            elif 60 < rsi <= 70:
                add_score("rsi_momentum", 0.04)
            elif rsi > 70:
                add_score("rsi_overbought", -0.03)

            # MACD (net 0.10)
            macd_hist = indicators.get("macd", {}).get("histogram", 0)
            if macd_hist > 0:
                add_score("macd_bullish", 0.08)
            elif macd_hist < 0:
                add_score("macd_bearish", -0.05)

            # ADX (net 0.10)
            adx = indicators.get("adx14", 20)
            if adx > 25:
                add_score("adx_strong", 0.08)
            elif adx > 20:
                add_score("adx_moderate", 0.04)
            elif adx < 15:
                add_score("adx_weak", -0.03)

            # Volume confirmation (net 0.10)
            vol_z = indicators.get("volume_z_20d", 0)
            if vol_z > 2.5:
                add_score("volume_surge", 0.08)
            elif vol_z > 1.5:
                add_score("volume_high", 0.05)
            elif vol_z < -1.5:
                add_score("volume_light", -0.05)

            # MA proximity (net 0.05)
            current_price = indicators.get("current_price", 0)
            ma20 = indicators.get("ma20", current_price)
            if ma20 and ma20 > 0:
                ma_dist = abs(current_price - ma20) / ma20
                if ma_dist < 0.03 and ("UPTREND" in state_tags or "MIXED" in state_tags):
                    add_score("near_ma20", 0.05)
                elif ma_dist > 0.08:
                    add_score("extended_from_ma20", -0.03)

            total = max(min(total, 1.0), 0.0)
            return total, scores
            
        except Exception as e:
            _logger.warning(f"{symbol}: Technical scoring failed - {e}")
            return 0.0, {"error": str(e)}
    
    async def score_profile(
        self,
        symbol: str,
    ) -> tuple[float, dict[str, float]]:
        """
        Get profile score on 0-1 scale.
        
        Uses pre-computed score from ProfileGenerator (LLM-derived).
        
        Returns:
            (score 0-1.0, breakdown)
        """
        try:
            profile = await self.profile_generator.get_profile(symbol)
            
            # Use pre-computed score if available
            if profile.profile_score > 0:
                return profile.profile_score, profile.score_breakdown
            
            # Fallback: compute on the fly (for old profiles without score)
            scores = {}
            
            if profile.bull_case and len(profile.bull_case) > 0:
                scores["has_bull_case"] = 0.15
            
            if profile.catalysts and len(profile.catalysts) > 0:
                scores["has_catalysts"] = 0.15
            
            if profile.revenue_growth_3y and profile.revenue_growth_3y > 0:
                scores["revenue_growth"] = 0.10
            
            if profile.risks and len(profile.risks) <= 2:
                scores["low_risk"] = 0.10
            elif profile.risks and len(profile.risks) <= 4:
                scores["moderate_risk"] = 0.05
            
            return sum(scores.values()), scores
            
        except Exception as e:
            _logger.warning(f"{symbol}: Profile scoring failed - {e}")
            return 0.0, {"error": str(e)}
    
    async def build_watchlist(
        self,
        scan_time: datetime,
    ) -> list[dict[str, Any]]:
        """
        Build watchlist from universe using technical + profile scoring.
        
        MACRO SAFEGUARD: Adjusts threshold based on VIX regime.
        
        Returns:
            List of candidates with scores
        """
        _logger.info(f"Building watchlist at {scan_time}")
        
        # MACRO SAFEGUARD: Check VIX regime and adjust threshold
        try:
            from eiqora_v2.tools.prices import get_indicators
            vix_symbol = "IDX_VIX"
            vix_indicators = await get_indicators(vix_symbol, 20, scan_time)
            if vix_indicators.get("error"):
                vix_indicators = await get_indicators("VIX", 20, scan_time)
            vix_level = vix_indicators.get("current_price", 15) if vix_indicators else 15
            
            # Dynamic threshold based on VIX
            if vix_level > 30:
                adjusted_threshold = 0.70  # High vol: be very selective
                _logger.warning(f"⚠️  HIGH VOL REGIME: VIX={vix_level:.1f}, threshold ↑ {adjusted_threshold}")
            elif vix_level > 20:
                adjusted_threshold = 0.60  # Moderate vol: slightly selective
                _logger.info(f"MODERATE VOL: VIX={vix_level:.1f}, threshold ↑ {adjusted_threshold}")
            else:
                adjusted_threshold = self.threshold  # Normal regime
                _logger.info(f"NORMAL REGIME: VIX={vix_level:.1f}, threshold = {adjusted_threshold}")
        except Exception as e:
            _logger.warning(f"Could not check VIX, using default threshold: {e}")
            adjusted_threshold = self.threshold

        # MACRO YIELD REGIME CHECK (IEF/TLT)
        try:
            from eiqora_v2.tools.market_regime import get_market_regime
            regime = await get_market_regime(scan_time)
            
            if regime["mode"] == "RISK_OFF":
                adjusted_threshold = max(adjusted_threshold, 0.80)
                _logger.warning(f"🛑 RISK OFF REGIME (Yields/Vol): Multiplier={regime['risk_multiplier']}, threshold ↑ {adjusted_threshold}")
            elif regime["mode"] == "CAUTIOUS":
                adjusted_threshold = max(adjusted_threshold, 0.65) 
                _logger.warning(f"⚠️ CAUTIOUS REGIME (Rising Yields): threshold ↑ {adjusted_threshold}")
            else:
                _logger.info(f"YIELD REGIME: {regime['mode']}, Trend={regime['yield_trend']}")
                
        except Exception as e:
            _logger.warning(f"Yield regime check failed: {e}")
        
        # Get universe symbols
        universe = self.load_universe()
        
        watchlist = []
        
        _logger.info(f"Building watchlist for {len(universe)} symbols...")
        
        # Prepare all logs for database
        all_logs = []
        
        for symbol in universe:
            # Score technicals
            tech_score, tech_breakdown = await self.score_daily_technicals(symbol, scan_time)
            
            # Score profile (0-1 from LLM)
            profile_score, profile_breakdown = await self.score_profile(symbol)
            
            # Combined score: 50% technical + 50% profile
            total_score = (tech_score * 0.5) + (profile_score * 0.5)
            
            is_selected = total_score >= adjusted_threshold
            
            # Prepare log entry
            log_entry = {
                "symbol": symbol,
                "scan_date": scan_time.date(),
                "total_score": total_score,
                "technical_score": tech_score,
                "profile_score": profile_score,
                "is_selected": is_selected,
                "details": {
                    "technical": tech_breakdown,
                    "profile": profile_breakdown,
                }
            }
            all_logs.append(log_entry)
            
            if is_selected:
                candidate = {
                    "symbol": symbol,
                    "total_score": total_score,
                    "technical_score": tech_score,
                    "profile_score": profile_score,
                    "technical_breakdown": tech_breakdown,
                    "profile_breakdown": profile_breakdown,
                    "added_at": scan_time,
                }
                watchlist.append(candidate)
                _logger.info(f"  ✅ {symbol}: {total_score:.2f} (tech={tech_score:.2f}, profile={profile_score:.2f})")
            else:
                _logger.debug(f"  ❌ {symbol}: {total_score:.2f}")

        # Bulk save logs
        await self.save_candidate_logs(all_logs)
        
        # Sort by score descending
        watchlist.sort(key=lambda x: x["total_score"], reverse=True)
        
        _logger.info(f"Watchlist selected: {len(watchlist)} candidates (threshold={adjusted_threshold})")
        return watchlist

    async def save_candidate_logs(self, logs: list[dict]) -> None:
        """Save full selection logs for all candidates."""
        try:
            import json
            async with get_connection() as conn:
                for log in logs:
                    await conn.execute("""
                        INSERT INTO candidate_selection_log (
                            symbol, scan_date, total_score, technical_score, profile_score, is_selected, details
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (symbol, scan_date) DO UPDATE 
                        SET total_score = EXCLUDED.total_score,
                            technical_score = EXCLUDED.technical_score,
                            profile_score = EXCLUDED.profile_score,
                            is_selected = EXCLUDED.is_selected,
                            details = EXCLUDED.details
                    """,
                        log["symbol"],
                        log["scan_date"],
                        log["total_score"],
                        log["technical_score"],
                        log["profile_score"],
                        log["is_selected"],
                        json.dumps(log["details"]),
                    )
            _logger.info(f"Logged evaluation for {len(logs)} symbols")
        except Exception as e:
            _logger.error(f"Failed to save candidate logs: {e}")
    
    async def save_watchlist(self, watchlist: list[dict], scan_date: date) -> None:
        """Save watchlist to database."""
        try:
            import json
            async with get_connection() as conn:
                # Clear existing watchlist for this date to ensure freshness/limit
                await conn.execute("DELETE FROM daily_watchlist WHERE scan_date = $1", scan_date)
                
                for candidate in watchlist:
                    await conn.execute("""
                        INSERT INTO daily_watchlist (symbol, scan_date, total_score, technical_score, profile_score, details)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (symbol, scan_date) DO UPDATE 
                        SET total_score = EXCLUDED.total_score,
                            technical_score = EXCLUDED.technical_score,
                            profile_score = EXCLUDED.profile_score,
                            details = EXCLUDED.details
                    """,
                        candidate["symbol"],
                        scan_date,
                        candidate["total_score"],
                        candidate["technical_score"],
                        candidate["profile_score"],
                        json.dumps({
                            "technical": candidate["technical_breakdown"],
                            "profile": candidate["profile_breakdown"],
                        }),
                    )
            _logger.info(f"Saved {len(watchlist)} candidates to daily_watchlist table")
        except Exception as e:
            _logger.error(f"Failed to save watchlist: {e}")


async def main():
    """Test the candidate selector."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    selector = CandidateSelector(threshold=0.70)
    from zoneinfo import ZoneInfo
    scan_time = datetime.now(ZoneInfo("America/New_York"))
    watchlist = await selector.build_watchlist(scan_time)
    
    # Save all selected candidates to DB
    if watchlist:
        await selector.save_watchlist(watchlist, scan_time.date())
    
    print(f"\n{'='*60}")
    print(f"WATCHLIST ({len(watchlist)} candidates) - All Saved")
    print(f"{'='*60}")
    
    for c in watchlist[:10]:
        print(f"{c['symbol']:6s}: {c['total_score']:.2f} (T={c['technical_score']:.2f}, P={c['profile_score']:.2f})")


if __name__ == "__main__":
    asyncio.run(main())
