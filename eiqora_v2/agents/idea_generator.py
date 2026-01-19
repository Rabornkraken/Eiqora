"""
Idea Generator Agent implementation.
Synthesizes trade ideas from context, chart, and event data.
"""

import uuid
from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.ideas import IdeaGeneratorOutput, TradeIdea
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.documents import get_documents


class IdeaGeneratorAgent(BaseAgent[IdeaGeneratorOutput]):
    """
    Idea Generator Agent: creates trade ideas from available context.
    
    Synthesizes:
    - Chart setup (from Chart Agent)
    - Stock context (from Context Agent)
    - Event facts (from Event Extractor, if available)
    - TopDown regime (from TopDown Agent, if available)
    """
    
    name = "idea_generator"
    output_schema = IdeaGeneratorOutput
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather all context needed for idea generation."""
        symbol = state["symbol"]
        asof_time = state["asof_time"]

        news_docs: list[dict[str, Any]] = []
        try:
            news_docs = await get_documents(symbol, 72, asof_time, limit=5)
        except Exception:
            news_docs = []

        return {
            "chart": state.get("chart", {}),
            "context": state.get("context", {}),
            "facts": state.get("facts"),
            "topdown": state.get("topdown"),
            "triage": state.get("triage", {}),
            "profile": state.get("profile", {}),  # Baseline thesis
            "fundamental": state.get("fundamental", {}),
            "news_docs": news_docs,
            "trigger_detail": state.get("trigger_detail") or (state.get("trigger") or {}).get("detail"),
        }
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt with all available context."""
        symbol = state["symbol"]
        sector = state.get("sector", "Unknown")
        
        chart = data.get("chart", {})
        context = data.get("context", {})
        facts = data.get("facts")
        topdown = data.get("topdown")
        triage = data.get("triage", {})
        profile = data.get("profile", {})
        fundamental = data.get("fundamental", {}) or {}
        news_docs = data.get("news_docs", []) or []
        trigger_detail = data.get("trigger_detail") or {}
        
        # Extract profile baseline (updated weekly)
        bull_case = profile.get("bull_case", [])
        catalysts = profile.get("catalysts", [])
        risks = profile.get("risks", [])

        sentiment = fundamental.get("sentiment", {}) if isinstance(fundamental, dict) else {}
        earnings = fundamental.get("earnings", {}) if isinstance(fundamental, dict) else {}
        sec_filings = fundamental.get("sec_filings", {}) if isinstance(fundamental, dict) else {}
        insider = fundamental.get("insider", {}) if isinstance(fundamental, dict) else {}
        data_status = fundamental.get("data_status", {}) if isinstance(fundamental, dict) else {}
        
        def _fmt_headlines(items: list[str]) -> str:
            return ", ".join([h for h in items if h]) if items else "None"

        def _fmt_news(docs: list[dict[str, Any]]) -> str:
            if not docs:
                return "None"
            lines = []
            for doc in docs[:5]:
                title = (doc.get("title") or "").strip()
                source = doc.get("source") or doc.get("provider") or "Unknown"
                published_at = doc.get("published_at")
                snippet = (doc.get("text_preview") or "").strip()
                if len(snippet) > 360:
                    snippet = snippet[:360] + "..."
                lines.append(
                    f"- {title} ({source}, {published_at})\n  Snippet: {snippet}"
                )
            return "\n".join(lines)
        
        def _fmt_earnings(earn: dict) -> str:
            """Format earnings data narratively."""
            if not earn or not earn.get("available"):
                return "No recent earnings data"
            
            lines = []
            eps_actual = earn.get("eps_actual")
            eps_est = earn.get("eps_estimate")
            eps_surprise = earn.get("eps_surprise_pct")
            
            if eps_actual is not None and eps_est is not None:
                beat_miss = "BEAT" if eps_actual > eps_est else "MISSED"
                lines.append(f"- {beat_miss} estimate: ${eps_actual:.2f} actual vs ${eps_est:.2f} est")
                if eps_surprise is not None:
                    lines.append(f"  Surprise: {eps_surprise:+.1f}%")
            
            rev_growth = earn.get("revenue_growth_yoy")
            if rev_growth is not None:
                lines.append(f"- Revenue growth: {rev_growth:+.1f}% YoY")
            
            guidance = earn.get("guidance")
            if guidance:
                lines.append(f"- Guidance: {guidance}")
            
            fiscal_q = earn.get("fiscal_quarter")
            if fiscal_q:
                lines.append(f"  ({fiscal_q})")
            
            return "\n".join(lines) if lines else "Earnings data incomplete"
        
        def _fmt_insider(ins: dict) -> str:
            """Format insider trading data narratively."""
            if not ins or not ins.get("available"):
                return "No insider trading data"
            
            buy_count = ins.get("buy_count", 0)
            sell_count = ins.get("sell_count", 0)
            net_value = ins.get("net_value", 0)
            
            if buy_count == 0 and sell_count == 0:
                return "No insider activity in last 90 days"
            
            # Determine sentiment
            if net_value > 100000:
                sentiment = "BULLISH (significant net buying)"
            elif net_value > 10000:
                sentiment = "SLIGHTLY BULLISH (net buying)"
            elif net_value < -100000:
                sentiment = "BEARISH (significant net selling)"
            elif net_value < -10000:
                sentiment = "SLIGHTLY BEARISH (net selling)"
            else:
                sentiment = "NEUTRAL (balanced)"
            
            lines = [
                f"- Activity: {buy_count} buys, {sell_count} sells",
                f"- Net value: ${net_value:,.0f}",
                f"- Sentiment: {sentiment}"
            ]
            
            # Add breakdown by role if available
            by_role = ins.get("by_role", {})
            if by_role:
                role_lines = []
                for role, data in by_role.items():
                    if data.get("net_value", 0) != 0:
                        role_lines.append(f"  {role}: ${data['net_value']:,.0f}")
                if role_lines:
                    lines.append("- By role:")
                    lines.extend(role_lines)
            
            return "\n".join(lines)
        
        prompt = f"""
Generate trade ideas for {symbol} (Sector: {sector}).

PROFILE BASELINE (updated weekly):
- Bull Case: {', '.join(bull_case[:2]) if bull_case else 'None'}
- Known Catalysts: {', '.join(catalysts[:2]) if catalysts else 'None'}
- Known Risks: {', '.join(risks[:2]) if risks else 'None'}

TRIGGER DETAIL:
- Raw: {trigger_detail if trigger_detail else 'None'}

CHART ANALYSIS:
- Setup Type: {chart.get('setup_type', 'NO_SETUP')}
- Direction: {chart.get('direction', 'NEUTRAL')}
- Entry Trigger: {chart.get('entry_trigger', 'None')}
- Invalidation: {chart.get('invalidation', 'None')}
- Setup Quality Score: {chart.get('setup_quality', {}).get('score', 0)}
- Key Levels: {chart.get('key_levels', {})}

        STOCK CONTEXT:
- Current Price: ${context.get('current_price', 0):.2f}
- Trend: {context.get('trend', {})}
- Volatility (RV20): {context.get('vol_basis', {}).get('value', 0):.4f}
- Momentum 20d: {context.get('momentum', {}).get('ret_20d', 'N/A')}%
- State Tags: {context.get('state_tags', [])}
"""
        
        if facts:
            prompt += f"""
EVENT FACTS:
- Summary: {facts.get('event_summary', 'None')}
- Sentiment: {facts.get('sentiment', 'NEUTRAL')}
- Materiality: {facts.get('materiality', 'LOW')}
"""

        if topdown:
            prompt += f"""
TOPDOWN REGIME:
- Market Regime: {topdown.get('regime', 'UNKNOWN')}
- Policy Hints: {topdown.get('policy_hints', {})}
"""

        prompt += f"""
FUNDAMENTAL SNAPSHOT:

News Sentiment:
- Overall: {sentiment.get('overall', 'NEUTRAL')}
- Articles: {sentiment.get('news_count', 0)} ({sentiment.get('positive_count', 0)} pos / {sentiment.get('negative_count', 0)} neg / {sentiment.get('neutral_count', 0)} neutral)
- Key Topics: {_fmt_headlines(sentiment.get('key_topics', []))}
- Notable Headlines: {_fmt_headlines(sentiment.get('notable_headlines', []))}

Earnings (Most Recent):
{_fmt_earnings(earnings)}

Insider Trading (Last 90 days):
{_fmt_insider(insider)}

SEC Filings (Last 30 days):
- 8-K filed: {sec_filings.get('has_8k', False)}
- 10-Q filed: {sec_filings.get('has_10q', False)}
- 10-K filed: {sec_filings.get('has_10k', False)}

Data Freshness:
- News: {'Fresh' if data_status.get('news_fresh') else 'Stale'}
- SEC: {'Fresh' if data_status.get('sec_fresh') else 'Stale'}
- Earnings: {'Fresh' if data_status.get('earnings_fresh') else 'Stale'}

RECENT NEWS SNIPPETS:
{_fmt_news(news_docs)}
"""
        
        prompt += """
Generate up to 3 trade ideas (or 0 if no actionable setup).
For each idea, provide:
- Direction (LONG/SHORT)
- Thesis (why this trade makes sense)
- Time horizon (SWING_SHORT: 5-15d, SWING_MEDIUM: 15-30d, SWING_LONG: 30-45d)
- Conviction level

If no setup is actionable, set skip_reason explaining why.
"""
        return prompt
    
    def _get_system_prompt(self) -> str:
        return """You are an Idea Generator Agent that creates swing trade ideas.

RULES:
1. Only generate ideas for actionable setups (not NO_SETUP).
2. Thesis must be specific and reference the setup, not just "it looks bullish".
3. When news or fundamental context is available, incorporate it into the thesis or catalyst.
4. Direction must align with chart setup direction.
5. Do not invent performance expectations.
6. Generate unique idea_id for each idea (format: "idea_<random>").

OUTPUT SCHEMA:
{
  "ideas": [
    {
      "idea_id": "idea_abc123",
      "direction": "LONG|SHORT",
      "thesis": "<specific thesis, max 300 chars>",
      "setup_type": "BREAKOUT_20D",
      "catalyst": "<catalyst if event-driven>",
      "time_horizon": "SWING_SHORT|SWING_MEDIUM|SWING_LONG",
      "conviction": "HIGH|MEDIUM|LOW"
    }
  ],
  "primary_idea_id": "idea_abc123",
  "skip_reason": null or "<reason if no ideas>"
}

For NO_SETUP charts, return empty ideas array with skip_reason.
Return ONLY valid JSON."""
    
    def _build_state_update(self, state: SwingTradeState, result: IdeaGeneratorOutput) -> dict[str, Any]:
        """Build state update with generated ideas."""
        return {"ideas": result.model_dump()}
