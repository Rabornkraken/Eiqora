"""Add options_daily_summary table.

Revision ID: 0003_add_options_data
Revises: 0002_add_analyst_ratings
Create Date: 2026-01-14

"""
from alembic import op
import sqlalchemy as sa

revision = '0003_add_options_data'
down_revision = '0002_add_analyst_ratings'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'options_daily_summary',
        sa.Column('summary_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(10), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        
        # Put/Call metrics
        sa.Column('put_call_ratio_volume', sa.Numeric(10, 4), nullable=True),
        sa.Column('put_call_ratio_oi', sa.Numeric(10, 4), nullable=True),
        sa.Column('total_call_volume', sa.BigInteger(), nullable=True),
        sa.Column('total_put_volume', sa.BigInteger(), nullable=True),
        sa.Column('total_call_oi', sa.BigInteger(), nullable=True),
        sa.Column('total_put_oi', sa.BigInteger(), nullable=True),
        
        # Volatility
        sa.Column('atm_iv', sa.Numeric(10, 4), nullable=True),
        
        # Key levels
        sa.Column('max_pain_strike', sa.Numeric(10, 2), nullable=True),
        sa.Column('highest_oi_call_strike', sa.Numeric(10, 2), nullable=True),
        sa.Column('highest_oi_put_strike', sa.Numeric(10, 2), nullable=True),
        
        # Reference
        sa.Column('spot_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('expiration_date', sa.Date(), nullable=True),
        
        sa.Column('collected_at', sa.TIMESTAMP(), server_default=sa.text('NOW()'), nullable=True),
        
        sa.PrimaryKeyConstraint('summary_id'),
        sa.UniqueConstraint('symbol', 'date', name='uq_options_summary_symbol_date')
    )
    
    op.create_index('idx_options_summary_symbol_date', 'options_daily_summary', ['symbol', 'date'])
    op.create_index('idx_options_summary_date', 'options_daily_summary', ['date'])


def downgrade():
    op.drop_index('idx_options_summary_date', table_name='options_daily_summary')
    op.drop_index('idx_options_summary_symbol_date', table_name='options_daily_summary')
    op.drop_table('options_daily_summary')
