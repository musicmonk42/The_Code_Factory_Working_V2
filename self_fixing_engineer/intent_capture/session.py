import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import aiofiles
import aiofiles.os
import backoff
import portalocker
from pydantic import BaseModel, Field, ValidationError, field_validator

# P1: Security - Import Fernet for encryption
try:
    from cryptography.fernet import Fernet

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    Fernet = None
    CRYPTOGRAPHY_AVAILABLE = False
    logging.warning("cryptography library not found. Session encryption will be disabled.")

# P5: Observability - Prometheus Metrics
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
    SESSION_SAVE_ATTEMPTS = Counter(
        "session_save_attempts_total",
        "Total attempts to save session",
        ["session_name"],
    )
    SESSION_LOAD_ATTEMPTS = Counter(
        "session_load_attempts_total",
        "Total attempts to load session",
        ["session_name"],
    )
    SESSION_ERRORS = Counter(
        "session_errors_total", "Total session errors", ["session_name", "error_type"]
    )
    SESSION_SAVE_LATENCY = Histogram(
        "session_save_latency_seconds", "Latency of saving session", ["session_name"]
    )
    SESSION_LOAD_LATENCY = Histogram(
        "session_load_latency_seconds", "Latency of loading session", ["session_name"]
    )
    SESSIONS_PRUNED_TOTAL = Counter("sessions_pruned_total", "Total sessions pruned", ["reason"])
    SESSION_PRUNE_LATENCY_SECONDS = Histogram(
        "session_prune_latency_seconds", "Latency of session pruning operation"
    )
except ImportError:
    PROMETHEUS_AVAILABLE = False

    class DummyCounter:
        def inc(self, *args, **kwargs):
            pass

    class DummyGauge:
        def set(self, *args, **kwargs):
            pass

    class DummyHistogram:
        def observe(self, *args, **kwargs):
            pass

    SESSION_SAVE_ATTEMPTS = DummyCounter()
    SESSION_LOAD_ATTEMPTS = DummyCounter()
    SESSION_ERRORS = DummyCounter()
    SESSION_SAVE_LATENCY = DummyHistogram()
    SESSION_LOAD_LATENCY = DummyHistogram()
    SESSIONS_PRUNED_TOTAL = DummyCounter()
    SESSION_PRUNE_LATENCY_SECONDS = DummyHistogram()

# P5: Observability - Audit Log Integration (conceptual, assuming `log_action` exists elsewhere)
try:
    # Assuming `log_action` is a function that sends logs to a central audit system
    from runner.logging import log_action

    AUDIT_LOG_AVAILABLE = True
except ImportError:
    AUDIT_LOG_AVAILABLE = False

    def log_action(*args, **kwargs):
        pass  # Dummy log_action


logger = logging.getLogger(__name__)

# --- Concurrency & Configuration Integration ---
_CONFIG_INSTANCE: Optional[Any] = None
_config_lock = threading.Lock()
_FERNET_INSTANCE: Optional[Fernet] = None  # P1: Global Fernet instance


def _get_config():
    global _CONFIG_INSTANCE, _FERNET_INSTANCE
    with _config_lock:
        if _CONFIG_INSTANCE is None:
            try:
                from .config import Config

                _CONFIG_INSTANCE = Config()
                logger.info("Config module loaded successfully in session.py.")
            except ImportError:
                logger.warning(
                    "Config module not found. Session management will use hardcoded defaults for storage paths and history sizes."
                )
                _CONFIG_INSTANCE = None
            except ValidationError as e:
                logger.error(
                    f"Config validation failed at session module load: {e}. Ensure all required environment variables are set for Config. Using defaults."
                )
                _CONFIG_INSTANCE = None
            except Exception as e:
                logger.error(
                    f"Unexpected error loading Config module in session.py: {e}. Using defaults.",
                    exc_info=True,
                )
                _CONFIG_INSTANCE = None

        session_storage_root = "sessions"
        session_max_history_size = 1000
        session_archive_enabled = False
        session_archive_path = "archived_sessions"
        session_max_age_days = 90
        encryption_key_str: Optional[str] = None

        if _CONFIG_INSTANCE:
            session_storage_root = getattr(
                _CONFIG_INSTANCE, "SESSION_STORAGE_PATH", session_storage_root
            )
            session_max_history_size = getattr(
                _CONFIG_INSTANCE, "SESSION_MAX_HISTORY_SIZE", session_max_history_size
            )
            session_archive_enabled = getattr(
                _CONFIG_INSTANCE, "SESSION_ARCHIVE_ENABLED", session_archive_enabled
            )
            session_archive_path = getattr(
                _CONFIG_INSTANCE, "SESSION_ARCHIVE_PATH", session_archive_path
            )
            session_max_age_days = getattr(
                _CONFIG_INSTANCE, "SESSION_MAX_AGE_DAYS", session_max_age_days
            )

            # P1: Security - Get encryption key from Config
            if hasattr(_CONFIG_INSTANCE, "ENCRYPTION_KEY") and _CONFIG_INSTANCE.ENCRYPTION_KEY:
                encryption_key_str = _CONFIG_INSTANCE.ENCRYPTION_KEY.get_secret_value()

        # P1: Initialize Fernet instance if key is available
        if _FERNET_INSTANCE is None and CRYPTOGRAPHY_AVAILABLE and encryption_key_str:
            try:
                _FERNET_INSTANCE = Fernet(encryption_key_str.encode())
                logger.info("Session encryption enabled.")
            except Exception as e:
                logger.error(
                    f"Failed to initialize Fernet with provided key: {e}. Session encryption disabled.",
                    exc_info=True,
                )
                _FERNET_INSTANCE = None

        return {
            "storage_root": session_storage_root,
            "max_history_size": session_max_history_size,
            "archive_enabled": session_archive_enabled,
            "archive_path": session_archive_path,
            "max_age_days": session_max_age_days,
            "encryption_enabled": _FERNET_INSTANCE is not None,
        }


