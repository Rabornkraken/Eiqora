# Tech Stack Analysis

This document provides a comprehensive technical breakdown of the Eiqora trading system.

## 1. Core Backend ("The Brain")

The analysis engine is built on a modern Python stack, leveraging agentic AI workflows.

*   **Language:** Python 3.10+
*   **Agent Orchestration:** `langgraph` (State machine based), `langchain`.
*   **LLM Interface:** `langchain-openai` (Connecting to OpenRouter/DeepSeek).
*   **Data Modeling:** `pydantic` (Strict schema validation for agent I/O).
*   **Technical Analysis:** `ta` (Technical Analysis Library), `pandas`, `numpy`.
*   **Asynchronous Processing:** `asyncio` native.

## 2. Data Collection Subsystem

A robust ETL system designed for reliability and stealth.

*   **Scheduling:** `apscheduler` (Manage cron jobs for market hours).
*   **Web Scraping:**
    *   `playwright`: Headless browser automation (Forex Factory, Corporate Actions). Includes stealth plugins.
    *   `beautifulsoup4` / `lxml`: HTML parsing.
    *   `feedparser`: Parsing SEC Atom RSS feeds.
*   **Market Data APIs:** `yfinance` (Primary), `alpha-vantage` (Backup).
*   **NLP & Sentiment:**
    *   `transformers`: Hugging Face library.
    *   `ProsusAI/finbert`: Specific model for financial sentiment scoring.
    *   `sentence-transformers`: Local text embeddings.

## 3. Database & Storage

Hybrid storage strategy using relational data and vector embeddings.

*   **Database Engine:** PostgreSQL 15.
*   **Extensions:**
    *   `pgvector`: For storing and querying high-dimensional vector embeddings (Search/RAG).
    *   `pg_trgm`: For fuzzy text matching (Symbol/Company name matching).
*   **Drivers:**
    *   `asyncpg`: High-performance async driver for the live pipeline.
    *   `psycopg[binary]`: Standard driver for synchronous ETL scripts.
*   **Schema Management:** `alembic` (Database migrations).

## 4. Frontend Interface

A lightweight, modern web dashboard for monitoring signals and performance.

*   **Framework:** React 19.
*   **Build Tool:** Vite.
*   **Visualization:** `recharts` (Financial charting and performance graphs).
*   **State/Data Fetching:** `axios`.
*   **Styling:** (Implied standard CSS/Modules based on file structure).

## 5. Infrastructure & DevOps

*   **Containerization:** Docker & Docker Compose.
*   **Orchestration:** Services defined in `docker-compose.yml` (`postgres`, `scheduler`).
*   **Configuration:** `pydantic-settings` reading from `.env` files.
*   **Version Control:** Git.

## 6. Key Libraries & Tools Summary

| Category | Tool | Usage |
| :--- | :--- | :--- |
| **LLM Orchestration** | `langgraph` | defining the DAG of 10+ agents |
| **Vector Search** | `pgvector` | RAG for news and SEC filings |
| **Browser Automation** | `playwright` | Scraping protected sites (Forex Factory) |
| **Data Validation** | `pydantic` | Ensuring JSON outputs from LLMs are valid |
| **Financial Data** | `yfinance` | Historical bars, news, earnings |
| **Sentiment** | `FinBERT` | Local sentiment classification |
