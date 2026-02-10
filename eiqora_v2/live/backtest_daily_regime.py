"""
Daily Regime-Filtered Trigger Backtest System

Extends the daily trigger backtest with a SPY-based market regime filter
(BULL / SIDEWAYS / BEAR) that adjusts position sizing and trigger selection
based on market conditions.

Regime Classification (using SPY daily indicators):
  BULL:     SPY close > 200 MA AND ADX > 20 AND +DI > -DI  -> $500/trade, all triggers
  SIDEWAYS: ADX < 20 OR SPY within ±2% of 200 MA           -> $250/trade, selective triggers
  BEAR:     SPY close < 200 MA AND ADX > 20 AND -DI > +DI  -> no trading
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
    _build_no_data_detail,
    EVENT_TRIGGERS,
    DEFAULT_EXCLUDED_TRIGGERS,
    configure_logging,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market Regime Filter
# ---------------------------------------------------------------------------

REGIME_POSITION_SIZE = {
    "BULL": 500.0,
    "SIDEWAYS": 250.0,
    "BEAR": 0.0,  # no trading
}

SIDEWAYS_ALLOWED_TRIGGERS = {
    "daily_rsi_oversold",
    "daily_order_block_retest",
}


def fetch_spy_regime_data(start_date: str, end_date: str) -> dict[date, dict]:
    """Pre-fetch SPY close, adx_14, plus_di, minus_di for entire date range.

    Also computes 200-day SMA from close prices.
    Returns: {date: {"close": float, "ma200": float, "adx": float,
                      "plus_di": float, "minus_di": float}}
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Fetch all SPY data up to end_date (extra history to seed the 200 MA)
            cur.execute(
                """
                SELECT date, close, adx_14, plus_di, minus_di
                FROM market_bar_daily
                WHERE symbol = 'SPY'
                  AND date <= %s
                ORDER BY date ASC
                """,
                (end_date,),
            )
            rows = cur.fetchall()

    if not rows:
        logger.warning("No SPY data found for regime calculation")
        return {}

    # Build close list and compute rolling 200-day SMA
    dates = [r[0] for r in rows]
    closes = [float(r[1]) if r[1] is not None else None for r in rows]
    adx_vals = [float(r[2]) if r[2] is not None else None for r in rows]
    plus_di_vals = [float(r[3]) if r[3] is not None else None for r in rows]
    minus_di_vals = [float(r[4]) if r[4] is not None else None for r in rows]

    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()

    result: dict[date, dict] = {}
    for i, d in enumerate(dates):
        if d < start_dt:
            continue

        # Compute 200-day SMA using closes[0..i]
        window = [c for c in closes[max(0, i - 199): i + 1] if c is not None]
        ma200 = sum(window) / len(window) if len(window) >= 100 else None

        result[d] = {
            "close": closes[i],
            "ma200": ma200,
            "adx": adx_vals[i],
            "plus_di": plus_di_vals[i],
            "minus_di": minus_di_vals[i],
        }

    return result


def classify_regime(spy_data: dict) -> str:
    """Classify a single day's regime based on SPY indicators.

    Returns: "BULL", "BEAR", or "SIDEWAYS"
    """
    close = spy_data.get("close")
    ma200 = spy_data.get("ma200")
    adx = spy_data.get("adx")
    plus_di = spy_data.get("plus_di")
    minus_di = spy_data.get("minus_di")

    # If missing data, default to SIDEWAYS (reduced risk)
    if close is None or ma200 is None or adx is None:
        return "SIDEWAYS"

    # Check proximity to 200 MA (within ±2%)
    pct_from_ma200 = abs(close - ma200) / ma200 * 100.0

    # SIDEWAYS: weak trend or price near 200 MA
    if adx < 20 or pct_from_ma200 < 2.0:
        return "SIDEWAYS"

    # BEAR: below 200 MA with strong downtrend
    if close < ma200 and minus_di is not None and plus_di is not None and minus_di > plus_di:
        return "BEAR"

    # BULL: above 200 MA with strong uptrend
    if close > ma200 and plus_di is not None and minus_di is not None and plus_di > minus_di:
        return "BULL"

    # Fallback: strong trend but DI doesn't confirm direction
    return "SIDEWAYS"


