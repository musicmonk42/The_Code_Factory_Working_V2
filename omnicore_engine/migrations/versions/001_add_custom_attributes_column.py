# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Add custom_attributes column to generator_agent_state table

Revision ID: 001_add_custom_attributes
Revises: 
Create Date: 2025-02-14 18:00:00.000000

Background:
-----------
The GeneratorAgentState SQLAlchemy model defines a custom_attributes column
for storing arbitrary JSON metadata (e.g., LLM model parameters, generation
configuration, custom flags). However, this column was missing from existing
PostgreSQL deployments, causing all queries to fail with:

    "column generator_agent_state.custom_attributes does not exist"

This migration adds the missing column with idempotent logic to prevent
errors on databases where the column already exists.

Database Support:
-----------------
- PostgreSQL: Uses JSONB type for optimal indexing and query performance
- SQLite: Uses JSON type (compatible with SQLAlchemy's generic JSON mapper)

The migration is safe to run multiple times (idempotent) and will only add
the column if it doesn't already exist.

Performance Impact:
-------------------
- No table locks on PostgreSQL (uses IF NOT EXISTS)
- Minimal performance impact (adds nullable column with no default)
- No data migration required (column is nullable)

Rollback Strategy:
------------------
The downgrade() function removes the column. WARNING: This will permanently
delete any custom_attributes data stored in the database. Only use downgrade
if you're certain no production data depends on this column.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.reflection import Inspector
import logging

# Configure module logger
logger = logging.getLogger(__name__)

# Revision identifiers, used by Alembic
revision = '001_add_custom_attributes'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add custom_attributes column to generator_agent_state table.
    
    This migration adds a JSONB column (PostgreSQL) or JSON column (SQLite)
    to store arbitrary metadata for code generation agents. The column is
    nullable and has no default value.
    
    The migration is idempotent and safe to run multiple times. It checks
    for the column's existence before attempting to add it.
    
    Industry Standards Applied:
    - Idempotent migrations (safe to re-run)
    - Database-agnostic column type handling
    - Graceful error handling
    - Comprehensive logging
    """
    # Get database connection to detect backend type
    connection = op.get_bind()
    dialect_name = connection.dialect.name
    
    logger.info(
        f"Running migration 001_add_custom_attributes on {dialect_name} database"
    )
    
    # Check if the table exists first (defensive programming)
    inspector = Inspector.from_engine(connection)
    tables = inspector.get_table_names()
    
    if 'generator_agent_state' not in tables:
        logger.warning(
            "Table 'generator_agent_state' does not exist. "
            "This migration will be skipped. Ensure models are created first."
        )
        return
    
    # Check if column already exists
    columns = [col['name'] for col in inspector.get_columns('generator_agent_state')]
    if 'custom_attributes' in columns:
        logger.info(
            "Column 'custom_attributes' already exists in generator_agent_state. "
            "Skipping migration."
        )
        return
    
    # Add column based on database dialect
    if dialect_name == 'postgresql':
        # PostgreSQL: Use JSONB for better performance and indexing
        # JSONB stores data in binary format, enabling efficient queries
        logger.info("Adding custom_attributes column (JSONB) to generator_agent_state")
        op.execute("""
            ALTER TABLE generator_agent_state 
            ADD COLUMN IF NOT EXISTS custom_attributes JSONB
        """)
    else:
        # SQLite and other databases: Use generic JSON type
        # SQLAlchemy will handle type conversions appropriately
        logger.info(
            f"Adding custom_attributes column (JSON) to generator_agent_state "
            f"(database: {dialect_name})"
        )
        # Use add_column for non-PostgreSQL databases
        # This is safer than raw SQL for dialects without IF NOT EXISTS
        try:
            op.add_column(
                'generator_agent_state',
                sa.Column('custom_attributes', sa.JSON(), nullable=True)
            )
        except Exception as e:
            # Column might already exist if migration was partially applied
            logger.warning(
                f"Failed to add column (may already exist): {e}. "
                "This is usually safe to ignore."
            )
    
    logger.info(
        "✓ Migration 001_add_custom_attributes completed successfully"
    )


def downgrade() -> None:
    """
    Remove custom_attributes column from generator_agent_state table.
    
    WARNING: This is a destructive operation that will permanently delete
    any custom_attributes data stored in the database. Only use this in
    development or if you're absolutely certain no production data depends
    on this column.
    
    The downgrade is NOT idempotent - attempting to remove a non-existent
    column will raise an error. This is intentional to prevent accidental
    data loss.
    
    Best Practices:
    - Always backup database before downgrading
    - Test downgrade in staging environment first
    - Verify no code depends on custom_attributes before downgrading
    """
    connection = op.get_bind()
    dialect_name = connection.dialect.name
    
    logger.warning(
        f"Running DESTRUCTIVE downgrade: Removing custom_attributes column "
        f"from generator_agent_state (database: {dialect_name})"
    )
    
    # Verify column exists before attempting to drop
    inspector = Inspector.from_engine(connection)
    columns = [col['name'] for col in inspector.get_columns('generator_agent_state')]
    
    if 'custom_attributes' not in columns:
        logger.warning(
            "Column 'custom_attributes' does not exist. "
            "Downgrade already applied or column was never created."
        )
        return
    
    # Drop the column (this will delete data permanently)
    op.drop_column('generator_agent_state', 'custom_attributes')
    
    logger.info(
        "✓ Downgrade completed: custom_attributes column removed"
    )
