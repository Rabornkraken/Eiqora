-- Add capital tracking columns to trigger_backtest_run table
-- Migration: add_capital_tracking_columns

ALTER TABLE trigger_backtest_run
ADD COLUMN IF NOT EXISTS starting_capital NUMERIC(12, 2),
ADD COLUMN IF NOT EXISTS final_capital NUMERIC(12, 2),
ADD COLUMN IF NOT EXISTS total_return_pct NUMERIC(8, 2),
ADD COLUMN IF NOT EXISTS max_drawdown_pct NUMERIC(8, 2);

COMMENT ON COLUMN trigger_backtest_run.starting_capital IS 'Starting capital for the backtest (default $10,000)';
COMMENT ON COLUMN trigger_backtest_run.final_capital IS 'Final capital after all trades';
COMMENT ON COLUMN trigger_backtest_run.total_return_pct IS 'Total return percentage from starting capital';
COMMENT ON COLUMN trigger_backtest_run.max_drawdown_pct IS 'Maximum drawdown from peak during backtest';
