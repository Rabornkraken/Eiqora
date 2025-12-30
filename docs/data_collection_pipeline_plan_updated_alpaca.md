# Updated Data Collection Pipeline Plan (Alpaca OHLCV + Critical Omissions Addressed)

**Scope:**  
- Insider / edge: **SEC EDGAR (Form 4 + 10-Q/K + 13F)** via **EdgarTools**, **Quiver** congress trading, **USASpending** contracts, **OpenInsider** (supplemental).  
- News: **GDELT 2.0 Doc API** + **IR RSS**.  
- Macro (“high impact”): **time series + release/event documents**, registry-driven (FRED + selected agency/central-bank/treasury release feeds/pages).  
- Earnings calendar: **FMP**.  
- **Market data OHLCV:** **Alpaca Market Data** (daily + optional intraday).  
- Exclude: social analyst ingestion (you already have it).

---

## 0) Why these updates are necessary

This revision closes the “must-have” gaps for a multi-agent analysis system:

1. **Market reaction** requires **OHLCV** (daily and/or intraday).  
2. **Historical correctness** requires **symbology history** (ticker changes, merges, rebrands) + **corporate actions** (splits/dividends) for adjusted returns.  
3. **Government contract linkage** requires **subsidiary hierarchy** (Exhibit 21 extraction + manual overrides).  
4. **Retrieval quality** requires **hybrid search** (FTS + vector) and **de-noising** before embedding.  
5. **SEC amendments** must supersede old chunks so agents don’t cite stale numbers.  
6. Adds two high-value edge datasets: **SEC fails-to-deliver** and **Senate lobbying downloads**.

---

## 1) Storage & execution architecture

### 1.1 Raw data lake (immutable)
**S3/MinIO**: store payloads as received (JSON/XML/HTML/PDF/ZIP/text).  
- `raw/sec/{form}/{cik}/{yyyy-mm-dd}/{accession}/...`
- `raw/alpaca/bars/{timeframe}/{symbol}/{yyyy-mm-dd}/{sha256}.json`
- `raw/rss/{feed_id}/{yyyy-mm-dd}/{sha256}.xml`
- `raw/gdelt/{query_id}/{yyyy-mm-dd-hh}/{sha256}.json`
- `raw/ftd/{yyyymm}/{sha256}.txt` (or zip)
- `raw/lobbying/{year}/Q{qtr}/{sha256}.zip|xml`
- `raw/fred/{series_id}/{yyyy-mm-dd}/{sha256}.json`
- `raw/fmp/{endpoint}/{yyyy-mm-dd}/{sha256}.json`
- `raw/usaspending/{query_id}/{yyyy-mm-dd}/{sha256}.json`

### 1.2 Primary database
**Postgres** as the main structured store:
- Joins across sources (CIK/ticker/time)
- JSONB for semi-structured metadata
- Postgres Full Text Search (FTS) + **pgvector** for embeddings
- Partition time-series tables by month (or TimescaleDB hypertables if you prefer)

### 1.3 Optional caching
**Redis**
- HTTP conditional caching (ETag/Last-Modified)
- job-level dedup/rate limiter tokens
- hot query caching for agent APIs

### 1.4 Orchestration
**Prefect** (recommended for fast Python-first execution)
- one flow per source family
- standardized tasks: `discover → fetch_raw → normalize → index_docs`

---

## 2) “Golden IDs” and core master data

### 2.1 Canonical identity
- Prefer **CIK** as issuer key.
- Maintain robust ticker mapping with validity windows.

### 2.2 Required tables (master)
**`issuer`**
- `cik (PK)`, `legal_name`, `sic`, `state`, `updated_at`

**`security`**
- `security_id (PK)`
- `ticker`, `exchange`, `cik (FK)`, `is_primary`
- `valid_from`, `valid_to`

**`ticker_history`** (NEW)
- `ticker_history_id (PK)`
- `cik`, `ticker`, `start_date`, `end_date`
- `company_name`, `reason` (rename/merge/spinoff), `source`, `source_ref`
- Index: `(cik, start_date)`, `(ticker, start_date)`

**`corporate_action`** (NEW)
- `action_id (PK)`
- `cik`, `ticker`, `action_type` (split/dividend/spinoff/merge)
- `ex_date`, `pay_date`, `ratio`, `cash_amount`, `currency`, `source`, `source_ref`

> Agents should be able to ask questions like: “What was the **adjusted** return after event X?”  
> That requires corporate action data plus a consistent symbology history.

---

## 3) Ingestion bookkeeping and provenance

