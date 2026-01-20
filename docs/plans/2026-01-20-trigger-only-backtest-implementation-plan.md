# Trigger-Only Backtest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement trigger-only backtesting on `main` that stores ATR14 outcomes in Postgres and provides a non-pytest smoke script for validation.

**Architecture:** Add a new trigger-only results table, a helper module to compute ATR brackets/outcomes and prepare JSON payloads, a runner that scans historical bars with date-range support, and a `scripts/` smoke script for non-pytest validation.

**Tech Stack:** Python 3.10, psycopg, Postgres.

---

### Task 1: Add trigger-only backtest schema

**Files:**
- Create: `eiqora_v2/live/trigger_backtest_schema.sql`

**Step 1: Write the failing test**

_Not applicable (schema SQL file)._ 

**Step 2: Run test to verify it fails**

_Not applicable._

**Step 3: Write minimal implementation**

Create the SQL file with the table and indexes:

```sql
-- Trigger-Only Backtest Schema

CREATE TABLE IF NOT EXISTS trigger_backtest_result (
    result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    run_name TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    symbol VARCHAR(10) NOT NULL,
    trigger_type VARCHAR(50) NOT NULL,
    trigger_priority VARCHAR(20),
    trigger_time TIMESTAMPTZ NOT NULL,
    trigger_detail JSONB,

    entry_price DECIMAL(12, 4),
    atr14 DECIMAL(12, 6),
    sl_mult DECIMAL(6, 3) DEFAULT 1.5,
    tp_mult DECIMAL(6, 3) DEFAULT 3.0,
    stop_loss DECIMAL(12, 4),
    take_profit DECIMAL(12, 4),

    outcome VARCHAR(20) NOT NULL, -- TP_HIT, SL_HIT, NO_HIT, NO_DATA
    outcome_time TIMESTAMPTZ,
    bars_to_outcome INT,
    max_favorable_pct DECIMAL(8, 4),
    max_adverse_pct DECIMAL(8, 4),
    realized_pnl_pct DECIMAL(8, 4),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trigger_backtest_run ON trigger_backtest_result(run_id);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_symbol ON trigger_backtest_result(symbol);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_trigger ON trigger_backtest_result(trigger_type);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_outcome ON trigger_backtest_result(outcome);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_time ON trigger_backtest_result(trigger_time DESC);
```

**Step 4: Run test to verify it passes**

_Not applicable._

**Step 5: Commit**

```bash
git add eiqora_v2/live/trigger_backtest_schema.sql
git commit -m "feat: add trigger-only backtest schema"
```

---

### Task 2: Add failing smoke script (no pytest)

**Files:**
- Create: `scripts/test_trigger_backtest.py`

**Step 1: Write the failing test**

Create a script that imports the not-yet-existing module and runs assertions:

```python
#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eiqora_v2.live.trigger_backtest import compute_atr_brackets  # noqa: F401


def main() -> None:
    raise AssertionError("should not reach: module missing")


if __name__ == "__main__":
    main()
```

**Step 2: Run test to verify it fails**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py`
Expected: ImportError for `eiqora_v2.live.trigger_backtest`.

**Step 3: Write minimal implementation**

_Not yet. Implement in Task 3._

**Step 4: Run test to verify it passes**

_Not yet._

**Step 5: Commit**

```bash
git add scripts/test_trigger_backtest.py
git commit -m "test: add trigger-only smoke script"
```

---

### Task 3: Implement trigger_backtest helpers

**Files:**
- Create: `eiqora_v2/live/trigger_backtest.py`
- Modify: `scripts/test_trigger_backtest.py`

**Step 1: Write the failing test**

Extend the smoke script to assert behavior:

```python
from datetime import datetime, timezone

from eiqora_v2.live.trigger_backtest import compute_atr_brackets, resolve_outcome, prepare_trigger_detail

# ... in main()
entry = 100.0
atr14 = 2.0
stop_loss, take_profit = compute_atr_brackets(entry, atr14)
assert stop_loss == 97.0
assert take_profit == 106.0

bars = [(datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc), 107.0, 99.0, 106.5)]
outcome = resolve_outcome(bars[0][0], entry, stop_loss, take_profit, bars)
assert outcome["outcome"] == "TP_HIT"

