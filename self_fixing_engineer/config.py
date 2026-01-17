"""
Configuration wrapper module for Self-Fixing Engineer.

This module provides a unified configuration interface that integrates with the
existing arbiter.config module while adding backwards compatibility and sensible
defaults for missing attributes.

Classes:
    ConfigWrapper: Wraps ArbiterConfig with additional fields and proper error handling
    GlobalConfigManager: Singleton manager for configuration instances

Functions:
    setup_logging: Configures logging with standard format and settings
"""

import logging
import os
from typing import Any, Dict, Optional

# Configure module logger
logger = logging.getLogger(__name__)

# Try to import from arbiter.config if it exists
try:
    from arbiter.config import ArbiterConfig

    _has_arbiter_config = True
except ImportError:
    _has_arbiter_config = False
    logger.debug("ArbiterConfig not available, will use fallback configuration")


class ConfigWrapper:
    """
    Configuration wrapper that combines ArbiterConfig with additional fields.

    This class provides a unified interface for configuration access, forwarding
    requests to an underlying ArbiterConfig instance when available, while also
    providing sensible defaults for optional fields.

    Attributes:
        AUDIT_LOG_PATH: Path to the audit log file
        REDIS_URL: URL for Redis connection
        APP_ENV: Application environment (development, staging, production)

    Known Optional Fields:
        Fields defined in _OPTIONAL_FIELDS will return their default value (typically None)
        instead of raising AttributeError when not present in the underlying config.
    """

    # Define known optional fields with their defaults
    # These fields are intentionally optional and should return None if not configured
    _OPTIONAL_FIELDS: Dict[str, Any] = {
        "REDIS_URL": None,
        "SENTRY_DSN": None,
        "API_CORS_ORIGINS": None,
        "MONITORING_ENABLED": None,
        "CACHE_BACKEND": None,
    }

    def __init__(self, arbiter_config: Optional[Any] = None) -> None:
        """
        Initialize the ConfigWrapper.

        Args:
            arbiter_config: Optional ArbiterConfig instance to wrap. If None,
                          configuration will come from environment variables only.
        """
        self._arbiter_config = arbiter_config

        # Add fields that main.py expects but ArbiterConfig doesn't have
        # Use environment variables with sensible defaults
        self.AUDIT_LOG_PATH: str = os.getenv("AUDIT_LOG_PATH", "./audit_trail.log")
        self.REDIS_URL: str = os.getenv("REDIS_URL", "")
        self.APP_ENV: str = os.getenv("APP_ENV", "development")

    def __getattr__(self, name: str) -> Any:
        """
        Forward attribute access to ArbiterConfig with proper error handling.

        This method implements the following lookup chain:
        1. Check if the attribute exists in the wrapped ArbiterConfig
        2. Check if it's a known optional field (return default)
        3. Raise AttributeError for unknown attributes

        Args:
            name: The attribute name being accessed

        Returns:
            The attribute value from ArbiterConfig or optional field default

        Raises:
            AttributeError: If the attribute is not found in any configuration source
                          and is not a known optional field
        """
        # First, try to get from wrapped config
        if self._arbiter_config and hasattr(self._arbiter_config, name):
            return getattr(self._arbiter_config, name)

        # Second, check if it's a known optional field
        if name in self._OPTIONAL_FIELDS:
            return self._OPTIONAL_FIELDS[name]

        # If not found anywhere, raise AttributeError as per Python conventions
        raise AttributeError(
            f"'{type(self).__name__}' object has no attribute '{name}'. "
            f"If this is an optional field, add it to _OPTIONAL_FIELDS."
        )

    def __repr__(self) -> str:
        """Return a string representation of the ConfigWrapper."""
        config_type = (
            type(self._arbiter_config).__name__ if self._arbiter_config else "None"
        )
        return f"ConfigWrapper(arbiter_config={config_type}, env={self.APP_ENV})"


