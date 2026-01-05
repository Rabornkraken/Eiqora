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
from eiqora_v2.tools.positions import get_open_positions

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
        threshold: float = 0.50,
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
            
            scores = {}
            
            # Trend (30%) - scaled to 0-1
            if "UPTREND" in indicators.get("state_tags", []):
                scores["uptrend"] = 0.30
            elif "MIXED" in indicators.get("state_tags", []):
                scores["sideways"] = 0.10
            
            # RSI (20%)
            rsi = indicators.get("rsi14", 50)
            if 30 <= rsi <= 50:  # Favorable zone
                scores["rsi_favorable"] = 0.20
            elif rsi < 30:
                scores["rsi_oversold"] = 0.15
            
            # MACD (20%)
            macd_hist = indicators.get("macd", {}).get("histogram", 0)
            if macd_hist > 0:
                scores["macd_bullish"] = 0.20
            
            # ADX (20%)
            adx = indicators.get("adx14", 20)
            if adx > 25:
                scores["strong_trend"] = 0.20
            elif adx > 20:
                scores["moderate_trend"] = 0.10
            
            # MA proximity (10%)
            current_price = indicators.get("current_price", 0)
            ma20 = indicators.get("ma20", current_price)
            if ma20 > 0:
                ma_dist = abs(current_price - ma20) / ma20
                if ma_dist < 0.03:  # Within 3%
                    scores["near_ma20"] = 0.10
            
            total = min(sum(scores.values()), 1.0)  # Cap at 1.0
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
        universe = await self.get_universe(scan_time)
        
        # POSITION EXCLUSION: Filter out symbols with open positions
        open_positions = await get_open_positions()
        position_symbols = {pos["symbol"] for pos in open_positions}
        
        if position_symbols:
            _logger.info(
                f"🔒 Excluding {len(position_symbols)} symbols with open positions: "
                f"{', '.join(sorted(position_symbols))}"
            )
            universe = [s for s in universe if s not in position_symbols]
        
        watchlist = []
        
        _logger.info(f"Building watchlist for {len(universe)} symbols...")
        
        for symbol in universe:
            # Score technicals
            tech_score, tech_breakdown = await self.score_daily_technicals(symbol, scan_time)
            
            # Score profile (0-1 from LLM)
            profile_score, profile_breakdown = await self.score_profile(symbol)
            
            # Combined score: 50% technical + 50% profile (both on 0-1 scale)
            total_score = (tech_score * 0.5) + (profile_score * 0.5)
            
            if total_score >= adjusted_threshold:
                candidate = {
                    "symbol": symbol,
                    "total_score": total_score,
                    "technical_score": tech_score,
                    "profile_score": profile_score,
                    "technical_breakdown": tech_breakdown,
                    "profile_breakdown": profile_breakdown,
                    "added_at": asof_time,
                }
                watchlist.append(candidate)
                _logger.info(f"  ✅ {symbol}: {total_score:.2f} (tech={tech_score:.2f}, profile={profile_score:.2f})")
            else:
                _logger.debug(f"  ❌ {symbol}: {total_score:.2f}")
        
        # Sort by score descending
        watchlist.sort(key=lambda x: x["total_score"], reverse=True)
        
        _logger.info(f"Watchlist built: {len(watchlist)} candidates (threshold={self.threshold})")
        return watchlist
    
    async def save_watchlist(self, watchlist: list[dict], scan_date: date) -> None:
        """Save watchlist to database."""
        try:
            import json
            async with get_connection() as conn:
                for candidate in watchlist:
                    await conn.execute("""
                        INSERT INTO watchlist (symbol, scan_date, total_score, technical_score, profile_score, details)
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
            _logger.info(f"Saved {len(watchlist)} candidates to watchlist table")
        except Exception as e:
            _logger.error(f"Failed to save watchlist: {e}")


async def main():
    """Test the candidate selector."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    selector = CandidateSelector(threshold=0.45)
    watchlist = await selector.build_watchlist()
    
    print(f"\n{'='*60}")
    print(f"WATCHLIST ({len(watchlist)} candidates)")
    print(f"{'='*60}")
    
    for c in watchlist[:10]:
        print(f"{c['symbol']:6s}: {c['total_score']:.2f} (T={c['technical_score']:.2f}, P={c['profile_score']:.2f})")


if __name__ == "__main__":
    asyncio.run(main())
