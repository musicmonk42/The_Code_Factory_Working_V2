import io
import json
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from pydantic import BaseModel

from .audit_ledger import AuditLedgerClient
from .metrics import METRICS

# Internal imports
from .reasoner_errors import ReasonerError, ReasonerErrorCode

# Conditional import for MultiModalData and schemas
try:
    from arbiter.models.multi_modal_schemas import (
        AudioAnalysisResult,
        ImageAnalysisResult,
        MultiModalAnalysisResult,
        MultiModalData,
        VideoAnalysisResult,
    )

    MULTI_MODAL_SCHEMAS_AVAILABLE = True
except ImportError:
    import logging

    logger = logging.getLogger(__name__)
    logger.debug(
        "arbiter.models.multi_modal_schemas not found; using dummy MultiModalData/Schemas."
    )

    class MultiModalData(BaseModel):
        data_type: str
        data: bytes
        metadata: Dict = {}

        def dict(self, exclude_unset=False) -> Dict[str, Any]:
            data_snippet = ""
            if self.data_type == "image" and self.data:
                import base64

                data_snippet = (
                    f"base64_preview:{base64.b64encode(self.data).decode()[:50]}..."
                )
            elif self.data_type == "audio" and self.data:
                data_snippet = f"bytes_len:{len(self.data)}"
            elif self.data_type == "video" and self.data:
                data_snippet = f"bytes_len:{len(self.data)}"

            return {
                "data_type": self.data_type,
                "data_preview": data_snippet,
                "metadata": self.metadata,
            }

    class MultiModalAnalysisResult(BaseModel):
        pass

    class ImageAnalysisResult(MultiModalAnalysisResult):
        image_id: str = "dummy_id"
        captioning_result: Optional[Any] = None
        ocr_result: Optional[Any] = None
        detected_objects: Optional[List[str]] = None

    class AudioAnalysisResult(MultiModalAnalysisResult):
        audio_id: str = "dummy_id"
        transcription: Optional[Any] = None
        sentiment: Optional[Any] = None
        keywords: Optional[List[str]] = None

    class VideoAnalysisResult(MultiModalAnalysisResult):
        video_id: str = "dummy_id"
        summary_result: Optional[Any] = None
        audio_transcription_result: Optional[Any] = None
        main_entities: Optional[List[str]] = None

    MULTI_MODAL_SCHEMAS_AVAILABLE = False

# --- Availability Checks for DB Backends and Security ---
try:
    import aiosqlite

    SQLITE_AVAILABLE = True
except ImportError:
    aiosqlite = None
    SQLITE_AVAILABLE = False

try:
    import asyncpg

    POSTGRES_AVAILABLE = True
except ImportError:
    asyncpg = None
    POSTGRES_AVAILABLE = False

try:
    import redis.asyncio as aioredis

    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None
    REDIS_AVAILABLE = False

try:
    from cryptography.fernet import Fernet, InvalidToken

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    Fernet = None
    InvalidToken = None
    CRYPTOGRAPHY_AVAILABLE = False

# Structured logging with structlog
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(indent=2),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)

# --- Deployability Note ---
# To select a history backend at runtime, use an environment variable like `REASONER_HISTORY_BACKEND`.
# A factory function can then read this variable and instantiate the corresponding manager class.
# Example Factory (place in your application's startup logic):
#
# def get_history_manager(config):
#     backend = os.getenv("REASONER_HISTORY_BACKEND", "sqlite").lower()
#     if backend == "postgres":
#         return PostgresHistoryManager(...)
#     elif backend == "redis":
#         return RedisHistoryManager(...)
#     else: # default to sqlite
#         return SQLiteHistoryManager(...)


