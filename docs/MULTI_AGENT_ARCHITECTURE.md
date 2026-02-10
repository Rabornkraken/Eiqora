# Multi-Agent Analysis Architecture

This document details the internal architecture of the `eiqora_v2` analysis engine ("The Brain"). It explains how specialized AI agents collaborate to generate trading decisions using a `langgraph`-based state machine.

## Overview

The core of Eiqora is a **Directed Acyclic Graph (DAG)** of 15+ specialized agents. Each agent performs a specific role (e.g., technical analysis, risk assessment) and contributes to a shared `SwingTradeState`.

**Key Technologies:**
*   `langgraph`: Manages the state machine and execution flow.
*   `pydantic`: Enforces strict schemas for agent inputs and outputs.
*   `asyncio`: Handles concurrent data fetching and agent execution.

---

## State Management (`SwingTradeState`)

Data flows through the system in a structured `TypedDict` defined in `eiqora_v2/schemas/state.py`. This acts as the "shared memory" for the agent swarm.

```python
# eiqora_v2/schemas/state.py

class SwingTradeState(TypedDict, total=False):
    """
    State object passed through the LangGraph workflow.
    
    All agent outputs are accumulated here as the graph executes.
    Uses total=False to allow partial updates.
    """
    # Request context
    request_id: str
    symbol: str
    sector: str
    sector_etf: str
    asof_time: datetime
    
    # Trigger information
    trigger_type: str  # SEC_8K, NEWS, CHART_SETUP, etc.
    trigger_refs: list[str]
    trigger_priority: str
    trigger_detail: dict[str, Any]

    # Enriched deterministic context
    profile: dict[str, Any] | None
    market_data: dict[str, Any] | None
    data_freshness: dict[str, Any] | None
    enrichment_errors: list[str] | None
    
    # Agent outputs (accumulated as graph executes)
    triage: dict[str, Any] | None
    facts: dict[str, Any] | None  # From Event Extractor
    context: dict[str, Any] | None
    topdown: dict[str, Any] | None
    chart: dict[str, Any] | None
    ideas: dict[str, Any] | None
    rules: list[dict[str, Any]] | None
    exit_policy: dict[str, Any] | None
    red_team: dict[str, Any] | None
    short_perspective: dict[str, Any] | None
    decision: dict[str, Any] | None
    position_manager: dict[str, Any] | None
    veto: dict[str, Any] | None
    narrative: dict[str, Any] | None
    
    # Control flow
    needs_extraction: bool
    should_continue: bool
    filter_reason: str | None
    
    # Error tracking
    errors: list[str]
```

---

## The Orchestrator

The `Orchestrator` (`eiqora_v2/orchestrator.py`) builds the agent graph. It ensures that agents run in the correct order so that downstream agents (like `Decision`) have access to the outputs of upstream agents (like `TopDown` and `Chart`).

