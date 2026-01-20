#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_backtest import (
    compute_atr_brackets,
    resolve_outcome,
    prepare_trigger_detail,
)
from eiqora_v2.live.backtest_triggers_only import (
    build_hourly_bars_query,
    run_trigger_only_backtest,
)


def summarize_run(run_id: str) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT trigger_type, outcome, COUNT(*)
                FROM trigger_backtest_result
                WHERE run_id = %s
                GROUP BY trigger_type, outcome
                ORDER BY COUNT(*) DESC
                """,
                (run_id,),
            )
            rows = cur.fetchall()

    if not rows:
        print(f"No rows found for run_id={run_id}")
        return

    print("Trigger outcome counts:")
    for trigger_type, outcome, count in rows:
        print(f"  {trigger_type:24} {outcome:10} {count}")


def smoke_assertions() -> None:
    entry = 100.0
    atr14 = 2.0
    stop_loss, take_profit = compute_atr_brackets(entry, atr14)
    assert stop_loss == 97.0
    assert take_profit == 106.0

    bar_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [(bar_time, 107.0, 99.0, 106.5)]
    outcome = resolve_outcome(bar_time, entry, stop_loss, take_profit, bars)
    assert outcome["outcome"] == "TP_HIT"

    assert prepare_trigger_detail({"a": 1}) is not None

    query, params = build_hourly_bars_query("2025-05-01", "2025-06-01", None)
    assert "datetime::date >= %s" in query
    assert "datetime::date <= %s" in query
    assert params[0] == "2025-05-01"
    assert params[1] == "2025-06-01"


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger-only backtest smoke script")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-name", type=str, default="trigger-only-smoke")
    parser.add_argument("--start-date", type=str)
    parser.add_argument("--end-date", type=str)
    parser.add_argument("--run-id", type=str, help="Summarize existing run id")
    parser.add_argument("--no-run", action="store_true", help="Skip run and only summarize")
    args = parser.parse_args()

    smoke_assertions()

    run_id = args.run_id
    if not run_id and not args.no_run:
        run_id = asyncio.run(
            run_trigger_only_backtest(
                args.days,
                args.limit,
                args.run_name,
                args.start_date,
                args.end_date,
            )
        )
        print(f"Run complete: {run_id}")

    if run_id:
        summarize_run(run_id)


if __name__ == "__main__":
    main()