**`ingest_cursor`**
- `source`, `cursor_key`, `cursor_value`, `updated_at`

**`raw_object`**
- `raw_id (PK)`, `source`, `object_key`, `content_type`
- `retrieved_at`, `sha256`, `http_status`, `meta (JSONB)`

---

## 4) LLM-ready document + retrieval layer (hybrid)

### 4.1 Documents
**`document`**
- `doc_id (PK)`
- `source` (sec, ir_rss, gdelt, ftd, lobbying, treasury, bea, etc.)
- `source_id` (accession/guid/url_hash/etc.)
- `doc_type` (sec_filing, press_release, news_article, macro_release, dataset_release, ...)
- `cik (nullable)`, `ticker (nullable)`
- `title`, `published_at`, `url`, `raw_id`
- `text_object_key` (S3) OR `text` (small)

### 4.2 Vector chunks
**`document_chunk`**
- `chunk_id (PK)`, `doc_id (FK)`, `chunk_index`
- `text`, `token_count`
- `embedding (vector)`
- `active (bool, default true)` **(NEW, for amendments/supersession)**
- Index: pgvector HNSW/IVFFLAT on `embedding`

### 4.3 Keyword search (FTS) (NEW)
**`document_fts`**
- `doc_id (PK/FK)`, `tsv (tsvector)`
- GIN index on `tsv`

### 4.4 Retrieval API contract for agents (NEW)
Provide a single endpoint that supports:
- `mode="exact"` → FTS rank only
- `mode="concept"` → vector similarity only
- `mode="hybrid"` → weighted combination, with `active=true` enforced by default

---

## 5) Market data OHLCV (NEW): Alpaca Market Data

### 5.1 What to ingest
**Phase 1 (recommended): daily bars**
- timeframes: `1Day`
- use cases: event studies, overnight gap reactions, trend & volatility features

**Phase 2 (optional): intraday bars**
- timeframes: `1Min` / `5Min` / `15Min`
- use cases: “reaction within 1–3 hours”, post-earnings drift in-session, spike detection

### 5.2 Tables
**`market_bar_daily`**
- `symbol`, `date` (UTC date), `open`, `high`, `low`, `close`, `volume`, `vwap`, `trade_count`
- `source='alpaca'`, `asof_ts`, `raw_id`
- PK: `(symbol, date)`

**`market_bar_intraday`**
- `symbol`, `ts` (timestamp), OHLCV + vwap + trade_count
- PK: `(symbol, ts)`
- Partition by month (or Timescale hypertable)

### 5.3 Adjustment strategy (important)
Alpaca bars may be unadjusted depending on endpoint/options and vendor behavior.  
To ensure correct returns:
- Maintain `corporate_action`
- Provide an `adjusted_close` view/materialization if you need adjusted analytics:
  - daily adjustment factors built from splits/dividends where available
- In agent-facing queries, default to **adjusted** returns for multi-year comparisons.

### 5.4 Ingestion patterns
- Batch symbols where possible (API returns per symbol; plan around pagination/limits).
- Incremental:
  - daily bars: each trading day + trailing 5–10 day rescan
  - intraday: store finalized bars; rescan last 1–2 days for corrections

### 5.5 Agent questions enabled
- “Did the stock drop after the insider sell was filed?”
- “How abnormal was volume within 2 hours of the press release?”
- “Was the move market-wide or idiosyncratic (beta-adjusted)?” (later add index bars)

---

## 6) Corporate actions & symbology history population (NEW)

### 6.1 Ticker/symbology history
Use a dataset/provider to populate `ticker_history`:
- Option A: Use **FMP** if it provides reliable symbol change endpoints
- Option B: Use another symbology source (Polygon, FIGI mapping, etc.)

**Pipeline behavior change (required):**
- When backfilling news (GDELT) or filings for historical windows, generate query keywords from:
  - current ticker + historical tickers in window
  - issuer legal name + common aliases
- Example: querying META 2021 must include **FB**.

### 6.2 Corporate actions
Populate `corporate_action`:
- splits/dividends from provider(s) (often easiest via a fundamentals vendor)
- additionally parse SEC filings for corporate action notes if needed (later)

---

## 7) SEC EDGAR (EdgarTools): 10-K/Q, 4, 13F + amendments handling

### 7.1 Filing discovery & raw download
**`sec_filing`**
- `accession (PK)`, `cik`, `form_type`, `filed_at`, `report_period`
- `is_amendment (bool)` (NEW)
- `amends_accession (nullable)` (NEW, link /A to original)
- `primary_doc_url`, `raw_id`, `ingested_at`

