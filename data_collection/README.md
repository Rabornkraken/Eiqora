# Data collection

Purpose-built home for ingestion pipelines and configs.

## Layout
- pipelines/: source-specific ingestion flows (market data, SEC, GDELT, etc.)
- config/: environment, credentials, and runtime settings
- raw/: local raw data lake (when `RAW_STORAGE=local`)
- config.yaml: project-wide pipeline configuration

## Running pipelines
Use the CLI to run one or more pipelines:

```
set -a
source data_collection/config/.env
set +a
python -m pipelines.universe run --date YYYY-MM-DD
python -m pipelines.stooq_daily backfill --start YYYY-MM-DD --end YYYY-MM-DD
python -m pipelines.fred run
python -m pipelines.earnings run
python -m pipelines.yfinance_news run
```

## Database
Local Postgres can be started with:

```
docker compose -f data_collection/docker-compose.yml up -d
```

Schema is initialized from `data_collection/db/init/001_schema.sql`.

Pipelines insert raw payload metadata into `raw_object` using the database
connection defined by `DATABASE_URL` (or `POSTGRES_*`).

## Shared environment variables
- `RAW_STORAGE` = `local` or `s3`
- `RAW_STORAGE_PATH` (local path; default `data_collection/raw`)
- `RAW_S3_BUCKET`, `RAW_S3_PREFIX`, `RAW_S3_ENDPOINT_URL` (S3/MinIO)
- `DATA_COLLECTION_USER_AGENT` (HTTP user agent)
- `HTTP_TIMEOUT_SECONDS`, `HTTP_MAX_RETRIES`, `HTTP_BACKOFF_SECONDS`
- `DATABASE_URL` (optional override)
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`
- `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`
- Embeddings use a local SentenceTransformer model configured in `data_collection/config.yaml`.

## Symbol universe override
If `universe.symbols_file` is set in `data_collection/config.yaml`, its symbols are used
across pipelines (universe, news, market data, earnings, etc.). Leave it empty or remove
the key to revert to SPY holdings as the source of truth.

## Per-source environment variables
- `sec_edgar`: `SEC_USER_AGENT`, `SEC_CIKS`, `SEC_FORMS`, `SEC_BATCH_SIZE`, `SEC_BATCH_OFFSET` (if `SEC_CIKS` is empty, uses symbols file + SEC ticker map). Parses 10-K/10-Q/8-K into `sec_filing_section` (full text), Form 4 into `insider_transaction`, and 13F into `sec_13f_holding`.
- `gdelt`: `GDELT_QUERY` or `GDELT_QUERIES`, `GDELT_START_DATETIME`, `GDELT_END_DATETIME`, `GDELT_MAX_RECORDS`, `GDELT_BATCH_SIZE`, `GDELT_BATCH_OFFSET`, `GDELT_LOOKBACK_HOURS`, `GDELT_ARTICLE_CONCURRENCY`, `GDELT_ARTICLE_TIMEOUT_SECONDS`, `GDELT_ARTICLE_MAX_RETRIES`, `GDELT_ARTICLE_BACKOFF_SECONDS`, `GDELT_MAX_FETCH_MULTIPLIER`, `GDELT_PREFILTER_TITLES`, `GDELT_DOMAIN_DENYLIST`, `GDELT_BROWSER_PRIMARY`, `GDELT_BROWSER_FALLBACK`, `GDELT_BROWSER_TIMEOUT_SECONDS`, `GDELT_BROWSER_PROXY`, `GDELT_CDP_URL`
- `yfinance_news`: `YFINANCE_NEWS_CONCURRENCY`, `YFINANCE_ARTICLE_TIMEOUT_SECONDS`, `YFINANCE_ARTICLE_MAX_RETRIES`, `YFINANCE_ARTICLE_BACKOFF_SECONDS`
- `stooq_daily`: `STOOQ_CONCURRENCY`, `STOOQ_BATCH_SIZE`, `STOOQ_BATCH_OFFSET`
- `earnings`: `NASDAQ_EARNINGS_CONCURRENCY`
- `fred`: `FRED_CONCURRENCY`
- `fred_macro`: `FRED_API_KEY`, `FRED_SERIES_IDS`, `FRED_START`, `FRED_END`
- `usaspending`: `USASPENDING_ENDPOINT`, `USASPENDING_METHOD`, `USASPENDING_PARAMS_JSON`, `USASPENDING_PAYLOAD_JSON`
- `sec_ftd`: `SEC_USER_AGENT`, `SEC_FTD_MONTHS`

## Virtual environment
This repo uses the existing `.venv` at the project root. Use it when running
anything in this folder:

```
source .venv/bin/activate
python -m pip --version
```
