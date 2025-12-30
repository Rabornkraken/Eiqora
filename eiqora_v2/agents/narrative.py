"""
Narrative Agent implementation.
Generates human-readable trade narratives for approved signals.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.monitor import NarrativeOutput
from eiqora_v2.schemas.state import SwingTradeState


class NarrativeAgent(BaseAgent[NarrativeOutput]):
    """
    Narrative Agent: generates human-readable trade narratives.
    
    Creates compelling, clear narratives for approved trade signals.
    Output is suitable for:
    - Trade journals
    - Client communications
    - Internal review
    """
    
    name = "narrative"
    output_schema = NarrativeOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather all data for narrative generation."""
        return {
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
            "ideas": state.get("ideas", {}),
            "decision": state.get("decision", {}),
            "stats": state.get("stats", []),
            "triage": state.get("triage", {}),
            "facts": state.get("facts"),
            "topdown": state.get("topdown"),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for narrative generation."""
        symbol = state["symbol"]
        sector = state.get("sector", "Unknown")
        
        context = data.get("context", {})
        chart = data.get("chart", {})
        ideas = data.get("ideas", {})
        decision = data.get("decision", {})
        stats = data.get("stats", [])
        topdown = data.get("topdown", {})
        
        ideas_list = ideas.get("ideas", [])
        primary_idea = ideas_list[0] if ideas_list else {}
        first_stats = stats[0] if stats else {}
        
        rule = decision.get("rule", {})
        
        return f"""
Generate a trade narrative for {symbol} ({sector}).

MARKET CONTEXT:
- Regime: {topdown.get('regime', 'N/A') if topdown else 'N/A'}
- Bias: {topdown.get('bias', 'N/A') if topdown else 'N/A'}

TRADE IDEA:
- Direction: {primary_idea.get('direction', 'N/A')}
- Setup Type: {primary_idea.get('setup_type', 'N/A')}
- Time Horizon: {primary_idea.get('time_horizon', 'N/A')}
- Conviction: {primary_idea.get('conviction', 'N/A')}
- Thesis: {primary_idea.get('thesis', 'N/A')}

CHART ANALYSIS:
- Current Price: ${context.get('current_price', 0):.2f}
- Trend: {context.get('trend', {})}
- Volatility (RV20): {context.get('vol_basis', {}).get('value', 0):.4f}
- Key Levels: {chart.get('key_levels', {})}

TRADE RULE:
- Entry: {rule.get('entry_trigger_type', 'N/A')} @ ${rule.get('entry_level', 0):.2f}
- Take Profit: {rule.get('tp_mult', 0)}x volatility
- Stop Loss: {rule.get('sl_mult', 0)}x volatility
- Time Stop: {rule.get('time_stop_days', 0)} days
- Invalidation: ${rule.get('invalidation_level', 0):.2f}

HISTORICAL STATS:
- Win Rate: {first_stats.get('win_rate', 0):.1%}
- Expected Return: {first_stats.get('expected_return', 0):.2%}
- Sample Size: {first_stats.get('sample_size', 0)}
- Avg Hold Days: {first_stats.get('avg_hold_days', 0):.1f}

Write a compelling but factual narrative covering:
1. Headline (catchy one-liner)
2. Thesis (why this trade makes sense)
3. Setup description (technical picture)
4. Risk factors (what could go wrong)
5. Entry and exit plans
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Narrative Agent that writes compelling trade narratives.

STYLE GUIDELINES:
- Professional but engaging
- Focus on facts, not hype
- Quantify where possible
- Acknowledge risks
- Action-oriented

OUTPUT SCHEMA:
{
  "symbol": "NVDA",
  "headline": "NVDA Pullback to 50MA: High-Conviction Long Setup",
  "thesis": "NVDA offers compelling entry after healthy consolidation...",
  "setup_description": "Price pulled back to 50-day MA with declining volume...",
  "risk_factors": ["Semiconductor cycle turning", "Valuation stretched"],
  "entry_plan": "Enter on break above yesterday's high at $145.50",
  "exit_plan": "Target 4x volatility (~$160), stop at 2x vol ($138)",
  "stats_summary": "Similar setups won 52% with 3.2% avg return over 8 years"
}

Keep each field concise. Maximum sizes enforced by schema.
Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: NarrativeOutput) -> dict[str, Any]:
        """Build state update."""
        return {"narrative": result.model_dump()}
