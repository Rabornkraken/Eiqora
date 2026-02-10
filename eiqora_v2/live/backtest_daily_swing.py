"""
Swing Trading Daily Backtest v3

Extends the research backtest with 8 new swing trading triggers:

Mean-Reversion (SL=2x ATR, TP=2x ATR, max hold 7 days):
  - daily_ibs_oversold: IBS < 0.2 + close > SMA200
  - daily_williams_r_oversold: Williams %R(2) < -90 + close > SMA200
  - daily_double_7s: close > SMA200 + close_n_day_low_7
  - daily_cumulative_rsi2: cumulative RSI(2) < 10 + close > SMA200
  - daily_three_line_strike: 3 bearish candles + 1 bullish engulf
  - daily_td_buy_9: TD setup buy count == 9

Momentum (SL=1.5x ATR, TP=3x ATR, max hold 30 days):
  - daily_holy_grail: ADX(14) > 30 + pullback to EMA20 + close > EMA20
  - daily_turtle_soup: 20-day low false breakout + recovery

IBS Filter: ibs < 0.5 applied to all mean-reversion triggers.

Data Leakage Prevention:
  - All stored indicators from check_date (lagging EOD data)
  - Pattern triggers computed from bars ending at check_date
  - Entry always at NEXT day's open
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
from eiqora_v2.live.backtest_daily_research import (
    is_stock,
    ETF_SYMBOLS,
    classify_stock_regime,
    fetch_research_indicators,
    fetch_ftd_signal,
    fetch_pcr_signal,
    get_exit_params as get_research_exit_params,
    check_research_triggers,
    EXCLUDED_RESEARCH_TRIGGERS,
    MEAN_REVERSION_TRIGGERS as RESEARCH_MR_TRIGGERS,
    MOMENTUM_TRIGGERS as RESEARCH_MOMENTUM_TRIGGERS,
    EXIT_PARAMS,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SWING_NEW_MR_TRIGGERS = {
    "daily_ibs_oversold",
    "daily_williams_r_oversold",
    "daily_double_7s",
    "daily_cumulative_rsi2",
    "daily_three_line_strike",
    "daily_td_buy_9",
}

SWING_NEW_MOMENTUM_TRIGGERS = {
    "daily_holy_grail",
    "daily_turtle_soup",
}

# Combined sets (research + swing)
SWING_MR_TRIGGERS = RESEARCH_MR_TRIGGERS | SWING_NEW_MR_TRIGGERS
SWING_MOMENTUM_TRIGGERS = RESEARCH_MOMENTUM_TRIGGERS | SWING_NEW_MOMENTUM_TRIGGERS
ALL_MR_TRIGGERS = SWING_MR_TRIGGERS


# ---------------------------------------------------------------------------
# Swing indicator data fetch
# ---------------------------------------------------------------------------

def fetch_swing_indicators(symbol: str, asof_date: date) -> dict | None:
    """Fetch swing indicator set from market_bar_daily for a single bar.

    Returns dict with keys: ibs, williams_r_2, rsi_2, cumulative_rsi2,
    close_n_day_low_7, close_n_day_high_7, td_setup_buy_count.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ibs, williams_r_2, rsi_2, cumulative_rsi2,
                       close_n_day_low_7, close_n_day_high_7,
                       td_setup_buy_count
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
                "ibs": float(row[0]) if row[0] is not None else None,
                "williams_r_2": float(row[1]) if row[1] is not None else None,
                "rsi_2": float(row[2]) if row[2] is not None else None,
                "cumulative_rsi2": float(row[3]) if row[3] is not None else None,
                "close_n_day_low_7": bool(row[4]) if row[4] is not None else None,
                "close_n_day_high_7": bool(row[5]) if row[5] is not None else None,
                "td_setup_buy_count": int(row[6]) if row[6] is not None else None,
            }


# ---------------------------------------------------------------------------
# Exit parameters (extends research)
# ---------------------------------------------------------------------------

def get_exit_params(trigger_type: str) -> dict:
    """Return SL/TP/max_hold based on trigger category."""
    if trigger_type in SWING_MR_TRIGGERS:
        return EXIT_PARAMS["mean_reversion"]
    return EXIT_PARAMS["momentum"]


