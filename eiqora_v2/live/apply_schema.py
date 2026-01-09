"""
Apply live trading database schema.
"""

import os
import psycopg

def apply_schema():
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/finance")
    
    schema_sql = """
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

-- Positions: database-tracked trade lifecycle
CREATE TABLE IF NOT EXISTS position (
    position_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(10) NOT NULL,
    direction VARCHAR(5) NOT NULL, -- LONG / SHORT
    entry_date TIMESTAMP NOT NULL,
    entry_price DECIMAL(12, 4) NOT NULL,
    shares DECIMAL(20, 6),
    notional_value DECIMAL(14, 4),
    current_price DECIMAL(12, 4),
    unrealized_pnl_pct DECIMAL(12, 4),
    unrealized_pnl DECIMAL(12, 4),
    take_profit DECIMAL(12, 4),
    stop_loss DECIMAL(12, 4),
    time_stop_date TIMESTAMP,
    status VARCHAR(10) NOT NULL DEFAULT 'ACTIVE',
    conviction VARCHAR(10),
    reasoning TEXT,
    position_size_pct DECIMAL(6, 3),
    max_profit_pct DECIMAL(12, 4) DEFAULT 0,
    max_loss_pct DECIMAL(12, 4) DEFAULT 0,
    max_profit DECIMAL(14, 4) DEFAULT 0,
    max_drawdown DECIMAL(14, 4) DEFAULT 0,
    exit_price DECIMAL(12, 4),
    exit_date TIMESTAMP,
    realized_pnl_pct DECIMAL(12, 4),
    realized_pnl DECIMAL(12, 4),
    r_multiple DECIMAL(12, 4),
    exit_reason TEXT,
    exit_type VARCHAR(20),
    signal_id UUID,
    created_at TIMESTAMP DEFAULT NOW(),
    last_updated TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_position_symbol_active ON position(symbol) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_position_status ON position(status);

-- Account state & snapshots
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
"""
    
    print(f"Connecting to database...")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            print("Applying schema...")
            cur.execute(schema_sql)
            conn.commit()
            print("✅ Schema applied successfully")

if __name__ == "__main__":
    apply_schema()
