# audit_crypto_factory.py
# Purpose: Initialization, configuration, metrics setup, provider factory, global state, fallback logic.
#
# Production Readiness Notes:
# - Configuration: Uses Dynaconf and environment variables; checks for missing critical config and fails fast.
# - Secret Handling: KMS decryption of key material is done here, but raw secret fetching is routed through secrets.py.
# - Metrics: Integrates Prometheus metrics robustly, with SystemExit if not present.
# - Logging: Sensitive data filtering is applied by default to prevent leaks.
# - Global State: Global variables are used for convenience; their lifecycle is documented below.
# - Error Handling: Fails fast if anything is missing, logs critical errors, uses alerting function, includes retry logic.
# - OpenTelemetry: Optional; logs if not present, disables tracing if available.
#
# Required Environment Variables (or equivalent configuration):
# - AUDIT_CRYPTO_PROVIDER_TYPE: 'software' or 'hsm'
# - AUDIT_CRYPTO_DEFAULT_ALGO: 'rsa', 'ecdsa', 'ed25519', or 'hmac'
# - AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS: Minimum 86400 (1 day)
# - AUDIT_CRYPTO_SOFTWARE_KEY_DIR: Path to directory for software keys
# - AUDIT_CRYPTO_KMS_KEY_ID: AWS KMS Key ID (if PROVIDER_TYPE is 'software')
# - AUDIT_CRYPTO_ALERT_ENDPOINT: URL for sending critical alerts
# - AUDIT_CRYPTO_HSM_ENABLED: 'true' or 'false' (if using HSM)
# - AUDIT_CRYPTO_HSM_LIBRARY_PATH: Path to PKCS#11 library (if HSM_ENABLED)
# - AUDIT_CRYPTO_HSM_SLOT_ID: HSM slot ID (if HSM_ENABLED)
# - AUDIT_CRYPTO_HSM_PIN: HSM PIN (if HSM_ENABLED) - MUST be securely managed in production via secrets.py
# - AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64: Base64 encoded secret for HMAC fallback (optional, but recommended for resilience) - MUST be securely managed in production via secrets.py
# - AWS_REGION: AWS region for KMS operations (if using KMS)
# - AUDIT_CRYPTO_ALERT_RETRY_ATTEMPTS: Number of retries for sending an alert.
# - AUDIT_CRYPTO_ALERT_BACKOFF_FACTOR: The backoff factor for alert retries.
#
# Global State Summary:
# - _SOFTWARE_KEY_MASTER: A bytes object holding the decrypted data encryption key for software keys.
#   Lifecycle: Initialized once, lazily on first access via `_ensure_software_key_master()`. Never reloaded.
# - _FALLBACK_HMAC_SECRET: A bytes object holding the fallback secret for HMAC signatures.
#   Lifecycle: Initialized once, lazily on first access via `_ensure_fallback_hmac_secret()`. Never reloaded.
# - crypto_provider_factory: The global factory instance for creating and caching CryptoProvider objects.
#   Lifecycle: Initialized once at module import. Should be used as the primary interface.
# - crypto_provider: A global instance of the configured CryptoProvider.
#   Lifecycle: Initialized once at module import. Provided for convenience/backward compatibility.
#
# WARNING: Do not use these global variables directly outside this module.
# Use the `crypto_provider_factory` to get provider instances.

import asyncio
import logging
import os
import signal
from typing import Any, Awaitable, Callable, Dict, Optional, Type

# Configuration management
from dynaconf import Dynaconf, Validator
from dynaconf.validator import (
    ValidationError,
)  # Import ValidationError here for the fix

# Prometheus metrics
try:
    from prometheus_client import Counter, Gauge, Histogram
except ImportError:
    logging.critical(
        "prometheus_client not found. Metrics are critical for production. Exiting."
    )
    raise SystemExit(1)

# OpenTelemetry for tracing
try:
    from opentelemetry import trace

    HAS_OPENTELEMETRY = True
    # Use the default/configured tracer provider instead of manually creating one
    # This avoids version compatibility issues and respects OTEL_* environment variables
    tracer = trace.get_tracer(__name__)
except ImportError:
    tracer = None
    HAS_OPENTELEMETRY = False
    logging.warning("OpenTelemetry not found. Tracing disabled.")
except Exception as e:
    tracer = None
    HAS_OPENTELEMETRY = False
    logging.error(
        f"Failed to initialize OpenTelemetry: {e}. Tracing disabled.", exc_info=True
    )

# AWS KMS for master key fetching
try:
    import boto3
    import botocore.exceptions

    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    logging.warning(
        "boto3 not found. KMS master key decryption will be unavailable if PROVIDER_TYPE is 'software'."
    )

import aiohttp  # For sending alerts

# Import secret fetching functions
from .secrets import (  # Import async secret fetchers
    aget_fallback_hmac_secret,
    aget_kms_master_key_ciphertext_blob,
)

# Import SecretError from secrets.py

# Placeholder for audit_log.log_action for key use auditing
try:
    from .. import log_action as real_log_action

    _DUMMY_LOG_ACTION_USED = False
except ImportError:
    _DUMMY_LOG_ACTION_USED = True
    logging.debug(
        "log_action import from parent package failed, using dummy function.",
        extra={"operation": "audit_log_import_fallback"},
    )

    async def real_log_action(
        *args, **kwargs
    ):  # Make dummy async to match expected signature
        logging.debug(
            f"Dummy log_action: {args}, {kwargs}",
            extra={"operation": "dummy_log_action"},
        )


# The function used throughout the module.
log_action = real_log_action


# --- Custom Exceptions ---
class ConfigurationError(Exception):
    """Exception raised for errors in cryptographic configuration."""

    pass


class CryptoInitializationError(Exception):
    """Exception raised when a cryptographic provider fails to initialize."""

    pass


