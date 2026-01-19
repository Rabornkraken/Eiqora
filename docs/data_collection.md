# Data Collection System

The Eiqora Data Collection system is a robust ETL (Extract, Transform, Load) framework designed to aggregate financial, regulatory, and sentiment data. It is orchestrated by a centralized scheduler and designed for data lineage and reproducibility.

## Orchestration
- **Scheduler**: `data_collection/scheduler.py` uses `APScheduler` (BlockingScheduler).
- **Timezone**: `America/New_York` (Market Time).
- **Execution**: Pipelines are run as subprocesses to ensure isolation. A startup sequence (`run_startup_pipelines`) ensures the database is fresh when the system begins.

## Data Pipelines

### 1. Market Data (Price & Volume)
| Data Type | Source | Frequency | Implementation Details |
|-----------|--------|-----------|------------------------|
| **Daily Bars** | Stooq.com | Daily (18:30 ET) | Fetches CSVs via `stooq_daily.py`. Supports backfilling and handles symbol mapping (e.g., adding `.us` suffix). |
| **Hourly Bars** | YFinance | Hourly (Market hours) | Fetches OHLCV via `hourly_bars.py`. Requests data in 60-day chunks to bypass API limits. |
| **Volatility** | YFinance | Daily (18:15 ET) | Tracks VIX and other volatility indices. |
| **Options** | YFinance/Internal | Daily (16:45 ET) | Summarizes options flow and volatility surface data. |

### 2. Regulatory & Fundamentals (SEC EDGAR)
The SEC pipeline (`pipelines/sec_edgar/`) is a comprehensive ingestion engine for regulatory filings.
- **Tracking**: Polls SEC RSS feeds every 15 minutes for real-time updates.
- **Filing Types**: 10-K, 10-Q, 8-K (Full text), 4 (Insiders), 13F (Institutional).
- **Extraction**:
  - **Form 4**: XML parsing to extract `insider_transaction` records (Owner, Relationship, Shares, Price).
  - **Form 13F**: XML parsing to extract `sec_13f_holding` records (CUSIP, Shares, Value).
  - **Sections**: HTML cleaning and extraction of full text for 10-Ks/Qs into `sec_filing_section`.

### 3. News & Sentiment
- **YFinance News**: Metadata is fetched via `yfinance`.
- **CDP Scraping**: For full article content, the system uses a **Chrome DevTools Protocol (CDP) browser** (`cdp_sync.py`). This allows it to bypass basic anti-bot measures and render JavaScript-heavy news sites.
- **Sentiment Scoring**: Uses **FinBERT** (ProsusAI/finbert) via the HuggingFace `transformers` library. Articles are chunked, scored, and results are stored in `yfinance_news_relevance`.

### 4. Fundamentals & Macro
- **Earnings**: Scrapes NASDAQ earnings calendars daily at 06:00 and 16:30 ET.
- **Analyst Ratings**: Daily ingestion of consensus ratings and price targets.
- **Macro Data**: Integrated with **FRED** (Federal Reserve Economic Data) for economic indicators.
- **Corporate Actions**: Weekly crawl of dividends, splits, and name changes.

## Infrastructure & Storage

### Database (PostgreSQL)
- **Primary Store**: Structured tables for prices, filings, news metadata, and sentiment scores.
- **Notifications**: Pipelines use `NOTIFY`/`LISTEN` on the `eiqora_ingest` channel to trigger downstream processing (like technical indicator calculation) immediately after data lands.

### Raw Object Storage
- **Location**: `data_collection/raw/` (Local) or S3/MinIO.
- **Purpose**: Every raw HTTP response (JSON, CSV, HTML, XML) is hashed (SHA-256) and stored.
- **Lineage**: The `raw_object` table links every record in the database back to its original raw source file, allowing for auditability and re-parsing.

### Configuration
- **Global Config**: `data_collection/config.yaml` manages symbol universes, HTTP timeouts, and storage backends.
- **Environment**: Sensitive credentials and batch overrides are managed via `.env` files in `data_collection/config/`.
