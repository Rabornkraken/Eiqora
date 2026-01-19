-- Backtest Database Schema
-- Stores historical backtest results in same format as live pipeline

-- =============================================================
-- BACKTEST RUN (Metadata for each backtest execution)
-- =============================================================
CREATE TABLE IF NOT EXISTS backtest_run (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_name VARCHAR(100),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    parameters JSONB, -- max_triggers, save_analyses, etc.
    
    -- Statistics
    total_triggers INT DEFAULT 0,
    go_count INT DEFAULT 0,
    no_go_count INT DEFAULT 0,
    error_count INT DEFAULT 0,
    
    -- Timing
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'RUNNING', -- RUNNING, COMPLETED, FAILED
    
    -- Notes
    description TEXT,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_run_started ON backtest_run(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_run_name ON backtest_run(run_name);

-- =============================================================
-- BACKTEST ANALYSIS (Mirrors analysis_log for backtest)
-- =============================================================
CREATE TABLE IF NOT EXISTS backtest_analysis (
    analysis_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES backtest_run(run_id) ON DELETE CASCADE,
    
    -- Trigger context
    symbol VARCHAR(10) NOT NULL,
    analysis_date DATE NOT NULL,
    analysis_time TIMESTAMPTZ NOT NULL,
    trigger_type VARCHAR(50) NOT NULL,
    trigger_detail JSONB,
    trigger_priority VARCHAR(20), -- HIGH, MEDIUM, LOW
    
    -- Final decision
    final_decision VARCHAR(20) NOT NULL, -- GO, NO_GO, ERROR
    decision_reason TEXT,
    
    -- Agent outputs (same structure as live analysis_log)
    topdown_output JSONB,
    context_output JSONB,
    chart_output JSONB,
    supply_chain_output JSONB,
    fundamental_output JSONB,
    idea_generator_output JSONB,
    exit_policy_output JSONB,
    red_team_output JSONB,
    decision_output JSONB,
    position_manager_output JSONB,
    veto_output JSONB,
    risk_model_output JSONB,
    
    -- Metadata
    profile_score DECIMAL(4,3),
    technical_score DECIMAL(4,3),
    processing_time_ms INTEGER,
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_analysis_run ON backtest_analysis(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_analysis_symbol_date ON backtest_analysis(symbol, analysis_date DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_analysis_decision ON backtest_analysis(final_decision);
CREATE INDEX IF NOT EXISTS idx_backtest_analysis_trigger ON backtest_analysis(trigger_type);

-- =============================================================
-- BACKTEST POSITION (Track hypothetical trades with outcomes)
-- =============================================================
CREATE TABLE IF NOT EXISTS backtest_position (
    position_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES backtest_run(run_id) ON DELETE CASCADE,
    analysis_id UUID REFERENCES backtest_analysis(analysis_id),
    
    symbol VARCHAR(10) NOT NULL,
    direction VARCHAR(10) DEFAULT 'LONG',
    
    -- Entry
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price DECIMAL(12, 4) NOT NULL,
    
    -- Risk levels
    stop_loss DECIMAL(12, 4),
    take_profit DECIMAL(12, 4),
    time_stop_days INT DEFAULT 30,
    
    -- Exit
    exit_time TIMESTAMPTZ,
    exit_price DECIMAL(12, 4),
    exit_reason TEXT,
    exit_type VARCHAR(20), -- TP, SL, EXPIRED, TIME_STOP, NO_DATA
    
    -- Outcome tracking
    bars_held INT,
    max_profit DECIMAL(14, 4) DEFAULT 0,
    max_drawdown DECIMAL(14, 4) DEFAULT 0,
    realized_pnl_pct DECIMAL(8, 4),
    
    -- Trade metadata
    conviction VARCHAR(20), -- HIGH, MEDIUM, LOW
    position_size_pct DECIMAL(5, 4),
    trigger_type VARCHAR(50),
    
    status VARCHAR(20) DEFAULT 'CLOSED', -- Always CLOSED for backtest
    
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_backtest_position_run ON backtest_position(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_position_symbol ON backtest_position(symbol);
CREATE INDEX IF NOT EXISTS idx_backtest_position_exit_type ON backtest_position(exit_type);
CREATE INDEX IF NOT EXISTS idx_backtest_position_pnl ON backtest_position(realized_pnl_pct DESC);
