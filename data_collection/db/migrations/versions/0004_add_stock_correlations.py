"""Add stock correlations table.

Revision ID: 0004_add_stock_correlations
Revises: 0003_add_options_data
Create Date: 2026-01-14

"""
from alembic import op
import sqlalchemy as sa

revision = '0004_add_stock_correlations'
down_revision = '0003_add_options_data'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'stock_correlations',
        sa.Column('correlation_id', sa.Integer(), nullable=False),
        sa.Column('symbol_a', sa.String(10), nullable=False),
        sa.Column('symbol_b', sa.String(10), nullable=False),
        sa.Column('period_days', sa.Integer(), nullable=False),
        
        sa.Column('correlation_coef', sa.Numeric(6, 4), nullable=True),
        
        sa.Column('calculated_date', sa.Date(), nullable=False),
        
        sa.PrimaryKeyConstraint('correlation_id'),
        sa.UniqueConstraint('symbol_a', 'symbol_b', 'period_days', 'calculated_date',
                          name='uq_correlation_symbols_period_date')
    )
    
    op.create_index('idx_stock_corr_symbols', 'stock_correlations', ['symbol_a', 'symbol_b'])
    op.create_index('idx_stock_corr_date', 'stock_correlations', ['calculated_date'])


def downgrade():
    op.drop_index('idx_stock_corr_date', table_name='stock_correlations')
    op.drop_index('idx_stock_corr_symbols', table_name='stock_correlations')
    op.drop_table('stock_correlations')