assert prepare_trigger_detail({"a": 1}) is not None
```

**Step 2: Run test to verify it fails**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py`
Expected: AttributeError / ImportError (functions not yet defined).

**Step 3: Write minimal implementation**

Create `eiqora_v2/live/trigger_backtest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Any

from psycopg.types.json import Json


@dataclass(frozen=True)
class OutcomeResult:
    outcome: str
    outcome_time: datetime | None
    bars_to_outcome: int | None
    max_favorable_pct: float | None
    max_adverse_pct: float | None
    realized_pnl_pct: float | None
    same_bar_tie: bool = False


def compute_atr_brackets(entry_price: float, atr14: float, *, sl_mult: float = 1.5, tp_mult: float = 3.0) -> tuple[float, float]:
    stop_loss = entry_price - (atr14 * sl_mult)
    take_profit = entry_price + (atr14 * tp_mult)
    return stop_loss, take_profit


def resolve_outcome(
    entry_time: datetime,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    bars: Iterable[tuple[datetime, float, float, float]],
) -> dict:
    bars_list = list(bars)
    max_high = entry_price
    min_low = entry_price
    bars_seen = 0

    for bar_time, bar_high, bar_low, bar_close in bars_list:
        bars_seen += 1
        if bar_high is not None:
            max_high = max(max_high, float(bar_high))
        if bar_low is not None:
            min_low = min(min_low, float(bar_low))

        tp_hit = bar_high is not None and float(bar_high) >= take_profit
        sl_hit = bar_low is not None and float(bar_low) <= stop_loss

        if tp_hit and sl_hit:
            max_fav = (max_high - entry_price) / entry_price
            max_adv = (min_low - entry_price) / entry_price
            return {
                "outcome": "SL_HIT",
                "outcome_time": bar_time,
                "bars_to_outcome": bars_seen,
                "max_favorable_pct": max_fav,
                "max_adverse_pct": max_adv,
                "realized_pnl_pct": (stop_loss - entry_price) / entry_price,
                "same_bar_tie": True,
            }
        if tp_hit:
            max_fav = (max_high - entry_price) / entry_price
            max_adv = (min_low - entry_price) / entry_price
            return {
                "outcome": "TP_HIT",
                "outcome_time": bar_time,
                "bars_to_outcome": bars_seen,
                "max_favorable_pct": max_fav,
                "max_adverse_pct": max_adv,
                "realized_pnl_pct": (take_profit - entry_price) / entry_price,
                "same_bar_tie": False,
            }
        if sl_hit:
            max_fav = (max_high - entry_price) / entry_price
            max_adv = (min_low - entry_price) / entry_price
            return {
                "outcome": "SL_HIT",
                "outcome_time": bar_time,
                "bars_to_outcome": bars_seen,
                "max_favorable_pct": max_fav,
                "max_adverse_pct": max_adv,
                "realized_pnl_pct": (stop_loss - entry_price) / entry_price,
                "same_bar_tie": False,
            }

    last_close = None
    for _, _, _, bar_close in bars_list:
        if bar_close is not None:
            last_close = float(bar_close)
    realized = (last_close - entry_price) / entry_price if last_close is not None else None
    max_fav = (max_high - entry_price) / entry_price
    max_adv = (min_low - entry_price) / entry_price
    return {
        "outcome": "NO_HIT",
        "outcome_time": None,
        "bars_to_outcome": bars_seen if bars_seen else None,
        "max_favorable_pct": max_fav,
        "max_adverse_pct": max_adv,
        "realized_pnl_pct": realized,
        "same_bar_tie": False,
    }


def prepare_trigger_detail(value: Any) -> Json:
    return Json(value)


def build_result_row(
    *,
    run_id,
    run_name,
    started_at,
    symbol,
    trigger_type,
    trigger_priority,
    trigger_time,
    trigger_detail,
    entry_price,
    atr14,
    stop_loss,
    take_profit,
    outcome,
    outcome_time,
    bars_to_outcome,
    max_favorable_pct,
    max_adverse_pct,
    realized_pnl_pct,
    sl_mult,
    tp_mult,
) -> tuple:
    return (
        run_id,
        run_name,
        symbol,
        trigger_type,
        trigger_priority,
        trigger_time,
        prepare_trigger_detail(trigger_detail),
        entry_price,
        atr14,
        sl_mult,
        tp_mult,
        stop_loss,
        take_profit,
        outcome,
        outcome_time,
        bars_to_outcome,
        max_favorable_pct,
        max_adverse_pct,
        realized_pnl_pct,
        started_at,
    )
```

