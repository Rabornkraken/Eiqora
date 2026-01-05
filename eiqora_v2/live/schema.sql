-- Live Trading Schema: Trade Signals
-- Stores pre-market scanner output for tracking and analysis

CREATE TABLE IF NOT EXISTS trade_signal (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(10) NOT NULL,
    signal_date DATE NOT NULL,
    trigger_type VARCHAR(50),
    action VARCHAR(10) NOT NULL, -- GO, NO_GO
    entry_price DECIMAL(10, 2),
    stop_loss DECIMAL(10, 2),
    take_profit DECIMAL(10, 2),
    conviction DECIMAL(3, 2), -- 0.0 to 1.0
    reasoning TEXT,
    agent_outputs JSONB,
    trigger_detail JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, signal_date, trigger_type)
);

CREATE INDEX IF NOT EXISTS idx_trade_signal_date ON trade_signal(signal_date DESC);
CREATE INDEX IF NOT EXISTS idx_trade_signal_symbol ON trade_signal(symbol);
CREATE INDEX IF NOT EXISTS idx_trade_signal_action ON trade_signal(action);
