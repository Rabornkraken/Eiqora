"""
TopDown Agent implementation.
Analyzes macro market conditions and regime.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.portfolio import TopDownOutput
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_prices, get_indicators


# Context symbols for macro analysis
CONTEXT_SYMBOLS = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
    "IWM": "Russell 2000 ETF",
    "TLT": "20+ Year Treasury ETF",
    "GLD": "Gold ETF",
    "VIX": "Volatility Index",
}

SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]


class TopDownAgent(BaseAgent[TopDownOutput]):
    """
    TopDown Agent: analyzes macro market conditions.
    
    Runs ONCE per sweep (not per ticker).
    Output is cached and shared across all ticker analyses.
    
    Evaluates:
    - SPY/QQQ trend and momentum
    - VIX regime
    - Sector rotation signals
    - Risk-on/risk-off indicators
    """
    
    name = "topdown"
    output_schema = TopDownOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather market-wide context data."""
        asof_time = state["asof_time"]
        
        data = {}
        
        # Get SPY data
        try:
            spy_indicators = await get_indicators("SPY", 60, asof_time)
            data["spy"] = spy_indicators
        except Exception as e:
            data["spy"] = {"error": str(e)}
        
        # Get QQQ data
        try:
            qqq_indicators = await get_indicators("QQQ", 60, asof_time)
            data["qqq"] = qqq_indicators
        except Exception as e:
            data["qqq"] = {"error": str(e)}
        
        # Get sector ETF performance for rotation
        sector_data = {}
        for etf in SECTOR_ETFS[:5]:  # Limit to avoid too many queries
            try:
                indicators = await get_indicators(etf, 20, asof_time)
                if not indicators.get("error"):
                    sector_data[etf] = {
                        "ret_20d": indicators.get("ret_20d"),
                        "trend": indicators.get("trend", {}).get("ma20"),
                    }
            except Exception:
                pass
        data["sectors"] = sector_data
        
        return data
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for macro analysis."""
        asof_time = state["asof_time"]
        
        spy = data.get("spy", {})
        qqq = data.get("qqq", {})
        sectors = data.get("sectors", {})
        
        # Format sector performance
        sector_text = "\n".join([
            f"  {etf}: 20d return={d.get('ret_20d', 'N/A'):.1%}, MA20={d.get('trend', 'N/A')}"
            for etf, d in sectors.items()
        ]) if sectors else "  No sector data"
        
        return f"""
Analyze macro market conditions as of {asof_time}.

SPY (S&P 500):
- Price vs MA20: {spy.get('trend', {}).get('ma20', 'N/A')}
- Price vs MA50: {spy.get('trend', {}).get('ma50', 'N/A')}
- RV20 (Volatility): {spy.get('rv20', 0):.4f}
- 20d Return: {spy.get('ret_20d', 0):.1%}
- 60d Return: {spy.get('ret_60d', 0):.1%}
- State Tags: {spy.get('state_tags', [])}

QQQ (Nasdaq 100):
- Price vs MA20: {qqq.get('trend', {}).get('ma20', 'N/A')}
- 20d Return: {qqq.get('ret_20d', 0):.1%}
- State Tags: {qqq.get('state_tags', [])}

SECTOR PERFORMANCE:
{sector_text}

Classify:
1. Market regime (RISK_ON, RISK_OFF, HIGH_VOL, etc.)
2. SPY trend (UP, DOWN, SIDEWAYS)
3. VIX level based on RV20 proxy (LOW <15, NORMAL 15-20, ELEVATED 20-25, HIGH >25)
4. Sector rotation (which sectors are LEADING/LAGGING)
5. Overall bias (BULLISH, BEARISH, NEUTRAL)
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a TopDown Agent that analyzes macro market conditions.

REGIME CLASSIFICATION:
- RISK_ON: SPY uptrend, VIX low, breadth expanding
- RISK_OFF: SPY downtrend, VIX elevated, defensive sectors leading
- HIGH_VOL: VIX elevated, large swings in either direction
- LOW_VOL: VIX very low, grinding price action
- TRANSITION: Mixed signals, regime change in progress

VIX LEVEL (based on RV20 proxy):
- LOW: RV20 < 0.01 (corresponds to VIX < 15)
- NORMAL: RV20 0.01-0.015 (VIX 15-20)
- ELEVATED: RV20 0.015-0.02 (VIX 20-25)
- HIGH: RV20 > 0.02 (VIX > 25)

SECTOR ROTATION:
- LEADING: 20d return significantly above SPY
- LAGGING: 20d return significantly below SPY
- NEUTRAL: Inline with SPY

OUTPUT SCHEMA:
{
  "regime": "RISK_ON|RISK_OFF|HIGH_VOL|LOW_VOL|TRANSITION|UNKNOWN",
  "spy_trend": "UP|DOWN|SIDEWAYS",
  "vix_level": "LOW|NORMAL|ELEVATED|HIGH",
  "sector_rotation": {"XLK": "LEADING", "XLE": "LAGGING", ...},
  "policy_hints": {"fed": "hawkish", "rates": "rising"},
  "bias": "BULLISH|BEARISH|NEUTRAL",
  "notes": "<brief macro summary>"
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: TopDownOutput) -> dict[str, Any]:
        """Build state update with topdown output."""
        return {"topdown": result.model_dump()}
