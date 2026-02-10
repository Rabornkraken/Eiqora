"""
Daily Trigger Backtest System (Capital-Aware)

Uses market_bar_daily instead of market_bar_hourly for trigger detection
and outcome resolution.

Capital is tracked in the main loop — trades are skipped if cash is
insufficient or all position slots are full. NO_HIT trades are force-closed
after max_hold_days trading days.

Entry Strategy:
- Technical triggers: Enter at NEXT day's OPEN (eliminates look-ahead bias)
- Event triggers: Enter at SAME day's OPEN (immediate reaction to known events)

Triggers:
- Daily Technical: stochastic_bounce, rsi_oversold, macd_crossover, breakout, volume_surge
- Daily ICT Patterns: fvg_bullish_fill, liquidity_sweep_reversal, order_block_retest
- Second-order: volatility_compression
"""

import argparse
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date

from psycopg.types.json import Json

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_monitor import TriggerMonitor, Trigger
from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    build_result_row,
)
from eiqora_v2.tools.ict_patterns import (
    Bar,
    find_fair_value_gaps,
    find_swing_points,
    detect_liquidity_sweep,
    find_order_blocks,
    is_price_in_fvg,
    is_price_in_order_block,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
POSITION_PCT = 0.05  # 5% of capital per trade
MIN_POSITION_DOLLARS = 50.0
MAX_POSITIONS_DEFAULT = 20
MAX_HOLD_DAYS_DEFAULT = 30
COMMISSION_PER_ORDER = 1.00
SLIPPAGE_PCT = 0.15  # 0.15% per side for daily open entries

PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

ETF_SYMBOLS = {
    "DIA", "IEF", "IWM", "QQQ", "SPY", "SHY", "TLT", "UUP",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
    "XLU", "XLV", "XLY",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class OpenPosition:
    """Tracks an open position for capital management."""
    symbol: str
    entry_date: date
    exit_date: date  # precomputed: outcome_date or entry_date + max_hold_days
    entry_price: float
    position_size_dollars: float
    outcome: str | None = None
    realized_pnl_pct: float | None = None


@dataclass
class PendingTrigger:
    """A trigger with pre-fetched entry data, ready for capital allocation."""
    trigger: Trigger
    check_date: date
    entry_price: float
    entry_date: date
    atr14: float
    stop_loss: float
    take_profit: float
    future_bars: list[tuple]
    outcome: dict | None = None  # resolved outcome (may be truncated by max_hold)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def is_stock(symbol: str) -> bool:
    """Return True if symbol is a stock (not ETF or index)."""
    if symbol in ETF_SYMBOLS:
        return False
    if symbol.startswith("IDX_"):
        return False
    return True


def fetch_daily_bars(symbol: str, asof_date: date, limit: int = 200) -> list[dict]:
    """Fetch OHLCV from market_bar_daily."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT date, open, high, low, close, volume, atr_14
                FROM market_bar_daily
                WHERE symbol = %s AND date <= %s
                ORDER BY date DESC
                LIMIT %s
                """,
                (symbol, asof_date, limit),
            )
            rows = cur.fetchall()
            # Return in chronological order (oldest first)
            return [
                {
                    "date": row[0],
                    "open": float(row[1]) if row[1] else None,
                    "high": float(row[2]) if row[2] else None,
                    "low": float(row[3]) if row[3] else None,
                    "close": float(row[4]) if row[4] else None,
                    "volume": float(row[5]) if row[5] else None,
                    "atr_14": float(row[6]) if row[6] else None,
                }
                for row in reversed(rows)
            ]


def fetch_atr14_daily(symbol: str, asof_date: date) -> float | None:
    """Fetch ATR14 from the daily bar on or before the given date."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT atr_14
                FROM market_bar_daily
                WHERE symbol = %s AND date <= %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (symbol, asof_date),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None


def fetch_next_day_open(symbol: str, after_date: date) -> tuple[float, date] | None:
    """
    Get next trading day's open price for entry.

    For technical triggers, we enter at the NEXT day's open to avoid look-ahead bias.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT open, date
                FROM market_bar_daily
                WHERE symbol = %s AND date > %s
                ORDER BY date ASC
                LIMIT 1
                """,
                (symbol, after_date),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0]), row[1]
            return None


def fetch_same_day_open(symbol: str, on_date: date) -> float | None:
    """Get the same day's open price for event triggers."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT open
                FROM market_bar_daily
                WHERE symbol = %s AND date = %s
                LIMIT 1
                """,
                (symbol, on_date),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None


def fetch_future_daily_bars(symbol: str, after_date: date) -> list[tuple]:
    """
    Get future daily bars for outcome resolution.

    Returns: list of (datetime, high, low, close) tuples
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT date, high, low, close
                FROM market_bar_daily
                WHERE symbol = %s AND date > %s
                ORDER BY date ASC
                """,
                (symbol, after_date),
            )
            # Convert date to datetime for resolve_outcome compatibility
            return [
                (
                    datetime.combine(row[0], datetime.min.time()).replace(tzinfo=timezone.utc),
                    row[1],
                    row[2],
                    row[3],
                )
                for row in cur.fetchall()
            ]


def insert_result(row: tuple) -> None:
    """Insert a single backtest result into the database."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trigger_backtest_result (
                    run_id, run_name, symbol, trigger_type, trigger_priority, trigger_time,
                    trigger_detail, entry_price, atr14, sl_mult, tp_mult, stop_loss, take_profit,
                    outcome, outcome_time, bars_to_outcome, max_favorable_pct, max_adverse_pct,
                    realized_pnl_pct, started_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                row,
            )
        conn.commit()


