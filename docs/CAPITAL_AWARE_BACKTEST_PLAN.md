# Capital-Aware Daily Trigger Backtest

## Context

The current `backtest_daily_triggers.py` tracks capital **post-hoc** — all trades fire regardless of available capital, then `_update_run_metrics()` replays them sequentially to compute equity curves. This inflates trade counts (trades execute even when capital is exhausted), ignores concurrent position limits (100 trades could fire on one day), and understates drawdown (no mark-to-market during overlapping positions). The backtest also has no time-based exit, so NO_HIT trades can lock capital indefinitely.

## What Changes

1. **Capital tracked in the main loop** — trades are skipped if cash is insufficient
2. **Max concurrent positions** (default 20) — trades skipped when all slots full
3. **Max hold days** (default 30) — force-close NO_HIT trades after N trading days
4. **Date-batched trigger processing** — triggers on the same date are prioritized (HIGH > MEDIUM > LOW) before capital allocation
5. **SKIPPED outcome** — trades that couldn't execute due to capital/slots are recorded with reason

## Files to Modify

### 1. `eiqora_v2/live/backtest_daily_triggers.py` (primary)

**Add new constants and dataclasses:**
- `POSITION_PCT`, `MIN_POSITION_DOLLARS`, `MAX_POSITIONS_DEFAULT`, `MAX_HOLD_DAYS_DEFAULT`, `COMMISSION_PER_ORDER`, `SLIPPAGE_PCT` constants
- `OpenPosition` dataclass — tracks symbol, entry/exit dates, position size, PnL, outcome
- `PendingTrigger` dataclass — holds a trigger + pre-fetched entry price/ATR for date-batching

**Add helper functions:**
- `close_expired_positions(current_date, open_positions, cash)` — closes positions whose precomputed exit_date <= current_date, returns freed cash
- `try_open_position(pending, cash, open_positions, max_positions)` — checks capital availability and slot limits, returns `(position_size, skip_reason)`
- `_process_date_batch(pending_triggers, ...)` — sorts triggers by priority, applies capital/slot checks, resolves outcomes, opens positions, inserts results

**Restructure `run_daily_backtest()`:**
- Add params: `max_positions: int = 20`, `max_hold_days: int = 30`
- Replace the current single-pass loop with date-batched processing:
  ```
  For each (symbol, date) in test_bars:
    On date boundary:
      1. Process previous date's pending triggers (prioritized, capital-checked)
      2. Close expired positions → free cash
      3. Record equity snapshot
    Detect triggers → collect as PendingTrigger
  After loop: process final batch, force-close remaining positions
  ```
- Track `cash`, `open_positions`, `equity_curve`, skip counters throughout
- Truncate `future_bars[:max_hold_days]` before calling `resolve_outcome()` (same pattern as `backtest_daily_confluence.py` line 332-334)

**Update `_update_run_metrics()`:**
- Accept equity curve and skip counters from the loop
- Store new metrics in `parameters` JSONB: `skipped_no_capital`, `skipped_max_positions`, `max_concurrent`, `avg_concurrent`
- Use loop's final capital as authoritative (keep post-hoc replay as verification)

### 2. `scripts/test_daily_backtest.py`

- Add CLI args: `--max-positions` (default 20), `--max-hold-days` (default 30)
- Pass to `run_daily_backtest()`
- Add "PORTFOLIO MANAGEMENT" section to summary output showing: max positions, max hold days, peak concurrent, skipped counts

### 3. `eiqora_v2/live/trigger_backtest.py` — NO CHANGES

`resolve_outcome()` and `compute_atr_brackets()` stay unchanged. Max hold is handled by truncating `future_bars` before calling `resolve_outcome()`.

### No Database Schema Changes

- "SKIPPED" fits in existing `outcome VARCHAR(20)` column
- New per-trade fields (`position_size_dollars`, `capital_at_entry`, `skip_reason`) go in existing `trigger_detail JSONB`
- New run metrics go in existing `parameters JSONB`

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Capital model | Event-driven (open/close), not bar-by-bar MTM | Bracket exits have known prices; bar-by-bar is 10x slower for minimal accuracy gain |
| Position sizing | `min(total_equity * 5%, available_cash)` | Prevents leverage; caps at what's actually available |
| Same-bar TP+SL tie | SL_HIT (unchanged) | Conservative, already implemented |
| Trigger prioritization | HIGH > MEDIUM > LOW within each date | Uses existing `trigger.priority` field |
| Multiple triggers same symbol same date | First (highest priority) wins | Others get `skip_reason: "already_in_position"` |
| Backward compatibility | `max_positions=999, max_hold_days=0` reproduces old behavior | Defaults (20/30) are realistic but configurable |

## Edge Cases

- **Capital exhausted**: all new trades skipped, existing positions continue resolving
- **End of backtest with open positions**: force-closed at last available close price
- **NO_HIT within max_hold window**: exits at last bar's close, capital freed
- **Cash can't go negative**: `try_open_position()` caps position_size at available cash

## Current vs Realistic Backtest Comparison

### What the Current System Gets Right
- Next-bar-open entry for technicals (no look-ahead bias)
- ATR-adaptive brackets (SL/TP)
- Per-symbol position lock (no stacking)
- Conservative same-bar tie resolution (SL_HIT)
- Transaction cost modeling ($1/order + 0.15% slippage)

### What's Missing (Ranked by Impact)
| Priority | Feature | Impact |
|----------|---------|--------|
| 1 | Real-time capital tracking in main loop | HIGH — inflates trade count |
| 2 | Max concurrent positions | HIGH — unbounded exposure |
| 3 | Time-based exit (max hold days) | MEDIUM — capital locked in NO_HIT trades forever |
| 4 | Trade prioritization when at capacity | MEDIUM — forced to pick signals |
| 5 | NO_HIT trades excluded from capital | MEDIUM — understates capital impact |

## Verification

1. Run with small date range (e.g., 1 month) and verify skip counts make sense
2. Run with `--max-positions 999 --max-hold-days 0` and compare to old results (should be very similar)
3. Run full 2016-2026 period with defaults and compare: expect fewer trades, lower drawdown, more realistic
4. Check that `final_capital` from loop matches post-hoc replay (sanity check)
