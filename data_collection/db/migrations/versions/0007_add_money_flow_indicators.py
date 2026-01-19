"""
Add money flow indicators to market_bar_daily.

Columns: MFI (Money Flow Index), CMF (Chaikin Money Flow), OBV (On-Balance Volume)
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0007_add_money_flow_indicators'
down_revision = '0006_profile_performance'
branch_labels = None
depends_on = None


def upgrade():
    # Add money flow columns to market_bar_daily
    op.execute("""
        ALTER TABLE market_bar_daily 
        ADD COLUMN IF NOT EXISTS mfi_14 DECIMAL(5, 2),
        ADD COLUMN IF NOT EXISTS cmf_20 DECIMAL(6, 4),
        ADD COLUMN IF NOT EXISTS obv BIGINT,
        ADD COLUMN IF NOT EXISTS ad_line BIGINT;
        
        CREATE INDEX IF NOT EXISTS idx_market_bar_daily_mfi ON market_bar_daily(symbol, date) WHERE mfi_14 IS NOT NULL;
        
        COMMENT ON COLUMN market_bar_daily.mfi_14 IS 'Money Flow Index (14-period) - Volume-weighted RSI';
        COMMENT ON COLUMN market_bar_daily.cmf_20 IS 'Chaikin Money Flow (20-period) - Accumulation/distribution indicator';
        COMMENT ON COLUMN market_bar_daily.obv IS 'On-Balance Volume - Cumulative volume based on price direction';
        COMMENT ON COLUMN market_bar_daily.ad_line IS 'Accumulation/Distribution Line - Volume-weighted price indicator';
    """)


def downgrade():
    op.execute("""
        ALTER TABLE market_bar_daily
        DROP COLUMN IF EXISTS mfi_14,
        DROP COLUMN IF EXISTS cmf_20,
        DROP COLUMN IF EXISTS obv,
        DROP COLUMN IF EXISTS ad_line;
        
        DROP INDEX IF EXISTS idx_market_bar_daily_mfi;
    """)