def _build_no_data_detail(trigger: Trigger, reason: str) -> dict:
    """Build trigger detail dict with no_data_reason."""
    details = dict(trigger.details or {})
    details["no_data_reason"] = reason
    return details


def get_daily_bars_to_test(start_date: str, end_date: str) -> list[tuple[str, date]]:
    """
    Fetch all daily bars with valid indicators in date range.

    Returns list of (symbol, date) tuples to test.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol, date
                FROM market_bar_daily
                WHERE rsi_14 IS NOT NULL
                  AND date >= %s
                  AND date <= %s
                ORDER BY date ASC, symbol
                """,
                (start_date, end_date),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]


def format_trigger_log(symbol: str, trigger_type: str, check_date: date) -> str:
    """Format a log message for a detected trigger."""
    return f"TRIGGER {symbol} {trigger_type} @ {check_date.isoformat()}"


async def check_daily_ict_triggers(
    symbol: str,
    check_date: date,
    bars: list[dict],
) -> list[Trigger]:
    """
    Detect ICT patterns on daily timeframe.

    Uses Fair Value Gaps, Liquidity Sweeps, and Order Blocks.

    Args:
        symbol: Stock symbol
        check_date: Date to check for triggers
        bars: List of daily bar dicts with keys: date, open, high, low, close, volume

    Returns:
        List of Trigger objects for detected patterns
    """
    triggers = []

    if len(bars) < 10:
        return triggers

    # Convert to Bar objects
    bar_objects = [
        Bar(
            datetime=datetime.combine(b["date"], datetime.min.time()),
            open=b["open"],
            high=b["high"],
            low=b["low"],
            close=b["close"],
            volume=b["volume"] or 0,
        )
        for b in bars
        if b["open"] is not None and b["high"] is not None
    ]

    if len(bar_objects) < 10:
        return triggers

    current_bar = bar_objects[-1]
    current_price = current_bar.close

    # Common details for all triggers
    base_details = {
        "current_price": current_price,
        "check_date": check_date.isoformat(),
    }

    check_datetime = datetime.combine(check_date, datetime.min.time()).replace(tzinfo=timezone.utc)

    # 1. FAIR VALUE GAP (FVG) FILL
    fvgs = find_fair_value_gaps(bar_objects, lookback=20)
    for fvg in fvgs:
        if fvg.type == "bullish" and is_price_in_fvg(current_price, fvg):
            triggers.append(
                Trigger(
                    symbol=symbol,
                    trigger_type="daily_fvg_bullish_fill",
                    priority="MEDIUM",
                    details={
                        **base_details,
                        "fvg_top": fvg.top,
                        "fvg_bottom": fvg.bottom,
                        "fvg_formed": fvg.formation_time.isoformat(),
                    },
                    detected_at=check_datetime,
                )
            )
            break  # Only one FVG trigger per check

    # 2. LIQUIDITY SWEEP REVERSAL
    swing_points = find_swing_points(bar_objects, lookback=5)
    sweep = detect_liquidity_sweep(current_bar, swing_points)

    if sweep and sweep["type"] == "bullish":
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_liquidity_sweep_reversal",
                priority="MEDIUM",
                details={
                    **base_details,
                    "sweep_type": sweep["type"],
                    "swing_level": sweep["swing_level"],
                    "sweep_depth_pct": round(sweep["sweep_depth_pct"], 2),
                },
                detected_at=check_datetime,
            )
        )

    # 3. ORDER BLOCK RETEST
    order_blocks = find_order_blocks(bar_objects, lookback=20, min_move_pct=1.0)
    for ob in order_blocks:
        if ob.type == "bullish" and is_price_in_order_block(current_price, ob):
            triggers.append(
                Trigger(
                    symbol=symbol,
                    trigger_type="daily_order_block_retest",
                    priority="MEDIUM",
                    details={
                        **base_details,
                        "ob_top": ob.top,
                        "ob_bottom": ob.bottom,
                        "ob_formed": ob.formation_time.isoformat(),
                    },
                    detected_at=check_datetime,
                )
            )
            break  # Only one OB trigger per check

    return triggers


