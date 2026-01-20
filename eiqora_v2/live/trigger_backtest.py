from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class OutcomeResult:
    outcome: str
    outcome_time: datetime | None
    bars_to_outcome: int | None
    max_favorable_pct: float | None
    max_adverse_pct: float | None
    realized_pnl_pct: float | None
    same_bar_tie: bool = False


def compute_atr_brackets(
    entry_price: float,
    atr14: float,
    *,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
) -> tuple[float, float]:
    stop_loss = entry_price - (atr14 * sl_mult)
    take_profit = entry_price + (atr14 * tp_mult)
    return stop_loss, take_profit


def resolve_outcome(
    entry_time: datetime,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    bars: Iterable[tuple[datetime, float, float, float]],
) -> dict:
    bars_list = list(bars)
    max_high = entry_price
    min_low = entry_price
    bars_seen = 0

    for bar_time, bar_high, bar_low, bar_close in bars_list:
        bars_seen += 1
        if bar_high is not None:
            max_high = max(max_high, float(bar_high))
        if bar_low is not None:
            min_low = min(min_low, float(bar_low))

        tp_hit = bar_high is not None and float(bar_high) >= take_profit
        sl_hit = bar_low is not None and float(bar_low) <= stop_loss

        if tp_hit and sl_hit:
            max_fav = (max_high - entry_price) / entry_price
            max_adv = (min_low - entry_price) / entry_price
            return {
                "outcome": "SL_HIT",
                "outcome_time": bar_time,
                "bars_to_outcome": bars_seen,
                "max_favorable_pct": max_fav,
                "max_adverse_pct": max_adv,
                "realized_pnl_pct": (stop_loss - entry_price) / entry_price,
                "same_bar_tie": True,
            }
        if tp_hit:
            max_fav = (max_high - entry_price) / entry_price
            max_adv = (min_low - entry_price) / entry_price
            return {
                "outcome": "TP_HIT",
                "outcome_time": bar_time,
                "bars_to_outcome": bars_seen,
                "max_favorable_pct": max_fav,
                "max_adverse_pct": max_adv,
                "realized_pnl_pct": (take_profit - entry_price) / entry_price,
                "same_bar_tie": False,
            }
        if sl_hit:
            max_fav = (max_high - entry_price) / entry_price
            max_adv = (min_low - entry_price) / entry_price
            return {
                "outcome": "SL_HIT",
                "outcome_time": bar_time,
                "bars_to_outcome": bars_seen,
                "max_favorable_pct": max_fav,
                "max_adverse_pct": max_adv,
                "realized_pnl_pct": (stop_loss - entry_price) / entry_price,
                "same_bar_tie": False,
            }

    last_close = None
    for _, _, _, bar_close in bars_list:
        if bar_close is not None:
            last_close = float(bar_close)
    realized = (last_close - entry_price) / entry_price if last_close is not None else None
    max_fav = (max_high - entry_price) / entry_price
    max_adv = (min_low - entry_price) / entry_price
    return {
        "outcome": "NO_HIT",
        "outcome_time": None,
        "bars_to_outcome": bars_seen if bars_seen else None,
        "max_favorable_pct": max_fav,
        "max_adverse_pct": max_adv,
        "realized_pnl_pct": realized,
        "same_bar_tie": False,
    }
