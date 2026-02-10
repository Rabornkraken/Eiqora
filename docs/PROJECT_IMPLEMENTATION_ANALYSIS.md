# Eiqora Project Implementation Analysis

**Date:** January 22, 2026
**Scope:** Backend, Data Collection, and System Architecture (excluding Frontend)

## 1. Executive Summary

Eiqora is a sophisticated, autonomous financial analysis and algorithmic trading system. It leverages a multi-agent architecture powered by Large Language Models (LLMs) to perform deep fundamental and technical analysis on stock market data. The system is designed to mimic a human hedge fund team, with specialized agents (Analysts, Risk Managers, Traders) collaborating to make investment decisions.

The project is structured into three primary domains:
1.  **Data Collection:** A robust, scheduled ETL (Extract, Transform, Load) system that aggregates data from various sources (SEC, YFinance, Stooq, etc.) into a central PostgreSQL database.
2.  **Eiqora V2 Core:** The "brain" of the operation, featuring a `langgraph`-based orchestrator that manages the flow of information between specialized AI agents to generate trading signals.
3.  **API Layer:** A FastAPI-based interface that serves data and analysis results to the frontend and other consumers.

## 2. System Architecture

The system follows a modular microservices-like architecture, though currently deployed as a monolith with distinct logical boundaries.

```mermaid
graph TD
    subgraph "Data Layer"
        PostgreSQL[(PostgreSQL DB)]
        Scheduler[APScheduler]
    end

    subgraph "Data Pipelines"
        MarketData[Market Data Pipelines]
        NewsData[News & Sentiment Pipelines]
        AltData[Alternative Data (SEC, etc.)]
    end

    subgraph "Eiqora V2 Core (Analysis)"
        Orchestrator[Multi-Agent Orchestrator]
        State[SwingTradeState]
        Agents[Agent Swarm (Chart, Fundamental, Decision...)]
    end

    subgraph "Interface"
        API[FastAPI Server]
        CLI[Command Line Interface]
    end

    Scheduler -->|Triggers| MarketData
    Scheduler -->|Triggers| NewsData
    Scheduler -->|Triggers| AltData
    MarketData -->|Writes| PostgreSQL
    NewsData -->|Writes| PostgreSQL
    AltData -->|Writes| PostgreSQL

    API -->|Reads/Writes| PostgreSQL
    API -->|Invokes| Orchestrator
    Orchestrator -->|Reads| PostgreSQL
    Orchestrator -->|Coordinates| Agents
    Agents -->|Updates| State
```

## 3. Data Collection Subsystem

The data collection engine is designed for reliability and autonomy, ensuring the analysis engine always has fresh data.

### 3.1 Orchestration
The heart of the data collection is `data_collection/scheduler.py`. It utilizes `APScheduler` to manage a complex timetable of jobs.
-   **Execution Model:** Pipelines are executed as independent subprocesses. This ensures that a failure (e.g., segfault or memory leak) in one pipeline does not crash the main scheduler.
-   **Logging:** Real-time stdout/stderr streaming is captured and logged.

### 3.2 Pipeline Strategy
Pipelines are Python modules located in `data_collection/pipelines/`. They act as independent ETL scripts.

**Key Pipeline Categories:**
*   **High Priority (Core):**
    *   `stooq_daily.py`: Daily OHLCV bars.
    *   `hourly_bars_auto.py`: Intraday price action (critical for timing).
    *   `sec_rss.py`: Monitors SEC feeds for new filings (10-K, 8-K) every 15 minutes.
    *   `earnings.py`: Updates earnings calendars morning and evening.
*   **Medium Priority (Context):**
    *   `yfinance_news.py`: Aggregates news articles and performs initial sentiment scoring.
    *   `options_summary.py`: Captures options volatility and open interest.
    *   `analyst_ratings.py`: Tracks upgrades/downgrades.
*   **Low Priority (Reference):**
    *   `universe.py`, `sec_ticker_map.py`: Maintains the master list of tradable assets and identifier mappings.
    *   `xbrl_revenue.py`: Deep dives into financial statements.