# Event triggers that use same-day open (not subject to look-ahead bias)
EVENT_TRIGGERS = {
    "earnings_release",
    "sec_8k",
    "supply_chain_cascade",
    "news_sentiment",
    "bad_news_no_drop",
}

# Default triggers to exclude (weak performers)
DEFAULT_EXCLUDED_TRIGGERS = {
    "daily_breakout",
    "daily_macd_crossover",
}


# ---------------------------------------------------------------------------
# Capital-aware helpers
# ---------------------------------------------------------------------------
def close_expired_positions(
    current_date: date,
    open_positions: list[OpenPosition],
    cash: float,
) -> tuple[list[OpenPosition], float]:
    """Close positions whose precomputed exit_date <= current_date, return freed cash.

    Returns (still_open, updated_cash).
    """
    still_open: list[OpenPosition] = []
    for pos in open_positions:
        if pos.exit_date <= current_date:
            # Position is closed — return capital + PnL
            pnl_pct = float(pos.realized_pnl_pct) if pos.realized_pnl_pct is not None else 0.0
            gross_pnl = pos.position_size_dollars * (pnl_pct / 100.0)
            total_commissions = COMMISSION_PER_ORDER * 2
            total_slippage = pos.position_size_dollars * (SLIPPAGE_PCT / 100.0) * 2
            net_pnl = gross_pnl - total_commissions - total_slippage
            cash += pos.position_size_dollars + net_pnl
        else:
            still_open.append(pos)
    return still_open, cash


def try_open_position(
    cash: float,
    total_equity: float,
    open_positions: list[OpenPosition],
    max_positions: int,
    symbol: str,
) -> tuple[float, str | None]:
    """Check capital availability and slot limits.

    Returns (position_size_dollars, skip_reason).
    skip_reason is None if the position can be opened.
    """
    # Check position slot limit
    if len(open_positions) >= max_positions:
        return 0.0, "max_positions_reached"

    # Check if already in position for this symbol
    for pos in open_positions:
        if pos.symbol == symbol:
            return 0.0, "already_in_position"

    # Calculate position size: min(equity * 5%, available cash)
    desired_size = total_equity * POSITION_PCT
    position_size = min(desired_size, cash)

    if position_size < MIN_POSITION_DOLLARS:
        return 0.0, "insufficient_capital"

    return position_size, None


