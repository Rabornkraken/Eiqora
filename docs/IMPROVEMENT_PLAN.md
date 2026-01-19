# Backend Improvement Plan

## Executive Summary
The backend is a four-layer system (agents, orchestrators, services, live pipeline). The current design works but has duplicated orchestration paths, uneven service integration, and limited reuse of agent outputs across triggers. This plan focuses on unifying execution, improving observability and consistency, and reducing redundant work.

## Current Architecture
### Layer 1: Agents (LLM + deterministic)
Located in `eiqora_v2/agents/`

- TopDownAgent: market regime
- ContextAgent: multi-timeframe context
- ChartAgent: setup classification
- FundamentalAgent: news/SEC/earnings context
- IdeaGeneratorAgent: trade ideas
- ExitPolicyAgent: TP/SL definition
- RedTeamAgent: stress testing
- DecisionAgent: GO/NO_GO
- VetoAgent: sanity checks
- NarrativeAgent: trade narrative (conditional)
- PositionReassessmentAgent: post-entry reassessment
- PositionManagerAgent: sizing override (live)

### Layer 2: Orchestrators
- `Orchestrator`: base sequential chain
- `BacktestOrchestrator`: point-in-time + caching
- `LiveTradingOrchestrator`: adds position manager + veto

### Layer 3: Services (utilities)
- `profile_generator.py`: profile build
- `risk_model.py`: deterministic sizing
- `metrics.py`: system metrics
- `signal_aggregator.py`: signal consolidation (unused)
- `sweep.py`: batch runner

### Layer 4: Live Pipeline
- `CandidateSelector`: daily watchlist
- `TriggerMonitor`: entry triggers
- `PositionMonitor`: exit triggers
- `SignalManager`: signal storage
- `LiveTradingOrchestrator`: per-trigger analysis
- `ProfileGenerator`: pre-loads profile

## Key Issues
- **Duplicated orchestration**: multiple orchestrator variants with overlapping logic.
- **Service integration gaps**: some services run outside the agent chain or are unused.
- **Limited reuse**: repeated analyses for the same symbol without a decision cache.
- **Inconsistent data freshness**: trigger logic and pricing updates can diverge without explicit freshness checks.
- **Observability gaps**: no unified audit trail for suppressed triggers and per-stage latency.

## Proposed Improvements
### 1) Unified Agent Registry
Create a central registry that resolves dependencies and allows selective or parallel execution.

```python
class AgentRegistry:
    def __init__(self):
        self.agents = {}
        self.dependencies = {}

    def register(self, name, agent, depends_on=None):
        self.agents[name] = agent
        self.dependencies[name] = depends_on or []

    async def run(self, name, state):
        for dep in self.dependencies[name]:
            if dep not in state:
                state.update(await self.run(dep, state))
        return await self.agents[name].run(state)
```

Benefits:
- Selective execution (skip non-essential agents)
- Clear dependency ordering
- Easier reuse across live/backtest/sweep

### 2) Context Enrichment Bridge
Centralize deterministic service calls (profile, risk model, market data) and attach results before LLMs run.

Benefits:
- Fewer redundant calls
- Cleaner agent prompts
- Easier caching

### 3) Unified Trigger Handling
Standardize trigger ingestion with a single dispatcher that handles:
- entry trigger scans
- exit condition checks
- account refresh updates

Benefits:
- Fewer race conditions between entry/exit logic
- Consistent time handling
- Simplified scheduling

### 4) Decision Cache
Use `analysis_log` + `trigger_hash` as a decision cache to prevent re-analysis on unchanged triggers.

Benefits:
- Lower LLM usage
- Consistent decisions within a window
- Faster responses

### 5) Configuration Unification
Move live/backtest orchestrator options into a single config object with mode presets.

Benefits:
- Fewer code paths
- Easier testing
- Reduced drift between modes

## Implementation Roadmap
### Phase 1: Context + Cache (1–2 days)
- Add a unified context enrichment step
- Implement decision cache lookups
- Add basic freshness checks

### Phase 2: Agent Registry (2–3 days)
- Introduce `AgentRegistry`
- Migrate orchestrators to registry
- Enable selective execution

### Phase 3: Trigger Unification (3–4 days)
- Consolidate entry/exit trigger handling
- Add observability for trigger sources and suppression

### Phase 4: Config Unification (1 day)
- Replace orchestrator variants with config presets

## Open Decisions
- Cache TTL window for decision reuse
- Minimum freshness requirements for hourly indicators
- Trigger priority rules when multiple signals arrive together
