# Eiqora V2 System Analysis (Codex)

## Part 1: Core Architecture & Risk Critique

### 1. Reckless Risk Management
**Location:** `eiqora_v2/agents/position_manager.py`
**Critique:**
The system delegates position sizing and portfolio construction to an LLM (`PositionManagerAgent`).
*   **The Hazard:** LLMs are probabilistic engines, not calculators. Asking an LLM to "decide if we should reduce size" based on a text prompt introduces non-deterministic variance into risk control.
*   **The Competitor Approach:** Risk management must be **mathematically deterministic**. A "Codex" system would use Volatility Targeting, Kelly Criterion, or Mean-Variance Optimization (MVO) for sizing, using the LLM *only* to adjust the confidence scalar, not the math itself.

### 2. "Retail" Alpha Factors
**Location:** `eiqora_v2/tools/prices.py`, `eiqora_v2/live/scanner.py`
**Critique:**
The signal generation relies heavily on standard library indicators: RSI, MACD, Bollinger Bands, and Moving Averages.
*   **Alpha Decay:** These signals have been arb'd out of the market decades ago.
*   **Arbitrary Scoring:** The `LiveScanner` uses hardcoded "magic numbers" (e.g., `scores["rsi_oversold"] = 0.15`) that have no statistical basis.
*   **The Competitor Approach:** Institutional systems use alternative data (credit card receipts, satellite imagery, dark pool volume), market microstructure (order book imbalance), or custom feature engineering trained on XGBoost/LightGBM, not hardcoded `if rsi < 30` logic.

### 3. Architectural Bottlenecks
*   **Synchronous Blockers:** While the system uses `asyncio`, the `metrics.py` and some logging features can introduce blocking I/O if not carefully managed under high load.
*   **State Bloat:** The `SwingTradeState` passes *everything* around. In a high-frequency loop, serializing/deserializing massive JSON blobs for every agent step adds unnecessary latency.
*   **Database as IPC:** Using Postgres tables (`trade_signal`, `analysis_log`) for inter-process communication between the Scanner and the Orchestrator is slow. A message queue (Redis/RabbitMQ) is the standard for live event-driven architectures.

## Part 2: Agent Workflow & Trigger Critique

### 4. Critique of the Agent Flow (The Graph)
**Location:** `eiqora_v2/graph/swing_trade.py`

The current graph architecture is a **Directed Acyclic Graph (DAG)**:
`Start` → `Parallel Analysis` → `Filter` → `Idea` → `Decision`.

**The Flaw: Linearity and Confirmation Bias.**
The flow is too linear. The `IdeaGenerator` creates a thesis, and subsequent agents (`ExitPolicy`, `Decision`) largely operate *on top of* that thesis. If the `IdeaGenerator` hallucinates a bullish bias because it ignored a bearish macro signals, the downstream agents often just "optimize" that bad idea rather than rejecting it until the very end.

**Proposed Architecture Change: The "Debate" Loop**
You need a feedback loop before the final decision.
*   **Current:** `Idea Generator` (Bullish Thesis) → `Decision`.
*   **Better:** `Idea Generator` (Thesis) → **`Red Team Agent`** (Anti-Thesis) → `Synthesizer` → `Decision`.

The **Red Team Agent** should specifically be prompted to find reasons *not* to trade, looking exclusively at the Bear Case profile, negative news, and adverse macro conditions. The system currently relies on `VetoAgent` for this, but `Veto` is implemented as a "Sanity Check" (safety rules), not an active intellectual adversary.

### 5. Critique of Agent Context (Data Isolation)
**Location:** `eiqora_v2/agents/chart.py` and `eiqora_v2/agents/topdown.py`

