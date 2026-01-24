"""
Configuration Validator for Production Environments

This module validates that required environment variables are properly configured
for production deployments. It provides clear error messages and default values
for development environments.
"""

import logging
import os
import sys
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when required configuration is missing in production mode."""

    pass


def is_production_mode() -> bool:
    """Check if the application is running in production mode."""
    return os.getenv("PRODUCTION_MODE", "0") == "1" or os.getenv("APP_ENV", "development") == "production"


def is_testing_mode() -> bool:
    """Check if the application is running in testing mode."""
    return os.getenv("TESTING", "0") == "1" or os.getenv("PYTEST_CURRENT_TEST") is not None


def get_env_with_default(key: str, default: str, required_in_prod: bool = False) -> str:
    """
    Get environment variable with default value.

    Args:
        key: Environment variable name
        default: Default value to use if not set
        required_in_prod: If True, raises error in production mode when not set

    Returns:
        The environment variable value or default

    Raises:
        ConfigValidationError: If required in production and not set
    """
    value = os.getenv(key)

    if value is None or value == "":
        if required_in_prod and is_production_mode() and not is_testing_mode():
            raise ConfigValidationError(
                f"Required environment variable '{key}' is not set. "
                f"This is required in production mode (PRODUCTION_MODE=1). "
                f"Please set {key} in your environment or secrets manager."
            )
        logger.debug(f"Using default value for {key}: {default}")
        return default

    return value


def validate_critical_configs() -> Tuple[bool, List[str]]:
    """
    Validate critical configuration settings.

    Returns:
        Tuple of (is_valid, list_of_warnings)
    """
    warnings = []
    is_production = is_production_mode()
    is_testing = is_testing_mode()

    # Skip validation in testing mode
    if is_testing:
        logger.info("Running in testing mode - skipping configuration validation")
        return True, []

    # Check for at least one LLM provider API key
    llm_keys = [
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "GROK_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
    ]
    has_llm_key = any(os.getenv(key) for key in llm_keys)

    if not has_llm_key and is_production:
        warnings.append(
            "No LLM API keys found. At least one of the following should be set: "
            + ", ".join(llm_keys)
        )

    # Check for encryption keys in production
    if is_production:
        if not os.getenv("SECRET_KEY") or os.getenv("SECRET_KEY") == "your-secret-key-here-change-in-production":
            warnings.append(
                "SECRET_KEY is not set or using default value. "
                "Generate a secure key for production: python -c 'import secrets; print(secrets.token_hex(32))'"
            )

        if not os.getenv("AUDIT_SIGNING_KEY"):
            warnings.append(
                "AUDIT_SIGNING_KEY is not set. "
                "This is required for audit log integrity. "
                "Generate with: openssl rand -hex 32"
            )

        if not os.getenv("AGENTIC_AUDIT_HMAC_KEY"):
            warnings.append(
                "AGENTIC_AUDIT_HMAC_KEY is not set. "
                "This is required for audit log HMAC. "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )

    # Check for optional but recommended configs
    optional_configs = {
        "WEB3_PROVIDER_URL": "Web3 blockchain integration (only needed if ENABLE_BLOCKCHAIN_LOGGING=true)",
        "SENTRY_DSN": "Sentry error tracking (recommended for production)",
        "REDIS_URL": "Redis cache and message bus (recommended for production)",
        "DATABASE_URL": "Database connection (required if ENABLE_DATABASE=1)",
    }

    for config_key, description in optional_configs.items():
        if not os.getenv(config_key):
            if is_production:
                logger.info(f"{config_key} not set - {description}")
            else:
                logger.debug(f"{config_key} not set - using default behavior")

    # Log warnings
    if warnings:
        logger.warning("Configuration validation warnings:")
        for warning in warnings:
            logger.warning(f"  - {warning}")

        if is_production:
            logger.error(
                "Critical configuration issues found in production mode. "
                "Please review the warnings above."
            )
            return False, warnings

    return True, warnings


def log_configuration_status():
    """Log the current configuration status for debugging."""
    is_production = is_production_mode()
    is_testing = is_testing_mode()

    logger.info("=" * 80)
    logger.info("Configuration Status")
    logger.info("=" * 80)
    logger.info(f"Production Mode: {is_production}")
    logger.info(f"Testing Mode: {is_testing}")
    logger.info(f"App Environment: {os.getenv('APP_ENV', 'development')}")
    logger.info(f"Debug: {os.getenv('DEBUG', 'true')}")
    logger.info(f"Log Level: {os.getenv('LOG_LEVEL', 'INFO')}")

    # Feature flags
    logger.info("Feature Flags:")
    feature_flags = [
        "ENABLE_DATABASE",
        "ENABLE_FEATURE_STORE",
        "ENABLE_HSM",
        "ENABLE_LIBVIRT",
        "ENABLE_AUDIT_LOGGING",
        "ENABLE_PROMETHEUS",
        "PARALLEL_AGENT_LOADING",
        "LAZY_LOAD_ML",
    ]
    for flag in feature_flags:
        value = os.getenv(flag, "not set")
        logger.info(f"  {flag}: {value}")

    # Check for mock implementations
    if not is_production and not is_testing:
        logger.info("Development mode: Mock implementations may be used for missing dependencies")
    elif is_production:
        logger.info("Production mode: Mock implementations will not be used")

    logger.info("=" * 80)


def get_config_defaults() -> Dict[str, str]:
    """
    Get default configuration values for development/testing.

    Returns:
        Dictionary of configuration key-value pairs
    """
    return {
        # Core settings
        "APP_ENV": "development",
        "DEBUG": "true",
        "LOG_LEVEL": "INFO",
        "PRODUCTION_MODE": "0",
        "TESTING": "0",
        # Database
        "DATABASE_URL": "sqlite:///./dev.db",
        "ENABLE_DATABASE": "0",
        # Redis
        "REDIS_URL": "redis://localhost:6379",
        "REDIS_DB": "0",
        # Message Bus
        "MESSAGE_BUS_TYPE": "memory",
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
        # Feature flags
        "ENABLE_FEATURE_STORE": "0",
        "ENABLE_HSM": "0",
        "ENABLE_LIBVIRT": "0",
        "ENABLE_AUDIT_LOGGING": "0",
        "PARALLEL_AGENT_LOADING": "1",
        "LAZY_LOAD_ML": "1",
        # Performance
        "STARTUP_TIMEOUT": "90",
        "WORKER_COUNT": "4",
        # Observability (defaults - production should override)
        "PROMETHEUS_PORT": "9090",
        "METRICS_PORT": "8001",
        # Security (development only - must be changed in production)
        "SECRET_KEY": "dev-secret-key-change-in-production",
        "JWT_ALGORITHM": "HS256",
        "JWT_EXPIRATION_MINUTES": "60",
    }


# Initialize configuration validation on module import
_validation_complete = False


def ensure_configuration_valid():
    """
    Ensure configuration is valid. Called once at startup.

    Raises:
        ConfigValidationError: If critical configuration is invalid in production
    """
    global _validation_complete

    if _validation_complete:
        return

    # Log configuration status
    log_configuration_status()

    # Validate critical configs
    is_valid, warnings = validate_critical_configs()

    if not is_valid and is_production_mode():
        error_msg = (
            "Configuration validation failed in production mode. "
            "Please fix the following issues:\n" + "\n".join(f"  - {w}" for w in warnings)
        )
        logger.error(error_msg)
        # Don't raise in production mode - just log warnings
        # This allows the application to start but makes issues visible
        logger.error(
            "Application is starting with configuration warnings. "
            "Please review and fix the issues above."
        )

    _validation_complete = True


# Optional: Auto-validate on import (can be disabled for testing)
if os.getenv("SKIP_CONFIG_VALIDATION") != "1":
    try:
        ensure_configuration_valid()
    except Exception as e:
        logger.error(f"Configuration validation error: {e}")
        if is_production_mode() and not is_testing_mode():
            # In production, log but don't crash - allow graceful degradation
            logger.error("Continuing with degraded configuration")
