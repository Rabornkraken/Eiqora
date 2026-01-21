
ALTER TABLE trigger_backtest_run
ADD COLUMN IF NOT EXISTS trigger_details JSONB;

DROP TABLE IF EXISTS trigger_backtest_run_detail;