async def run_regime_backtest(
    start_date: str,
    end_date: str,
    run_name: str,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    starting_capital: float = 10000.0,
    excluded_triggers: set[str] | None = None,
) -> uuid.UUID:
    """
    Run daily trigger backtest with SPY regime filter.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        run_name: Name for this backtest run
        sl_mult: Stop loss ATR multiplier
        tp_mult: Take profit ATR multiplier
        starting_capital: Starting capital for capital tracking
        excluded_triggers: Set of trigger types to skip (default: weak performers)

    Returns:
        UUID of the backtest run
    """
    if excluded_triggers is None:
        excluded_triggers = DEFAULT_EXCLUDED_TRIGGERS
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    # Pre-fetch SPY regime data for the entire date range
    logger.info("Fetching SPY regime data...")
    spy_regime_data = fetch_spy_regime_data(start_date, end_date)
    logger.info(f"SPY regime data loaded for {len(spy_regime_data)} trading days")

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
                        "mode": "regime-filtered",
                        "sl_mult": sl_mult,
                        "tp_mult": tp_mult,
                        "position_size": "regime-based",
                        "regime_position_sizes": REGIME_POSITION_SIZE,
                        "sideways_allowed_triggers": list(SIDEWAYS_ALLOWED_TRIGGERS),
                        "excluded_triggers": list(excluded_triggers),
                    }),
                ),
            )
        conn.commit()

    monitor = TriggerMonitor(backtest_mode=True)

    # Get all daily bars with indicators in range
    test_bars = get_daily_bars_to_test(start_date, end_date)
    total_bars = len(test_bars)
    logger.info(f"Starting regime backtest with {total_bars} bars...")

    active_positions = {}  # symbol -> date (exclusive end date of trade)
    triggers_found = 0
    bars_processed = 0
    regime_day_counts: dict[str, int] = {"BULL": 0, "SIDEWAYS": 0, "BEAR": 0}
    regime_dates_seen: set[date] = set()

    for symbol, check_date in test_bars:
        bars_processed += 1
        if bars_processed % 1000 == 0:
            logger.info(f"Progress: {bars_processed}/{total_bars} bars ({bars_processed*100//total_bars}%), {triggers_found} triggers found")

        # Classify regime for this date (count each date once)
        spy_day = spy_regime_data.get(check_date)
        regime = classify_regime(spy_day) if spy_day else "SIDEWAYS"

        if check_date not in regime_dates_seen:
            regime_dates_seen.add(check_date)
            regime_day_counts[regime] += 1

        # BEAR regime: skip all triggers
        if regime == "BEAR":
            continue

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

        # SIDEWAYS regime: only allow strongest triggers
        if regime == "SIDEWAYS":
            triggers = [t for t in triggers if t.trigger_type in SIDEWAYS_ALLOWED_TRIGGERS]
            if not triggers:
                continue

        # Determine position size based on regime
        position_size = REGIME_POSITION_SIZE[regime]

        triggers_found += len(triggers)
        trade_end_date = None

        for trigger in triggers:
            logger.info(format_trigger_log(symbol, trigger.trigger_type, check_date))

            # Determine entry price strategy
            is_event_trigger = trigger.trigger_type in EVENT_TRIGGERS

            if is_event_trigger:
                # Event triggers: enter at same day's open
                entry_price = fetch_same_day_open(symbol, check_date)
                entry_date = check_date
            else:
                # Technical triggers: enter at NEXT day's open (no look-ahead bias)
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

            # Get ATR14 from the trigger date (same day as signal)
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

            # Fetch future bars from entry date for outcome resolution
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

            # Resolve outcome using daily bars
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
            trigger_detail["regime"] = regime
            trigger_detail["position_size"] = position_size
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

    logger.info(f"Backtest loop complete: {bars_processed} bars, {triggers_found} triggers found")
    logger.info(f"Regime days: BULL={regime_day_counts['BULL']}, SIDEWAYS={regime_day_counts['SIDEWAYS']}, BEAR={regime_day_counts['BEAR']}")

    # Calculate metrics and update run record
    logger.info("Calculating run metrics...")
    _update_run_metrics(run_id, started_at, starting_capital, regime_day_counts)
    logger.info(f"Run complete: {run_id}")

    return run_id