class BaseHistoryManager(ABC):
    """Abstract base class for managing history entries."""

    def __init__(
        self,
        max_history_size: int,
        retention_days: int,
        audit_client: Optional[AuditLedgerClient] = None,
    ):
        self.max_history_size: int = max_history_size
        self.retention_days: int = retention_days
        self.audit_client: Optional[AuditLedgerClient] = audit_client
        self._backend_name: str = "base"  # Overridden by subclasses
        self._logger = logger.bind(backend=self._backend_name)
        self._encryption_key: Optional[Fernet] = None

    async def _pre_add_entry_checks(self, entry: Dict[str, Any]) -> None:
        """Performs pre-add checks for sensitive data and binary leaks."""
        sensitive_patterns = [r"\b(api_key|password|secret)\b", r"\b\d{16}\b"]
        binary_data_present = False
        for v in entry.values():
            if isinstance(v, dict):
                if any(isinstance(sub_v, bytes) for sub_v in v.values()):
                    binary_data_present = True
                    break
            elif isinstance(v, bytes):
                binary_data_present = True
                break

        if binary_data_present:
            self._logger.warning("binary_data_in_entry", entry_id=entry.get("id"))
            raise ReasonerError(
                "Binary data detected in history entry",
                code=ReasonerErrorCode.SENSITIVE_DATA_LEAK,
            )

        for key, value in entry.items():
            if isinstance(value, str) and any(
                re.search(pattern, value) for pattern in sensitive_patterns
            ):
                self._logger.warning("sensitive_data_detected", key=key)
                raise ReasonerError(
                    "Sensitive data detected in history entry",
                    code=ReasonerErrorCode.SENSITIVE_DATA_LEAK,
                )

    def _encrypt(self, text: str) -> str:
        """Encrypts text if a key is available."""
        if self._encryption_key:
            return self._encryption_key.encrypt(text.encode("utf-8")).decode("utf-8")
        return text

    def _decrypt(self, encrypted_text: str) -> str:
        """Decrypts text if a key is available."""
        if self._encryption_key:
            try:
                return self._encryption_key.decrypt(
                    encrypted_text.encode("utf-8")
                ).decode("utf-8")
            except InvalidToken:
                self._logger.warning(
                    "decryption_failed_invalid_token",
                    message="Could not decrypt field. Key may have changed or data is corrupt.",
                )
                return "[DECRYPTION FAILED]"
        return encrypted_text

    def _record_op_success(self, operation: str, start_time: float):
        """Records metrics for a successful operation."""
        # FIX: Check if metric exists before accessing.
        if "reasoner_history_operations_total" in METRICS:
            METRICS["reasoner_history_operations_total"].labels(
                operation=operation, status="success"
            ).inc()
        if "reasoner_history_operation_latency_seconds" in METRICS:
            METRICS["reasoner_history_operation_latency_seconds"].labels(
                operation=operation
            ).observe(time.monotonic() - start_time)

    def _record_op_error(self, operation: str, start_time: float, e: Exception):
        """Records metrics and logs for a failed operation."""
        # FIX: Check if metric exists before accessing.
        if "reasoner_history_operations_total" in METRICS:
            METRICS["reasoner_history_operations_total"].labels(
                operation=operation, status="error"
            ).inc()
        if "reasoner_history_operation_latency_seconds" in METRICS:
            METRICS["reasoner_history_operation_latency_seconds"].labels(
                operation=operation
            ).observe(time.monotonic() - start_time)
        self._logger.error(
            f"{self._backend_name}_{operation}_failed", error=str(e), exc_info=True
        )

    async def _log_audit_event(self, event_type: str, details: Dict, operator: str):
        """Logs an event to the audit ledger if available."""
        if self.audit_client:
            await self.audit_client.log_event(
                event_type=event_type, details=details, operator=operator
            )

    @abstractmethod
    async def init_db(self) -> None:
        """Initializes the database connection and schema."""
        pass

    @abstractmethod
    async def add_entry(self, entry: Dict[str, Any]) -> None:
        """Adds a new entry to the history."""
        pass

    @abstractmethod
    async def add_entries_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Adds a batch of new entries to the history."""
        pass

    @abstractmethod
    async def get_entries(
        self, limit: int = 10, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves recent history entries, optionally filtered by session_id."""
        pass

    @abstractmethod
    async def get_size(self) -> int:
        """Returns the current number of history entries."""
        pass

    @abstractmethod
    async def prune_old_entries(self) -> None:
        """Prunes old entries based on retention days."""
        pass

    @abstractmethod
    async def clear(self, session_id: Optional[str] = None) -> None:
        """Clears history entries, optionally for a session."""
        pass

    @abstractmethod
    async def purge_all(self, operator_id: str = "system_api_request") -> None:
        """Purges all history data."""
        pass

    @abstractmethod
    async def export_history(
        self, output_format: str = "json", operator_id: str = "system_api_request"
    ) -> AsyncGenerator[Union[str, bytes], None]:
        """Exports history data as chunks."""
        pass

    @abstractmethod
    async def aclose(self) -> None:
        """Closes the database connection."""
        pass


