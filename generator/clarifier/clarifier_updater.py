# clarifier_updater.py
# Fully production-ready requirements updater with schema evolution, LLM inference,
# PII redaction, conflict resolution, versioning, and comprehensive observability.
# Created: July 30, 2025.
# REFACTORED: Now uses central runner for logging, alerting, and security utils.

import asyncio
import copy
import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import aiohttp  # For alerting and async LLM calls (reqs: aiohttp)
import requests  # Retained for potential synchronous LLM calls fallback or other sync needs
import zstandard as zstd  # Compression (reqs: zstandard)
from cryptography.fernet import Fernet  # Encryption (reqs: cryptography)
from jsonschema import (  # Schema validation (reqs: jsonschema)
    Draft7Validator,
    ValidationError,
    validate,
)
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
)  # Metrics (reqs: prometheus-client)

# Use centralized utilities from clarifier.py
from .clarifier import get_config, get_fernet, get_logger

# --- Central Runner Foundation Imports ---
try:
    from runner.runner_logging import log_action
except ImportError:

    def log_action(*args, **kwargs):
        logging.warning("Dummy log_action used: Runner logging is not available.")


try:
    from runner.alerting import send_alert
except ImportError:

    async def send_alert(message: str, severity: str = "info"):
        """Dummy alert function if runner.alerting is not available."""
        logging.warning(f"Dummy ALERT [{severity.upper()}]: {message}")


try:
    from runner.security_utils import _recursive_transform, detect_pii, redact_sensitive
except ImportError:
    logging.warning("Dummy security utils used: Runner security utils not available.")

    def redact_sensitive(text: str) -> str:
        """Redacts sensitive information based on patterns (dummy implementation)."""
        text = re.sub(
            r'(?i)api[-_]?key\s*[:=]\s*["\']?[\w-]{20,}["\']?',
            "[REDACTED_API_KEY]",
            text,
        )
        text = re.sub(
            r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
            "[REDACTED_EMAIL]",
            text,
        )
        return text

    def detect_pii(text: str) -> bool:
        """Detects presence of PII (dummy implementation)."""
        email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
        if re.search(email_pattern, text, re.IGNORECASE):
            return True
        return False

    def _recursive_transform(
        data: Any, detect_func: Callable[[str], bool], redact_func: Callable[[str], str]
    ) -> Any:
        """Recursively applies detect and redact functions (dummy implementation)."""
        if isinstance(data, str):
            return redact_func(data)
        elif isinstance(data, dict):
            return {
                k: _recursive_transform(v, detect_func, redact_func)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [
                _recursive_transform(item, detect_func, redact_func) for item in data
            ]
        return data


# -----------------------------------------


# --- Centralized Utilities Initialization ---
settings = get_config()
logger = get_logger()
fernet = get_fernet()

# REFACTORED: Removed local dummy functions for log_action, send_alert,
# _recursive_transform, detect_pii, and redact_sensitive.
# They are now imported from the runner foundation above.

# Configuration for Redaction Patterns (Conceptual: can be loaded from settings/DB)
REDACTION_CONFIG = {
    "patterns": [
        r'(?i)api[-_]?key\s*[:=]\s*["\']?[\w-]{20,}["\']?',  # API Keys
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",  # Emails
        r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",  # US Phone Numbers
        r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",  # Credit Card Numbers (basic)
        r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b",  # US Social Security Number (basic)
    ],
    "policy": "mask",
}


def _load_redaction_patterns() -> List[re.Pattern]:
    """Loads and compiles redaction patterns from a configurable source."""
    # This example uses the REDACTION_CONFIG defined above.
    return [re.compile(p) for p in REDACTION_CONFIG["patterns"]]


# OpenTelemetry tracing
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.propagate import get_global_textmap, set_global_textmap
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON
    from opentelemetry.trace import Status, StatusCode

    resource = Resource.create({"service.name": "clarifier-updater"})
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    processor = BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://otel-collector:4317")
    )
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer(__name__)
    HAS_OPENTELEMETRY = True
except ImportError:
    tracer = None
    HAS_OPENTELEMETRY = False
    logging.warning("OpenTelemetry not installed. Tracing disabled.")


# --- Metrics ---
UPDATE_CYCLES = Counter("clarifier_updates_total", "Requirement updates")
UPDATE_ERRORS = Counter(
    "clarifier_update_errors", "Update errors", ["type", "component"]
)
UPDATE_CONFLICTS = Gauge("clarifier_conflicts", "Conflicts detected", ["conflict_type"])
REDACTION_EVENTS = Counter(
    "clarifier_redaction_events_total", "PII/Secret redactions", ["pattern_type"]
)
SCHEMA_MIGRATIONS = Counter(
    "clarifier_schema_migrations_total",
    "Schema migrations",
    ["from_version", "to_version"],
)
INFERENCE_LATENCY = Histogram(
    "clarifier_inference_latency_seconds", "LLM inference latency", ["model_name"]
)
SELF_TEST_PASS = Gauge(
    "clarifier_updater_self_test", "Self-test status (1=pass, 0=fail)"
)
HISTORY_STORAGE_LATENCY = Histogram(
    "clarifier_history_storage_seconds", "History storage latency", ["operation"]
)
ALERT_SEND_EVENTS = Counter(
    "clarifier_alert_send_total", "Total alerts sent", ["severity"]
)

