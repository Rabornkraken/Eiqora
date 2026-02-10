"""
Confluence-Filtered Daily Trigger Backtest

Extends the daily trigger backtest with:
1. Confluence filter — validates every trigger against confirming indicators
2. Four new trigger types using available but unused indicators
3. Max holding period to cap open-ended trades

Entry Strategy (same as original):
- Technical triggers: Enter at NEXT day's OPEN (eliminates look-ahead bias)
- Event triggers: Enter at SAME day's OPEN (immediate reaction to known events)

Confluence Conditions (score >= min_confluence to enter):
1. Trend aligned: close > MA50
2. Volume confirmed: volume_z_20 > 0.5
3. Money flow positive: cmf_20 > 0 OR mfi_14 < 30
4. Near support: close within 5% of support_level

New Triggers:
- daily_bb_squeeze_breakout: BB width at 20-day min AND close > bb_upper_20
- daily_mfi_oversold_bounce: mfi_14 < 20 AND close > prev_close
- daily_support_bounce: close within 2% of support AND close > open
- daily_inside_day_breakout: today's range < yesterday's range AND close > yesterday's high
"""

import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta, date

from psycopg.types.json import Json

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_monitor import TriggerMonitor, Trigger
from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    build_result_row,
)
from eiqora_v2.live.backtest_daily_triggers import (
    fetch_daily_bars,
    fetch_atr14_daily,
    fetch_next_day_open,
    fetch_same_day_open,
    fetch_future_daily_bars,
    insert_result,
    get_daily_bars_to_test,
    format_trigger_log,
    check_daily_ict_triggers,
    EVENT_TRIGGERS,
    DEFAULT_EXCLUDED_TRIGGERS,
    _build_no_data_detail,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fetch_daily_bar_indicators(symbol: str, asof_date: date) -> dict | None:
    """Fetch indicator columns from market_bar_daily for a single bar.

    Returns dict with keys: bb_upper_20, bb_width, mfi_14, cmf_20,
    support_level, volume_z_20, or None if no row found.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bb_upper_20, bb_width, mfi_14, cmf_20,
                       support_level, volume_z_20
                FROM market_bar_daily
                WHERE symbol = %s AND date = %s
                LIMIT 1
                """,
                (symbol, asof_date),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "bb_upper_20": float(row[0]) if row[0] is not None else None,
                "bb_width": float(row[1]) if row[1] is not None else None,
                "mfi_14": float(row[2]) if row[2] is not None else None,
                "cmf_20": float(row[3]) if row[3] is not None else None,
                "support_level": float(row[4]) if row[4] is not None else None,
                "volume_z_20": float(row[5]) if row[5] is not None else None,
            }


def fetch_bb_width_min_20(symbol: str, asof_date: date) -> float | None:
    """Return the minimum bb_width over the last 20 trading days (inclusive)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(bb_width)
                FROM (
                    SELECT bb_width
                    FROM market_bar_daily
                    WHERE symbol = %s AND date <= %s AND bb_width IS NOT NULL
                    ORDER BY date DESC
                    LIMIT 20
                ) sub
                """,
                (symbol, asof_date),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None


# ---------------------------------------------------------------------------
# In-memory computations
# ---------------------------------------------------------------------------

def compute_ma(bars: list[dict], period: int) -> float | None:
    """Compute simple moving average from bars list (must be chronological).

    Uses the last *period* closing prices.
    """
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


# ---------------------------------------------------------------------------
# Confluence scorer
# ---------------------------------------------------------------------------