```python
# eiqora_v2/orchestrator.py

    def _build_registry(self) -> tuple[AgentRegistry, list[str]]:
        """Build registry and resolve execution order."""
        registry = AgentRegistry()
        order: list[str] = []

        # 1. TopDown (macro context) - uses SPY/QQQ/sector OHLCV + VIX
        if self.config.include_topdown:
            try:
                from eiqora_v2.agents.topdown import TopDownAgent
                registry.register("topdown", TopDownAgent())
                order.append("topdown")
            except Exception as e:
                logger.warning(f"Could not load TopDownAgent: {e}")

        # 2. Context (stock-level technicals) - uses market_bar_daily
        from eiqora_v2.agents.context import ContextAgent
        registry.register("context", ContextAgent())
        order.append("context")

        # 3. Chart (setup classification) - uses market_bar_daily
        from eiqora_v2.agents.chart import ChartAgent
        registry.register("chart", ChartAgent(), depends_on=["context"])
        order.append("chart")

        # 4. Supply Chain (relationships & correlations)
        if self.config.include_supply_chain:
            try:
                from eiqora_v2.agents.supply_chain import SupplyChainAgent
                registry.register("supply_chain", SupplyChainAgent(), depends_on=["context"])
                order.append("supply_chain")
            except Exception as e:
                logger.warning(f"Could not load SupplyChainAgent: {e}")

        # 5. Fundamental (news/SEC/earnings) - uses yfinance_news, sec_filing, earnings_event
        if self.config.include_fundamental:
            try:
                from eiqora_v2.agents.fundamental import FundamentalAgent
                registry.register("fundamental", FundamentalAgent())
                order.append("fundamental")
            except Exception as e:
                logger.warning(f"Could not load FundamentalAgent: {e}")

        # 6. Idea Generator - synthesizes context/chart/supply_chain/fundamental
        from eiqora_v2.agents.idea_generator import IdeaGeneratorAgent
        idea_deps = ["context", "chart"]
        if registry.has("supply_chain"):
            idea_deps.append("supply_chain")
        if self.config.include_topdown and registry.has("topdown"):
            idea_deps.insert(0, "topdown")
        if self.config.include_fundamental and registry.has("fundamental"):
            idea_deps.append("fundamental")
        registry.register("idea_generator", IdeaGeneratorAgent(), depends_on=idea_deps)
        order.append("idea_generator")

        # 7. Exit Policy - TP/SL/time stop definition
        from eiqora_v2.agents.exit_policy import ExitPolicyAgent
        registry.register("exit_policy", ExitPolicyAgent(), depends_on=["idea_generator"])
        order.append("exit_policy")

        # 8. Red Team - stress test ideas
        from eiqora_v2.agents.red_team import RedTeamAgent
        registry.register("red_team", RedTeamAgent(), depends_on=["idea_generator", "exit_policy"])
        order.append("red_team")

        # 9. Short Perspective - contrarian rebuttal (would I short this?)
        from eiqora_v2.agents.short_perspective import ShortPerspectiveAgent
        registry.register("short_perspective", ShortPerspectiveAgent(), depends_on=["red_team"])
        order.append("short_perspective")

        # 10. Decision - final GO/NO_GO
        from eiqora_v2.agents.decision import DecisionAgent
        registry.register("decision", DecisionAgent(), depends_on=["short_perspective"])
        order.append("decision")

        # 10. Veto - sanity checks
        from eiqora_v2.agents.veto import VetoAgent
        registry.register("veto", VetoAgent(), depends_on=["decision"])
        order.append("veto")

        return registry, registry.execution_order(order)
```

---

## Agent Roles & Implementation

### 1. Analysis Agents (The Core DAG)

#### TopDownAgent (`topdown.py`)
*   **Role:** Macro strategist. Runs **once per batch**.
*   **Why:** Prevents buying breakouts during market crashes or ahead of critical Fed events.

