-- Core schema for Eiqora data collection

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Master data
CREATE TABLE IF NOT EXISTS issuer (
    cik VARCHAR(10) PRIMARY KEY,
    legal_name TEXT,
    sic TEXT,
    state TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS security (
    security_id BIGSERIAL PRIMARY KEY,
    ticker TEXT,
    exchange TEXT,
    cik VARCHAR(10) REFERENCES issuer(cik),
    is_primary BOOLEAN DEFAULT FALSE,
    valid_from DATE,
    valid_to DATE
);

CREATE TABLE IF NOT EXISTS ticker_history (
    ticker_history_id BIGSERIAL PRIMARY KEY,
    cik VARCHAR(10),
    ticker TEXT,
    start_date DATE,
    end_date DATE,
    company_name TEXT,
    reason TEXT,
    source TEXT,
    source_ref TEXT
);

CREATE INDEX IF NOT EXISTS idx_ticker_history_cik_start ON ticker_history (cik, start_date);
CREATE INDEX IF NOT EXISTS idx_ticker_history_ticker_start ON ticker_history (ticker, start_date);

CREATE TABLE IF NOT EXISTS corporate_action (
    action_id BIGSERIAL PRIMARY KEY,
    cik VARCHAR(10),
    ticker TEXT,
    action_type TEXT,
    ex_date DATE,
    pay_date DATE,
    ratio NUMERIC,
    cash_amount NUMERIC,
    currency TEXT,
    source TEXT,
    source_ref TEXT
);

-- Ingestion bookkeeping
CREATE TABLE IF NOT EXISTS ingest_cursor (
    source TEXT NOT NULL,
    cursor_key TEXT NOT NULL,
    cursor_value TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (source, cursor_key)
);

CREATE TABLE IF NOT EXISTS raw_object (
    raw_id BIGSERIAL PRIMARY KEY,
    source TEXT,
    object_key TEXT,
    content_type TEXT,
    retrieved_at TIMESTAMPTZ DEFAULT NOW(),
    sha256 TEXT,
    http_status INT,
    meta JSONB
);

-- Documents and retrieval
CREATE TABLE IF NOT EXISTS document (
    doc_id BIGSERIAL PRIMARY KEY,
    source TEXT,
    source_id TEXT,
    doc_type TEXT,
    cik VARCHAR(10),
    ticker TEXT,
    title TEXT,
    published_at TIMESTAMPTZ,
    url TEXT,
    raw_id BIGINT REFERENCES raw_object(raw_id),
    text_object_key TEXT,
    text TEXT
);

CREATE TABLE IF NOT EXISTS document_chunk (
    chunk_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT REFERENCES document(doc_id),
    chunk_index INT,
    text TEXT,
    token_count INT,
    embedding VECTOR(1536),
    active BOOLEAN DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_document_chunk_doc ON document_chunk (doc_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_document_chunk_embedding ON document_chunk USING ivfflat (embedding vector_l2_ops);

CREATE TABLE IF NOT EXISTS document_fts (
    doc_id BIGINT PRIMARY KEY REFERENCES document(doc_id),
    tsv TSVECTOR
);

CREATE INDEX IF NOT EXISTS idx_document_fts_tsv ON document_fts USING GIN (tsv);

CREATE TABLE IF NOT EXISTS news_relevance (
    doc_id BIGINT PRIMARY KEY REFERENCES document(doc_id),
    score NUMERIC,
    features_json JSONB,
    model_version TEXT
);

-- Market data
CREATE TABLE IF NOT EXISTS market_bar_daily (
    symbol TEXT NOT NULL,
    date DATE NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume BIGINT,
    vwap NUMERIC,
    trade_count BIGINT,
    source TEXT,
    asof_ts TIMESTAMPTZ,
    raw_id BIGINT REFERENCES raw_object(raw_id),
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS market_bar_intraday (
    symbol TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume BIGINT,
    vwap NUMERIC,
    trade_count BIGINT,
    source TEXT,
    raw_id BIGINT REFERENCES raw_object(raw_id),
    PRIMARY KEY (symbol, ts)
);

-- SEC filings
CREATE TABLE IF NOT EXISTS sec_filing (
    accession TEXT PRIMARY KEY,
    cik VARCHAR(10),
    form_type TEXT,
    filed_at DATE,
    report_period DATE,
    is_amendment BOOLEAN,
    amends_accession TEXT,
    primary_doc_url TEXT,
    raw_id BIGINT REFERENCES raw_object(raw_id),
    ingested_at TIMESTAMPTZ DEFAULT NOW()
);

-- Subsidiary mapping
CREATE TABLE IF NOT EXISTS subsidiary_map (
    subsidiary_id BIGSERIAL PRIMARY KEY,
    subsidiary_name_normalized TEXT,
    parent_cik VARCHAR(10),
    source_accession TEXT,
    source_exhibit TEXT,
    asof_date DATE,
    confidence NUMERIC,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_subsidiary_map_name_trgm ON subsidiary_map USING GIN (subsidiary_name_normalized gin_trgm_ops);

CREATE TABLE IF NOT EXISTS entity_alias (
    alias_id BIGSERIAL PRIMARY KEY,
    alias_name TEXT,
    cik VARCHAR(10),
    alias_type TEXT,
    source TEXT,
    confidence NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_entity_alias_name_trgm ON entity_alias USING GIN (alias_name gin_trgm_ops);

-- USASpending linkage
CREATE TABLE IF NOT EXISTS gov_award (
    award_id TEXT PRIMARY KEY,
    source TEXT,
    raw_id BIGINT REFERENCES raw_object(raw_id),
    payload JSONB
);

CREATE TABLE IF NOT EXISTS gov_award_issuer_link (
    award_id TEXT NOT NULL,
    cik VARCHAR(10) NOT NULL,
    confidence NUMERIC,
    method TEXT,
    reviewed_at TIMESTAMPTZ,
    PRIMARY KEY (award_id, cik)
);

-- SEC fails-to-deliver
CREATE TABLE IF NOT EXISTS sec_ftd (
    settlement_date DATE,
    cusip TEXT,
    ticker TEXT,
    issuer_name TEXT,
    price NUMERIC,
    quantity BIGINT,
    PRIMARY KEY (settlement_date, cusip)
);

-- Senate lobbying
CREATE TABLE IF NOT EXISTS lobbying_filing (
    filing_id TEXT PRIMARY KEY,
    filing_type TEXT,
    received_date DATE,
    registrant TEXT,
    client TEXT,
    raw_id BIGINT REFERENCES raw_object(raw_id),
    source_url TEXT
);

CREATE TABLE IF NOT EXISTS lobbying_issue (
    filing_id TEXT REFERENCES lobbying_filing(filing_id),
    issue_code TEXT,
    issue_text TEXT,
    bill_numbers TEXT
);
