# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# Export modules for easier importing
from . import audit_common, audit_crypto_factory, audit_crypto_provider, secrets

# Export commonly used classes and functions from audit_common
from .audit_common import (
    ConfigurationError,
    CryptoInitializationError,
    CryptoOperationError,
    HSMConnectionError,
    HSMError,
    HSMKeyError,
    InvalidKeyStatusError,
    KeyNotFoundError,
    RuntimeEnvironment,
    SensitiveDataFilter,
    UnsupportedAlgorithmError,
    add_sensitive_filter_to_logger,
    detect_runtime_environment,
    get_audit_logger,
    is_production_environment,
    is_supported_algorithm,
    is_test_or_dev_mode,
    SUPPORTED_ALGOS,
)

__all__ = [
    "audit_common",
    "audit_crypto_factory",
    "audit_crypto_provider",
    "secrets",
    # Exceptions
    "ConfigurationError",
    "CryptoInitializationError",
    "CryptoOperationError",
    "HSMConnectionError",
    "HSMError",
    "HSMKeyError",
    "InvalidKeyStatusError",
    "KeyNotFoundError",
    "UnsupportedAlgorithmError",
    # Utilities
    "SensitiveDataFilter",
    "RuntimeEnvironment",
    "add_sensitive_filter_to_logger",
    "detect_runtime_environment",
    "get_audit_logger",
    "is_production_environment",
    "is_supported_algorithm",
    "is_test_or_dev_mode",
    "SUPPORTED_ALGOS",
]
