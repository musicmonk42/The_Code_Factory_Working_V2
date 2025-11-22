"""
Checkpoint module for the Mesh framework.
Provides checkpoint management, persistence, and recovery capabilities.
"""

from .checkpoint_manager import (
    CheckpointManager,
    get_checkpoint_manager,
    checkpoint_session,
    Environment,
)

from .checkpoint_exceptions import (
    CheckpointError,
    CheckpointAuditError,
    CheckpointBackendError,
    CheckpointValidationError,
)

from .checkpoint_utils import (
    hash_dict,
    compress_json,
    decompress_json,
    scrub_data,
    deep_diff,
)

__all__ = [
    # Manager
    "CheckpointManager",
    "get_checkpoint_manager",
    "checkpoint_session",
    "Environment",
    # Exceptions
    "CheckpointError",
    "CheckpointAuditError",
    "CheckpointBackendError",
    "CheckpointValidationError",
    # Utils
    "hash_dict",
    "compress_json",
    "decompress_json",
    "scrub_data",
    "deep_diff",
]
