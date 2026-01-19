-- Add comprehensive technical indicators to market_bar_daily
-- This enables faster queries and consistent indicator storage

-- Add RSI indicators
ALTER TABLE market_bar_daily 
ADD COLUMN IF NOT EXISTS rsi_14 NUMERIC,
ADD COLUMN IF NOT EXISTS rsi_20 NUMERIC;

-- Add MACD indicators
ALTER TABLE market_bar_daily
ADD COLUMN IF NOT EXISTS macd NUMERIC,
ADD COLUMN IF NOT EXISTS macd_signal NUMERIC,
ADD COLUMN IF NOT EXISTS macd_hist NUMERIC;

-- Add Bollinger Bands
ALTER TABLE market_bar_daily
ADD COLUMN IF NOT EXISTS bb_upper_20 NUMERIC,
ADD COLUMN IF NOT EXISTS bb_middle_20 NUMERIC,
ADD COLUMN IF NOT EXISTS bb_lower_20 NUMERIC,
ADD COLUMN IF NOT EXISTS bb_width NUMERIC;

-- Add ATR (Average True Range)
ALTER TABLE market_bar_daily
ADD COLUMN IF NOT EXISTS atr_14 NUMERIC;

-- Add ADX (Average Directional Index)
ALTER TABLE market_bar_daily
ADD COLUMN IF NOT EXISTS adx_14 NUMERIC,
ADD COLUMN IF NOT EXISTS plus_di NUMERIC,
ADD COLUMN IF NOT EXISTS minus_di NUMERIC;

-- Create index for faster indicator queries
CREATE INDEX IF NOT EXISTS idx_bar_daily_indicators 
ON market_bar_daily(symbol, date) 
WHERE rsi_14 IS NOT NULL;

COMMENT ON COLUMN market_bar_daily.rsi_14 IS 'Relative Strength Index (14-period)';
COMMENT ON COLUMN market_bar_daily.macd IS 'MACD Line (12,26,9)';
COMMENT ON COLUMN market_bar_daily.bb_upper_20 IS 'Bollinger Band Upper (20, 2 std)';
COMMENT ON COLUMN market_bar_daily.atr_14 IS 'Average True Range (14-period)';
COMMENT ON COLUMN market_bar_daily.adx_14 IS 'Average Directional Index (14-period)';
