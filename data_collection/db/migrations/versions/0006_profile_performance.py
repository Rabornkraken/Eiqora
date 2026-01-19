"""
Profile performance tracking schema.

Tracks profile scores against forward returns to validate prediction quality.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0006_profile_performance'
down_revision = '0005_add_relationships_and_statements'
branch_labels = None
depends_on = None


def upgrade():
    # Create profile_performance table
    op.execute("""
        CREATE TABLE IF NOT EXISTS profile_performance (
            symbol VARCHAR(10) NOT NULL,
            profile_generated_date DATE NOT NULL,
            profile_score DECIMAL(5, 3),
            profile_breakdown JSONB,
            
            -- Forward returns (updated retroactively)
            forward_1w_return DECIMAL(8, 4),
            forward_1m_return DECIMAL(8, 4),
            forward_3m_return DECIMAL(8, 4),
            
            -- Sector benchmarks for relative performance
            sector_etf VARCHAR(10),
            sector_forward_1w DECIMAL(8, 4),
            sector_forward_1m DECIMAL(8, 4),
            sector_forward_3m DECIMAL(8, 4),
            
            -- SPY benchmark
            spy_forward_1w DECIMAL(8, 4),
            spy_forward_1m DECIMAL(8, 4),
            spy_forward_3m DECIMAL(8, 4),
            
            -- Metadata
            created_at TIMESTAMP DEFAULT NOW(),
            returns_updated_at TIMESTAMP,
            
            PRIMARY KEY (symbol, profile_generated_date)
        );
        
        CREATE INDEX idx_profile_perf_generated_date ON profile_performance(profile_generated_date);
        CREATE INDEX idx_profile_perf_score ON profile_performance(profile_score);
        CREATE INDEX idx_profile_perf_symbol ON profile_performance(symbol);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS profile_performance CASCADE;")
