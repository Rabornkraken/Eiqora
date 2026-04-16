"""
Market Regime Service.

Computes a composite market regime score from SPY trend, VIX level, and
SPY RSI. Used by the screening pipeline to dynamically adjust thresholds
and block new long entries in bear markets.

The regime feeds into two things:
1. Watchlist threshold adjustment (+/-offset from portfolio base)
2. Position size multiplier (0.0 in BEAR = no new longs)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from eiqora_v2.tools.db import get_connection

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketRegime:
    regime: Literal["BULL", "NEUTRAL", "CAUTIOUS", "BEAR"]
    composite_score: float  # -1.0 to +1.0
    trend_score: float
    vix_score: float
    rsi_score: float
    position_size_multiplier: float  # 0.0 to 1.0
    threshold_adjustment: float  # -0.10 to +0.20
    spy_close: float | None
    spy_sma20: float | None
    spy_rsi: float | None
    vix_level: float | None
    reason: str


def _score_trend(trend_pct: float) -> float:
    """Score SPY trend relative to SMA20."""
    if trend_pct > 1.0:
        return 1.0
    elif trend_pct >= -1.0:
        return 0.5
    elif trend_pct >= -3.0:
        return -0.5
    else:
        return -1.0


def _score_vix(vix_level: float) -> float:
    """Score VIX level."""
    if vix_level < 15:
        return 0.5
    elif vix_level <= 20:
        return 1.0
    elif vix_level <= 25:
        return 0.0
    elif vix_level <= 30:
        return -0.5
    else:
        return -1.0


def _score_rsi(rsi: float) -> float:
    """Score SPY RSI."""
    if rsi > 50:
        return 1.0
    elif rsi >= 40:
        return 0.0
    elif rsi >= 30:
        return -0.5
    else:
        return -1.0


def _classify_regime(
    composite: float,
) -> tuple[Literal["BULL", "NEUTRAL", "CAUTIOUS", "BEAR"], float, float]:
    """Return (regime, position_size_multiplier, threshold_adjustment)."""
    if composite > 0.3:
        return "BULL", 1.0, -0.05
    elif composite > -0.2:
        return "NEUTRAL", 0.75, 0.0
    elif composite > -0.6:
        return "CAUTIOUS", 0.50, 0.10
    else:
        return "BEAR", 0.0, 0.20


def _build_reason(
    regime: str,
    spy_close: float | None,
    spy_sma20: float | None,
    spy_rsi: float | None,
    vix_level: float | None,
    trend_score: float,
    vix_score: float,
    rsi_score: float,
    composite: float,
) -> str:
    """Build a human-readable reason string."""
    parts: list[str] = [f"Regime={regime} (composite={composite:+.2f})"]

    if spy_close is not None and spy_sma20 is not None:
        trend_pct = (spy_close - spy_sma20) / spy_sma20 * 100
        direction = "above" if trend_pct >= 0 else "below"
        parts.append(f"SPY {direction} SMA20 by {abs(trend_pct):.1f}% (trend={trend_score:+.1f})")
    else:
        parts.append("SPY trend data unavailable")

    if vix_level is not None:
        parts.append(f"VIX={vix_level:.1f} (vix={vix_score:+.1f})")
    else:
        parts.append("VIX data unavailable")

    if spy_rsi is not None:
        parts.append(f"RSI={spy_rsi:.1f} (rsi={rsi_score:+.1f})")
    else:
        parts.append("RSI data unavailable")

    return "; ".join(parts)


_NEUTRAL_DEFAULT = MarketRegime(
    regime="NEUTRAL",
    composite_score=0.0,
    trend_score=0.0,
    vix_score=0.0,
    rsi_score=0.0,
    position_size_multiplier=1.0,
    threshold_adjustment=0.0,
    spy_close=None,
    spy_sma20=None,
    spy_rsi=None,
    vix_level=None,
    reason="Defaulting to NEUTRAL - insufficient market data",
)


async def compute_market_regime(scan_time: datetime) -> MarketRegime:
    """
    Compute the current market regime from SPY and VIX data.

    Queries ``market_bar_daily`` for the latest SPY close, SMA20
    (``bb_middle_20``), RSI-14, and VIX close. Produces a composite score
    from -1.0 to +1.0 that maps to one of four regimes (BULL, NEUTRAL,
    CAUTIOUS, BEAR).

    Falls back to NEUTRAL with multiplier=1.0 when data is missing.
    """
    scan_date = scan_time.date() if hasattr(scan_time, "date") else scan_time

    try:
        async with get_connection() as conn:
            # Fetch the most recent SPY row ON or BEFORE the scan date
            spy_row = await conn.fetchrow(
                """
                SELECT close, bb_middle_20, rsi_14
                FROM market_bar_daily
                WHERE symbol = 'SPY' AND close IS NOT NULL AND date <= $1
                ORDER BY date DESC
                LIMIT 1
                """,
                scan_date,
            )

            # Fetch the most recent VIX close ON or BEFORE the scan date
            vix_row = await conn.fetchrow(
                """
                SELECT close
                FROM market_bar_daily
                WHERE symbol IN ('VIX', 'IDX_VIX') AND close IS NOT NULL AND date <= $1
                ORDER BY date DESC
                LIMIT 1
                """,
                scan_date,
            )
    except Exception as exc:
        _logger.warning("Market regime DB query failed: %s", exc)
        return _NEUTRAL_DEFAULT

    if spy_row is None:
        _logger.warning("No SPY data in market_bar_daily; defaulting to NEUTRAL")
        return _NEUTRAL_DEFAULT

    spy_close = float(spy_row["close"]) if spy_row["close"] is not None else None
    spy_sma20 = float(spy_row["bb_middle_20"]) if spy_row["bb_middle_20"] is not None else None
    spy_rsi = float(spy_row["rsi_14"]) if spy_row["rsi_14"] is not None else None
    vix_level = float(vix_row["close"]) if vix_row and vix_row["close"] is not None else None

    # --- Compute individual scores ----------------------------------------
    # Trend: requires both close and SMA20
    if spy_close is not None and spy_sma20 is not None and spy_sma20 > 0:
        trend_pct = (spy_close - spy_sma20) / spy_sma20 * 100
        trend_score = _score_trend(trend_pct)
    else:
        trend_score = 0.0  # neutral when unavailable

    # VIX
    if vix_level is not None:
        vix_score = _score_vix(vix_level)
    else:
        vix_score = 0.0

    # RSI
    if spy_rsi is not None:
        rsi_score = _score_rsi(spy_rsi)
    else:
        rsi_score = 0.0

    # --- Composite --------------------------------------------------------
    composite = trend_score * 0.50 + vix_score * 0.30 + rsi_score * 0.20
    regime, multiplier, threshold_adj = _classify_regime(composite)

    reason = _build_reason(
        regime, spy_close, spy_sma20, spy_rsi, vix_level,
        trend_score, vix_score, rsi_score, composite,
    )

    _logger.info("Market regime computed: %s", reason)

    return MarketRegime(
        regime=regime,
        composite_score=round(composite, 4),
        trend_score=trend_score,
        vix_score=vix_score,
        rsi_score=rsi_score,
        position_size_multiplier=multiplier,
        threshold_adjustment=threshold_adj,
        spy_close=spy_close,
        spy_sma20=spy_sma20,
        spy_rsi=spy_rsi,
        vix_level=vix_level,
        reason=reason,
    )