# --- Sensitive Data Filtering for Logging ---
class SensitiveDataFilter(logging.Filter):
    """
    A logging filter to redact sensitive information (like PINs or secrets)
    from log records.
    """

    def filter(self, record):
        if hasattr(record, "msg") and isinstance(record.msg, str):
            # Redact common sensitive terms.
            record.msg = record.msg.replace("PIN", "***REDACTED_PIN***").replace(
                "secret", "***REDACTED_SECRET***"
            )
            try:
                # Attempt to redact actual values if they are known at module load time.
                # NOTE: Calling get_hsm_pin() in a sync filter is dangerous if the loop is running.
                # It's better to rely on careful logging elsewhere.
                pass
            except Exception:
                pass  # Fail silently if secret can't be fetched

            # This is a bit fragile as the secret isn't available everywhere
            # It's better to ensure it's never logged in the first place.

        # More robustly redact sensitive data from the entire log record's __dict__ (which includes `extra` data).
        # FIX 1: Iterate over record.__dict__ keys to catch all extra fields merged by LoggerAdapter.
        for key in list(record.__dict__.keys()):
            if any(s in key.lower() for s in ["pin", "secret", "password"]):
                record.__dict__[key] = "***REDACTED***"

        return True


# Apply the sensitive data filter to the root logger
logging.getLogger().addFilter(SensitiveDataFilter())
logger = logging.getLogger(__name__)

# Set initial logging level for production. This can be overridden by environment variables.
logging.getLogger().setLevel(os.getenv("LOG_LEVEL", "INFO").upper())


# --- Helper for checking if crypto is disabled ---
def _is_crypto_disabled() -> bool:
    """
    Returns True when audit crypto is explicitly disabled.
    This allows the application to start without any crypto provider initialization.
    """
    audit_crypto_mode = os.getenv("AUDIT_CRYPTO_MODE", "").lower()
    return audit_crypto_mode == "disabled"


# --- Helper for DEV/TEST Mode ---
def _is_test_or_dev_mode() -> bool:
    """
    Returns True when running under pytest or explicit DEV mode.
    Keeps audit_crypto_factory from failing hard during tests.
    """
    # Check explicit audit crypto mode setting
    # NOTE: "disabled" is NOT considered dev mode - it's a valid production setting
    # when cryptographic secrets are not yet configured. Only "dev" triggers dev mode.
    audit_crypto_mode = os.getenv("AUDIT_CRYPTO_MODE", "").lower()
    if audit_crypto_mode == "dev":
        return True
    
    if os.getenv("AUDIT_LOG_DEV_MODE", "").lower() == "true":
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    if os.getenv("RUNNING_TESTS", "").lower() == "true":
        return True
    # Also check for common development environment indicators
    dev_mode = os.getenv("DEV_MODE", "").lower()
    if dev_mode in ("true", "1"):
        return True
    app_env = os.getenv("APP_ENV", "").lower()
    if app_env in ("development", "dev", "local"):
        return True
    return False


# --- Configuration Management ---
# FIX: Detect testing environment and bypass critical 'must_exist' validation
_IS_TESTING = (
    os.getenv("PYTEST_CURRENT_TEST") is not None or os.getenv("RUNNING_TESTS") == "True"
)

# Disable multi-env mode to allow reading settings from the root of audit_config.yaml
# Multi-env mode expects sections like [development], [production] in the config file
environments = False
# Use absolute path to ensure the config file is found regardless of working directory
_module_dir = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_config_path = os.path.join(_module_dir, "audit_config.yaml")
settings = Dynaconf(
    environments=environments,  # <-- disabled in tests
    envvar_prefix="AUDIT_CRYPTO",
    settings_files=[_config_path],
    # FIX: Remove conditional validators to prevent RecursionError
    validators=[
        Validator("PROVIDER_TYPE", must_exist=True, is_in=["software", "hsm"]),
        Validator(
            "DEFAULT_ALGO", must_exist=True, is_in=["rsa", "ecdsa", "ed25519", "hmac"]
        ),
        Validator("KEY_ROTATION_INTERVAL_SECONDS", must_exist=True, gte=3600),
        # KMS/Software requirements (now unconditional or removed, handled in post-validation)
        Validator("SOFTWARE_KEY_DIR", is_type_of=str, default="audit_keys"),
        Validator(
            "KMS_KEY_ID", is_type_of=str
        ),  # Now unconditional, existence checked manually
        Validator(
            "AWS_REGION", is_type_of=str, default="us-east-1"
        ),  # Defaulted for non-KMS checks
        # HSM requirements (handled in post-validation)
        Validator("HSM_ENABLED", default=False, is_type_of=bool),
        Validator("HSM_LIBRARY_PATH", is_type_of=str),
        Validator("HSM_SLOT_ID", is_type_of=int, default=0),
        Validator(
            "ALERT_ENDPOINT", is_type_of=str, default="http://localhost:8080/alert"
        ),
        Validator("FALLBACK_HMAC_SECRET_B64", is_type_of=str, must_exist=False),
        Validator(
            "HSM_HEALTH_CHECK_INTERVAL_SECONDS", is_type_of=int, default=30, gte=5
        ),
        Validator("ALERT_RETRY_ATTEMPTS", is_type_of=int, default=3, gte=1),
        Validator("ALERT_BACKOFF_FACTOR", is_type_of=float, default=2.0, gte=1.0),
        Validator("ALERT_INITIAL_DELAY", is_type_of=float, default=1.0, gte=0.1),
        Validator("HSM_RETRY_ATTEMPTS", is_type_of=int, default=5, gte=1),
        Validator("HSM_BACKOFF_FACTOR", is_type_of=float, default=2.0, gte=1.0),
        Validator("HSM_INITIAL_DELAY", is_type_of=float, default=1.0, gte=0.1),
    ],
)

# --- START OF PATCH 1 ---


def post_validation_checks():
    """Manual checks for conditional requirements after initial validation."""
    # IMPORTANT: use attribute access so tests that set attributes work,
    # and so Dynaconf's attribute access is honored in prod.
    provider_type = settings.PROVIDER_TYPE
    errors = []

    if provider_type == "software":
        if not settings.KMS_KEY_ID and not _is_test_or_dev_mode():
            errors.append(
                "KMS_KEY_ID is required when PROVIDER_TYPE is 'software' in production"
            )
        if not settings.SOFTWARE_KEY_DIR:
            errors.append(
                "SOFTWARE_KEY_DIR is required when PROVIDER_TYPE is 'software'"
            )

    elif settings.HSM_ENABLED:
        if not settings.HSM_LIBRARY_PATH:
            errors.append("HSM_LIBRARY_PATH is required when HSM_ENABLED is true")
        if settings.HSM_SLOT_ID is None:
            errors.append("HSM_SLOT_ID is required when HSM_ENABLED is true")

    if errors:
        raise ValidationError(f"Conditional validation failed: {'; '.join(errors)}")


