"""
Exit Policy Agent implementation.
Defines exit brackets (TP/SL/time stop) for trade ideas.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.ideas import ExitPolicyOutput
from eiqora_v2.schemas.state import SwingTradeState


class ExitPolicyAgent(BaseAgent[ExitPolicyOutput]):
    """
    Exit Policy Agent: defines exit strategy for each trade idea.
    
    Outputs:
    - Take profit level (as volatility multiple)
    - Stop loss level (as volatility multiple)
    - Time stop (trading days)
    - Optional trailing stop
    - Optional scale-out levels
    """
    
    name = "exit_policy"
    output_schema = ExitPolicyOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather context and idea data for exit policy."""
        return {
            "ideas": state.get("ideas", {}),
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for exit policy generation."""
        symbol = state["symbol"]
        
        ideas = data.get("ideas", {})
        context = data.get("context", {})
        chart = data.get("chart", {})
        
        primary_idea_id = ideas.get("primary_idea_id")
        ideas_list = ideas.get("ideas", [])
        
        if not primary_idea_id or not ideas_list:
            return f"""
No trade ideas to create exit policy for {symbol}.
Return a default exit policy with idea_id="none".
"""
        
        # Find primary idea
        primary_idea = next((i for i in ideas_list if i.get("idea_id") == primary_idea_id), ideas_list[0])
        
        vol_basis = context.get("vol_basis", {})
        rv20 = vol_basis.get("value", 0.02)
        
        return f"""
Define exit policy for trade idea on {symbol}.

IDEA:
- ID: {primary_idea.get('idea_id')}
- Direction: {primary_idea.get('direction')}
- Time Horizon: {primary_idea.get('time_horizon')}
- Conviction: {primary_idea.get('conviction')}
- Setup Type: {primary_idea.get('setup_type')}

CONTEXT:
- Current Price: ${context.get('current_price', 0):.2f}
- RV20 (Volatility): {rv20:.4f} ({rv20 * 100:.2f}% daily)
- State Tags: {context.get('state_tags', [])}

CHART LEVELS:
- Invalidation: {chart.get('invalidation', {})}
- Key Levels: {chart.get('key_levels', {})}

Define the exit bracket:
- TP mult: Typically 3-5x volatility for swings (higher for trending, lower for range)
- SL mult: Typically 1.5-2.5x volatility (wider for volatile stocks)
- Time stop: 20-45 days (shorter for SHORT horizon, longer for LONG)

Consider trailing stops for trending setups with high conviction.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are an Exit Policy Agent that defines exit strategies for swing trades.

EXIT BRACKET GUIDELINES:
- TP/SL are multiples of daily volatility (RV20)
- Standard swing: TP=4x vol, SL=2x vol (2:1 R:R)
- High conviction trending: TP=5x vol, SL=2x vol
- Range-bound: TP=3x vol, SL=1.5x vol
- Time stop: 20-35 days for SWING_SHORT, 25-45 for SWING_MEDIUM/LONG

TRAILING STOPS:
- Use for trending setups (BREAKOUT, PULLBACK) with HIGH conviction
- activation_pct: Profit level to activate (e.g., 0.5 = 50% of TP)
- trail_pct: Trail from peak (e.g., 0.03 = 3% trailing stop)

OUTPUT SCHEMA:
{
  "idea_id": "<idea ID>",
  "bracket": {
    "tp_mult": 4.0,
    "sl_mult": 2.0,
    "time_stop_days": 30
  },
  "vol_basis_type": "RV20",
  "trailing_stop": {"activation_pct": 0.5, "trail_pct": 0.03} or null,
  "scale_out_levels": [0.5, 0.75] or [],
  "notes": "<brief strategy notes>"
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: ExitPolicyOutput) -> dict[str, Any]:
        """Build state update with exit policy."""
        # Store as part of rules
        existing_rules = state.get("rules", []) or []
        existing_rules.append({"exit_policy": result.model_dump()})
        return {"rules": existing_rules}