def _compute_exit_date(
    outcome: dict,
    entry_date: date,
    future_bars: list[tuple],
    max_hold_days: int,
) -> date:
    """Compute the exit date for a position based on outcome."""
    outcome_time = outcome.get("outcome_time")
    if outcome_time:
        if hasattr(outcome_time, "date"):
            return outcome_time.date()
        return outcome_time

    # NO_HIT: use last future bar date or entry + max_hold_days
    if future_bars:
        last_bar_time = future_bars[-1][0]
        if hasattr(last_bar_time, "date"):
            return last_bar_time.date()
        return last_bar_time

    return entry_date


async def run_daily_backtest(
    start_date: str,
    end_date: str,
    run_name: str,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    starting_capital: float = 10000.0,
    excluded_triggers: set[str] | None = None,
    max_positions: int = MAX_POSITIONS_DEFAULT,
    max_hold_days: int = MAX_HOLD_DAYS_DEFAULT,
) -> uuid.UUID:
    """
    Run daily trigger backtest with capital-aware position management.

    Triggers on the same date are batched and prioritized (HIGH > MEDIUM > LOW)
    before capital allocation. Trades are skipped when cash is insufficient or
    all position slots are full.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        run_name: Name for this backtest run
        sl_mult: Stop loss ATR multiplier
        tp_mult: Take profit ATR multiplier
        starting_capital: Starting capital for capital tracking
        excluded_triggers: Set of trigger types to skip (default: weak performers)
        max_positions: Maximum concurrent positions (default 20, use 999 for unlimited)
        max_hold_days: Force-close NO_HIT trades after N days (default 30, use 0 for unlimited)

    Returns:
        UUID of the backtest run
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
                        "sl_mult": sl_mult,
                        "tp_mult": tp_mult,
                        "position_sizing": "5% of capital",
                        "max_positions": max_positions,
                        "max_hold_days": max_hold_days,
                        "excluded_triggers": list(excluded_triggers),
                    }),
                ),
            )
        conn.commit()

    monitor = TriggerMonitor(backtest_mode=True)

    # Get all daily bars with indicators in range
    test_bars = get_daily_bars_to_test(start_date, end_date)
    total_bars = len(test_bars)
    logger.info(f"Starting daily backtest with {total_bars} bars (max_positions={max_positions}, max_hold_days={max_hold_days})...")

    # -----------------------------------------------------------------------
    # Capital-aware state
    # -----------------------------------------------------------------------
    cash = starting_capital
    open_positions: list[OpenPosition] = []
    equity_curve: list[tuple[date, float]] = []  # (date, total_equity)
    skipped_no_capital = 0
    skipped_max_positions = 0
    skipped_already_in_position = 0
    max_concurrent = 0
    concurrent_sum = 0
    concurrent_snapshots = 0

    triggers_found = 0
    bars_processed = 0
    skipped_symbols: set[str] = set()

    # Date-batched processing: collect pending triggers per date,
    # then process them all at once on date boundary.
    current_batch_date: date | None = None
    pending_triggers: list[PendingTrigger] = []

    def _total_equity() -> float:
        """Current total equity = cash + sum of open position sizes."""
        return cash + sum(p.position_size_dollars for p in open_positions)

    def _process_pending_batch() -> None:
        """Process a batch of pending triggers for one date.

        Sorts by priority, applies capital/slot checks, resolves outcomes,
        opens positions, inserts results.
        """
        nonlocal cash, skipped_no_capital, skipped_max_positions
        nonlocal skipped_already_in_position, max_concurrent

        if not pending_triggers:
            return

        # Sort by priority: HIGH > MEDIUM > LOW
        pending_triggers.sort(
            key=lambda pt: PRIORITY_ORDER.get(pt.trigger.priority, 99)
        )

        for pt in pending_triggers:
            trigger = pt.trigger
            check_time = datetime.combine(
                pt.check_date, datetime.min.time()
            ).replace(tzinfo=timezone.utc)

            # Capital / slot check
            total_eq = _total_equity()
            position_size, skip_reason = try_open_position(
                cash, total_eq, open_positions, max_positions, trigger.symbol
            )

            if skip_reason:
                # Record as SKIPPED
                trigger_detail = dict(trigger.details or {})
                trigger_detail["entry_date"] = pt.entry_date.isoformat()
                trigger_detail["skip_reason"] = skip_reason
                trigger_detail["capital_at_entry"] = round(cash, 2)

                insert_result(
                    build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=trigger.symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail=trigger_detail,
                        entry_price=pt.entry_price,
                        atr14=pt.atr14,
                        stop_loss=pt.stop_loss,
                        take_profit=pt.take_profit,
                        outcome="SKIPPED",
                        outcome_time=None,
                        bars_to_outcome=None,
                        max_favorable_pct=None,
                        max_adverse_pct=None,
                        realized_pnl_pct=None,
                        sl_mult=sl_mult,
                        tp_mult=tp_mult,
                    )
                )

                if skip_reason == "insufficient_capital":
                    skipped_no_capital += 1
                elif skip_reason == "max_positions_reached":
                    skipped_max_positions += 1
                elif skip_reason == "already_in_position":
                    skipped_already_in_position += 1
                continue

            # Truncate future_bars by max_hold_days
            future_bars = pt.future_bars
            if max_hold_days > 0:
                future_bars = future_bars[:max_hold_days]

            # Resolve outcome
            entry_datetime = datetime.combine(
                pt.entry_date, datetime.min.time()
            ).replace(tzinfo=timezone.utc)
            outcome = resolve_outcome(
                entry_datetime,
                pt.entry_price,
                pt.stop_loss,
                pt.take_profit,
                future_bars,
            )

            trigger_detail = dict(trigger.details or {})
            trigger_detail["entry_date"] = pt.entry_date.isoformat()
            trigger_detail["position_size_dollars"] = round(position_size, 2)
            trigger_detail["capital_at_entry"] = round(cash, 2)
            if outcome.get("same_bar_tie"):
                trigger_detail["same_bar_tie"] = True

            insert_result(
                build_result_row(
                    run_id=run_id,
                    run_name=run_name,
                    started_at=started_at,
                    symbol=trigger.symbol,
                    trigger_type=trigger.trigger_type,
                    trigger_priority=trigger.priority,
                    trigger_time=check_time,
                    trigger_detail=trigger_detail,
                    entry_price=pt.entry_price,
                    atr14=pt.atr14,
                    stop_loss=pt.stop_loss,
                    take_profit=pt.take_profit,
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

            # Open position: deduct cash, track position
            exit_date = _compute_exit_date(
                outcome, pt.entry_date, future_bars, max_hold_days
            )
            pos = OpenPosition(
                symbol=trigger.symbol,
                entry_date=pt.entry_date,
                exit_date=exit_date,
                entry_price=pt.entry_price,
                position_size_dollars=position_size,
                outcome=outcome["outcome"],
                realized_pnl_pct=outcome["realized_pnl_pct"],
            )
            open_positions.append(pos)
            cash -= position_size

            if len(open_positions) > max_concurrent:
                max_concurrent = len(open_positions)

        pending_triggers.clear()

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    for symbol, check_date in test_bars:
        bars_processed += 1
        if bars_processed % 1000 == 0:
            logger.info(
                f"Progress: {bars_processed}/{total_bars} bars "
                f"({bars_processed * 100 // total_bars}%), "
                f"{triggers_found} triggers, "
                f"{len(open_positions)} open positions, "
                f"cash=${cash:,.0f}"
            )

        # --- Date boundary processing ---
        if current_batch_date is not None and check_date != current_batch_date:
            # 1. Process previous date's pending triggers
            _process_pending_batch()

            # 2. Close expired positions → free cash
            open_positions, cash = close_expired_positions(
                check_date, open_positions, cash
            )

            # 3. Record equity snapshot
            total_eq = _total_equity()
            equity_curve.append((current_batch_date, total_eq))

            # 4. Track concurrent position stats
            concurrent_sum += len(open_positions)
            concurrent_snapshots += 1

        current_batch_date = check_date

        # Skip ETFs and index symbols
        if not is_stock(symbol):
            skipped_symbols.add(symbol)
            continue

        # Fetch daily bars for this symbol
        bars = fetch_daily_bars(symbol, check_date, limit=200)
        if len(bars) < 20:
            continue

        # Aggregate triggers from allowed sources
        triggers = []

        # Create check_time as datetime for trigger_monitor methods
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

        # 3. Volatility Compression (works with daily data)
        try:
            compression = await monitor.check_volatility_compression_trigger(symbol, check_time)
            if compression:
                triggers.append(compression)
        except Exception as e:
            logger.debug(f"{symbol}: Volatility compression trigger failed - {e}")

        if not triggers:
            continue

        # Filter out excluded triggers
        triggers = [t for t in triggers if t.trigger_type not in excluded_triggers]
        if not triggers:
            continue

        triggers_found += len(triggers)

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

            future_bars = fetch_future_daily_bars(symbol, entry_date)
            if not future_bars:
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

            # Add to pending batch for capital-aware processing
            pending_triggers.append(
                PendingTrigger(
                    trigger=trigger,
                    check_date=check_date,
                    entry_price=entry_price,
                    entry_date=entry_date,
                    atr14=atr14,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    future_bars=future_bars,
                )
            )

    # --- After loop: process final batch ---
    _process_pending_batch()

    # Force-close any remaining open positions at their exit dates
    if open_positions:
        logger.info(f"Force-closing {len(open_positions)} remaining open positions")
        # Sort by exit_date to process in order
        for pos in open_positions:
            pnl_pct = float(pos.realized_pnl_pct) if pos.realized_pnl_pct is not None else 0.0
            gross_pnl = pos.position_size_dollars * (pnl_pct / 100.0)
            total_commissions = COMMISSION_PER_ORDER * 2
            total_slippage = pos.position_size_dollars * (SLIPPAGE_PCT / 100.0) * 2
            net_pnl = gross_pnl - total_commissions - total_slippage
            cash += pos.position_size_dollars + net_pnl
        open_positions = []

    # Final equity snapshot
    if current_batch_date is not None:
        equity_curve.append((current_batch_date, cash))

    avg_concurrent = (concurrent_sum / concurrent_snapshots) if concurrent_snapshots > 0 else 0.0

    logger.info(f"Backtest loop complete: {bars_processed} bars, {triggers_found} triggers found")
    logger.info(
        f"Capital-aware stats: skipped_no_capital={skipped_no_capital}, "
        f"skipped_max_positions={skipped_max_positions}, "
        f"skipped_already_in_position={skipped_already_in_position}, "
        f"max_concurrent={max_concurrent}, avg_concurrent={avg_concurrent:.1f}"
    )
    if skipped_symbols:
        logger.info(f"Skipped {len(skipped_symbols)} ETF/index symbols: {sorted(skipped_symbols)}")

    # Calculate metrics and update run record
    logger.info("Calculating run metrics...")
    _update_run_metrics(
        run_id,
        started_at,
        starting_capital,
        loop_final_capital=cash,
        equity_curve=equity_curve,
        skipped_no_capital=skipped_no_capital,
        skipped_max_positions=skipped_max_positions,
        skipped_already_in_position=skipped_already_in_position,
        max_concurrent=max_concurrent,
        avg_concurrent=avg_concurrent,
    )
    logger.info(f"Run complete: {run_id}")

    return run_id


def _update_run_metrics(
    run_id: uuid.UUID,
    started_at: datetime,
    starting_capital: float,
    *,
    loop_final_capital: float | None = None,
    equity_curve: list[tuple[date, float]] | None = None,
    skipped_no_capital: int = 0,
    skipped_max_positions: int = 0,
    skipped_already_in_position: int = 0,
    max_concurrent: int = 0,
    avg_concurrent: float = 0.0,
) -> None:
    """Calculate and store aggregate metrics for the backtest run."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Update completed_at in result table
            cur.execute(
                """
                UPDATE trigger_backtest_result
                SET completed_at = %s
                WHERE run_id = %s AND completed_at IS NULL
                """,
                (datetime.now(timezone.utc), run_id),
            )

            # 2. Calculate aggregate metrics
            cur.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE outcome IN ('TP_HIT', 'SL_HIT', 'NO_HIT')) as executed,
                    COUNT(*) FILTER (WHERE outcome = 'TP_HIT') as wins,
                    SUM(realized_pnl_pct) FILTER (WHERE outcome NOT IN ('NO_DATA', 'ERROR', 'SKIPPED')) as total_pnl,
                    AVG(realized_pnl_pct) FILTER (WHERE outcome NOT IN ('NO_DATA', 'ERROR', 'SKIPPED')) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR', 'SKIPPED')
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

            # Post-hoc capital replay for verification
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

            current_capital = starting_capital
            peak_capital = starting_capital
            max_drawdown_pct = 0.0

            for trade_time, pnl_pct in trades:
                position_size = current_capital * POSITION_PCT

                if position_size < MIN_POSITION_DOLLARS:
                    continue

                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0
                gross_pnl_dollars = position_size * (pnl_pct / 100.0)

                total_commissions = COMMISSION_PER_ORDER * 2
                total_slippage = position_size * (SLIPPAGE_PCT / 100.0) * 2

                net_pnl_dollars = gross_pnl_dollars - total_commissions - total_slippage
                current_capital += net_pnl_dollars

                if current_capital > peak_capital:
                    peak_capital = current_capital

                if peak_capital > 0:
                    drawdown = ((peak_capital - current_capital) / peak_capital) * 100.0
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)

            posthoc_final = current_capital

            # Use loop's final capital as authoritative if available
            if loop_final_capital is not None:
                final_capital = loop_final_capital
                logger.info(
                    f"Capital check: loop=${loop_final_capital:,.2f}, "
                    f"posthoc=${posthoc_final:,.2f}, "
                    f"delta=${loop_final_capital - posthoc_final:+,.2f}"
                )
            else:
                final_capital = posthoc_final

            total_return_pct = ((final_capital - starting_capital) / starting_capital) * 100.0

            # Compute drawdown from equity curve if available
            if equity_curve:
                curve_peak = starting_capital
                curve_max_dd = 0.0
                for _, eq in equity_curve:
                    if eq > curve_peak:
                        curve_peak = eq
                    if curve_peak > 0:
                        dd = ((curve_peak - eq) / curve_peak) * 100.0
                        curve_max_dd = max(curve_max_dd, dd)
                max_drawdown_pct = curve_max_dd

            # 3. Calculate yearly performance
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

            # Calculate yearly capital performance with 5% position sizing
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

            # Group trades by year and calculate capital returns
            yearly_capital = {}
            year_start_capital = {}
            current_year = None
            year_capital = starting_capital

            for year, trigger_time, pnl_pct in all_trades:
                year = int(year)

                if current_year is None:
                    current_year = year
                    year_start_capital[year] = year_capital

                if year != current_year:
                    # Close out previous year
                    yearly_capital[current_year] = year_capital
                    current_year = year
                    year_start_capital[year] = year_capital

                pos_size = year_capital * POSITION_PCT
                if pos_size < MIN_POSITION_DOLLARS:
                    continue

                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0
                gross_pnl = pos_size * (pnl_pct / 100.0)
                # $2 commission + 0.3% slippage (0.15% * 2 sides)
                net_pnl = gross_pnl - 2.0 - (pos_size * 0.003)
                year_capital += net_pnl

            # Close out final year
            if current_year is not None:
                yearly_capital[current_year] = year_capital

            # Build yearly performance list
            yearly_performance = []
            for year, trades, wins, losses, total_pnl, avg_pnl in yearly_rows:
                year = int(year)
                win_rate = (wins / trades * 100) if trades > 0 else 0.0

                start_cap = year_start_capital.get(year, starting_capital)
                end_cap = yearly_capital.get(year, start_cap)
                year_return = ((end_cap - start_cap) / start_cap * 100) if start_cap > 0 else 0.0

                yearly_performance.append({
                    "year": year,
                    "trades": trades,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": round(float(win_rate), 1),
                    "avg_pnl_pct": round(float(avg_pnl or 0), 2),
                    "total_pnl_pct": round(float(total_pnl or 0), 2),
                    "start_capital": round(float(start_cap), 2),
                    "end_capital": round(float(end_cap), 2),
                    "return_pct": round(float(year_return), 1),
                })

            # 4. Find best/worst triggers
            cur.execute(
                """
                SELECT trigger_type, AVG(realized_pnl_pct) as avg_pnl
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR', 'SKIPPED')
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
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR', 'SKIPPED')
                GROUP BY trigger_type
                ORDER BY avg_pnl ASC
                LIMIT 1
                """,
                (run_id,),
            )
            worst_row = cur.fetchone()
            worst_trigger = f"{worst_row[0]} ({float(worst_row[1]):.2f}%)" if worst_row else None

            # 5. Aggregate per-trigger detail for JSON storage
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
                WHERE run_id = %s AND outcome NOT IN ('NO_DATA', 'ERROR', 'SKIPPED')
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

            # 6. Update run record with JSON details
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
                    parameters = trigger_backtest_run.parameters || %s
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
                    Json({
                        "skipped_no_capital": skipped_no_capital,
                        "skipped_max_positions": skipped_max_positions,
                        "skipped_already_in_position": skipped_already_in_position,
                        "max_concurrent": max_concurrent,
                        "avg_concurrent": round(avg_concurrent, 1),
                        "posthoc_final_capital": round(posthoc_final, 2),
                    }),
                    run_id,
                ),
            )
            conn.commit()


