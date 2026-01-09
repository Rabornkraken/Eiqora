# Eiqora Improvements Plan (Actionable Roadmap)

This plan targets the critiques that still apply and lays out concrete work items,
dependencies, and acceptance criteria.

## Scope Summary
- Deterministic risk/position sizing (replace LLM sizing).
- Red-team/anti-thesis agent before final decision.
- Relative strength (ticker vs sector ETF vs SPY) in context/decisioning.
- Second-order triggers using existing data (bad-news-no-drop, sector-laggard, compression).
- ETL for ghost data (sec_ftd) into structured tables.
- Backfills, monitoring queries, and doc updates.

## Phase 1: Scope Confirmation (Required)
**Goal:** Lock scope, metrics, and rollout order before coding.

Questions to confirm:
1. Risk model: Volatility-targeted position sizing only, or include max risk per trade,
   portfolio heat, and cluster caps?
2. Red-team agent: Should its output be *mandatory* for every trigger,
   or only for GO-leaning setups?
3. Relative strength: Which benchmarks are required (SPY + sector ETF only, or also QQQ)?
4. Trigger upgrades: Enable all three second-order triggers immediately, or phase in?
5. ETL: Which "ghost data" sources are priority (sec_ftd)?
6. Backfill windows: How far back for new tables (e.g., 2 years, 5 years)?

## Phase 2: Deterministic Risk & Sizing (Core)
**Work items**
- Add `risk_model.py` for deterministic sizing and portfolio caps.
- Wire into live pipeline to compute position_size_pct for GO decisions.
- Remove/ignore LLM sizing outputs in PositionManager/Decision.

**Acceptance**
- Position size is deterministic per input (repeatable).
- Sizing uses volatility (ATR/RV20) + max risk per trade.
- Portfolio caps enforced (max positions and cluster caps).

## Phase 3: Red-Team Reasoning
**Work items**
- Add RedTeamAgent to argue against the thesis (bear case + negative news + macro).
- Add synthesizer step or integrate RedTeam output into Decision prompt.
- Ensure Decision gates explicitly reference red-team objections.

**Acceptance**
- Every GO decision includes a “red-team response” summary.
- GO is blocked if red-team flags critical thesis break.

## Phase 4: Relative Strength Context
**Work items**
- Compute ticker vs sector ETF and ticker vs SPY relative strength.
- Add to Context/Chart outputs and Decision prompts.
- Store relative-strength metrics in analysis_log.

**Acceptance**
- Relative strength appears in agent outputs.
- Decision references relative strength in rationale for GO/NO_GO.

## Phase 5: Trigger Upgrades (Second-Order)
**Work items**
- “Bad news, no drop” (negative sentiment + flat/green price).
- Sector laggard trigger (sector breakout, ticker lags).
- Volatility compression trigger (NR7 / low 5d volatility).
- Guardrails: minimum volume, price validity, recency.

**Acceptance**
- New triggers appear in trigger_monitor with documented rules.
- False positives reduced with guards.

## Phase 6: ETL for Ghost Data
**Work items**
- Parse SEC FTD ZIP → `ftd_daily`.
- Parse SEC FTD ZIP → `sec_ftd`.
- Add lightweight aggregations into `signal_aggregator` / `fundamental`.

**Acceptance**
- New tables populated + queryable.
- Fundamental agent includes new signals.

## Phase 7: Backfills, Monitoring, Docs
**Work items**
- Backfill new tables to agreed horizon.
- Add monitoring queries for data freshness.
- Update `docs/system_architecture.md` and `docs/DATABASE_UPDATES.md`.

**Acceptance**
- Backfills complete with expected row counts.
- Docs reflect new architecture and data sources.
