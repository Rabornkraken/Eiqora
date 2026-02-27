"""
Exit Policy Agent implementation.
Defines exit brackets (TP/SL/time stop) for trade ideas.
"""

from typing import Any, Literal

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.llm.client import call_llm
from eiqora_v2.schemas.ideas import ExitBracket, ExitPolicyOutput
from eiqora_v2.schemas.state import SwingTradeState
from pydantic import BaseModel, Field


class ExitBracketDraft(BaseModel):
    """Loose bracket model for LLM output parsing."""

    tp_mult: float | None = None
    sl_mult: float | None = None
    time_stop_days: int | None = None
    tp_level: float | None = None
    sl_level: float | None = None
    tp_rationale: str | None = None
    sl_rationale: str | None = None


class ExitPolicyDraft(BaseModel):
    """Loose exit policy model for LLM output parsing."""

    idea_id: str | None = None
    bracket: ExitBracketDraft | None = None
    vol_basis_type: Literal["RV20", "ATR14_PROXY"] | None = None
    trailing_stop: Any | None = None
    scale_out_levels: list[Any] | None = None
    notes: str | None = None


class ExitPolicyAgent(BaseAgent[ExitPolicyOutput]):
    """
    Exit Policy Agent: defines exit strategy for each trade idea.
    
    Outputs:
    - Take profit level (as volatility multiple)
    - Stop loss level (as volatility multiple)
    - Time stop (trading days)
    - Optional trailing stop
    - Optional scale-out levels
    """
    
    name = "exit_policy"
    output_schema = ExitPolicyOutput

    async def run(self, state: SwingTradeState) -> dict[str, Any]:
        """Run exit policy with deterministic normalization."""
        try:
            self.logger.info(f"Running {self.name} for {state.get('symbol')}")

            data = await self._gather_data(state)
            if data.get("error"):
                self.logger.warning(f"Data error: {data['error']}")
                return {
                    self.name: {"error": data["error"]},
                    "errors": state.get("errors", []) + [f"{self.name}: {data['error']}"],
                }

            prompt = self._build_prompt(state, data)
            draft = await call_llm(
                prompt=prompt,
                schema=ExitPolicyDraft,
                system_prompt=self._get_system_prompt(),
            )

            normalized = self._normalize_output(state, data, draft)
            self.logger.info(f"{self.name} completed successfully")
            return self._build_state_update(state, normalized)

        except Exception as e:
            self.logger.error(f"{self.name} failed: {e}")
            return {
                self.name: {"error": str(e)},
                "errors": state.get("errors", []) + [f"{self.name}: {str(e)}"],
            }
    
    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        """Gather context and idea data for exit policy."""
        context = state.get("context", {})
        market_data = state.get("market_data", {})
        price_levels = market_data.get("price_levels", {}) if isinstance(market_data, dict) else {}
        
        return {
            "ideas": state.get("ideas", {}),
            "context": context,
            "chart": state.get("chart", {}),
            "price_levels": price_levels,
        }

    def _coerce_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _coerce_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _clamp(self, value: float, min_value: float, max_value: float) -> float:
        return max(min_value, min(max_value, value))

    def _valid_mult(self, value: Any, min_value: float, max_value: float) -> float | None:
        num = self._coerce_float(value)
        if num is None:
            return None
        if num < min_value or num > max_value:
            return None
        return num

    def _default_sl_mult(self, setup_type: str, rv20: float | None, conviction: str) -> float:
        """Swing trading SL multipliers (structure-first, wider than intraday)."""
        setup = (setup_type or "").upper()
        if "BREAKOUT" in setup:
            base = 2.0  # Below breakout, avoid noise
        elif "PULLBACK" in setup:
            base = 2.5  # Below swing low
        elif "REVERSAL" in setup:
            base = 3.0  # Reversals need room
        elif "EARNINGS" in setup or "EVENT" in setup:
            base = 3.5  # Events need more room for whipsaws
        else:
            base = 2.5  # Default swing trading stop

        # Volatility adjustments
        if rv20 is not None:
            if rv20 > 0.04:
                base *= 1.1  # Wider in high vol
            elif rv20 < 0.015:
                base *= 0.95  # Slightly tighter in low vol

        # Conviction adjustments (minimal impact)
        if conviction == "HIGH":
            base *= 1.0  # No change for high conviction
        elif conviction == "LOW":
            base *= 0.95  # Slightly tighter for low conviction

        return self._clamp(base, 1.5, 5.0)  # Range: 1.5-5.0 ATR

    def _default_tp_mult(self, setup_type: str) -> float:
        """Swing trading TP multipliers (realistic targets)."""
        setup = (setup_type or "").upper()
        if "BREAKOUT" in setup:
            base = 4.0  # Target ~1:2 R:R from 2.0x SL
        elif "REVERSAL" in setup:
            base = 3.5  # Target ~1:1.2-1.3 R:R from 3.0x SL
        else:
            base = 3.75  # Target ~1:1.5 R:R from 2.5x SL
        return self._clamp(base, 2.5, 8.0)  # Range: 2.5-8.0 ATR

    def _normalize_output(
        self,
        state: SwingTradeState,
        data: dict[str, Any],
        draft: ExitPolicyDraft,
    ) -> ExitPolicyOutput:
        ideas = data.get("ideas", {}) or {}
        context = data.get("context", {}) or {}
        chart = data.get("chart", {}) or {}

        ideas_list = ideas.get("ideas", []) or []
        primary_idea_id = ideas.get("primary_idea_id")
        primary_idea = (
            next((i for i in ideas_list if i.get("idea_id") == primary_idea_id), None)
            if ideas_list
            else None
        )
        if primary_idea is None and ideas_list:
            primary_idea = ideas_list[0]

        direction = (primary_idea or {}).get("direction", "LONG")
        setup_type = (primary_idea or {}).get("setup_type") or chart.get("setup_type", "UNKNOWN")
        conviction = (primary_idea or {}).get("conviction", "MEDIUM")
        time_horizon = (primary_idea or {}).get("time_horizon", "SWING_MEDIUM")

        vol_basis = context.get("vol_basis", {}) if isinstance(context, dict) else {}
        vol_basis_type = vol_basis.get("type") if isinstance(vol_basis, dict) else None
        if vol_basis_type not in ("RV20", "ATR14_PROXY"):
            vol_basis_type = "RV20"

        current_price = self._coerce_float(context.get("current_price")) or 0.0
        atr14 = self._coerce_float(context.get("atr14"))
        rv20 = self._coerce_float(vol_basis.get("value") if isinstance(vol_basis, dict) else None)

        if atr14 is None or atr14 <= 0:
            if rv20 is not None and current_price > 0:
                atr14 = current_price * rv20
            elif current_price > 0:
                atr14 = current_price * 0.02
        if rv20 is None and atr14 and current_price > 0:
            rv20 = atr14 / current_price
        if atr14 and current_price > 0:
            atr_pct = atr14 / current_price
            if atr_pct < 0.005:
                fallback_pct = rv20 if rv20 and rv20 > 0 else 0.02
                atr14 = current_price * fallback_pct

        default_sl_mult = self._default_sl_mult(setup_type, rv20, conviction)
        default_tp_mult = self._default_tp_mult(setup_type)

        bracket = draft.bracket or ExitBracketDraft()
        sl_mult = self._valid_mult(bracket.sl_mult, 0.5, 5.0)
        tp_mult = self._valid_mult(bracket.tp_mult, 1.0, 10.0)
        time_stop_days = self._coerce_int(bracket.time_stop_days)

        key_levels = chart.get("key_levels", {}) if isinstance(chart, dict) else {}
        key_support = self._coerce_float(key_levels.get("support") if isinstance(key_levels, dict) else None)
        key_resistance = self._coerce_float(key_levels.get("resistance") if isinstance(key_levels, dict) else None)
        invalidation = chart.get("invalidation", {}) if isinstance(chart, dict) else {}
        invalidation_level = self._coerce_float(invalidation.get("level") if isinstance(invalidation, dict) else None)
        price_levels = data.get("price_levels", {}) or {}
        swing_low_20d = self._coerce_float(price_levels.get("low_20d"))
        swing_high_20d = self._coerce_float(price_levels.get("high_20d"))
        swing_low_60d = self._coerce_float(price_levels.get("low_60d"))
        swing_high_60d = self._coerce_float(price_levels.get("high_60d"))

        sl_level = self._coerce_float(bracket.sl_level)
        tp_level = self._coerce_float(bracket.tp_level)
        sl_rationale = bracket.sl_rationale
        tp_rationale = bracket.tp_rationale

        if sl_level is None:
            if invalidation_level is not None:
                sl_level = invalidation_level
                sl_rationale = sl_rationale or "Below invalidation level"
            elif direction == "LONG":
                if swing_low_20d is not None:
                    sl_level = swing_low_20d * 0.99
                    sl_rationale = sl_rationale or "Below 20d swing low"
                elif key_support is not None:
                    sl_level = key_support
                    sl_rationale = sl_rationale or "Below support"
                elif swing_low_60d is not None:
                    sl_level = swing_low_60d * 0.99
                    sl_rationale = sl_rationale or "Below 60d swing low"
            else:
                if swing_high_20d is not None:
                    sl_level = swing_high_20d * 1.01
                    sl_rationale = sl_rationale or "Above 20d swing high"
                elif key_resistance is not None:
                    sl_level = key_resistance
                    sl_rationale = sl_rationale or "Above resistance"
                elif swing_high_60d is not None:
                    sl_level = swing_high_60d * 1.01
                    sl_rationale = sl_rationale or "Above 60d swing high"

        if sl_mult is None:
            if sl_level is not None and atr14 and atr14 > 0:
                sl_mult = abs(current_price - sl_level) / atr14
            if sl_mult is None:
                sl_mult = default_sl_mult
        sl_mult = self._clamp(sl_mult, 0.5, 5.0)

        # Ensure stop is on the correct side of price; otherwise recompute from ATR.
        if sl_level is not None:
            if direction == "LONG" and sl_level >= current_price:
                sl_level = None
            elif direction == "SHORT" and sl_level <= current_price:
                sl_level = None

        if sl_level is None and atr14 and atr14 > 0 and current_price > 0:
            if direction == "SHORT":
                sl_level = current_price + (atr14 * sl_mult)
            else:
                sl_level = current_price - (atr14 * sl_mult)
            sl_rationale = sl_rationale or "ATR-based catastrophic stop"
        
        # Enforce minimum stop distance (>= 1.5x ATR and >= 3%).
        MIN_STOP_PCT = 0.03
        MIN_ATR_MULT = 1.5
        if sl_level is not None and current_price > 0:
            stop_distance = abs(sl_level - current_price)
            min_distance = current_price * MIN_STOP_PCT
            if atr14 and atr14 > 0:
                min_distance = max(min_distance, atr14 * MIN_ATR_MULT)

            if stop_distance < min_distance:
                if direction == "SHORT":
                    sl_level = current_price + min_distance
                else:
                    sl_level = current_price - min_distance
                sl_rationale = "Widened to minimum swing distance"
                if atr14 and atr14 > 0:
                    sl_mult = min_distance / atr14

        if tp_level is None:
            if direction == "LONG" and key_resistance is not None:
                tp_level = key_resistance
                tp_rationale = tp_rationale or "At resistance"
            elif direction == "SHORT" and key_support is not None:
                tp_level = key_support
                tp_rationale = tp_rationale or "At support"

        if tp_mult is None:
            if tp_level is not None and atr14 and atr14 > 0:
                tp_mult = abs(tp_level - current_price) / atr14
            if tp_mult is None:
                tp_mult = default_tp_mult
        tp_mult = self._clamp(tp_mult, 1.0, 10.0)

        # Ensure take-profit is on the correct side of price; otherwise recompute from ATR.
        if tp_level is not None:
            if direction == "LONG" and tp_level <= current_price:
                tp_level = None
            elif direction == "SHORT" and tp_level >= current_price:
                tp_level = None

        # Enforce minimum reward vs stop width (>=1:1.5).
        min_tp_mult = max(default_tp_mult, sl_mult * 1.5)
        if tp_mult < min_tp_mult:
            tp_mult = min_tp_mult
            tp_level = None
            tp_rationale = "Adjusted to meet minimum reward/risk"

        if tp_level is None and atr14 and atr14 > 0 and current_price > 0:
            if direction == "SHORT":
                tp_level = current_price - (atr14 * tp_mult)
            else:
                tp_level = current_price + (atr14 * tp_mult)
            tp_rationale = tp_rationale or "ATR-based reference target"

        if tp_rationale is None:
            tp_rationale = "Structure target" if tp_level is not None else "ATR multiple reference"
        if sl_rationale is None:
            sl_rationale = "Catastrophic stop"
        if tp_rationale and len(tp_rationale) > 120:
            tp_rationale = tp_rationale[:120]
        if sl_rationale and len(sl_rationale) > 120:
            sl_rationale = sl_rationale[:120]

        default_time_stop = {"SWING_SHORT": 8, "SWING_MEDIUM": 20, "SWING_LONG": 40}.get(
            time_horizon, 20
        )
        if time_stop_days is None or time_stop_days < 5 or time_stop_days > 60:
            time_stop_days = default_time_stop

        raw_levels = draft.scale_out_levels
        if not isinstance(raw_levels, list):
            raw_levels = []
        scale_out_levels = []
        for level in raw_levels[:2]:
            val = self._coerce_float(level)
            if val is None:
                continue
            if 0 < val <= 10:
                scale_out_levels.append(val)

        notes = draft.notes or "Decision-based exits via monitoring"
        if len(notes) > 200:
            notes = notes[:200]

        bracket_out = ExitBracket(
            tp_mult=float(tp_mult),
            sl_mult=float(sl_mult),
            time_stop_days=int(time_stop_days),
            tp_level=tp_level,
            sl_level=sl_level,
            tp_rationale=tp_rationale,
            sl_rationale=sl_rationale,
        )

        idea_id = (primary_idea or {}).get("idea_id") or draft.idea_id or "none"

        return ExitPolicyOutput(
            idea_id=idea_id,
            bracket=bracket_out,
            vol_basis_type=vol_basis_type,
            trailing_stop=None,
            scale_out_levels=scale_out_levels,
            notes=notes,
        )
    
    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        """Build prompt for exit policy generation."""
        symbol = state["symbol"]
        
        ideas = data.get("ideas", {})
        context = data.get("context", {})
        chart = data.get("chart", {})
        price_levels = data.get("price_levels", {})
        
        primary_idea_id = ideas.get("primary_idea_id")
        ideas_list = ideas.get("ideas", [])
        
        if not primary_idea_id or not ideas_list:
            return f"""
No trade ideas to create exit policy for {symbol}.
Return a default exit policy with idea_id="none".
"""
        
        # Find primary idea
        primary_idea = next((i for i in ideas_list if i.get("idea_id") == primary_idea_id), ideas_list[0])
        
        # Extract chart structure for adaptive stops
        key_levels = chart.get("key_levels", {})
        key_support = key_levels.get("support") if isinstance(key_levels, dict) else None
        key_resistance = key_levels.get("resistance") if isinstance(key_levels, dict) else None
        invalidation = chart.get("invalidation", {})
        invalidation_level = invalidation.get("level") if isinstance(invalidation, dict) else None
        
        # MA levels as potential support/resistance
        ma50 = context.get("ma50")
        ma200 = context.get("ma200")
        
        # Volatility for regime detection
        vol_basis = context.get("vol_basis", {})
        rv20 = vol_basis.get("value")
        try:
            rv20 = float(rv20) if rv20 is not None else 0.02
        except (TypeError, ValueError):
            rv20 = 0.02

        vix = context.get("vix")
        try:
            vix = float(vix) if vix is not None else 15.0
        except (TypeError, ValueError):
            vix = 15.0
        
        # Setup characteristics
        setup_type = primary_idea.get("setup_type", "UNKNOWN")
        conviction = primary_idea.get("conviction", "MEDIUM")
        try:
            current_price = float(context.get("current_price") or 0.0)
        except (TypeError, ValueError):
            current_price = 0.0
        
        # Volatility regime
        vol_regime = "HIGH" if rv20 > 0.035 else "LOW" if rv20 < 0.015 else "NORMAL"
        
        key_support_text = f"${key_support:.2f}" if key_support is not None else "None"
        key_resistance_text = f"${key_resistance:.2f}" if key_resistance is not None else "None"
        invalidation_text = f"${invalidation_level:.2f}" if invalidation_level is not None else "None"
        ma50_text = f"${ma50:.2f}"if ma50 is not None else "None"
        ma200_text = f"${ma200:.2f}" if ma200 is not None else "None"
        price_text = f"${current_price:.2f}" if current_price > 0 else "N/A"
        
        # Swing lows/highs for structure-based stops
        swing_low_20d = price_levels.get("low_20d")
        swing_high_20d = price_levels.get("high_20d")
        swing_low_60d = price_levels.get("low_60d")
        swing_low_20d_text = f"${swing_low_20d:.2f}" if swing_low_20d else "None"
        swing_high_20d_text = f"${swing_high_20d:.2f}" if swing_high_20d else "None"
        swing_low_60d_text = f"${swing_low_60d:.2f}" if swing_low_60d else "None"

        return f"""
Define ADAPTIVE exit policy for {symbol}.

PRIORITY: Use chart structure for stops when available!

IDEA:
- ID: {primary_idea.get('idea_id')}
- Direction: {primary_idea.get('direction')}
- Time Horizon: {primary_idea.get('time_horizon')}
- Conviction: {conviction}
- Setup Type: {setup_type}

CHART STRUCTURE (PRIMARY - use for stop placement):
- Current Price: {price_text}
- **20d Swing Low: {swing_low_20d_text}** ← Use this for LONG stops!
- **20d Swing High: {swing_high_20d_text}** ← Use this for SHORT stops!
- 60d Swing Low: {swing_low_60d_text}
- Key Support: {key_support_text}
- Key Resistance: {key_resistance_text}
- Invalidation Level: {invalidation_text}
- MA50: {ma50_text}
- MA200: {ma200_text}

VOLATILITY CONTEXT:
- RV20: {rv20:.4f} ({rv20*100:.1f}% daily)
- VIX: {vix:.1f}
- Regime: {vol_regime}

STOP LOSS PLACEMENT (CRITICAL - Professional Swing Trading Method):

**STEP 1: Identify Structure Level**
For LONG trades, place stop BELOW:
1. **20d Swing Low** (most common - use if available)
2. Key Support level (if stronger than swing low)
3. MA50/MA200 (if no swing low visible)

For SHORT trades, place stop ABOVE:
1. **20d Swing High** (most common)
2. Key Resistance level
3. MA50/MA200

**STEP 2: Validate Distance**
- Stop must be AT LEAST 1.5x ATR away from entry
- Stop must be AT LEAST 3% away from entry
- If structure stop is closer, widen to meet BOTH minimums

**STEP 3: Provide Both sl_level AND sl_mult**
- sl_level: Exact $ price (from structure)
- sl_mult: Calculate as distance/ATR for reference
- sl_rationale: "Below $X.XX 20d swing low" or "Below $X.XX support"

**ONLY use pure ATR if**:
- No swing low/high available
- No clear support/resistance
- Then use setup-specific multipliers (1.5-2.5x ATR)

EXAMPLE OUTPUT WITH CHART LEVELS:
{{
  "bracket": {{
    "sl_level": 269.50,
    "sl_mult": 2.8,
    "tp_level": 285.00,
    "tp_mult": 3.6,
    "sl_rationale": "Below $270 support",
    "tp_rationale": "First resistance"
  }}
}}

EXAMPLE OUTPUT WITHOUT LEVELS (ATR-based):
{{
  "bracket": {{
    "sl_level": null,
    "sl_mult": 2.5,
    "tp_level": null,
    "tp_mult": 3.75,
    "sl_rationale": "2.5x ATR for {setup_type}",
    "tp_rationale": "3.75x ATR target"
  }}
}}

CONSTRAINTS:
- sl_rationale <= 120 chars
- tp_rationale <= 120 chars
- notes <= 200 chars
"""
    
    def _get_system_prompt(self) -> str:
        return """You are an Exit Policy Agent that defines ADAPTIVE exit strategies for SWING TRADING.

**SWING TRADING FOCUS: Structure-first stops with minimum distance safeguards.**

PHILOSOPHY:
- Use structure (swing low/high, support/resistance) for stop placement.
- Validate against volatility to avoid noise.
- Typical swing stops are 3-5% unless structure demands wider.
- Targets should maintain >=1:1.5 reward/risk.

DECISION PROCESS:
1. Place SL at structure level (swing low/high, support/resistance).
2. Ensure stop is >= 1.5x ATR and >= 3% from entry.
3. Set TP to meet >=1:1.5 reward/risk (structure if available).

SETUP-SPECIFIC SWING STOPS:

**BREAKOUT** (clean level break):
- SL: below breakout level or swing low, widened to min distance
- TP: structure or ATR target (>=1:1.5 R:R)

**PULLBACK** (buy the dip):
- SL: below swing low/support, widened to min distance
- TP: structure or ATR target

**REVERSAL** (V-bottom, climax):
- SL: wider, below swing low, widened to min distance
- TP: structure or ATR target

**EARNINGS/EVENT**:
- SL: wider, structure-first, widened to min distance
- TP: structure or ATR target

**DEFAULT** (no specific setup):
- SL: structure-first, widened to min distance
- TP: structure or ATR target

TIME STOPS:
- SWING_SHORT: 8 days (mean reversion edge dies after day 8)
- SWING_MEDIUM: 20 days (~4 weeks, breakout continuation window)
- SWING_LONG: 40 days (~8 weeks, full stage 2 breakout development)

OUTPUT SCHEMA:
{
  "idea_id": "<idea ID>",
  "bracket": {
    "tp_mult": 3.75,
    "tp_level": <chart level> or null,
    "sl_mult": 2.5,
    "sl_level": <chart level> or null,
    "time_stop_days": 20,
    "sl_rationale": "Below 20d swing low",
    "tp_rationale": "Structure target"
  },
  "vol_basis_type": "RV20",
  "trailing_stop": null,
  "scale_out_levels": [],
  "notes": "Swing trade with structure-based stop"
}

HARD LIMITS:
- sl_mult: 1.5 to 5.0
- tp_mult: 2.5 to 8.0
- sl_rationale <= 120 chars
- tp_rationale <= 120 chars
- notes <= 200 chars

**Return ONLY valid JSON.**"""
    
    def _build_state_update(self, state: SwingTradeState, result: ExitPolicyOutput) -> dict[str, Any]:
        """Build state update with exit policy."""
        # Store as part of rules
        existing_rules = state.get("rules", []) or []
        exit_policy = result.model_dump()
        existing_rules.append({"exit_policy": exit_policy})
        return {"rules": existing_rules, "exit_policy": exit_policy}
