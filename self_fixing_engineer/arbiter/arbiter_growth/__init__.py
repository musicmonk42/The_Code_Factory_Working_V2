# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Arbiter Growth module — lifecycle management, storage backends, and growth events.
"""

try:
    from .arbiter_growth_manager import ArbiterGrowthManager, PluginHook
    from .exceptions import (
        ArbiterGrowthError,
        AuditChainTamperedError,
        CircuitBreakerOpenError,
        OperationQueueFullError,
        RateLimitError,
    )
    from .models import ArbiterState, GrowthEvent
    from .storage_backends import SQLiteStorageBackend, StorageBackend

    __all__ = [
        "ArbiterGrowthManager",
        "PluginHook",
        "GrowthEvent",
        "ArbiterState",
        "StorageBackend",
        "SQLiteStorageBackend",
        "ArbiterGrowthError",
        "OperationQueueFullError",
        "RateLimitError",
        "CircuitBreakerOpenError",
        "AuditChainTamperedError",
    ]
except ImportError as e:
    import warnings
    warnings.warn(f"arbiter_growth not fully available: {e}")
    __all__ = []