```python
# eiqora_v2/agents/topdown.py

    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for macro analysis including economic calendar."""
        asof_time = state["asof_time"]
        
        spy = data.get("spy", {})
        qqq = data.get("qqq", {})
        sectors = data.get("sectors", {})
        econ = data.get("economic_calendar", {})
        
        # Format sector performance with short-term momentum
        def format_sector(etf, d):
            ret_20d = d.get('ret_20d', 0) or 0
            ret_5d = d.get('ret_5d', 0) or 0
            trend = d.get('trend', 'N/A')
            # Flag sectors where 5d momentum diverges from 20d (potential rotation)
            divergence = ""
            if ret_20d > 0.02 and ret_5d < -0.01:
                divergence = " ⚠️ WEAKENING"
            elif ret_20d < -0.02 and ret_5d > 0.01:
                divergence = " 🔄 RECOVERING"
            return f"  {etf}: 5d={ret_5d:.1%}, 20d={ret_20d:.1%}, MA20={trend}{divergence}"
        
        sector_text = "\n".join([
            format_sector(etf, d)
            for etf, d in sectors.items()
        ]) if sectors else "  No sector data"
        
        # Format economic calendar
        upcoming = econ.get("upcoming_events", [])[:5]  # Limit to 5
        recent = econ.get("recent_events", [])[:3]  # Limit to 3
        
        upcoming_text = "\n".join([
            f"  {e.get('event_date', 'N/A')}: {e.get('indicator_name', 'N/A')}"
            for e in upcoming
        ]) if upcoming else "  No upcoming events"
        
        recent_text = "\n".join([
            f"  {e.get('event_date', 'N/A')}: {e.get('indicator_name', 'N/A')} = {e.get('value', 'N/A')}"
            for e in recent
        ]) if recent else "  No recent events"
        
        days_to_fomc = econ.get("days_to_fomc")
        fomc_warning = ""
        if days_to_fomc is not None and days_to_fomc <= 3:
            fomc_warning = f"\n⚠️ FOMC MEETING IN {days_to_fomc} DAYS - Consider volatility impact!"
        
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

ECONOMIC CALENDAR:
Upcoming Events (next 14 days):
{upcoming_text}

Recent Events (last 7 days):
{recent_text}

Days to next FOMC: {days_to_fomc if days_to_fomc is not None else 'Unknown'}
Days to next CPI: {econ.get('next_cpi', 'Unknown')}
{fomc_warning}

Classify:
1. Market regime (RISK_ON, RISK_OFF, HIGH_VOL, etc.)
2. SPY trend (UP, DOWN, SIDEWAYS)
3. VIX level based on RV20 proxy (LOW <15, NORMAL 15-20, ELEVATED 20-25, HIGH >25)
4. Sector rotation (which sectors are LEADING/LAGGING)
5. Policy stance based on economic calendar (Fed hawkish/dovish, data beats/misses)
6. Overall bias (BULLISH, BEARISH, NEUTRAL)
"
```

#### ContextAgent (`context.py`)
*   **Role:** Quantitative analyst.
*   **Implementation:** **Deterministic (No LLM).** purely code-based feature extraction.
*   **Why:** Provides grounded mathematical truth that LLMs cannot hallucinate.

```python
# eiqora_v2/agents/context.py

    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Compute deterministic context output from indicators."""
        symbol = state.get("symbol")
        asof_time = state.get("asof_time")

        try:
            data = await self._gather_data(state)
            daily = data.get("daily", {})

            if daily.get("error"):
                msg = daily.get("error", "Unknown error")
                self.logger.warning(f"{symbol}: Context data error - {msg}")
                return {
                    "context": {"error": msg, "data_points": daily.get("data_points", 0)},
                    "errors": state.get("errors", []) + [f"context: {msg}"],
                }

            current_price = float(daily.get("current_price") or 0)
            if current_price <= 0:
                raise ValueError("Missing or invalid current_price")

            vol_basis = self._build_vol_basis(daily, current_price)
            trend = self._build_trend(daily, current_price)
            momentum = MomentumMetrics(
                ret_20d=daily.get("ret_20d"),
                ret_60d=daily.get("ret_60d"),
            )
            rel_strength = await self._build_relative_strength(
                symbol,
                daily,
                asof_time,
            )

            output = ContextOutput(
                vol_basis=vol_basis,
                trend=trend,
                momentum=momentum,
                volume_z_20d=float(daily.get("volume_z_20d") or 0.0),
                state_tags=list(daily.get("state_tags") or []),
                current_price=current_price,
                atr14=float(daily.get("atr14") or current_price * 0.02),  # ATR for stop loss
                relative_strength=rel_strength,
                data_quality=self._data_quality(daily, asof_time),
                options=data.get("options"),  # Add options sentiment
            )

            return {"context": output.model_dump()}

        except Exception as e:
            self.logger.error(f"{symbol}: Context computation failed - {e}")
            return {
                "context": {"error": str(e)},
                "errors": state.get("errors", []) + [f"context: {e}"],
            }
```

