"""
Stats Service - Deterministic backtester for trade rules.

This service evaluates trade ideas against historical analogs.
NO LLM is involved - this is pure Python/SQL computation.

The Decision Agent uses these stats to make GO/NO_GO decisions.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from eiqora_v2.tools.db import get_connection
from eiqora_v2.schemas.decision import StatsResult

logger = logging.getLogger(__name__)

# Default relaxation order when insufficient samples
DEFAULT_RELAXATION_ORDER = [
    "regime",       # Drop regime filter first
    "trend_bucket", # Then trend
    "vol_bucket",   # Then volatility
    "sector_etf",   # Finally sector (cross-sector analogs)
]

# Minimum sample thresholds
MIN_SAMPLES_THRESHOLDS = {
    "strict": 30,    # High confidence
    "normal": 20,    # Standard
    "relaxed": 12,   # Allows more relaxation
    "minimum": 8,    # Hard floor - below this return INSUFFICIENT_DATA
}


@dataclass
class TradeOutcome:
    """Result of simulating a single trade."""
    exit_type: str  # TAKE_PROFIT, STOP_LOSS, TIME_STOP, INVALIDATED
    exit_price: float
    return_pct: float
    hold_days: int
    entry_date: date
    exit_date: date


async def run_analog_stats(
    analog_plan: dict,
    trade_rule: dict,
    asof_time: datetime,
) -> StatsResult:
    """
    Run deterministic analog evaluation.
    
    This is the core Stats Service function:
    1. Query analog_event table for matching historical setups
    2. Simulate the trade rule on each analog
    3. Compute performance statistics
    
    Args:
        analog_plan: Query plan from Analog Planner (event_type, filters, lookback)
        trade_rule: Exit policy (tp_mult, sl_mult, time_stop_days)
        asof_time: Point-in-time reference (only use data before this)
    
    Returns:
        StatsResult with win_rate, expected_return, sample_size, etc.
    """
    plan_id = analog_plan.get("plan_id", "unknown")
    event_type = analog_plan.get("event_type", "UNKNOWN")
    filters = analog_plan.get("filters", {})
    lookback_years = analog_plan.get("lookback_years", 8)
    min_samples = analog_plan.get("min_samples", 30)
    
    logger.info(f"Stats Service: Running plan {plan_id} for {event_type}")
    
    # Query analogs
    analogs, relaxations = await _query_analogs(
        event_type=event_type,
        filters=filters,
        lookback_years=lookback_years,
        min_samples=min_samples,
        asof_time=asof_time,
    )
    
    if len(analogs) < MIN_SAMPLES_THRESHOLDS["minimum"]:
        logger.warning(f"Stats Service: Insufficient data ({len(analogs)} analogs)")
        return StatsResult(
            plan_id=plan_id,
            status="INSUFFICIENT_DATA",
            sample_size=len(analogs),
            relaxations_applied=relaxations,
        )
    
    # Simulate trades
    outcomes = []
    for analog in analogs:
        outcome = await _simulate_trade(
            symbol=analog["symbol"],
            entry_date=analog["event_date"],
            entry_price=float(analog["entry_price"]),
            trade_rule=trade_rule,
            asof_time=asof_time,
        )
        if outcome:
            outcomes.append(outcome)
    
    if not outcomes:
        return StatsResult(
            plan_id=plan_id,
            status="ERROR",
            sample_size=0,
            relaxations_applied=relaxations,
        )
    
    # Compute statistics
    stats = _compute_statistics(outcomes, asof_time)
    stats["plan_id"] = plan_id
    stats["relaxations_applied"] = relaxations
    
    logger.info(
        f"Stats Service: {plan_id} completed - "
        f"n={stats['sample_size']}, wr={stats['win_rate']:.1%}, "
        f"exp={stats['expected_return']:.2%}"
    )
    
    return StatsResult(**stats)


async def _query_analogs(
    event_type: str,
    filters: dict,
    lookback_years: int,
    min_samples: int,
    asof_time: datetime,
) -> tuple[list[dict], list[str]]:
    """
    Query analog_event table with progressive relaxation.
    
    Returns:
        Tuple of (analogs list, relaxations applied)
    """
    relaxations = []
    current_filters = filters.copy()
    
    async with get_connection() as conn:
        # Start date for lookback
        start_date = asof_time.date() - timedelta(days=365 * lookback_years)
        
        for attempt in range(len(DEFAULT_RELAXATION_ORDER) + 1):
            analogs = await _execute_query(
                conn, event_type, current_filters, start_date, asof_time.date()
            )
            
            if len(analogs) >= min_samples:
                return analogs, relaxations
            
            # Need to relax - find next filter to drop
            if attempt < len(DEFAULT_RELAXATION_ORDER):
                filter_to_drop = DEFAULT_RELAXATION_ORDER[attempt]
                if filter_to_drop in current_filters and current_filters[filter_to_drop]:
                    logger.debug(f"Relaxing filter: {filter_to_drop}")
                    relaxations.append(filter_to_drop)
                    current_filters[filter_to_drop] = None
        
        return analogs, relaxations


async def _execute_query(
    conn,
    event_type: str,
    filters: dict,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Execute the analog query with current filters."""
    
    # Build query dynamically
    query = """
        SELECT analog_id, symbol, event_date, event_type,
               sector_etf, vol_bucket, trend_bucket, regime,
               entry_price, invalidation_price, setup_quality_score
        FROM analog_event
        WHERE event_type = $1
          AND event_date >= $2
          AND event_date < $3
    """
    params = [event_type, start_date, end_date]
    param_idx = 4
    
    if filters.get("sector_etf"):
        query += f" AND sector_etf = ${param_idx}"
        params.append(filters["sector_etf"])
        param_idx += 1
    
    if filters.get("vol_bucket"):
        query += f" AND vol_bucket = ${param_idx}"
        params.append(filters["vol_bucket"])
        param_idx += 1
    
    if filters.get("trend_bucket"):
        query += f" AND trend_bucket = ${param_idx}"
        params.append(filters["trend_bucket"])
        param_idx += 1
    
    if filters.get("regime"):
        query += f" AND regime = ${param_idx}"
        params.append(filters["regime"])
        param_idx += 1
    
    query += " ORDER BY event_date DESC"
    
    rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


