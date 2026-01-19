"""
Live hourly scanner for swing trading.

Two-stage filtering:
1. Fast quality filter (daily setup + hourly confirmation)
2. LLM deep analysis on ALL suitable candidates
"""

import asyncio
import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from eiqora_v2.backtest.engine import TriggerScanner, Trigger
from eiqora_v2.orchestrator import Orchestrator
from eiqora_v2.tools.db import get_connection
from eiqora_v2.tools.prices import get_indicators, get_hourly_indicators

_logger = logging.getLogger(__name__)
EASTERN_TZ = ZoneInfo("America/New_York")


class LiveScanner:
    """Hourly scanner for live swing trading."""
    
    def __init__(
        self,
        symbols_file: str = "data_collection/config/symbols.txt",
        db_url: str | None = None,
    ):
        self.symbols_file = Path(symbols_file)
        self.db_url = db_url or self._get_db_url()
        self.scanner = TriggerScanner(self.db_url)
        self.orchestrator = Orchestrator()
    
    def _get_db_url(self) -> str:
        import os
        return os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    
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
    
    async def calculate_confluence_score(
        self,
        symbol: str,
        scan_date: date,
        scan_time: datetime,
    ) -> tuple[float, dict[str, float]]:
        """
        Calculate weighted confluence score for quality filtering.
        
        Scoring breakdown:
        - Technical (40%): RSI, MACD, ADX, Bollinger
        - Price Action (30%): MA proximity, VWAP, volume
        - Fundamental (30%): Earnings, news, SEC filings
        
        Returns:
            (total_score, component_scores)
        """
        try:
            # Get daily indicators
            daily = await get_indicators(symbol, 60, scan_time)
            if daily.get("error"):
                return 0.0, {"error": "No daily data"}
            
            # Get hourly indicators
            hourly = await get_hourly_indicators(symbol, scan_date, scan_time)
            if hourly.get("error"):
                return 0.0, {"error": "No hourly data"}
            
            # Get ticker profile (fundamentals + events)
            # TODO: Implement get_ticker_profile()
            # For now, use placeholder
            profile = {}  # Will add in next iteration
            
            scores = {}
            
            # === TECHNICAL INDICATORS (40%) ===
            
            # RSI oversold/overbought (15%)
            rsi = daily.get("rsi14", 50)
            if rsi < 35:
                scores["rsi_oversold"] = 0.15
            elif 35 <= rsi < 45:
                scores["rsi_mild_oversold"] = 0.08
            elif rsi > 65:
                scores["rsi_overbought"] = -0.10  # Penalty
            else:
                scores["rsi_neutral"] = 0.0
            
            # MACD bullish (10%)
            macd_hist = daily.get("macd", {}).get("histogram", 0)
            if macd_hist > 0:
                scores["macd_bullish"] = 0.10
            elif macd_hist < 0:
                scores["macd_bearish"] = 0.0
            
            # ADX trend strength (10%)
            adx = daily.get("adx14", 25)
            if adx > 25:
                scores["strong_trend"] = 0.10
            elif adx > 20:
                scores["moderate_trend"] = 0.05
            
            # Bollinger position (5%)
            bb_pos = daily.get("bollinger", {}).get("price_position", "MIDDLE")
            if bb_pos == "LOWER":
                scores["bb_oversold"] = 0.05
            elif bb_pos == "UPPER":
                scores["bb_overbought"] = -0.05
            
            # === PRICE ACTION (30%) ===
            
            # MA20 proximity (10%)
            current_price = daily.get("current_price", 0)
            ma20 = daily.get("ma20", current_price)
            if ma20 > 0:
                ma20_dist = abs(current_price - ma20) / ma20
                if ma20_dist < 0.02:  # Within 2%
                    scores["near_ma20"] = 0.10
                elif ma20_dist < 0.05:
                    scores["close_ma20"] = 0.05
            
            # VWAP test (10%)
            vwap_dist = hourly.get("vwap_distance_pct", 0)
            if -2.0 < vwap_dist < 0.5:  # Near or slightly below
                scores["vwap_support"] = 0.10
            elif vwap_dist > 3:
                scores["extended_vwap"] = -0.05
            
            # Volume spike (10%)
            if "HOURLY_VOLUME_SPIKE" in hourly.get("state_tags", []):
                scores["volume_spike"] = 0.10
            
            # === FUNDAMENTAL (30%) ===
            # TODO: Integrate ticker profile
            # For now, use state tags as proxy
            
            # Trend direction (15%)
            if "UPTREND" in daily.get("state_tags", []):
                scores["uptrend"] = 0.15
            elif "MIXED" in daily.get("state_tags", []):
                scores["sideways"] = 0.05
            elif "DOWNTREND" in daily.get("state_tags", []):
                scores["downtrend"] = -0.10
            
            # Hourly oversold in uptrend (15% bonus)
            if "UPTREND" in daily.get("state_tags", []) and "HOURLY_OVERSOLD" in hourly.get("state_tags", []):
                scores["oversold_uptrend_confluence"] = 0.15
            
            # Calculate total
            total_score = sum(scores.values())
            
            return total_score, scores
            
        except Exception as e:
            _logger.warning(f"{symbol}: Confluence scoring failed - {e}")
            return 0.0, {"error": str(e)}
    
    async def passes_quality_filter(
        self,
        symbol: str,
        scan_date: date,
        scan_time: datetime,
    ) -> tuple[bool, float, dict]:
        """
        Quality filter with confluence scoring.
        
        Returns:
            (passes, score, breakdown)
        """
        score, breakdown = await self.calculate_confluence_score(symbol, scan_date, scan_time)
        
        # Threshold for high-quality candidates
        # Empirically determined: scores range 0.0-0.6, threshold 0.45 passes top 20%
        QUALITY_THRESHOLD = 0.45
        
        passes = score >= QUALITY_THRESHOLD
        
        if passes:
            _logger.debug(f"{symbol}: Score {score:.2f} - PASS")
        
        return passes, score, breakdown
    
    async def scan_with_quality_filter(
        self,
        scan_date: date | None = None,
        scan_time: datetime | None = None,
    ) -> list[tuple[str, float, dict]]:
        """
        Stage 1: Scan all tickers with confluence-based quality filter.
        
        Returns:
            List of (symbol, score, breakdown) tuples for candidates that pass
        """
        if scan_date is None:
            scan_date = datetime.now(EASTERN_TZ).date()
        if scan_time is None:
            scan_time = datetime.now(EASTERN_TZ)
        
        symbols = self.load_universe()
        candidates = []
        
        _logger.info(f"🔍 Confluence filter scan: {len(symbols)} tickers at {scan_time.strftime('%H:%M')}")
        
        for symbol in symbols:
            passes, score, breakdown = await self.passes_quality_filter(symbol, scan_date, scan_time)
            if passes:
                candidates.append((symbol, score, breakdown))
                # Show top components
                top_components = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)[:3]
                components_str = ", ".join([f"{k}:{v:.2f}" for k, v in top_components if v > 0])
                _logger.info(f"  ✓ {symbol}: {score:.2f} ({components_str})")
        
        _logger.info(f"📊 Confluence filter: {len(candidates)} candidates (from {len(symbols)} tickers)")
        return candidates
    
    async def analyze_candidate(
        self,
        symbol: str,
        score: float,
        breakdown: dict,
        scan_date: date,
        scan_time: datetime,
    ) -> dict[str, Any] | None:
        """
        Stage 2: Run 10-agent LLM pipeline on a quality-filtered candidate.
        """
        try:
            _logger.info(f"🧠 LLM analysis: {symbol} (score: {score:.2f})")
            
            # Build state for orchestrator
            initial_state = {
                "symbol": symbol,
                "asof_time": scan_time,
                "trigger_type": "hourly_confluence",
                "trigger_detail": {
                    "confluence_score": score,
                    "score_breakdown": breakdown,
                    "scan_time": str(scan_time),
                },
            }
            
            # Run full agent pipeline
            final_state = await self.orchestrator.run(initial_state)
            
            return final_state
        except Exception as e:
            _logger.error(f"{symbol}: LLM analysis failed - {e}", exc_info=True)
            return None
    
    async def generate_trade_signals(
        self,
        scan_date: date | None = None,
        scan_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run full hourly scan with confluence-based two-stage filtering.
        
        Returns:
            List of GO signals with entry/TP/SL levels
        """
        if scan_date is None:
            scan_date = datetime.now(EASTERN_TZ).date()
        if scan_time is None:
            scan_time = datetime.now(EASTERN_TZ)
        
        _logger.info(f"\n{'='*60}")
        _logger.info(f"HOURLY SCAN: {scan_date} {scan_time.strftime('%H:%M %Z')}")
        _logger.info(f"{'='*60}")
        
        # Stage 1: Confluence filter
        candidates = await self.scan_with_quality_filter(scan_date, scan_time)
        
        if not candidates:
            _logger.info("No suitable candidates found")
            return []
        
        # Stage 2: LLM analysis on ALL candidates
        signals = []
        for symbol, score, breakdown in candidates:
            result = await self.analyze_candidate(symbol, score, breakdown, scan_date, scan_time)
            
            if not result:
                continue
            
            decision = result.get("decision", {})
            action = decision.get("final_call", "NO_GO")
            
            if action == "GO":
                signal = {
                    "symbol": symbol,
                    "signal_date": scan_date,
                    "signal_time": scan_time,
                    "trigger_type": "hourly_confluence",
                    "action": "GO",
                    "entry_price": decision.get("entry_price"),
                    "stop_loss": decision.get("stop_loss"),
                    "take_profit": decision.get("take_profit"),
                    "conviction": decision.get("conviction", 0.5),
                    "reasoning": decision.get("reasoning", ""),
                    "agent_outputs": result,
                    "trigger_detail": {
                        "confluence_score": score,
                        "score_breakdown": breakdown,
                    },
                }
                signals.append(signal)
                _logger.info(
                    f"✅ GO: {symbol} @ ${signal['entry_price']:.2f} "
                    f"(SL: ${signal['stop_loss']:.2f}, TP: ${signal['take_profit']:.2f})"
                )
            else:
                _logger.info(f"❌ NO_GO: {symbol} (score: {score:.2f})")
        
        _logger.info(f"\n{'='*60}")
        _logger.info(f"SCAN COMPLETE: {len(signals)} GO signal(s)")
        _logger.info(f"{'='*60}\n")
        return signals


async def main():
    """Main entry point for manual testing."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    scanner = LiveScanner()
    signals = await scanner.generate_trade_signals()
    
    print(f"\n=== Trade Signals ({len(signals)}) ===")
    for sig in signals:
        print(f"{sig['symbol']}: {sig['trigger_type']} @ ${sig.get('entry_price', 0):.2f}")


if __name__ == "__main__":
    asyncio.run(main())
