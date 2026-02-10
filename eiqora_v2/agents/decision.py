"""
Decision Agent implementation.
Makes GO/NO_GO decision based on setup quality and conviction.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.decision import DecisionOutput, TradeRule
from eiqora_v2.schemas.state import SwingTradeState


class DecisionAgent(BaseAgent[DecisionOutput]):
    """
    Decision Agent: makes final GO/NO_GO decision.
    
    Evaluates:
    - Setup quality and conviction
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
            "profile": state.get("profile", {}),  # Baseline risks
            "fundamental": state.get("fundamental", {}),
            "red_team": state.get("red_team", {}),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for decision making."""
        symbol = state["symbol"]
        
        ideas = data.get("ideas", {})
        rules = data.get("rules", [])
        context = data.get("context", {})
        chart = data.get("chart", {})
        profile = data.get("profile", {})
        fundamental = data.get("fundamental", {})
        red_team = data.get("red_team", {})
        rel_strength = (context or {}).get("relative_strength", {})
        
        # Extract profile baseline risks
        baseline_risks = profile.get("risks", [])
        bear_case = profile.get("bear_case", [])
        insider = fundamental.get("insider") if isinstance(fundamental, dict) else None
        
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
        
        vol_basis = context.get("vol_basis", {})
        rv20 = vol_basis.get("value", 0.02)

        # Pre-compute risk metrics to avoid LLM math errors.
        direction = primary_idea.get("direction", "LONG")
        current_price = context.get("current_price", 0)
        sl_mult = exit_policy.get("bracket", {}).get("sl_mult", 2) if exit_policy else 2
        if current_price and rv20:
            if direction == "SHORT":
                sl_level = current_price * (1 + rv20 * sl_mult)
                sl_pct = (sl_level - current_price) / current_price
            else:
                sl_level = current_price * (1 - rv20 * sl_mult)
                sl_pct = (current_price - sl_level) / current_price
        else:
            sl_level = None
            sl_pct = None
        setup_type = chart.get("setup_type", "NO_SETUP")
        setup_quality = (chart.get("setup_quality") or {}).get("score")
        
        return f"""
Make GO/NO_GO decision for {symbol}.

PROFILE BASELINE (updated weekly):
- Known Risks: {', '.join(baseline_risks[:2]) if baseline_risks else 'None'}
- Bear Case: {', '.join(bear_case[:2]) if bear_case else 'None'}
(Check if trigger / fresh news confirms or contradicts these)

INSIDER ACTIVITY (last 90 days):
- Available: {insider.get('available') if isinstance(insider, dict) else 'Unknown'}
- Buys/Sells: {insider.get('buy_count') if isinstance(insider, dict) else 'N/A'} / {insider.get('sell_count') if isinstance(insider, dict) else 'N/A'}
- Net value: {insider.get('net_value') if isinstance(insider, dict) else 'N/A'}
- CEO net: {insider.get('ceo_net_value') if isinstance(insider, dict) else 'N/A'}
- CFO net: {insider.get('cfo_net_value') if isinstance(insider, dict) else 'N/A'}
- Director net: {insider.get('director_net_value') if isinstance(insider, dict) else 'N/A'}

SIGNAL QUALITY (Trigger Assessment):
- Quality Score: {state.get('trigger_detail', {}).get('quality_score', 'N/A')} / 1.0
- Quality Level: {state.get('trigger_detail', {}).get('quality_level', 'Unknown')}
- Quality Flags: {', '.join(state.get('trigger_detail', {}).get('quality_flags', []))}
- Confluence: {state.get('trigger_detail', {}).get('has_confluence', False)} ({state.get('trigger_detail', {}).get('trigger_count', 1)} triggers)

Use signal quality to calibrate conviction:
- Quality > 0.75: Consider HIGH conviction if other factors align
- Quality 0.50-0.75: Standard evaluation
- Quality < 0.50: Reduce conviction or pass unless setup is exceptional

EXIT POLICY (from exit_policy agent):
{exit_policy if exit_policy else 'Not available - use defaults'}

**IMPORTANT: Use exit policy values in your rule output!**
If exit_policy provides sl_mult/sl_level, use those values.
Do NOT hardcode sl_mult=2.0 - respect the agent's adaptive choices.

PRIMARY IDEA:
- ID: {primary_idea.get('idea_id')}
- Direction: {primary_idea.get('direction')}
- Setup: {primary_idea.get('setup_type')}
- Conviction: {primary_idea.get('conviction')}

EXIT POLICY:
{exit_policy if exit_policy else 'Not defined'}

