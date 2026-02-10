import argparse
import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta

from psycopg.types.json import Json

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_monitor import TriggerMonitor
from eiqora_v2.live.candidate_selector import CandidateSelector
from eiqora_v2.live.backtest_with_agents import get_hourly_bars_to_test
from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    build_result_row,
)
logger = logging.getLogger(__name__)

ETF_SYMBOLS = {
    "DIA", "IEF", "IWM", "QQQ", "SPY", "SHY", "TLT", "UUP",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
    "XLU", "XLV", "XLY",
}


def is_stock(symbol: str) -> bool:
    """Return True if symbol is a stock (not ETF or index)."""
    if symbol in ETF_SYMBOLS:
        return False
    if symbol.startswith("IDX_"):
        return False
    return True


def fetch_atr14(symbol: str, asof_date) -> float | None:
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


def fetch_entry_price(symbol: str, entry_time: datetime, column: str = "close") -> float | None:
    """Fetch price from the SAME bar as entry_time."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {column}
                FROM market_bar_hourly
                WHERE symbol = %s AND datetime = %s
                LIMIT 1
                """,
                (symbol, entry_time),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None


def fetch_next_bar_open(symbol: str, after_time: datetime) -> tuple[float, datetime] | None:
    """
    Fetch the OPEN price of the next bar AFTER the trigger bar.
    
    This eliminates look-ahead bias by entering at a price we could
    actually get in live trading (after the trigger bar completes).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT open, datetime
                FROM market_bar_hourly
                WHERE symbol = %s AND datetime > %s
                ORDER BY datetime ASC
                LIMIT 1
                """,
                (symbol, after_time),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return float(row[0]), row[1]
            return None


def fetch_future_bars(symbol: str, after_time: datetime) -> list[tuple]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT datetime, high, low, close
                FROM market_bar_hourly
                WHERE symbol = %s AND datetime > %s
                ORDER BY datetime ASC
                """,
                (symbol, after_time),
            )
            return cur.fetchall()


def insert_result(row: tuple) -> None:
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


def _build_no_data_detail(trigger, reason: str) -> dict:
    details = dict(trigger.details or {})
    details["no_data_reason"] = reason
    return details


async def build_historical_watchlist(scan_date: datetime) -> list[str]:
    """Build watchlist for a historical date using production candidate selector."""
    
    selector = CandidateSelector()
    
    # Check if watchlist already exists
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol
                FROM daily_watchlist
                WHERE scan_date = %s
            """, (scan_date.date(),))
            rows = cur.fetchall()
            if rows:
                return [row[0] for row in rows]
    
    # Build new watchlist
    logger.info(f"  Building fresh watchlist for {scan_date.date()}...")
    watchlist = await selector.build_watchlist(scan_date)
    
    # Save to ensure consistency
    await selector.save_watchlist(watchlist, scan_date.date())
    logger.info(f"  Saved {len(watchlist)} symbols to daily_watchlist table for {scan_date.date()}")
    
    return [c['symbol'] for c in watchlist]


def format_trigger_log(symbol: str, trigger_type: str, check_time: datetime) -> str:
    return f"TRIGGER {symbol} {trigger_type} @ {check_time.isoformat()}"


def build_hourly_bars_query(
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
) -> tuple[str, tuple]:
    query = """
        SELECT m.symbol, m.datetime
        FROM market_bar_hourly m
        WHERE m.rsi_14 IS NOT NULL
          AND EXTRACT(HOUR FROM m.datetime AT TIME ZONE 'America/New_York') BETWEEN 10 AND 15
    """
    params: list = []

    if start_date:
        query += " AND m.datetime::date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND m.datetime::date <= %s"
        params.append(end_date)

    query += " ORDER BY m.datetime ASC, m.symbol"

    if limit:
        query += " LIMIT %s"
        params.append(limit)

    return query, tuple(params)


async def get_hourly_bars_to_test_range(
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
) -> list[tuple[str, datetime]]:
    query, params = build_hourly_bars_query(start_date, end_date, limit)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def parse_date_arg(value: str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).date().isoformat()