# --- Schema Definitions ---
SCHEMAS = {
    1: {
        "type": "object",
        "properties": {
            "features": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "clarifications": {"type": "object"},
            "conflict_strategy": {"type": "string"},
            "version": {"type": "integer"},
            "version_hash": {"type": "string"},
            "prev_hash": {"type": ["string", "null"]},
            "updated_by": {"type": "string"},
            "update_timestamp": {"type": "string"},
            "update_reason": {"type": "string"},
            "schema_version": {"type": "integer"},
        },
        "required": ["features", "schema_version"],
        "additionalProperties": False,
    },
    2: {
        "type": "object",
        "properties": {
            "features": {"type": "array", "items": {"type": "string"}},
            "inferred_features": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "inferred_constraints": {"type": "array", "items": {"type": "string"}},
            "clarifications": {"type": "object"},
            "desired_doc_formats": {"type": "array", "items": {"type": "string"}},
            "conflict_strategy": {"type": "string"},
            "changes": {"type": "array"},
            "version": {"type": "integer"},
            "version_hash": {"type": "string"},
            "prev_hash": {"type": ["string", "null"]},
            "updated_by": {"type": "string"},
            "update_timestamp": {"type": "string"},
            "update_reason": {"type": "string"},
            "schema_version": {"type": "integer"},
        },
        "required": ["features", "schema_version"],
        "additionalProperties": False,
    },
    3: {
        "type": "object",
        "properties": {
            "features": {"type": "array", "items": {"type": "string"}},
            "inferred_features": {"type": "array", "items": {"type": "string"}},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "inferred_constraints": {"type": "array", "items": {"type": "string"}},
            "clarifications": {"type": "object"},
            "desired_doc_formats": {"type": "array", "items": {"type": "string"}},
            "new_v3_required_field": {"type": "string"},
            "conflict_strategy": {"type": "string"},
            "changes": {"type": "array"},
            "version": {"type": "integer"},
            "version_hash": {"type": "string"},
            "prev_hash": {"type": ["string", "null"]},
            "updated_by": {"type": "string"},
            "update_timestamp": {"type": "string"},
            "update_reason": {"type": "string"},
            "schema_version": {"type": "integer"},
        },
        "required": ["features", "schema_version", "new_v3_required_field"],
        "additionalProperties": False,
    },
}
CURRENT_SCHEMA_VERSION = settings.SCHEMA_VERSION


# --- Schema Migration Steps Registry ---
async def _migrate_schema_v1_to_v2(
    data: Dict[str, Any], span: Optional[Any]
) -> Dict[str, Any]:
    """Migrates schema from version 1 to 2."""
    if span:
        span.add_event("Migrating v1 to v2")
    data["inferred_features"] = data.get("inferred_features", [])
    data["inferred_constraints"] = data.get("inferred_constraints", [])
    data["desired_doc_formats"] = data.get("desired_doc_formats", [])
    logger.debug(
        "Migrated from v1: Added 'inferred_features', 'inferred_constraints', 'desired_doc_formats'."
    )
    return data


async def _migrate_schema_v2_to_v3(
    data: Dict[str, Any], span: Optional[Any]
) -> Dict[str, Any]:
    """Migrates schema from version 2 to 3 (hypothetical)."""
    if span:
        span.add_event("Migrating v2 to v3")
    data["new_v3_required_field"] = data.get(
        "new_v3_required_field", "default_v3_value"
    )
    if "deprecated_field_v1_v2" in data:
        del data["deprecated_field_v1_v2"]
        logger.debug("Migrated from v2: Removed 'deprecated_field_v1_v2'.")
    logger.debug("Migrated from v2: Added 'new_v3_required_field'.")
    return data


_MIGRATION_FUNCTIONS: Dict[
    int, Callable[[Dict[str, Any], Optional[Any]], Awaitable[Dict[str, Any]]]
] = {
    1: _migrate_schema_v1_to_v2,
    2: _migrate_schema_v2_to_v3,
}


# --- History Storage ---
class HistoryStore:
    """Manages persistent storage of update history, including encryption and compression."""

    def __init__(self, db_path: str, fernet_instance: Fernet):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        if fernet_instance is None:
            raise RuntimeError(
                "Fernet encryption key not initialized. Ensure secure key management is set up."
            )
        self.fernet = fernet_instance
        self.compression_enabled = settings.HISTORY_COMPRESSION
        self._init_db_lock = asyncio.Lock()

    async def _init_db(self):
        """Initializes SQLite database connection and schema. Thread-safe."""
        async with self._init_db_lock:
            if self.conn is not None:
                return

            def connect_and_setup():
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                        version INTEGER NOT NULL,
                        entry_id TEXT UNIQUE NOT NULL,
                        encrypted_data BLOB NOT NULL
                    )
                """
                )
                conn.commit()
                return conn

            try:
                self.conn = await asyncio.to_thread(connect_and_setup)
                logger.info(f"HistoryStore initialized: {self.db_path}")
            except Exception as e:
                logger.critical(
                    f"Failed to initialize HistoryStore database at {self.db_path}: {e}"
                )
                UPDATE_ERRORS.labels("history", "db_init_failed").inc()
                await send_alert(
                    f"History database initialization failed: {e}", severity="critical"
                )
                raise SystemExit(1)

    async def store(self, entry: Dict[str, Any]):
        """Stores an encrypted and optionally compressed history entry."""
        if self.conn is None:
            await self._init_db()

        start_time = time.perf_counter()
        entry_id = str(uuid.uuid4())
        data = json.dumps(entry, sort_keys=True).encode("utf-8")

        try:
            if self.compression_enabled:
                original_len = len(data)
                data = zstd.compress(data)
                logger.debug(
                    f"Compressed history entry. Original size: {original_len} bytes, Compressed size: {len(data)} bytes"
                )

            encrypted_data = self.fernet.encrypt(data)

            await asyncio.to_thread(
                self.conn.execute,
                "INSERT INTO history (version, entry_id, encrypted_data) VALUES (?, ?, ?)",
                (entry.get("version", 0), entry_id, encrypted_data),
            )
            await asyncio.to_thread(self.conn.commit)
            HISTORY_STORAGE_LATENCY.labels(operation="store").observe(
                time.perf_counter() - start_time
            )
            # FIX: Removed await from synchronous log_action call
            log_action(
                "history_stored",
                category="history",
                entry_id=entry_id,
                version=entry.get("version", 0),
            )
        except Exception as e:
            logger.error(f"Failed to store history: {e}", exc_info=True)
            UPDATE_ERRORS.labels("history", "store_failed").inc()
            await send_alert(
                f"Failed to store requirements history: {e}", severity="high"
            )
            raise

    async def query(
        self, version: Optional[int] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Queries history entries, decrypts, and decompresses them."""
        if self.conn is None:
            await self._init_db()

        start_time = time.perf_counter()
        query_sql = "SELECT version, entry_id, encrypted_data FROM history"
        params = []
        if version is not None:
            query_sql += " WHERE version = ?"
            params.append(version)
        query_sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        try:
            rows_cursor = await asyncio.to_thread(
                self.conn.execute, query_sql, tuple(params)
            )
            rows = await asyncio.to_thread(rows_cursor.fetchall)

            entries = []
            for row in rows:
                encrypted_blob = row["encrypted_data"]

                try:
                    decrypted = self.fernet.decrypt(encrypted_blob)
                    if self.compression_enabled:
                        decrypted = zstd.decompress(decrypted)

                    entries.append(json.loads(decrypted.decode("utf-8")))
                except Exception as decrypt_e:
                    logger.error(
                        f"Failed to decrypt/decompress history entry {row['entry_id']}: {decrypt_e}",
                        exc_info=True,
                    )
                    UPDATE_ERRORS.labels("history", "decrypt_failed").inc()
                    await send_alert(
                        f"Failed to decrypt/decompress history entry: {decrypt_e}",
                        severity="medium",
                    )

            HISTORY_STORAGE_LATENCY.labels(operation="query").observe(
                time.perf_counter() - start_time
            )
            # FIX: Removed await from synchronous log_action call
            log_action("history_queried", category="history", count=len(entries))
            return entries
        except Exception as e:
            logger.error(f"History query failed: {e}", exc_info=True)
            UPDATE_ERRORS.labels("history", "query_failed").inc()
            await send_alert(
                f"Failed to query requirements history: {e}", severity="high"
            )
            return []

    async def close(self):
        """Closes database connection safely."""
        if self.conn:
            try:
                await asyncio.to_thread(self.conn.close)
                self.conn = None
                logger.info("HistoryStore closed.")
            except Exception as e:
                logger.error(
                    f"Error closing HistoryStore connection: {e}", exc_info=True
                )
                UPDATE_ERRORS.labels("history", "close_failed").inc()