async def _simulate_trade(
    symbol: str,
    entry_date: date,
    entry_price: float,
    trade_rule: dict,
    asof_time: datetime,
) -> TradeOutcome | None:
    """
    Simulate a single trade using the trade rule.
    
    Walks through price bars after entry to find exit.
    """
    # Get exit parameters
    exit_policy = trade_rule.get("exit_policy", trade_rule)
    bracket = exit_policy.get("bracket", exit_policy)
    
    tp_mult = bracket.get("tp_mult", 4.0)
    sl_mult = bracket.get("sl_mult", 2.0)
    time_stop_days = bracket.get("time_stop_days", 45)
    
    async with get_connection() as conn:
        # Get volatility at entry for bracket calculation
        vol = await _get_volatility_at_date(conn, symbol, entry_date)
        if not vol:
            vol = 0.02  # Default 2% daily vol
        
        # Calculate exit levels
        tp_level = entry_price * (1 + vol * tp_mult)
        sl_level = entry_price * (1 - vol * sl_mult)
        
        # Fetch price bars after entry
        bars = await conn.fetch("""
            SELECT date, open, high, low, close
            FROM market_bar_daily
            WHERE symbol = $1
              AND date > $2
              AND date <= $2 + interval '1 day' * $3
            ORDER BY date ASC
        """, symbol, entry_date, time_stop_days + 10)
        
        if not bars:
            return None
        
        # Walk through bars to find exit
        for i, bar in enumerate(bars):
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_close = float(bar["close"])
            bar_date = bar["date"]
            
            # Check stop loss (hit first in case of gap down)
            if bar_low <= sl_level:
                return TradeOutcome(
                    exit_type="STOP_LOSS",
                    exit_price=sl_level,
                    return_pct=(sl_level / entry_price) - 1,
                    hold_days=i + 1,
                    entry_date=entry_date,
                    exit_date=bar_date,
                )
            
            # Check take profit
            if bar_high >= tp_level:
                return TradeOutcome(
                    exit_type="TAKE_PROFIT",
                    exit_price=tp_level,
                    return_pct=(tp_level / entry_price) - 1,
                    hold_days=i + 1,
                    entry_date=entry_date,
                    exit_date=bar_date,
                )
            
            # Check time stop
            if i + 1 >= time_stop_days:
                return TradeOutcome(
                    exit_type="TIME_STOP",
                    exit_price=bar_close,
                    return_pct=(bar_close / entry_price) - 1,
                    hold_days=i + 1,
                    entry_date=entry_date,
                    exit_date=bar_date,
                )
        
        # No exit triggered (data ended)
        if bars:
            last_bar = bars[-1]
            return TradeOutcome(
                exit_type="DATA_END",
                exit_price=float(last_bar["close"]),
                return_pct=(float(last_bar["close"]) / entry_price) - 1,
                hold_days=len(bars),
                entry_date=entry_date,
                exit_date=last_bar["date"],
            )
        
        return None