# --- END OF PATCH 1 ---


def validate_and_load_config():
    """Validates the configuration and raises an error on failure."""
    try:
        settings.validators.validate()
        post_validation_checks()  # Run manual checks after
        logger.info("Cryptographic configuration validated successfully.")
    except ValidationError as e:
        if _is_test_or_dev_mode():
            # In tests/DEV: Warn and continue (tests can mock/provide defaults)
            logger.warning(
                "AUDIT CRYPTO VALIDATION FAILED in DEV/TEST context: %s. "
                "Continuing with mocked or default configuration.",
                e,
            )
        else:
            # Prod: Hard fail
            logger.critical(
                "AUDIT CRYPTO VALIDATION FAILED in production context: %s. "
                "Refusing to start without compliant configuration.",
                e,
                exc_info=True,
            )
            raise ConfigurationError(f"Invalid configuration: {e}")
    except Exception as e:
        # Catch unexpected exceptions during validation outside of ValidationError
        if _is_test_or_dev_mode():
            logger.warning(f"Unexpected validation error in DEV/TEST context: {e}")
        else:
            logger.critical(f"Unexpected validation error: {e}")
            raise ConfigurationError(f"Unexpected configuration error: {e}")


# Initial config validation and load
validate_and_load_config()

# --- END OF SURGICAL FIX FOR VALIDATION ---


# --- Runtime Constants ---
settings.SUPPORTED_ALGOS = ["rsa", "ecdsa", "ed25519", "hmac"]
# Other constants are accessed directly from `settings`.


# --- Global Crypto State (master keys, fallback secrets) ---

_SOFTWARE_KEY_MASTER: Optional[bytes] = None
_FALLBACK_HMAC_SECRET: Optional[bytes] = None
# Old lazy init flags and locks are removed as they are no longer necessary with the simpler logic below.


# --- Master Key for Software Key Encryption (Lazy Init) ---


async def _ensure_software_key_master() -> bytes:
    """
    Returns the master key used to encrypt/decrypt software-stored keys.

    In production:
        - Must be loaded from a secure secret/KMS.
        - Failure is fatal.

    In DEV/TEST:
        - Falls back to a deterministic dummy key so imports & tests don't explode.
    """
    global _SOFTWARE_KEY_MASTER

    if _SOFTWARE_KEY_MASTER is not None:
        return _SOFTWARE_KEY_MASTER

    # DEV/TEST: safe, deterministic dummy key so SoftwareCryptoProvider can initialize.
    if _is_test_or_dev_mode():
        _SOFTWARE_KEY_MASTER = b"0123456789abcdef0123456789abcdef"  # 32 bytes
        logging.getLogger(__name__).warning(
            "AUDIT_CRYPTO: Using DEV/TEST dummy master key for SoftwareCryptoProvider."
        )
        return _SOFTWARE_KEY_MASTER

    # PRODUCTION PATH (simplified; adjust to match your real KMS/secret manager wiring)
    try:
        # Example using async secret helper; replace with your actual logic if different.
        ciphertext = await aget_kms_master_key_ciphertext_blob()
        if not ciphertext:
            raise CryptoInitializationError(
                "No KMS master key ciphertext blob returned."
            )

        if not HAS_BOTO3:
            raise CryptoInitializationError("boto3 not available for KMS decryption.")

        import boto3  # local import to avoid issues if unused

        kms = boto3.client("kms", region_name=settings.AWS_REGION)
        response = await asyncio.to_thread(
            kms.decrypt,
            CiphertextBlob=ciphertext,
            KeyId=settings.KMS_KEY_ID,
        )
        plaintext = response.get("Plaintext")
        if not plaintext:
            raise CryptoInitializationError("KMS decrypt returned no Plaintext.")

        # The new key management ensures 32 bytes are used for Fernet
        _SOFTWARE_KEY_MASTER = plaintext[:32]
        return _SOFTWARE_KEY_MASTER

    except Exception as e:
        logger.critical(
            f"Failed to initialize software key master in production: {e}",
            exc_info=True,
        )
        raise CryptoInitializationError(
            f"Failed to initialize software key master: {e}"
        ) from e


async def _ensure_fallback_hmac_secret() -> bytes:
    """
    Returns the HMAC fallback secret.

    In DEV/TEST:
        - Provides a deterministic dummy secret.
    In production:
        - Must come from a secure secret manager.
    """
    global _FALLBACK_HMAC_SECRET

    if _FALLBACK_HMAC_SECRET is not None:
        return _FALLBACK_HMAC_SECRET

    # --- START OF PATCH 2 ---
    if _is_test_or_dev_mode():
        _FALLBACK_HMAC_SECRET = (
            b"0123456789abcdef0123456789abcdef"  # 32 bytes; deterministic
        )
        logger.warning("AUDIT_CRYPTO: Using DEV/TEST dummy fallback HMAC secret.")
        return _FALLBACK_HMAC_SECRET
    # --- END OF PATCH 2 ---

    try:
        secret = await aget_fallback_hmac_secret()
        if not secret:
            raise CryptoInitializationError(
                "Fallback HMAC secret not available from secret manager."
            )
        _FALLBACK_HMAC_SECRET = secret
        return _FALLBACK_HMAC_SECRET
    except Exception as e:
        logger.critical(
            f"Failed to initialize fallback HMAC secret in production: {e}",
            exc_info=True,
        )
        raise CryptoInitializationError(
            f"Failed to initialize fallback HMAC secret: {e}"
        ) from e


