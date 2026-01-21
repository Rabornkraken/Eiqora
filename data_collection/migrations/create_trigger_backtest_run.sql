
CREATE TABLE IF NOT EXISTS trigger_backtest_run (
    run_id UUID PRIMARY KEY,
    run_name TEXT NOT NULL,
    start_date DATE,
    end_date DATE,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    
    -- Metrics
    total_triggers INTEGER,
    executed_trades INTEGER,
    win_rate NUMERIC(10, 4),      -- stored as percentage (e.g., 55.50)
    total_pnl_pct NUMERIC(10, 4), -- stored as percentage
    avg_pnl_pct NUMERIC(10, 4),   -- stored as percentage
    
    -- Insights
    best_trigger_type TEXT,
    worst_trigger_type TEXT,
    
    -- Config
    parameters JSONB
);

-- Add foreign key to trigger_backtest_result if not already consistent
-- (Assuming trigger_backtest_result.run_id references this, but we might need to add the constraint)