CONTEXT:
- Current Price: ${current_price:.2f}
- RV20: {rv20:.4f}
- SL Level (model): {f'${sl_level:.2f}' if sl_level else 'N/A'}
- SL % (fraction): {sl_pct if sl_pct is not None else 'N/A'} (percent: {(sl_pct * 100):.2f} if sl_pct is not None else 'N/A')
- Relative Strength: vs SPY={rel_strength.get('vs_spy', {})}, vs Sector={rel_strength.get('vs_sector', {})}, vs QQQ={rel_strength.get('vs_qqq', {})}

CHART:
- Setup Type: {setup_type}
- Setup Quality Score: {setup_quality if setup_quality is not None else 'N/A'}
- Entry Trigger: {chart.get('entry_trigger', {})}
- Invalidation: {chart.get('invalidation', {})}

RED TEAM:
- Decision: {red_team.get('decision', 'N/A')}
- Critical: {red_team.get('critical', False)}
- Summary: {red_team.get('summary', 'N/A')}
- Key Risks: {red_team.get('key_risks', [])}
- Missing Data: {red_team.get('missing_data', [])}

Evaluate all gates and provide decision.
Consider: do baseline risks create additional concerns for this trade?
You may return GO if setup_quality >= 0.65 and conviction is HIGH or MEDIUM. Otherwise use REVIEW/NO_GO.
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Decision Agent that makes final trading decisions.

GATE EVALUATION:
1. conviction_gate: conviction is HIGH or MEDIUM for GO.
2. setup_quality_gate: setup_quality >= 0.65 for GO.

DECISION RULES:
- GO: Conviction is HIGH or MEDIUM, setup_quality >= 0.65.
- REVIEW: setup_quality is 0.4-0.65, or conviction is LOW but setup exists.
- NO_GO: No setup or setup_quality < 0.4.
 - NO_GO: If red_team critical=true or decision=BLOCK.

TRADE RULE (if GO):
Create complete rule with entry trigger, exit bracket, and invalidation level.
**Use exit_policy bracket values (sl_mult, tp_mult, sl_level, tp_level) if available.**

OUTPUT SCHEMA:
{
  "decision": "GO|NO_GO|REVIEW",
  "rule": {
    "rule_id": "rule_<random>",
    "idea_id": "<idea ID>",
    "direction": "LONG|SHORT",
    "entry_trigger_type": "BREAK_YDAY_HIGH",
    "entry_level": 275.50,
    "tp_mult": <from exit_policy or 3.5>,
    "tp_level": <from exit_policy or null>,
    "sl_mult": <from exit_policy or 1.5>,
    "sl_level": <from exit_policy or null>,
    "time_stop_days": <from exit_policy or 30>,
    "invalidation_type": "CLOSE_BELOW_LEVEL",
    "invalidation_level": 270.0
  } or null,
  "gates_passed": ["setup_quality_gate"],
  "gates_failed": [],
  "reason": "<decision reasoning>",
  "risk_score": 0.4
}

Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: DecisionOutput) -> dict[str, Any]:
        """Build state update with decision."""
        decision_dict = result.model_dump()
        red_team = state.get("red_team", {}) or {}

        # Check Short Perspective (now embedded in red_team output)
        if isinstance(red_team, dict) and red_team.get("recommend_reject_long"):
            short_case = red_team.get("short_case_strength", "NONE")
            short_score = red_team.get("short_score", 0.0)

            # Only reject on STRONG short case
            if short_case == "STRONG":
                reason_prefix = f"Short Perspective REJECT: strong short case (score={short_score:.1f})"
                merged_reason = f"{reason_prefix} {decision_dict.get('reason', '')}".strip()
                if len(merged_reason) > 1000:
                    merged_reason = merged_reason[:1000]

                decision_dict["decision"] = "NO_GO"
                decision_dict["rule"] = None
                decision_dict["reason"] = merged_reason
                decision_dict["risk_score"] = max(decision_dict.get("risk_score", 0.0), 0.85)

                gates_failed = decision_dict.get("gates_failed", [])
                if "short_perspective" not in gates_failed:
                    gates_failed.append("short_perspective")
                decision_dict["gates_failed"] = gates_failed

        # Check Red Team critical (if not already rejected by Short Perspective)
        if decision_dict.get("decision") != "NO_GO" and isinstance(red_team, dict) and red_team.get("critical"):
            reason_prefix = f"RedTeam BLOCK: {red_team.get('summary', 'Critical risk flagged')}"
            merged_reason = f"{reason_prefix} {decision_dict.get('reason', '')}".strip()
            if len(merged_reason) > 1000:
                merged_reason = merged_reason[:1000]

            decision_dict["decision"] = "NO_GO"
            decision_dict["rule"] = None
            decision_dict["reason"] = merged_reason
            decision_dict["risk_score"] = max(decision_dict.get("risk_score", 0.0), 0.9)

            gates_failed = decision_dict.get("gates_failed", [])
            if "red_team" not in gates_failed:
                gates_failed.append("red_team")
            decision_dict["gates_failed"] = gates_failed

        return {"decision": decision_dict}