**The Flaw: The Chart Agent is Blind to Regime.**
In `graph/swing_trade.py`, `ChartAgent` runs in parallel with `TopDown` (via `context_node` or implicitly via state).
*   **Problem:** A "Pullback to MA20" in a **Risk-On** market is a high-probability buy. A "Pullback to MA20" in a **Risk-Off** market is often the first leg of a collapse.
*   **Fix:** The `ChartAgent` should *not* be parallel to `TopDown` or `Context`. It should be **downstream**. The Chart Agent must know the Market Regime (Bull/Bear/Chop) *before* it classifies the setup. It should be prompted: *"Given we are in a High Volatility Bear Market, evaluate this chart."*

### 6. Missing Agent Capability: "Relative Value"
The system analyzes the ticker in isolation (Absolute Analysis). It lacks **Relative Analysis**.
*   **Missing Agent:** **`SectorRelativityAgent`**.
*   **Function:** Compare the ticker to its Sector ETF (e.g., NVDA vs. XLK) and the Sector vs. SPY.
*   **Why:** Even with free data, you can calculate Relative Strength (RS). Buying a stock that is making new highs while its sector is flat is a far stronger signal than buying a stock rising with the tide. The current `ContextAgent` looks at the stock's trend, but not its *performance relative to peers*.

### 7. Critical Review of Triggers (Out-of-the-Box Thinking)
**Location:** `eiqora_v2/live/trigger_monitor.py`

The current triggers are "First-Order" events (Earnings Release, 8-K, Price Level Hit). These are crowded and often result in "buying the news" (exit liquidity).

Since you are relying on **Free Data** and **Human Decision**, your edge is **Synthesis** and **Second-Order Effects**, not speed.

**New Trigger Concepts:**

*   **A. The "Dog That Didn't Bark" Trigger (Divergence)**
    *   *Logic:* Bad News + Price Goes UP (or stays flat).
    *   *Implementation:* If `Sentiment < Negative Threshold` AND `Price Change > -0.5%` (Intraday), trigger a **"Seller Exhaustion"** signal. This indicates all sellers have sold; the path of least resistance is up.

*   **B. The "Sector laggard" Trigger**
    *   *Logic:* Sector ETF (e.g., XLK) breaks 20-day high. Target Ticker (e.g., AAPL) is still 2% below its 20-day high.
    *   *Implementation:* Trigger a "Catch-up Play". This gives the human trader a window to enter before the rotation hits the specific stock.

*   **C. Volatility Compression (The Coiled Spring)**
    *   *Logic:* Current Triggers look for "Volume Surge" (action has started). You want to be there *before*.
    *   *Implementation:* Trigger when `Standard Deviation(Last 5 Days)` is at a 3-month low (NR7 / Compression). This signals a massive move is imminent, allowing the human to set alert brackets.

*   **D. The "Analyst Cluster" Trigger**
    *   *Logic:* One analyst upgrade is noise. Three analysts upgrading within 48 hours is a campaign.
    *   *Implementation:* Scrape the count of analyst revisions (often available in free YFinance/Benzinga data). Trigger if `count(upgrades_last_48h) >= 2`.

### 8. Data & "YFinance" Reality Check
Since you are keeping `yfinance`:
*   **The Trap:** `yfinance` data is adjusted for dividends/splits by default. Technical indicators (like RSI) calculated on adjusted close can differ from what other traders see on their live charts (unadjusted).
*   **Action:** Ensure your `get_prices` tool explicitly requests `auto_adjust=False` or handles adjustments consistently so your "MA20" matches what the human trader sees on their brokerage interface.

## Summary of Recommendations

1.  **Re-wire the Graph:** Make `TopDown` (Regime) an input to `ChartAgent`. Don't run them parallel. Context changes the meaning of the chart.
2.  **Add `RedTeamAgent`:** Force an LLM to argue *against* the trade before the `DecisionAgent` sees it.
3.  **Implement Relative Strength:** Add a check for `Ticker Performance / SPY Performance` logic.
4.  **Implement "Divergence" Triggers:** Trigger on *Price resilience in the face of bad news* (or vice versa).
5.  **Rewrite Risk:** Remove LLM sizing. Implement mathematical position sizing.
6.  **Factor Upgrade:** Move beyond RSI/MACD. Integrate volume profile or relative strength.