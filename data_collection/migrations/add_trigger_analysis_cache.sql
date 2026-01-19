-- Migration: Add trigger analysis cache table
-- Purpose: Prevent redundant LLM analyses by caching decisions with context
-- Strategy: Condition-based invalidation (only re-analyze if conditions improve materially)

CREATE TABLE IF NOT EXISTS trigger_analysis_cache (
    id SERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    decision TEXT NOT NULL,  -- 'BUY', 'PASS', 'SELL'
    analyzed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- Context snapshot at analysis time (JSON for flexibility)
    context JSONB NOT NULL,
    /* Example context structure:
    {
        "hourly_score": 0.45,
        "hourly_breakdown": {"hourly_trend_up": 0.25, "hourly_rsi_neutral": 0.15, ...},
        "daily_score": 0.82,
        "price": 258.50,
        "hourly_rsi": 48,
        "volume_z": 1.2,
        "vwap_distance_pct": -0.5,
        "cmf": 0.15,
        "mfi_14": 55,
        "trend_direction": "DOWNTREND",
        "trigger_details": {...}
    }
    */
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create unique index instead of constraint (more flexible)
CREATE UNIQUE INDEX idx_trigger_cache_daily_unique 
ON trigger_analysis_cache(symbol, trigger_type, (DATE(analyzed_at AT TIME ZONE 'America/New_York')));

-- Index for fast lookups
CREATE INDEX idx_trigger_cache_lookup ON trigger_analysis_cache(symbol, trigger_type, analyzed_at);

-- Index for expiration cleanup
CREATE INDEX idx_trigger_cache_expires ON trigger_analysis_cache(expires_at);

-- Index for decision filtering
CREATE INDEX idx_trigger_cache_decision ON trigger_analysis_cache(decision) WHERE decision != 'PASS';

COMMENT ON TABLE trigger_analysis_cache IS 'Caches trigger analysis decisions with context to prevent redundant LLM analyses. Re-analyzes only when conditions improve significantly.';
COMMENT ON COLUMN trigger_analysis_cache.context IS 'JSON snapshot of market conditions at analysis time, used to detect significant changes';
COMMENT ON COLUMN trigger_analysis_cache.expires_at IS 'Cache expires at end of trading day (4 PM ET). No overnight caching.';
