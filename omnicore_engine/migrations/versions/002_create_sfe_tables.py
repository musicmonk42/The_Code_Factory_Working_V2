# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Create SFE core tables: feedback, agent_knowledge, agent_states, audit_events

Revision ID: 002_create_sfe_tables
Revises: 001_add_custom_attributes
Create Date: 2026-03-01

Background:
-----------
The self-fixing engineer (SFE) uses four core tables that were defined only
inside PostgresClient._TABLE_SCHEMAS (embedded DDL) and never added to the
Alembic migration chain.  This means any deployment that runs
``alembic upgrade head`` independently — Kubernetes init containers, CI/CD
pipelines, manual ``alembic`` invocations — never creates these tables,
producing errors such as:

    Failed to update row count for feedback: relation "feedback" does not exist
    Failed to update row count for agent_knowledge: relation "agent_knowledge" does not exist
    Failed to update row count for agent_states: relation "agent_states" does not exist
    Failed to update row count for audit_events: relation "audit_events" does not exist

This migration adds idempotent CREATE TABLE IF NOT EXISTS statements for all
four tables, matching the schema already defined in PostgresClient._TABLE_SCHEMAS
so that the embedded DDL and the Alembic migration stay in sync.

Tables created:
- feedback: stores user / system feedback records
- agent_knowledge: key-value knowledge store with merkle audit support
- agent_states: persists agent session state as JSONB
- audit_events: append-only audit log with hash chaining

Database Support:
-----------------
- PostgreSQL (primary): uses JSONB and TIMESTAMPTZ
- SQLite (fallback / dev): JSONB / TIMESTAMPTZ columns are accepted by
  SQLite as TEXT affinity, so the CREATE TABLE statements are safe to run
  on both dialects.

The migration is idempotent (CREATE TABLE IF NOT EXISTS) and safe to
re-run on a database where the tables already exist.

Rollback Strategy:
------------------
The downgrade() function drops all four tables.  WARNING: this permanently
destroys stored feedback, agent knowledge, agent states, and audit events.
Only use downgrade in a development environment or after taking a full backup.
"""

import logging

from alembic import op

logger = logging.getLogger(__name__)

# Revision identifiers used by Alembic
revision = "002_create_sfe_tables"
down_revision = "001_add_custom_attributes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create the four SFE core tables if they do not already exist.

    All statements use IF NOT EXISTS so the migration is safe to re-run
    on a database where the tables were already created by the embedded
    DDL in PostgresClient.connect().
    """
    connection = op.get_bind()
    dialect_name = connection.dialect.name

    logger.info(
        "Running migration 002_create_sfe_tables on %s database", dialect_name
    )

    # ------------------------------------------------------------------
    # feedback
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            data JSONB NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_type ON feedback (type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_timestamp ON feedback (timestamp)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_feedback_data ON feedback USING GIN (data)"
    )

    # ------------------------------------------------------------------
    # agent_knowledge
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_knowledge (
            domain TEXT NOT NULL,
            key TEXT NOT NULL,
            value JSONB NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source TEXT,
            user_id TEXT,
            version INTEGER,
            diff JSONB,
            merkle_leaf TEXT,
            merkle_proof JSONB,
            merkle_root TEXT,
            PRIMARY KEY (domain, key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_knowledge_timestamp ON agent_knowledge (timestamp)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_knowledge_domain ON agent_knowledge (domain)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_knowledge_value ON agent_knowledge USING GIN (value)"
    )

    # ------------------------------------------------------------------
    # agent_states
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_states (
            session_id TEXT PRIMARY KEY,
            state JSONB NOT NULL,
            last_updated TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_states_last_updated ON agent_states (last_updated)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_states_state ON agent_states USING GIN (state)"
    )

    # ------------------------------------------------------------------
    # audit_events
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            event_type TEXT NOT NULL,
            details JSONB,
            host TEXT,
            previous_log_hash TEXT,
            hash TEXT UNIQUE,
            signatures JSONB,
            correlation_id TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp ON audit_events (timestamp)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_event_type ON audit_events (event_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_correlation_id ON audit_events (correlation_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_events_details ON audit_events USING GIN (details)"
    )

    logger.info("Migration 002_create_sfe_tables completed successfully")


def downgrade() -> None:
    """
    Drop the four SFE core tables.

    WARNING: This is a destructive operation.  All stored feedback, agent
    knowledge, agent states, and audit events will be permanently deleted.
    Only use this in development or after a full database backup.
    """
    connection = op.get_bind()
    logger.warning(
        "Running DESTRUCTIVE downgrade 002_create_sfe_tables on %s: "
        "dropping feedback, agent_knowledge, agent_states, audit_events",
        connection.dialect.name,
    )

    op.execute("DROP TABLE IF EXISTS audit_events")
    op.execute("DROP TABLE IF EXISTS agent_states")
    op.execute("DROP TABLE IF EXISTS agent_knowledge")
    op.execute("DROP TABLE IF EXISTS feedback")

    logger.info("Downgrade 002_create_sfe_tables completed")