# ---------------------------------------------------------------------------
# In-memory helpers
# ---------------------------------------------------------------------------

def _compute_sma(bars: list[dict], period: int) -> float | None:
    """Compute SMA from a list of bar dicts (must have 'close')."""
    closes = [
        b["close"] for b in bars
        if b.get("close") is not None
    ]
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _compute_ema(closes: list[float], period: int) -> float | None:
    """Compute EMA from a list of closing prices."""
    if len(closes) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


# ---------------------------------------------------------------------------
# Swing triggers from stored indicators
# ---------------------------------------------------------------------------

def check_swing_triggers(
    symbol: str,
    check_date: date,
    bars: list[dict],
    ind: dict | None,
    swing_ind: dict | None,
) -> list[Trigger]:
    """Detect 6 swing triggers from stored indicators.

    1. daily_ibs_oversold: IBS < 0.2 + close > SMA200
    2. daily_williams_r_oversold: Williams %R(2) < -90 + close > SMA200
    3. daily_double_7s: close > SMA200 + close_n_day_low_7
    4. daily_cumulative_rsi2: cumulative RSI(2) < 10 + close > SMA200
    5. daily_td_buy_9: td_setup_buy_count == 9
    6. daily_turtle_soup: new 20-day low + prior low set >= 3 days ago + recovery
    """
    triggers: list[Trigger] = []
    check_datetime = datetime.combine(check_date, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    if swing_ind is None:
        return triggers

    close = ind.get("close") if ind else None
    sma200 = _compute_sma(bars, 200) if len(bars) >= 200 else None

    # --- 1. daily_ibs_oversold ---
    ibs = swing_ind.get("ibs")
    if (
        ibs is not None
        and close is not None
        and sma200 is not None
        and ibs < 0.2
        and close > sma200
    ):
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_ibs_oversold",
                priority="HIGH",
                details={
                    "ibs": round(ibs, 4),
                    "close": close,
                    "sma200": round(sma200, 2),
                },
                detected_at=check_datetime,
            )
        )

    # --- 2. daily_williams_r_oversold ---
    wr2 = swing_ind.get("williams_r_2")
    if (
        wr2 is not None
        and close is not None
        and sma200 is not None
        and wr2 < -90
        and close > sma200
    ):
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_williams_r_oversold",
                priority="HIGH",
                details={
                    "williams_r_2": round(wr2, 2),
                    "close": close,
                    "sma200": round(sma200, 2),
                },
                detected_at=check_datetime,
            )
        )

    # --- 3. daily_double_7s ---
    close_n_day_low_7 = swing_ind.get("close_n_day_low_7")
    if (
        close is not None
        and sma200 is not None
        and close > sma200
        and close_n_day_low_7 is True
    ):
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_double_7s",
                priority="HIGH",
                details={
                    "close": close,
                    "sma200": round(sma200, 2),
                    "close_n_day_low_7": True,
                },
                detected_at=check_datetime,
            )
        )

    # --- 4. daily_cumulative_rsi2 ---
    cum_rsi2 = swing_ind.get("cumulative_rsi2")
    if (
        cum_rsi2 is not None
        and close is not None
        and sma200 is not None
        and cum_rsi2 < 10
        and close > sma200
    ):
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_cumulative_rsi2",
                priority="HIGH",
                details={
                    "cumulative_rsi2": round(cum_rsi2, 2),
                    "close": close,
                    "sma200": round(sma200, 2),
                },
                detected_at=check_datetime,
            )
        )

    # --- 5. daily_td_buy_9 ---
    td_count = swing_ind.get("td_setup_buy_count")
    if td_count is not None and td_count == 9:
        triggers.append(
            Trigger(
                symbol=symbol,
                trigger_type="daily_td_buy_9",
                priority="HIGH",
                details={
                    "td_setup_buy_count": td_count,
                    "close": close,
                },
                detected_at=check_datetime,
            )
        )

    # --- 6. daily_turtle_soup ---
    # New 20-day low + prior 20-day low was set >= 3 days ago + close recovers above prior low
    support = ind.get("support_level") if ind else None
    if (
        close is not None
        and support is not None
        and ind is not None
        and ind.get("low") is not None
    ):
        today_low = ind["low"]
        # Check: today's low went below support (new 20-day low breakout)
        if today_low < support:
            # Check: close recovered above support (false breakout)
            if close > support:
                # Check: prior 20-day low was set >= 3 days ago
                # We approximate by checking that support hasn't changed in recent bars
                if len(bars) >= 4:
                    # Look at bar 4 days ago - if support was similar, the low was old enough
                    old_low = min(
                        b.get("low", float("inf"))
                        for b in bars[-23:-3]
                        if b.get("low") is not None
                    ) if len(bars) >= 23 else None
                    if old_low is not None and abs(old_low - support) / support < 0.005:
                        triggers.append(
                            Trigger(
                                symbol=symbol,
                                trigger_type="daily_turtle_soup",
                                priority="HIGH",
                                details={
                                    "today_low": today_low,
                                    "support_level": support,
                                    "close": close,
                                    "prior_low_approx": round(old_low, 2),
                                },
                                detected_at=check_datetime,
                            )
                        )

    return triggers