_get_config()  # Initialize config and Fernet on module load


# --- Pydantic Models for Session Data Validation ---
class AgentMemory(BaseModel):
    messages: List[Dict[str, Any]] = Field(default_factory=list)


class SessionMetadata(BaseModel):
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "active"
    version: str = "1.0.0"
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class SessionState(BaseModel):
    """
    Session data model for validation.
    NOTE: The llm_config dictionary should be handled by the caller (agent_core)
    to redact or encrypt sensitive information like API keys before saving.
    """

    session_id: str
    agent_id: str
    llm_config: Dict[str, Any]
    persona_key: str
    language: str
    memory: Union[AgentMemory, str]  # P1: Memory can be encrypted string or AgentMemory object
    last_spec: Optional[str] = None
    last_spec_format: str = "gherkin"
    last_trace: Optional[Dict[str, Any]] = None
    meta: SessionMetadata = Field(default_factory=SessionMetadata)

    @field_validator("session_id", "agent_id")
    @classmethod
    def check_valid_id(cls, v: str) -> str:
        # P1: Security - Validate IDs to prevent path traversal or invalid filenames
        if not v or any(c in '/\\:*?"<>|' for c in v):
            raise ValueError(f"ID contains invalid characters or is empty: {v}")
        return v


# --- Helper Functions for File Paths and Validation ---
def _validate_path(path: str, base_dir: str) -> str:
    """
    Validates a given path to ensure it is a safe subdirectory of the base directory.
    """
    safe_path = os.path.normpath(os.path.join(base_dir, path))
    if not os.path.abspath(safe_path).startswith(os.path.abspath(base_dir)):
        raise ValueError(f"Path '{path}' is outside the allowed directory '{base_dir}'.")
    return safe_path


def _get_session_path(session_name: str) -> str:
    """Returns the validated absolute path for a session JSON file."""
    config_params = _get_config()
    base_dir = os.path.abspath(config_params["storage_root"])
    return _validate_path(f"{session_name}.json", base_dir)


def _get_history_path(session_name: str) -> str:
    """Returns the validated absolute path for a session history JSON file."""
    config_params = _get_config()
    base_dir = os.path.abspath(config_params["storage_root"])
    return _validate_path(f"{session_name}_history.json", base_dir)


# --- Error Handling and Retries with Backoff ---
@backoff.on_exception(
    backoff.expo,
    (
        portalocker.exceptions.LockException,
        FileExistsError,
        FileNotFoundError,
        IOError,
        json.JSONDecodeError,
    ),
    max_tries=5,
    jitter=backoff.full_jitter,
    factor=0.5,
)
async def _atomic_write_json(filepath: str, data: Dict[str, Any]):
    """
    Performs an atomic and thread-safe write of JSON data to a file.
    """
    await aiofiles.os.makedirs(os.path.dirname(filepath), exist_ok=True)
    tmp_filepath = f"{filepath}.{uuid.uuid4().hex}.tmp"

    # P3: Concurrency - Use portalocker for cross-process locking
    lock_path = f"{filepath}.lock"
    try:
        with portalocker.Lock(lock_path, mode="w", timeout=10):
            async with aiofiles.open(tmp_filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=2))
            await aiofiles.os.rename(tmp_filepath, filepath)
    except Exception as e:
        logger.error(f"Atomic write failed for {filepath}: {e}", exc_info=True)
        if await aiofiles.os.path.exists(tmp_filepath):
            await aiofiles.os.remove(tmp_filepath)
        raise