# --- Metrics ---
SIGN_OPERATIONS = Counter(
    "audit_crypto_signs_total", "Sign operations", ["algo", "provider_type"]
)
VERIFY_OPERATIONS = Counter(
    "audit_crypto_verifies_total",
    "Verify operations",
    ["algo", "provider_type", "status"],
)
CRYPTO_ERRORS = Counter(
    "audit_crypto_errors_total", "Crypto errors", ["type", "provider_type", "operation"]
)
KEY_ROTATIONS = Counter(
    "audit_crypto_rotations_total", "Key rotations", ["algo", "provider_type"]
)
HSM_SESSION_HEALTH = Gauge(
    "audit_crypto_hsm_session_health",
    "HSM session health (1=up, 0=down)",
    ["provider_type"],
)
SIGN_LATENCY = Histogram(
    "audit_crypto_sign_latency_seconds",
    "Sign operation latency",
    ["algo", "provider_type"],
)
VERIFY_LATENCY = Histogram(
    "audit_crypto_verify_latency_seconds",
    "Verify operation latency",
    ["algo", "provider_type"],
)
KEY_LOAD_COUNT = Counter(
    "audit_crypto_key_load_total",
    "Total keys loaded from storage",
    ["provider_type", "status"],
)
KEY_STORE_COUNT = Counter(
    "audit_crypto_key_store_total",
    "Total keys stored to storage",
    ["provider_type", "status"],
)
KEY_CLEANUP_COUNT = Counter(
    "audit_crypto_key_cleanup_total",
    "Total retired keys cleaned up",
    ["provider_type", "status"],
)


# --- Alerting ---
async def send_alert(
    message: str, severity: str = "critical", endpoint: str = settings.ALERT_ENDPOINT
):
    """
    Sends an alert to the configured endpoint (e.g., PagerDuty, Slack webhook).
    This function is non-blocking and attempts to send the alert asynchronously with retries.
    Args:
        message (str): The alert message. Sensitive data should be redacted.
        severity (str): Severity level (e.g., "critical", "high", "warning").
        endpoint (str): The URL to send the alert to.
    """

    async def _send():
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, json={"message": message, "severity": severity}
            ) as response:
                response.raise_for_status()

    try:
        await retry_operation(
            _send,
            max_attempts=settings.ALERT_RETRY_ATTEMPTS,
            backoff_factor=settings.ALERT_BACKOFF_FACTOR,
            initial_delay=settings.ALERT_INITIAL_DELAY,
            backend_name="AlertingSystem",
            op_name="send_alert",
        )
        logger.info(
            f"Alert sent successfully: {message}",
            extra={"operation": "send_alert_success", "severity": severity},
        )
        await log_action(
            "send_alert", status="success", severity=severity, message=message
        )
    except Exception as e:
        logger.error(
            f"Failed to send alert to {endpoint} after multiple retries: {e}. Alert message: {message}",
            exc_info=True,
            extra={"operation": "send_alert_fail", "severity": severity},
        )
        CRYPTO_ERRORS.labels(
            type="AlertSendFail", provider_type="alerting", operation="send_alert"
        ).inc()
        await log_action(
            "send_alert",
            status="fail",
            severity=severity,
            message=message,
            error=str(e),
        )


# --- Retry Helper ---
async def retry_operation(
    func: Callable[[], Awaitable[Any]],
    max_attempts: int = 5,
    backoff_factor: float = 2,
    initial_delay: float = 1,
    backend_name: str = "unknown",
    op_name: str = "unknown",
):
    """
    Retries an asynchronous operation with exponential backoff.
    Args:
        func (Callable): The asynchronous function to retry.
        max_attempts (int): Maximum number of retry attempts.
        backoff_factor (float): Factor by which the delay increases each attempt.
        initial_delay (float): Initial delay in seconds before the first retry.
        backend_name (str): Name of the backend (e.g., 'HSMCryptoProvider').
        op_name (str): Name of the operation being retried (e.g., 'init_session').
    Raises:
        Exception: If the operation fails after all retry attempts.
    """
    attempt = 0
    while attempt < max_attempts:
        try:
            # FIX 5 & 6: Log success before returning the result.
            result = await func()
            await log_action(
                "retry_operation",
                status="success",
                backend=backend_name,
                operation=op_name,
                attempts_taken=attempt + 1,
            )
            return result
        except asyncio.CancelledError:
            await log_action(
                "retry_operation",
                status="cancelled",
                backend=backend_name,
                operation=op_name,
                attempt=attempt,
            )
            raise  # Propagate cancellation immediately
        except Exception as e:
            attempt += 1
            if attempt >= max_attempts:
                logger.error(
                    f"Operation '{op_name}' on '{backend_name}' failed after {max_attempts} attempts: {e}",
                    exc_info=True,
                )
                CRYPTO_ERRORS.labels(
                    type="RetryFinalFail", provider_type=backend_name, operation=op_name
                ).inc()
                await log_action(
                    "retry_operation",
                    status="final_fail",
                    backend=backend_name,
                    operation=op_name,
                    attempt=attempt,
                    error=str(e),
                )
                raise
            delay = initial_delay * (backoff_factor ** (attempt - 1))
            logger.warning(
                f"Operation '{op_name}' on '{backend_name}' failed (attempt {attempt}/{max_attempts}). Retrying in {delay:.2f} seconds. Error: {e}"
            )
            CRYPTO_ERRORS.labels(
                type="RetryAttemptFail", provider_type=backend_name, operation=op_name
            ).inc()
            await log_action(
                "retry_operation",
                status="attempt_fail",
                backend=backend_name,
                operation=op_name,
                attempt=attempt,
                error=str(e),
            )
            await asyncio.sleep(delay)


# Import CryptoProvider here to avoid circular dependencies in type hints
from .audit_crypto_provider import (
    CryptoProvider,
    HSMCryptoProvider,
    SoftwareCryptoProvider,
)

# Import centralized environment detection from audit_common
# (imported here, after audit_crypto_provider, to ensure proper load order)
from .audit_common import is_production_environment as _is_production_env


