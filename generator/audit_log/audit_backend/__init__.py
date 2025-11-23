# generator/audit_log/audit_backend/__init__.py
from typing import Any, Dict, Optional, Type

# Cloud Backends
from .audit_backend_cloud import AzureBlobBackend, GCSBackend, S3Backend

# Core components
from .audit_backend_core import retry_operation  # <-- ADD here (it’s defined in audit_backend_core)
from .audit_backend_core import (
    _STATUS_ERROR,
    _STATUS_OK,
    SCHEMA_VERSION,
    AuditBackendError,
    CryptoInitializationError,
    LogBackend,
    MigrationError,
    TamperDetectionError,
    compute_hash,
    logger,
)

# File and SQL Backends
from .audit_backend_file_sql import FileBackend, SQLiteBackend

# Streaming Backends
from .audit_backend_streaming_backends import (
    HTTPBackend,
    InMemoryBackend,
    KafkaBackend,
    SplunkBackend,
)

# Streaming Utilities (public exports expected by tests)
from .audit_backend_streaming_utils import (  # retry_operation,  # <-- REMOVE this line
    FileBackedRetryQueue,
    PersistentRetryQueue,
    SensitiveDataFilter,
    SimpleCircuitBreaker,
)

# A central registry for easy backend instantiation
_BACKEND_REGISTRY: Dict[str, Type[LogBackend]] = {
    "file": FileBackend,
    "sqlite": SQLiteBackend,
    "s3": S3Backend,
    "gcs": GCSBackend,
    "azureblob": AzureBlobBackend,
    "http": HTTPBackend,
    "kafka": KafkaBackend,
    "splunk": SplunkBackend,
    "inmemory": InMemoryBackend,
}


def get_backend(backend_type: str, params: Optional[Dict[str, Any]] = None) -> LogBackend:
    """Factory for backend instantiation."""
    backend_type_lower = backend_type.lower()
    if backend_type_lower not in _BACKEND_REGISTRY:
        raise ValueError(
            f"Unknown backend: {backend_type}. Available: {list(_BACKEND_REGISTRY.keys())}"
        )

    # normalize params so we can index safely
    params = dict(params or {})

    # Instantiate the DLQ class if a specific one is requested for a backend
    # This assumes dlq_class param would be passed by string name, e.g. "FileBackedRetryQueue"
    if "dlq_class" in params and isinstance(params["dlq_class"], str):
        if params["dlq_class"] == "FileBackedRetryQueue":
            params["dlq_class"] = FileBackedRetryQueue
        elif params["dlq_class"] == "PersistentRetryQueue":
            params["dlq_class"] = PersistentRetryQueue
        else:
            logger.warning(
                "Unknown DLQ class '%s'. Defaulting to PersistentRetryQueue.",
                params["dlq_class"],
            )
            params["dlq_class"] = PersistentRetryQueue

    return _BACKEND_REGISTRY[backend_type_lower](params)


__all__ = [
    # core
    "LogBackend",
    "AuditBackendError",
    "MigrationError",
    "TamperDetectionError",
    "CryptoInitializationError",
    "SCHEMA_VERSION",
    "compute_hash",
    "_STATUS_OK",
    "_STATUS_ERROR",
    "logger",
    # file/sql backends
    "FileBackend",
    "SQLiteBackend",
    # cloud backends
    "S3Backend",
    "GCSBackend",
    "AzureBlobBackend",
    # streaming backends
    "HTTPBackend",
    "KafkaBackend",
    "SplunkBackend",
    "InMemoryBackend",
    # streaming utils
    "SensitiveDataFilter",
    "SimpleCircuitBreaker",
    "PersistentRetryQueue",
    "FileBackedRetryQueue",
    "retry_operation",
    # factory
    "get_backend",
]
