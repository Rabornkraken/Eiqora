"""
Position Reassessment Agent - LLM-based decision maker for existing positions.

When thesis-breaking events occur, this agent reassesses whether to hold or exit.
"""

from typing import Any
from pydantic import BaseModel, Field

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.state import SwingTradeState


class PositionReassessmentOutput(BaseModel):
    """Output from position reassessment."""
    decision: str = Field(description="HOLD, EXIT, or REDUCE_SIZE")
    reasoning: str = Field(description="Why this decision was made")
    urgency: str = Field(description="IMMEDIATE, END_OF_DAY, or MONITOR")
    thesis_broken: bool = Field(description="Is the original trade thesis invalidated?")
    confidence: float = Field(description="Confidence in decision (0-1)")


class PositionReassessmentAgent(BaseAgent[PositionReassessmentOutput]):
    """
    Position Reassessment Agent: Decides whether to exit existing positions.
    
    Runs when thesis-breaking triggers are detected (SEC 8-K, earnings miss, etc.)
    Compares new event against original trade thesis.
    """
    
    name = "position_reassessment"
    output_schema = PositionReassessmentOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather position details and trigger event."""
        return {
            "position": state.get("position", {}),
            "trigger": state.get("trigger", {}),
            "profile": state.get("profile", {}),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build reassessment prompt."""
        symbol = state.get("symbol")
        position = data.get("position", {})
        trigger = data.get("trigger", {})
        profile = data.get("profile", {})
        
        # Position details
        entry_price = position.get("entry_price", 0)
        current_price = position.get("current_price", entry_price)
        pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
        days_held = position.get("days_held", 0)
        
        # Trigger details
        trigger_type = trigger.get("trigger_type", "unknown")
        trigger_severity = trigger.get("severity", "UNKNOWN")
        trigger_detail = trigger.get("details", {})
        
        # Original thesis from profile
        bull_case = profile.get("bull_case", [])
        known_risks = profile.get("risks", [])
        
        return f"""
Reassess EXISTING position due to new thesis-breaking event.

POSITION DETAILS:
- Symbol: {symbol}
- Entry Price: ${entry_price:.2f}
- Current Price: ${current_price:.2f}
- P&L: {pnl_pct:+.1f}%
- Days Held: {days_held}

ORIGINAL THESIS (from profile):
- Bull Case: {', '.join(bull_case[:2]) if bull_case else 'Not available'}
- Known Risks: {', '.join(known_risks[:2]) if known_risks else 'Not available'}

NEW EVENT (Severity: {trigger_severity}):
- Type: {trigger_type}
- Details: {trigger_detail}

CRITICAL QUESTION:
Does this event INVALIDATE the original trade thesis?

Consider:
1. Is this a temporary setback or fundamental change?
2. Does it contradict the bull case assumptions?
3. Does it confirm a known risk we accepted?
4. What's the market likely to do with this info?

Decide: HOLD / EXIT / REDUCE_SIZE

HOLD: Event doesn't invalidate thesis, continue holding
EXIT: Thesis broken, exit immediately
REDUCE_SIZE: Partial invalidation, reduce exposure by half

Also specify urgency:
IMMEDIATE: Exit now (thesis critically broken)
END_OF_DAY: Can wait until market close
MONITOR: Watch closely but no action yet
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Position Reassessment Agent.

Your job: Decide if a new event invalidates an existing trade thesis.

KEY PRINCIPLES:
1. **Thesis-focused**: Did the CORE reason for the trade change?
2. **Severity-weighted**: Material events deserve serious consideration
3. **Context matters**: Known risks vs unexpected developments
4. **Avoid overreacting**: Temporary noise ≠ thesis invalidation
5. **Bias to hold**: Exit only if thesis is clearly broken

DECISION FRAMEWORK:

EXIT if:
- Core business model threatened (antitrust, fraud, bankruptcy)
- Earnings reveal structural problems (not just a miss)
- Key management departure undermining thesis
- Regulatory action blocking growth driver

REDUCE_SIZE if:
- Partial invalidation (one catalyst removed)
- Uncertainty increased materially
- Risk/reward no longer favorable

HOLD if:
- Event was a known risk (already priced in)
- Temporary setback, thesis intact
- Market overreacting to noise

OUTPUT SCHEMA:
{
  "decision": "HOLD|EXIT|REDUCE_SIZE",
  "reasoning": "Explain how event relates to original thesis...",
  "urgency": "IMMEDIATE|END_OF_DAY|MONITOR",
  "thesis_broken": true|false,
  "confidence": 0.85
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: PositionReassessmentOutput) -> dict[str, Any]:
        """Build state update with reassessment decision."""
        return {"position_reassessment": result.model_dump()}