# --- LLM Client ---
class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    @abstractmethod
    async def call_llm(self, prompt: str, model: str, api_key: str) -> Dict[str, Any]:
        """Makes a call to the LLM and returns the parsed JSON response."""
        pass


class GrokLLMClient(LLMClient):
    """Concrete implementation for calling the Grok LLM using aiohttp."""

    async def call_llm(self, prompt: str, model: str, api_key: str) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1024,
        }

        if HAS_OPENTELEMETRY and tracer:
            context = {}
            get_global_textmap().inject(context, headers)
            logger.debug(f"Injected OTel trace context: {headers}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.x.ai/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=30,
                ) as response:
                    response.raise_for_status()
                    raw_response_content = await response.text()
                    try:
                        llm_response_json = json.loads(raw_response_content)
                        content = llm_response_json["choices"][0]["message"][
                            "content"
                        ].strip()
                        return json.loads(content)
                    except (json.JSONDecodeError, KeyError) as inner_e:
                        logger.error(
                            f"LLM returned valid HTTP status but invalid JSON content or structure. Raw response: {raw_response_content[:500]}...",
                            exc_info=True,
                        )
                        raise ValueError(
                            f"LLM content parsing failed: {inner_e}"
                        ) from inner_e
        except aiohttp.ClientError as e:
            raise requests.exceptions.RequestException(
                f"LLM API client error: {e}"
            ) from e
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM API returned non-JSON response: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error during LLM call: {e}") from e