#### IdeaGeneratorAgent (`idea_generator.py`)
*   **Role:** The "Trader" persona. Synthesizes all previous inputs.
*   **Prompt:** Combines technicals, macro, and news into a cohesive thesis.

```python
# eiqora_v2/agents/idea_generator.py

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
        
        # ... (formatting helper functions _fmt_headlines, _fmt_news, _fmt_earnings, _fmt_insider omitted for brevity in Python code but are part of logic) ...
        
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
"
        
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
```

#### RedTeamAgent (`red_team.py`)
*   **Role:** The "Devil's Advocate". Tries to kill the trade.
*   **Logic:** Explicitly checks for "fatal flaws" like upcoming earnings.

```python
# eiqora_v2/agents/red_team.py

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
""
```

#### DecisionAgent (`decision.py`)
*   **Role:** The Portfolio Manager. Makes the final call.
*   **Logic:** Enforces strict gates on setup quality and conviction.

```python
# eiqora_v2/agents/decision.py

    def _get_system_prompt(self) -> str:
        return """
You are a Decision Agent that makes final trading decisions.

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

Return ONLY valid JSON.
"""
```

#### VetoAgent (`veto.py`)
*   **Role:** Final deterministic safety check.
*   **Logic:** Rejects trades if direction defies trend or data is stale.

```python
# eiqora_v2/agents/veto.py

    def _get_system_prompt(self) -> str:
        return """
You are a Sanity/Veto Agent that performs final safety checks.

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

Return ONLY valid JSON.
"""
```

---

### 2. Portfolio Agents (Post-Analysis)

These agents run *after* a trade has been approved by the analysis core, to fit it into the broader portfolio.

#### PositionManagerAgent (`position_manager.py`)
*   **Role:** Contextual portfolio sizing.
*   **Logic:** Uses LLM to check if a new trade fits the current portfolio (exposure, concentration).

```python
# eiqora_v2/agents/position_manager.py

    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build contextual prompt for position management."""
        proposed = data["proposed_trade"]
        positions = data["current_positions"]
        exposure = data["total_exposure_pct"]
        available = data["available_capital_pct"]
        topdown = data.get("topdown", {})
        risk_model = data.get("risk_model", {}) or {}
        
        # MACRO SAFEGUARD: Check for market stress (SPY drawdown)
        spy_indicators = topdown.get("spy", {})
        spy_drawdown = spy_indicators.get("drawdown_pct", 0) if isinstance(spy_indicators, dict) else 0
        market_stress = spy_drawdown < -10  # SPY down 10%+
        
        # Format current positions
        if positions:
            positions_text = "\n".join([
                f"  - {p['symbol']}: {p['size_pct']:.1f}% exposure, "
                f"{p['days_held']}d held, P&L: {p['pnl_pct']:+.1f}%"
                for p in positions
            ])
        else:
            positions_text = "  (No open positions)"
        
        market_regime = topdown.get("regime", "UNKNOWN")
        market_bias = topdown.get("bias", "NEUTRAL")
        
        # Build stress warning if applicable
        stress_warning = ""
        if market_stress:
            stress_warning = f"""
⚠️ MARKET STRESS ALERT:
- SPY drawdown: {spy_drawdown:.1f}%
- CORRELATION RISK: All stocks may move together
- Consider: Smaller position sizes or pause
"""
        
        return f"""
Evaluate whether to accept new trade given current portfolio state.

MARKET CONTEXT:
- Regime: {market_regime}
- Bias: {market_bias}

{stress_warning}

CURRENT PORTFOLIO:
- Open positions: {len(positions)}
- Total exposure: {exposure:.1f}%
- Available capital: {available:.1f}%

Positions:
{positions_text}

PROPOSED TRADE:
- Symbol: {proposed['symbol']}
- Direction: {proposed['direction']}
- Setup: {proposed['setup_type']}
- Conviction: {proposed['conviction']}
- Proposed size: {proposed['proposed_size_pct']:.1f}%
- Entry Price: {proposed.get('entry_price', 0)}
- Stop Loss: {proposed.get('stop_loss', 'N/A')}

RISK MODEL (deterministic caps):
- Recommended size %: {risk_model.get('position_size_pct', 'N/A')}
- Portfolio heat cap: {risk_model.get('portfolio_heat_cap', 'N/A')}
- Available exposure %: {risk_model.get('available_exposure_pct', 'N/A')}
- Position count: {risk_model.get('position_count', 'N/A')}
- Sector exposure: {risk_model.get('sector_exposure', {})}
- Notes: {risk_model.get('notes', [])}

GUIDELINES (interpret contextually, not hard rules):
- Generally prefer 3-4 positions max
- Generally prefer <50% total exposure
- Watch for cluster/sector concentration
- Consider existing position performance
- IN MARKET STRESS: Be VERY cautious, consider smaller sizes or pause

CONTEXTUAL CONSIDERATIONS:
- Exceptional setup in new sector → might allow 4th+ position
- Same cluster as existing winner → might size smaller
- Multiple losing positions → might be more selective
- Low total exposure → more room for new position
- High conviction + diversifying → override position count
- Market stress (correlation breakdown) → extra caution warranted

Make a contextual decision: APPROVE, REDUCE_SIZE, or REJECT.
If REDUCE_SIZE, suggest appropriate size. Do not exceed the risk model recommended size.
Explain reasoning based on portfolio context.
"""
```