@backoff.on_exception(
    backoff.expo,
    (FileNotFoundError, IOError, json.JSONDecodeError),
    max_tries=5,
    jitter=backoff.full_jitter,
    factor=0.5,
)
async def _read_json_file(filepath: str) -> Dict[str, Any]:
    """Reads JSON data from a file with retries."""
    async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
        content = await f.read()
    return json.loads(content)


# --- Public API for Session Management ---
async def save_session(session_name: str, session_data: Dict[str, Any]) -> bool:
    """
    Saves the current session data, including agent state and metadata.
    P1: Security - Encrypt sensitive parts of the state.
    P5: Observability - Metrics for save operations.
    """
    SESSION_SAVE_ATTEMPTS.inc(session_name=session_name)
    start_time = time.perf_counter()

    try:
        session_path = _get_session_path(session_name)

        # P1: Encrypt memory before saving
        config_params = _get_config()
        if config_params["encryption_enabled"] and _FERNET_INSTANCE:
            memory_obj = session_data.get("memory", {"messages": []})
            if isinstance(memory_obj, dict):  # Ensure it's a dict before dumping
                memory_json = json.dumps(memory_obj).encode("utf-8")
                encrypted_memory = _FERNET_INSTANCE.encrypt(memory_json).decode("utf-8")
                session_data["memory"] = encrypted_memory
            else:
                logger.warning(
                    f"Memory for session {session_name} is not a dict, skipping encryption."
                )

        validated_session_state = SessionState(session_id=session_name, **session_data)

        if not validated_session_state.meta.created_at:
            validated_session_state.meta.created_at = datetime.now(timezone.utc).isoformat()
        validated_session_state.meta.updated_at = datetime.now(timezone.utc).isoformat()

        await _atomic_write_json(session_path, validated_session_state.model_dump(mode="json"))

        latency = time.perf_counter() - start_time
        SESSION_SAVE_LATENCY.observe(
            {"session_name": session_name}, latency
        )  # Updated for Prometheus client
        log_action(
            "session_saved",
            {
                "session_id": session_name,
                "path": session_path,
                "size_bytes": await aiofiles.os.path.getsize(session_path),
                "latency_seconds": latency,
            },
        )
        logger.info(f"Session '{session_name}' saved to {session_path}.")
        return True
    except (ValidationError, ValueError) as e:
        SESSION_ERRORS.inc(session_name=session_name, error_type="validation_error")
        logger.error(f"Session data validation failed for '{session_name}': {e}. Save aborted.")
        log_action(
            "session_save_failed",
            {
                "session_id": session_name,
                "error": str(e),
                "reason": "validation_failed",
            },
        )
        return False
    except Exception as e:
        SESSION_ERRORS.inc(session_name=session_name, error_type=type(e).__name__)
        logger.error(f"Failed to save session '{session_name}': {e}.", exc_info=True)
        log_action(
            "session_save_failed",
            {"session_id": session_name, "error": str(e), "reason": "io_error"},
        )
        return False


