# Trigger-Only Backtest Design

**Goal:** Add trigger-only backtesting that stores outcomes in Postgres, using daily ATR14 brackets and no time stop, to evaluate whether technical triggers are good entries.

## Architecture

- Add a new table `trigger_backtest_result` to store per-trigger outcomes for a run.
- Reuse trigger detection from `TriggerMonitor.check_hourly_technical_triggers`.
- Compute entry + ATR14-based brackets from daily data (`market_bar_daily.atr_14`).
- Resolve outcome by scanning forward across all available hourly bars for the symbol.
- Record outcomes and excursion stats without any LLM analysis.

## Data Flow

1. Iterate historical hourly bars (same selection logic as the existing trigger backtest).
2. Generate technical triggers for each bar.
3. Resolve entry price from trigger details or hourly close at the trigger time.
4. Fetch ATR14 from the latest daily bar on or before the trigger date.
5. Compute brackets: SL = entry - 1.5 * ATR14, TP = entry + 3.0 * ATR14.
6. Scan forward until data ends and record the first hit (TP or SL).
7. If no hit, record `NO_HIT` and final close PnL.
8. Store a row in `trigger_backtest_result` with full context.

## Outcome Rules

- TP/SL first-hit wins; if both hit within the same bar, record `SL_HIT` (conservative).
- No time stop. Scan until the last available hourly bar.
- If entry price, ATR14, or future bars are missing, record `NO_DATA` and include a reason in `trigger_detail`.

## Schema

Create a new table to keep results self-contained by run:

- `result_id` UUID PK
- `run_id` UUID
- `run_name` TEXT
- `started_at`, `completed_at` TIMESTAMPTZ
- `symbol` VARCHAR(10)
- `trigger_type` VARCHAR(50)
- `trigger_priority` VARCHAR(20)
- `trigger_time` TIMESTAMPTZ
- `trigger_detail` JSONB
- `entry_price` DECIMAL(12,4)
- `atr14` DECIMAL(12,6)
- `sl_mult` DECIMAL(6,3) default 1.5
- `tp_mult` DECIMAL(6,3) default 3.0
- `stop_loss` DECIMAL(12,4)
- `take_profit` DECIMAL(12,4)
- `outcome` VARCHAR(20) (TP_HIT, SL_HIT, NO_HIT, NO_DATA)
- `outcome_time` TIMESTAMPTZ
- `bars_to_outcome` INT
- `max_favorable_pct` DECIMAL(8,4)
- `max_adverse_pct` DECIMAL(8,4)
- `realized_pnl_pct` DECIMAL(8,4)

Indexes: `run_id`, `symbol`, `trigger_type`, `outcome`, `trigger_time`.

## Implementation Notes

- Prefer a new runner module (e.g. `eiqora_v2/live/backtest_triggers_only.py`) to keep trigger-only logic separate from agent backtests.
- Use the same DB connection style as other backtest utilities (psycopg via `data_collection.db.connection`).
- Store `run_id`/`run_name` per row instead of a separate run table for now.

## Error Handling

- Missing entry price, ATR14, or future bars: write a result with `NO_DATA` and a short reason in `trigger_detail`.
- If future bars exist but neither TP nor SL hit, write `NO_HIT` and compute PnL from the final close.

## Testing

- Unit tests for bracket computation and outcome resolution (including same-bar TP+SL behavior).
- Minimal integration test that inserts a single result row inside a transaction and rolls back.

## Example Queries

- Win rate by trigger type:
  ```sql
  SELECT trigger_type,
         COUNT(*) FILTER (WHERE outcome = 'TP_HIT')::float / COUNT(*) AS win_rate
  FROM trigger_backtest_result
  WHERE run_id = $1 AND outcome != 'NO_DATA'
  GROUP BY trigger_type;
  ```

- Distribution of outcomes:
  ```sql
  SELECT outcome, COUNT(*)
  FROM trigger_backtest_result
  WHERE run_id = $1
  GROUP BY outcome
  ORDER BY COUNT(*) DESC;
  ```
