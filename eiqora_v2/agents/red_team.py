"""
Red Team Agent implementation.
Challenges the trade idea to surface risks and missing data.
Also evaluates the contrarian short case (merged from ShortPerspectiveAgent).
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.llm.client import call_llm
from eiqora_v2.schemas.red_team import RedTeamOutput
from eiqora_v2.schemas.state import SwingTradeState


class RedTeamAgent(BaseAgent[RedTeamOutput]):
    """
    Red Team Agent: highlights contradictions, missing data, and fatal flaws.
    Also scores the contrarian short case deterministically and includes
    the result in the LLM prompt for unified risk + short evaluation.
    Runs before the Decision Agent.
    """

    name = "red_team"
    output_schema = RedTeamOutput

    # ------------------------------------------------------------------
    # Short-perspective scoring helpers (from ShortPerspectiveAgent)
    # ------------------------------------------------------------------

    def _coerce_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_metrics(self, data: dict[str, Any]) -> dict[str, Any]:
        context = data.get("context", {}) or {}
        chart = data.get("chart", {}) or {}
        topdown = data.get("topdown", {}) or {}
        fundamental = data.get("fundamental", {}) or {}
        facts = data.get("facts") or {}
        daily = data.get("daily_indicators", {}) or {}
        trigger_detail = data.get("trigger_detail") or {}
        sector_etf = data.get("sector_etf")

        current_price = self._coerce_float(context.get("current_price")) or 0.0
        rsi14 = self._coerce_float(daily.get("rsi14") or context.get("rsi14"))
        mfi_14 = self._coerce_float(daily.get("mfi_14") or daily.get("mfi14") or context.get("mfi_14"))
        volume_z = self._coerce_float(context.get("volume_z_20d"))
        cmf_20 = self._coerce_float(daily.get("cmf_20") or daily.get("cmf20") or daily.get("cmf") or context.get("cmf_20"))

        trend = context.get("trend", {}) or {}
        ma20_state = trend.get("ma20", "UNKNOWN")
        ma50_state = trend.get("ma50", "UNKNOWN")

        key_levels = chart.get("key_levels", {}) if isinstance(chart.get("key_levels"), dict) else {}
        resistance = key_levels.get("resistance") or chart.get("resistance_level")
        support = key_levels.get("support") or chart.get("support_level")

        intraday_vs_sector = trigger_detail.get("intraday_vs_sector") if isinstance(trigger_detail, dict) else None
        intraday_vs_spy = trigger_detail.get("intraday_vs_spy") if isinstance(trigger_detail, dict) else None
        rel_sector_20d = trigger_detail.get("rel_sector_20d") if isinstance(trigger_detail, dict) else None

        rel_strength = context.get("relative_strength", {}) if isinstance(context, dict) else {}
        if rel_sector_20d is None and isinstance(rel_strength, dict):
            vs_sector = rel_strength.get("vs_sector", {})
            if isinstance(vs_sector, dict):
                rel_sector_20d = vs_sector.get("rel_ret_20d")

        sector_rotation = topdown.get("sector_rotation", {}) if isinstance(topdown, dict) else {}
        sector_status = sector_rotation.get(sector_etf, "UNKNOWN")

        spy_trend = topdown.get("spy_trend", "UNKNOWN") if isinstance(topdown, dict) else "UNKNOWN"
        regime = topdown.get("regime", "UNKNOWN") if isinstance(topdown, dict) else "UNKNOWN"
        bias = topdown.get("bias", "NEUTRAL") if isinstance(topdown, dict) else "NEUTRAL"

        sentiment = fundamental.get("sentiment", {}) if isinstance(fundamental, dict) else {}
        earnings = fundamental.get("earnings") if isinstance(fundamental, dict) else None
        insider = fundamental.get("insider") if isinstance(fundamental, dict) else None
        options = context.get("options", {}) if isinstance(context, dict) else {}

        event_sentiment = facts.get("sentiment") if isinstance(facts, dict) else None
        event_materiality = facts.get("materiality") if isinstance(facts, dict) else None
        event_summary = facts.get("event_summary") if isinstance(facts, dict) else None

        return {
            "current_price": current_price,
            "rsi14": rsi14,
            "mfi_14": mfi_14,
            "volume_z": volume_z,
            "cmf_20": cmf_20,
            "ma20_state": ma20_state,
            "ma50_state": ma50_state,
            "resistance": resistance,
            "support": support,
            "intraday_vs_sector": intraday_vs_sector,
            "intraday_vs_spy": intraday_vs_spy,
            "rel_sector_20d": rel_sector_20d,
            "sector_etf": sector_etf,
            "sector_status": sector_status,
            "spy_trend": spy_trend,
            "regime": regime,
            "bias": bias,
            "sentiment": sentiment,
            "earnings": earnings,
            "insider": insider,
            "options": options,
            "event_sentiment": event_sentiment,
            "event_materiality": event_materiality,
            "event_summary": event_summary,
        }

    def _score_short_case(self, data: dict[str, Any]) -> dict[str, Any]:
        metrics = self._extract_metrics(data)
        red_flags: list[str] = []
        scores = {"technical": 0.0, "fundamental": 0.0, "sentiment": 0.0, "macro": 0.0}

        def add_flag(flag: str) -> None:
            if flag not in red_flags:
                red_flags.append(flag)

        overbought = False
        rsi14 = metrics.get("rsi14")
        mfi_14 = metrics.get("mfi_14")
        if rsi14 is not None and rsi14 >= 70:
            overbought = True
        if mfi_14 is not None and mfi_14 >= 80:
            overbought = True

        if overbought:
            scores["technical"] += 2.0
            add_flag("overbought")

        resistance = metrics.get("resistance")
        current_price = metrics.get("current_price") or 0.0
        if resistance is not None and current_price >= float(resistance) * 0.98:
            scores["technical"] += 2.0
            add_flag("at_resistance")

        volume_z = metrics.get("volume_z")
        cmf_20 = metrics.get("cmf_20")
        if volume_z is not None and volume_z >= 1.5 and cmf_20 is not None and cmf_20 <= -0.1:
            scores["technical"] += 1.5
            add_flag("distribution")

        intraday_vs_sector = metrics.get("intraday_vs_sector")
        intraday_vs_spy = metrics.get("intraday_vs_spy")
        if intraday_vs_sector is not None and intraday_vs_sector <= -0.01:
            scores["technical"] += 1.5
            add_flag("intraday_underperform_sector")
        elif intraday_vs_spy is not None and intraday_vs_spy <= -0.01:
            scores["technical"] += 1.5
            add_flag("intraday_underperform_spy")

        rel_sector_20d = metrics.get("rel_sector_20d")
        if rel_sector_20d is not None and rel_sector_20d <= -0.03:
            scores["technical"] += 1.0
            add_flag("weak_relative_strength_20d")

        if metrics.get("ma20_state") == "BELOW" and metrics.get("ma50_state") == "BELOW":
            scores["technical"] += 1.0
            add_flag("trend_below_ma20_ma50")

        if metrics.get("sector_status") == "LAGGING":
            scores["macro"] += 1.0
            add_flag("sector_lagging")

        if metrics.get("bias") == "BEARISH" or metrics.get("regime") == "RISK_OFF" or metrics.get("spy_trend") == "DOWN":
            scores["macro"] += 1.0
            add_flag("bearish_macro")

        insider = metrics.get("insider")
        if isinstance(insider, dict) and insider.get("available"):
            net_value = self._coerce_float(insider.get("net_value")) or 0.0
            sell_count = int(insider.get("sell_count") or 0)
            buy_count = int(insider.get("buy_count") or 0)

            if net_value <= -2_000_000:
                scores["fundamental"] += 1.5
                add_flag("heavy_insider_selling")
            elif net_value <= -500_000:
                scores["fundamental"] += 1.0
                add_flag("insider_selling")

            if sell_count >= 3 and sell_count > buy_count:
                scores["fundamental"] += 0.5
                add_flag("insider_sell_cluster")

        earnings = metrics.get("earnings")
        if isinstance(earnings, dict):
            eps_surprise = self._coerce_float(earnings.get("eps_surprise_pct"))
            revenue_growth = self._coerce_float(earnings.get("revenue_growth_yoy"))
            guidance = earnings.get("guidance")

            if eps_surprise is not None and eps_surprise <= -5:
                scores["fundamental"] += 1.0
                add_flag("earnings_miss")
            if revenue_growth is not None and revenue_growth < 0:
                scores["fundamental"] += 0.5
                add_flag("revenue_decline")
            if guidance == "LOWERED":
                scores["fundamental"] += 0.5
                add_flag("guidance_lowered")

        sentiment = metrics.get("sentiment")
        if isinstance(sentiment, dict):
            overall = sentiment.get("overall")
            pos_count = sentiment.get("positive_count", 0)
            neg_count = sentiment.get("negative_count", 0)

            if overall == "NEGATIVE":
                scores["sentiment"] += 1.5
                add_flag("negative_news_sentiment")
            elif overall == "MIXED":
                scores["sentiment"] += 0.5
                add_flag("mixed_news_sentiment")

            if isinstance(pos_count, int) and isinstance(neg_count, int) and neg_count >= pos_count + 2:
                scores["sentiment"] += 0.5
                add_flag("news_negative_skew")

        options = metrics.get("options")
        if isinstance(options, dict) and options.get("sentiment") == "BEARISH":
            scores["sentiment"] += 0.5
            add_flag("options_bearish")

        if metrics.get("event_sentiment") in ("NEGATIVE", "MIXED") and metrics.get("event_materiality") in ("HIGH", "MEDIUM"):
            scores["sentiment"] += 1.0
            add_flag("negative_event_catalyst")

        total = sum(scores.values())
        total = max(0.0, min(10.0, total))

        if total >= 8.0:
            strength = "STRONG"
        elif total >= 5.0:
            strength = "MODERATE"
        elif total >= 3.0:
            strength = "WEAK"
        else:
            strength = "NONE"

        return {
            "short_score": total,
            "short_case_strength": strength,
            "recommend_reject_long": strength == "STRONG",
            "red_flags": red_flags,
            "scores": scores,
            "metrics": metrics,
        }

    # ------------------------------------------------------------------
    # Core agent methods
    # ------------------------------------------------------------------

    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather context for red team review + short perspective scoring."""
        from eiqora_v2.config.universe import get_sector_etf
        from eiqora_v2.tools.event_risk import check_upcoming_events, format_event_warnings

        symbol = state["symbol"]
        asof_time = state["asof_time"]
        market_data = state.get("market_data") or {}
        sector_etf = get_sector_etf(symbol)

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
            # Additional data needed for short perspective scoring
            "facts": state.get("facts", {}),
            "daily_indicators": market_data.get("daily_indicators", {}),
            "sector_etf": sector_etf,
        }

    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Run red team analysis with integrated short perspective scoring."""
        try:
            self.logger.info(f"Running {self.name} for {state.get('symbol')}")

            data = await self._gather_data(state)

            # Pre-compute short case scoring deterministically
            scoring = self._score_short_case(data)

            prompt = self._build_prompt(state, data, scoring)

            result = await call_llm(
                prompt=prompt,
                schema=self.output_schema,
                system_prompt=self._get_system_prompt(),
            )

            # Merge deterministic short scores into LLM output
            merged = self._merge_short_scoring(result, scoring)
            output = RedTeamOutput(**merged)

            self.logger.info(f"{self.name} completed successfully")
            return self._build_state_update(state, output)

        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                self.name: {"error": str(e)},
                "errors": state.get("errors", []) + [f"{self.name}: {str(e)}"],
            }

    def _merge_short_scoring(self, result: RedTeamOutput, scoring: dict[str, Any]) -> dict[str, Any]:
        """Merge deterministic short scores into LLM result."""
        result_data = result.model_dump()

        result_data["short_score"] = scoring.get("short_score", 0.0)
        result_data["short_case_strength"] = scoring.get("short_case_strength", "NONE")
        result_data["recommend_reject_long"] = scoring.get("recommend_reject_long", False)

        # Combine LLM key_risks with short red flags (dedup)
        short_flags = scoring.get("red_flags", [])
        result_data["short_red_flags"] = short_flags

        return result_data

    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any], scoring: dict[str, Any] | None = None) -> str:
        """Build prompt for red team analysis + short perspective."""
        symbol = state["symbol"]
        scoring = scoring or {}

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
decision=ALLOW, critical=false, key_risks=[], missing_data=[], summary="No idea to challenge.",
short_case_strength=NONE, short_score=0.0, recommend_reject_long=false, short_red_flags=[]
"""

        sentiment = fundamental.get("sentiment", {}) if isinstance(fundamental, dict) else {}
        data_status = fundamental.get("data_status", {}) if isinstance(fundamental, dict) else {}
        baseline_risks = profile.get("risks", [])
        upcoming_events = data.get("upcoming_events", {})
        event_warning = data.get("event_warning", "")

        # Short perspective scoring context
        short_scores = scoring.get("scores", {})
        short_score = scoring.get("short_score", 0.0)
        short_strength = scoring.get("short_case_strength", "NONE")
        reject_long = scoring.get("recommend_reject_long", False)
        auto_flags = scoring.get("red_flags", [])

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