class GlobalConfigManager:
    """
    Singleton manager for configuration instances.

    This class ensures only one configuration instance is created and shared
    across the application, following the Singleton pattern.

    Thread Safety:
        The current implementation is not thread-safe. Since configuration
        is typically loaded once at application startup before any concurrent
        operations begin, this is acceptable for most use cases. If thread
        safety is required (e.g., for hot-reloading configuration), implement
        locking using threading.Lock() or use a thread-safe singleton pattern.
    """

    _instance: Optional[ConfigWrapper] = None

    @classmethod
    def get_config(cls) -> ConfigWrapper:
        """
        Get or create the configuration instance.

        Returns:
            ConfigWrapper: The singleton configuration instance

        Note:
            This method is not thread-safe. If thread safety is required,
            implement locking around the _instance check and creation.
        """
        if cls._instance is None:
            cls._instance = cls._load_config()
        return cls._instance

    @classmethod
    def _load_config(cls) -> ConfigWrapper:
        """
        Load configuration from arbiter.config or create minimal fallback config.

        This method attempts to load the full ArbiterConfig, falling back to
        a minimal environment-based configuration if that fails.

        Returns:
            ConfigWrapper or MinimalConfig: The loaded configuration instance
        """
        arbiter_config = None

        if _has_arbiter_config:
            try:
                # Try to get the ArbiterConfig instance
                arbiter_config = ArbiterConfig()
                logger.info("Successfully loaded ArbiterConfig")
            except Exception as e:
                logger.debug(f"Failed to instantiate ArbiterConfig: {e}")
                try:
                    # If that fails, try the initialize method
                    arbiter_config = ArbiterConfig.initialize()
                    logger.info(
                        "Successfully initialized ArbiterConfig via initialize()"
                    )
                except Exception as init_error:
                    logger.warning(
                        f"Failed to initialize ArbiterConfig: {init_error}. "
                        f"Falling back to minimal configuration."
                    )
                    arbiter_config = None

        if arbiter_config:
            # Wrap the ArbiterConfig with our wrapper that adds missing fields
            return ConfigWrapper(arbiter_config)

        # Fallback: Create a minimal config object based on environment variables
        logger.info("Using minimal fallback configuration")

        class MinimalConfig:
            """
            Minimal fallback configuration when ArbiterConfig is unavailable.

            This configuration uses only environment variables and provides
            basic functionality for the application to run in degraded mode.
            """

            def __init__(self) -> None:
                """Initialize minimal config from environment variables."""
                self.REDIS_URL: str = os.getenv("REDIS_URL", "")
                self.AUDIT_LOG_PATH: str = os.getenv(
                    "AUDIT_LOG_PATH", "./audit_trail.log"
                )
                self.DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///sfe.db")
                self.APP_ENV: str = os.getenv("APP_ENV", "development")
                self.DB_PATH: str = os.getenv("DB_PATH", "sqlite:///sfe.db")
                self.ARENA_PORT: int = int(os.getenv("ARENA_PORT", "8000"))
                self.REPORTS_DIRECTORY: str = os.getenv(
                    "REPORTS_DIRECTORY", "./reports"
                )

            def __getattr__(self, name: str) -> Any:
                """
                Return None for any missing attributes in minimal config.

                Args:
                    name: The attribute name being accessed

                Returns:
                    None for missing attributes (to allow graceful degradation)

                Note:
                    This is intentionally permissive to allow graceful degradation
                    when running with minimal configuration. The application should
                    check for None values and handle them appropriately.

                    If strict attribute checking is required, use ConfigWrapper
                    instead of MinimalConfig by ensuring ArbiterConfig is available.

                    Debug logging is intentionally minimal to avoid performance
                    impact from frequent __getattr__ calls.

                Example:
                    >>> config = MinimalConfig()
                    >>> value = config.SOME_OPTIONAL_FEATURE
                    >>> if value is not None:
                    >>>     # Use the feature
                    >>>     pass
                """
                # Only log at debug level and only for first access (implicit)
                # to avoid performance impact from frequent attribute access
                return None

            def __repr__(self) -> str:
                """Return a string representation of MinimalConfig."""
                return (
                    f"MinimalConfig(env={self.APP_ENV}, database={self.DATABASE_URL})"
                )

        return MinimalConfig()


def setup_logging() -> None:
    """
    Configure logging with production-ready settings.

    This sets up the root logger with a standard format including timestamps,
    logger names, and log levels. The default level is INFO to balance between
    verbosity and useful information.

    Note:
        This should be called once at application startup. Subsequent calls
        will not reconfigure the logging if handlers are already present.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("Logging configured successfully")
