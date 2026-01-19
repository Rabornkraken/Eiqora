-- Add hourly technical indicator columns to market_bar_hourly table
-- Run with: psql eiqora < add_hourly_indicators_columns.sql

BEGIN;

-- Add technical indicator columns
ALTER TABLE market_bar_hourly ADD COLUMN IF NOT EXISTS
    rsi_14 NUMERIC,           -- Hourly RSI (14 periods)
    macd_histogram NUMERIC,   -- MACD histogram
    macd_line NUMERIC,        -- MACD line  
    macd_signal NUMERIC,      -- MACD signal line
    vwap NUMERIC,             -- Volume-Weighted Average Price
    cmf_20 NUMERIC,           -- Chaikin Money Flow (20 periods)
    mfi_14 NUMERIC,           -- Money Flow Index (14 periods)
    volume_z_20h NUMERIC,     -- Volume Z-score (20-hour basis)
    trend_direction TEXT,     -- UPTREND/DOWNTREND/SIDEWAYS
    support_level NUMERIC,    -- Nearest support from recent hours
    resistance_level NUMERIC; -- Nearest resistance from recent hours

-- Add index for efficient queries by symbol and datetime
CREATE INDEX IF NOT EXISTS idx_market_bar_hourly_symbol_datetime 
    ON market_bar_hourly(symbol, datetime DESC);

-- Add index for trend direction filtering
CREATE INDEX IF NOT EXISTS idx_market_bar_hourly_trend 
    ON market_bar_hourly(trend_direction) WHERE trend_direction IS NOT NULL;

COMMIT;

-- Verify columns added
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'market_bar_hourly' 
  AND column_name IN ('rsi_14', 'macd_histogram', 'vwap', 'cmf_20', 'mfi_14', 'volume_z_20h', 'trend_direction')
ORDER BY column_name;