def compute_confluence_score(
    close: float,
    ma50: float | None,
    indicator_bar: dict | None,
) -> tuple[int, dict]:
    """Score 4 confluence conditions.

    Returns (score, breakdown_dict).
    """
    breakdown: dict[str, bool] = {}
    score = 0

    # 1. Trend aligned: close > MA50
    if ma50 is not None and close > ma50:
        breakdown["trend_aligned"] = True
        score += 1
    else:
        breakdown["trend_aligned"] = False

    if indicator_bar is None:
        breakdown["volume_confirmed"] = False
        breakdown["money_flow_positive"] = False
        breakdown["near_support"] = False
        return score, breakdown

    # 2. Volume confirmed: volume_z_20 > 0.5
    vz = indicator_bar.get("volume_z_20")
    if vz is not None and vz > 0.5:
        breakdown["volume_confirmed"] = True
        score += 1
    else:
        breakdown["volume_confirmed"] = False

    # 3. Money flow positive: cmf_20 > 0 OR mfi_14 < 30
    cmf = indicator_bar.get("cmf_20")
    mfi = indicator_bar.get("mfi_14")
    if (cmf is not None and cmf > 0) or (mfi is not None and mfi < 30):
        breakdown["money_flow_positive"] = True
        score += 1
    else:
        breakdown["money_flow_positive"] = False

    # 4. Near support: close within 5% of support_level
    support = indicator_bar.get("support_level")
    if support is not None and support > 0:
        dist_pct = abs(close - support) / close * 100
        if dist_pct <= 5.0:
            breakdown["near_support"] = True
            score += 1
        else:
            breakdown["near_support"] = False
    else:
        breakdown["near_support"] = False

    return score, breakdown


# ---------------------------------------------------------------------------
# New trigger detection
# ---------------------------------------------------------------------------

def check_new_triggers(
    symbol: str,
    check_date: date,
    bars: list[dict],
    indicator_bar: dict | None,
) -> list[Trigger]:
    """Detect 4 new daily trigger types using bars list and indicator columns.

    Args:
        symbol: Stock symbol
        check_date: Date being checked
        bars: Chronological list of daily bar dicts
        indicator_bar: Dict from fetch_daily_bar_indicators (may be None)

    Returns:
        List of Trigger objects for any detected patterns
    """
    triggers: list[Trigger] = []
    if len(bars) < 2:
        return triggers

    check_datetime = datetime.combine(check_date, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )
    current = bars[-1]
    prev = bars[-2]

    close = current.get("close")
    open_ = current.get("open")
    high = current.get("high")
    low = current.get("low")
    prev_close = prev.get("close")
    prev_high = prev.get("high")
    prev_low = prev.get("low")

    if close is None or open_ is None or high is None or low is None:
        return triggers
    if prev_close is None or prev_high is None or prev_low is None:
        return triggers

    # --- 1. daily_bb_squeeze_breakout ---
    if indicator_bar:
        bb_upper = indicator_bar.get("bb_upper_20")
        bb_width = indicator_bar.get("bb_width")
        if bb_upper is not None and bb_width is not None and close > bb_upper:
            # Short-circuit: only query min if close already above upper band
            bb_min_20 = fetch_bb_width_min_20(symbol, check_date)
            if bb_min_20 is not None and bb_width <= bb_min_20 * 1.01:
                triggers.append(
                    Trigger(
                        symbol=symbol,
                        trigger_type="daily_bb_squeeze_breakout",
                        priority="HIGH",
                        details={
                            "bb_upper_20": bb_upper,
                            "bb_width": bb_width,
                            "bb_width_min_20": bb_min_20,
                            "close": close,
                        },
                        detected_at=check_datetime,
                    )
                )

    # --- 2. daily_mfi_oversold_bounce ---
    if indicator_bar:
        mfi = indicator_bar.get("mfi_14")
        if mfi is not None and mfi < 20 and close > prev_close:
            triggers.append(
                Trigger(
                    symbol=symbol,
                    trigger_type="daily_mfi_oversold_bounce",
                    priority="MEDIUM",
                    details={
                        "mfi_14": mfi,
                        "close": close,
                        "prev_close": prev_close,
                    },
                    detected_at=check_datetime,
                )
            )

    # --- 3. daily_support_bounce ---
    if indicator_bar:
        support = indicator_bar.get("support_level")
        if support is not None and support > 0 and close > open_:
            dist_pct = abs(close - support) / close * 100
            if dist_pct <= 2.0:
                triggers.append(
                    Trigger(
                        symbol=symbol,
                        trigger_type="daily_support_bounce",
                        priority="MEDIUM",
                        details={
                            "support_level": support,
                            "close": close,
                            "open": open_,
                            "support_distance_pct": round(dist_pct, 2),
                        },
                        detected_at=check_datetime,
                    )
                )

    # --- 4. daily_inside_day_breakout ---
    today_range = high - low
    yesterday_range = prev_high - prev_low
    if yesterday_range > 0 and today_range < yesterday_range and close > prev_high:
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_inside_day_breakout",
                priority="HIGH",
                details={
                    "today_range": round(today_range, 4),
                    "yesterday_range": round(yesterday_range, 4),
                    "close": close,
                    "prev_high": prev_high,
                },
                detected_at=check_datetime,
            )
        )

    return triggers


