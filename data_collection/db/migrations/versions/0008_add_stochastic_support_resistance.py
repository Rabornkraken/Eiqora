"""
Add stochastic oscillator and support/resistance levels to market_bar_daily.

These indicators are needed for daily bar trigger backtesting.
"""
from alembic import op

revision = "0008_add_stochastic_support_resistance"
down_revision = "0007_add_money_flow_indicators"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add stochastic and support/resistance columns to market_bar_daily
    op.execute("""
        ALTER TABLE market_bar_daily 
        ADD COLUMN IF NOT EXISTS stochastic_k NUMERIC(8,4),
        ADD COLUMN IF NOT EXISTS stochastic_d NUMERIC(8,4),
        ADD COLUMN IF NOT EXISTS support_level NUMERIC(12,4),
        ADD COLUMN IF NOT EXISTS resistance_level NUMERIC(12,4),
        ADD COLUMN IF NOT EXISTS volume_z_20 NUMERIC(8,4);

        CREATE INDEX IF NOT EXISTS idx_market_bar_daily_stochastic 
            ON market_bar_daily(symbol, date) 
            WHERE stochastic_k IS NOT NULL;

        COMMENT ON COLUMN market_bar_daily.stochastic_k IS 'Stochastic %K (14-period fast line)';
        COMMENT ON COLUMN market_bar_daily.stochastic_d IS 'Stochastic %D (3-period slow line / signal)';
        COMMENT ON COLUMN market_bar_daily.support_level IS 'Support level based on recent swing lows';
        COMMENT ON COLUMN market_bar_daily.resistance_level IS 'Resistance level based on recent swing highs';
        COMMENT ON COLUMN market_bar_daily.volume_z_20 IS 'Volume Z-score over 20-day period';
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE market_bar_daily
        DROP COLUMN IF EXISTS stochastic_k,
        DROP COLUMN IF EXISTS stochastic_d,
        DROP COLUMN IF EXISTS support_level,
        DROP COLUMN IF EXISTS resistance_level,
        DROP COLUMN IF EXISTS volume_z_20;

        DROP INDEX IF EXISTS idx_market_bar_daily_stochastic;
    """)