### 3.3 Data Storage
Data is persisted in a PostgreSQL database. The schema is managed via `alembic` migrations (`data_collection/db/migrations`).
-   **Connection:** Handled by `data_collection/db/connection.py` using `psycopg`.
-   **Schema Design:** Likely normalized to separate static reference data (Tickers) from time-series data (Prices, Financials) and event data (News, SEC filings).

## 4. Eiqora V2 Core ("The Brain")

The core logic resides in the `eiqora_v2` package. It implements a sophisticated agentic workflow where distinct "personas" contribute to a shared state.

### 4.1 The Orchestrator
Located in `eiqora_v2/orchestrator.py`, the `Orchestrator` class manages the lifecycle of a trade analysis. It defines a directed acyclic graph (DAG) of agents.

**The Agent Chain:**
1.  **TopDownAgent:** Analyzes macro conditions (SPY, VIX, Sector trends).
2.  **ContextAgent:** Gathers technical context for the specific ticker (Relative Strength, Volatility).
3.  **ChartAgent:** Classifies technical setups (e.g., "Bull Flag", "Breakout") using pattern recognition logic.
4.  **FundamentalAgent:** Digests news, earnings, and SEC filings to assess fundamental health and catalysts.
5.  **IdeaGeneratorAgent:** Synthesizes inputs from previous agents to propose trade ideas.
6.  **ExitPolicyAgent:** Defines risk parameters (Stop Loss, Take Profit) based on volatility (RV20).
7.  **RedTeamAgent:** Acts as a "Devil's Advocate," specifically looking for reasons *not* to take the trade.
8.  **ShortPerspectiveAgent:** Evaluates the stock from a short-seller's perspective to counter-balance confirmation bias.
9.  **DecisionAgent:** The final gatekeeper. It weighs the conviction, setup quality, and Red Team risks to output a `GO`, `NO_GO`, or `REVIEW` decision.
10. **VetoAgent:** A final sanity check layer.
11. **NarrativeAgent:** Generates a human-readable explanation of the trade.

### 4.2 Agent Implementation
Agents (in `eiqora_v2/agents/`) inherit from a `BaseAgent`.
-   **Prompt Engineering:** Each agent has a specialized system prompt (e.g., `_get_system_prompt` in `decision.py`) that enforces a strict role.
-   **Structured Output:** Agents are required to return valid JSON that conforms to Pydantic models (e.g., `DecisionOutput`), ensuring programmatic reliability.
-   **State Management:** The system passes a `SwingTradeState` object between agents. Each agent reads relevant keys (e.g., `decision` agent reads `red_team` output) and writes to its own section.

### 4.3 Decision Logic
The `DecisionAgent` (`eiqora_v2/agents/decision.py`) implements explicit "gates":
-   **Conviction Gate:** Requires HIGH or MEDIUM conviction.
-   **Setup Quality Gate:** Requires a setup score >= 0.65.
-   **Risk Gate:** If the Red Team flags a "Critical" risk, the trade is blocked regardless of the setup.

## 5. API & Interface

The system interacts with the outside world via `api.py`.
-   **Framework:** FastAPI.
-   **Endpoints:**
    *   `/chat`: Natural language interface for querying the system.
    *   `/analysis/start`: Triggers an asynchronous run of the Orchestrator.
    *   `/api/dashboard-stats`, `/api/equity-history`: Serves live trading performance metrics.
-   **Integration:** The API directly imports `eiqora_v2` tools to fetch positions and run analyses, bridging the gap between the web frontend and the python backend.

## 6. Infrastructure & Tooling

-   **Environment:** Configuration is managed via `.env` files and `pydantic-settings`.
-   **Package Management:** `pyproject.toml` defines the project dependencies, including `langchain`, `langgraph`, `yfinance`, and `torch` (implied by `sentence-transformers`).
-   **Docker:** The `data_collection` folder contains a `Dockerfile`, suggesting the data collector can be deployed as an independent container service.