async def load_session(session_name: str) -> Optional[Dict[str, Any]]:
    """
    Loads session data from storage and validates it.
    P1: Security - Decrypt encrypted parts of the state.
    P5: Observability - Metrics for load operations.
    """
    SESSION_LOAD_ATTEMPTS.inc(session_name=session_name)
    start_time = time.perf_counter()

    try:
        session_path = _get_session_path(session_name)
        logger.debug(
            f"Attempting to load session from: {session_path} (exists: {await aiofiles.os.path.exists(session_path)})"
        )
        raw_data = await _read_json_file(session_path)

        # P1: Decrypt memory after loading
        config_params = _get_config()
        if config_params["encryption_enabled"] and _FERNET_INSTANCE:
            encrypted_memory = raw_data.get("memory")
            if isinstance(encrypted_memory, str):
                try:
                    decrypted_memory_bytes = _FERNET_INSTANCE.decrypt(
                        encrypted_memory.encode("utf-8")
                    )
                    raw_data["memory"] = json.loads(decrypted_memory_bytes.decode("utf-8"))
                except Exception as e:
                    logger.error(
                        f"Failed to decrypt memory for session {session_name}: {e}. Data might be corrupted.",
                        exc_info=True,
                    )
                    SESSION_ERRORS.inc(session_name=session_name, error_type="decryption_failed")
                    return None  # Fail to load if decryption fails
            else:
                logger.warning(
                    f"Memory for session {session_name} is not encrypted string, skipping decryption."
                )

        validated_session_state = SessionState(**raw_data)
        validated_session_state.meta.last_accessed_at = datetime.now(timezone.utc).isoformat()

        # Update the timestamp atomically after successful load
        await _atomic_write_json(session_path, validated_session_state.model_dump(mode="json"))

        latency = time.perf_counter() - start_time
        SESSION_LOAD_LATENCY.observe(
            {"session_name": session_name}, latency
        )  # Updated for Prometheus client
        log_action(
            "session_loaded",
            {
                "session_id": session_name,
                "path": session_path,
                "size_bytes": await aiofiles.os.path.getsize(session_path),
                "latency_seconds": latency,
            },
        )
        logger.info(f"Session '{session_name}' loaded successfully from {session_path}.")
        return validated_session_state.model_dump(mode="json")
    except (FileNotFoundError, ValueError):
        logger.warning(
            f"Session '{session_name}' not found at {_get_session_path(session_name)}. Returning None."
        )
        log_action("session_load_failed", {"session_id": session_name, "reason": "not_found"})
        return None
    except ValidationError as e:
        SESSION_ERRORS.inc(session_name=session_name, error_type="validation_error")
        logger.error(
            f"Session data validation failed for loaded session '{session_name}': {e}. Data might be corrupted or outdated."
        )
        log_action(
            "session_load_failed",
            {
                "session_id": session_name,
                "error": str(e),
                "reason": "validation_failed",
            },
        )
        return None
    except Exception as e:
        SESSION_ERRORS.inc(session_name=session_name, error_type=type(e).__name__)
        logger.error(f"Failed to load session '{session_name}': {e}.", exc_info=True)
        log_action(
            "session_load_failed",
            {"session_id": session_name, "error": str(e), "reason": "io_error"},
        )
        return None


async def list_sessions() -> List[str]:
    """
    Lists available sessions by scanning the session storage directory.
    P5: Observability - Metrics for list operations.
    """
    logger.info("Listing sessions...")
    sessions = []
    try:
        session_dir = os.path.abspath(_get_config()["storage_root"])
        if await aiofiles.os.path.exists(session_dir):
            entries = await aiofiles.os.listdir(session_dir)
            for filename in entries:
                if filename.endswith(".json") and not filename.endswith("_history.json"):
                    sessions.append(os.path.splitext(filename)[0])
        log_action("list_sessions", {"count": len(sessions), "status": "success"})
    except Exception as e:
        SESSION_ERRORS.inc(error_type=type(e).__name__)
        logger.error(f"Failed to list sessions: {e}.", exc_info=True)
        log_action("list_sessions", {"status": "failed", "error": str(e)})
    return sorted(sessions)


async def export_spec(
    spec_content: Union[str, Dict[str, Any]], file_format: str, output_path: str
) -> bool:
    """
    Exports a specification to a file in the specified format.
    P5: Observability - Metrics for export operations.
    """
    logger.info(f"Exporting spec to '{output_path}' in '{file_format}' format...")
    try:
        output_dir = os.path.dirname(output_path)
        if output_dir:
            await aiofiles.os.makedirs(output_dir, exist_ok=True)

        content_to_write = (
            spec_content if isinstance(spec_content, str) else json.dumps(spec_content, indent=2)
        )  # Ensure dict is dumped to string
        await _atomic_write_json(output_path, {"format": file_format, "content": content_to_write})

        log_action(
            "spec_exported",
            {
                "format": file_format,
                "path": output_path,
                "size_bytes": len(content_to_write.encode("utf-8")),
            },
        )
        logger.info(f"Spec exported successfully to {output_path}.")
        return True
    except Exception as e:
        SESSION_ERRORS.inc(error_type=type(e).__name__)
        logger.error(f"Failed to export spec: {e}.", exc_info=True)
        log_action("spec_export_failed", {"path": output_path, "error": str(e)})
        return False


