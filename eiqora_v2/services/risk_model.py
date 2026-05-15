"""
Deterministic risk model for position sizing and portfolio caps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from eiqora_v2.tools.db import get_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskConfig:
    # 2026-05-16: aggressive retune — the prior values were silently
    # blocking ~all new entries (max_positions=8 vs 11 open positions,
    # 90% heat cap with sector_cap=35% in a tech-heavy book). See the
    # NVDA/AAPL/MU NO_GO chain in analysis_log around that date.
    risk_per_trade_pct: float = 0.05   # 5% of equity risk per trade
    max_position_pct: float = 0.30     # 30% max position size
    portfolio_heat_cap: float = 1.00   # 100% fully invested allowed
    max_positions: int = 25
    sector_cap: float = 0.50           # 50% max exposure per sector
    default_position_pct: float = 0.15


def _conviction_multiplier(conviction: str | None) -> float:
    value = (conviction or "MEDIUM").upper()
    if value == "HIGH":
        return 1.2
    if value == "LOW":
        return 0.8
    return 1.0


async def _load_positions() -> list[dict[str, Any]]:
    """Load active positions for portfolio-cap calculations.

    Note: this used to JOIN ``ticker_info`` for a ``sector`` column, but
    that table was never created in the live schema. The whole sector-cap
    logic was therefore raising ``UndefinedTableError`` on every call,
    which the caller in pipeline.py swallowed silently — leading to 423
    consecutive analyses with risk_model_output='{}' until 2026-05-07.
    Until we wire up a real sector source (yfinance.Ticker.info has it
    but isn't currently ingested), positions return without a sector and
    the sector cap below is a no-op.
    """
    async with get_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT symbol, position_size_pct
            FROM position
            WHERE status = 'ACTIVE'
            """
        )
    return [dict(r) for r in rows]


def _normalize_size(value: Any, default: float) -> float:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return default
    if size > 1.0:
        size = size / 100.0
    return size


async def size_position(
    *,
    symbol: str,
    entry_price: float,
    stop_loss: float | None,
    conviction: str | None = None,
    config: RiskConfig | None = None,
) -> dict[str, Any]:
    """
    Compute deterministic position size as a percent of equity.
    Returns dict with size and gating metadata.
    """
    cfg = config or RiskConfig()
    notes: list[str] = []

    if entry_price <= 0:
        return {
            "position_size_pct": 0.0,
            "notes": ["invalid_entry_price"],
        }

    if stop_loss is None or stop_loss <= 0:
        sl_pct = 0.02
        notes.append("missing_stop_loss_default_sl_pct")
    else:
        sl_pct = abs(entry_price - stop_loss) / entry_price

    if sl_pct <= 0:
        sl_pct = 0.02
        notes.append("invalid_sl_pct_default")

    base_size = cfg.risk_per_trade_pct / sl_pct
    base_size *= _conviction_multiplier(conviction)

    # Cap by per-position limit.
    size_pct = min(base_size, cfg.max_position_pct)

    # Portfolio caps.
    positions = await _load_positions()
    position_count = len(positions)
    exposure = sum(_normalize_size(p.get("position_size_pct"), cfg.default_position_pct) for p in positions)
    available_exposure = max(0.0, cfg.portfolio_heat_cap - exposure)
    size_pct = min(size_pct, available_exposure)

    # Max positions cap.
    if position_count >= cfg.max_positions:
        size_pct = 0.0
        notes.append("max_positions_reached")

    # Sector cap.
    #
    # The original implementation queried ``ticker_info.sector``, which
    # never existed in the live schema, so the entire sector check has
    # been a silent no-op (and earlier was raising and bringing the whole
    # sizer down). We do the bookkeeping here for the response shape, but
    # without a sector source the caps don't engage. When we wire up a
    # real sector table this block becomes active again unchanged.
    sector_exposure: dict[str, float] = {}
    sector_position_count: dict[str, int] = {}
    symbol_sector = None

    for p in positions:
        sector = p.get("sector")
        if not sector:
            continue
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + _normalize_size(
            p.get("position_size_pct"), cfg.default_position_pct
        )
        sector_position_count[sector] = sector_position_count.get(sector, 0) + 1

    # Check sector exposure cap
    if symbol_sector:
        remaining_sector = cfg.sector_cap - sector_exposure.get(symbol_sector, 0.0)
        if remaining_sector <= 0:
            size_pct = 0.0
            notes.append("sector_cap_reached")
        else:
            size_pct = min(size_pct, remaining_sector)

        # Per-sector position count cap. Raised from 2 -> 5 on 2026-05-16
        # as part of the aggressive retune so the tech-heavy universe
        # isn't permanently single-sector-blocked once we hold a few
        # semis/software names.
        max_positions_per_sector = 5
        current_sector_count = sector_position_count.get(symbol_sector, 0)
        if current_sector_count >= max_positions_per_sector:
            size_pct = 0.0
            notes.append(f"sector_position_limit_{current_sector_count}_of_{max_positions_per_sector}")
            logger.info(f"Rejecting {symbol}: Sector {symbol_sector} already has {current_sector_count} positions (max {max_positions_per_sector})")
    else:
        notes.append("sector_data_unavailable")

    if size_pct <= 0:
        notes.append("size_zero_after_caps")

    return {
        "position_size_pct": round(size_pct, 4),
        "risk_per_trade_pct": cfg.risk_per_trade_pct,
        "sl_pct": round(sl_pct, 4),
        "base_size_pct": round(base_size, 4),
        "max_position_pct": cfg.max_position_pct,
        "portfolio_heat_cap": cfg.portfolio_heat_cap,
        "available_exposure_pct": round(available_exposure, 4),
        "position_count": position_count,
        "sector_cap": cfg.sector_cap,
        "sector_exposure": sector_exposure,
        "notes": notes,
    }