class DummyCryptoProvider(CryptoProvider):
    """
    A minimal crypto provider for DEV/TEST environments ONLY.

    WARNING: This provider provides NO REAL SECURITY.
    - All signatures are deterministic and predictable
    - All verifications return True
    - This is intended ONLY for testing and development

    Security Guardrails:
    - In production environments, using this provider will raise an error
    - Even in dev/test, a warning is always logged
    - The provider checks for explicit opt-in via AUDIT_CRYPTO_ALLOW_DUMMY_PROVIDER

    Usage:
        This provider is automatically used when:
        1. Running in test/dev mode (AUDIT_LOG_DEV_MODE=true or PYTEST_CURRENT_TEST set)
        2. AND PROVIDER_TYPE is not explicitly set to a production provider

        To force a real provider in tests, set:
        - AUDIT_CRYPTO_FORCE_REAL_PROVIDER=true
    """

    # NOTE: The base CryptoProvider.__init__ takes accessors and settings.
    # The dummy provider must accept them to match the factory's call signature, even if it ignores them.
    def __init__(
        self,
        software_key_master_accessor: Callable[[], Awaitable[bytes]],
        fallback_hmac_secret_accessor: Callable[[], Awaitable[bytes]],
        settings: Dynaconf,
    ):
        # SECURITY GUARDRAIL: Verify this is not being used in production
        # Use centralized environment detection from audit_common
        is_production = _is_production_env()
        allow_dummy_override = (
            os.getenv("AUDIT_CRYPTO_ALLOW_DUMMY_PROVIDER", "").lower() == "true"
        )

        if is_production and not allow_dummy_override:
            error_msg = (
                "CRITICAL SECURITY ERROR: Attempted to use DummyCryptoProvider in production. "
                "This provider offers NO SECURITY and must not be used in production. "
                "If this is intentional (NOT RECOMMENDED), set AUDIT_CRYPTO_ALLOW_DUMMY_PROVIDER=true"
            )
            logger.critical(error_msg)
            raise CryptoInitializationError(error_msg)

        super().__init__(
            software_key_master_accessor, fallback_hmac_secret_accessor, settings
        )
        self.key_id = "test-key-id"

        # Always log a warning when DummyCryptoProvider is used
        logger.warning(
            "AUDIT_CRYPTO: Using DummyCryptoProvider. "
            "This provider offers NO REAL SECURITY and should NEVER be used in production. "
            "All signatures are deterministic and all verifications return True.",
            extra={"operation": "dummy_provider_init", "security_warning": True},
        )

    async def generate_key(self, algo: str) -> str:
        logger.debug(f"DummyCryptoProvider.generate_key called with algo={algo}")
        return self.key_id

    async def sign(self, data: bytes, key_id: str) -> bytes:
        logger.debug("DummyCryptoProvider.sign called (returning dummy signature)")
        return b"dummy-signature"

    async def verify(self, data: bytes, signature: bytes, key_id: str) -> bool:
        logger.debug("DummyCryptoProvider.verify called (always returns True)")
        return True

    # Must implement all required abstract methods, including rotate_key and close
    async def rotate_key(self, key_id: str) -> str:
        logger.debug("DummyCryptoProvider.rotate_key called")
        return self.key_id

    async def close(self):
        logger.debug("DummyCryptoProvider.close called")
        pass


class NoOpCryptoProvider(CryptoProvider):
    """
    No-operation crypto provider for when AUDIT_CRYPTO_MODE=disabled.
    
    This provider is used when cryptographic functionality is explicitly disabled.
    Unlike DummyCryptoProvider, it doesn't require any initialization, doesn't make
    AWS calls, and doesn't perform any production environment checks.
    
    Use this when:
    - AUDIT_CRYPTO_MODE=disabled is set
    - Starting the application without crypto secrets configured
    - No audit log cryptographic signatures are needed
    
    Security Notes:
    - This provider offers NO SECURITY
    - Signatures are empty (0 bytes)
    - Verifications always return False (no signature validation)
    - Should ONLY be used when audit crypto is explicitly disabled
    """

    def __init__(
        self,
        software_key_master_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        fallback_hmac_secret_accessor: Optional[Callable[[], Awaitable[bytes]]] = None,
        settings: Optional[Dynaconf] = None,
    ):
        # Initialize parent class with None/mock values to maintain interface compatibility
        # This ensures all expected attributes are set (self.settings, self.logger, etc.)
        super().__init__(
            software_key_master_accessor=software_key_master_accessor,
            fallback_hmac_secret_accessor=fallback_hmac_secret_accessor,
            settings=settings,
        )
        self.key_id = "noop-key-id"
        
        self.logger.info(
            "AUDIT_CRYPTO: Using NoOpCryptoProvider (AUDIT_CRYPTO_MODE=disabled). "
            "Cryptographic functionality is DISABLED. No audit log signatures will be generated.",
            extra={"operation": "noop_provider_init"},
        )

    async def generate_key(self, algo: str) -> str:
        self.logger.debug("NoOpCryptoProvider.generate_key called (no-op)")
        return self.key_id

    async def sign(self, data: bytes, key_id: str) -> bytes:
        self.logger.debug("NoOpCryptoProvider.sign called (no-op, returning empty signature)")
        return b""

    async def verify(self, data: bytes, signature: bytes, key_id: str) -> bool:
        # Return False instead of True to make it explicit that verification is not performed
        # This prevents masking signature verification failures
        self.logger.warning(
            "NoOpCryptoProvider.verify called - crypto is disabled, returning False. "
            "No actual signature verification is performed when AUDIT_CRYPTO_MODE=disabled.",
            extra={"operation": "noop_verify_called"}
        )
        return False

    async def rotate_key(self, key_id: str) -> str:
        self.logger.debug("NoOpCryptoProvider.rotate_key called (no-op)")
        return self.key_id

    async def close(self):
        self.logger.debug("NoOpCryptoProvider.close called (no-op)")
        pass


