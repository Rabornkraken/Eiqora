# Data Pipeline Review & Roadmap

## Executive Summary
The current `eiqora_v2` data pipeline is excellent at **ingestion** (downloading raw files) but poor at **synthesis** (making data usable for agents). Some high-value datasets (FTD) were being collected as raw "blobs" (ZIP/HTML) but were never parsed into structured database tables. Consequently, the Trading Agents were blind to them.

## 1. Critical Gaps: "The Ghost Data"
The system collects the following data but **does not use it**:

| Source | Current State | Issue | Impact |
| :--- | :--- | :--- | :--- |
| **SEC FTD** | `sec_ftd/etl.py` downloads ZIPs and loads `sec_ftd`. | Structured table available. | Agents can see short squeeze risk. |

**Recommendation:**
Implement **ETL Parsers** for these three sources immediately. Convert raw ZIP/HTML into queryable Postgres tables (`ftd_daily`, `congress_trading`, `insider_trades`).

## 2. Missing High-Value Data (Free/Cheap)

### A. Options Gamma Exposure (GEX)
*   **Why:** Market makers hedging option positions create massive support/resistance "walls" that pure price charts don't show.
*   **Source:** `yfinance` (Free).
*   **Implementation:** 
    1. Fetch Option Chain: `yf.Ticker(sym).option_chain(date)`
    2. Calculate GEX: `Gamma * Open Interest * 100 * Spot Price`
    3. Store `gex_profile` (Strike Price vs Net Gamma) in DB.
    4. **Agent Use:** Chart Agent checks if price is approaching a "Zero Gamma" or "High Gamma" wall.

### B. Retail "Hype" Flow
*   **Why:** News sentiment (FinBERT) measures *editorial* tone. It misses *retail* mania (e.g., GME/AMC/TSLA hype cycles).
*   **Source:** Reddit API (PRAW - Free tier).
*   **Implementation:**
    1. Scrape `r/wallstreetbets`, `r/stocks`, `r/options`.
    2. Count ticker mentions (Cashtags `$NVDA`).
    3. Calculate "Hype Velocity" (Mentions Today / Mentions Yesterday).
    4. **Agent Use:** Veto Agent rejects shorts if Hype Velocity > 300%.

### C. Market Microstructure (Short Interest)
*   **Why:** Official bi-weekly short interest is laggy. We need real-time proxies.
*   **Source:** FINRA Daily Short Volume (Free).
*   **Implementation:**
    1. Download FINRA daily short volume files.
    2. Calculate `Short Volume Ratio` (Short Vol / Total Vol).
    3. **Agent Use:** Warning if Short Vol Ratio > 60% consistently.

## 3. Data Integrity & Reliability
*   **The YFinance Risk:** The system relies heavily on `yfinance`. This is a scraping library that breaks often.
*   **Mitigation:** 
    *   **Validation Layer:** Cross-reference `yfinance` daily close with `Stooq` (which is also free) to detect data corruption.
    *   **Fallback:** If `yfinance` fails, failover to `Stooq` for OHLCV.

## Roadmap: Proposed Priority
1.  **Phase 1 (Unlock Existing Data):** Write Parser for `sec_ftd`. (Low effort, High value).
2.  **Phase 2 (The "Whale" View):** Implement `Options GEX` pipeline. (Med effort, Massive value).
3.  **Phase 3 (The "Retail" View):** Implement Reddit Scraper. (Low effort, Med value).
