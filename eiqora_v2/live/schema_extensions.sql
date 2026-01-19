-- Live Trading Schema Extensions
-- Additional tables for analysis tracking and trigger management

-- =============================================================
-- ECONOMIC EVENTS (for macro blackout checks)
-- =============================================================
CREATE TABLE IF NOT EXISTS economic_event (
    event_id SERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    event_time TIME,
    event_name VARCHAR(200) NOT NULL,
    country VARCHAR(10) DEFAULT 'US',
    impact VARCHAR(10), -- HIGH, MEDIUM, LOW
    actual VARCHAR(50),
    forecast VARCHAR(50),
    previous VARCHAR(50),
    source VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(event_name, event_date, country)
);

ALTER TABLE economic_event
    ADD COLUMN IF NOT EXISTS event_time TIME;

CREATE INDEX IF NOT EXISTS idx_economic_event_date ON economic_event(event_date DESC);
CREATE INDEX IF NOT EXISTS idx_economic_event_impact ON economic_event(impact);

-- =============================================================
-- ANALYSIS LOG (records all agent analysis, GO or NO_GO)
-- =============================================================
CREATE TABLE IF NOT EXISTS analysis_log (
    analysis_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(10) NOT NULL,
    analysis_date DATE NOT NULL,
    analysis_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_type VARCHAR(50) NOT NULL,
    trigger_id VARCHAR(100), -- Reference to the trigger (e.g., news_id)
    trigger_detail JSONB,
    trigger_hash VARCHAR(64),

    -- Final outcome
    final_decision VARCHAR(20) NOT NULL, -- GO, NO_GO, REVIEW
    decision_reason TEXT,

    -- Individual agent outputs (stored even for NO_GO)
    topdown_output JSONB,
    context_output JSONB,
    chart_output JSONB,
    fundamental_output JSONB,
    idea_generator_output JSONB,
    exit_policy_output JSONB,
    red_team_output JSONB,
    decision_output JSONB,
    position_manager_output JSONB,
    sanity_veto_output JSONB,
    risk_model_output JSONB,

    -- Metadata
    profile_score DECIMAL(4,3),
    technical_score DECIMAL(4,3),
    processing_time_ms INTEGER,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analysis_log_symbol_date ON analysis_log(symbol, analysis_date DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_log_decision ON analysis_log(final_decision);
CREATE INDEX IF NOT EXISTS idx_analysis_log_trigger_id ON analysis_log(trigger_id);
CREATE INDEX IF NOT EXISTS idx_analysis_log_trigger_hash ON analysis_log(trigger_hash, symbol);

ALTER TABLE analysis_log
ADD COLUMN IF NOT EXISTS red_team_output JSONB;

ALTER TABLE analysis_log
ADD COLUMN IF NOT EXISTS risk_model_output JSONB;

ALTER TABLE analysis_log
ADD COLUMN IF NOT EXISTS trigger_hash VARCHAR(64);

-- =============================================================
-- SUPPRESSED TRIGGERS (blocked by analysis gate)
-- =============================================================
DROP TABLE IF EXISTS processed_trigger;

CREATE TABLE IF NOT EXISTS suppressed_trigger (
    trigger_hash VARCHAR(64) NOT NULL,
    symbol VARCHAR(10) NOT NULL,
    trigger_type VARCHAR(50) NOT NULL,
    trigger_source_id VARCHAR(200), -- e.g., news_id, filing accession
    trigger_detail JSONB,
    detected_at TIMESTAMPTZ,
    suppressed_reason TEXT,
    technical_score DECIMAL(4,3),
    profile_score DECIMAL(4,3),
    suppressed_count INT DEFAULT 1,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (trigger_hash, symbol)
);

CREATE INDEX IF NOT EXISTS idx_suppressed_trigger_symbol ON suppressed_trigger(symbol);
CREATE INDEX IF NOT EXISTS idx_suppressed_trigger_type ON suppressed_trigger(trigger_type);
CREATE INDEX IF NOT EXISTS idx_suppressed_trigger_date ON suppressed_trigger(last_seen_at DESC);

-- =============================================================
-- Update trade_signal to link to analysis_log
-- =============================================================
ALTER TABLE trade_signal 
ADD COLUMN IF NOT EXISTS analysis_id UUID REFERENCES analysis_log(analysis_id);

-- =============================================================
-- CANDIDATE SELECTION LOG (track all 50 symbols' evaluation)
-- =============================================================
CREATE TABLE IF NOT EXISTS candidate_selection_log (
    symbol VARCHAR(10) NOT NULL,
    scan_date DATE NOT NULL,
    technical_score DECIMAL(4,3),
    profile_score DECIMAL(4,3),
    total_score DECIMAL(4,3),
    is_selected BOOLEAN,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (symbol, scan_date)
);

CREATE INDEX IF NOT EXISTS idx_candidate_selection_log_date ON candidate_selection_log(scan_date DESC);

-- =============================================================
-- ACCOUNT STATE & SNAPSHOTS
-- =============================================================
ALTER TABLE position
    ADD COLUMN IF NOT EXISTS shares DECIMAL(20, 6),
    ADD COLUMN IF NOT EXISTS notional_value DECIMAL(14, 4),
    ADD COLUMN IF NOT EXISTS max_profit DECIMAL(14, 4) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS max_drawdown DECIMAL(14, 4) DEFAULT 0;

CREATE TABLE IF NOT EXISTS account_state (
    account_id TEXT PRIMARY KEY,
    base_currency TEXT DEFAULT 'USD',
    starting_equity NUMERIC,
    cash_balance NUMERIC,
    equity NUMERIC,
    realized_pnl NUMERIC DEFAULT 0,
    unrealized_pnl NUMERIC DEFAULT 0,
    gross_exposure NUMERIC DEFAULT 0,
    net_exposure NUMERIC DEFAULT 0,
    positions_count INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS account_snapshot (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id TEXT NOT NULL REFERENCES account_state(account_id),
    asof_ts TIMESTAMPTZ NOT NULL,
    cash_balance NUMERIC,
    equity NUMERIC,
    realized_pnl NUMERIC,
    unrealized_pnl NUMERIC,
    gross_exposure NUMERIC,
    net_exposure NUMERIC,
    positions_count INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_account_snapshot_account_ts ON account_snapshot(account_id, asof_ts DESC);