async def save_session_history(session_name: str, history_data: List[Dict[str, Any]]) -> bool:
    """
    Saves the command history for a specific session.
    P5: Observability - Metrics for history save operations.
    """
    logger.info(f"Saving history for session '{session_name}'...")
    try:
        history_path = _get_history_path(session_name)
        config_params = _get_config()

        if len(history_data) > config_params["max_history_size"]:
            history_data = history_data[-config_params["max_history_size"] :]
            logger.warning(
                f"Truncated history for '{session_name}' to {config_params['max_history_size']} items."
            )

        await _atomic_write_json(history_path, history_data)

        log_action(
            "session_history_saved",
            {
                "session_id": session_name,
                "path": history_path,
                "items_count": len(history_data),
            },
        )
        logger.info(f"History for session '{session_name}' saved to {history_path}.")
        return True
    except (ValueError, ValidationError) as e:
        SESSION_ERRORS.inc(error_type="validation_error")
        logger.error(
            f"History data validation failed for '{session_name}': {e}. Save aborted.",
            exc_info=True,
        )
        return False
    except Exception as e:
        SESSION_ERRORS.inc(error_type=type(e).__name__)
        logger.error(f"Failed to save history for session '{session_name}': {e}.", exc_info=True)
        log_action("session_history_save_failed", {"session_id": session_name, "error": str(e)})
        return False


async def load_session_history(session_name: str) -> List[Dict[str, Any]]:
    """
    Loads the command history for a specific session.
    P5: Observability - Metrics for history load operations.
    """
    logger.info(f"Loading history for session '{session_name}'...")
    try:
        history_path = _get_history_path(session_name)
        logger.debug(
            f"Attempting to load history from: {history_path} (exists: {await aiofiles.os.path.exists(history_path)})"
        )

        history_data = await _read_json_file(history_path)
        if not isinstance(history_data, list):
            raise ValueError("History data is not a list.")

        log_action(
            "session_history_loaded",
            {
                "session_id": session_name,
                "path": history_path,
                "items_count": len(history_data),
            },
        )
        logger.info(
            f"History for session '{session_name}' loaded successfully from {history_path}."
        )
        return history_data
    except FileNotFoundError:
        logger.warning(
            f"History for session '{session_name}' not found at {_get_history_path(session_name)}. Returning empty list."
        )
        log_action(
            "session_history_load_failed",
            {"session_id": session_name, "reason": "not_found"},
        )
        return []
    except Exception as e:
        SESSION_ERRORS.inc(error_type=type(e).__name__)
        logger.error(f"Failed to load history for session '{session_name}': {e}.", exc_info=True)
        log_action(
            "session_history_load_failed",
            {"session_id": session_name, "error": str(e), "reason": "io_error"},
        )
        return []


