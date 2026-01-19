"""Add stock relationships and influential statements tables.

Revision ID: 0005_add_relationships_and_statements
Revises: 0004_add_stock_correlations
Create Date: 2026-01-14

"""
from alembic import op
import sqlalchemy as sa

revision = '0005_add_relationships_and_statements'
down_revision = '0004_add_stock_correlations'
branch_labels = None
depends_on = None


def upgrade():
    # Stock relationships table
    op.create_table(
        'stock_relationships',
        sa.Column('relationship_id', sa.Integer(), nullable=False),
        sa.Column('symbol_from', sa.String(10), nullable=False),
        sa.Column('symbol_to', sa.String(10), nullable=False),
        sa.Column('relationship_type', sa.String(50), nullable=False),
        
        sa.Column('strength', sa.Numeric(3, 2), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        
        sa.Column('data_source', sa.String(50), server_default='MANUAL', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()'), nullable=True),
        
        sa.PrimaryKeyConstraint('relationship_id'),
        sa.UniqueConstraint('symbol_from', 'symbol_to', 'relationship_type',
                          name='uq_relationship_from_to_type')
    )
    
    # Influential figures table
    op.create_table(
        'influential_figures',
        sa.Column('figure_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('title', sa.String(200), nullable=True),
        sa.Column('organization', sa.String(200), nullable=True),
        sa.Column('category', sa.String(50), nullable=True),
        sa.Column('influence_score', sa.Numeric(3, 2), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('NOW()'), nullable=True),
        
        sa.PrimaryKeyConstraint('figure_id'),
        sa.UniqueConstraint('name', name='uq_influential_figure_name')
    )
    
    # Influential statements table
    op.create_table(
        'influential_statements',
        sa.Column('statement_id', sa.Integer(), nullable=False),
        sa.Column('figure_id', sa.Integer(), nullable=False),
        sa.Column('statement_date', sa.TIMESTAMP(), nullable=False),
        
        sa.Column('statement_text', sa.Text(), nullable=False),
        sa.Column('statement_source', sa.String(200), nullable=True),
        
        sa.Column('sentiment', sa.String(20), nullable=True),
        sa.Column('mentioned_symbols', sa.ARRAY(sa.String(10)), nullable=True),
        
        sa.Column('collected_at', sa.TIMESTAMP(), server_default=sa.text('NOW()'), nullable=True),
        
        sa.PrimaryKeyConstraint('statement_id'),
        sa.ForeignKeyConstraint(['figure_id'], ['influential_figures.figure_id'])
    )
    
    op.create_index('idx_inf_statements_date', 'influential_statements', ['statement_date'])


def downgrade():
    op.drop_index('idx_inf_statements_date', table_name='influential_statements')
    op.drop_table('influential_statements')
    op.drop_table('influential_figures')
    op.drop_table('stock_relationships')
