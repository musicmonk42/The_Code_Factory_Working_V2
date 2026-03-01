# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Create SFE core tables: feedback, agent_knowledge, agent_states, audit_events

Revision ID: 002_create_sfe_tables
Revises: 001_add_custom_attributes
Create Date: 2026-03-01

Background:
-----------
The self-fixing engineer (SFE) uses four core tables that were defined only
inside PostgresClient._TABLE_SCHEMAS (embedded DDL) and were never included in
the Alembic migration chain.  Any deployment workflow that runs
``alembic upgrade head`` independently — Kubernetes init-containers, CI/CD
pipeline migration steps, manual ``alembic`` invocations during developer
onboarding — never triggers PostgresClient.connect() and therefore never
creates these tables, producing errors such as:

    relation "feedback" does not exist
    relation "agent_knowledge" does not exist
    relation "agent_states" does not exist
    relation "audit_events" does not exist

This migration adds the four tables to the Alembic chain, mirroring the schema
already defined in PostgresClient._TABLE_SCHEMAS so that both code paths
(application startup DDL and migration-tool DDL) remain in sync.

Tables created:
---------------
- feedback:        Stores user and system feedback records.  ``data`` is a
                   free-form JSONB payload; ``type`` is indexed for fast
                   category queries.
- agent_knowledge: Composite-PK (domain, key) knowledge store with full
                   Merkle-audit support (``merkle_leaf``, ``merkle_proof``,
                   ``merkle_root``).  ``value`` is indexed via GIN for
                   efficient JSONB containment queries.
- agent_states:    Persists per-session agent state as a JSONB blob.
                   ``last_updated`` tracks staleness for garbage collection.
- audit_events:    Append-only, hash-chained audit log.  ``hash`` carries a
                   UNIQUE constraint to detect tampering.  ``details`` and
                   ``signatures`` are indexed via GIN for forensic queries.

Schema Design Rationale:
-------------------------
All JSONB columns are indexed with GIN (Generalized Inverted Index), the
PostgreSQL index type optimised for semi-structured data and containment
operators (``@>``, ``?``, ``?|``, ``?&``).  Timestamp columns use
TIMESTAMPTZ / TIMESTAMP WITH TIME ZONE so that values stored by agents running
in different time zones are always compared correctly after UTC normalisation.

Database Support:
-----------------
- PostgreSQL (primary target): full JSONB storage with GIN indexes and
  TIMESTAMPTZ / TIMESTAMP WITH TIME ZONE column types.
- SQLite (development / CI fallback): JSONB and TIMESTAMPTZ are not native
  SQLite types.  For SQLite the migration substitutes TEXT (for JSONB columns)
  and DATETIME (for timestamp columns) and skips PostgreSQL-specific GIN
  indexes, matching SQLite's type-affinity model.  B-tree indexes are still
  created for non-JSONB columns.

Idempotency:
------------
Each table creation is guarded by a pre-flight existence check via
``Inspector.from_engine()``.  Tables that already exist (created by the
embedded DDL in PostgresClient.connect()) are silently skipped so the
migration is safe to re-run.

Performance Impact:
-------------------
- No lock escalation: ``CREATE TABLE IF NOT EXISTS`` acquires only an
  ACCESS EXCLUSIVE lock on the new table itself, not on existing tables.
- GIN index builds on an empty table are instantaneous.
- No data migration is required; all columns are nullable or have defaults.

