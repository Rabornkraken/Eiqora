"""
Add swing trading indicators to market_bar_daily.

New columns: ibs, williams_r_2, rsi_2, cumulative_rsi2,
close_n_day_low_7, close_n_day_high_7, td_setup_buy_count.
"""
from alembic import op

revision = "0009_add_swing_indicators"
down_revision = "0008_add_stochastic_support_resistance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE market_bar_daily
        ADD COLUMN IF NOT EXISTS ibs NUMERIC(8,4),
        ADD COLUMN IF NOT EXISTS williams_r_2 NUMERIC(8,4),
        ADD COLUMN IF NOT EXISTS rsi_2 NUMERIC(8,4),
        ADD COLUMN IF NOT EXISTS cumulative_rsi2 NUMERIC(8,4),
        ADD COLUMN IF NOT EXISTS close_n_day_low_7 BOOLEAN,
        ADD COLUMN IF NOT EXISTS close_n_day_high_7 BOOLEAN,
        ADD COLUMN IF NOT EXISTS td_setup_buy_count SMALLINT;

        COMMENT ON COLUMN market_bar_daily.ibs IS 'Internal Bar Strength: (close-low)/(high-low)';
        COMMENT ON COLUMN market_bar_daily.williams_r_2 IS 'Williams %R with 2-bar lookback';
        COMMENT ON COLUMN market_bar_daily.rsi_2 IS 'RSI with period=2';
        COMMENT ON COLUMN market_bar_daily.cumulative_rsi2 IS 'RSI(2) today + RSI(2) yesterday';
        COMMENT ON COLUMN market_bar_daily.close_n_day_low_7 IS 'Close <= min(close) over last 7 days';
        COMMENT ON COLUMN market_bar_daily.close_n_day_high_7 IS 'Close >= max(close) over last 7 days';
        COMMENT ON COLUMN market_bar_daily.td_setup_buy_count IS 'TD Sequential buy setup count (0-9)';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE market_bar_daily
        DROP COLUMN IF EXISTS ibs,
        DROP COLUMN IF EXISTS williams_r_2,
        DROP COLUMN IF EXISTS rsi_2,
        DROP COLUMN IF EXISTS cumulative_rsi2,
        DROP COLUMN IF EXISTS close_n_day_low_7,
        DROP COLUMN IF EXISTS close_n_day_high_7,
        DROP COLUMN IF EXISTS td_setup_buy_count;
    """)
