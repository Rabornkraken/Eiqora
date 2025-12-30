"""
Sanity/Veto Agent implementation.
Final safety check before trade execution.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.decision import VetoOutput
from eiqora_v2.schemas.state import SwingTradeState


class VetoAgent(BaseAgent[VetoOutput]):
    """
    Sanity/Veto Agent: final safety check before execution.
    
    Checks for:
    - Logical inconsistencies (long in downtrend, short in uptrend)
    - Data quality issues
    - Unusual conditions that warrant human review
    - Risk concentration issues
    """
    
    name = "sanity_veto"
    output_schema = VetoOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather all context for sanity check."""
        return {
            "decision": state.get("decision", {}),
            "ideas": state.get("ideas", {}),
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
            "triage": state.get("triage", {}),
            "facts": state.get("facts"),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for sanity checks."""
        symbol = state["symbol"]
        
        decision = data.get("decision", {})
        ideas = data.get("ideas", {})
        context = data.get("context", {})
        chart = data.get("chart", {})
        triage = data.get("triage", {})
        facts = data.get("facts")
        
        if decision.get("decision") != "GO":
            return f"""
Decision for {symbol} is {decision.get('decision', 'NOT GO')}.
No veto needed for non-GO decisions.
Return veto=false with empty sanity_checks.
"""
        
        rule = decision.get("rule", {})
        ideas_list = ideas.get("ideas", [])
        primary_idea = ideas_list[0] if ideas_list else {}
        
        return f"""
Perform sanity check on GO decision for {symbol}.

DECISION:
- Direction: {rule.get('direction')}
- Entry: {rule.get('entry_trigger_type')} @ ${rule.get('entry_level', 0):.2f}
- TP Mult: {rule.get('tp_mult')}
- SL Mult: {rule.get('sl_mult')}
- Invalidation: {rule.get('invalidation_level')}
- Risk Score: {decision.get('risk_score')}

CONTEXT:
- State Tags: {context.get('state_tags', [])}
- Trend: {context.get('trend', {})}
- Data Quality: {context.get('data_quality', 'UNKNOWN')}

CHART:
- Setup Type: {chart.get('setup_type')}
- Setup Quality Score: {chart.get('setup_quality', {}).get('score', 0)}

EVENT:
- Event Type: {triage.get('event_type', 'TECHNICAL_ONLY')}
- Materiality: {facts.get('materiality') if facts else 'N/A'}
- Sentiment: {facts.get('sentiment') if facts else 'N/A'}

SANITY CHECKS TO PERFORM:
1. direction_alignment: Is direction aligned with trend? (LONG in UPTREND, SHORT in DOWNTREND)
2. data_quality: Is data quality GOOD?
3. setup_quality: Is setup quality score >= 0.3?
4. risk_reward: Is risk_score <= 0.7?
5. event_alignment: If event-driven, is sentiment aligned with direction?

VETO if any critical check fails (direction misaligned with strong trend, bad data, very low quality).
WARN for minor issues but don't veto.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Sanity/Veto Agent that performs final safety checks.

VETO CRITERIA (any = veto):
1. Direction contradicts strong trend (LONG when DOWNTREND + HIGH_VOL)
2. Data quality is SPARSE or STALE
3. Setup quality score < 0.2
4. Risk score > 0.8
5. Event sentiment strongly contradicts direction (LONG on NEGATIVE high-materiality event)

WARNING CRITERIA (log but don't veto):
1. Mixed trend signals
2. Medium conviction
3. Unusual volume patterns
4. Minor event-direction misalignment

OUTPUT SCHEMA:
{
  "veto": true|false,
  "veto_reason": "<reason if veto>" or null,
  "warnings": ["warning 1", "warning 2"],
  "sanity_checks": {
    "direction_alignment": true|false,
    "data_quality": true|false,
    "setup_quality": true|false,
    "risk_reward": true|false,
    "event_alignment": true|false
  }
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: VetoOutput) -> dict[str, Any]:
        """Build state update with veto result."""
        return {"veto": result.model_dump()}