async def delete_session(session_name: str) -> bool:
    """
    Deletes a session and its associated history files, with optional archiving.
    P5: Observability - Metrics for delete operations.
    """
    logger.info(f"Attempting to delete session '{session_name}'...")
    try:
        session_path = _get_session_path(session_name)
        history_path = _get_history_path(session_name)
    except ValueError as e:
        logger.error(f"Invalid session name '{session_name}': {e}")
        SESSION_ERRORS.inc(session_name=session_name, error_type="invalid_session_name")
        return False

    config_params = _get_config()
    deleted_files = []

    if config_params["archive_enabled"]:
        archive_dir = os.path.abspath(config_params["archive_path"])
        await aiofiles.os.makedirs(archive_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

        if await aiofiles.os.path.exists(session_path):
            archive_session_path = os.path.join(archive_dir, f"{session_name}.{timestamp}.json")
            try:
                await aiofiles.os.rename(session_path, archive_session_path)
                logger.info(f"Archived session '{session_name}' to {archive_session_path}.")
                deleted_files.append(archive_session_path)
            except Exception as e:
                logger.error(f"Failed to archive session file {session_path}: {e}")

        if await aiofiles.os.path.exists(history_path):
            archive_history_path = os.path.join(
                archive_dir, f"{session_name}_history.{timestamp}.json"
            )
            try:
                await aiofiles.os.rename(history_path, archive_history_path)
                logger.info(f"Archived session history '{session_name}' to {archive_history_path}.")
                deleted_files.append(archive_history_path)
            except Exception as e:
                logger.error(f"Failed to archive history file {history_path}: {e}")

    try:
        if await aiofiles.os.path.exists(session_path):
            await aiofiles.os.remove(session_path)
            logger.info(f"Deleted session file '{session_name}'.")
            deleted_files.append(session_path)
        if await aiofiles.os.path.exists(history_path):
            await aiofiles.os.remove(history_path)
            logger.info(f"Deleted session history file '{session_name}'.")
            deleted_files.append(history_path)

        if not deleted_files:
            logger.warning(f"Session '{session_name}' or its history not found for deletion.")
            log_action(
                "session_delete_failed",
                {"session_id": session_name, "reason": "not_found"},
            )
            return False

        log_action(
            "session_deleted",
            {
                "session_id": session_name,
                "deleted_files": deleted_files,
                "archived": config_params["archive_enabled"],
            },
        )
        return True
    except Exception as e:
        SESSION_ERRORS.inc(session_name=session_name, error_type=type(e).__name__)
        logger.error(f"Failed to delete session '{session_name}': {e}.", exc_info=True)
        log_action("session_delete_failed", {"session_id": session_name, "error": str(e)})
        return False


async def get_session_metadata(session_name: str) -> Optional[SessionMetadata]:
    """
    Retrieves only the metadata for a session without loading its full state.
    """
    try:
        session_path = _get_session_path(session_name)
        raw_data = await _read_json_file(session_path)

        # P1: Decrypt memory (if present and encrypted) before validation, as metadata is part of SessionState
        config_params = _get_config()
        if config_params["encryption_enabled"] and _FERNET_INSTANCE:
            encrypted_memory = raw_data.get("memory")
            if isinstance(encrypted_memory, str):
                try:
                    decrypted_memory_bytes = _FERNET_INSTANCE.decrypt(
                        encrypted_memory.encode("utf-8")
                    )
                    raw_data["memory"] = json.loads(decrypted_memory_bytes.decode("utf-8"))
                except Exception as e:
                    logger.error(
                        f"Failed to decrypt memory for metadata of session {session_name}: {e}. Skipping decryption.",
                        exc_info=True,
                    )
                    # Don't return None here, try to proceed with other metadata if possible

        full_session_state = SessionState(**raw_data)
        return full_session_state.meta
    except FileNotFoundError:
        return None
    except ValidationError as e:
        logger.error(
            f"Metadata validation failed for session '{session_name}': {e}. File might be corrupted."
        )
        return None
    except Exception as e:
        logger.error(
            f"Failed to retrieve metadata for session '{session_name}': {e}",
            exc_info=True,
        )
        return None


# P2: Expiration - Pruning old/unused sessions
async def prune_old_sessions(max_age_days: Optional[int] = None) -> int:
    """
    Deletes sessions that have not been accessed for a specified number of days.
    P5: Observability - Metrics for prune operation.
    P9: GDPR Compliance - Provides a mechanism for data retention policy enforcement.
    """
    start_time = time.perf_counter()

    config_params = _get_config()
    age_threshold = max_age_days if max_age_days is not None else config_params["max_age_days"]

    if age_threshold <= 0:
        logger.info("Session pruning is disabled as max_age_days is not a positive value.")
        SESSIONS_PRUNED_TOTAL.labels(reason="disabled").inc(0)  # Log 0 pruned if disabled
        SESSION_PRUNE_LATENCY_SECONDS.observe(0)  # Observe 0 latency if disabled
        return 0

    pruned_count = 0
    now = datetime.now(timezone.utc)
    sessions_to_prune = await list_sessions()

    for session_name in sessions_to_prune:
        metadata = await get_session_metadata(session_name)
        if metadata:
            try:
                last_accessed_date = datetime.fromisoformat(metadata.last_accessed_at)
                age = now - last_accessed_date
                if age > timedelta(days=age_threshold):
                    logger.warning(
                        f"Pruning session '{session_name}' as it is {age.days} days old."
                    )
                    if await delete_session(session_name):
                        pruned_count += 1
                        SESSIONS_PRUNED_TOTAL.labels(reason="age_exceeded").inc()
                    else:
                        SESSIONS_PRUNED_TOTAL.labels(
                            reason="delete_failed"
                        ).inc()  # Count failures to delete
            except Exception as e:
                logger.error(
                    f"Error processing session '{session_name}' for pruning: {e}",
                    exc_info=True,
                )
                SESSION_ERRORS.inc(session_name=session_name, error_type="prune_processing_error")

    latency = time.perf_counter() - start_time
    SESSION_PRUNE_LATENCY_SECONDS.observe(latency)
    if pruned_count > 0:
        log_action(
            "sessions_pruned",
            {
                "count": pruned_count,
                "max_age_days": age_threshold,
                "latency_seconds": latency,
            },
        )
    logger.info(f"Session pruning complete. {pruned_count} sessions were deleted.")
    return pruned_count