async def run_trigger_only_backtest(
    days: int,
    limit: int | None,
    run_name: str,
    start_date: str | None = None,
    end_date: str | None = None,
    sl_mult: float = 1.5,
    tp_mult: float = 3.0,
    starting_capital: float = 10000.0,
    risk_per_trade: float = 5.0,  # Risk 5% of capital per trade
) -> uuid.UUID:
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
                    start_date if start_date else None,
                    end_date if end_date else None,
                    started_at,
                    Json({"days": days, "limit": limit, "sl_mult": sl_mult, "tp_mult": tp_mult}),
                ),
            )
        conn.commit()

    monitor = TriggerMonitor(backtest_mode=True)
    if start_date or end_date:
        test_bars = await get_hourly_bars_to_test_range(start_date, end_date, limit)
    else:
        test_bars = await get_hourly_bars_to_test(days, limit)

    logger.info(f"Starting backtest loop with {len(test_bars)} bars...")

    active_positions = {}  # symbol -> datetime (exclusive end time of trade)

    skipped_symbols: set[str] = set()

    for symbol, check_time in test_bars:
        # Skip ETFs and index symbols
        if not is_stock(symbol):
            skipped_symbols.add(symbol)
            continue

        # Check if already in a position
        if symbol in active_positions:
            if check_time <= active_positions[symbol]:
                continue
            else:
                del active_positions[symbol]

        # Aggregate triggers from allowed sources
        triggers = []
        
        # 1. Hourly Technicals
        tech_triggers = await monitor.check_hourly_technical_triggers(symbol, check_time)
        triggers.extend(tech_triggers)
        
        # 2. Earnings
        # earnings = await monitor.check_earnings_trigger(symbol, check_time)
        # if earnings:
        #     triggers.append(earnings)
            
        # 3. SEC 8-K
        sec8k = await monitor.check_sec_8k_trigger(symbol, check_time)
        if sec8k:
            triggers.append(sec8k)
            
        # 4. Sector Laggard
        # laggard = await monitor.check_sector_laggard_trigger(symbol, check_time)
        # if laggard:
        #     triggers.append(laggard)
            
        # 5. Volatility Compression
        compression = await monitor.check_volatility_compression_trigger(symbol, check_time)
        if compression:
            triggers.append(compression)
            
        # 6. Supply Chain Cascade
        supply = await monitor.check_supply_chain_cascade_trigger(symbol, check_time)
        if supply:
            triggers.append(supply)

        if not triggers:
            continue

        trade_end_time = None

        for trigger in triggers:
            logger.info(format_trigger_log(symbol, trigger.trigger_type, check_time))
            
            # Determine entry price strategy:
            # - Event triggers: Use SAME bar's OPEN (immediate reaction to known event)
            # - Technical triggers: Use NEXT bar's OPEN (eliminates look-ahead bias)
            #   We can only know a bar's indicators AFTER it closes, so entry must be on next bar
            
            is_event_trigger = trigger.trigger_type in {
                "earnings_release", 
                "sec_8k", 
                "supply_chain_cascade", 
                "news_sentiment",
                "bad_news_no_drop"
            }
            
            if is_event_trigger:
                # Event triggers: enter at same bar's open (immediate reaction)
                entry_price = trigger.details.get("current_price") if trigger.details else None
                if not entry_price:
                    entry_price = fetch_entry_price(symbol, check_time, column="open")
            else:
                # Technical triggers: enter at NEXT bar's open (no look-ahead bias)
                next_bar = fetch_next_bar_open(symbol, check_time)
                entry_price = next_bar[0] if next_bar else None
            
            if not entry_price:
                insert_result(build_result_row(
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
                ))
                continue

            atr14 = fetch_atr14(symbol, check_time.date())
            if not atr14:
                insert_result(build_result_row(
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
                ))
                continue

            stop_loss, take_profit = compute_atr_brackets(entry_price, atr14, sl_mult=sl_mult, tp_mult=tp_mult)
            future_bars = fetch_future_bars(symbol, check_time)
            if not future_bars:
                insert_result(build_result_row(
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
                ))
                continue

            outcome = resolve_outcome(check_time, entry_price, stop_loss, take_profit, future_bars)
            trigger_detail = dict(trigger.details or {})
            if outcome.get("same_bar_tie"):
                trigger_detail["same_bar_tie"] = True

            insert_result(build_result_row(
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
            ))

            # Determine trade end time for locking
            this_outcome_time = outcome.get("outcome_time")
            if this_outcome_time:
                # If we have a definite outcome time, update the lock
                if trade_end_time is None or this_outcome_time > trade_end_time:
                    trade_end_time = this_outcome_time
            elif future_bars:
                # If NO_HIT, we assume we held until end of data
                last_bar_time = future_bars[-1][0]
                if trade_end_time is None or last_bar_time > trade_end_time:
                    trade_end_time = last_bar_time
        
        # Lock symbol if we executed trades
        if trade_end_time:
            active_positions[symbol] = trade_end_time

    if skipped_symbols:
        logger.info(f"Skipped {len(skipped_symbols)} ETF/index symbols: {sorted(skipped_symbols)}")

    # Calculate metrics and update run record
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
            
            # Calculate capital tracking and max drawdown with position sizing
            # Fetch all trades chronologically with entry price and stop-loss
            cur.execute(
                """
                SELECT trigger_time, realized_pnl_pct, entry_price, stop_loss
                FROM trigger_backtest_result
                WHERE run_id = %s AND outcome IN ('TP_HIT', 'SL_HIT')
                ORDER BY trigger_time ASC
                """,
                (run_id,),
            )
            trades = cur.fetchall()
            
            # Simulate equity curve with percentage-based position sizing
            # Each trade uses 5% of current capital
            POSITION_PCT = 0.05  # 5% of capital per trade

            # Transaction Costs (Conservative Estimates)
            # Interactive Brokers Pro / Tiered: ~$1.00 min per order
            COMMISSION_PER_ORDER = 1.00  # $1 to enter, $1 to exit
            # Slippage: ~0.05% per execution for liquid stocks (5 bps)
            SLIPPAGE_PCT = 0.05

            current_capital = starting_capital
            peak_capital = starting_capital
            max_drawdown_pct = 0.0

            for trade_time, pnl_pct, entry_price, stop_loss in trades:
                position_size = current_capital * POSITION_PCT

                # Skip trade if position too small to cover costs
                if position_size < 50.0:
                    continue

                # Convert PnL percentage to float
                pnl_pct = float(pnl_pct) if pnl_pct is not None else 0.0

                # Calculate Gross PnL: position_size × PnL%
                gross_pnl_dollars = position_size * (pnl_pct / 100.0)

                # Calculate Transaction Costs
                # 1. Commissions (Entry + Exit)
                total_commissions = COMMISSION_PER_ORDER * 2

                # 2. Slippage (Entry + Exit)
                entry_slippage = position_size * (SLIPPAGE_PCT / 100.0)
                exit_slippage = position_size * (SLIPPAGE_PCT / 100.0)
                total_slippage = entry_slippage + exit_slippage

                # Net PnL
                net_pnl_dollars = gross_pnl_dollars - total_commissions - total_slippage

                current_capital += net_pnl_dollars

                # Update peak
                if current_capital > peak_capital:
                    peak_capital = current_capital

                # Calculate drawdown from peak
                if peak_capital > 0:
                    drawdown = ((peak_capital - current_capital) / peak_capital) * 100.0
                    max_drawdown_pct = max(max_drawdown_pct, drawdown)
            
            final_capital = current_capital
            total_return_pct = ((final_capital - starting_capital) / starting_capital) * 100.0
            
            # 3. Find best/worst triggers
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
            
            # 4. Aggregate per-trigger detail for JSON storage
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
                    "avg_pnl_pct": round(float(t_avg_pnl or 0), 2)
                })

            # 5. Update run record with JSON details
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
                    starting_capital = %s,
                    final_capital = %s,
                    total_return_pct = %s,
                    max_drawdown_pct = %s
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
                    starting_capital,
                    final_capital,
                    total_return_pct,
                    max_drawdown_pct,
                    run_id,
                ),
            )
            conn.commit()

    return run_id


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler("logs/trigger_backtest.log")
    file_handler.setFormatter(formatter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
    
    # Explicitly enable candidate selector logs
    logging.getLogger("eiqora_v2.live.candidate_selector").setLevel(logging.INFO)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description="Trigger-only backtest")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-name", type=str, default="trigger-only")
    parser.add_argument("--start-date", type=str)
    parser.add_argument("--end-date", type=str)
    args = parser.parse_args()

    start_date = parse_date_arg(args.start_date)
    end_date = parse_date_arg(args.end_date)

    run_id = asyncio.run(
        run_trigger_only_backtest(args.days, args.limit, args.run_name, start_date, end_date)
    )
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
