
CREATE TABLE IF NOT EXISTS trigger_backtest_run_detail (
    run_id UUID REFERENCES trigger_backtest_run(run_id),
    trigger_type TEXT NOT NULL,
    count INTEGER,
    wins INTEGER,
    losses INTEGER,
    win_rate NUMERIC(10, 4),
    total_pnl_pct NUMERIC(10, 4),
    avg_pnl_pct NUMERIC(10, 4),
    PRIMARY KEY (run_id, trigger_type)
);