### 7.2 Parsing
- Form 4 → `insider_transaction`
- 10-K/Q → `sec_filing_section` + `document` + embeddings
- 13F → `sec_13f_holding` + `document` + embeddings

### 7.3 Amendment supersession (NEW)
When a `/A` filing arrives:
1. Insert the amended filing normally.
2. Mark the original filing’s `document_chunk.active=false`.
3. Retrieval defaults to `active=true` to prevent stale citations.

---

## 8) Subsidiary mapping (NEW): Exhibit 21 extraction + overrides

### 8.1 Tables
**`subsidiary_map`**
- `subsidiary_id (PK)`
- `subsidiary_name_normalized`
- `parent_cik`
- `source_accession`, `source_exhibit` (Exhibit 21), `asof_date`
- `confidence` (0–1), `notes`
- Index: trigram on `subsidiary_name_normalized`

**`entity_alias`** (recommended)
- `alias_id`, `alias_name`, `cik`, `alias_type`, `source`, `confidence`

### 8.2 Pipeline
1. During 10-K processing, locate and extract Exhibit 21.
2. Normalize subsidiary names (strip punctuation/legal suffixes; uppercase; collapse spaces).
3. Upsert into `subsidiary_map`.
4. Provide manual overrides for key issuers.

---

## 9) USASpending contracts (improved linkage)
- Linkage workflow:
  1. Match via `subsidiary_map` / `entity_alias`
  2. Fall back to fuzzy match (pg_trgm)
  3. Record: `match_method`, `match_score`, `review_status`
- Store:
  - `gov_award`
  - `gov_award_issuer_link(award_id, cik, confidence, method, reviewed_at)`

---

## 10) Financial news ingestion: GDELT 2.0 + IR RSS (noise control)

### 10.1 GDELT noise filtering (NEW)
Avoid embedding the firehose.

**Two-stage gating**
1. **Pre-filter** at query time (tight entity queries, language, time window).
2. **Relevance scoring** before embedding:
   - entity match strength (ticker+name in title/body)
   - finance keyword density
   - source reputation score
   - dedup by canonical URL / near-duplicate title hash
   - only embed if `relevance_score >= threshold`

Store:
- `news_relevance(doc_id, score, features_json, model_version)`

### 10.2 IR RSS
- Conditional GET, dedup on GUID/link hash.
- Embed only substantive releases (length threshold / relevance gate).

---

## 11) SEC fails-to-deliver (NEW edge dataset)

**`sec_ftd`**
- `settlement_date`, `cusip`, `ticker`, `issuer_name`, `price`, `quantity`
- PK: `(settlement_date, cusip)`
- Maintain `cusip→cik` mapping where possible.

---

## 12) Senate lobbying downloads (NEW edge dataset)

**`lobbying_filing`**
- `filing_id (PK)`, `filing_type`, `received_date`, `registrant`, `client`, `raw_id`, `source_url`

**`lobbying_issue`**
- `filing_id (FK)`, `issue_code`, `issue_text`, `bill_numbers`

Entity linkage:
- match `registrant/client` against `entity_alias` (with confidence + manual review)

---

## 13) Build order (phased)

### Phase 1 — Retrieval-ready core + OHLCV
1. Infra: Postgres (+ pgvector + FTS), MinIO, Redis
2. Tables: issuer/security + ticker_history + corporate_action + raw_object + document + chunk + FTS
3. IR RSS → document → relevance gate → chunk+embed
4. Alpaca daily OHLCV ingestion → `market_bar_daily`
5. Agent API: retrieval (exact/concept/hybrid) + event/price joins

### Phase 2 — SEC backbone + amendments
1. EdgarTools filings discovery + fetch
2. Parse 10-Q/K sections + embed; parse Form 4; parse 13F
3. Amendment supersession logic (`active=false` for old chunks)

### Phase 3 — Government edge correctness
1. Exhibit 21 extraction → `subsidiary_map`
2. USASpending linkage improvements + alias workflow
3. Quiver congress trading

### Phase 4 — Edge expansions + hardening
1. SEC FTD ingestion
2. Senate lobbying downloads
3. GDELT classifier improvements + dedup + quality controls
4. Partitioning/tuning/observability dashboards

---

## 14) References (starting points)
- Alpaca historical stock bars endpoint: `GET https://data.alpaca.markets/v2/stocks/bars`
- SEC fails-to-deliver dataset page
- Senate public disclosure bulk downloads (lobbying databases)
