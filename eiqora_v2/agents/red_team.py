"""
Red Team Agent implementation.
Challenges the trade idea to surface risks and missing data.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.red_team import RedTeamOutput
from eiqora_v2.schemas.state import SwingTradeState


class RedTeamAgent(BaseAgent[RedTeamOutput]):
    """
    Red Team Agent: highlights contradictions, missing data, and fatal flaws.
    Runs before the Decision Agent.
    """

    name = "red_team"
    output_schema = RedTeamOutput

    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather context for red team review."""
        from eiqora_v2.tools.event_risk import check_upcoming_events, format_event_warnings
        
        symbol = state["symbol"]
        asof_time = state["asof_time"]
        
        # Check for upcoming events that could affect entry timing
        upcoming_events = await check_upcoming_events(symbol, asof_time, lookforward_days=7)
        event_warning = format_event_warnings(upcoming_events)
        
        return {
            "ideas": state.get("ideas", {}),
            "rules": state.get("rules", []),
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
            "fundamental": state.get("fundamental", {}),
            "topdown": state.get("topdown", {}),
            "profile": state.get("profile", {}),
            "trigger_detail": state.get("trigger_detail") or (state.get("trigger") or {}).get("detail"),
            "upcoming_events": upcoming_events,
            "event_warning": event_warning,
        }

    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for red team analysis."""
        symbol = state["symbol"]

        ideas = data.get("ideas", {})
        rules = data.get("rules", []) or []
        context = data.get("context", {}) or {}
        chart = data.get("chart", {}) or {}
        fundamental = data.get("fundamental", {}) or {}
        topdown = data.get("topdown", {}) or {}
        profile = data.get("profile", {}) or {}
        trigger_detail = data.get("trigger_detail") or {}

        ideas_list = ideas.get("ideas", []) or []
        primary_idea_id = ideas.get("primary_idea_id")
        primary_idea = next(
            (idea for idea in ideas_list if idea.get("idea_id") == primary_idea_id),
            ideas_list[0] if ideas_list else {},
        )

        exit_policy = None
        for rule in rules:
            if "exit_policy" in rule:
                exit_policy = rule.get("exit_policy")
                break

        if not ideas_list:
            return f"""
No trade idea available for {symbol}. Return:
decision=ALLOW, critical=false, key_risks=[], missing_data=[], summary="No idea to challenge."
"""

        sentiment = fundamental.get("sentiment", {}) if isinstance(fundamental, dict) else {}
        data_status = fundamental.get("data_status", {}) if isinstance(fundamental, dict) else {}
        baseline_risks = profile.get("risks", [])
        upcoming_events = data.get("upcoming_events", {})
        event_warning = data.get("event_warning", "")

        return f"""
Red-team the trade idea for {symbol}.

TRADE IDEA:
- Idea ID: {primary_idea.get('idea_id')}
- Direction: {primary_idea.get('direction')}
- Setup: {primary_idea.get('setup_type')}
- Conviction: {primary_idea.get('conviction')}
- Thesis: {primary_idea.get('thesis')}

EXIT POLICY:
{exit_policy if exit_policy else 'Not defined'}

CHART:
- Setup Type: {chart.get('setup_type', 'NO_SETUP')}
- Direction: {chart.get('direction', 'NEUTRAL')}
- Setup Quality: {chart.get('setup_quality', {}).get('score', 'N/A')}
- Entry Trigger: {chart.get('entry_trigger', {})}
- Invalidation: {chart.get('invalidation', {})}

CONTEXT:
- Current Price: {context.get('current_price', 'N/A')}
- Trend: {context.get('trend', {})}
- State Tags: {context.get('state_tags', [])}
- Data Quality: {context.get('data_quality', 'UNKNOWN')}

TOPDOWN:
- Regime: {topdown.get('regime', 'UNKNOWN')}
- Bias: {topdown.get('bias', 'NEUTRAL')}

FUNDAMENTAL:
- News Sentiment: {sentiment.get('overall', 'NEUTRAL')}
- News Count: {sentiment.get('news_count', 0)}
- Data Freshness: news={data_status.get('news_fresh', False)}, sec={data_status.get('sec_fresh', False)}, earnings={data_status.get('earnings_fresh', False)}

UPCOMING EVENTS (Next 7 Days):
{event_warning}

PROFILE RISKS (weekly):
- {', '.join(baseline_risks[:3]) if baseline_risks else 'None'}

TRIGGER DETAIL:
- Raw: {trigger_detail if trigger_detail else 'None'}

Find contradictions, missing data, or fatal flaws. Be strict about:
- Direction vs trend contradiction.
- Weak setup quality or NO_SETUP.
- Exit policy missing or inconsistent.
- Data quality stale or missing.
- Fundamental risk that undermines thesis.
- **UPCOMING EVENTS: Earnings within 2 days = HIGH RISK, consider CAUTION or BLOCK**

If any critical issue exists, set decision=BLOCK and critical=true.
If issues are moderate (e.g. earnings in 3-5 days), decision=CAUTION and critical=false.
If clean, decision=ALLOW and critical=false.

Return key_risks and missing_data lists (max 4 items each).
Summary must be concise (<=300 chars).
"""

    def _get_system_prompt(self) -> str:
        return """You are a Red Team Agent that stress-tests trade ideas.

OBJECTIVE:
Identify the most important contradictions, risks, or missing data.
Do NOT make a trade recommendation. Only assess risk posture.

GUIDANCE:
- BLOCK only for critical issues that invalidate the setup.
- CAUTION for meaningful but non-fatal issues.
- ALLOW if no material issues are found.
- Do not invent missing data or metrics.

OUTPUT SCHEMA:
{
  "decision": "BLOCK|CAUTION|ALLOW",
  "critical": true|false,
  "key_risks": ["..."],
  "missing_data": ["..."],
  "summary": "..."
}

Return ONLY valid JSON."""

    def _build_state_update(self, state: SwingTradeState, result: RedTeamOutput) -> dict[str, Any]:
        """Build state update with red-team output."""
        return {"red_team": result.model_dump()}