# ---------------------------------------------------------------------------
# In-memory pattern triggers
# ---------------------------------------------------------------------------

def check_swing_pattern_triggers(
    symbol: str,
    check_date: date,
    bars: list[dict],
    ind: dict | None,
) -> list[Trigger]:
    """Detect 2 swing triggers from in-memory pattern detection.

    1. daily_three_line_strike: 3 bearish candles + 1 bullish engulfing all 3
    2. daily_holy_grail: ADX(14) > 30 + pullback to EMA20 + close > EMA20
    """
    triggers: list[Trigger] = []
    check_datetime = datetime.combine(check_date, datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    # --- 1. daily_three_line_strike ---
    # Need at least 4 recent bars (bars[-4] through bars[-1])
    if len(bars) >= 5:
        # bars[-1] is check_date bar, bars[-2] through bars[-4] are the 3 prior bars
        b1 = bars[-4]  # oldest of the 3 bearish
        b2 = bars[-3]
        b3 = bars[-2]
        b4 = bars[-1]  # the bullish engulfing bar (check_date)

        # All 3 prior bars must be bearish (close < open)
        bearish_1 = (
            b1.get("close") is not None and b1.get("open") is not None
            and b1["close"] < b1["open"]
        )
        bearish_2 = (
            b2.get("close") is not None and b2.get("open") is not None
            and b2["close"] < b2["open"]
        )
        bearish_3 = (
            b3.get("close") is not None and b3.get("open") is not None
            and b3["close"] < b3["open"]
        )

        if bearish_1 and bearish_2 and bearish_3:
            # 4th bar must be bullish (close > open)
            if (
                b4.get("close") is not None
                and b4.get("open") is not None
                and b4["close"] > b4["open"]
            ):
                # 4th bar must engulf all 3 bearish bars
                lowest_open = min(b1["open"], b2["open"], b3["open"])
                lowest_close = min(b1["close"], b2["close"], b3["close"])
                engulf_low = min(lowest_open, lowest_close)

                if (
                    b4["open"] <= engulf_low
                    and b4["close"] >= b1["open"]  # closes above first bar's open
                ):
                    triggers.append(
                        Trigger(
                            symbol=symbol,
                            trigger_type="daily_three_line_strike",
                            priority="HIGH",
                            details={
                                "b1_open": b1["open"],
                                "b1_close": b1["close"],
                                "b4_open": b4["open"],
                                "b4_close": b4["close"],
                            },
                            detected_at=check_datetime,
                        )
                    )

    # --- 2. daily_holy_grail ---
    # ADX(14) > 30 + price touched EMA20 + close > EMA20 + ADX still > 30
    if ind is not None and len(bars) >= 20:
        adx = ind.get("adx_14")
        close = ind.get("close")
        low = ind.get("low")

        closes = [
            b["close"] for b in bars
            if b.get("close") is not None
        ]
        ema20 = _compute_ema(closes, 20)

        if (
            adx is not None
            and close is not None
            and low is not None
            and ema20 is not None
            and adx > 30
            and low <= ema20  # price touched EMA20 (pullback)
            and close > ema20  # closed above EMA20 (recovery)
        ):
            triggers.append(
                Trigger(
                    symbol=symbol,
                    trigger_type="daily_holy_grail",
                    priority="HIGH",
                    details={
                        "adx_14": round(adx, 2),
                        "ema20": round(ema20, 2),
                        "close": close,
                        "low": low,
                    },
                    detected_at=check_datetime,
                )
            )

    return triggers


# ---------------------------------------------------------------------------
# Main backtest loop
# ---------------------------------------------------------------------------

async def run_swing_backtest(
    start_date: str,
    end_date: str,
    run_name: str,
    starting_capital: float = 10000.0,
    excluded_triggers: set[str] | None = None,
    max_hold_mr: int = 7,
    max_hold_momentum: int = 30,
    use_ibs_filter: bool = True,
) -> uuid.UUID:
    """Run swing trading daily backtest.

    Combines all research triggers + 8 new swing triggers.
    Optionally applies IBS < 0.5 filter on mean-reversion triggers.
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
                        "mode": "swing",
                        "position_size": 500.0,
                        "excluded_triggers": list(excluded_triggers),
                        "excluded_research_triggers": list(EXCLUDED_RESEARCH_TRIGGERS),
                        "exit_params": EXIT_PARAMS,
                        "etf_filter": True,
                        "adx_regime_filter": True,
                        "ma50_early_bear": True,
                        "ftd_filter": True,
                        "pcr_info": True,
                        "ibs_filter": use_ibs_filter,
                    }),
                ),
            )
        conn.commit()

    monitor = TriggerMonitor(backtest_mode=True)

    # Get all daily bars with indicators in range
    test_bars = get_daily_bars_to_test(start_date, end_date)
    total_bars = len(test_bars)
    logger.info(f"Starting swing backtest with {total_bars} bars...")

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

    # IBS filter counters
    ibs_filter_checked = 0
    ibs_filter_removed = 0

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

        # Fetch full indicator set (research)
        ind = fetch_research_indicators(symbol, check_date)

        # Fetch swing indicators
        swing_ind = fetch_swing_indicators(symbol, check_date)

        # Classify per-stock regime
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

        # 4. Confluence-style triggers
        try:
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

        # 5. Research triggers (3 from research backtest)
        try:
            research_triggers = check_research_triggers(symbol, check_date, bars, ind)
            triggers.extend(research_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: Research triggers failed - {e}")

        # 6. Swing triggers from stored indicators (6 triggers)
        try:
            swing_triggers = check_swing_triggers(
                symbol, check_date, bars, ind, swing_ind
            )
            triggers.extend(swing_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: Swing triggers failed - {e}")

        # 7. Swing pattern triggers from in-memory detection (2 triggers)
        try:
            pattern_triggers = check_swing_pattern_triggers(
                symbol, check_date, bars, ind
            )
            triggers.extend(pattern_triggers)
        except Exception as e:
            logger.debug(f"{symbol}: Swing pattern triggers failed - {e}")

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
                if t.trigger_type in SWING_MR_TRIGGERS
            ]
            if not triggers:
                continue

        # IBS filter: remove MR triggers when IBS >= 0.5
        if use_ibs_filter and swing_ind and swing_ind.get("ibs") is not None:
            ibs = swing_ind["ibs"]
            mr_triggers_before = [
                t for t in triggers if t.trigger_type in ALL_MR_TRIGGERS
            ]
            if mr_triggers_before:
                ibs_filter_checked += len(mr_triggers_before)
                if ibs >= 0.5:
                    before_count = len(triggers)
                    triggers = [
                        t for t in triggers
                        if t.trigger_type not in ALL_MR_TRIGGERS
                    ]
                    ibs_filter_removed += before_count - len(triggers)
                    if not triggers:
                        continue

        # FTD negative filter (mean-reversion only)
        has_mr_triggers = any(
            t.trigger_type in SWING_MR_TRIGGERS for t in triggers
        )
        ftd_info = None
        if has_mr_triggers:
            ftd_checked += 1
            try:
                ftd_info = fetch_ftd_signal(symbol, check_date)
                if ftd_info["elevated"]:
                    ftd_filtered += 1
                    triggers = [
                        t for t in triggers
                        if t.trigger_type not in SWING_MR_TRIGGERS
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

            # All triggers use next-day open
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

            # Category-appropriate exit parameters
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

            # Build trigger detail with swing-specific info
            trigger_detail = dict(trigger.details or {})
            trigger_detail["entry_date"] = entry_date.isoformat()
            trigger_detail["regime"] = regime
            trigger_detail["exit_strategy"] = (
                "mean_reversion" if trigger.trigger_type in SWING_MR_TRIGGERS
                else "momentum"
            )
            trigger_detail["sl_mult"] = sl_mult
            trigger_detail["tp_mult"] = tp_mult
            trigger_detail["max_hold"] = max_hold

            # IBS info
            if swing_ind and swing_ind.get("ibs") is not None:
                trigger_detail["ibs"] = round(swing_ind["ibs"], 4)

            # N-day high exit signal info
            if swing_ind and swing_ind.get("close_n_day_high_7") is not None:
                trigger_detail["close_n_day_high_7"] = swing_ind["close_n_day_high_7"]

            if ftd_info is not None:
                trigger_detail["ftd_recent_avg"] = round(ftd_info["recent_avg"], 0)
                trigger_detail["ftd_prior_avg"] = round(ftd_info["prior_avg"], 0)
                trigger_detail["ftd_elevated"] = ftd_info["elevated"]

            if pcr_info is not None:
                trigger_detail["pcr"] = pcr_info["pcr"]
                if (
                    pcr_info["pcr_contrarian"]
                    and trigger.trigger_type in SWING_MR_TRIGGERS
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
    _update_run_metrics_swing(
        run_id,
        started_at,
        starting_capital,
        regime_counts,
        regime_trades,
        ftd_checked,
        ftd_filtered,
        ibs_filter_checked,
        ibs_filter_removed,
        use_ibs_filter,
        len(stock_symbols),
        len(skipped_symbols),
    )
    logger.info(f"Run complete: {run_id}")

    return run_id


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _update_run_metrics_swing(
    run_id: uuid.UUID,
    started_at: datetime,
    starting_capital: float,
    regime_counts: dict[str, int],
    regime_trades: dict[str, list],
    ftd_checked: int,
    ftd_filtered: int,
    ibs_filter_checked: int,
    ibs_filter_removed: int,
    use_ibs_filter: bool,
    stock_count: int,
    skipped_count: int,
) -> None:
    """Calculate and store aggregate metrics with swing-specific stats."""
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

            # Swing-specific stats
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

            swing_stats = {
                "regime_breakdown": regime_breakdown,
                "ftd_checked": ftd_checked,
                "ftd_filtered": ftd_filtered,
                "ibs_filter_enabled": use_ibs_filter,
                "ibs_filter_checked": ibs_filter_checked,
                "ibs_filter_removed": ibs_filter_removed,
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
                    Json({"swing_stats": swing_stats}),
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

    file_handler = logging.FileHandler("logs/daily_swing_backtest.log")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger("eiqora_v2.live.candidate_selector").setLevel(logging.INFO)


def main() -> None:
    """CLI entry point for swing trading daily backtest."""
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Swing trading daily trigger backtest"
    )
    parser.add_argument(
        "--start-date", type=str, required=True, help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--end-date", type=str, required=True, help="End date (YYYY-MM-DD)"
    )
    parser.add_argument("--run-name", type=str, default="swing-backtest")
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
    parser.add_argument(
        "--no-ibs-filter", action="store_true",
        help="Disable IBS < 0.5 filter on mean-reversion triggers",
    )
    args = parser.parse_args()

    run_id = asyncio.run(
        run_swing_backtest(
            args.start_date,
            args.end_date,
            args.run_name,
            args.starting_capital,
            max_hold_mr=args.max_hold_mr,
            max_hold_momentum=args.max_hold_momentum,
            use_ibs_filter=not args.no_ibs_filter,
        )
    )
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
