# Multi-Agent System Critique & Recommendations

**Date:** January 17, 2026
**Scope:** `eiqora_v2` (Orchestrator, Agents, LLM Client)

## Executive Summary
The current `eiqora_v2` system implements a robust, albeit rigid, linear pipeline of LLM-based agents for swing trading analysis. It excels in structured output enforcement (Pydantic integration) and error recovery (schema validation retries). However, the sequential architecture creates significant latency bottlenecks, and the reliance on LLMs for deterministic safety checks introduces unnecessary risk.

## 1. Architectural Analysis

### 1.1 Chain vs. Graph Architecture
**Current State:** The `Orchestrator` implements a linear "Chain" architecture (`TopDown -> Context -> Chart -> ... -> Decision`).
**Industry Standard:** Modern autonomous systems favor "Graph" architectures (e.g., LangGraph) that allow for:
- **Parallelism:** Running independent agents (e.g., `Fundamental` and `Chart`) simultaneously.
- **Dynamic Branching:** Terminating early if a setup is invalid (e.g., `ChartAgent` returns `NO_SETUP`) rather than running the full costly pipeline.
- **Loops:** Iterating on analysis if data is ambiguous (e.g., "Check news again for specific date").
**Critique:** The current linear approach is token-inefficient and slow. A move to an async DAG (Directed Acyclic Graph) would reduce end-to-end latency by ~40% and token costs by ~30% (by skipping invalid setups).

### 1.2 Configuration Management
**Current State:** Business logic (e.g., `MEGA_CAP_GATES`) is hardcoded in Python files.
**Critique:** This requires code deploys to tune trading parameters.
**Recommendation:** Externalize all risk gates, thresholds, and prompts into a `config.yaml` or database, allowing for hot-reloading and easier backtesting optimization.

## 2. Detailed Agent Analysis

### 2.1 TopDown Agent (Macro)
- **Strengths:** correctly identifies market regime, sector rotation, and policy risks.
- **Weakness:** Its output is descriptive ("Risk Off") rather than functional. It does not mechanically tighten risk parameters for downstream agents.
- **Recommendation:** `TopDown` should output a `risk_modifier` (e.g., `0.5x` position size) that mathematically adjusts the `DecisionAgent`'s constraints.

### 2.2 Chart & Context Agents (Technical)
- **Strengths:** `ContextAgent` correctly uses deterministic code for indicators, avoiding LLM math hallucinations. `ChartAgent` uses a clear taxonomy of setups.
- **Weakness:** `ChartAgent` is limited to pre-fetched price data. It cannot "zoom in" to hourly charts if the daily is ambiguous.
- **Recommendation:** Allow `ChartAgent` to request higher-resolution data tools dynamically.

### 2.3 RedTeam vs. ShortPerspective (Safety)
- **Design Pattern:** The separation of `RedTeam` (internal stress test) and `ShortPerspective` (external contrarian) is a **best-in-class design**.
- **Critical Weakness 1 (Shallow Logic):** The agent is **Technical-Only**. It completely ignores Fundamentals (Insider Selling, Earnings), News Sentiment, and Event Risks.
    - *Impact:* It cannot spot key short setups like "Price High + Insider Dumping" or "News Fade" strategies. It is effectively blind to half the market signal.
- **Critical Weakness 2 (Bad Math):** The agent asks the LLM to perform arithmetic scoring (e.g., "+2 if RSI > 70"). LLMs are poor calculators.
- **Recommendation:**
    1.  **Expand Data Scope:** Inject `fundamental` (Insider, Sentiment) and `facts` into the agent's context.
    2.  **Hybrid Scoring:** Calculate a **Multi-Factor Score** in Python:
        - `Tech_Score` (RSI, Resistance)
        - `Fund_Score` (Insider Net Sells, Valuation)
        - `Sent_Score` (News Sentiment Divergence)
    3.  **Qualitative Rebuttal:** Task the LLM with synthesizing these 3 scores into a holistic "Bear Case."

### 2.4 Veto Agent (Final Gate)
- **CRITICAL FLAW:** The `VetoAgent` relies on an LLM to enforce hard safety rules (e.g., "Don't trade if earnings < 2 days").
- **Risk:** LLMs are probabilistic. An LLM might hallucinate that "1 day is > 2 days" or ignore the instruction.
- **Recommendation:** **Replace `VetoAgent` with a deterministic Python class.** Hard rules (Earnings Dates, FDA approvals, PnL stops) must be code-enforced, not prompt-enforced.

### 2.5 Narrative Agent (XAI)
- **Strengths:** Provides excellent "Explainable AI" (XAI) utility, building trust with human operators.
- **Recommendation:** Keep as is. This is high-value for the "Human-in-the-Loop" workflow.

## 3. Missing Capabilities

### 3.1 Supply Chain Analysis
- **Gap:** The `SupplyChainAgent` is referenced but seemingly inactive/unimplemented.
- **Opportunity:** In modern markets, second-order effects (e.g., TSMC earnings beat -> NVDA bullish) are powerful alpha sources.
- **Recommendation:** Prioritize implementing a graph-based supply chain lookup to validate sector theses.

### 3.2 Dynamic Tooling
- **Gap:** Agents operate on "pushed" data (pre-fetched). They cannot "pull" new data.
- **Recommendation:** Give `IdeaGenerator` or `Fundamental` agents access to specific search/lookup tools to resolve ambiguities (e.g., "What was the specific catalyst for yesterday's 5% drop?").

## 4. Reliability & Error Handling

### 4.1 Failure Propagation
**Current State:** Pipeline continues even if critical upstream data fails.
**Recommendation:** Implement "Circuit Breakers". If `ContextAgent` fails (no price data), the pipeline for that ticker must abort immediately.


## Summary of Action Items

### High Priority (Safety & Architecture)
1.  **Refactor `VetoAgent`:** Convert from LLM to deterministic Python logic.
2.  **Implement DAG Orchestrator:** Move from linear list to async graph (parallel execution & early stopping).
3.  **Circuit Breakers:** Enforce "Fail Fast" on missing data.

### Medium Priority (Alpha Generation)
4.  **Connect `TopDown` to Risk:** Make macro regime automatically adjust position sizing/stops.
5.  **Implement `SupplyChainAgent`:** Add second-order correlation checks.

### Low Priority (Optimization)
6.  **Externalize Config:** Move gates to `yaml`.
7.  **Dynamic Tooling:** Allow agents to request more data.