#### PortfolioCoordinatorAgent (`portfolio_coordinator.py`)
*   **Role:** Batch constraint enforcement.
*   **Logic:** Runs once per batch of signals. Deterministically enforces limits like "Max 3 Tech stocks" or "Max 10 positions".

---

### 3. Monitoring Agents (Lifecycle)

These agents manage *existing* positions, typically running on a daily schedule.

#### PositionMonitorAgent (`position_monitor.py`)
*   **Role:** Daily checkup.
*   **Logic:** Checks for "Tier 2" events (VIX spike, earnings approaching).
*   **Actions:** `HOLD`, `TIGHTEN` (move stop up), `WIDEN` (loosen stop), `EXIT`.

#### PositionReassessmentAgent (`position_reassessment.py`)
*   **Role:** Crisis management.
*   **Logic:** Triggered by "Tier 1" events (e.g., earnings miss). Decides if the *original thesis* is broken.
*   **Actions:** `HOLD`, `REDUCE_SIZE`, `EXIT`.

```

---

## Redundancy & Improvement Audit (Jan 2026)

### Full Pipeline Flow

```
Profile (weekly LLM)
  → Watchlist (daily deterministic: 50% tech + 50% profile score)
    → Trigger Monitor (continuous: hourly + daily + event triggers)
      → Context Enrichment (load profile + indicators into state)
        → 10-Agent LLM Pipeline (TopDown → Context → Chart → ... → Narrative)
          → Trade Execution or Rejection