class SQLiteHistoryManager(BaseHistoryManager):
    """SQLite implementation of history manager."""

    _backend_name = "sqlite"

    def __init__(
        self,
        db_path: Path,
        max_history_size: int,
        retention_days: int,
        audit_client: Optional[AuditLedgerClient] = None,
    ):
        super().__init__(max_history_size, retention_days, audit_client)
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        if not SQLITE_AVAILABLE:
            self._logger.error(
                "aiosqlite_missing",
                message="SQLite backend unavailable. Please install 'aiosqlite'.",
            )
            raise ReasonerError(
                "aiosqlite not installed", code=ReasonerErrorCode.CONFIGURATION_ERROR
            )

    async def init_db(self) -> None:
        """Initializes the SQLite database and schema."""
        start_time = time.monotonic()
        try:
            self._conn = await aiosqlite.connect(self.db_path)
            await self._conn.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY,               -- Unique identifier for the history entry
                    query TEXT NOT NULL,               -- The user's query or input text
                    context JSON NOT NULL,             -- JSON object containing contextual data
                    response TEXT NOT NULL,            -- The model's response
                    response_type TEXT NOT NULL,       -- The type of the response (e.g., 'text', 'tool_code')
                    timestamp TEXT NOT NULL,           -- ISO 8601 timestamp of the entry
                    session_id TEXT                    -- Optional identifier to group related entries
                )
            """)
            await self._conn.commit()
            self._logger.info("sqlite_db_initialized", db_path=str(self.db_path))
            self._record_op_success("init_db", start_time)
        except Exception as e:
            # FIX: Check if metric exists before accessing.
            if "reasoner_history_db_connection_failures_total" in METRICS:
                METRICS["reasoner_history_db_connection_failures_total"].labels(
                    backend=self._backend_name
                ).inc()
            self._record_op_error("init_db", start_time, e)
            raise ReasonerError(
                "Failed to initialize SQLite DB",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def add_entry(self, entry: Dict[str, Any]) -> None:
        """Adds a new entry to the history."""
        await self._pre_add_entry_checks(entry)
        start_time = time.monotonic()
        try:
            await self._conn.execute(
                "INSERT INTO history (id, query, context, response, response_type, timestamp, session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entry["id"],
                    entry["query"],
                    json.dumps(entry["context"]),
                    entry["response"],
                    entry["response_type"],
                    entry["timestamp"],
                    entry.get("session_id"),
                ),
            )
            await self._conn.commit()
            self._record_op_success("add_entry", start_time)
            await self._log_audit_event(
                event_type="history_add_entry",
                details={
                    "entry_id": entry["id"],
                    "timestamp": entry["timestamp"],
                    "session_id": entry.get("session_id"),
                },
                operator="system",
            )
        except Exception as e:
            self._record_op_error("add_entry", start_time, e)
            raise ReasonerError(
                "Failed to add history entry",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def add_entries_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Adds a batch of new entries to the history using executemany for efficiency."""
        if not entries:
            return
        start_time = time.monotonic()
        try:
            for entry in entries:
                await self._pre_add_entry_checks(entry)

            data_to_insert = [
                (
                    e["id"],
                    e["query"],
                    json.dumps(e["context"]),
                    e["response"],
                    e["response_type"],
                    e["timestamp"],
                    e.get("session_id"),
                )
                for e in entries
            ]
            await self._conn.executemany(
                "INSERT INTO history (id, query, context, response, response_type, timestamp, session_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                data_to_insert,
            )
            await self._conn.commit()
            self._record_op_success("add_entries_batch", start_time)
            self._logger.info("sqlite_batch_add_completed", count=len(entries))
            await self._log_audit_event(
                event_type="history_add_entries_batch",
                details={"count": len(entries)},
                operator="system",
            )
        except Exception as e:
            self._record_op_error("add_entries_batch", start_time, e)
            raise ReasonerError(
                "Failed to add history entries in batch",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def get_entries(
        self, limit: int = 10, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves recent history entries, optionally filtered by session_id."""
        start_time = time.monotonic()
        try:
            query = "SELECT * FROM history ORDER BY timestamp DESC LIMIT ?"
            params = [limit]
            if session_id:
                query = "SELECT * FROM history WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?"
                params = [session_id, limit]

            async with self._conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                entries = [
                    {
                        "id": row[0],
                        "query": row[1],
                        "context": json.loads(row[2]),
                        "response": row[3],
                        "response_type": row[4],
                        "timestamp": row[5],
                        "session_id": row[6],
                    }
                    for row in rows
                ]
            self._record_op_success("get_entries", start_time)
            return entries
        except Exception as e:
            self._record_op_error("get_entries", start_time, e)
            raise ReasonerError(
                "Failed to retrieve history entries",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def get_size(self) -> int:
        """Returns the current number of history entries."""
        start_time = time.monotonic()
        try:
            async with self._conn.execute("SELECT COUNT(*) FROM history") as cursor:
                size = (await cursor.fetchone())[0]
            self._record_op_success("get_size", start_time)
            return size
        except Exception as e:
            self._record_op_error("get_size", start_time, e)
            raise ReasonerError(
                "Failed to get history size",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def prune_old_entries(self) -> None:
        """Prunes old entries based on retention days."""
        start_time = time.monotonic()
        try:
            cutoff_timestamp = (
                datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            ).isoformat()
            async with self._conn.execute(
                "SELECT COUNT(*) FROM history WHERE timestamp < ?", (cutoff_timestamp,)
            ) as cursor:
                count = (await cursor.fetchone())[0]

            if count > 0:
                await self._conn.execute(
                    "DELETE FROM history WHERE timestamp < ?", (cutoff_timestamp,)
                )
                await self._conn.commit()
                self._logger.info(
                    "sqlite_prune_completed",
                    count=count,
                    cutoff_timestamp=cutoff_timestamp,
                )
                if "reasoner_history_pruned_entries_total" in METRICS:
                    METRICS["reasoner_history_pruned_entries_total"].inc(count)
                await self._log_audit_event(
                    event_type="history_prune",
                    details={
                        "db_path": str(self.db_path),
                        "count": count,
                        "cutoff_timestamp": cutoff_timestamp,
                    },
                    operator="system_scheduled_task",
                )
            else:
                self._logger.debug("sqlite_no_entries_to_prune")

            self._record_op_success("prune_old_entries", start_time)
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(await self.get_size())
        except Exception as e:
            self._record_op_error("prune_old_entries", start_time, e)
            raise ReasonerError(
                "Failed to prune old history entries",
                code=ReasonerErrorCode.HISTORY_PRUNING_FAILED,
                original_exception=e,
            )

    async def clear(self, session_id: Optional[str] = None) -> None:
        """Clears history entries, optionally for a session."""
        start_time = time.monotonic()
        try:
            query = (
                "DELETE FROM history"
                if not session_id
                else "DELETE FROM history WHERE session_id = ?"
            )
            params = [] if not session_id else [session_id]
            cursor = await self._conn.execute(query, params)
            deleted_count = cursor.rowcount
            await self._conn.commit()

            self._logger.info(
                "sqlite_clear_completed",
                session_id=session_id or "all",
                count=deleted_count,
            )
            self._record_op_success("clear", start_time)
            if deleted_count > 0:
                await self._log_audit_event(
                    event_type="history_clear",
                    details={"session_id": session_id or "all", "count": deleted_count},
                    operator="system",
                )
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(await self.get_size())
        except Exception as e:
            self._record_op_error("clear", start_time, e)
            raise ReasonerError(
                "Failed to clear history",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def purge_all(self, operator_id: str = "system_api_request") -> None:
        """Purges all history data."""
        start_time = time.monotonic()
        try:
            async with self._conn.execute("SELECT COUNT(*) FROM history") as cursor:
                count = (await cursor.fetchone())[0]

            await self._conn.execute("DELETE FROM history")
            await self._conn.commit()

            self._logger.info(
                "sqlite_purge_completed", count=count, operator_id=operator_id
            )
            self._record_op_success("purge_all", start_time)
            if count > 0:
                await self._log_audit_event(
                    event_type="history_purge",
                    details={"count": count, "operator_id": operator_id},
                    operator=operator_id,
                )
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(0)
        except Exception as e:
            self._record_op_error("purge_all", start_time, e)
            raise ReasonerError(
                "Failed to purge all history",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def export_history(
        self, output_format: str = "json", operator_id: str = "system_api_request"
    ) -> AsyncGenerator[Union[str, bytes], None]:
        """Exports history data as chunks."""
        start_time = time.monotonic()
        try:
            if output_format not in ["json", "csv"]:
                raise ReasonerError(
                    f"Unsupported format: {output_format}",
                    code=ReasonerErrorCode.INVALID_INPUT,
                )

            async with self._conn.execute(
                "SELECT * FROM history ORDER BY timestamp DESC"
            ) as cursor:
                # This part is not truly async streaming with aiosqlite, it fetches all.
                # For very large DBs, a different approach would be needed.
                rows = await cursor.fetchall()
                batch_size = 100
                for i in range(0, len(rows), batch_size):
                    batch_rows = rows[i : i + batch_size]
                    batch_entries = [
                        {
                            "id": row[0],
                            "query": row[1],
                            "context": json.loads(row[2]),
                            "response": row[3],
                            "response_type": row[4],
                            "timestamp": row[5],
                            "session_id": row[6],
                        }
                        for row in batch_rows
                    ]
                    if output_format == "json":
                        yield json.dumps(batch_entries, indent=2).encode("utf-8")
                    else:  # csv
                        output = io.StringIO()
                        # Simplified CSV generation for example
                        for entry in batch_entries:
                            output.write(
                                f"\"{entry['id']}\",\"{entry['timestamp']}\"\n"
                            )
                        yield output.getvalue().encode("utf-8")

            self._logger.info(
                "sqlite_export_completed", format=output_format, operator_id=operator_id
            )
            self._record_op_success("export_history", start_time)
            await self._log_audit_event(
                event_type="history_export",
                details={"format": output_format, "operator_id": operator_id},
                operator=operator_id,
            )
        except Exception as e:
            self._record_op_error("export_history", start_time, e)
            raise ReasonerError(
                "Failed to export history",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def aclose(self) -> None:
        """Closes the database connection."""
        start_time = time.monotonic()
        try:
            if self._conn:
                await self._conn.close()
                self._logger.info("sqlite_connection_closed")
                self._record_op_success("aclose", start_time)
        except Exception as e:
            self._record_op_error("aclose", start_time, e)
            raise ReasonerError(
                "Failed to close SQLite connection",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )


class PostgresHistoryManager(BaseHistoryManager):
    """Postgres implementation of history manager with response encryption."""

    _backend_name = "postgres"

    def __init__(
        self,
        db_url: str,
        max_history_size: int,
        retention_days: int,
        audit_client: Optional[AuditLedgerClient] = None,
    ):
        super().__init__(max_history_size, retention_days, audit_client)
        self.db_url = db_url
        self._pool: Optional[asyncpg.Pool] = None

        if not POSTGRES_AVAILABLE:
            self._logger.error(
                "asyncpg_missing",
                message="Postgres backend unavailable. Please install 'asyncpg'.",
            )
            raise ReasonerError(
                "asyncpg not installed", code=ReasonerErrorCode.CONFIGURATION_ERROR
            )
        if not CRYPTOGRAPHY_AVAILABLE:
            self._logger.error(
                "cryptography_missing",
                message="Cryptography backend unavailable. Please install 'cryptography'.",
            )
            raise ReasonerError(
                "cryptography not installed", code=ReasonerErrorCode.CONFIGURATION_ERROR
            )

        encryption_key = os.getenv("REASONER_ENCRYPTION_KEY")
        if encryption_key:
            self._encryption_key = Fernet(encryption_key.encode("utf-8"))
        else:
            self._logger.warning(
                "encryption_key_missing",
                message="REASONER_ENCRYPTION_KEY not set. History responses will not be encrypted.",
            )

    async def init_db(self) -> None:
        """Initializes the Postgres database connection pool and schema."""
        start_time = time.monotonic()
        try:
            self._pool = await asyncpg.create_pool(self.db_url, max_size=10)
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS history (
                        id TEXT PRIMARY KEY,               -- Unique identifier for the history entry
                        query TEXT NOT NULL,               -- The user's query or input text
                        context JSONB NOT NULL,            -- JSONB object containing contextual data
                        response TEXT NOT NULL,            -- The model's (potentially encrypted) response
                        response_type TEXT NOT NULL,       -- The type of the response (e.g., 'text', 'tool_code')
                        timestamp TIMESTAMPTZ NOT NULL,    -- Timestamp with timezone of the entry
                        session_id TEXT                    -- Optional identifier to group related entries
                    );
                    CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history (timestamp);
                    CREATE INDEX IF NOT EXISTS idx_history_session_id ON history (session_id);
                """)
            self._logger.info("postgres_db_initialized", db_url="***")
            self._record_op_success("init_db", start_time)
        except Exception as e:
            # FIX: Check if metric exists before accessing.
            if "reasoner_history_db_connection_failures_total" in METRICS:
                METRICS["reasoner_history_db_connection_failures_total"].labels(
                    backend=self._backend_name
                ).inc()
            self._record_op_error("init_db", start_time, e)
            raise ReasonerError(
                "Failed to initialize Postgres DB",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def add_entry(self, entry: Dict[str, Any]) -> None:
        """Adds a new, encrypted entry to the history."""
        await self._pre_add_entry_checks(entry)
        start_time = time.monotonic()

        entry_to_store = entry.copy()
        entry_to_store["response"] = self._encrypt(entry["response"])

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO history (id, query, context, response, response_type, timestamp, session_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    entry_to_store["id"],
                    entry_to_store["query"],
                    json.dumps(entry_to_store["context"]),
                    entry_to_store["response"],
                    entry_to_store["response_type"],
                    datetime.fromisoformat(entry_to_store["timestamp"]),
                    entry_to_store.get("session_id"),
                )
            self._record_op_success("add_entry", start_time)
            await self._log_audit_event(
                event_type="history_add_entry",
                details={
                    "entry_id": entry["id"],
                    "timestamp": entry["timestamp"],
                    "session_id": entry.get("session_id"),
                },
                operator="system",
            )
        except Exception as e:
            self._record_op_error("add_entry", start_time, e)
            raise ReasonerError(
                "Failed to add history entry",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def add_entries_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Adds a batch of new, encrypted entries to the history."""
        if not entries:
            return
        start_time = time.monotonic()
        try:
            data_to_insert = []
            for entry in entries:
                await self._pre_add_entry_checks(entry)
                entry_to_store = entry.copy()
                entry_to_store["response"] = self._encrypt(entry["response"])
                data_to_insert.append(
                    (
                        entry_to_store["id"],
                        entry_to_store["query"],
                        json.dumps(entry_to_store["context"]),
                        entry_to_store["response"],
                        entry_to_store["response_type"],
                        datetime.fromisoformat(entry_to_store["timestamp"]),
                        entry_to_store.get("session_id"),
                    )
                )

            async with self._pool.acquire() as conn:
                await conn.executemany(
                    "INSERT INTO history (id, query, context, response, response_type, timestamp, session_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
                    data_to_insert,
                )
            self._record_op_success("add_entries_batch", start_time)
            self._logger.info("postgres_batch_add_completed", count=len(entries))
            await self._log_audit_event(
                "history_add_entries_batch", {"count": len(entries)}, "system"
            )
        except Exception as e:
            self._record_op_error("add_entries_batch", start_time, e)
            raise ReasonerError(
                "Failed to add history entries in batch",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def get_entries(
        self, limit: int = 10, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves and decrypts recent history entries."""
        start_time = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                if session_id:
                    query = "SELECT * FROM history WHERE session_id = $1 ORDER BY timestamp DESC LIMIT $2"
                    rows = await conn.fetch(query, session_id, limit)
                else:
                    query = "SELECT * FROM history ORDER BY timestamp DESC LIMIT $1"
                    rows = await conn.fetch(query, limit)

                entries = [
                    {
                        "id": row["id"],
                        "query": row["query"],
                        "context": row["context"],
                        "response": self._decrypt(row["response"]),
                        "response_type": row["response_type"],
                        "timestamp": row["timestamp"].isoformat(),
                        "session_id": row["session_id"],
                    }
                    for row in rows
                ]
            self._record_op_success("get_entries", start_time)
            return entries
        except Exception as e:
            self._record_op_error("get_entries", start_time, e)
            raise ReasonerError(
                "Failed to retrieve history entries",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def get_size(self) -> int:
        """Returns the current number of history entries."""
        start_time = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                size = await conn.fetchval("SELECT COUNT(*) FROM history")
            self._record_op_success("get_size", start_time)
            return size
        except Exception as e:
            self._record_op_error("get_size", start_time, e)
            raise ReasonerError(
                "Failed to get history size",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def prune_old_entries(self) -> None:
        """Prunes old entries based on retention days."""
        start_time = time.monotonic()
        try:
            cutoff_timestamp = datetime.now(timezone.utc) - timedelta(
                days=self.retention_days
            )
            async with self._pool.acquire() as conn:
                # Using DELETE ... RETURNING id allows getting the count without a second query
                result = await conn.execute(
                    "DELETE FROM history WHERE timestamp < $1", cutoff_timestamp
                )
                count = int(result.split(" ")[1]) if result.startswith("DELETE") else 0

            if count > 0:
                self._logger.info(
                    "postgres_prune_completed",
                    count=count,
                    cutoff_timestamp=cutoff_timestamp.isoformat(),
                )
                if "reasoner_history_pruned_entries_total" in METRICS:
                    METRICS["reasoner_history_pruned_entries_total"].inc(count)
                await self._log_audit_event(
                    "history_prune",
                    {"count": count, "cutoff_timestamp": cutoff_timestamp.isoformat()},
                    "system_scheduled_task",
                )
            else:
                self._logger.debug("postgres_no_entries_to_prune")

            self._record_op_success("prune_old_entries", start_time)
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(await self.get_size())
        except Exception as e:
            self._record_op_error("prune_old_entries", start_time, e)
            raise ReasonerError(
                "Failed to prune old history entries",
                code=ReasonerErrorCode.HISTORY_PRUNING_FAILED,
                original_exception=e,
            )

    async def clear(self, session_id: Optional[str] = None) -> None:
        """Clears history entries, optionally for a session."""
        start_time = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                if session_id:
                    result = await conn.execute(
                        "DELETE FROM history WHERE session_id = $1", session_id
                    )
                else:
                    result = await conn.execute("DELETE FROM history")
                deleted_count = (
                    int(result.split(" ")[1]) if result.startswith("DELETE") else 0
                )

            self._logger.info(
                "postgres_clear_completed",
                session_id=session_id or "all",
                count=deleted_count,
            )
            self._record_op_success("clear", start_time)
            if deleted_count > 0:
                await self._log_audit_event(
                    "history_clear",
                    {"session_id": session_id or "all", "count": deleted_count},
                    "system",
                )
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(await self.get_size())
        except Exception as e:
            self._record_op_error("clear", start_time, e)
            raise ReasonerError(
                "Failed to clear history",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def purge_all(self, operator_id: str = "system_api_request") -> None:
        """Purges all history data using TRUNCATE for efficiency."""
        start_time = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                count = await conn.fetchval("SELECT COUNT(*) FROM history")
                if count > 0:
                    await conn.execute("TRUNCATE TABLE history")

            self._logger.info(
                "postgres_purge_completed", count=count, operator_id=operator_id
            )
            self._record_op_success("purge_all", start_time)
            if count > 0:
                await self._log_audit_event(
                    "history_purge",
                    {"count": count, "operator_id": operator_id},
                    operator_id,
                )
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(0)
        except Exception as e:
            self._record_op_error("purge_all", start_time, e)
            raise ReasonerError(
                "Failed to purge all history",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def export_history(
        self, output_format: str = "json", operator_id: str = "system_api_request"
    ) -> AsyncGenerator[Union[str, bytes], None]:
        """Exports history data as chunks using a server-side cursor."""
        start_time = time.monotonic()
        try:
            if output_format not in ["json", "csv"]:
                raise ReasonerError(
                    f"Unsupported format: {output_format}",
                    code=ReasonerErrorCode.INVALID_INPUT,
                )

            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    async for row in conn.cursor(
                        "SELECT * FROM history ORDER BY timestamp DESC"
                    ):
                        entry = {
                            "id": row["id"],
                            "query": row["query"],
                            "context": row["context"],
                            "response": self._decrypt(row["response"]),
                            "response_type": row["response_type"],
                            "timestamp": row["timestamp"].isoformat(),
                            "session_id": row["session_id"],
                        }
                        if output_format == "json":
                            yield json.dumps([entry], indent=2).encode("utf-8")
                        else:  # csv
                            # Simplified CSV generation
                            yield f"\"{entry['id']}\",\"{entry['timestamp']}\"\n".encode(
                                "utf-8"
                            )

            self._logger.info(
                "postgres_export_completed",
                format=output_format,
                operator_id=operator_id,
            )
            self._record_op_success("export_history", start_time)
            await self._log_audit_event(
                "history_export",
                {"format": output_format, "operator_id": operator_id},
                operator_id,
            )
        except Exception as e:
            self._record_op_error("export_history", start_time, e)
            raise ReasonerError(
                "Failed to export history",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def aclose(self) -> None:
        """Closes the database connection pool."""
        start_time = time.monotonic()
        try:
            if self._pool:
                await self._pool.close()
                self._logger.info("postgres_connection_closed")
                self._record_op_success("aclose", start_time)
        except Exception as e:
            self._record_op_error("aclose", start_time, e)
            raise ReasonerError(
                "Failed to close Postgres connection",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )


class RedisHistoryManager(BaseHistoryManager):
    """Redis implementation of history manager using a sorted set."""

    _backend_name = "redis"

    def __init__(
        self,
        redis_url: str,
        max_history_size: int,
        retention_days: int,
        audit_client: Optional[AuditLedgerClient] = None,
    ):
        super().__init__(max_history_size, retention_days, audit_client)
        self.redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._pool: Optional[aioredis.ConnectionPool] = None
        self._history_key = "reasoner:history"

        if not REDIS_AVAILABLE:
            self._logger.error(
                "redis_missing",
                message="Redis backend unavailable. Please install 'redis'.",
            )
            raise ReasonerError(
                "redis not installed", code=ReasonerErrorCode.CONFIGURATION_ERROR
            )
        if not CRYPTOGRAPHY_AVAILABLE:
            self._logger.error(
                "cryptography_missing",
                message="Cryptography backend unavailable. Please install 'cryptography'.",
            )
            raise ReasonerError(
                "cryptography not installed", code=ReasonerErrorCode.CONFIGURATION_ERROR
            )

        encryption_key = os.getenv("REASONER_ENCRYPTION_KEY")
        if encryption_key:
            self._encryption_key = Fernet(encryption_key.encode("utf-8"))
        else:
            self._logger.warning(
                "encryption_key_missing",
                message="REASONER_ENCRYPTION_KEY not set. History responses will not be encrypted.",
            )

    async def init_db(self) -> None:
        """Initializes the Redis connection."""
        start_time = time.monotonic()
        try:
            self._pool = aioredis.ConnectionPool.from_url(
                self.redis_url, max_connections=10, decode_responses=True
            )
            self._redis = aioredis.Redis(connection_pool=self._pool)
            await self._redis.ping()
            self._logger.info("redis_db_initialized", redis_url="***")
            self._record_op_success("init_db", start_time)
        except Exception as e:
            # FIX: Check if metric exists before accessing.
            if "reasoner_history_db_connection_failures_total" in METRICS:
                METRICS["reasoner_history_db_connection_failures_total"].labels(
                    backend=self._backend_name
                ).inc()
            self._record_op_error("init_db", start_time, e)
            raise ReasonerError(
                "Failed to initialize Redis DB",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def add_entry(self, entry: Dict[str, Any]) -> None:
        """Adds a new, encrypted entry to the history."""
        await self._pre_add_entry_checks(entry)
        start_time = time.monotonic()

        entry_to_store = entry.copy()
        entry_to_store["response"] = self._encrypt(entry["response"])

        try:
            serialized_entry = json.dumps(entry_to_store)
            timestamp = datetime.fromisoformat(entry["timestamp"]).timestamp()

            async with self._redis.pipeline() as pipe:
                await pipe.zadd(self._history_key, {serialized_entry: timestamp})
                await pipe.zremrangebyrank(
                    self._history_key, 0, -self.max_history_size - 1
                )
                await pipe.execute()

            self._record_op_success("add_entry", start_time)
            await self._log_audit_event(
                "history_add_entry",
                {"entry_id": entry["id"], "session_id": entry.get("session_id")},
                "system",
            )
        except Exception as e:
            self._record_op_error("add_entry", start_time, e)
            raise ReasonerError(
                "Failed to add history entry",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def add_entries_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Adds a batch of new, encrypted entries to the history."""
        if not entries:
            return
        start_time = time.monotonic()
        try:
            mapping = {}
            for entry in entries:
                await self._pre_add_entry_checks(entry)
                entry_to_store = entry.copy()
                entry_to_store["response"] = self._encrypt(entry["response"])
                serialized = json.dumps(entry_to_store)
                timestamp = datetime.fromisoformat(entry["timestamp"]).timestamp()
                mapping[serialized] = timestamp

            async with self._redis.pipeline() as pipe:
                await pipe.zadd(self._history_key, mapping)
                await pipe.zremrangebyrank(
                    self._history_key, 0, -self.max_history_size - 1
                )
                await pipe.execute()

            self._record_op_success("add_entries_batch", start_time)
            self._logger.info("redis_batch_add_completed", count=len(entries))
            await self._log_audit_event(
                "history_add_entries_batch", {"count": len(entries)}, "system"
            )
        except Exception as e:
            self._record_op_error("add_entries_batch", start_time, e)
            raise ReasonerError(
                "Failed to add history entries in batch",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def get_entries(
        self, limit: int = 10, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves and decrypts recent history entries."""
        start_time = time.monotonic()
        try:
            # Note: Filtering by session_id in Redis requires fetching all and filtering in-memory.
            # For high-scale session-based retrieval, a different data model would be better
            # (e.g., a sorted set per session `reasoner:history:session_id`).
            if session_id:
                all_entries_raw = await self._redis.zrevrange(self._history_key, 0, -1)
                all_entries = [json.loads(e) for e in all_entries_raw]
                filtered = [
                    e for e in all_entries if e.get("session_id") == session_id
                ][:limit]
            else:
                entries_raw = await self._redis.zrevrange(
                    self._history_key, 0, limit - 1
                )
                filtered = [json.loads(e) for e in entries_raw]

            for entry in filtered:
                entry["response"] = self._decrypt(entry["response"])

            self._record_op_success("get_entries", start_time)
            return filtered
        except Exception as e:
            self._record_op_error("get_entries", start_time, e)
            raise ReasonerError(
                "Failed to retrieve history entries",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def get_size(self) -> int:
        """Returns the current number of history entries."""
        start_time = time.monotonic()
        try:
            size = await self._redis.zcard(self._history_key)
            self._record_op_success("get_size", start_time)
            return size
        except Exception as e:
            self._record_op_error("get_size", start_time, e)
            raise ReasonerError(
                "Failed to get history size",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def prune_old_entries(self) -> None:
        """Prunes old entries based on retention days using their timestamp score."""
        start_time = time.monotonic()
        try:
            cutoff_timestamp = (
                datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            ).timestamp()
            # Corrected: remove by score (timestamp)
            removed_count = await self._redis.zremrangebyscore(
                self._history_key, -float("inf"), cutoff_timestamp
            )

            if removed_count > 0:
                self._logger.info(
                    "redis_prune_completed",
                    count=removed_count,
                    cutoff_timestamp=cutoff_timestamp,
                )
                if "reasoner_history_pruned_entries_total" in METRICS:
                    METRICS["reasoner_history_pruned_entries_total"].inc(removed_count)
                await self._log_audit_event(
                    "history_prune",
                    {"count": removed_count, "cutoff_timestamp": cutoff_timestamp},
                    "system_scheduled_task",
                )
            else:
                self._logger.debug("redis_no_entries_to_prune")

            self._record_op_success("prune_old_entries", start_time)
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(await self.get_size())
        except Exception as e:
            self._record_op_error("prune_old_entries", start_time, e)
            raise ReasonerError(
                "Failed to prune old history entries",
                code=ReasonerErrorCode.HISTORY_PRUNING_FAILED,
                original_exception=e,
            )

    async def clear(self, session_id: Optional[str] = None) -> None:
        """Clears history entries, optionally for a session."""
        start_time = time.monotonic()
        try:
            count = 0
            if session_id:
                # This is inefficient and should be used with caution on large histories.
                entries = await self._redis.zrange(self._history_key, 0, -1)
                to_remove = []
                for entry_raw in entries:
                    parsed = json.loads(entry_raw)
                    if parsed.get("session_id") == session_id:
                        to_remove.append(entry_raw)
                if to_remove:
                    count = await self._redis.zrem(self._history_key, *to_remove)
            else:
                count = await self._redis.delete(self._history_key)

            self._logger.info(
                "redis_clear_completed", session_id=session_id or "all", count=count
            )
            self._record_op_success("clear", start_time)
            if count > 0:
                await self._log_audit_event(
                    "history_clear",
                    {"session_id": session_id or "all", "count": count},
                    "system",
                )
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(await self.get_size())
        except Exception as e:
            self._record_op_error("clear", start_time, e)
            raise ReasonerError(
                "Failed to clear history",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def purge_all(self, operator_id: str = "system_api_request") -> None:
        """Purges all history data."""
        start_time = time.monotonic()
        try:
            count = await self._redis.zcard(self._history_key)
            if count > 0:
                await self._redis.delete(self._history_key)

            self._logger.info(
                "redis_purge_completed", count=count, operator_id=operator_id
            )
            self._record_op_success("purge_all", start_time)
            if count > 0:
                await self._log_audit_event(
                    "history_purge",
                    {"count": count, "operator_id": operator_id},
                    operator_id,
                )
            if "reasoner_history_entries_current" in METRICS:
                METRICS["reasoner_history_entries_current"].labels(
                    backend=self._backend_name
                ).set(0)
        except Exception as e:
            self._record_op_error("purge_all", start_time, e)
            raise ReasonerError(
                "Failed to purge all history",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )

    async def export_history(
        self, output_format: str = "json", operator_id: str = "system_api_request"
    ) -> AsyncGenerator[Union[str, bytes], None]:
        """Exports history data as chunks."""
        start_time = time.monotonic()
        try:
            if output_format not in ["json", "csv"]:
                raise ReasonerError(
                    f"Unsupported format: {output_format}",
                    code=ReasonerErrorCode.INVALID_INPUT,
                )

            # Using zscan_iter for memory efficiency with large datasets
            async for entry_raw in self._redis.zscan_iter(self._history_key):
                # zscan_iter returns (member, score) tuples
                parsed = json.loads(entry_raw[0])
                parsed["response"] = self._decrypt(parsed["response"])

                if output_format == "json":
                    yield json.dumps([parsed], indent=2).encode("utf-8")
                else:  # csv
                    yield f"\"{parsed['id']}\",\"{parsed['timestamp']}\"\n".encode(
                        "utf-8"
                    )

            self._logger.info(
                "redis_export_completed", format=output_format, operator_id=operator_id
            )
            self._record_op_success("export_history", start_time)
            await self._log_audit_event(
                "history_export",
                {"format": output_format, "operator_id": operator_id},
                operator_id,
            )
        except Exception as e:
            self._record_op_error("export_history", start_time, e)
            raise ReasonerError(
                "Failed to export history",
                code=ReasonerErrorCode.HISTORY_READ_FAILED,
                original_exception=e,
            )

    async def aclose(self) -> None:
        """Closes the Redis connection pool."""
        start_time = time.monotonic()
        try:
            if self._pool:
                await self._pool.disconnect()
                self._logger.info("redis_connection_closed")
                self._record_op_success("aclose", start_time)
        except Exception as e:
            self._record_op_error("aclose", start_time, e)
            raise ReasonerError(
                "Failed to close Redis connection",
                code=ReasonerErrorCode.HISTORY_WRITE_FAILED,
                original_exception=e,
            )
