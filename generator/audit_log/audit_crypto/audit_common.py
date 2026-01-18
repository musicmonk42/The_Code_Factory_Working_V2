# audit_common.py
"""
Common utilities and classes for the audit_crypto module.

This module serves as a "leaf node" in the dependency graph - it MUST NOT import
from other audit_crypto modules to avoid circular dependencies. All other modules
in audit_crypto can safely import from this module.

Design Rationale:
    This design pattern addresses the circular dependency issues identified in
    the security audit between:
    - audit_keystore <-> audit_crypto_factory
    - audit_crypto_provider <-> audit_crypto_factory

    By centralizing shared components here, we:
    1. Break circular import chains
    2. Provide a single source of truth for common definitions
    3. Enable proper static analysis and type checking
    4. Improve testability through clear dependency boundaries

Security Considerations:
    - SensitiveDataFilter prevents accidental logging of secrets
    - Exception hierarchy provides clear error classification
    - Dev mode detection prevents insecure configurations in production

Module Dependencies:
    This module depends ONLY on:
    - Python standard library (logging, os, typing, re, enum)

    It MUST NOT import from:
    - audit_crypto_factory
    - audit_crypto_provider
    - audit_crypto_ops
    - audit_keystore
    - secrets
"""

import logging
import os
import re
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, List, Optional, Pattern, Set

# =============================================================================
# CUSTOM EXCEPTIONS
# =============================================================================
# Exception hierarchy designed for clear classification and handling of
# cryptographic operation failures. Each exception type maps to specific
# failure modes and recovery strategies.


