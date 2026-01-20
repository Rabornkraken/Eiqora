import argparse
import asyncio
import uuid
from datetime import datetime, timezone

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_monitor import TriggerMonitor
from eiqora_v2.live.backtest_with_agents import get_hourly_bars_to_test
from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    build_result_row,
)


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


def fetch_entry_price(symbol: str, entry_time: datetime) -> float | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT close
                FROM market_bar_hourly
                WHERE symbol = %s AND datetime = %s
                LIMIT 1
                """,
                (symbol, entry_time),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None


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


def build_hourly_bars_query(
    start_date: str | None,
    end_date: str | None,
    limit: int | None,
) -> tuple[str, tuple]:
    query = """
        SELECT symbol, datetime
        FROM market_bar_hourly
        WHERE rsi_14 IS NOT NULL
          AND EXTRACT(HOUR FROM datetime AT TIME ZONE 'America/New_York') BETWEEN 10 AND 15
    """
    params: list = []

    if start_date:
        query += " AND datetime::date >= %s"
        params.append(start_date)
    if end_date:
        query += " AND datetime::date <= %s"
        params.append(end_date)

    query += " ORDER BY datetime DESC, symbol"

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
) -> uuid.UUID:
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    monitor = TriggerMonitor()
    if start_date or end_date:
        test_bars = await get_hourly_bars_to_test_range(start_date, end_date, limit)
    else:
        test_bars = await get_hourly_bars_to_test(days, limit)

    for symbol, check_time in test_bars:
        triggers = await monitor.check_hourly_technical_triggers(symbol, check_time)
        if not triggers:
            continue

        for trigger in triggers:
            entry_price = trigger.details.get("current_price") if trigger.details else None
            if not entry_price:
                entry_price = fetch_entry_price(symbol, check_time)
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
                    sl_mult=1.5,
                    tp_mult=3.0,
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
                    sl_mult=1.5,
                    tp_mult=3.0,
                ))
                continue

            stop_loss, take_profit = compute_atr_brackets(entry_price, atr14)
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
                    sl_mult=1.5,
                    tp_mult=3.0,
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
                sl_mult=1.5,
                tp_mult=3.0,
            ))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trigger_backtest_result
                SET completed_at = %s
                WHERE run_id = %s AND completed_at IS NULL
                """,
                (datetime.now(timezone.utc), run_id),
            )
            conn.commit()

    return run_id


def main() -> None:
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