def _update_run_metrics(
    run_id: uuid.UUID,
    started_at: datetime,
    starting_capital: float,
    regime_day_counts: dict[str, int],
) -> None:
    """Calculate and store aggregate metrics for the regime backtest run."""
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

            # Calculate capital tracking with regime-based position sizing
            cur.execute(
                """
                SELECT trigger_time, realized_pnl_pct, trigger_detail
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome IN ('TP_HIT', 'SL_HIT')
                ORDER BY trigger_time ASC
                """,
                (run_id,),
            )
            trades = cur.fetchall()

            COMMISSION_PER_ORDER = 1.00
            SLIPPAGE_PCT = 0.15  # 0.15% per side for daily open entries

            current_capital = starting_capital
            peak_capital = starting_capital
            max_drawdown_pct = 0.0

            for trade_time, pnl_pct, trigger_detail in trades:
                detail = trigger_detail if isinstance(trigger_detail, dict) else {}
                pos_size = float(detail.get("position_size", 500.0))

                if current_capital < pos_size:
                    continue

                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0
                gross_pnl_dollars = pos_size * (pnl_pct / 100.0)

                total_commissions = COMMISSION_PER_ORDER * 2
                total_slippage = pos_size * (SLIPPAGE_PCT / 100.0) * 2

                net_pnl_dollars = gross_pnl_dollars - total_commissions - total_slippage
                current_capital += net_pnl_dollars

                if current_capital > peak_capital:
                    peak_capital = current_capital

                if peak_capital > 0:
                    drawdown = ((peak_capital - current_capital) / peak_capital) * 100.0
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)

            final_capital = current_capital
            total_return_pct = ((final_capital - starting_capital) / starting_capital) * 100.0

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

            # Calculate yearly capital performance with regime-based sizing
            cur.execute(
                """
                SELECT
                    EXTRACT(YEAR FROM trigger_time) as year,
                    trigger_time,
                    realized_pnl_pct,
                    trigger_detail
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

            for year, trigger_time, pnl_pct, trigger_detail in all_trades:
                year = int(year)
                detail = trigger_detail if isinstance(trigger_detail, dict) else {}
                pos_size = float(detail.get("position_size", 500.0))

                if current_year is None:
                    current_year = year
                    year_start_capital[year] = year_capital

                if year != current_year:
                    yearly_capital[current_year] = year_capital
                    current_year = year
                    year_start_capital[year] = year_capital

                if year_capital < pos_size:
                    continue

                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0
                gross_pnl = pos_size * (pnl_pct / 100.0)
                net_pnl = gross_pnl - 2.0 - (pos_size * 0.003)
                year_capital += net_pnl

            if current_year is not None:
                yearly_capital[current_year] = year_capital

            # Build yearly performance list
            yearly_performance = []
            for year, yr_trades, wins, losses, total_pnl, avg_pnl in yearly_rows:
                year = int(year)
                win_rate = (wins / yr_trades * 100) if yr_trades > 0 else 0.0

                start_cap = year_start_capital.get(year, starting_capital)
                end_cap = yearly_capital.get(year, start_cap)
                year_return = ((end_cap - start_cap) / start_cap * 100) if start_cap > 0 else 0.0

                yearly_performance.append({
                    "year": year,
                    "trades": yr_trades,
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

            # 6. Compute regime breakdown stats
            regime_breakdown = []
            for regime_name in ("BULL", "SIDEWAYS", "BEAR"):
                regime_days = regime_day_counts.get(regime_name, 0)
                regime_trades = 0
                regime_wins = 0
                regime_gross_pnl = 0.0
                for _tt, pnl_pct, t_detail in trades:
                    detail = t_detail if isinstance(t_detail, dict) else {}
                    if detail.get("regime") == regime_name:
                        regime_trades += 1
                        pnl_val = float(pnl_pct) if pnl_pct is not None else 0.0
                        pos_size = float(detail.get("position_size", 500.0))
                        regime_gross_pnl += pos_size * (pnl_val / 100.0)
                        if pnl_val > 0:
                            regime_wins += 1

                regime_wr = (regime_wins / regime_trades * 100) if regime_trades > 0 else 0.0
                regime_breakdown.append({
                    "regime": regime_name,
                    "days": regime_days,
                    "trades": regime_trades,
                    "wins": regime_wins,
                    "win_rate": round(regime_wr, 1),
                    "est_pnl": round(regime_gross_pnl, 2),
                })

            # 7. Update run record with JSON details
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
                    Json({"regime_breakdown": regime_breakdown}),
                    run_id,
                ),
            )
            conn.commit()


def main() -> None:
    """CLI entry point for regime-filtered daily trigger backtest."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Regime-filtered daily trigger backtest")
    parser.add_argument("--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--run-name", type=str, default="regime-backtest")
    parser.add_argument("--sl-mult", type=float, default=1.5, help="Stop loss ATR multiplier")
    parser.add_argument("--tp-mult", type=float, default=3.0, help="Take profit ATR multiplier")
    parser.add_argument("--starting-capital", type=float, default=10000.0)
    args = parser.parse_args()

    run_id = asyncio.run(
        run_regime_backtest(
            args.start_date,
            args.end_date,
            args.run_name,
            args.sl_mult,
            args.tp_mult,
            args.starting_capital,
        )
    )
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