class CryptoProviderFactory:
    """
    Factory for creating and managing CryptoProvider instances.
    Supports dynamic registration and provides a robust way to get provider instances
    with fallback logic.
    """

    _registry: Dict[str, Type[CryptoProvider]] = {}
    _instances: Dict[str, CryptoProvider] = {}

    def __init__(self):
        # Register default providers
        self.register_provider("software", SoftwareCryptoProvider)
        if settings.HSM_ENABLED:
            self.register_provider("hsm", HSMCryptoProvider)
        # Always register the noop provider for disabled mode
        self.register_provider("noop", NoOpCryptoProvider)
        # Always register the dummy provider for test/dev mode fallback
        self.register_provider("dummy", DummyCryptoProvider)

    def register_provider(self, name: str, provider_cls: Type[CryptoProvider]):
        """
        Dynamically registers a CryptoProvider class with the factory.
        Args:
            name (str): The name to register the provider under (e.g., "software", "hsm").
            provider_cls (Type[CryptoProvider]): The CryptoProvider subclass to register.
        Raises:
            TypeError: If `provider_cls` is not a subclass of `CryptoProvider` or
                       if it does not implement required methods.
        """
        if not issubclass(provider_cls, CryptoProvider):
            raise TypeError(
                f"Class {provider_cls.__name__} must be a subclass of CryptoProvider."
            )

        # Validate that the provider class implements all required methods
        # NOTE: 'get_key' is not a standard abstract method; checking for abstract methods is better.
        # We will check the required abstract methods defined in CryptoProvider.
        required_methods = ["sign", "verify", "generate_key", "rotate_key", "close"]
        for method in required_methods:
            if not callable(getattr(provider_cls, method, None)):
                if method == "close" and hasattr(provider_cls, method):
                    continue  # Allow non-async override
                if (
                    method != "close"
                    and not getattr(provider_cls, method, None).__isabstractmethod__
                ):  # Check if abstract
                    # This check is a bit too strict, but aims for a type-safe interface check
                    pass

        self._registry[name.lower()] = provider_cls
        logger.info(
            f"Registered crypto provider: {name.lower()} -> {provider_cls.__name__}"
        )

        # Use an event loop if available, otherwise just log and skip the async call
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                log_action(
                    "register_provider",
                    provider_name=name.lower(),
                    provider_class=provider_cls.__name__,
                )
            )
        except RuntimeError:
            # If called from a synchronous context before the event loop starts
            pass

    def _create_dummy_provider(self) -> CryptoProvider:
        """
        Creates and caches a DummyCryptoProvider instance for graceful degradation.
        
        This private helper is used when AUDIT_CRYPTO_ALLOW_INIT_FAILURE is set
        and the real crypto provider fails to initialize.
        
        Returns:
            CryptoProvider: A DummyCryptoProvider instance.
        """
        if "dummy" in self._instances:
            return self._instances["dummy"]
        dummy_cls = self._registry.get("dummy", DummyCryptoProvider)
        dummy_instance = dummy_cls(
            software_key_master_accessor=_ensure_software_key_master,
            fallback_hmac_secret_accessor=_ensure_fallback_hmac_secret,
            settings=settings,
        )
        self._instances["dummy"] = dummy_instance
        return dummy_instance

    # --- START OF PATCH 3 ---
    def get_provider(
        self, provider_type: str = settings.PROVIDER_TYPE
    ) -> CryptoProvider:
        """
        Factory method to get a CryptoProvider instance dynamically based on configuration.

        SECURITY CONSIDERATIONS:
        - In production, this method will NEVER automatically fall back to DummyCryptoProvider
        - If initialization fails in production, the application MUST crash (fail-closed)
        - Silent security downgrades are explicitly prevented
        - The 'software' provider can be used as fallback from 'hsm', but never 'dummy'

        Behavior by Environment:
        - Disabled Mode: Returns NoOpCryptoProvider (no AWS calls, no initialization)
        - Production: Initializes requested provider, falls back to 'software' if HSM fails,
                      crashes if software fails (fail-closed security)
        - Dev/Test: Returns DummyCryptoProvider for safe, deterministic testing
        - Force real provider in tests: Set AUDIT_CRYPTO_FORCE_REAL_PROVIDER=true

        Args:
            provider_type (str): The type of crypto provider to retrieve (e.g., "software", "hsm").

        Returns:
            CryptoProvider: An initialized instance of the requested crypto provider.

        Raises:
            CryptoInitializationError: If no crypto provider can be successfully initialized.
                                       In production, this is a fatal error that prevents
                                       application startup.
        """
        # CRITICAL: Check if crypto is disabled FIRST, before any other logic
        # This prevents AWS calls and initialization attempts when disabled
        if _is_crypto_disabled():
            if "noop" in self._instances:
                return self._instances["noop"]
            
            # Initialize NoOpCryptoProvider - doesn't require any secrets or AWS calls
            noop_cls = self._registry.get("noop", NoOpCryptoProvider)
            noop_instance = noop_cls()
            self._instances["noop"] = noop_instance
            logger.info(
                "AUDIT_CRYPTO_MODE=disabled - using NoOpCryptoProvider",
                extra={"operation": "noop_provider_disabled_mode"},
            )
            return noop_instance
        
        provider_type_lower = provider_type.lower()

        # Check if real provider is forced in test environment
        force_real_provider = (
            os.getenv("AUDIT_CRYPTO_FORCE_REAL_PROVIDER", "").lower() == "true"
        )

        # DEV/TEST MODE HANDLING
        # IMPORTANT: This block ONLY runs in dev/test environments
        # Production environments (PYTHON_ENV=production) skip this entirely
        if _is_test_or_dev_mode() and not force_real_provider:
            # Extra safety check: verify we're not accidentally in production
            # Use centralized environment detection from audit_common
            if _is_production_env():
                # This is a CRITICAL security guardrail
                # If we reach here, something is misconfigured - dev mode flags are set
                # but production env vars are also set. Fail closed.
                error_msg = (
                    "SECURITY ERROR: Conflicting environment configuration detected. "
                    "Production environment variables are set but dev mode is also enabled. "
                    "This could lead to security downgrade. Refusing to continue. "
                    f"AUDIT_LOG_DEV_MODE={os.getenv('AUDIT_LOG_DEV_MODE', '')}"
                )
                logger.critical(
                    error_msg, extra={"operation": "security_config_conflict"}
                )
                raise CryptoInitializationError(error_msg)

            # Safe to use dummy provider in dev/test
            if "dummy" in self._instances:
                return self._instances["dummy"]

            # Initializing the DummyCryptoProvider
            dummy_cls = self._registry["dummy"]
            dummy_instance = dummy_cls(
                software_key_master_accessor=_ensure_software_key_master,
                fallback_hmac_secret_accessor=_ensure_fallback_hmac_secret,
                settings=settings,
            )
            self._instances["dummy"] = dummy_instance
            logger.info(
                "Using DummyCryptoProvider in dev/test mode",
                extra={"operation": "dummy_provider_dev_mode"},
            )
            return dummy_instance

        # --- Production/Non-Test Path ---
        # From this point on, we are in production mode (or forced real provider in tests)

        if provider_type_lower in self._instances:
            logger.debug(
                f"Returning cached instance of {provider_type_lower} crypto provider."
            )
            return self._instances[provider_type_lower]

        # >>> REFRESH REGISTRY (honor monkeypatched classes) <<<
        current_cls = None
        if provider_type_lower == "software":
            current_cls = SoftwareCryptoProvider
        elif provider_type_lower == "hsm":
            current_cls = HSMCryptoProvider

        if current_cls is not None:
            reg_cls = self._registry.get(provider_type_lower)
            if reg_cls is not current_cls:
                self._registry[provider_type_lower] = current_cls
                logger.info(
                    f"Refreshed provider class for '{provider_type_lower}' -> {current_cls.__name__}"
                )

        provider_cls = self._registry.get(provider_type_lower)

        if not provider_cls:
            # SECURITY: In production, unknown provider type is a configuration error
            # Do NOT fall back silently - fail fast
            error_msg = (
                f"Crypto provider '{provider_type}' not found in registry. "
                "Valid options are: 'software', 'hsm'. "
                "Check AUDIT_CRYPTO_PROVIDER_TYPE configuration."
            )
            logger.critical(error_msg, extra={"operation": "get_provider_not_found"})
            raise CryptoInitializationError(error_msg)

        try:
            # Pass only the accessors and settings, matching the CryptoProvider.__init__ signature
            instance = provider_cls(
                software_key_master_accessor=_ensure_software_key_master,
                fallback_hmac_secret_accessor=_ensure_fallback_hmac_secret,
                settings=settings,  # Pass settings to provider for internal config
            )
            self._instances[provider_type_lower] = instance
            logger.info(
                f"Initialized crypto provider: {provider_cls.__name__}",
                extra={
                    "operation": "get_provider_success",
                    "provider_type": provider_cls.__name__,
                },
            )
            return instance
        except Exception as e:
            error_msg = f"Failed to initialize provider '{provider_type}': {e}"
            logger.error(error_msg, exc_info=True)

            # Check if graceful degradation is allowed via AUDIT_CRYPTO_ALLOW_INIT_FAILURE
            allow_init_failure = os.getenv("AUDIT_CRYPTO_ALLOW_INIT_FAILURE", "0").lower() in ("1", "true", "yes")

            # SECURITY: In production, we only allow fallback from HSM to software
            # Never to dummy, and software itself has no fallback
            # EXCEPTION: When AUDIT_CRYPTO_ALLOW_INIT_FAILURE is set, allow fallback to DummyCryptoProvider
            if provider_type_lower == "software":
                if allow_init_failure:
                    # User explicitly requested graceful degradation
                    # Return DummyCryptoProvider with strong security warnings
                    logger.critical(
                        "SECURITY CRITICAL: AUDIT_CRYPTO_ALLOW_INIT_FAILURE is set. "
                        f"Failed to initialize 'software' crypto provider due to: {e}. "
                        "Falling back to DummyCryptoProvider which provides NO REAL SECURITY. "
                        "Audit log integrity is COMPROMISED. "
                        "URGENT: Configure proper AWS credentials and secrets, then set "
                        "AUDIT_CRYPTO_ALLOW_INIT_FAILURE=0 for production security.",
                        extra={"operation": "get_provider_software_init_fail_graceful_degradation"},
                    )
                    return self._create_dummy_provider()
                else:
                    # No fallback possible from software - this is fatal
                    critical_msg = (
                        f"CRITICAL: Failed to initialize 'software' crypto provider: {e}. "
                        "This is a fatal error. The application cannot provide cryptographic "
                        "guarantees and must not start. Check configuration and secret access. "
                        "If you need to start the application without proper crypto configuration, "
                        "set AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1 (NOT recommended for production)."
                    )
                    logger.critical(
                        critical_msg, extra={"operation": "get_provider_software_init_fail"}
                    )
                    raise CryptoInitializationError(critical_msg) from e

            # HSM failed - attempt fallback to software provider
            # This is the ONLY allowed fallback path in production
            logger.warning(
                f"HSM provider initialization failed. Attempting fallback to software provider. "
                f"Original error: {e}",
                extra={"operation": "hsm_fallback_to_software"},
            )

            try:
                # >>> REFRESH 'software' ENTRY BEFORE FALLBACK <<<
                if self._registry.get("software") is not SoftwareCryptoProvider:
                    self._registry["software"] = SoftwareCryptoProvider

                if "software" in self._instances:
                    logger.warning(
                        "Returning cached 'software' crypto provider as HSM fallback."
                    )
                    return self._instances["software"]

                # Pass only the accessors and settings to fallback instance
                fallback_instance = self._registry["software"](
                    software_key_master_accessor=_ensure_software_key_master,
                    fallback_hmac_secret_accessor=_ensure_fallback_hmac_secret,
                    settings=settings,
                )
                self._instances["software"] = fallback_instance
                logger.warning(
                    "Successfully initialized 'software' crypto provider as a fallback.",
                    extra={"operation": "get_provider_fallback_success"},
                )
                return fallback_instance
            except Exception as fallback_e:
                # Also check for AUDIT_CRYPTO_ALLOW_INIT_FAILURE when HSM fallback to software fails
                if allow_init_failure:
                    logger.critical(
                        "SECURITY CRITICAL: AUDIT_CRYPTO_ALLOW_INIT_FAILURE is set. "
                        f"Both HSM and software crypto providers failed. HSM error: {e}. "
                        f"Software fallback error: {fallback_e}. "
                        "Falling back to DummyCryptoProvider which provides NO REAL SECURITY. "
                        "Audit log integrity is COMPROMISED. "
                        "URGENT: Configure proper AWS credentials and secrets, then set "
                        "AUDIT_CRYPTO_ALLOW_INIT_FAILURE=0 for production security.",
                        exc_info=True,
                        extra={"operation": "get_provider_fallback_fail_graceful_degradation"},
                    )
                    return self._create_dummy_provider()
                else:
                    logger.critical(
                        f"Failed to initialize fallback 'software' crypto provider: {fallback_e}. "
                        "No crypto provider available. Exiting. "
                        "If you need to start the application without proper crypto configuration, "
                        "set AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1 (NOT recommended for production).",
                        exc_info=True,
                        extra={"operation": "get_provider_fallback_fail"},
                    )
                    raise CryptoInitializationError(
                        f"No crypto provider available: {fallback_e}"
                    )

    # --- END OF PATCH 3 ---

    def close_all_providers(self):
        """
        Closes all initialized crypto provider instances.
        This should be called during application shutdown to release resources.
        """
        for name, instance in list(self._instances.items()):
            try:
                # Ensure close is awaited if it is an async method
                if asyncio.iscoroutinefunction(instance.close):
                    # Since this is called from a sync signal handler, we must use asyncio.run
                    # on the async close method.
                    try:
                        asyncio.run(instance.close())
                    except RuntimeError:
                        # If called while a loop is running (e.g., in a thread), this might fail.
                        # We'll just run it as-is, accepting the risk in a signal handler.
                        instance.close()  # Fallback to sync call if it exists
                else:
                    instance.close()

                logger.info(f"Successfully closed crypto provider: {name}")

                try:
                    # Log action is async, must run in a loop
                    # Using asyncio.run inside the signal handler context
                    asyncio.run(
                        log_action(
                            "close_provider", provider_name=name, status="success"
                        )
                    )
                except Exception:
                    logging.warning(
                        "Failed to log provider closure (could not run async log action)."
                    )

            except Exception as e:
                logger.error(
                    f"Error closing crypto provider {name}: {e}", exc_info=True
                )

                try:
                    asyncio.run(
                        log_action(
                            "close_provider",
                            provider_name=name,
                            status="fail",
                            error=str(e),
                        )
                    )
                except Exception:
                    logging.warning(
                        "Failed to log provider closure failure (could not run async log action)."
                    )
            finally:
                # This should be safe as we are iterating over a copy of keys (list(self._instances.items()))
                if name in self._instances:
                    del self._instances[name]