class CryptoOperationError(Exception):
    """
    Base exception for cryptographic operation failures.

    This is the root of the crypto exception hierarchy. Catch this to handle
    any crypto-related error generically.

    Attributes:
        operation: Optional name of the failed operation
        key_id: Optional identifier of the key involved
    """

    def __init__(
        self,
        message: str,
        operation: Optional[str] = None,
        key_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.operation = operation
        self.key_id = key_id


class KeyNotFoundError(CryptoOperationError):
    """
    Exception raised when a specified key is not found.

    This indicates the key does not exist in the key store or HSM.
    Recovery: Generate a new key or use a different key ID.
    """

    pass


class InvalidKeyStatusError(CryptoOperationError):
    """
    Exception raised when a key is in an invalid status for the operation.

    Common cases:
    - Attempting to sign with a retired key
    - Using a key before it's activated
    - Using an expired key

    Recovery: Use an active key or rotate to a new key.
    """

    pass


class UnsupportedAlgorithmError(CryptoOperationError):
    """
    Exception raised for unsupported cryptographic algorithms.

    This is raised when attempting to use an algorithm not in the
    approved SUPPORTED_ALGOS list.

    Recovery: Use a supported algorithm (rsa, ecdsa, ed25519, hmac).
    """

    pass


class HSMError(CryptoOperationError):
    """
    Base exception for HSM-related errors.

    Covers all Hardware Security Module failures including connection,
    authentication, and key operation errors.
    """

    pass


class HSMConnectionError(HSMError):
    """
    Exception raised for HSM connection or session issues.

    Common causes:
    - HSM hardware not available
    - PKCS#11 library not found or incompatible
    - Network issues (for network-attached HSMs)
    - Session timeout or invalidation

    Recovery: Check HSM connectivity, restart session, verify configuration.
    """

    pass


class HSMKeyError(HSMError):
    """
    Exception raised for HSM key-related issues.

    Common causes:
    - Key not found on HSM
    - Key generation failed
    - Key destruction failed
    - Insufficient permissions for key operation

    Recovery: Verify key exists, check permissions, generate new key.
    """

    pass


class CryptoInitializationError(Exception):
    """
    Exception raised when a cryptographic provider fails to initialize.

    This is a fatal error that prevents the crypto subsystem from operating.
    The application should NOT fall back to insecure alternatives - it
    should fail fast and require operator intervention.

    Common causes:
    - Missing configuration
    - Secret manager unavailable
    - HSM not accessible
    - Invalid credentials

    Recovery: Fix configuration, ensure secret manager is accessible.
    """

    pass


class ConfigurationError(Exception):
    """
    Exception raised for errors in cryptographic configuration.

    This indicates the audit_crypto subsystem is misconfigured and
    cannot operate safely.

    Common causes:
    - Missing required configuration values
    - Invalid configuration values
    - Conflicting configuration options

    Recovery: Review and fix configuration according to documentation.
    """

    pass


# =============================================================================
# SENSITIVE DATA FILTERING
# =============================================================================
# Industry-standard log filtering to prevent accidental exposure of secrets.
# Implements defense-in-depth: even if code accidentally logs sensitive data,
# the filter will redact it before it reaches log storage.


class SensitiveDataFilter(logging.Filter):
    """
    A logging filter to redact sensitive information from log records.

    This filter provides defense-in-depth protection against accidental
    logging of sensitive data such as PINs, secrets, passwords, tokens,
    and cryptographic keys.

    The filter operates on:
    1. Log message content (record.msg)
    2. Extra fields in the log record (__dict__)
    3. Arguments in format strings (record.args)

    Security Considerations:
        - Uses compiled regex patterns for performance
        - Redaction is performed in-place to minimize copies of sensitive data
        - Pattern matching is case-insensitive for robustness
        - Multiple redaction passes ensure thorough sanitization

    Usage:
        >>> logger = logging.getLogger(__name__)
        >>> logger.addFilter(SensitiveDataFilter())
        >>> logger.info("User PIN is 1234")  # Logs: "User ***REDACTED_PIN*** is 1234"

    Thread Safety:
        This filter is thread-safe. All operations are performed on the
        log record which is local to the logging call.
    """

    # Keywords that indicate sensitive data in field names
    # Using frozenset for O(1) lookup and immutability
    SENSITIVE_FIELD_KEYWORDS: FrozenSet[str] = frozenset(
        [
            "pin",
            "secret",
            "password",
            "token",
            "key",
            "credential",
            "auth",
            "apikey",
            "api_key",
            "private",
            "cipher",
            "encrypt",
        ]
    )

    # Compiled regex patterns for message redaction
    # Pre-compiled for performance in high-throughput logging
    _MESSAGE_PATTERNS: List[tuple] = [
        (re.compile(r"\bPIN\b", re.IGNORECASE), "***REDACTED_PIN***"),
        (re.compile(r"\bsecret\b", re.IGNORECASE), "***REDACTED_SECRET***"),
        (re.compile(r"\bpassword\b", re.IGNORECASE), "***REDACTED_PASSWORD***"),
        (re.compile(r"\btoken\b", re.IGNORECASE), "***REDACTED_TOKEN***"),
        (re.compile(r"\bapi[_-]?key\b", re.IGNORECASE), "***REDACTED_APIKEY***"),
        (re.compile(r"\bcredential\b", re.IGNORECASE), "***REDACTED_CREDENTIAL***"),
    ]

    # Pattern to detect potential secret values (hex strings, base64, etc.)
    # These patterns help catch accidentally logged key material
    _VALUE_PATTERNS: List[Pattern] = [
        re.compile(r"[A-Fa-f0-9]{32,}"),  # Long hex strings (potential keys)
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),  # Base64 encoded data
    ]

    # Placeholder for redacted content
    REDACTED_VALUE: str = "***REDACTED***"

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter and redact sensitive information from the log record.

        Args:
            record: The log record to filter.

        Returns:
            bool: Always True (record is always processed, just sanitized).
        """
        # Redact message content
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = self._redact_message(record.msg)

        # Redact format string arguments
        if record.args:
            record.args = self._redact_args(record.args)

        # Redact extra fields by key name
        self._redact_extra_fields(record)

        return True

    def _redact_message(self, msg: str) -> str:
        """Apply all message redaction patterns."""
        for pattern, replacement in self._MESSAGE_PATTERNS:
            msg = pattern.sub(replacement, msg)
        return msg

    def _redact_args(self, args: Any) -> Any:
        """Redact sensitive data from format string arguments."""
        if isinstance(args, dict):
            return {
                k: self.REDACTED_VALUE if self._is_sensitive_key(k) else v
                for k, v in args.items()
            }
        elif isinstance(args, (list, tuple)):
            # For positional args, we can't know which are sensitive
            # Just redact obvious patterns in string values
            result = []
            for arg in args:
                if isinstance(arg, str):
                    for pattern in self._VALUE_PATTERNS:
                        if pattern.search(arg):
                            result.append(self.REDACTED_VALUE)
                            break
                    else:
                        result.append(arg)
                else:
                    result.append(arg)
            return tuple(result) if isinstance(args, tuple) else result
        return args

    def _redact_extra_fields(self, record: logging.LogRecord) -> None:
        """Redact sensitive extra fields in the log record."""
        for key in list(record.__dict__.keys()):
            if self._is_sensitive_key(key):
                record.__dict__[key] = self.REDACTED_VALUE

    def _is_sensitive_key(self, key: str) -> bool:
        """Check if a field name indicates sensitive data."""
        key_lower = key.lower()
        return any(keyword in key_lower for keyword in self.SENSITIVE_FIELD_KEYWORDS)


# =============================================================================
# ENVIRONMENT AND MODE DETECTION
# =============================================================================
# Functions for detecting the runtime environment to enable appropriate
# security controls. Production environments have stricter requirements.


class RuntimeEnvironment(Enum):
    """Enumeration of recognized runtime environments."""

    PRODUCTION = auto()
    STAGING = auto()
    DEVELOPMENT = auto()
    TESTING = auto()
    UNKNOWN = auto()


def detect_runtime_environment() -> RuntimeEnvironment:
    """
    Detects the current runtime environment from environment variables.

    Checks multiple common environment variable patterns used by different
    frameworks and deployment systems.

    Returns:
        RuntimeEnvironment: The detected environment.
    """
    # Check explicit environment indicators
    python_env = os.getenv("PYTHON_ENV", "").lower()
    app_env = os.getenv("APP_ENV", "").lower()
    node_env = os.getenv("NODE_ENV", "").lower()
    environment = os.getenv("ENVIRONMENT", "").lower()

    # Production indicators
    if any(env == "production" for env in [python_env, app_env, node_env, environment]):
        return RuntimeEnvironment.PRODUCTION

    # Staging indicators
    if any(
        env in ["staging", "stage", "pre-prod", "preprod"]
        for env in [python_env, app_env, node_env, environment]
    ):
        return RuntimeEnvironment.STAGING

    # Testing indicators
    if os.getenv("PYTEST_CURRENT_TEST"):
        return RuntimeEnvironment.TESTING
    if os.getenv("RUNNING_TESTS", "").lower() in ("true", "1"):
        return RuntimeEnvironment.TESTING

    # Development indicators
    if any(
        env in ["development", "dev", "local"]
        for env in [python_env, app_env, node_env, environment]
    ):
        return RuntimeEnvironment.DEVELOPMENT
    if os.getenv("AUDIT_LOG_DEV_MODE", "").lower() == "true":
        return RuntimeEnvironment.DEVELOPMENT
    if os.getenv("DEV_MODE", "").lower() in ("true", "1"):
        return RuntimeEnvironment.DEVELOPMENT

    return RuntimeEnvironment.UNKNOWN


def is_test_or_dev_mode() -> bool:
    """
    Returns True when running in a non-production environment.

    This function is used throughout the audit_crypto module to:
    1. Allow relaxed validation in development/testing
    2. Enable dummy crypto providers for testing
    3. Skip production-only security checks

    SECURITY WARNING:
        Code that uses this function to bypass security controls MUST
        ensure those controls cannot be bypassed in production by simply
        setting environment variables. The production guardrails should
        be defense-in-depth, not solely reliant on this check.

    Returns:
        bool: True if running in test/development mode, False otherwise.
    """
    env = detect_runtime_environment()
    return env in (
        RuntimeEnvironment.DEVELOPMENT,
        RuntimeEnvironment.TESTING,
        RuntimeEnvironment.UNKNOWN,  # Default to permissive for backwards compatibility
    )


def is_production_environment() -> bool:
    """
    Returns True when running in production or staging.

    This is a stricter check than `not is_test_or_dev_mode()` because
    it explicitly requires production indicators, not just absence of
    dev indicators.

    Returns:
        bool: True if running in production or staging, False otherwise.
    """
    env = detect_runtime_environment()
    return env in (RuntimeEnvironment.PRODUCTION, RuntimeEnvironment.STAGING)


# =============================================================================
# LOGGING UTILITIES
# =============================================================================


def add_sensitive_filter_to_logger(logger: logging.Logger) -> None:
    """
    Adds the SensitiveDataFilter to a logger if not already present.

    This function is idempotent - safe to call multiple times on the
    same logger without adding duplicate filters.

    Args:
        logger: The logger to add the filter to.
    """
    filter_exists = any(isinstance(f, SensitiveDataFilter) for f in logger.filters)
    if not filter_exists:
        logger.addFilter(SensitiveDataFilter())


def get_audit_logger(name: str) -> logging.Logger:
    """
    Gets a logger pre-configured with the SensitiveDataFilter.

    Use this function instead of logging.getLogger() for any logger
    that may handle sensitive audit data.

    Args:
        name: The name for the logger (typically __name__).

    Returns:
        logging.Logger: A logger with sensitive data filtering enabled.
    """
    logger = logging.getLogger(name)
    add_sensitive_filter_to_logger(logger)
    return logger


# =============================================================================
# SUPPORTED ALGORITHMS
# =============================================================================
# Centralized list of supported cryptographic algorithms.
# Defined here to avoid import cycles when checking algorithm support.

SUPPORTED_ALGOS: FrozenSet[str] = frozenset(["rsa", "ecdsa", "ed25519", "hmac"])
"""
Supported cryptographic algorithms for audit log signing.

- rsa: RSA-PSS with SHA-256 (2048-bit minimum key size)
- ecdsa: ECDSA with P-256 curve and SHA-256
- ed25519: EdDSA with Curve25519
- hmac: HMAC-SHA256 (fallback only, not for primary signing)

Note: HMAC should only be used as a fallback when HSM/software
signing is unavailable. It does not provide non-repudiation.
"""


def is_supported_algorithm(algo: str) -> bool:
    """
    Check if an algorithm is in the supported list.

    Args:
        algo: Algorithm name to check.

    Returns:
        bool: True if the algorithm is supported.
    """
    return algo.lower() in SUPPORTED_ALGOS
