"""Add analyst ratings table.

Revision ID: 0002_add_analyst_ratings
Revises: 0001_initial
Create Date: 2026-01-12 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002_add_analyst_ratings'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'analyst_rating',
        sa.Column('rating_id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('symbol', sa.Text(), nullable=False),
        sa.Column('firm', sa.Text(), nullable=False),
        sa.Column('rating_date', sa.DateTime(timezone=True), nullable=False),
        
        # Rating details
        sa.Column('action', sa.Text(), nullable=True),  # up, down, main, init, reit
        sa.Column('to_grade', sa.Text(), nullable=True),
        sa.Column('from_grade', sa.Text(), nullable=True),
        
        # Price Target details (High Value)
        sa.Column('price_target', sa.Numeric(), nullable=True),
        sa.Column('price_target_action', sa.Text(), nullable=True), # Raises, Lowers
        
        # Metadata
        sa.Column('source', sa.Text(), server_default='yfinance'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        
        # Constraints
        sa.UniqueConstraint('symbol', 'firm', 'rating_date', name='uq_analyst_rating_event')
    )
    
    # Indexes for fast retrieval
    op.create_index('idx_analyst_rating_symbol_date', 'analyst_rating', ['symbol', 'rating_date'])
    op.create_index('idx_analyst_rating_date', 'analyst_rating', ['rating_date'])
    op.create_index('idx_analyst_rating_firm', 'analyst_rating', ['firm'])


def downgrade() -> None:
    op.drop_index('idx_analyst_rating_firm', table_name='analyst_rating')
    op.drop_index('idx_analyst_rating_date', table_name='analyst_rating')
    op.drop_index('idx_analyst_rating_symbol_date', table_name='analyst_rating')
    op.drop_table('analyst_rating')