# ---------------------------------------------------------------------------
# Max holding period wrapper
# ---------------------------------------------------------------------------

def resolve_outcome_with_max_hold(
    entry_time: datetime,
    entry_price: float,
    sl: float,
    tp: float,
    future_bars: list[tuple],
    max_hold_days: int | None = None,
) -> dict:
    """Resolve outcome, optionally truncating future bars to max_hold_days."""
    if max_hold_days is not None and max_hold_days > 0:
        future_bars = future_bars[:max_hold_days]
    return resolve_outcome(entry_time, entry_price, sl, tp, future_bars)


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

async def run_daily_confluence_backtest(
    start_date: str,
    end_date: str,
    run_name: str,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    starting_capital: float = 10000.0,
    excluded_triggers: set[str] | None = None,
    min_confluence: int = 2,
    max_hold_days: int | None = 20,
) -> uuid.UUID:
    """Run daily trigger backtest with confluence filtering.

    Same structure as run_daily_backtest() with:
    - Indicator lookup per bar
    - 4 new trigger types
    - Confluence scoring (filters triggers below min_confluence)
    - Max holding period
    """
    if excluded_triggers is None:
        excluded_triggers = DEFAULT_EXCLUDED_TRIGGERS
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    # Insert initial run record
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trigger_backtest_run (
                    run_id, run_name, start_date, end_date, started_at, parameters
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    run_name,
                    start_date,
                    end_date,
                    started_at,
                    Json({
                        "timeframe": "daily",
                        "mode": "confluence",
                        "sl_mult": sl_mult,
                        "tp_mult": tp_mult,
                        "position_size": 500.0,
                        "excluded_triggers": list(excluded_triggers),
                        "min_confluence": min_confluence,
                        "max_hold_days": max_hold_days,
                    }),
                ),
            )
        conn.commit()

    monitor = TriggerMonitor(backtest_mode=True)

    # Get all daily bars with indicators in range
    test_bars = get_daily_bars_to_test(start_date, end_date)
    total_bars = len(test_bars)
    logger.info(f"Starting confluence backtest with {total_bars} bars (min_confluence={min_confluence}, max_hold={max_hold_days})...")

    active_positions: dict[str, date] = {}
    triggers_found = 0
    confluence_passed = 0
    confluence_filtered = 0
    bars_processed = 0

    for symbol, check_date in test_bars:
        bars_processed += 1
        if bars_processed % 1000 == 0:
            logger.info(
                f"Progress: {bars_processed}/{total_bars} bars "
                f"({bars_processed * 100 // total_bars}%), "
                f"{triggers_found} triggers, {confluence_passed} passed confluence"
            )

        # Check if already in a position
        if symbol in active_positions:
            if check_date <= active_positions[symbol]:
                continue
            else:
                del active_positions[symbol]

        # Fetch daily bars for this symbol
        bars = fetch_daily_bars(symbol, check_date, limit=200)
        if len(bars) < 20:
            continue

        # Fetch indicator bar for confluence + new triggers
        indicator_bar = fetch_daily_bar_indicators(symbol, check_date)

        # Compute MA50 in-memory
        ma50 = compute_ma(bars, 50)

        # Current close for confluence scoring
        current_close = bars[-1].get("close")
        if current_close is None:
            continue

        # Aggregate triggers from all sources
        triggers: list[Trigger] = []

        check_time = datetime.combine(check_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )

        # 1. Daily Technical Triggers (from TriggerMonitor)
        try:
            tech_triggers = await monitor.check_daily_technical_triggers(symbol, check_time)
            triggers.extend(tech_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: Daily technical triggers failed - {e}")

        # 2. Daily ICT Triggers
        try:
            ict_triggers = await check_daily_ict_triggers(symbol, check_date, bars)
            triggers.extend(ict_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: Daily ICT triggers failed - {e}")

        # 3. Volatility Compression
        try:
            compression = await monitor.check_volatility_compression_trigger(symbol, check_time)
            if compression:
                triggers.append(compression)
        except Exception as e:
            logger.debug(f"{symbol}: Volatility compression trigger failed - {e}")

        # 4. NEW: Check 4 new trigger types
        try:
            new_triggers = check_new_triggers(symbol, check_date, bars, indicator_bar)
            triggers.extend(new_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: New trigger detection failed - {e}")

        if not triggers:
            continue

        # Filter out excluded triggers
        triggers = [t for t in triggers if t.trigger_type not in excluded_triggers]
        if not triggers:
            continue

        triggers_found += len(triggers)

        # Apply confluence filter
        if min_confluence > 0:
            passed_triggers = []
            for t in triggers:
                score, breakdown = compute_confluence_score(
                    current_close, ma50, indicator_bar
                )
                # Store confluence info in trigger details
                if t.details is None:
                    t.details = {}
                t.details["confluence_score"] = score
                t.details["confluence_breakdown"] = breakdown
                t.details["min_confluence"] = min_confluence
                if max_hold_days is not None:
                    t.details["max_hold_days"] = max_hold_days

                if score >= min_confluence:
                    passed_triggers.append(t)
                    confluence_passed += 1
                else:
                    confluence_filtered += 1

            triggers = passed_triggers
        else:
            # No confluence filter — still compute and store score for analysis
            for t in triggers:
                score, breakdown = compute_confluence_score(
                    current_close, ma50, indicator_bar
                )
                if t.details is None:
                    t.details = {}
                t.details["confluence_score"] = score
                t.details["confluence_breakdown"] = breakdown
                t.details["min_confluence"] = 0
                if max_hold_days is not None:
                    t.details["max_hold_days"] = max_hold_days
            confluence_passed += len(triggers)

        if not triggers:
            continue

        trade_end_date = None

        for trigger in triggers:
            logger.info(format_trigger_log(symbol, trigger.trigger_type, check_date))

            # Determine entry price strategy
            is_event_trigger = trigger.trigger_type in EVENT_TRIGGERS

            if is_event_trigger:
                entry_price = fetch_same_day_open(symbol, check_date)
                entry_date = check_date
            else:
                next_bar = fetch_next_day_open(symbol, check_date)
                if next_bar:
                    entry_price, entry_date = next_bar
                else:
                    entry_price = None
                    entry_date = None

            if not entry_price:
                insert_result(
                    build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail=_build_no_data_detail(trigger, "missing_entry_price"),
                        entry_price=None,
                        atr14=None,
                        stop_loss=None,
                        take_profit=None,
                        outcome="NO_DATA",
                        outcome_time=None,
                        bars_to_outcome=None,
                        max_favorable_pct=None,
                        max_adverse_pct=None,
                        realized_pnl_pct=None,
                        sl_mult=sl_mult,
                        tp_mult=tp_mult,
                    )
                )
                continue

            atr14 = fetch_atr14_daily(symbol, check_date)
            if not atr14:
                insert_result(
                    build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail=_build_no_data_detail(trigger, "missing_atr14"),
                        entry_price=entry_price,
                        atr14=None,
                        stop_loss=None,
                        take_profit=None,
                        outcome="NO_DATA",
                        outcome_time=None,
                        bars_to_outcome=None,
                        max_favorable_pct=None,
                        max_adverse_pct=None,
                        realized_pnl_pct=None,
                        sl_mult=sl_mult,
                        tp_mult=tp_mult,
                    )
                )
                continue

            stop_loss, take_profit = compute_atr_brackets(
                entry_price, atr14, sl_mult=sl_mult, tp_mult=tp_mult
            )

            all_future_bars = fetch_future_daily_bars(symbol, entry_date)
            if not all_future_bars:
                insert_result(
                    build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail=_build_no_data_detail(trigger, "missing_future_bars"),
                        entry_price=entry_price,
                        atr14=atr14,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        outcome="NO_DATA",
                        outcome_time=None,
                        bars_to_outcome=None,
                        max_favorable_pct=None,
                        max_adverse_pct=None,
                        realized_pnl_pct=None,
                        sl_mult=sl_mult,
                        tp_mult=tp_mult,
                    )
                )
                continue

            # Truncate future bars for both outcome resolution AND position locking
            if max_hold_days is not None and max_hold_days > 0:
                future_bars = all_future_bars[:max_hold_days]
            else:
                future_bars = all_future_bars

            # Resolve outcome with (possibly truncated) future bars
            entry_datetime = datetime.combine(entry_date, datetime.min.time()).replace(
                tzinfo=timezone.utc
            )
            outcome = resolve_outcome(
                entry_datetime,
                entry_price,
                stop_loss,
                take_profit,
                future_bars,
            )

            trigger_detail = dict(trigger.details or {})
            trigger_detail["entry_date"] = entry_date.isoformat()
            if outcome.get("same_bar_tie"):
                trigger_detail["same_bar_tie"] = True

            insert_result(
                build_result_row(
                    run_id=run_id,
                    run_name=run_name,
                    started_at=started_at,
                    symbol=symbol,
                    trigger_type=trigger.trigger_type,
                    trigger_priority=trigger.priority,
                    trigger_time=check_time,
                    trigger_detail=trigger_detail,
                    entry_price=entry_price,
                    atr14=atr14,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    outcome=outcome["outcome"],
                    outcome_time=outcome["outcome_time"],
                    bars_to_outcome=outcome["bars_to_outcome"],
                    max_favorable_pct=outcome["max_favorable_pct"],
                    max_adverse_pct=outcome["max_adverse_pct"],
                    realized_pnl_pct=outcome["realized_pnl_pct"],
                    sl_mult=sl_mult,
                    tp_mult=tp_mult,
                )
            )

            # Determine trade end date for locking
            outcome_time = outcome.get("outcome_time")
            if outcome_time:
                if hasattr(outcome_time, "date"):
                    outcome_date = outcome_time.date()
                else:
                    outcome_date = outcome_time
                if trade_end_date is None or outcome_date > trade_end_date:
                    trade_end_date = outcome_date
            elif future_bars:
                last_bar_time = future_bars[-1][0]
                last_bar_date = last_bar_time.date() if hasattr(last_bar_time, "date") else last_bar_time
                if trade_end_date is None or last_bar_date > trade_end_date:
                    trade_end_date = last_bar_date

        # Lock symbol if we executed trades
        if trade_end_date:
            active_positions[symbol] = trade_end_date

    logger.info(
        f"Backtest loop complete: {bars_processed} bars, "
        f"{triggers_found} triggers detected, "
        f"{confluence_passed} passed confluence, "
        f"{confluence_filtered} filtered out"
    )

    # Calculate metrics and update run record
    logger.info("Calculating run metrics...")
    _update_run_metrics_confluence(
        run_id, started_at, starting_capital,
        triggers_found, confluence_passed, confluence_filtered,
        min_confluence, max_hold_days,
    )
    logger.info(f"Run complete: {run_id}")

    return run_id


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _update_run_metrics_confluence(
    run_id: uuid.UUID,
    started_at: datetime,
    starting_capital: float,
    triggers_detected: int,
    confluence_passed: int,
    confluence_filtered: int,
    min_confluence: int,
    max_hold_days: int | None,
) -> None:
    """Calculate and store aggregate metrics (same as original + confluence stats)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Mark completed
            cur.execute(
                """
                UPDATE trigger_backtest_result
                SET completed_at = %s
                WHERE run_id = %s AND completed_at IS NULL
                """,
                (datetime.now(timezone.utc), run_id),
            )

            # 2. Aggregate metrics
            cur.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE outcome IN ('TP_HIT', 'SL_HIT')) as executed,
                    COUNT(*) FILTER (WHERE outcome = 'TP_HIT') as wins,
                    SUM(realized_pnl_pct) as total_pnl,
                    AVG(realized_pnl_pct) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR')
                """,
                (run_id,),
            )
            stats = cur.fetchone()

            total_triggers = stats[0] or 0
            executed_trades = stats[1] or 0
            wins = stats[2] or 0
            total_pnl = stats[3] or 0.0
            avg_pnl = stats[4] or 0.0
            win_rate = (wins / executed_trades * 100) if executed_trades > 0 else 0.0

            # Capital tracking (fixed $500/trade)
            cur.execute(
                """
                SELECT trigger_time, realized_pnl_pct
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome IN ('TP_HIT', 'SL_HIT')
                ORDER BY trigger_time ASC
                """,
                (run_id,),
            )
            trades = cur.fetchall()

            FIXED_POSITION_SIZE = 500.0
            COMMISSION_PER_ORDER = 1.00
            SLIPPAGE_PCT = 0.15

            current_capital = starting_capital
            peak_capital = starting_capital
            max_drawdown_pct = 0.0

            for trade_time, pnl_pct in trades:
                if current_capital < FIXED_POSITION_SIZE:
                    continue
                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0
                gross_pnl_dollars = FIXED_POSITION_SIZE * (pnl_pct / 100.0)
                total_commissions = COMMISSION_PER_ORDER * 2
                total_slippage = FIXED_POSITION_SIZE * (SLIPPAGE_PCT / 100.0) * 2
                net_pnl_dollars = gross_pnl_dollars - total_commissions - total_slippage
                current_capital += net_pnl_dollars
                if current_capital > peak_capital:
                    peak_capital = current_capital
                if peak_capital > 0:
                    drawdown = ((peak_capital - current_capital) / peak_capital) * 100.0
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)

            final_capital = current_capital
            total_return_pct = ((final_capital - starting_capital) / starting_capital) * 100.0

            # Yearly performance
            cur.execute(
                """
                SELECT
                    EXTRACT(YEAR FROM trigger_time) as year,
                    COUNT(*) as trades,
                    COUNT(*) FILTER (WHERE outcome = 'TP_HIT') as wins,
                    COUNT(*) FILTER (WHERE outcome = 'SL_HIT') as losses,
                    SUM(realized_pnl_pct) as total_pnl,
                    AVG(realized_pnl_pct) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome IN ('TP_HIT', 'SL_HIT')
                GROUP BY EXTRACT(YEAR FROM trigger_time)
                ORDER BY year
                """,
                (run_id,),
            )
            yearly_rows = cur.fetchall()

            # Yearly capital calculation
            cur.execute(
                """
                SELECT
                    EXTRACT(YEAR FROM trigger_time) as year,
                    trigger_time,
                    realized_pnl_pct
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome IN ('TP_HIT', 'SL_HIT')
                ORDER BY trigger_time ASC
                """,
                (run_id,),
            )
            all_trades = cur.fetchall()

            yearly_capital: dict[int, float] = {}
            year_start_capital: dict[int, float] = {}
            current_year = None
            year_capital = starting_capital

            for year, trigger_time, pnl_pct in all_trades:
                year = int(year)
                if current_year is None:
                    current_year = year
                    year_start_capital[year] = year_capital
                if year != current_year:
                    yearly_capital[current_year] = year_capital
                    current_year = year
                    year_start_capital[year] = year_capital
                if year_capital < FIXED_POSITION_SIZE:
                    continue
                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0
                gross_pnl = FIXED_POSITION_SIZE * (pnl_pct / 100.0)
                net_pnl = gross_pnl - 2.0 - (FIXED_POSITION_SIZE * 0.003)
                year_capital += net_pnl

            if current_year is not None:
                yearly_capital[current_year] = year_capital

            yearly_performance = []
            for year, trades_count, yr_wins, yr_losses, yr_total_pnl, yr_avg_pnl in yearly_rows:
                year = int(year)
                yr_wr = (yr_wins / trades_count * 100) if trades_count > 0 else 0.0
                start_cap = year_start_capital.get(year, starting_capital)
                end_cap = yearly_capital.get(year, start_cap)
                year_return = ((end_cap - start_cap) / start_cap * 100) if start_cap > 0 else 0.0
                yearly_performance.append({
                    "year": year,
                    "trades": trades_count,
                    "wins": yr_wins,
                    "losses": yr_losses,
                    "win_rate": round(float(yr_wr), 1),
                    "avg_pnl_pct": round(float(yr_avg_pnl or 0), 2),
                    "total_pnl_pct": round(float(yr_total_pnl or 0), 2),
                    "start_capital": round(float(start_cap), 2),
                    "end_capital": round(float(end_cap), 2),
                    "return_pct": round(float(year_return), 1),
                })

            # Best/worst triggers
            cur.execute(
                """
                SELECT trigger_type, AVG(realized_pnl_pct) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR')
                GROUP BY trigger_type
                ORDER BY avg_pnl DESC
                LIMIT 1
                """,
                (run_id,),
            )
            best_row = cur.fetchone()
            best_trigger = f"{best_row[0]} ({float(best_row[1]):.2f}%)" if best_row else None

            cur.execute(
                """
                SELECT trigger_type, AVG(realized_pnl_pct) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR')
                GROUP BY trigger_type
                ORDER BY avg_pnl ASC
                LIMIT 1
                """,
                (run_id,),
            )
            worst_row = cur.fetchone()
            worst_trigger = f"{worst_row[0]} ({float(worst_row[1]):.2f}%)" if worst_row else None

            # Per-trigger detail
            cur.execute(
                """
                SELECT
                    trigger_type,
                    COUNT(*) as count,
                    COUNT(*) FILTER (WHERE outcome = 'TP_HIT') as wins,
                    COUNT(*) FILTER (WHERE outcome = 'SL_HIT') as losses,
                    SUM(realized_pnl_pct) as total_pnl,
                    AVG(realized_pnl_pct) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR')
                GROUP BY trigger_type
                ORDER BY total_pnl DESC
                """,
                (run_id,),
            )
            trigger_stats = cur.fetchall()

            details_list = []
            for t_type, t_count, t_wins, t_losses, t_total_pnl, t_avg_pnl in trigger_stats:
                t_closed = t_wins + t_losses
                t_win_rate = (t_wins / t_closed * 100) if t_closed > 0 else 0.0
                details_list.append({
                    "trigger_type": t_type,
                    "count": t_count,
                    "wins": t_wins,
                    "losses": t_losses,
                    "win_rate": round(float(t_win_rate), 2),
                    "total_pnl_pct": round(float(t_total_pnl or 0), 2),
                    "avg_pnl_pct": round(float(t_avg_pnl or 0), 2),
                })

            # Confluence-specific stats stored in parameters
            confluence_stats = {
                "triggers_detected": triggers_detected,
                "confluence_passed": confluence_passed,
                "confluence_filtered": confluence_filtered,
                "min_confluence": min_confluence,
                "max_hold_days": max_hold_days,
            }

            # Update run record
            cur.execute(
                """
                UPDATE trigger_backtest_run
                SET completed_at = %s,
                    total_triggers = %s,
                    executed_trades = %s,
                    win_rate = %s,
                    total_pnl_pct = %s,
                    avg_pnl_pct = %s,
                    best_trigger_type = %s,
                    worst_trigger_type = %s,
                    trigger_details = %s,
                    yearly_performance = %s,
                    starting_capital = %s,
                    final_capital = %s,
                    total_return_pct = %s,
                    max_drawdown_pct = %s,
                    parameters = parameters || %s::jsonb
                WHERE run_id = %s
                """,
                (
                    datetime.now(timezone.utc),
                    total_triggers,
                    executed_trades,
                    win_rate,
                    total_pnl,
                    avg_pnl,
                    best_trigger,
                    worst_trigger,
                    Json(details_list),
                    Json(yearly_performance),
                    starting_capital,
                    final_capital,
                    total_return_pct,
                    max_drawdown_pct,
                    Json({"confluence_stats": confluence_stats}),
                    run_id,
                ),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """Configure logging for the backtest."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler("logs/daily_confluence_backtest.log")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("eiqora_v2.live.candidate_selector").setLevel(logging.INFO)


def main() -> None:
    """CLI entry point for confluence-filtered daily trigger backtest."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Daily confluence-filtered trigger backtest")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--run-name", type=str, default="confluence-backtest")
    parser.add_argument("--sl-mult", type=float, default=1.5, help="Stop loss ATR multiplier")
    parser.add_argument("--tp-mult", type=float, default=3.0, help="Take profit ATR multiplier")
    parser.add_argument("--starting-capital", type=float, default=10000.0)
    parser.add_argument("--min-confluence", type=int, default=2, help="Min confluence score (0-4)")
    parser.add_argument("--max-hold-days", type=int, default=20, help="Max holding period in days (0=unlimited)")
    args = parser.parse_args()

    max_hold = args.max_hold_days if args.max_hold_days > 0 else None

    run_id = asyncio.run(
        run_daily_confluence_backtest(
            args.start_date,
            args.end_date,
            args.run_name,
            args.sl_mult,
            args.tp_mult,
            args.starting_capital,
            min_confluence=args.min_confluence,
            max_hold_days=max_hold,
        )
    )
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