def configure_logging() -> None:
    """Configure logging for the backtest."""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler("logs/daily_trigger_backtest.log")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("eiqora_v2.live.candidate_selector").setLevel(logging.INFO)


def main() -> None:
    """CLI entry point for daily trigger backtest."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Daily trigger backtest")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--run-name", type=str, default="daily-backtest")
    parser.add_argument("--sl-mult", type=float, default=1.5, help="Stop loss ATR multiplier")
    parser.add_argument("--tp-mult", type=float, default=3.0, help="Take profit ATR multiplier")
    parser.add_argument("--starting-capital", type=float, default=10000.0)
    parser.add_argument("--max-positions", type=int, default=MAX_POSITIONS_DEFAULT,
                        help=f"Max concurrent positions (default: {MAX_POSITIONS_DEFAULT})")
    parser.add_argument("--max-hold-days", type=int, default=MAX_HOLD_DAYS_DEFAULT,
                        help=f"Force-close NO_HIT after N days (default: {MAX_HOLD_DAYS_DEFAULT}, 0=unlimited)")
    args = parser.parse_args()

    run_id = asyncio.run(
        run_daily_backtest(
            args.start_date,
            args.end_date,
            args.run_name,
            args.sl_mult,
            args.tp_mult,
            args.starting_capital,
            max_positions=args.max_positions,
            max_hold_days=args.max_hold_days,
        )
    )
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
