"""
Short Perspective Agent implementation.
Acts as contrarian rebuttal by asking: "Would I short this stock right now?"
If yes (strong short case) -> Recommends rejecting the long trade.
"""

from typing import Any

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.llm.client import call_llm
from eiqora_v2.schemas.portfolio import ShortPerspectiveOutput
from eiqora_v2.schemas.state import SwingTradeState


class ShortPerspectiveAgent(BaseAgent[ShortPerspectiveOutput]):
    """
    Short Perspective Agent: Evaluates if this stock would make a good SHORT.
    
    Acts as contrarian rebuttal to long trades by looking for:
    - Sector rotation/weakness
    - Technical overbought conditions
    - Price at resistance
    - Underperformance vs benchmarks
    - Distribution patterns
    
    If short case is STRONG -> Recommends rejecting the long
    """
    
    name = "short_perspective"
    output_schema = ShortPerspectiveOutput

    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Run with deterministic scoring + LLM synthesis."""
        try:
            self.logger.info(f"Running {self.name} for {state.get('symbol')}")

            data = await self._gather_data(state)
            if data.get("error"):
                self.logger.warning(f"Data error: {data['error']}")
                return {
                    self.name: {"error": data["error"]},
                    "errors": state.get("errors", []) + [f"{self.name}: {data['error']}"],
                }

            scoring = self._score_short_case(data)
            prompt = self._build_prompt(state, data, scoring)

            result = await call_llm(
                prompt=prompt,
                schema=self.output_schema,
                system_prompt=self._get_system_prompt(),
            )

            merged = self._merge_scoring(result, scoring)
            output = ShortPerspectiveOutput(**merged)

            self.logger.info(f"{self.name} completed successfully")
            return self._build_state_update(state, output)

        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                self.name: {"error": str(e)},
                "errors": state.get("errors", []) + [f"{self.name}: {str(e)}"],
            }
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather data to evaluate short thesis."""
        from eiqora_v2.config.universe import get_sector_etf
        
        symbol = state["symbol"]
        sector_etf = get_sector_etf(symbol)
        market_data = state.get("market_data") or {}
        
        return {
            "symbol": symbol,
            "sector_etf": sector_etf,
            "topdown": state.get("topdown", {}),
            "context": state.get("context", {}),
            "chart": state.get("chart", {}),
            "fundamental": state.get("fundamental", {}),
            "facts": state.get("facts", {}),
            "daily_indicators": market_data.get("daily_indicators", {}),
            "trigger_detail": state.get("trigger_detail") or (state.get("trigger") or {}).get("detail"),
        }

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
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any], scoring: dict[str, Any] | None = None) -> str:
        """Build prompt to evaluate short case."""
        symbol = data["symbol"]
        scoring = scoring or {}
        metrics = scoring.get("metrics") or self._extract_metrics(data)
        scores = scoring.get("scores", {})

        sector_etf = metrics.get("sector_etf")
        sector_status = metrics.get("sector_status", "UNKNOWN")
        spy_trend = metrics.get("spy_trend", "UNKNOWN")
        regime = metrics.get("regime", "UNKNOWN")
        bias = metrics.get("bias", "NEUTRAL")

        current_price = metrics.get("current_price", 0.0)
        rsi14 = metrics.get("rsi14")
        mfi_14 = metrics.get("mfi_14")
        volume_z = metrics.get("volume_z")
        cmf_20 = metrics.get("cmf_20")
        ma20_state = metrics.get("ma20_state", "UNKNOWN")
        ma50_state = metrics.get("ma50_state", "UNKNOWN")
        resistance = metrics.get("resistance")
        support = metrics.get("support")
        intraday_vs_sector = metrics.get("intraday_vs_sector")
        intraday_vs_spy = metrics.get("intraday_vs_spy")
        rel_sector_20d = metrics.get("rel_sector_20d")

        sentiment = metrics.get("sentiment") or {}
        earnings = metrics.get("earnings") or {}
        insider = metrics.get("insider") or {}
        options = metrics.get("options") or {}

        event_sentiment = metrics.get("event_sentiment")
        event_materiality = metrics.get("event_materiality")
        event_summary = metrics.get("event_summary")

        short_score = scoring.get("short_score", 0.0)
        short_strength = scoring.get("short_case_strength", "NONE")
        reject_long = scoring.get("recommend_reject_long", False)
        auto_flags = scoring.get("red_flags", [])

        return f"""
You are a SHORT SELLER evaluating {symbol}.

Your job: Determine if you would SHORT this stock RIGHT NOW.

SECTOR CONTEXT:
- Sector ETF: {sector_etf}
- Sector Status: {sector_status}
- SPY Trend: {spy_trend}
- Market Regime: {regime}
- Market Bias: {bias}

TECHNICAL INDICATORS:
- Current Price: ${current_price:.2f}
- RSI (14d): {f"{rsi14:.1f}" if rsi14 is not None else "Unknown"}
- MFI (14d): {f"{mfi_14:.1f}" if mfi_14 is not None else "Unknown"}
- MA20 State: {ma20_state}
- MA50 State: {ma50_state}
- Volume Z-Score: {f"{volume_z:.2f}" if volume_z is not None else "Unknown"}
- CMF (20d): {f"{cmf_20:.3f}" if cmf_20 is not None else "Unknown"}

PRICE LEVELS:
- Resistance: {f"${resistance:.2f}" if resistance else "Unknown"}
- Support: {f"${support:.2f}" if support else "Unknown"}
- At Resistance?: {current_price >= resistance * 0.98 if resistance else "N/A"}

RELATIVE STRENGTH:
- Intraday vs Sector: {f"{intraday_vs_sector:+.2%}" if intraday_vs_sector is not None else "Unknown"}
- Intraday vs SPY: {f"{intraday_vs_spy:+.2%}" if intraday_vs_spy is not None else "Unknown"}
- 20d vs Sector: {f"{rel_sector_20d:+.2%}" if rel_sector_20d is not None else "Unknown"}

FUNDAMENTAL + SENTIMENT:
- News Sentiment: {sentiment.get("overall", "NEUTRAL")} (pos={sentiment.get("positive_count", 0)}, neg={sentiment.get("negative_count", 0)})
- News Count: {sentiment.get("news_count", 0)}
- Insider Net Value (90d): {insider.get("net_value", "N/A")}
- Insider Buys/Sells: {insider.get("buy_count", "N/A")} / {insider.get("sell_count", "N/A")}
- Earnings EPS Surprise %: {earnings.get("eps_surprise_pct", "N/A")}
- Revenue Growth YoY %: {earnings.get("revenue_growth_yoy", "N/A")}
- Guidance: {earnings.get("guidance", "N/A")}
- Options Sentiment: {options.get("sentiment", "N/A")}

EVENT CONTEXT:
- Event Summary: {event_summary or "None"}
- Event Sentiment: {event_sentiment or "N/A"}
- Event Materiality: {event_materiality or "N/A"}

DETERMINISTIC SCORING (precomputed - do NOT recalculate):
- Technical Score: {scores.get("technical", 0.0):.1f}
- Fundamental Score: {scores.get("fundamental", 0.0):.1f}
- Sentiment Score: {scores.get("sentiment", 0.0):.1f}
- Macro Score: {scores.get("macro", 0.0):.1f}
- Total Short Score: {short_score:.1f}/10
- Short Case Strength: {short_strength}
- Recommend Reject Long: {reject_long}
- Auto Red Flags: {auto_flags}

TASK:
1. Provide reasoning that is consistent with the precomputed scores.
2. Add any additional red flags not already listed (if any).
3. Keep reasoning concise (<= 400 characters).
"""
    
    def _get_system_prompt(self) -> str:
        return """You are a Short Perspective Agent.

Your role: Evaluate if this stock would make a good SHORT right now.
This is a contrarian check for long trades.

IMPORTANT:
- The scoring is already computed deterministically.
- Do NOT recompute or change the provided scores.
- Use the scores to justify your reasoning.

OUTPUT SCHEMA:
{
  "short_case_strength": "STRONG|MODERATE|WEAK|NONE",
  "short_score": 0.0-10.0,
  "reasoning": "Detailed explanation...",
  "red_flags": ["sector_weakness", "overbought", ...],
  "recommend_reject_long": true|false
}

Return ONLY valid JSON."""

    def _merge_scoring(self, result: ShortPerspectiveOutput, scoring: dict[str, Any]) -> dict[str, Any]:
        result_data = result.model_dump()
        red_flags = scoring.get("red_flags", [])
        llm_flags = result_data.get("red_flags", []) or []

        combined_flags: list[str] = []
        for flag in red_flags + llm_flags:
            if flag and flag not in combined_flags:
                combined_flags.append(flag)

        result_data["short_score"] = scoring.get("short_score", result_data.get("short_score", 0.0))
        result_data["short_case_strength"] = scoring.get(
            "short_case_strength", result_data.get("short_case_strength", "NONE")
        )
        result_data["recommend_reject_long"] = scoring.get(
            "recommend_reject_long", result_data.get("recommend_reject_long", False)
        )
        result_data["red_flags"] = combined_flags

        reasoning = result_data.get("reasoning") or ""
        if len(reasoning) > 500:
            result_data["reasoning"] = reasoning[:500]

        return result_data

    def _build_state_update(self, state: SwingTradeState, result: ShortPerspectiveOutput) -> dict[str, Any]:
        """Build state update with short perspective output."""
        symbol = state.get("symbol", "")
        self.logger.info(f"  🔻 Short Perspective: {result.short_case_strength} (score: {result.short_score:.1f}/10)")
        
        if result.red_flags:
            self.logger.info(f"     Red flags: {', '.join(result.red_flags)}")
        
        if result.recommend_reject_long:
            self.logger.warning(f"     ⚠️  RECOMMENDS REJECTING LONG: {result.reasoning[:100]}...")
        
        return {"short_perspective": result.model_dump()}