# --- Conflict Resolver ---
class ConflictResolver(ABC):
    """Abstract base class for conflict resolution strategies."""

    @abstractmethod
    async def resolve(
        self,
        conflicts: List[Dict[str, str]],
        requirements: Dict[str, Any],
        clarifications: Dict[str, str],
        user_feedback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Resolves conflicts and returns potentially modified requirements."""
        pass


class DefaultConflictResolver(ConflictResolver):
    """Default conflict resolution based on strategy config."""

    async def resolve(
        self,
        conflicts: List[Dict[str, str]],
        requirements: Dict[str, Any],
        clarifications: Dict[str, str],
        user_feedback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        if not conflicts:
            return requirements

        logger.info(
            f"Resolving {len(conflicts)} conflicts using strategy: {requirements.get('conflict_strategy', 'discard')}."
        )
        strategy = requirements.get("conflict_strategy", "discard")

        resolved_requirements = copy.deepcopy(requirements)

        for conflict in conflicts:
            conflict_type = conflict["type"]
            conflict_desc = conflict["description"]
            feature = conflict.get("feature")
            clarity = conflict.get("clarity")

            action_taken = False
            if user_feedback and strategy == "user_feedback":
                try:
                    action = user_feedback(conflict_desc)
                    if action == "ignore":
                        logger.info(
                            f"Ignoring conflict: {conflict_desc} per user feedback."
                        )
                        action_taken = True
                    elif action == "prioritize_new" and feature and clarity:
                        if feature in resolved_requirements.get("features", []):
                            resolved_requirements["features"].remove(feature)
                            # FIX: Removed await from synchronous log_action call
                            log_action(
                                "conflict_resolved",
                                category="conflict",
                                type="user_prioritize_new",
                                feature=feature,
                            )
                            logger.info(
                                f"Removed feature '{feature}' from requirements per user feedback."
                            )
                            action_taken = True
                    elif action == "prioritize_old" and feature and clarity:
                        if feature in clarifications:
                            del clarifications[feature]
                            # FIX: Removed await from synchronous log_action call
                            log_action(
                                "conflict_resolved",
                                category="conflict",
                                type="user_prioritize_old",
                                feature=feature,
                            )
                            logger.info(
                                f"Discarded clarification for '{feature}' per user feedback."
                            )
                            action_taken = True
                    else:
                        logger.warning(
                            f"Unknown user feedback action: {action} for conflict: {conflict_desc}."
                        )
                        UPDATE_ERRORS.labels(
                            "conflict_resolution", "invalid_user_action"
                        ).inc()
                except Exception as e:
                    logger.error(
                        f"Error executing user feedback for conflict '{conflict_desc}': {e}",
                        exc_info=True,
                    )
                    UPDATE_ERRORS.labels(
                        "conflict_resolution", "user_feedback_error"
                    ).inc()
                    await send_alert(
                        f"Error in user feedback for conflict: {e}", severity="medium"
                    )
            elif strategy == "auto_merge":
                if conflict_type == "feature_contradiction" and feature and clarity:
                    if feature in resolved_requirements.get("features", []):
                        resolved_requirements["features"].remove(feature)
                        # FIX: Removed await from synchronous log_action call
                        log_action(
                            "conflict_resolved",
                            category="conflict",
                            type="auto_merge",
                            feature=feature,
                        )
                        logger.info(
                            f"Auto-merged: Removed feature '{feature}' due to clarification '{clarity}'."
                        )
                        action_taken = True
            elif strategy == "discard":
                # FIX: Removed await from synchronous log_action call
                log_action(
                    "conflict_resolved",
                    category="conflict",
                    type="discard",
                    description=conflict_desc,
                )
                logger.info(
                    f"Discarding conflict: {conflict_desc} (no action taken on requirements)."
                )
                action_taken = True
            elif strategy == "ml_recommend":
                logger.warning(
                    f"ML recommendation strategy not yet implemented for conflict: {conflict_desc}."
                )
                UPDATE_ERRORS.labels("conflict_resolution", "ml_not_implemented").inc()
            else:
                logger.warning(
                    f"Unknown conflict resolution strategy '{strategy}'. Conflict '{conflict_desc}' remains unaddressed."
                )
                UPDATE_ERRORS.labels("conflict_resolution", "unknown_strategy").inc()

            if action_taken:
                UPDATE_CONFLICTS.labels(conflict_type=conflict_type).dec()

        return resolved_requirements


# --- Requirements Updater ---
class RequirementsUpdater:
    """Orchestrates requirement updates with schema evolution, inference, redaction, conflict resolution, and versioning."""

    _schema_migration_lock = asyncio.Lock()

    def __init__(
        self,
        conflict_resolver: Optional[ConflictResolver] = None,
        llm_client: Optional[LLMClient] = None,
        run_self_test: bool = True,
    ):
        self.config = get_config()
        self.fernet = get_fernet()
        self.logger = get_logger()
        self.conflict_resolver = conflict_resolver or DefaultConflictResolver()
        self.llm_client = llm_client or GrokLLMClient()
        self.history_store = HistoryStore(self.config.HISTORY_DB_PATH, self.fernet)
        self.requirements_snapshot: Dict[str, Any] = {}
        self.clarifications_snapshot: Dict[str, str] = {}

        self._db_init_task = asyncio.create_task(self.history_store._init_db())
        # FIX: Allow disabling self-test for testing
        if run_self_test:
            self._self_test_task = asyncio.create_task(self._run_self_test_on_startup())
        else:
            self._self_test_task = None

    async def _run_self_test_on_startup(self):
        """Runs self-test on initialization. Enforces fail-closed mode."""
        await self._db_init_task

        # FIX: Now self_test() is async, so we can await it
        success = await self.self_test()
        if not success:
            logger.critical(
                "Updater self-test failed on initialization. Exiting due to fail-closed policy."
            )
            await send_alert(
                "Updater self-test failed on initialization. System exiting.",
                severity="critical",
            )
            raise SystemExit(1)

    async def _migrate_schema(self, requirements: Dict[str, Any]) -> Dict[str, Any]:
        """Migrates requirements schema. Uses a class-level lock for concurrent safety."""
        async with RequirementsUpdater._schema_migration_lock:
            if HAS_OPENTELEMETRY:
                with tracer.start_as_current_span("schema_migration") as span:
                    return await self._migrate_schema_internal(requirements, span)
            return await self._migrate_schema_internal(requirements, None)

    async def _migrate_schema_internal(
        self, requirements: Dict[str, Any], span: Optional[Any]
    ) -> Dict[str, Any]:
        current = copy.deepcopy(requirements)
        original_version = current.get("schema_version", 1)
        target_version = self.config.SCHEMA_VERSION

        if span:
            span.set_attribute("schema.initial_version", original_version)
            span.set_attribute("schema.target_version", target_version)

        if original_version < target_version:
            logger.info(
                f"Migrating schema from v{original_version} to v{target_version}"
            )
            backup = copy.deepcopy(current)
            try:
                for version_from in range(original_version, target_version):
                    migration_func = _MIGRATION_FUNCTIONS.get(version_from)
                    if migration_func:
                        current = await migration_func(current, span)
                        logger.debug(
                            f"Applied migration from v{version_from} to v{version_from + 1}"
                        )
                    else:
                        logger.warning(
                            f"No specific migration function found from v{version_from}. Skipping."
                        )

                current["schema_version"] = target_version
                SCHEMA_MIGRATIONS.labels(
                    from_version=str(original_version), to_version=str(target_version)
                ).inc()
                # FIX: Removed await from synchronous log_action call
                log_action(
                    "schema_migrated",
                    category="schema",
                    from_version=original_version,
                    to_version=target_version,
                )
                if span:
                    span.set_status(StatusCode.OK, "Schema migration successful")
            except Exception as e:
                logger.error(
                    f"Schema migration from v{original_version} to v{target_version} failed: {e}",
                    exc_info=True,
                )
                UPDATE_ERRORS.labels("schema", "migration_failed").inc()
                if span:
                    span.set_status(StatusCode.ERROR, str(e))
                    span.record_exception(e)
                await send_alert(
                    f"Schema migration from v{original_version} to v{target_version} failed: {e}",
                    severity="critical",
                )
                return backup
        elif original_version > target_version:
            logger.warning(
                f"Requirements schema v{original_version} is newer than updater's v{target_version}. Ignoring new fields for backward compatibility."
            )
            if span:
                span.set_status(
                    StatusCode.OK,
                    "Newer schema version detected, ignoring extra fields.",
                )
        else:
            logger.debug(
                f"Schema is already at current version v{target_version}. No migration needed."
            )
            if span:
                span.set_attribute("schema.migration_performed", False)
                span.set_status(StatusCode.OK, "No migration needed")

        return current

    def _validate_schema(self, requirements: Dict[str, Any]) -> None:
        """Validates requirements against current schema version."""
        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span("schema_validation") as span:
                self._validate_schema_internal(requirements, span)
        else:
            self._validate_schema_internal(requirements, None)

    def _validate_schema_internal(
        self, requirements: Dict[str, Any], span: Optional[Any]
    ) -> None:
        schema = SCHEMAS.get(self.config.SCHEMA_VERSION)
        if not schema:
            if span:
                span.set_status(
                    StatusCode.ERROR,
                    f"Unknown schema version: {self.config.SCHEMA_VERSION}",
                )
            UPDATE_ERRORS.labels("schema", "validation_config").inc()
            raise ValueError(f"Unknown schema version: {self.config.SCHEMA_VERSION}")
        try:
            validate(instance=requirements, schema=schema, cls=Draft7Validator)
            if span:
                span.set_status(StatusCode.OK)
            logger.debug("Requirements validated against schema successfully.")
        except ValidationError as e:
            UPDATE_ERRORS.labels("schema", "validation_failed").inc()
            if span:
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            # FIX: Check for event loop before attempting async call
            try:
                asyncio.get_running_loop()
                asyncio.create_task(
                    send_alert(
                        f"Schema validation failed: {e.message}", severity="critical"
                    )
                )
            except RuntimeError:
                # No event loop running, log only
                logger.error(f"Cannot send alert - no event loop: {e.message}")
            raise ValueError(
                f"Schema validation failed: {e.message} at {'.'.join(map(str, e.path))}"
            ) from e

    async def _infer_updates(self, answers: Dict[str, str]) -> Dict[str, List[str]]:
        """Infers updates using LLM."""
        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span("llm_inference") as span:
                return await self._infer_updates_internal(answers, span)
        return await self._infer_updates_internal(answers, None)

    async def _infer_updates_internal(
        self, answers: Dict[str, str], span: Optional[Any]
    ) -> Dict[str, List[str]]:
        if span:
            span.set_attribute("inference.llm_provider", self.config.INFERENCE_LLM)
            span.set_attribute("inference.answers_count", len(answers))

        if not answers:
            if span:
                span.set_status(StatusCode.OK, "No answers provided for inference.")
                span.set_attribute("inference.skipped", True)
            logger.info("No answers provided for inference. Skipping LLM inference.")
            return {"inferred_features": [], "inferred_constraints": []}

        llm_api_key = os.getenv(f"{self.config.INFERENCE_LLM.upper()}_API_KEY")
        if not llm_api_key:
            logger.warning("LLM API key is missing. Skipping LLM inference.")
            UPDATE_ERRORS.labels("llm_inference", "missing_api_key").inc()
            if span:
                span.set_status(StatusCode.ERROR, "LLM API key is missing.")
                span.set_attribute("inference.skipped", True)
            await send_alert(
                "LLM API key is missing, inference skipped.", severity="medium"
            )
            return {"inferred_features": [], "inferred_constraints": []}

        start_time = time.perf_counter()
        prompt = f"""
        Analyze the following user clarifications and extract any new or refined software features and system constraints.
        Clarifications:
        {json.dumps(answers, indent=2)}

        Output a JSON object with two keys: "inferred_features" (a list of strings) and "inferred_constraints" (a list of strings).
        Example: {{"inferred_features": ["User authentication via OAuth"], "inferred_constraints": ["API must handle 1000 requests/sec"]}}
        """
        try:
            inferred = await self.llm_client.call_llm(
                prompt, self.config.INFERENCE_LLM, llm_api_key
            )

            if (
                not isinstance(inferred, dict)
                or not isinstance(inferred.get("inferred_features"), list)
                or not all(
                    isinstance(f, str) for f in inferred.get("inferred_features", [])
                )
                or not isinstance(inferred.get("inferred_constraints"), list)
                or not all(
                    isinstance(c, str) for c in inferred.get("inferred_constraints", [])
                )
            ):

                logger.error(
                    f"LLM response structure validation failed. Unexpected keys or types. Raw LLM output (partial): {str(inferred)[:500]}",
                    exc_info=True,
                )
                raise ValueError(
                    "LLM response did not conform to expected JSON structure or types."
                )

            inference_duration = time.perf_counter() - start_time
            INFERENCE_LATENCY.labels(model_name=self.config.INFERENCE_LLM).observe(
                inference_duration
            )

            # FIX: Removed await from synchronous log_action call
            log_action(
                "inference_updates",
                category="llm",
                features_count=len(inferred.get("inferred_features", [])),
                constraints_count=len(inferred.get("inferred_constraints", [])),
                duration_seconds=inference_duration,
            )
            if span:
                span.set_status(StatusCode.OK)
                span.set_attribute("inference.duration_seconds", inference_duration)
                span.set_attribute(
                    "inference.features_count",
                    len(inferred.get("inferred_features", [])),
                )
                span.set_attribute(
                    "inference.constraints_count",
                    len(inferred.get("inferred_constraints", [])),
                )
            return inferred
        except (requests.exceptions.RequestException, aiohttp.ClientError) as e:
            log_msg = f"LLM inference failed due to network/API error: {type(e).__name__} - {e}. Skipping inference for this cycle."
            error_type_label = "network_error"
            logger.warning(log_msg, exc_info=True)
            UPDATE_ERRORS.labels("llm_inference", error_type_label).inc()
            if span:
                span.set_status(StatusCode.ERROR, log_msg)
                span.record_exception(e)
            await send_alert(
                f"LLM inference network/API error: {type(e).__name__}",
                severity="medium",
            )
            return {"inferred_features": [], "inferred_constraints": []}
        except (json.JSONDecodeError, ValueError) as e:
            log_msg = f"LLM inference failed due to invalid JSON response or structure: {type(e).__name__} - {e}. Skipping inference."
            error_type_label = "invalid_response"
            logger.warning(log_msg, exc_info=True)
            UPDATE_ERRORS.labels("llm_inference", error_type_label).inc()
            if span:
                span.set_status(StatusCode.ERROR, log_msg)
                span.record_exception(e)
            await send_alert(
                f"LLM inference response format error: {type(e).__name__}",
                severity="medium",
            )
            return {"inferred_features": [], "inferred_constraints": []}
        except Exception as e:
            logger.error(f"Unexpected error during LLM inference: {e}", exc_info=True)
            UPDATE_ERRORS.labels("llm_inference", "unexpected_error").inc()
            if span:
                span.set_status(StatusCode.ERROR, f"LLM Unexpected Error: {e}")
                span.record_exception(e)
            await send_alert(
                f"Unexpected LLM inference error: {type(e).__name__}", severity="high"
            )
            return {"inferred_features": [], "inferred_constraints": []}

    def _redact_answers(self, answers: Dict[str, Any]) -> Dict[str, Any]:
        """Redacts PII and secrets from answers, supports nested structures."""
        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span("redact_answers") as span:
                return self._redact_answers_internal(answers, span)
        return self._redact_answers_internal(answers, None)

    def _redact_answers_internal(
        self, answers: Dict[str, Any], span: Optional[Any]
    ) -> Dict[str, Any]:
        # REFACTORED: Calls imported _recursive_transform, detect_pii, and redact_sensitive
        redacted_answers = _recursive_transform(answers, detect_pii, redact_sensitive)

        redaction_count = 0
        for k, v in answers.items():
            if redacted_answers.get(k) != v:
                redaction_count += 1
                REDACTION_EVENTS.labels(
                    pattern_type="general_redaction_performed"
                ).inc()

        if span:
            span.set_attribute("redaction.count", redaction_count)
            span.set_status(StatusCode.OK)
        logger.info(
            f"Redaction process completed. {redaction_count} answers had sensitive information redacted."
        )
        return redacted_answers

    def _detect_conflicts(
        self, requirements: Dict[str, Any], clarifications: Dict[str, str]
    ) -> List[Dict[str, str]]:
        """Detects conflicts in requirements."""
        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span("detect_conflicts") as span:
                return self._detect_conflicts_internal(
                    requirements, clarifications, span
                )
        return self._detect_conflicts_internal(requirements, clarifications, None)

    def _detect_conflicts_internal(
        self,
        requirements: Dict[str, Any],
        clarifications: Dict[str, str],
        span: Optional[Any],
    ) -> List[Dict[str, str]]:
        conflicts = []
        for feature, clarity in clarifications.items():
            if feature in requirements.get(
                "features", []
            ) and clarity.lower().strip() in [
                "no",
                "false",
                "n/a",
                "not required",
            ]:
                conflict = {
                    "type": "feature_contradiction",
                    "description": f"Conflict: Feature '{feature}' exists in requirements but clarified as '{clarity}'.",
                    "feature": feature,
                    "clarity": clarity,
                }
                conflicts.append(conflict)
                UPDATE_CONFLICTS.labels(conflict_type="feature_contradiction").inc()
                if span:
                    span.add_event("Conflict detected", attributes=conflict)

        if span:
            span.set_attribute("conflicts.count", len(conflicts))
            span.set_status(StatusCode.OK)
        return conflicts

    async def _resolve_conflicts(
        self,
        conflicts: List[Dict[str, str]],
        requirements: Dict[str, Any],
        clarifications: Dict[str, str],
        user_feedback: Optional[Callable],
    ) -> Dict[str, Any]:
        """Resolves conflicts using the configured strategy."""
        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span("resolve_conflicts") as span:
                return await self._resolve_conflicts_internal(
                    conflicts, requirements, clarifications, user_feedback, span
                )
        return await self._resolve_conflicts_internal(
            conflicts, requirements, clarifications, user_feedback, None
        )

    async def _resolve_conflicts_internal(
        self,
        conflicts: List[Dict[str, str]],
        requirements: Dict[str, Any],
        clarifications: Dict[str, str],
        user_feedback: Optional[Callable],
        span: Optional[Any],
    ) -> Dict[str, Any]:
        if not conflicts:
            if span:
                span.set_status(StatusCode.OK, "No conflicts to resolve.")
            return requirements

        try:
            resolved_requirements = await self.conflict_resolver.resolve(
                conflicts, requirements, clarifications, user_feedback
            )

            if span:
                span.set_status(StatusCode.OK, f"Resolved {len(conflicts)} conflicts.")
            return resolved_requirements
        except Exception as e:
            UPDATE_ERRORS.labels("conflict_resolution", type(e).__name__).inc()
            logger.error(f"Conflict resolution failed: {e}", exc_info=True)
            if span:
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            await send_alert(f"Conflict resolution failed: {e}", severity="critical")
            raise

    def _add_versioning(
        self, requirements: Dict[str, Any], user: str, reason: str
    ) -> Dict[str, Any]:
        """Adds versioning and hash chain to requirements document."""
        if HAS_OPENTELEMETRY:
            with tracer.start_as_current_span("add_versioning") as span:
                return self._add_versioning_internal(requirements, user, reason, span)
        return self._add_versioning_internal(requirements, user, reason, None)

    def _add_versioning_internal(
        self, requirements: Dict[str, Any], user: str, reason: str, span: Optional[Any]
    ) -> Dict[str, Any]:
        current = copy.deepcopy(requirements)
        timestamp = datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"

        prev_hash = current.get("version_hash", "genesis_hash_placeholder")

        current["version"] = current.get("version", 0) + 1
        current["updated_by"] = user
        current["update_reason"] = reason
        current["update_timestamp"] = timestamp
        current["changes"] = []

        hashable_data = {k: v for k, v in current.items() if k not in ["version_hash"]}
        hashable_data["prev_hash"] = prev_hash

        canonical_json = json.dumps(
            hashable_data, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        current["version_hash"] = hashlib.sha256(canonical_json).hexdigest()
        current["prev_hash"] = prev_hash

        if span:
            span.set_attribute("versioning.version", current["version"])
            span.set_attribute("versioning.current_hash", current["version_hash"])
            span.set_attribute("versioning.previous_hash", current["prev_hash"])
            span.set_status(StatusCode.OK)

        # FIX: Handle None prev_hash safely
        prev_hash_display = (
            prev_hash[:8]
            if prev_hash and prev_hash != "genesis_hash_placeholder"
            else "None (first version)"
        )
        # FIX: Removed await from synchronous log_action call
        log_action(
            "requirements_versioned",
            category="versioning",
            version=current["version"],
            current_hash=current["version_hash"][:8],
            previous_hash=prev_hash_display,
        )
        logger.info(
            f"Requirements versioned to v{current['version']} by {user} (hash: {current['version_hash'][:8]})."
        )
        return current

    def _verify_hash_chain(self, entry: Dict[str, Any]) -> bool:
        """Verifies hash chain integrity for a single entry."""
        if "version_hash" not in entry or "prev_hash" not in entry:
            logger.warning(
                f"History entry version {entry.get('version', 'N/A')} missing hash components for verification."
            )
            return False

        hashable_data = {k: v for k, v in entry.items() if k != "version_hash"}

        canonical_json = json.dumps(
            hashable_data, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        recomputed_hash = hashlib.sha256(canonical_json).hexdigest()

        if recomputed_hash == entry["version_hash"]:
            return True
        else:
            # FIX: Handle None prev_hash safely when logging
            prev_hash = entry.get("prev_hash")
            prev_hash_display = prev_hash[:8] if prev_hash else "None (first version)"
            logger.error(
                f"Hash chain integrity check failed for version {entry.get('version')}. "
                f"Stored hash: {entry['version_hash'][:8]}, Recomputed: {recomputed_hash[:8]}. "
                f"Previous hash used for computation: {prev_hash_display}"
            )
            UPDATE_ERRORS.labels("integrity", "hash_mismatch").inc()
            return False

    async def update(
        self,
        requirements: Dict[str, Any],
        ambiguities: List[str],
        answers: List[str],
        user: str = "system",
        reason: str = "clarification",
        user_feedback: Optional[Callable] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Orchestrates the update workflow with full traceability.
        """
        await self._db_init_task

        if HAS_OPENTELEMETRY:
            context_attrs = {}
            if correlation_id:
                context_attrs["correlation.id"] = correlation_id

            with tracer.start_as_current_span(
                "update_requirements_workflow", attributes=context_attrs
            ) as span:
                return await self._update_internal(
                    requirements,
                    ambiguities,
                    answers,
                    user,
                    reason,
                    user_feedback,
                    span,
                )
        else:
            return await self._update_internal(
                requirements, ambiguities, answers, user, reason, user_feedback, None
            )

    async def _update_internal(
        self,
        requirements: Dict[str, Any],
        ambiguities: List[str],
        answers: List[str],
        user: str,
        reason: str,
        user_feedback: Optional[Callable],
        span: Optional[Any],
    ) -> Dict[str, Any]:
        UPDATE_CYCLES.inc()
        try:
            self.requirements_snapshot = copy.deepcopy(requirements)
            self.clarifications_snapshot = dict(zip(ambiguities, answers))

            # 1. Schema evolution
            self.requirements_snapshot = await self._migrate_schema(
                self.requirements_snapshot
            )
            self._validate_schema(self.requirements_snapshot)
            if span:
                span.add_event("Schema migrated and validated")

            # 2. Redaction
            self.clarifications_snapshot = self._redact_answers(
                self.clarifications_snapshot
            )
            if span:
                span.add_event("Answers redacted")

            # 3. Inference
            inferred = await self._infer_updates(self.clarifications_snapshot)
            self.requirements_snapshot.setdefault("inferred_features", []).extend(
                inferred["inferred_features"]
            )
            self.requirements_snapshot.setdefault("inferred_constraints", []).extend(
                inferred["inferred_constraints"]
            )
            if span:
                span.add_event("LLM inference applied")

            # 4. Append clarifications
            self.requirements_snapshot.setdefault("clarifications", {}).update(
                self.clarifications_snapshot
            )
            if span:
                span.add_event("Clarifications appended")

            # 5. Conflicts
            conflicts = self._detect_conflicts(
                self.requirements_snapshot, self.clarifications_snapshot
            )
            if conflicts:
                self.requirements_snapshot = await self._resolve_conflicts(
                    conflicts,
                    self.requirements_snapshot,
                    self.clarifications_snapshot,
                    user_feedback,
                )
                if span:
                    span.add_event("Conflicts detected and resolved")
            else:
                if span:
                    span.add_event("No conflicts detected")
                UPDATE_CONFLICTS.labels(conflict_type="none").set(0)

            # 6. Versioning/provenance
            final_requirements = self._add_versioning(
                self.requirements_snapshot, user, reason
            )

            # 7. Store history
            await self.history_store.store(final_requirements)
            if span:
                span.add_event("Requirements stored in history")

            # FIX: Removed await from synchronous log_action call
            log_action(
                "requirements_updated",
                category="update_workflow",
                version=final_requirements["version"],
                conflicts_detected=len(conflicts),
                final_status="success",
            )
            if span:
                span.set_status(StatusCode.OK)
            return final_requirements
        except Exception as e:
            UPDATE_ERRORS.labels("update_workflow", type(e).__name__).inc()
            logger.error(f"Requirements update process failed: {e}", exc_info=True)
            await send_alert(f"Requirements update failed: {e}", severity="high")
            if span:
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
            # FIX: Removed await from synchronous log_action call
            log_action(
                "requirements_update_failed",
                category="update_workflow",
                error=str(e),
                user=user,
                reason=reason,
                final_status="failure",
            )
            raise

    async def self_test(self) -> bool:
        """Performs comprehensive self-test. This method is now properly async."""
        logger.info("Running self-test for RequirementsUpdater...")

        # FIX: Use await instead of asyncio.run()
        await self.history_store._init_db()
        await self._clear_history_for_test()

        # FIX: Add conflict_strategy to the test data
        test_req_initial = {
            "features": ["test_feature_1"],
            "schema_version": 1,
            "conflict_strategy": "auto_merge",  # <-- FIX APPLIED HERE
        }
        test_ambiguities = ["test_secret", "email_pii", "contradictory_feature"]
        test_answers = ["api_key=SECRET123", "user@example.com", "no"]

        try:
            with patch.object(
                self,
                "_infer_updates",
                AsyncMock(
                    return_value={
                        "inferred_features": ["inferred_self_test_feature"],
                        "inferred_constraints": [],
                    }
                ),
            ):
                initial_req_for_test = copy.deepcopy(test_req_initial)
                initial_req_for_test["features"].append("contradictory_feature")

                # FIX: Use await instead of asyncio.run()
                updated_for_test = await self.update(
                    initial_req_for_test,
                    test_ambiguities,
                    test_answers,
                    user="self_test",
                    reason="updater_self_test",
                )

            # 1. Integrity
            if not self._verify_hash_chain(updated_for_test):
                logger.error(
                    "Self-test FAILED: Hash chain integrity check failed for final updated requirements."
                )
                SELF_TEST_PASS.set(0)
                return False

            # FIX: Use await instead of asyncio.run()
            history = await self.history_store.query(limit=1)
            assert len(history) == 1, "Self-test: History should contain one entry."
            assert self._verify_hash_chain(
                history[0]
            ), "Self-test: Stored history entry hash mismatch."

            # 2. Correctness
            self._validate_schema(updated_for_test)

            # 3. Compliance: Redaction
            # REFACTORED: These checks now rely on the *imported* runner.security_utils
            if "[REDACTED_API_KEY]" not in updated_for_test["clarifications"].get(
                "test_secret", ""
            ) or "[REDACTED_EMAIL]" not in updated_for_test["clarifications"].get(
                "email_pii", ""
            ):
                logger.error(
                    "Self-test FAILED: Redaction compliance check failed. Sensitive data not redacted."
                )
                UPDATE_ERRORS.labels("redaction", "self_test_failed").inc()
                SELF_TEST_PASS.set(0)
                return False

            # 4. Conflict Resolution
            if self.config.CONFLICT_STRATEGY == "auto_merge":
                assert (
                    "contradictory_feature" not in updated_for_test["features"]
                ), "Self-test failed: Auto-merge did not remove contradictory feature."
            else:
                assert (
                    "contradictory_feature" in updated_for_test["features"]
                ), "Self-test failed: Conflicting feature removed unexpectedly."

            # 5. LLM Inference
            assert "inferred_self_test_feature" in updated_for_test.get(
                "inferred_features", []
            ), "Self-test failed: Inferred feature missing."

            # 6. Config and Encryption
            assert self.config is not None
            assert self.fernet is not None
            assert self.logger == get_logger()

            logger.info(
                "Self-test PASSED: All checks successful (Integrity, Schema, Redaction, Conflict Resolution, Inference, History)."
            )
            SELF_TEST_PASS.set(1)
            return True
        except Exception as e:
            logger.error(
                f"Self-test FAILED due to an unexpected error: {e}", exc_info=True
            )
            SELF_TEST_PASS.set(0)
            # FIX: Use await instead of asyncio.run()
            await send_alert(f"Updater self-test failed: {e}", severity="critical")
            return False

    async def _clear_history_for_test(self):
        """Utility to clear history database for a clean test run (used by self_test)."""
        if self.history_store.conn:
            await asyncio.to_thread(
                self.history_store.conn.execute, "DELETE FROM history"
            )
            await asyncio.to_thread(self.history_store.conn.commit)
            logger.info("History database cleared for self-test.")

    async def close(self):
        """Closes resources (database connection, etc.)."""
        if self._db_init_task:
            await self._db_init_task

        if self._self_test_task and not self._self_test_task.done():
            await self._self_test_task

        await self.history_store.close()


# --- Convenience Function ---
updater: Optional[RequirementsUpdater] = None


async def initialize_updater():
    """Initializes the global RequirementsUpdater instance."""
    global updater
    if updater is None:
        logger.info("Initializing global RequirementsUpdater instance...")
        updater = RequirementsUpdater()
        await updater._db_init_task
        if updater._self_test_task:
            await updater._self_test_task
        logger.info("Global RequirementsUpdater instance initialized successfully.")


def update_requirements_with_answers(
    requirements: Dict[str, Any],
    ambiguities: List[str],
    answers: List[str],
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function to call the global updater instance.
    Handles both cases: called from sync or async context.
    """

    async def _async_update():
        if updater is None:
            await initialize_updater()
        return await updater.update(
            requirements, ambiguities, answers, correlation_id=correlation_id
        )

    # FIX: Check if we're already in an async context
    try:
        asyncio.get_running_loop()
        # We're in an async context - this shouldn't be called synchronously
        raise RuntimeError(
            "update_requirements_with_answers() called from async context. "
            "Use 'await initialize_updater()' and 'await updater.update()' directly instead."
        )
    except RuntimeError as e:
        # Check if it's the "no running event loop" error or our custom error
        if "async context" in str(e):
            raise  # Re-raise our custom error
        # No event loop running - safe to use asyncio.run()
        return asyncio.run(_async_update())
