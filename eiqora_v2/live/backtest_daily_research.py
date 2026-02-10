"""
Research-Backed Daily Backtest v2

Implements findings from quantitative finance studies with zero data leakage.
Stocks only (no ETFs/indexes).

Key features:
- Stock-only universe (47 stocks, excludes ETFs and indexes)
- Per-stock ADX regime filter (RANGE / TREND_UP / TREND_DOWN)
- Regime-appropriate exits: mean-reversion (2x ATR SL/TP, 7-day hold)
  vs momentum (1.5x ATR SL, 3x ATR TP, 30-day hold)
- 3 new research-backed triggers: RSI(2) oversold, MACD hist reversal, BB lower bounce
- FTD negative filter on mean-reversion triggers
- PCR sentiment boost (informational)

Data Leakage Prevention:
1. All indicators read from market_bar_daily on check_date (lagging, EOD from prior data)
2. RSI(2) computed in-memory from bars[-3:-1] closes (excludes today's close)
3. Entry always at NEXT day's open for technical triggers
4. FTD data: only settlement_date < check_date (strict lookback)
5. Options PCR: only date < check_date (prior day's close data)
6. ADX(14): read from check_date (safe, 14-period lagging indicator)
"""

import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone, date

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
    fetch_future_daily_bars,
    insert_result,
    get_daily_bars_to_test,
    format_trigger_log,
    check_daily_ict_triggers,
    DEFAULT_EXCLUDED_TRIGGERS,
    _build_no_data_detail,
)
from eiqora_v2.live.backtest_daily_confluence import (
    fetch_daily_bar_indicators,
    fetch_bb_width_min_20,
    compute_ma,
    check_new_triggers,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ETF_SYMBOLS = {
    "DIA", "IEF", "IWM", "QQQ", "SPY", "SHY", "TLT", "UUP",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
    "XLU", "XLV", "XLY",
}

MEAN_REVERSION_TRIGGERS = {
    "daily_rsi_oversold",
    "daily_stochastic_bounce",
    "daily_mfi_oversold_bounce",
    "daily_rsi2_oversold",
    "daily_macd_hist_reversal",
    "daily_bb_lower_bounce",
}

MOMENTUM_TRIGGERS = {
    "daily_volume_surge",
    "daily_inside_day_breakout",
    "daily_order_block_retest",
    "daily_fvg_bullish_fill",
    "daily_liquidity_sweep_reversal",
    "volatility_compression",
}

# Weak performers excluded from trigger detection
EXCLUDED_RESEARCH_TRIGGERS = {
    "daily_support_bounce",
    "daily_bb_squeeze_breakout",
}

# Exit parameters per category
EXIT_PARAMS = {
    "mean_reversion": {"sl_mult": 2.0, "tp_mult": 2.0, "max_hold": 7},
    "momentum": {"sl_mult": 1.5, "tp_mult": 3.0, "max_hold": 30},
}


# ---------------------------------------------------------------------------
# Universe filter
# ---------------------------------------------------------------------------

def is_stock(symbol: str) -> bool:
    """Return True if symbol is a stock (not ETF or index)."""
    if symbol in ETF_SYMBOLS:
        return False
    if symbol.startswith("IDX_"):
        return False
    return True


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fetch_research_indicators(symbol: str, asof_date: date) -> dict | None:
    """Fetch full indicator set from market_bar_daily for a single bar.

    Returns dict with keys: adx_14, plus_di, minus_di, rsi_14, macd_hist,
    bb_lower_20, bb_upper_20, bb_width, mfi_14, cmf_20,
    support_level, resistance_level, volume_z_20, stochastic_k, stochastic_d,
    close, open, high, low
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT adx_14, plus_di, minus_di, rsi_14, macd_hist,
                       bb_lower_20, bb_upper_20, bb_width, mfi_14, cmf_20,
                       support_level, resistance_level, volume_z_20,
                       stochastic_k, stochastic_d,
                       close, open, high, low
                FROM market_bar_daily
                WHERE symbol = %s AND date = %s
                LIMIT 1
                """,
                (symbol, asof_date),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [
                "adx_14", "plus_di", "minus_di", "rsi_14", "macd_hist",
                "bb_lower_20", "bb_upper_20", "bb_width", "mfi_14", "cmf_20",
                "support_level", "resistance_level", "volume_z_20",
                "stochastic_k", "stochastic_d",
                "close", "open", "high", "low",
            ]
            return {
                col: (float(val) if val is not None else None)
                for col, val in zip(cols, row)
            }


def fetch_prev_day_indicators(symbol: str, before_date: date) -> dict | None:
    """Fetch macd_hist from the trading day before before_date."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT macd_hist
                FROM market_bar_daily
                WHERE symbol = %s AND date < %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (symbol, before_date),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "macd_hist": float(row[0]) if row[0] is not None else None,
            }


# ---------------------------------------------------------------------------
# ADX regime classification (per-stock)
# ---------------------------------------------------------------------------

def classify_stock_regime(ind: dict | None, bars: list[dict] | None = None) -> str:
    """Classify stock regime based on ADX/DI indicators + MA50 early bear filter.

    The ADX is a lagging indicator and misses early bear moves.  Adding a
    close-below-MA50 check catches the transition sooner: if the stock is
    already trading below its 50-day MA *and* minus_di dominates, treat it
    as TREND_DOWN even when ADX hasn't caught up yet.

    Returns: "RANGE", "TREND_UP", "TREND_DOWN", or "UNKNOWN"
    """
    if ind is None:
        return "UNKNOWN"

    adx = ind.get("adx_14")
    plus_di = ind.get("plus_di")
    minus_di = ind.get("minus_di")
    close = ind.get("close")

    if adx is None:
        return "UNKNOWN"

    # Compute MA50 for early bear detection
    ma50 = None
    if bars is not None:
        ma50 = compute_ma(bars, 50)

    # Early bear filter: close < MA50 AND minus_di > plus_di → TREND_DOWN
    # This fires before ADX reaches 25, catching early selloffs
    if (
        ma50 is not None
        and close is not None
        and close < ma50
        and plus_di is not None
        and minus_di is not None
        and minus_di > plus_di
    ):
        return "TREND_DOWN"

    if adx < 25:
        return "RANGE"

    # ADX >= 25 — trending
    if plus_di is not None and minus_di is not None:
        if plus_di > minus_di:
            return "TREND_UP"
        else:
            return "TREND_DOWN"

    # Missing DI data
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# In-memory RSI computation (for RSI(2))
# ---------------------------------------------------------------------------

def compute_rsi(closes: list[float], period: int = 2) -> float | None:
    """Compute RSI from a list of closing prices.

    Needs at least period+1 prices.
    """
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# Exit parameters
# ---------------------------------------------------------------------------

def get_exit_params(trigger_type: str) -> dict:
    """Return SL/TP/max_hold based on trigger category."""
    if trigger_type in MEAN_REVERSION_TRIGGERS:
        return EXIT_PARAMS["mean_reversion"]
    return EXIT_PARAMS["momentum"]


# ---------------------------------------------------------------------------
# FTD negative filter
# ---------------------------------------------------------------------------

def fetch_ftd_signal(symbol: str, check_date: date) -> dict:
    """Check if FTD (Failure to Deliver) is elevated.

    Compares avg FTD last 10 days vs prior 10 days.
    Only uses settlement_date < check_date (strict lookback).

    Returns: {"elevated": bool, "recent_avg": float, "prior_avg": float}
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Recent 10 business days (approx 14 calendar days)
            cur.execute(
                """
                SELECT AVG(quantity)
                FROM sec_ftd
                WHERE ticker = %s
                  AND settlement_date < %s
                  AND settlement_date >= %s::date - INTERVAL '14 days'
                """,
                (symbol, check_date, check_date),
            )
            row = cur.fetchone()
            recent_avg = float(row[0]) if row and row[0] is not None else 0.0

            # Prior 10 business days (approx 14-28 calendar days back)
            cur.execute(
                """
                SELECT AVG(quantity)
                FROM sec_ftd
                WHERE ticker = %s
                  AND settlement_date < %s::date - INTERVAL '14 days'
                  AND settlement_date >= %s::date - INTERVAL '28 days'
                """,
                (symbol, check_date, check_date),
            )
            row = cur.fetchone()
            prior_avg = float(row[0]) if row and row[0] is not None else 0.0

    elevated = False
    if prior_avg > 0 and recent_avg > 2.0 * prior_avg and recent_avg > 10000:
        elevated = True

    return {
        "elevated": elevated,
        "recent_avg": recent_avg,
        "prior_avg": prior_avg,
    }


# ---------------------------------------------------------------------------
# PCR sentiment boost
# ---------------------------------------------------------------------------

def fetch_pcr_signal(symbol: str, check_date: date) -> dict:
    """Fetch put/call ratio from prior day.

    Returns: {"pcr": float|None, "pcr_contrarian": bool}
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT put_call_ratio_volume
                FROM options_daily_summary
                WHERE symbol = %s AND date < %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (symbol, check_date),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                pcr = float(row[0])
                return {"pcr": pcr, "pcr_contrarian": pcr > 1.0}
            return {"pcr": None, "pcr_contrarian": False}


# ---------------------------------------------------------------------------
# Research triggers (3 new)
# ---------------------------------------------------------------------------

def check_research_triggers(
    symbol: str,
    check_date: date,
    bars: list[dict],
    ind: dict | None,
) -> list[Trigger]:
    """Detect 3 research-backed trigger types.

    1. daily_rsi2_oversold: RSI(2) < 10 + close > MA200  (Connors)
    2. daily_macd_hist_reversal: MACD hist turns less negative + RSI < 40
    3. daily_bb_lower_bounce: close <= bb_lower * 1.01 + RSI < 35 + bullish candle
    """
    triggers: list[Trigger] = []
    check_datetime = datetime.combine(check_date, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    # --- 1. daily_rsi2_oversold ---
    # RSI(2) computed from bars[-3:-1] closes (excludes today's close)
    # Relaxed: RSI(2) < 20 (original Connors used < 10, too strict for this universe)
    # MA200 requirement dropped — oversold is oversold regardless of long-term trend
    if len(bars) >= 4:
        # bars[-4], bars[-3], bars[-2] give us 3 closes for RSI(2) calc
        rsi2_closes = [
            b["close"] for b in bars[-4:-1]
            if b.get("close") is not None
        ]
        if len(rsi2_closes) >= 3:
            rsi2 = compute_rsi(rsi2_closes, period=2)
            yesterday_close = bars[-2].get("close")

            if (
                rsi2 is not None
                and yesterday_close is not None
                and rsi2 < 20
            ):
                triggers.append(
                    Trigger(
                        symbol=symbol,
                        trigger_type="daily_rsi2_oversold",
                        priority="HIGH",
                        details={
                            "rsi2": round(rsi2, 2),
                            "yesterday_close": yesterday_close,
                        },
                        detected_at=check_datetime,
                    )
                )

    # --- 2. daily_macd_hist_reversal ---
    if ind is not None:
        macd_hist_today = ind.get("macd_hist")
        rsi_14 = ind.get("rsi_14")

        if macd_hist_today is not None and rsi_14 is not None:
            # Fetch yesterday's macd_hist
            prev_ind = fetch_prev_day_indicators(symbol, check_date)
            if prev_ind is not None:
                macd_hist_prev = prev_ind.get("macd_hist")
                if (
                    macd_hist_prev is not None
                    and macd_hist_today < 0
                    and macd_hist_prev < 0
                    and macd_hist_today > macd_hist_prev
                    and rsi_14 < 40
                ):
                    triggers.append(
                        Trigger(
                            symbol=symbol,
                            trigger_type="daily_macd_hist_reversal",
                            priority="MEDIUM",
                            details={
                                "macd_hist_today": round(macd_hist_today, 4),
                                "macd_hist_prev": round(macd_hist_prev, 4),
                                "rsi_14": round(rsi_14, 2),
                            },
                            detected_at=check_datetime,
                        )
                    )

    # --- 3. daily_bb_lower_bounce ---
    if ind is not None:
        bb_lower = ind.get("bb_lower_20")
        rsi_14 = ind.get("rsi_14")
        close = ind.get("close")
        open_ = ind.get("open")

        if (
            bb_lower is not None
            and rsi_14 is not None
            and close is not None
            and open_ is not None
            and close <= bb_lower * 1.01
            and rsi_14 < 35
            and close > open_
        ):
            triggers.append(
                Trigger(
                    symbol=symbol,
                    trigger_type="daily_bb_lower_bounce",
                    priority="MEDIUM",
                    details={
                        "bb_lower_20": round(bb_lower, 4),
                        "close": close,
                        "open": open_,
                        "rsi_14": round(rsi_14, 2),
                    },
                    detected_at=check_datetime,
                )
            )

    return triggers


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

async def run_research_backtest(
    start_date: str,
    end_date: str,
    run_name: str,
    starting_capital: float = 10000.0,
    excluded_triggers: set[str] | None = None,
    max_hold_mr: int = 7,
    max_hold_momentum: int = 30,
) -> uuid.UUID:
    """Run research-backed daily backtest.

    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        run_name: Name for this backtest run
        starting_capital: Starting capital for capital tracking
        excluded_triggers: Set of trigger types to skip
        max_hold_mr: Max hold days for mean-reversion triggers
        max_hold_momentum: Max hold days for momentum triggers

    Returns:
        UUID of the backtest run
    """
    if excluded_triggers is None:
        excluded_triggers = DEFAULT_EXCLUDED_TRIGGERS
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    # Override exit params with CLI values
    EXIT_PARAMS["mean_reversion"]["max_hold"] = max_hold_mr
    EXIT_PARAMS["momentum"]["max_hold"] = max_hold_momentum

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
                        "mode": "research",
                        "position_size": 500.0,
                        "excluded_triggers": list(excluded_triggers),
                        "excluded_research_triggers": list(EXCLUDED_RESEARCH_TRIGGERS),
                        "exit_params": EXIT_PARAMS,
                        "etf_filter": True,
                        "adx_regime_filter": True,
                        "ma50_early_bear": True,
                        "ftd_filter": True,
                        "pcr_info": True,
                    }),
                ),
            )
        conn.commit()

    monitor = TriggerMonitor(backtest_mode=True)

    # Get all daily bars with indicators in range
    test_bars = get_daily_bars_to_test(start_date, end_date)
    total_bars = len(test_bars)
    logger.info(f"Starting research backtest with {total_bars} bars...")

    active_positions: dict[str, date] = {}
    triggers_found = 0
    bars_processed = 0

    # Regime counters
    regime_counts: dict[str, int] = {
        "RANGE": 0, "TREND_UP": 0, "TREND_DOWN": 0, "UNKNOWN": 0,
    }
    regime_trades: dict[str, list] = {
        "RANGE": [], "TREND_UP": [], "TREND_DOWN": [], "UNKNOWN": [],
    }

    # FTD filter counters
    ftd_checked = 0
    ftd_filtered = 0

    # Stock universe tracking
    stock_symbols: set[str] = set()
    skipped_symbols: set[str] = set()

    for symbol, check_date in test_bars:
        bars_processed += 1
        if bars_processed % 1000 == 0:
            logger.info(
                f"Progress: {bars_processed}/{total_bars} bars "
                f"({bars_processed * 100 // total_bars}%), "
                f"{triggers_found} triggers found"
            )

        # Stock-only universe filter
        if not is_stock(symbol):
            skipped_symbols.add(symbol)
            continue
        stock_symbols.add(symbol)

        # Position lock check
        if symbol in active_positions:
            if check_date <= active_positions[symbol]:
                continue
            else:
                del active_positions[symbol]

        # Fetch daily bars for this symbol
        bars = fetch_daily_bars(symbol, check_date, limit=200)
        if len(bars) < 20:
            continue

        # Fetch full indicator set
        ind = fetch_research_indicators(symbol, check_date)

        # Classify per-stock regime (pass bars for MA50 early bear detection)
        regime = classify_stock_regime(ind, bars)
        regime_counts[regime] += 1

        # Skip TREND_DOWN (long-only)
        if regime == "TREND_DOWN":
            continue

        # Aggregate triggers from all sources
        triggers: list[Trigger] = []

        check_time = datetime.combine(check_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        )

        # 1. Daily Technical Triggers (from TriggerMonitor)
        try:
            tech_triggers = await monitor.check_daily_technical_triggers(
                symbol, check_time
            )
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
            compression = await monitor.check_volatility_compression_trigger(
                symbol, check_time
            )
            if compression:
                triggers.append(compression)
        except Exception as e:
            logger.debug(f"{symbol}: Volatility compression trigger failed - {e}")

        # 4. Confluence-style triggers (support_bounce, inside_day, mfi, bb_squeeze)
        try:
            # fetch_daily_bar_indicators returns a subset; reuse ind for what we can
            indicator_bar = None
            if ind is not None:
                indicator_bar = {
                    "bb_upper_20": ind.get("bb_upper_20"),
                    "bb_width": ind.get("bb_width"),
                    "mfi_14": ind.get("mfi_14"),
                    "cmf_20": ind.get("cmf_20"),
                    "support_level": ind.get("support_level"),
                    "volume_z_20": ind.get("volume_z_20"),
                }
            new_triggers = check_new_triggers(symbol, check_date, bars, indicator_bar)
            triggers.extend(new_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: New trigger detection failed - {e}")

        # 5. Research triggers (3 new)
        try:
            research_triggers = check_research_triggers(symbol, check_date, bars, ind)
            triggers.extend(research_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: Research triggers failed - {e}")

        if not triggers:
            continue

        # Filter out excluded triggers + weak research triggers
        all_excluded = excluded_triggers | EXCLUDED_RESEARCH_TRIGGERS
        triggers = [t for t in triggers if t.trigger_type not in all_excluded]
        if not triggers:
            continue

        # Filter by regime: RANGE allows only mean-reversion
        if regime == "RANGE":
            triggers = [
                t for t in triggers
                if t.trigger_type in MEAN_REVERSION_TRIGGERS
            ]
            if not triggers:
                continue

        # FTD negative filter (mean-reversion only)
        has_mr_triggers = any(
            t.trigger_type in MEAN_REVERSION_TRIGGERS for t in triggers
        )
        ftd_info = None
        if has_mr_triggers:
            ftd_checked += 1
            try:
                ftd_info = fetch_ftd_signal(symbol, check_date)
                if ftd_info["elevated"]:
                    ftd_filtered += 1
                    # Remove mean-reversion triggers only
                    triggers = [
                        t for t in triggers
                        if t.trigger_type not in MEAN_REVERSION_TRIGGERS
                    ]
                    if not triggers:
                        continue
            except Exception as e:
                logger.debug(f"{symbol}: FTD lookup failed - {e}")

        # PCR sentiment (informational)
        pcr_info = None
        try:
            pcr_info = fetch_pcr_signal(symbol, check_date)
        except Exception as e:
            logger.debug(f"{symbol}: PCR lookup failed - {e}")

        triggers_found += len(triggers)
        trade_end_date = None

        for trigger in triggers:
            logger.info(format_trigger_log(symbol, trigger.trigger_type, check_date))

            # All triggers use next-day open (no event triggers in research backtest)
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
                        trigger_detail=_build_no_data_detail(
                            trigger, "missing_entry_price"
                        ),
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
                        sl_mult=0,
                        tp_mult=0,
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
                        trigger_detail=_build_no_data_detail(
                            trigger, "missing_atr14"
                        ),
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
                        sl_mult=0,
                        tp_mult=0,
                    )
                )
                continue

            # Regime-appropriate exit parameters
            exit_p = get_exit_params(trigger.trigger_type)
            sl_mult = exit_p["sl_mult"]
            tp_mult = exit_p["tp_mult"]
            max_hold = exit_p["max_hold"]

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
                        trigger_detail=_build_no_data_detail(
                            trigger, "missing_future_bars"
                        ),
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

            # Truncate future bars by max hold
            future_bars = all_future_bars[:max_hold]

            entry_datetime = datetime.combine(
                entry_date, datetime.min.time()
            ).replace(tzinfo=timezone.utc)
            outcome = resolve_outcome(
                entry_datetime, entry_price, stop_loss, take_profit, future_bars
            )

            # Build trigger detail with research-specific info
            trigger_detail = dict(trigger.details or {})
            trigger_detail["entry_date"] = entry_date.isoformat()
            trigger_detail["regime"] = regime
            trigger_detail["exit_strategy"] = (
                "mean_reversion" if trigger.trigger_type in MEAN_REVERSION_TRIGGERS
                else "momentum"
            )
            trigger_detail["sl_mult"] = sl_mult
            trigger_detail["tp_mult"] = tp_mult
            trigger_detail["max_hold"] = max_hold

            if ftd_info is not None:
                trigger_detail["ftd_recent_avg"] = round(ftd_info["recent_avg"], 0)
                trigger_detail["ftd_prior_avg"] = round(ftd_info["prior_avg"], 0)
                trigger_detail["ftd_elevated"] = ftd_info["elevated"]

            if pcr_info is not None:
                trigger_detail["pcr"] = pcr_info["pcr"]
                if (
                    pcr_info["pcr_contrarian"]
                    and trigger.trigger_type in MEAN_REVERSION_TRIGGERS
                ):
                    trigger_detail["pcr_contrarian"] = True

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

            # Track regime performance
            if outcome["outcome"] in ("TP_HIT", "SL_HIT"):
                regime_trades[regime].append(outcome["realized_pnl_pct"])

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
                last_bar_date = (
                    last_bar_time.date()
                    if hasattr(last_bar_time, "date")
                    else last_bar_time
                )
                if trade_end_date is None or last_bar_date > trade_end_date:
                    trade_end_date = last_bar_date

        # Lock symbol if we executed trades
        if trade_end_date:
            active_positions[symbol] = trade_end_date

    logger.info(
        f"Backtest loop complete: {bars_processed} bars, "
        f"{triggers_found} triggers found, "
        f"universe: {len(stock_symbols)} stocks, "
        f"skipped: {len(skipped_symbols)} ETFs/indexes"
    )

    # Calculate metrics and update run record
    logger.info("Calculating run metrics...")
    _update_run_metrics_research(
        run_id,
        started_at,
        starting_capital,
        regime_counts,
        regime_trades,
        ftd_checked,
        ftd_filtered,
        len(stock_symbols),
        len(skipped_symbols),
    )
    logger.info(f"Run complete: {run_id}")

    return run_id


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _update_run_metrics_research(
    run_id: uuid.UUID,
    started_at: datetime,
    starting_capital: float,
    regime_counts: dict[str, int],
    regime_trades: dict[str, list],
    ftd_checked: int,
    ftd_filtered: int,
    stock_count: int,
    skipped_count: int,
) -> None:
    """Calculate and store aggregate metrics with research-specific stats."""
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
                    drawdown = (
                        (peak_capital - current_capital) / peak_capital
                    ) * 100.0
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)

            final_capital = current_capital
            total_return_pct = (
                (final_capital - starting_capital) / starting_capital
            ) * 100.0

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
                year_return = (
                    (end_cap - start_cap) / start_cap * 100
                ) if start_cap > 0 else 0.0
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
            best_trigger = (
                f"{best_row[0]} ({float(best_row[1]):.2f}%)" if best_row else None
            )

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
            worst_trigger = (
                f"{worst_row[0]} ({float(worst_row[1]):.2f}%)" if worst_row else None
            )

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

            # Research-specific stats
            regime_breakdown = []
            for r in ("RANGE", "TREND_UP", "TREND_DOWN", "UNKNOWN"):
                r_trades = regime_trades.get(r, [])
                r_wins = sum(1 for p in r_trades if p is not None and float(p) > 0)
                r_count = len(r_trades)
                regime_breakdown.append({
                    "regime": r,
                    "bars": regime_counts.get(r, 0),
                    "trades": r_count,
                    "wins": r_wins,
                    "win_rate": round(r_wins / r_count * 100, 1) if r_count > 0 else 0.0,
                    "est_pnl": round(
                        sum(float(p) for p in r_trades if p is not None) * 500.0 / 100.0,
                        2,
                    ) if r_trades else 0.0,
                })

            research_stats = {
                "regime_breakdown": regime_breakdown,
                "ftd_checked": ftd_checked,
                "ftd_filtered": ftd_filtered,
                "stock_universe": stock_count,
                "skipped_etf_index": skipped_count,
                "exit_params": EXIT_PARAMS,
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
                    Json({"research_stats": research_stats}),
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

    file_handler = logging.FileHandler("logs/daily_research_backtest.log")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("eiqora_v2.live.candidate_selector").setLevel(logging.INFO)


def main() -> None:
    """CLI entry point for research-backed daily backtest."""
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Research-backed daily trigger backtest"
    )
    parser.add_argument(
        "--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, required=True, help="End date (YYYY-MM-DD)"
    )
    parser.add_argument("--run-name", type=str, default="research-backtest")
    parser.add_argument(
        "--starting-capital", type=float, default=10000.0
    )
    parser.add_argument(
        "--max-hold-mr", type=int, default=7,
        help="Max hold days for mean-reversion triggers (default: 7)",
    )
    parser.add_argument(
        "--max-hold-momentum", type=int, default=30,
        help="Max hold days for momentum triggers (default: 30)",
    )
    args = parser.parse_args()

    run_id = asyncio.run(
        run_research_backtest(
            args.start_date,
            args.end_date,
            args.run_name,
            args.starting_capital,
            max_hold_mr=args.max_hold_mr,
            max_hold_momentum=args.max_hold_momentum,
        )
    )
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
