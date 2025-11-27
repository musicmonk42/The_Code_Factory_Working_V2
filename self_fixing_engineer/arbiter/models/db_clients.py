"""
Database client implementations for the Arbiter platform.

This module provides unified database client abstractions including:
- PostgresClient: Production-grade async PostgreSQL client
- SQLiteClient: Lightweight async SQLite client for development/testing
- DummyDBClient: In-memory client for testing without persistence
"""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DummyDBClient:
    """
    In-memory database client for testing and development.
    All data is stored in memory and lost when the instance is garbage collected.
    """

    def __init__(self) -> None:
        self.feedback_entries: List[Dict[str, Any]] = []

    async def save_feedback_entry(self, entry: Dict[str, Any]) -> None:
        entry_copy = entry.copy()
        if "timestamp" not in entry_copy:
            entry_copy["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.feedback_entries.append(entry_copy)
        logger.debug(
            f"DummyDBClient: Saved entry. Total entries: {len(self.feedback_entries)}"
        )

    async def get_feedback_entries(
        self, query: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        if query is None:
            return self.feedback_entries.copy()
        return [
            e
            for e in self.feedback_entries
            if isinstance(e, dict) and all(e.get(k) == v for k, v in query.items())
        ]

    async def update_feedback_entry(
        self, query: Dict[str, Any], updates: Dict[str, Any]
    ) -> bool:
        updated = 0
        for e in self.feedback_entries:
            if isinstance(e, dict) and all(e.get(k) == v for k, v in query.items()):
                e.update(updates)
                updated += 1
        logger.debug(f"DummyDBClient: Updated {updated} entries for query {query}.")
        return updated > 0


class SQLiteClient:
    """
    SQLite database client for lightweight persistent storage.
    Suitable for development and single-instance deployments.
    """

    def __init__(
        self, db_file: str = "feedback.db", timeout: float = 30.0
    ) -> None:
        self.db_file = db_file
        self.timeout = timeout
        self._conn: Optional[sqlite3.Connection] = None
        self._ensure_table()
        logger.info(f"SQLiteClient initialized for database: {self.db_file}")

    def _ensure_table(self) -> None:
        """Create the feedback table if it doesn't exist."""
        conn = sqlite3.connect(self.db_file, timeout=self.timeout)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    type TEXT,
                    decision_id TEXT,
                    data TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    async def connect(self) -> None:
        """Connect to the SQLite database."""
        self._conn = sqlite3.connect(self.db_file, timeout=self.timeout)
        logger.debug(f"SQLiteClient connected to {self.db_file}")

    async def disconnect(self) -> None:
        """Disconnect from the SQLite database."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("SQLiteClient disconnected")

    async def save_feedback_entry(self, entry: Dict[str, Any]) -> None:
        """Save a feedback entry to the database."""
        import json as json_module

        conn = sqlite3.connect(self.db_file, timeout=self.timeout)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO feedback_entries (timestamp, type, decision_id, data)
                VALUES (?, ?, ?, ?)
                """,
                (
                    entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
                    entry.get("type", "unknown"),
                    entry.get("decision_id", ""),
                    json_module.dumps(entry),
                ),
            )
            conn.commit()
            logger.debug(f"SQLiteClient: Saved entry with type={entry.get('type')}")
        finally:
            conn.close()

    async def get_feedback_entries(
        self, query: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve feedback entries, optionally filtered by query."""
        import json as json_module

        conn = sqlite3.connect(self.db_file, timeout=self.timeout)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM feedback_entries")
            rows = cursor.fetchall()
            entries = [json_module.loads(row[0]) for row in rows]

            if query:
                entries = [
                    e
                    for e in entries
                    if all(e.get(k) == v for k, v in query.items())
                ]
            return entries
        finally:
            conn.close()

    async def update_feedback_entry(
        self, query: Dict[str, Any], updates: Dict[str, Any]
    ) -> bool:
        """Update feedback entries matching the query."""
        entries = await self.get_feedback_entries(query)
        if not entries:
            return False

        for entry in entries:
            entry.update(updates)
            await self.save_feedback_entry(entry)
        return True


# Re-export PostgresClient from postgres_client module for convenience
try:
    from arbiter.models.postgres_client import PostgresClient
except ImportError:
    # Provide a stub if the postgres_client module is not available
    class PostgresClient:  # type: ignore
        """Stub PostgresClient when the full implementation is not available."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "PostgresClient requires the arbiter.models.postgres_client module"
            )


__all__ = ["DummyDBClient", "SQLiteClient", "PostgresClient"]
