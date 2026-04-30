"""
Time-Stop Decision Agent.

Fired the moment a position reaches its ``time_stop_date``. Replaces the
previous unconditional auto-exit with a contextual, LLM-mediated choice:

- **EXIT**         — close the position now (the historical default)
- **TRAIL_TIGHT**  — keep the position open but raise the stop loss to
                     a tighter level proposed by the LLM, and clear the
                     time stop so it doesn't re-fire
- **EXTEND_HOLD**  — leave SL/TP alone but push the time stop forward by
                     ``extension_days`` more calendar days

Motivation: a 19-trade retrospective on 2026-05-01 found that holding to
TP/SL would have been worse on average (-1.91% vs realized +2.57%), but
the asymmetry was striking — the time stop saved us ~30% combined on
META/TMUS/PLTR (broken momentum) while costing us ~$8-9% per trade on
AMAT/MU/GOOGL/CVX (slow winners that did reach TP after the deadline).
A static numeric threshold ("only exit if PnL < +3%") would be brittle
across regimes; a focused LLM call with full live context picks the
right action per trade without hardcoded magic numbers.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from eiqora_v2.agents.base import BaseAgent
from eiqora_v2.schemas.state import SwingTradeState
from eiqora_v2.tools.prices import get_indicators


class TimeStopDecisionOutput(BaseModel):
    """Output from the time-stop decision agent."""

    action: Literal["EXIT", "TRAIL_TIGHT", "EXTEND_HOLD"] = Field(
        description=(
            "EXIT closes the position now. TRAIL_TIGHT keeps it open with a "
            "tighter stop loss. EXTEND_HOLD pushes the time stop forward."
        )
    )
    new_stop_loss: float | None = Field(
        default=None,
        description=(
            "Required when action=TRAIL_TIGHT. The new stop loss price. For "
            "LONG positions, MUST be above the current stop loss but below "
            "the current price. For SHORT, the inverse."
        ),
    )
    extension_days: int | None = Field(
        default=None,
        ge=1,
        le=30,
        description=(
            "Required when action=EXTEND_HOLD. Number of additional calendar "
            "days to extend the time stop. Capped at 30."
        ),
    )
    reasoning: str = Field(description="One short paragraph justifying the choice.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this decision, 0-1."
    )


class TimeStopDecisionAgent(BaseAgent[TimeStopDecisionOutput]):
    """LLM agent fired at a position's time-stop deadline."""

    name = "time_stop_decision"
    output_schema = TimeStopDecisionOutput

    async def _gather_data(self, state: SwingTradeState) -> dict[str, Any]:
        symbol = state.get("symbol")
        position = state.get("position", {}) or {}

        indicators: dict[str, Any] = {}
        try:
            asof = state.get("asof_time")
            indicators = await get_indicators(symbol, 30, asof)
            if indicators.get("error"):
                indicators = {"error": indicators.get("error")}
        except Exception as exc:  # noqa: BLE001 — never fail the deadline path
            indicators = {"error": str(exc)}

        return {
            "position": position,
            "indicators": indicators,
            "profile": state.get("profile", {}) or {},
        }

    def _build_prompt(self, state: SwingTradeState, data: dict[str, Any]) -> str:
        symbol = state.get("symbol")
        position = data["position"]
        indicators = data["indicators"]
        profile = data["profile"]

        entry_price = float(position.get("entry_price") or 0)
        current_price = float(position.get("current_price") or entry_price)
        stop_loss = float(position.get("stop_loss") or 0)
        take_profit = float(position.get("take_profit") or 0)
        direction = str(position.get("direction") or "LONG").upper()

        if entry_price > 0:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            if direction == "SHORT":
                pnl_pct = -pnl_pct
        else:
            pnl_pct = 0.0

        if take_profit and current_price:
            distance_to_tp_pct = (
                (take_profit - current_price) / current_price * 100
                if direction == "LONG"
                else (current_price - take_profit) / current_price * 100
            )
        else:
            distance_to_tp_pct = None

        if stop_loss and current_price:
            distance_to_sl_pct = (
                (current_price - stop_loss) / current_price * 100
                if direction == "LONG"
                else (stop_loss - current_price) / current_price * 100
            )
        else:
            distance_to_sl_pct = None

        days_held = position.get("days_held")
        original_time_stop = position.get("max_holding_days") or position.get(
            "time_stop_days"
        )
        conviction = profile.get("conviction") or position.get("conviction") or "—"

        # Trend snapshot — these are the same fields the periodic position
        # monitor agent already uses, so we stay consistent with how the
        # rest of the system reads "is this trend intact?"
        ind_block_lines: list[str] = []
        if indicators and not indicators.get("error"):
            trend_state = indicators.get("trend") or {}
            ind_block_lines.append(
                f"- Daily MA20: price is {trend_state.get('ma20', 'unknown')} "
                f"(state tags: {', '.join(indicators.get('state_tags', []) or []) or 'none'})"
            )
            for label, key in [
                ("RSI(14)", "rsi14"),
                ("MFI(14)", "mfi_14"),
                ("ATR(14)", "atr_14"),
                ("MA20", "ma20"),
                ("MA50", "ma50"),
            ]:
                val = indicators.get(key)
                if val is not None:
                    ind_block_lines.append(f"- {label}: {val}")
        elif indicators.get("error"):
            ind_block_lines.append(f"- Indicator data unavailable: {indicators['error']}")
        ind_block = "\n".join(ind_block_lines) or "- No indicator data."

        bull_case = profile.get("bull_case", []) or []
        risks = profile.get("risks", []) or []

        return f"""
A position has reached its time-stop deadline. Decide whether to exit,
tighten the stop, or extend the deadline.

POSITION ({direction} {symbol}):
- Entry price:        ${entry_price:.2f}
- Current price:      ${current_price:.2f}
- Unrealized P&L:     {pnl_pct:+.2f}%
- Stop loss:          ${stop_loss:.2f}  (distance: {distance_to_sl_pct:+.2f}% from current)
- Take profit:        ${take_profit:.2f}  (distance to TP: {distance_to_tp_pct:+.2f}% from current)
- Days held:          {days_held if days_held is not None else 'unknown'}
- Original time stop: {original_time_stop if original_time_stop else 'unknown'} days
- Conviction at entry: {conviction}

TREND SNAPSHOT:
{ind_block}

ORIGINAL THESIS (from profile):
- Bull case: {', '.join(bull_case[:2]) if bull_case else 'Not available'}
- Known risks: {', '.join(risks[:2]) if risks else 'Not available'}

DECISION FRAMEWORK (no hardcoded thresholds — judge the full context):

EXIT when:
- Position is at a loss or marginally profitable AND the trend is broken
  or stalled (price below MA20, RSI weakening, momentum gone)
- Bull case has visibly failed to play out

TRAIL_TIGHT when:
- Position is comfortably profitable AND the trend is still intact
- We want to lock in gains while leaving room to ride a continuation
- Propose a NEW stop loss that locks in some profit (between current
  stop and current price for LONG; inverse for SHORT). A breakeven stop
  or one just below a recent swing low is typical.

EXTEND_HOLD when:
- Position is making progress toward TP (closer to TP than to SL on a
  pct-distance basis) AND trend is intact
- The thesis is still playing out, just slower than the original time
  stop assumed
- Propose extension_days (1-30) proportional to remaining distance.

Bias: when in doubt between EXIT and TRAIL_TIGHT, prefer TRAIL_TIGHT for
a profitable position with intact trend. When in doubt between
TRAIL_TIGHT and EXTEND_HOLD, prefer EXTEND_HOLD if there is real
progress toward TP. When in doubt between EXIT and EXTEND_HOLD on a
losing position, prefer EXIT.
""".strip()

    def _get_system_prompt(self) -> str:
        return (
            "You are the Time-Stop Decision Agent. A swing-trading position "
            "has hit its pre-set time deadline. Your job is to decide whether "
            "to exit, trail the stop tighter, or extend the deadline.\n\n"
            "CRITICAL RULES:\n"
            "1. Return ONLY valid JSON matching the schema. No markdown.\n"
            "2. If action=TRAIL_TIGHT, you MUST set new_stop_loss, and for a "
            "LONG position it MUST be > current stop loss AND < current price.\n"
            "3. If action=EXTEND_HOLD, you MUST set extension_days (1-30).\n"
            "4. If action=EXIT, leave new_stop_loss and extension_days null.\n"
            "5. Reason from the data given — do not invent indicators or news.\n"
            "6. Confidence reflects how clearly the data points one way."
        )
