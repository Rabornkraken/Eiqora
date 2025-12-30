"""
Analog Planner Agent implementation.
Creates query plans for the Stats Service to evaluate historical analogs.
"""

import uuid
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.decision import AnalogPlannerOutput, AnalogPlan, AnalogFilter
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.config.universe import get_sector_etf


class AnalogPlannerAgent(BaseAgent[AnalogPlannerOutput]):
    """
    Analog Planner Agent: creates query plans for historical analog lookup.
    
    For each idea, creates a plan specifying:
    - Event type to match
    - Filters (sector, volatility bucket, trend, regime)
    - Lookback period and minimum samples
    
    The Stats Service executes these plans (deterministically, no LLM).
    """
    
    name = "analog_planner"
    output_schema = AnalogPlannerOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather context for analog planning."""
        return {
            "ideas": state.get("ideas", {}),
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
            "topdown": state.get("topdown"),
            "sector_etf": get_sector_etf(state["symbol"]),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for analog plan generation."""
        symbol = state["symbol"]
        sector_etf = data.get("sector_etf", "SPY")
        
        ideas = data.get("ideas", {})
        context = data.get("context", {})
        chart = data.get("chart", {})
        topdown = data.get("topdown")
        
        ideas_list = ideas.get("ideas", [])
        if not ideas_list:
            return f"""
No ideas to create analog plans for {symbol}.
Return empty plans array.
"""
        
        # Determine vol bucket
        rv20 = context.get("vol_basis", {}).get("value", 0.02)
        if rv20 < 0.015:
            vol_bucket = "LOW"
        elif rv20 > 0.03:
            vol_bucket = "HIGH"
        else:
            vol_bucket = "MED"
        
        # Determine trend bucket
        state_tags = context.get("state_tags", [])
        if "UPTREND" in state_tags:
            trend_bucket = "UP"
        elif "DOWNTREND" in state_tags:
            trend_bucket = "DOWN"
        else:
            trend_bucket = "SIDEWAYS"
        
        # Get regime if available
        regime = topdown.get("regime") if topdown else None
        
        ideas_text = "\n".join([
            f"- {i.get('idea_id')}: {i.get('setup_type')} {i.get('direction')} ({i.get('conviction')} conviction)"
            for i in ideas_list
        ])
        
        return f"""
Create analog query plans for trade ideas on {symbol}.

IDEAS:
{ideas_text}

CURRENT CONTEXT:
- Sector ETF: {sector_etf}
- Volatility Bucket: {vol_bucket} (RV20={rv20:.4f})
- Trend Bucket: {trend_bucket}
- Market Regime: {regime or 'UNKNOWN'}

For each idea, create an analog plan:
1. Start with tight filters (sector + vol + trend)
2. Specify appropriate lookback (8 years for common setups, less for regime-specific)
3. Set min_samples (30 for high confidence, 20 for exploratory)

The Stats Service will automatically relax filters if insufficient samples found.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are an Analog Planner Agent that creates query plans for historical analog lookup.

CONTEXT FILTER RULES:
- sector_etf: Include for sector-specific patterns, omit for market-wide
- vol_bucket: Include to match similar volatility regime
- trend_bucket: Include for trend-following strategies
- regime: Include only if topdown regime is meaningful

LOOKBACK GUIDELINES:
- 8 years: Standard for common setups (PULLBACK, BREAKOUT)
- 5 years: For regime-specific patterns
- 3 years: For newer market dynamics (crypto correlation era)

OUTPUT SCHEMA:
{
  "plans": [
    {
      "plan_id": "plan_<random>",
      "idea_id": "<idea ID>",
      "event_type": "PULLBACK_MA50",  // Must match Chart setup_type
      "filters": {
        "sector_etf": "XLK" or null,
        "vol_bucket": "LOW|MED|HIGH" or null,
        "trend_bucket": "UP|DOWN|SIDEWAYS" or null,
        "regime": "<regime>" or null
      },
      "lookback_years": 8,
      "min_samples": 30
    }
  ]
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: AnalogPlannerOutput) -> dict[str, Any]:
        """Build state update with analog plans."""
        # Plans will be executed by Stats Service in next phase
        existing_rules = state.get("rules", []) or []
        for plan in result.plans:
            existing_rules.append({"analog_plan": plan.model_dump()})
        return {"rules": existing_rules}