**Step 4: Run test to verify it passes**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py`
Expected: exits 0, no output.

**Step 5: Commit**

```bash
git add eiqora_v2/live/trigger_backtest.py scripts/test_trigger_backtest.py
git commit -m "feat: add trigger-only backtest helpers"
```

---

### Task 4: Add trigger-only runner with date-range support

**Files:**
- Create: `eiqora_v2/live/backtest_triggers_only.py`
- Modify: `scripts/test_trigger_backtest.py`

**Step 1: Write the failing test**

Extend the smoke script to import and validate the query builder:

```python
from eiqora_v2.live.backtest_triggers_only import build_hourly_bars_query

query, params = build_hourly_bars_query("2025-05-01", "2025-06-01", None)
assert "datetime::date >= %s" in query
assert "datetime::date <= %s" in query
assert params[0] == "2025-05-01"
assert params[1] == "2025-06-01"
```

**Step 2: Run test to verify it fails**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py`
Expected: ImportError for `build_hourly_bars_query`.

**Step 3: Write minimal implementation**

Create `eiqora_v2/live/backtest_triggers_only.py`:

```python
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


def build_hourly_bars_query(start_date: str | None, end_date: str | None, limit: int | None) -> tuple[str, tuple]:
    query = """
        SELECT symbol, datetime
        FROM market_bar_hourly
        WHERE rsi_14 IS NOT NULL
          AND EXTRACT(HOUR FROM datetime AT TIME ZONE 'America/New_York') BETWEEN 10 AND 15
    """
    params = []
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


async def get_hourly_bars_to_test_range(start_date: str | None, end_date: str | None, limit: int | None) -> list[tuple[str, datetime]]:
    query, params = build_hourly_bars_query(start_date, end_date, limit)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def parse_date_arg(value: str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).date().isoformat()


async def run_trigger_only_backtest(days: int, limit: int | None, run_name: str, start_date: str | None = None, end_date: str | None = None) -> uuid.UUID:
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
```

**Step 4: Run test to verify it passes**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py`
Expected: exits 0, no output.

**Step 5: Commit**

```bash
git add eiqora_v2/live/backtest_triggers_only.py scripts/test_trigger_backtest.py
git commit -m "feat: add trigger-only backtest runner"
```

---

### Task 5: Extend smoke script to run + summarize (no pytest)

**Files:**
- Modify: `scripts/test_trigger_backtest.py`

**Step 1: Write the failing test**

Extend the script to call the runner and print outcome counts by `run_id`.

**Step 2: Run test to verify it fails**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py --start-date 2025-05-01 --limit 200`
Expected: failure due to missing runner summary function.

**Step 3: Write minimal implementation**

Add summary logic (SQL `GROUP BY trigger_type, outcome`) and CLI args.

**Step 4: Run test to verify it passes**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py --start-date 2025-05-01 --limit 200`
Expected: prints `Run complete: <uuid>` and either counts or `No rows found`.

**Step 5: Commit**

```bash
git add scripts/test_trigger_backtest.py
git commit -m "feat: add trigger-only smoke runner summary"
```

---

### Task 6: Manual verification (optional)

**Files:**
- None

**Step 1: Apply schema (once per DB)**

```bash
/Users/pan/Documents/Github/Eiqora/.venv/bin/python - <<'PY'
from pathlib import Path
from data_collection.db.connection import get_connection

sql = Path("eiqora_v2/live/trigger_backtest_schema.sql").read_text()
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
print("Applied trigger_backtest_result schema")
PY
```

**Step 2: Run a longer period**

```bash
/Users/pan/Documents/Github/Eiqora/.venv/bin/python scripts/test_trigger_backtest.py --start-date 2025-05-01 --limit 5000
```

Expected: `Run complete: <uuid>` and non-empty outcome summary.