---

SHORT PERSPECTIVE EVALUATION (precomputed deterministic scores):
- Technical Score: {short_scores.get("technical", 0.0):.1f}
- Fundamental Score: {short_scores.get("fundamental", 0.0):.1f}
- Sentiment Score: {short_scores.get("sentiment", 0.0):.1f}
- Macro Score: {short_scores.get("macro", 0.0):.1f}
- Total Short Score: {short_score:.1f}/10
- Short Case Strength: {short_strength}
- Recommend Reject Long: {reject_long}
- Auto Red Flags: {auto_flags}

Consider the short perspective scores when assessing risk. A STRONG short case
(score >= 8) is a critical risk factor. MODERATE (5-7) is a caution signal.
Factor this into your decision and key_risks.
"""

    def _get_system_prompt(self) -> str:
        return """You are a Red Team Agent that stress-tests trade ideas.

OBJECTIVE:
Identify the most important contradictions, risks, or missing data.
Also consider the precomputed short perspective scores in your assessment.
Do NOT make a trade recommendation. Only assess risk posture.

GUIDANCE:
- BLOCK only for critical issues that invalidate the setup.
- CAUTION for meaningful but non-fatal issues.
- ALLOW if no material issues are found.
- Do not invent missing data or metrics.
- The short perspective scores are deterministic and precomputed. Do NOT recalculate them.
  Factor the short case strength into your risk assessment.

OUTPUT SCHEMA:
{
  "decision": "BLOCK|CAUTION|ALLOW",
  "critical": true|false,
  "key_risks": ["..."],
  "missing_data": ["..."],
  "summary": "...",
  "short_case_strength": "STRONG|MODERATE|WEAK|NONE",
  "short_score": 0.0,
  "recommend_reject_long": true|false,
  "short_red_flags": ["..."]
}

Return ONLY valid JSON."""

    def _build_state_update(self, state: SwingTradeState, result: RedTeamOutput) -> dict[str, Any]:
        """Build state update with red-team + short perspective output."""
        self.logger.info(f"  Red Team: {result.decision} | Short: {result.short_case_strength} (score: {result.short_score:.1f}/10)")

        if result.short_red_flags:
            self.logger.info(f"     Short red flags: {', '.join(result.short_red_flags)}")

        if result.recommend_reject_long:
            self.logger.warning(f"     RECOMMENDS REJECTING LONG (short score: {result.short_score:.1f})")

        return {"red_team": result.model_dump()}
