# audit_backends/__init__.py
from typing import Any, Dict, Optional, Type

# Core components
from .audit_backend_core import (
    LogBackend, AuditBackendError, MigrationError, TamperDetectionError, logger # Also logger, for consistency if needed
)

# File and SQL Backends
from .audit_backend_file_sql import FileBackend, SQLiteBackend

# Cloud Backends
from .audit_backend_cloud import S3Backend, GCSBackend, AzureBlobBackend

# Streaming Backends (from the new _backends file)
from .audit_backend_streaming_backends import HTTPBackend, KafkaBackend, SplunkBackend, InMemoryBackend

# Streaming Utilities (optional, for direct access if needed, but primarily for internal use)
from .audit_backend_streaming_utils import (
    SensitiveDataFilter, SimpleCircuitBreaker, PersistentRetryQueue, FileBackedRetryQueue
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
        raise ValueError(f"Unknown backend: {backend_type}. Available: {list(_BACKEND_REGISTRY.keys())}")
    
    # Instantiate the DLQ class if a specific one is requested for a backend
    # This assumes dlq_class param would be passed by string name, e.g. "FileBackedRetryQueue"
    if "dlq_class" in params and isinstance(params["dlq_class"], str):
        if params["dlq_class"] == "FileBackedRetryQueue":
            params["dlq_class"] = FileBackedRetryQueue
        elif params["dlq_class"] == "PersistentRetryQueue":
            params["dlq_class"] = PersistentRetryQueue
        else:
            logger.warning(f"Unknown DLQ class '{params['dlq_class']}'. Defaulting to PersistentRetryQueue.")
            params["dlq_class"] = PersistentRetryQueue

    return _BACKEND_REGISTRY[backend_type_lower](params or {})


# Define what is accessible when 'from audit_backends import *' is used
__all__ = [
    "LogBackend",
    "FileBackend",
    "SQLiteBackend",
    "S3Backend",
    "GCSBackend",
    "AzureBlobBackend",
    "HTTPBackend",
    "KafkaBackend",
    "SplunkBackend",
    "InMemoryBackend",
    "get_backend",
    "AuditBackendError",
    "MigrationError",
    "TamperDetectionError",
    # Optionally re-export utility classes if they are commonly used directly.
    # Otherwise, they are implicitly available via specific imports.
    "SensitiveDataFilter",
    "SimpleCircuitBreaker",
    "PersistentRetryQueue",
    "FileBackedRetryQueue",
]