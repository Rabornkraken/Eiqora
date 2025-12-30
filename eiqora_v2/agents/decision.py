"""
Decision Agent implementation.
Makes GO/NO_GO decision based on stats and gate checks.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.decision import DecisionOutput, TradeRule, StatsResult
from eiqora_v2.schemas.state import SwingTradeState


# Decision gates for mega-cap stocks
MEGA_CAP_GATES = {
    "min_win_rate": 0.45,
    "min_expected_return": 0.02,
    "min_sample_size": 20,
    "max_risk_score": 0.7,
    "max_sl_pct": 0.08,  # 8% max stop loss
}


class DecisionAgent(BaseAgent[DecisionOutput]):
    """
    Decision Agent: makes final GO/NO_GO decision.
    
    Evaluates:
    - Stats from analog evaluation (win rate, expected return)
    - Gate checks (sample size, risk limits)
    - Exit policy feasibility
    
    Outputs a complete trade rule if GO.
    """
    
    name = "decision"
    output_schema = DecisionOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather all data needed for decision."""
        return {
            "ideas": state.get("ideas", {}),
            "rules": state.get("rules", []),
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
            "stats": state.get("stats", []),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for decision making."""
        symbol = state["symbol"]
        
        ideas = data.get("ideas", {})
        rules = data.get("rules", [])
        context = data.get("context", {})
        chart = data.get("chart", {})
        stats = data.get("stats", [])
        
        primary_idea_id = ideas.get("primary_idea_id")
        ideas_list = ideas.get("ideas", [])
        
        if not ideas_list:
            return f"""
No ideas to evaluate for {symbol}. Return NO_GO decision.
"""
        
        primary_idea = next((i for i in ideas_list if i.get("idea_id") == primary_idea_id), ideas_list[0])
        
        # Find exit policy
        exit_policy = None
        for rule in rules:
            if "exit_policy" in rule:
                exit_policy = rule["exit_policy"]
                break
        
        # Get stats if available
        stats_text = "No stats available (Stats Service not run)"
        if stats:
            stats_text = "\n".join([
                f"- Plan {s.get('plan_id')}: win_rate={s.get('win_rate', 'N/A')}, "
                f"exp_return={s.get('expected_return', 'N/A')}, samples={s.get('sample_size', 0)}"
                for s in stats
            ])
        
        vol_basis = context.get("vol_basis", {})
        rv20 = vol_basis.get("value", 0.02)
        
        return f"""
Make GO/NO_GO decision for {symbol}.

MEGA-CAP GATES:
- Min Win Rate: {MEGA_CAP_GATES['min_win_rate']}
- Min Expected Return: {MEGA_CAP_GATES['min_expected_return']}
- Min Sample Size: {MEGA_CAP_GATES['min_sample_size']}
- Max Risk Score: {MEGA_CAP_GATES['max_risk_score']}
- Max SL %: {MEGA_CAP_GATES['max_sl_pct']}

PRIMARY IDEA:
- ID: {primary_idea.get('idea_id')}
- Direction: {primary_idea.get('direction')}
- Setup: {primary_idea.get('setup_type')}
- Conviction: {primary_idea.get('conviction')}

EXIT POLICY:
{exit_policy if exit_policy else 'Not defined'}

STATS:
{stats_text}

CONTEXT:
- Current Price: ${context.get('current_price', 0):.2f}
- RV20: {rv20:.4f}
- SL Level: ${context.get('current_price', 0) * (1 - rv20 * (exit_policy.get('bracket', {}).get('sl_mult', 2) if exit_policy else 2)):.2f}

CHART:
- Entry Trigger: {chart.get('entry_trigger', {})}
- Invalidation: {chart.get('invalidation', {})}

Evaluate all gates and provide decision.
If stats are unavailable, make decision based on setup quality and conviction.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Decision Agent that makes final trading decisions.

GATE EVALUATION:
1. stats_gate: win_rate >= 0.45 AND expected_return >= 0.02 AND sample_size >= 20
2. risk_gate: sl_pct <= 0.08 (stop loss not too wide)
3. conviction_gate: conviction is HIGH or MEDIUM for GO
4. data_quality_gate: stats status is OK or sample_size is reasonable

DECISION RULES:
- GO: All critical gates pass, stats look good
- NO_GO: Critical gate fails, stats prohibitive
- REVIEW: Edge case, needs human review

TRADE RULE (if GO):
Create complete rule with entry trigger, exit bracket, and invalidation level.

OUTPUT SCHEMA:
{
  "decision": "GO|NO_GO|REVIEW",
  "rule": {
    "rule_id": "rule_<random>",
    "idea_id": "<idea ID>",
    "direction": "LONG|SHORT",
    "entry_trigger_type": "BREAK_YDAY_HIGH",
    "entry_level": 275.50,
    "tp_mult": 4.0,
    "sl_mult": 2.0,
    "time_stop_days": 30,
    "invalidation_type": "CLOSE_BELOW_LEVEL",
    "invalidation_level": 270.0,
    "stats": null
  } or null,
  "gates_passed": ["stats_gate", "risk_gate"],
  "gates_failed": [],
  "reason": "<decision reasoning>",
  "risk_score": 0.4
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: DecisionOutput) -> dict[str, Any]:
        """Build state update with decision."""
        return {"decision": result.model_dump()}