```

### Identified Redundancies

#### 1. Quadruple Decision Gate (Highest Impact)

Four sequential agents apply overlapping veto logic:

| Agent | Checks | Overlap With |
|-------|--------|-------------|
| **RedTeam** | Contradictions, missing data, direction vs trend, upcoming events | Veto (direction alignment, data quality) |
| **ShortPerspective** | Re-scores RSI/MFI/volume overbought — indicators already in Context/Chart | Context, Chart |
| **Decision** | setup_quality >= 0.65, conviction HIGH/MEDIUM | Chart (setup_quality), IdeaGenerator (conviction) |
| **Veto** | Direction vs trend, data quality SPARSE/STALE, risk_score > 0.8 | RedTeam (all three), Context (data quality) |

**Recommendation**: Merge ShortPerspective into RedTeam (combined "Risk Assessment Agent"). Fold Veto's novel checks into Decision. Result: 4 agents → 2 agents.

#### 2. FundamentalAgent Re-queries Profile Data

ProfileGenerator already gathers and scores:
- Earnings (beat rate, surprise %, guidance)
- News sentiment (FinBERT article-level)
- SEC filings (8-K, 10-Q, 10-K)
- Insider transactions (90d buy/sell)
- Money flow (CMF, MFI, OBV)

Then FundamentalAgent re-queries the same tables:
```python
# fundamental.py lines 63-76 — all redundant with profile
news_docs = await get_documents(symbol, 72, asof_time, limit=20)
earnings_snapshot = await self._get_latest_earnings(symbol, asof_time)
insider_summary = await self._get_insider_summary(symbol, asof_time)
```

**Recommendation**: FundamentalAgent should read from the cached profile and only fetch **new events since profile creation** (delta query). Saves 4-5 DB queries + sentiment re-inference per trigger.

#### 3. Technical Indicators Extracted 2-3 Times

| Indicator | ContextAgent | ChartAgent | ShortPerspective |
|-----------|-------------|-----------|-----------------|
| RSI14 | Yes | Yes | Yes (re-scored) |
| MA20/50/200 | Yes (trend) | Yes (invalidation) | Yes (trend check) |
| Volume Z | Yes | No | Yes (re-scored) |
| MFI/CMF | No | No | Yes (scored from raw) |

ShortPerspective has defensive fallback chains because there's no canonical source:
```python
rsi14 = self._coerce_float(daily.get("rsi14") or context.get("rsi14"))
```

**Recommendation**: ContextAgent should be the single canonical source for all technical indicators. Downstream agents reference `state["context"]` only.

#### 4. Data Quality Checked 3 Times

- **ContextAgent**: Classifies SPARSE/STALE/GOOD (< 20 bars or gap >= 3 days)
- **RedTeam**: Flags `missing_data` if stale
- **Veto**: Re-checks and vetoes if not GOOD

**Recommendation**: Single canonical check in ContextAgent. Others reference `context.data_quality`.

### Profile → Watchlist → Agents: Does This Logic Make Sense?

**Yes, the three-stage funnel is architecturally sound:**

1. **Profile** (weekly, expensive LLM): Deep dossier with bull/bear cases, catalysts, risks, hybrid score
2. **Watchlist** (daily, cheap deterministic): 50/50 blend of daily technicals + profile score, macro-adjusted threshold → ~15 candidates
3. **Triggers** (continuous, cheap pattern matching): Only fires on the watchlist subset
4. **Agents** (per-trigger, expensive LLM x10): Full analysis only on triggered candidates

**The problem is not the funnel — it's the handoff.** The profile already contains rich fundamental analysis, but the agent pipeline partially re-derives it instead of consuming it. Specifically:

- FundamentalAgent should be a **delta agent** (what changed since the profile?), not a full re-analysis
- IdeaGenerator already reads `profile.bull_case`, `profile.catalysts` — good
- RedTeam already reads `profile.risks` — good
- ShortPerspective ignores the profile entirely and re-scores from raw indicators — wasteful

### Improvement Roadmap

| Priority | Change | Impact |
|----------|--------|--------|
| **P0** | Merge ShortPerspective into RedTeam | -1 LLM call/trigger, cleaner risk layer |
| **P0** | FundamentalAgent reads profile + delta only | -4 DB queries/trigger, -500ms latency |
| **P1** | Fold Veto into Decision | -1 LLM call/trigger |
| **P1** | ContextAgent owns all technical indicators | Eliminates defensive fallback chains |
| **P2** | Add early filter after ContextAgent (before Chart) | Skip Chart for SPARSE/STALE data |
| **P2** | Unify PositionManager + PortfolioCoordinator constraints | Single source of truth for portfolio rules |
| **P3** | Trigger quality_score as explicit Decision gate | Better conviction calibration |