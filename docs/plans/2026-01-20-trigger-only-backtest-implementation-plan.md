# Trigger-Only Backtest Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build trigger-only backtesting that stores ATR14-based outcomes in Postgres without LLM analysis.

**Architecture:** Add a new SQL schema for trigger-only results and a new backtest runner module that generates technical triggers, computes ATR brackets, scans forward across hourly bars, and inserts per-trigger outcomes. Core calculations are pure functions with unit tests.

**Tech Stack:** Python 3.10, psycopg, pytest, Postgres.

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

### Task 2: Add core trigger-only backtest helpers with tests

**Files:**
- Create: `eiqora_v2/live/trigger_backtest.py`
- Create: `tests/test_trigger_backtest.py`

**Step 1: Write the failing test**

```python
from datetime import datetime, timezone

import pytest

from eiqora_v2.live.trigger_backtest import compute_atr_brackets, resolve_outcome


def test_compute_atr_brackets():
    entry = 100.0
    atr14 = 2.0
    stop_loss, take_profit = compute_atr_brackets(entry, atr14, sl_mult=1.5, tp_mult=3.0)
    assert stop_loss == 97.0
    assert take_profit == 106.0


def test_resolve_outcome_tp_hit_first():
    entry_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [
        (entry_time, 107.0, 99.0, 106.5),
    ]
    outcome = resolve_outcome(entry_time, 100.0, 97.0, 106.0, bars)
    assert outcome["outcome"] == "TP_HIT"
    assert outcome["bars_to_outcome"] == 1


def test_resolve_outcome_same_bar_tie_sl_wins():
    entry_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [
        (entry_time, 106.0, 96.0, 100.0),
    ]
    outcome = resolve_outcome(entry_time, 100.0, 97.0, 106.0, bars)
    assert outcome["outcome"] == "SL_HIT"
    assert outcome["same_bar_tie"] is True


def test_resolve_outcome_no_hit_uses_final_close():
    entry_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    bars = [
        (entry_time, 101.0, 99.0, 100.5),
        (datetime(2026, 1, 2, 16, 30, tzinfo=timezone.utc), 101.5, 99.5, 101.0),
    ]
    outcome = resolve_outcome(entry_time, 100.0, 97.0, 106.0, bars)
    assert outcome["outcome"] == "NO_HIT"
    assert outcome["realized_pnl_pct"] == pytest.approx(0.01)
```

**Step 2: Run test to verify it fails**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/pytest tests/test_trigger_backtest.py::test_compute_atr_brackets -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eiqora_v2.live.trigger_backtest'`

**Step 3: Write minimal implementation**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


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

    # No hit
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
```

**Step 4: Run test to verify it passes**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/pytest tests/test_trigger_backtest.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add eiqora_v2/live/trigger_backtest.py tests/test_trigger_backtest.py
git commit -m "feat: add trigger-only backtest helpers"
```

---

### Task 3: Add DB integration and runner

**Files:**
- Modify: `eiqora_v2/live/trigger_backtest.py`
- Create: `eiqora_v2/live/backtest_triggers_only.py`
- Modify: `tests/test_trigger_backtest.py`

**Step 1: Write the failing test**

```python
import uuid
from datetime import datetime, timezone

from eiqora_v2.live.trigger_backtest import build_result_row


def test_build_result_row_defaults():
    run_id = uuid.uuid4()
    trigger_time = datetime(2026, 1, 2, 15, 30, tzinfo=timezone.utc)
    row = build_result_row(
        run_id=run_id,
        run_name="test-run",
        started_at=trigger_time,
        symbol="AAPL",
        trigger_type="vwap_reclaim",
        trigger_priority="HIGH",
        trigger_time=trigger_time,
        trigger_detail={"foo": "bar"},
        entry_price=100.0,
        atr14=2.0,
        stop_loss=97.0,
        take_profit=106.0,
        outcome="TP_HIT",
        outcome_time=trigger_time,
        bars_to_outcome=1,
        max_favorable_pct=0.07,
        max_adverse_pct=-0.03,
        realized_pnl_pct=0.06,
        sl_mult=1.5,
        tp_mult=3.0,
    )
    assert row[0] == run_id
    assert row[2] == "AAPL"
    assert row[5] == trigger_time
    assert row[9] == 1.5
    assert row[10] == 3.0
```