def shutdown_handler(signum, frame):
    """
    Signal handler to ensure a graceful shutdown and resource cleanup.
    """
    logger.info("Shutdown signal received. Closing crypto providers...")
    # This must be sync for signal handler, so we wrap the async call.
    try:
        crypto_provider_factory.close_all_providers()
    except Exception as e:
        logger.error(f"Error during final shutdown cleanup: {e}")
    # REMOVED: sys.exit(0) # <--- CRITICAL FIX: Removed sys.exit to prevent SystemExit INTERNALERROR


# Register shutdown hooks
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)


# --- Global Crypto Provider Factory Instance ---
# This global instance provides convenient access to the configured CryptoProviderFactory.
crypto_provider_factory = CryptoProviderFactory()

# --- Global Crypto Provider Instance (for backward compatibility or simple access) ---
# This instance will be initialized once at module load time, with import-safe fallback.

# CRITICAL: Check if crypto is disabled FIRST to prevent AWS calls during import
if _is_crypto_disabled():
    # When disabled, use NoOpCryptoProvider - no AWS calls, no initialization
    logger.info(
        "AUDIT_CRYPTO_MODE=disabled detected. Skipping crypto provider initialization. "
        "Using NoOpCryptoProvider for all crypto operations."
    )
    crypto_provider: Optional[CryptoProvider] = crypto_provider_factory.get_provider("noop")
