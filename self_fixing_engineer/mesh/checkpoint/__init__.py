# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Checkpoint module for the Mesh framework.
Provides checkpoint management, persistence, and recovery capabilities.
"""

from .checkpoint_exceptions import (
    CheckpointAuditError,
    CheckpointBackendError,
    CheckpointError,
    CheckpointValidationError,
)
from .checkpoint_manager import (
    CheckpointManager,
    Environment,
    checkpoint_session,
    get_checkpoint_manager,
)
from .checkpoint_utils import (
    compress_json,
    decompress_json,
    deep_diff,
    hash_dict,
    scrub_data,
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