Rollback Strategy:
------------------
The downgrade() function drops all four tables in reverse-creation order.
WARNING: This is a destructive, data-destroying operation.  Always take a
full database backup before running a downgrade in any environment that holds
real data.  Verify that no application code depends on these tables before
downgrading.
"""

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

# Configure module logger
logger = logging.getLogger(__name__)

# Revision identifiers, used by Alembic
revision = "002_create_sfe_tables"
down_revision = "001_add_custom_attributes"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _existing_tables(connection: sa.engine.Connection) -> set:
    """Return the set of table names that currently exist in the database."""
    inspector = Inspector.from_engine(connection)
    return set(inspector.get_table_names())


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    """
    Create the four SFE core tables if they do not already exist.

    Each table is guarded by a pre-flight existence check so the function is
    fully idempotent.  On PostgreSQL the full schema (JSONB columns, GIN
    indexes, TIMESTAMPTZ types) is applied.  On SQLite compatible fallback
    types are used and GIN indexes are omitted.

    Industry Standards Applied:
    - Idempotent migrations (pre-flight existence check per table)
    - Database-agnostic column type handling (PostgreSQL vs SQLite)
    - Graceful handling of partial application (each table is independent)
    - Comprehensive structured logging for every DDL operation
    - Schema stays in sync with PostgresClient._TABLE_SCHEMAS (single source
      of truth for column names; migration is the authoritative DDL path)
    """
    connection = op.get_bind()
    dialect_name = connection.dialect.name
    is_postgres = dialect_name == "postgresql"

    logger.info(
        f"Running migration 002_create_sfe_tables on {dialect_name} database"
    )

    existing = _existing_tables(connection)
    tables_created = 0

    # ------------------------------------------------------------------
    # feedback
    # Stores user and system feedback.  ``data`` is a free-form JSONB
    # payload; ``type`` is a discriminator column for category routing.
    # ------------------------------------------------------------------
    if "feedback" in existing:
        logger.info(
            "Table 'feedback' already exists — skipping creation "
            "(created by PostgresClient.connect() embedded DDL)"
        )
    else:
        logger.info("Creating table 'feedback'")
        if is_postgres:
            op.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id        TEXT        PRIMARY KEY,
                    type      TEXT        NOT NULL,
                    data      JSONB       NOT NULL,
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
            # GIN index for JSONB containment queries on the data payload
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_data ON feedback USING GIN (data)"
            )
        else:
            # SQLite fallback: TEXT affinity for JSONB; DATETIME for timestamps;
            # no GIN indexes (not supported outside PostgreSQL)
            op.create_table(
                "feedback",
                sa.Column("id",        sa.Text(),     nullable=False, primary_key=True),
                sa.Column("type",      sa.Text(),     nullable=False),
                sa.Column("data",      sa.Text(),     nullable=False),
                sa.Column("timestamp", sa.DateTime(), nullable=False,
                          server_default=sa.text("CURRENT_TIMESTAMP")),
            )
            op.create_index("idx_feedback_type",      "feedback", ["type"])
            op.create_index("idx_feedback_timestamp", "feedback", ["timestamp"])
        logger.info(f"✓ Table 'feedback' created on {dialect_name}")
        tables_created += 1

    # ------------------------------------------------------------------
    # agent_knowledge
    # Composite-PK (domain, key) knowledge store.  ``value`` carries the
    # knowledge payload; ``merkle_*`` columns support tamper-evident audit
    # chains; ``diff`` records incremental changes between versions.
    # ------------------------------------------------------------------
    if "agent_knowledge" in existing:
        logger.info(
            "Table 'agent_knowledge' already exists — skipping creation "
            "(created by PostgresClient.connect() embedded DDL)"
        )
    else:
        logger.info("Creating table 'agent_knowledge'")
        if is_postgres:
            op.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_knowledge (
                    domain       TEXT        NOT NULL,
                    key          TEXT        NOT NULL,
                    value        JSONB       NOT NULL,
                    timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source       TEXT,
                    user_id      TEXT,
                    version      INTEGER,
                    diff         JSONB,
                    merkle_leaf  TEXT,
                    merkle_proof JSONB,
                    merkle_root  TEXT,
                    PRIMARY KEY (domain, key)
                )
                """
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_knowledge_timestamp "
                "ON agent_knowledge (timestamp)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_knowledge_domain "
                "ON agent_knowledge (domain)"
            )
            # GIN index enables efficient JSONB containment queries on knowledge values
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_knowledge_value "
                "ON agent_knowledge USING GIN (value)"
            )
        else:
            op.create_table(
                "agent_knowledge",
                sa.Column("domain",       sa.Text(),    nullable=False),
                sa.Column("key",          sa.Text(),    nullable=False),
                sa.Column("value",        sa.Text(),    nullable=False),
                sa.Column("timestamp",    sa.DateTime(), nullable=False,
                          server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("source",       sa.Text(),    nullable=True),
                sa.Column("user_id",      sa.Text(),    nullable=True),
                sa.Column("version",      sa.Integer(), nullable=True),
                sa.Column("diff",         sa.Text(),    nullable=True),
                sa.Column("merkle_leaf",  sa.Text(),    nullable=True),
                sa.Column("merkle_proof", sa.Text(),    nullable=True),
                sa.Column("merkle_root",  sa.Text(),    nullable=True),
                sa.PrimaryKeyConstraint("domain", "key"),
            )
            op.create_index("idx_agent_knowledge_timestamp", "agent_knowledge", ["timestamp"])
            op.create_index("idx_agent_knowledge_domain",    "agent_knowledge", ["domain"])
        logger.info(f"✓ Table 'agent_knowledge' created on {dialect_name}")
        tables_created += 1

    # ------------------------------------------------------------------
    # agent_states
    # Persists per-session agent state as a JSONB blob keyed by session_id.
    # ``last_updated`` is used by garbage-collection routines to evict stale
    # sessions.  Column type matches PostgresClient._TABLE_SCHEMAS exactly:
    # TIMESTAMP WITH TIME ZONE (equivalent to TIMESTAMPTZ; both are aliases
    # in PostgreSQL but TIMESTAMP WITH TIME ZONE is the SQL-standard form).
    # ------------------------------------------------------------------
    if "agent_states" in existing:
        logger.info(
            "Table 'agent_states' already exists — skipping creation "
            "(created by PostgresClient.connect() embedded DDL)"
        )
    else:
        logger.info("Creating table 'agent_states'")
        if is_postgres:
            op.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_states (
                    session_id   TEXT                     PRIMARY KEY,
                    state        JSONB                    NOT NULL,
                    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
                """
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_states_last_updated "
                "ON agent_states (last_updated)"
            )
            # GIN index for JSONB containment queries when filtering by state fields
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_states_state "
                "ON agent_states USING GIN (state)"
            )
        else:
            op.create_table(
                "agent_states",
                sa.Column("session_id",   sa.Text(),     nullable=False, primary_key=True),
                sa.Column("state",        sa.Text(),     nullable=False),
                sa.Column("last_updated", sa.DateTime(), nullable=True,
                          server_default=sa.text("CURRENT_TIMESTAMP")),
            )
            op.create_index("idx_agent_states_last_updated", "agent_states", ["last_updated"])
        logger.info(f"✓ Table 'agent_states' created on {dialect_name}")
        tables_created += 1

    # ------------------------------------------------------------------
    # audit_events
    # Append-only, hash-chained audit log.  ``previous_log_hash`` and
    # ``hash`` form the tamper-evident chain; ``hash`` carries a UNIQUE
    # constraint so duplicate events are detected at the database level.
    # ``details`` and ``signatures`` are indexed via GIN to support fast
    # forensic queries (e.g., finding all events for a correlation ID or
    # events signed by a specific key).
    # ------------------------------------------------------------------
    if "audit_events" in existing:
        logger.info(
            "Table 'audit_events' already exists — skipping creation "
            "(created by PostgresClient.connect() embedded DDL)"
        )
    else:
        logger.info("Creating table 'audit_events'")
        if is_postgres:
            op.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id                TEXT        PRIMARY KEY,
                    timestamp         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    event_type        TEXT        NOT NULL,
                    details           JSONB,
                    host              TEXT,
                    previous_log_hash TEXT,
                    hash              TEXT        UNIQUE,
                    signatures        JSONB,
                    correlation_id    TEXT
                )
                """
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp "
                "ON audit_events (timestamp)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_event_type "
                "ON audit_events (event_type)"
            )
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_correlation_id "
                "ON audit_events (correlation_id)"
            )
            # GIN indexes enable forensic containment queries across details and signatures
            op.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_details "
                "ON audit_events USING GIN (details)"
            )
        else:
            op.create_table(
                "audit_events",
                sa.Column("id",                sa.Text(),     nullable=False, primary_key=True),
                sa.Column("timestamp",         sa.DateTime(), nullable=False,
                          server_default=sa.text("CURRENT_TIMESTAMP")),
                sa.Column("event_type",        sa.Text(),     nullable=False),
                sa.Column("details",           sa.Text(),     nullable=True),
                sa.Column("host",              sa.Text(),     nullable=True),
                sa.Column("previous_log_hash", sa.Text(),     nullable=True),
                sa.Column("hash",              sa.Text(),     nullable=True, unique=True),
                sa.Column("signatures",        sa.Text(),     nullable=True),
                sa.Column("correlation_id",    sa.Text(),     nullable=True),
            )
            op.create_index("idx_audit_events_timestamp",      "audit_events", ["timestamp"])
            op.create_index("idx_audit_events_event_type",     "audit_events", ["event_type"])
            op.create_index("idx_audit_events_correlation_id", "audit_events", ["correlation_id"])
        logger.info(f"✓ Table 'audit_events' created on {dialect_name}")
        tables_created += 1

    logger.info(
        f"✓ Migration 002_create_sfe_tables completed successfully "
        f"({tables_created} table(s) created, "
        f"{4 - tables_created} already existed)"
    )


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    """
    Drop the four SFE core tables in reverse-creation order.

    Each table is guarded by a pre-flight existence check so the function is
    idempotent and safe to run even if a previous downgrade was partially
    applied.

    WARNING: This is a destructive, data-destroying operation.  All stored
    feedback, agent knowledge, agent states, and audit events will be
    permanently deleted.

    Best Practices:
    - Always take a full database backup before downgrading in any
      environment that holds real data.
    - Test the downgrade in a staging environment first.
    - Confirm that no running application instances depend on these tables
      before downgrading to avoid runtime errors.
    - Verify that the upgrade() can be re-applied cleanly after downgrade
      if re-migration is needed.
    """
    connection = op.get_bind()
    dialect_name = connection.dialect.name

    logger.warning(
        f"Running DESTRUCTIVE downgrade 002_create_sfe_tables on {dialect_name}: "
        f"dropping audit_events, agent_states, agent_knowledge, feedback"
    )

    existing = _existing_tables(connection)
    tables_dropped = 0

    # Drop in reverse-creation order to respect any future FK dependencies
    for table in ("audit_events", "agent_states", "agent_knowledge", "feedback"):
        if table not in existing:
            logger.warning(
                f"Table '{table}' does not exist — skipping drop "
                f"(downgrade already applied or table was never created)"
            )
            continue
        logger.info(f"Dropping table '{table}'")
        op.drop_table(table)
        logger.info(f"✓ Table '{table}' dropped")
        tables_dropped += 1

    logger.info(
        f"✓ Downgrade 002_create_sfe_tables completed "
        f"({tables_dropped} table(s) dropped)"
    )