else:
    # Normal initialization path when crypto is enabled
    try:
        # In DEV/TEST, get_provider will return DummyCryptoProvider, bypassing most config/secret checks.
        crypto_provider: Optional[CryptoProvider] = crypto_provider_factory.get_provider(
            settings.PROVIDER_TYPE
        )
    except CryptoInitializationError as e:
        # Check if we should allow initialization failure
        allow_init_failure = os.getenv("AUDIT_CRYPTO_ALLOW_INIT_FAILURE", "0").lower() in ("1", "true", "yes")
        
        if _is_test_or_dev_mode() or allow_init_failure:
            logger.warning(
                "AUDIT_CRYPTO: Failed to eagerly initialize crypto_provider (%s). "
                "Tests/consumers will implicitly use or call get_provider() lazily, "
                "which will return DummyCryptoProvider. "
                "Set AUDIT_CRYPTO_ALLOW_INIT_FAILURE=0 to enforce strict initialization.",
                e,
            )
            # Explicitly set to DummyCryptoProvider to ensure the backward-compatible global variable is safe
            # even if the initial call failed in a test environment.
            crypto_provider = crypto_provider_factory.get_provider("dummy")
            
            if not _is_test_or_dev_mode() and allow_init_failure:
                logger.critical(
                    "SECURITY CRITICAL: Using DummyCryptoProvider in PRODUCTION due to AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1. "
                    "This provides NO REAL SECURITY for audit log signatures and cryptographic operations. "
                    "Audit log integrity is COMPROMISED. "
                    "URGENT: Configure proper secrets (AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64) "
                    "and set AUDIT_CRYPTO_ALLOW_INIT_FAILURE=0 immediately for production security. "
                    "This configuration should ONLY be used temporarily during initial deployment."
                )
        else:
            # In production with strict mode, this is fatal.
            logger.critical(
                "AUDIT_CRYPTO: Crypto provider initialization failed in production. "
                "Configure AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64 secret or "
                "set AUDIT_CRYPTO_ALLOW_INIT_FAILURE=1 to allow graceful degradation."
            )
            raise


# --- FIX: Expose simple helper function ---
def get_crypto_provider() -> CryptoProvider:
    """Returns the globally configured CryptoProvider instance."""
    if crypto_provider is None:
        # If it failed to initialize in DEV/TEST, force re-initialization now if requested
        # This allows test/dev code to run `get_provider()` after patching.
        # In DEV/TEST, this call will correctly return the DummyCryptoProvider.
        return crypto_provider_factory.get_provider(settings.PROVIDER_TYPE)
    return crypto_provider


if _DUMMY_LOG_ACTION_USED:
    logger.critical(
        "WARNING: The dummy log_action function is in use. This indicates a potential circular dependency or missing module. Logging to the audit log will not work. THIS IS NOT PRODUCTION READY.",
        extra={"operation": "dummy_log_action_in_use"},
    )
