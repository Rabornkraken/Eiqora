# Database Schema Documentation

Eiqora uses a PostgreSQL 15 database with `pgvector` and `pg_trgm` extensions. The schema is normalized to separate master data, time-series market data, and unstructured text/document data.

## 1. Master Data

### `issuer`
Companies/Entities that issue securities.
*   `cik` (PK): Central Index Key (10-digit).
*   `legal_name`: Official name.
*   `sic`: Standard Industrial Classification.
*   `state`: State of incorporation.

### `security`
Tradable assets linked to issuers.
*   `security_id` (PK): Internal ID.
*   `ticker`: Symbol (e.g., AAPL).
*   `cik`: FK to `issuer`.
*   `exchange`: Listing exchange.
*   `is_primary`: Boolean, main issue flag.

### `universe_snapshot`
Daily snapshot of the active tradeable universe (e.g., S&P 500).
*   `asof_date` (PK): Date of snapshot.
*   `symbol` (PK): Ticker symbol.
*   `weight`: Index weight (if applicable).

---

## 2. Market Data (Time-Series)

### `market_bar_daily`
Daily OHLCV data + Technical Indicators.
*   `symbol` (PK): Ticker.
*   `date` (PK): Trading date.
*   `open`, `high`, `low`, `close`: Adjusted prices.
*   `volume`: Trading volume.
*   `vwap`: Volume-Weighted Average Price.
*   **Indicators:**
    *   `rsi_14`: Relative Strength Index.
    *   `macd`, `macd_signal`, `macd_hist`: MACD components.
    *   `bb_upper_20`, `bb_lower_20`: Bollinger Bands.
    *   `atr_14`: Average True Range.
    *   `mfi_14`: Money Flow Index.
    *   `cmf_20`: Chaikin Money Flow.
    *   `obv`: On-Balance Volume.

### `market_bar_hourly`
Intraday hourly bars.
*   `symbol` (PK): Ticker.
*   `datetime` (PK): Timestamp (UTC).
*   `open`, `high`, `low`, `close`, `volume`.
*   **Indicators:** `rsi_14`, `macd_histogram`, `vwap`, `volume_z_20h` (Volume Z-Score).

### `options_daily_summary`
Aggregated options flow and volatility.
*   `symbol` (PK), `date` (PK).
*   `put_call_ratio_volume`: P/C Ratio by volume.
*   `put_call_ratio_oi`: P/C Ratio by Open Interest.
*   `atm_iv`: At-the-money Implied Volatility.
*   `max_pain_strike`: Strike with max pain.

### `stock_correlations`
Rolling correlation matrices.
*   `symbol_a`, `symbol_b`, `period_days`.
*   `correlation_coef`: Pearson coefficient (-1 to 1).

---

## 3. Fundamental & Alternative Data

### `earnings_event`
Earnings calendar and results.
*   `symbol` (PK), `earnings_date` (PK).
*   `eps_est`, `eps_actual`: EPS consensus vs actual.
*   `revenue_est`, `revenue_actual`: Revenue.
*   `time_of_day`: Pre-market / After-hours.

### `sec_filing`
SEC EDGAR filings (8-K, 10-Q, 10-K).
*   `accession` (PK): Unique SEC identifier.
*   `cik`: Filer CIK.
*   `form_type`: e.g., "8-K".
*   `filed_at`: Filing date.
*   `primary_doc_url`: Link to full text.

### `analyst_rating`
Sell-side upgrades/downgrades.
*   `symbol`, `rating_date`, `firm`.
*   `action`: UP, DOWN, MAIN, INIT.
*   `price_target`: New price target.

### `yfinance_news`
Aggregated financial news.
*   `doc_id` (PK): Internal ID.
*   `news_id`: Source ID.
*   `ticker`: Related symbol.
*   `title`, `text`: Full content.
*   `published_at`: Timestamp.
*   `sentiment_score`: (via `yfinance_news_relevance` join) FinBERT score.

---

## 4. Relationships & Knowledge Graph

### `stock_relationships`
Supply chain and competitor mapping.
*   `symbol_from` -> `symbol_to`.
*   `relationship_type`: SUPPLIER, CUSTOMER, COMPETITOR.
*   `strength`: Confidence/importance score.

### `influential_statements`
Tracked statements from key figures.
*   `figure_id`: FK to `influential_figures` (Fed Chair, CEOs).
*   `statement_text`: Quote.
*   `sentiment`: HAWKISH, DOVISH, BULLISH, BEARISH.
*   `mentioned_symbols`: Array of tickers.

---

## 5. System & Backtesting

### `trigger_analysis_cache`
Caches LLM decisions to prevent redundant costs.
*   `symbol`, `trigger_type`.
*   `decision`: BUY, PASS, SELL.
*   `context`: JSON snapshot of indicators at decision time.
*   `expires_at`: Validity window.

### `trigger_backtest_run`
Results of trigger-based backtests.
*   `run_id` (PK).
*   `win_rate`, `total_pnl_pct`: Aggregate metrics.
*   `best_trigger_type`, `worst_trigger_type`.
*   `trigger_details`: JSONB breakdown by trigger type.

### `profile_performance`
Tracks accuracy of LLM-generated profiles.
*   `symbol`, `profile_generated_date`.
*   `profile_score`: The score given (0-1).
*   `forward_1w_return`, `forward_1m_return`: Actual market outcome.
