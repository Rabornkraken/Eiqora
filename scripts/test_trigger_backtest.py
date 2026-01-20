#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_collection.db.connection import get_connection  # noqa: E402
from eiqora_v2.live.backtest_triggers_only import run_trigger_only_backtest  # noqa: E402


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
