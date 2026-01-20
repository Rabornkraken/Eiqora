-- Trigger-Only Backtest Schema

CREATE TABLE IF NOT EXISTS trigger_backtest_result (
    result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    run_name TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,

    symbol VARCHAR(10) NOT NULL,
    trigger_type VARCHAR(50) NOT NULL,
    trigger_priority VARCHAR(20),
    trigger_time TIMESTAMPTZ NOT NULL,
    trigger_detail JSONB,

    entry_price DECIMAL(12, 4),
    atr14 DECIMAL(12, 6),
    sl_mult DECIMAL(6, 3) DEFAULT 1.5,
    tp_mult DECIMAL(6, 3) DEFAULT 3.0,
    stop_loss DECIMAL(12, 4),
    take_profit DECIMAL(12, 4),

    outcome VARCHAR(20) NOT NULL, -- TP_HIT, SL_HIT, NO_HIT, NO_DATA
    outcome_time TIMESTAMPTZ,
    bars_to_outcome INT,
    max_favorable_pct DECIMAL(8, 4),
    max_adverse_pct DECIMAL(8, 4),
    realized_pnl_pct DECIMAL(8, 4),

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trigger_backtest_run ON trigger_backtest_result(run_id);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_symbol ON trigger_backtest_result(symbol);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_trigger ON trigger_backtest_result(trigger_type);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_outcome ON trigger_backtest_result(outcome);
CREATE INDEX IF NOT EXISTS idx_trigger_backtest_time ON trigger_backtest_result(trigger_time DESC);