async def _get_volatility_at_date(conn, symbol: str, as_of_date: date) -> float | None:
    """Get 20-day realized volatility at a specific date."""
    rows = await conn.fetch("""
        SELECT close
        FROM market_bar_daily
        WHERE symbol = $1
          AND date <= $2
        ORDER BY date DESC
        LIMIT 21
    """, symbol, as_of_date)
    
    if len(rows) < 20:
        return None
    
    closes = [float(r["close"]) for r in rows]
    log_returns = np.diff(np.log(closes))
    return float(np.std(log_returns))


def _compute_statistics(outcomes: list[TradeOutcome], asof_time: datetime) -> dict:
    """Compute performance statistics from trade outcomes."""
    returns = [o.return_pct for o in outcomes]
    hold_days = [o.hold_days for o in outcomes]
    
    wins = [r for r in returns if r > 0]
    
    # Basic stats
    win_rate = len(wins) / len(returns)
    expected_return = float(np.mean(returns))
    median_return = float(np.median(returns))
    
    # Percentiles
    p10 = float(np.percentile(returns, 10))
    p90 = float(np.percentile(returns, 90))
    
    # Hold time
    avg_hold_days = float(np.mean(hold_days))
    
    # Stability score (how consistent are recent results vs older)
    stability = _compute_stability(outcomes, asof_time)
    
    return {
        "status": "OK",
        "sample_size": len(outcomes),
        "win_rate": win_rate,
        "expected_return": expected_return,
        "median_return": median_return,
        "p10": p10,
        "p90": p90,
        "avg_hold_days": avg_hold_days,
        "stability": stability,
    }


def _compute_stability(outcomes: list[TradeOutcome], asof_time: datetime) -> float:
    """
    Compute stability score (0-1).
    
    Compares win rate of recent 2 years vs older data.
    High stability = consistent performance over time.
    """
    if len(outcomes) < 20:
        return 0.5  # Not enough data
    
    two_years_ago = asof_time.date() - timedelta(days=730)
    
    recent = [o for o in outcomes if o.entry_date >= two_years_ago]
    older = [o for o in outcomes if o.entry_date < two_years_ago]
    
    if len(recent) < 5 or len(older) < 5:
        return 0.5  # Not enough split
    
    recent_wr = len([o for o in recent if o.return_pct > 0]) / len(recent)
    older_wr = len([o for o in older if o.return_pct > 0]) / len(older)
    
    # Stability = 1 - difference in win rates (max 1.0)
    diff = abs(recent_wr - older_wr)
    return max(0.0, min(1.0, 1.0 - diff * 2))