**Step 2: Run test to verify it fails**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/pytest tests/test_trigger_backtest.py::test_build_result_row_defaults -v`
Expected: FAIL with `AttributeError: module 'eiqora_v2.live.trigger_backtest' has no attribute 'build_result_row'`

**Step 3: Write minimal implementation**

Add to `eiqora_v2/live/trigger_backtest.py`:

```python
from typing import Any


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
) -> tuple[Any, ...]:
    return (
        run_id,
        run_name,
        symbol,
        trigger_type,
        trigger_priority,
        trigger_time,
        trigger_detail,
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

Create the runner `eiqora_v2/live/backtest_triggers_only.py` with:

```python
import argparse
import asyncio
import uuid
from datetime import datetime, timezone

from data_collection.db.connection import get_connection
from eiqora_v2.live.trigger_monitor import TriggerMonitor
from eiqora_v2.live.backtest_with_agents import get_hourly_bars_to_test
from eiqora_v2.live.trigger_backtest import compute_atr_brackets, resolve_outcome, build_result_row


def fetch_atr14(conn, symbol: str, asof_date) -> float | None:
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


def fetch_future_bars(conn, symbol: str, after_time) -> list[tuple]:
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


def insert_result(conn, row: tuple):
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


async def run_trigger_only_backtest(days: int, limit: int | None, run_name: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    started_at = datetime.now(timezone.utc)

    monitor = TriggerMonitor()
    test_bars = await get_hourly_bars_to_test(days, limit)

    with get_connection() as conn:
        for symbol, check_time in test_bars:
            triggers = await monitor.check_hourly_technical_triggers(symbol, check_time)
            for trigger in triggers:
                entry_price = trigger.details.get("current_price") if trigger.details else None
                if not entry_price:
                    entry_price = trigger.details.get("close") if trigger.details else None
                if not entry_price:
                    insert_result(conn, build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail={"no_data_reason": "missing_entry_price"},
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

                atr14 = fetch_atr14(conn, symbol, check_time.date())
                if not atr14:
                    insert_result(conn, build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail={"no_data_reason": "missing_atr14"},
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
                future_bars = fetch_future_bars(conn, symbol, check_time)
                if not future_bars:
                    insert_result(conn, build_result_row(
                        run_id=run_id,
                        run_name=run_name,
                        started_at=started_at,
                        symbol=symbol,
                        trigger_type=trigger.trigger_type,
                        trigger_priority=trigger.priority,
                        trigger_time=check_time,
                        trigger_detail={"no_data_reason": "missing_future_bars"},
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
                insert_result(conn, build_result_row(
                    run_id=run_id,
                    run_name=run_name,
                    started_at=started_at,
                    symbol=symbol,
                    trigger_type=trigger.trigger_type,
                    trigger_priority=trigger.priority,
                    trigger_time=check_time,
                    trigger_detail=trigger.details or {},
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


def main():
    parser = argparse.ArgumentParser(description="Trigger-only backtest")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--run-name", type=str, default="trigger-only")
    args = parser.parse_args()

    run_id = asyncio.run(run_trigger_only_backtest(args.days, args.limit, args.run_name))
    print(f"Run complete: {run_id}")


if __name__ == "__main__":
    main()
```

**Step 4: Run test to verify it passes**

Run: `/Users/pan/Documents/Github/Eiqora/.venv/bin/pytest tests/test_trigger_backtest.py::test_build_result_row_defaults -v`
Expected: PASS

**Step 5: Commit**

```bash
git add eiqora_v2/live/trigger_backtest.py eiqora_v2/live/backtest_triggers_only.py tests/test_trigger_backtest.py
git commit -m "feat: add trigger-only backtest runner"
```

---

### Task 4: Manual verification

**Files:**
- None

**Step 1: Run trigger-only backtest in dev**

Run:
```bash
/Users/pan/Documents/Github/Eiqora/.venv/bin/python -m eiqora_v2.live.backtest_triggers_only --days 30
```
Expected: Prints a run UUID; DB populated in `trigger_backtest_result`.

**Step 2: Verify counts**

Run:
```bash
/Users/pan/Documents/Github/Eiqora/.venv/bin/python - <<'PY'
from data_collection.db.connection import get_connection

RUN_ID = "<paste-run-id>"
with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT outcome, COUNT(*)
            FROM trigger_backtest_result
            WHERE run_id = %s
            GROUP BY outcome
            ORDER BY COUNT(*) DESC
            """,
            (RUN_ID,),
        )
        print(cur.fetchall())
PY
```
Expected: Non-empty rows by outcome.

**Step 3: Commit (optional)**

_No commit; verification only._
