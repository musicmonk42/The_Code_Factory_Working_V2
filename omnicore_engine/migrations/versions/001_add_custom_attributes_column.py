"""add custom_attributes column to generator_agent_state

Revision ID: 001_add_custom_attributes
Revises: 
Create Date: 2025-02-14 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_add_custom_attributes'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add custom_attributes JSONB column to generator_agent_state table."""
    # Add the column only if it doesn't exist (PostgreSQL)
    # For PostgreSQL, use JSONB type for better performance
    op.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'generator_agent_state' 
                AND column_name = 'custom_attributes'
            ) THEN
                ALTER TABLE generator_agent_state 
                ADD COLUMN custom_attributes JSONB;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """Remove custom_attributes column from generator_agent_state table."""
    op.drop_column('generator_agent_state', 'custom_attributes')
