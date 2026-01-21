# Trigger Confluence and Exit Logic (Proposed)

## Goal
Align stop-loss / take-profit logic with trigger type and improve trigger quality
through explicit confluence. This document captures the reasoning and a practical
plan before code changes.

## Exit Logic by Trigger Type (Structure-First)
All triggers should remain structure-first and then validate against volatility:
- SL must be at least 1.5x ATR away and at least 3% from entry.
- TP should meet at least 1:1.5 reward/risk (structure-first, then RR floor).

### 1) CHART_SETUP (Pure Technical)
- SL: swing low/high or invalidation level; then ATR + min distance guard.
- TP: structure resistance/support; then RR floor.
- Time stop: 10-20 trading days.

### 2) BREAKOUT Trigger
- SL: below breakout level or consolidation low; ATR guard applies.
- TP: next resistance or measured-move target; RR floor applies.
- Time stop: shorter if breakout fails early.

### 3) PULLBACK Trigger
- SL: below swing low or MA50; ATR guard applies.
- TP: prior high/upper range; RR floor applies.
- Time stop: standard.

### 4) REVERSAL Trigger
- SL: below pivot low/high; wider room is typical; ATR guard applies.
- TP: mid-range or first resistance/support; RR floor applies.
- Time stop: medium, avoid over-holding.

### 5) NEWS / SEC / EVENT Trigger
- SL: structure-first but wider to avoid event noise; ATR guard applies.
- TP: often no fixed target; use reassessment or modest RR floor.
- Time stop: shorter, edge decays fast.

### 6) EARNINGS Trigger
- SL: widest; structure-first but with larger volatility guard.
- TP: modest and fast or reassess-only; RR floor applies if fixed.
- Time stop: very short (1-5 days).

### 7) MACRO / REGIME Trigger
- SL: standard structure; ATR guard applies.
- TP: conservative; avoid long holds in regime uncertainty.
- Time stop: shorter in high-vol regime.

### 8) MULTI-TRIGGER (Confluence)
- SL: allow more room if confluence is strong.
- TP: can be more ambitious (structure + higher RR).
- Time stop: longer if conviction is supported by multiple signals.

### 9) HOURLY / INTRADAY Trigger
- SL/TP: should reference hourly structure or blended levels.
- Time stop: short; reevaluate quickly.

## Trigger Confluence Improvements
Current triggers fire independently and can overreact to a single weak signal.
Confluence should gate entry and calibrate sizing and exit logic.

### A) Normalize Trigger Evidence
Define a standard schema per trigger:
- trigger_type
- strength_score (0-1)
- freshness (minutes/hours)
- direction (LONG/SHORT)
- supporting_features (list)
- disqualifiers (list)

### B) Confluence Scoring
Aggregate into a single confluence score:
- Base: weighted average of trigger strength.
- Bonuses:
  - Multiple independent trigger types in same direction.
  - Agreement with higher timeframe trend.
  - Freshness within a tight window.
- Penalties:
  - Conflicting trigger directions.
  - Stale triggers.
  - Low data quality flags.

### C) Confluence Tiers
Define tiers that affect decision logic:
- Tier 0 (No confluence): block or force review.
- Tier 1 (Weak): allow only top-quality setups; tighter time stop.
- Tier 2 (Moderate): standard rules.
- Tier 3 (Strong): allow wider SL and more ambitious TP.

### D) Trigger Coordination Rules
Prevent redundant or conflicting signals:
- If a higher-tier trigger exists, lower-tier triggers should not override it.
- If triggers conflict (e.g., NEWS negative + TECH long), require review.
- Use a short "confluence window" (e.g., 2-6 hours) for multi-trigger joins.

### E) Exit Policy Coupling
Exit logic should read the confluence tier:
- Higher tier -> allow wider SL, higher TP, longer time stop.
- Lower tier -> smaller SL (but still >= minimum), smaller TP, shorter stop.

## Proposed Next Steps (No Code Yet)
1) Document the existing trigger types and current scoring inputs.
2) Define the confluence schema and scoring rules.
3) Map confluence tier to exit-policy parameters and decision gates.
4) Add a validation report to compare pre/post confluence outcomes.
