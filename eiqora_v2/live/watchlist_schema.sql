-- Watchlist table for storing daily candidate selections
CREATE TABLE IF NOT EXISTS watchlist (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    scan_date DATE NOT NULL,
    total_score DECIMAL(4. 3),
    technical_score DECIMAL(4, 3),
    profile_score DECIMAL(4, 3),
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(symbol, scan_date)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_date ON watchlist(scan_date DESC);
CREATE INDEX IF NOT EXISTS idx_watchlist_symbol ON watchlist(symbol);
