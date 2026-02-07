# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Configuration Utilities Module
==============================

This module provides centralized configuration management for the Code Factory platform,
including environment detection, feature flags, and API key validation.

Key Features:
- Proper environment detection (not pytest-based)
- Feature flag system for optional components
- API key validation with fail-fast behavior
- Configuration documentation and validation
- Input validation and bounds checking
- Secure default values

Usage:
    from server.config_utils import get_config, validate_required_api_keys
    
    # Get configuration
    config = get_config()
    
    # Check environment
    if config.is_production:
        # Production-specific logic
        validate_required_api_keys(config)
    
    # Use feature flags
    if config.enable_database:
        # Database functionality
        pass

Environment Detection Hierarchy:
    1. PRODUCTION_MODE env var (explicit override)
    2. APP_ENV env var (production/staging/development)
    3. TESTING env var (CI/test mode)
    4. pytest detection (informational only)
    5. Default to development

Feature Flags:
    Database & Storage:
        - ENABLE_DATABASE: PostgreSQL/SQLite functionality
        - ENABLE_FEATURE_STORE: Feast feature store
    
    Observability:
        - ENABLE_SENTRY: Sentry error tracking
        - ENABLE_PROMETHEUS: Prometheus metrics (auto-enabled in non-test mode)
        - ENABLE_AUDIT_LOGGING: Audit logging (auto-enabled in production)
    
    Optional Features:
        - ENABLE_HSM: Hardware Security Module support
        - ENABLE_LIBVIRT: Libvirt virtualization
    
    Performance:
        - PARALLEL_AGENT_LOADING: Parallel vs sequential agent loading
        - LAZY_LOAD_ML: Lazy loading of ML libraries

Constants:
    MIN_STARTUP_TIMEOUT: Minimum allowed startup timeout (seconds)
    MAX_STARTUP_TIMEOUT: Maximum allowed startup timeout (seconds)
    DEFAULT_STARTUP_TIMEOUT: Default startup timeout (seconds)

Security Considerations:
    - API keys are detected but never logged
    - Configuration values are validated
    - Timeouts are bounded to prevent abuse
    - Production mode requires explicit enablement

Examples:
    >>> # Basic configuration
    >>> config = get_config()
    >>> print(f"Environment: {config.is_production}")
    
    >>> # Validate API keys
    >>> try:
    ...     validate_required_api_keys(config, fail_fast=True)
    ... except RuntimeError as e:
    ...     print(f"Missing API keys: {e}")
    
    >>> # Initialize with logging
    >>> config = initialize_config(log_summary=True)
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Configuration constants
MIN_STARTUP_TIMEOUT = 10  # Minimum startup timeout in seconds
MAX_STARTUP_TIMEOUT = 600  # Maximum startup timeout in seconds (10 minutes)
DEFAULT_STARTUP_TIMEOUT = 90  # Default startup timeout in seconds


def sanitize_env_value(value: Optional[str]) -> Optional[str]:
    """
    Sanitize environment variable values by removing wrapping quotes and whitespace.
    
    Railway and other cloud providers sometimes provide environment variables
    with wrapping quotes or extra whitespace that cause validation failures.
    
    This function:
    1. Strips leading/trailing whitespace
    2. Removes wrapping quotes (both single and double) only if they wrap the entire value
    
    Args:
        value: Raw environment variable value
        
    Returns:
        Sanitized value with wrapping quotes and leading/trailing whitespace removed,
        or None if the input is None or empty after sanitization
        
    Examples:
        >>> sanitize_env_value('"sk-abc123"')  # Wrapped in double quotes
        'sk-abc123'
        >>> sanitize_env_value("'sk-abc123'")  # Wrapped in single quotes
        'sk-abc123'
        >>> sanitize_env_value('  sk-abc123  ')  # Extra whitespace
        'sk-abc123'
        >>> sanitize_env_value('key-with-"quote"-inside')  # Quote in middle preserved
        'key-with-"quote"-inside'
    """
    if value is None:
        return None
    
    # Strip whitespace first
    sanitized = value.strip()
    
    # Remove wrapping quotes only (preserves quotes in the middle of values)
    if len(sanitized) >= 2:
        if (sanitized.startswith('"') and sanitized.endswith('"')) or \
           (sanitized.startswith("'") and sanitized.endswith("'")):
            sanitized = sanitized[1:-1]
    
    return sanitized if sanitized else None


def get_sanitized_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get an environment variable with automatic sanitization.
    
    This function retrieves environment variables and automatically sanitizes
    them by removing quotes and whitespace that cloud providers (especially Railway)
    may inadvertently include.
    
    Args:
        key: Environment variable name
        default: Default value if not set
        
    Returns:
        Sanitized environment variable value, or default if not set
    """
    value = os.environ.get(key)
    if value is None:
        return default
    return sanitize_env_value(value) or default


@dataclass
class PlatformConfig:
    """
    Centralized configuration for the Code Factory platform.
    
    Attributes:
        is_production: True if running in production mode
        is_testing: True if running in test/CI mode
        is_development: True if running in development mode
        
        # Feature Flags
        enable_database: Enable database functionality
        enable_feature_store: Enable Feast feature store
        enable_sentry: Enable Sentry error tracking
        enable_prometheus: Enable Prometheus metrics
        enable_audit_logging: Enable audit logging
        enable_hsm: Enable HSM support
        
        # Performance Flags
        parallel_agent_loading: Enable parallel agent loading
        lazy_load_ml: Enable lazy loading of ML libraries
        
        # API Keys
        available_api_keys: Set of available LLM API keys
        available_providers: Set of available LLM provider names
        required_api_keys: Set of required API keys for production
        
        # Infrastructure
        kafka_enabled: Whether Kafka is enabled
        kafka_available: Whether Kafka connection is available
    """
    # Environment Detection
    is_production: bool = False
    is_testing: bool = False
    is_development: bool = True
    
    # Feature Flags - Database & Storage
    enable_database: bool = False
    enable_feature_store: bool = False
    
    # Feature Flags - Observability
    enable_sentry: bool = False
    enable_prometheus: bool = True
    enable_audit_logging: bool = True
    
    # Feature Flags - Optional Features
    enable_hsm: bool = False
    enable_libvirt: bool = False
    
    # Performance Flags
    parallel_agent_loading: bool = True
    lazy_load_ml: bool = True
    
    # API Keys
    available_api_keys: Set[str] = field(default_factory=set)
    available_providers: Set[str] = field(default_factory=set)
    required_api_keys: Set[str] = field(default_factory=set)
    
    # Infrastructure
    kafka_enabled: bool = False
    kafka_available: bool = False
    
    # Additional metadata
    startup_timeout: int = 90  # seconds
    redis_url: Optional[str] = None
    database_url: Optional[str] = None


def detect_environment() -> Tuple[bool, bool, bool]:
    """
    Detect the current runtime environment.
    
    DEPRECATED: Use server.environment module instead for consistent detection.
    This function remains for backward compatibility but delegates to the
    centralized environment detector.
    
    Returns:
        Tuple of (is_production, is_testing, is_development)
    """
    from server.environment import is_production, is_test, is_development
    
    return is_production(), is_test(), is_development()


def get_config() -> PlatformConfig:
    """
    Get the platform configuration based on environment variables.
    
    This function reads environment variables and constructs a PlatformConfig
    object with all feature flags, API keys, and settings properly configured.
    
    Returns:
        PlatformConfig instance with current configuration
    """
    config = PlatformConfig()
    
    # Detect environment
    config.is_production, config.is_testing, config.is_development = detect_environment()
    
    # Feature Flags - Database & Storage
    config.enable_database = os.getenv("ENABLE_DATABASE", "0") == "1"
    
    # FIX: Enable Feature Store if explicitly enabled OR if Feast is available and user wants auto-enable
    feature_store_env = os.getenv("ENABLE_FEATURE_STORE", "0")
    if feature_store_env == "1":
        config.enable_feature_store = True
    elif feature_store_env == "auto":
        # Auto-detect: enable if Feast is installed
        try:
            import feast  # noqa: F401
            config.enable_feature_store = True
            logger.info("Feature Store auto-enabled (Feast library detected)")
        except ImportError:
            config.enable_feature_store = False
    else:
        config.enable_feature_store = False
    
    # Feature Flags - Observability (default enabled in production)
    # FIX: Enable Sentry if SENTRY_DSN is provided (regardless of environment)
    config.enable_sentry = bool(os.getenv("SENTRY_DSN"))
    config.enable_prometheus = not config.is_testing  # Enabled except in test mode
    config.enable_audit_logging = config.is_production or os.getenv("ENABLE_AUDIT_LOGGING", "0") == "1"
    
    # Feature Flags - Optional Features
    # FIX: Enable HSM if explicitly enabled OR if python-pkcs11 is available and user wants auto-enable
    hsm_env = os.getenv("ENABLE_HSM", "0")
    if hsm_env == "1":
        config.enable_hsm = True
    elif hsm_env == "auto":
        # Auto-detect: enable if python-pkcs11 is installed
        try:
            import pkcs11  # noqa: F401
            config.enable_hsm = True
            logger.info("HSM Support auto-enabled (python-pkcs11 library detected)")
        except ImportError:
            config.enable_hsm = False
    else:
        config.enable_hsm = False
    
    # FIX: Enable Libvirt if explicitly enabled OR if libvirt is available
    libvirt_env = os.getenv("ENABLE_LIBVIRT", "0")
    if libvirt_env == "1":
        config.enable_libvirt = True
    elif libvirt_env == "auto":
        # Auto-detect: enable if libvirt-python is installed
        try:
            import libvirt  # noqa: F401
            config.enable_libvirt = True
            logger.info("Libvirt Support auto-enabled (libvirt-python library detected)")
        except ImportError:
            config.enable_libvirt = False
    else:
        config.enable_libvirt = False
    
    # Performance Flags
    config.parallel_agent_loading = os.getenv("PARALLEL_AGENT_LOADING", "1") == "1"
    config.lazy_load_ml = os.getenv("LAZY_LOAD_ML", "1") == "1"
    
    # Kafka Configuration
    config.kafka_enabled = os.getenv("KAFKA_ENABLED", "false").lower() in ("true", "1", "yes")
    # kafka_available will be determined at runtime when connection is attempted
    config.kafka_available = False  # Default to False, updated when connection succeeds
    
    # Startup Configuration
    try:
        timeout_value = int(os.getenv("STARTUP_TIMEOUT", str(DEFAULT_STARTUP_TIMEOUT)))
        # Validate timeout is within reasonable bounds
        if MIN_STARTUP_TIMEOUT <= timeout_value <= MAX_STARTUP_TIMEOUT:
            config.startup_timeout = timeout_value
        else:
            logger.warning(
                f"STARTUP_TIMEOUT value {timeout_value} out of range "
                f"({MIN_STARTUP_TIMEOUT}-{MAX_STARTUP_TIMEOUT}s), using default: {DEFAULT_STARTUP_TIMEOUT}"
            )
            config.startup_timeout = DEFAULT_STARTUP_TIMEOUT
    except ValueError as e:
        logger.warning(f"Invalid STARTUP_TIMEOUT value: {e}, using default: {DEFAULT_STARTUP_TIMEOUT}")
        config.startup_timeout = DEFAULT_STARTUP_TIMEOUT
    
    # Database & Redis
    config.database_url = os.getenv("DATABASE_URL")
    config.redis_url = os.getenv("REDIS_URL")
    
    # API Keys Detection with provider mapping
    api_key_vars = {
        "OPENAI_API_KEY": "OpenAI",
        "ANTHROPIC_API_KEY": "Anthropic Claude",
        "CLAUDE_API_KEY": "Anthropic Claude",
        "GOOGLE_API_KEY": "Google Gemini",
        "GEMINI_API_KEY": "Google Gemini",
        "XAI_API_KEY": "xAI Grok",
        "GROK_API_KEY": "xAI Grok",
        "COHERE_API_KEY": "Cohere",
    }
    
    available_providers = set()
    for key_var, provider_name in api_key_vars.items():
        raw_key = os.getenv(key_var)
        if raw_key:
            # FIX: Sanitize API key values from Railway/cloud providers that may include
            # wrapping quotes or whitespace that cause API key validation failures
            sanitized_key = sanitize_env_value(raw_key)
            if sanitized_key:
                config.available_api_keys.add(key_var)
                available_providers.add(provider_name)
                # Log if sanitization changed the value (indicates potential config issue)
                if raw_key != sanitized_key:
                    logger.debug(f"API key {key_var} was sanitized (removed wrapping quotes/whitespace)")
    
    # Store available providers in config for display
    config.available_providers = available_providers
    
    # Define required keys for production
    if config.is_production:
        # At least one LLM API key is required
        config.required_api_keys = {"at_least_one_llm_key"}
    
    return config


def validate_required_api_keys(config: Optional[PlatformConfig] = None, fail_fast: bool = True) -> bool:
    """
    Validate that required API keys are present.
    
    Args:
        config: Platform configuration (will auto-detect if None)
        fail_fast: If True, raises RuntimeError on missing keys (production behavior)
                  If False, logs warnings only (development behavior)
    
    Returns:
        True if all required keys present, False otherwise
    
    Raises:
        RuntimeError: If fail_fast=True and required keys are missing
    """
    if config is None:
        config = get_config()
    
    # Check if at least one LLM API key is available
    if not config.available_api_keys:
        message = (
            "No LLM API keys found! At least one of the following is required:\n"
            "  - OPENAI_API_KEY\n"
            "  - ANTHROPIC_API_KEY\n"
            "  - GOOGLE_API_KEY / GEMINI_API_KEY\n"
            "  - XAI_API_KEY / GROK_API_KEY\n"
            "  - COHERE_API_KEY\n"
            "\n"
            "Set at least one API key to enable LLM functionality."
        )
        
        if fail_fast and config.is_production:
            logger.error(message)
            raise RuntimeError(message)
        else:
            # Use DEBUG instead of WARNING for optional API keys
            logger.debug(message)
            logger.debug("LLM functionality may be disabled or limited.")
            return False
    
    # Log available keys
    logger.info(f"Available LLM API keys: {', '.join(sorted(config.available_api_keys))}")
    return True


def log_configuration_summary(config: Optional[PlatformConfig] = None):
    """
    Log a comprehensive summary of the current configuration.
    
    This is useful for debugging and understanding the runtime configuration.
    
    Args:
        config: Platform configuration (will auto-detect if None)
    """
    if config is None:
        config = get_config()
    
    logger.info("=" * 80)
    logger.info("PLATFORM CONFIGURATION SUMMARY")
    logger.info("=" * 80)
    
    # Environment
    env_mode = "PRODUCTION" if config.is_production else ("TESTING" if config.is_testing else "DEVELOPMENT")
    logger.info(f"Environment Mode: {env_mode}")
    
    # Feature Flags
    logger.info("\nFeature Flags:")
    logger.info(f"  Database:        {'ENABLED' if config.enable_database else 'DISABLED'}")
    logger.info(f"  Feature Store:   {'ENABLED' if config.enable_feature_store else 'DISABLED'}")
    logger.info(f"  Sentry:          {'ENABLED' if config.enable_sentry else 'DISABLED'}")
    logger.info(f"  Prometheus:      {'ENABLED' if config.enable_prometheus else 'DISABLED'}")
    logger.info(f"  Audit Logging:   {'ENABLED' if config.enable_audit_logging else 'DISABLED'}")
    logger.info(f"  HSM Support:     {'ENABLED' if config.enable_hsm else 'DISABLED'}")
    logger.info(f"  Libvirt Support: {'ENABLED' if config.enable_libvirt else 'DISABLED'}")
    
    # Performance
    logger.info("\nPerformance Settings:")
    logger.info(f"  Parallel Agent Loading: {'ENABLED' if config.parallel_agent_loading else 'DISABLED'}")
    logger.info(f"  Lazy Load ML:           {'ENABLED' if config.lazy_load_ml else 'DISABLED'}")
    logger.info(f"  Startup Timeout:        {config.startup_timeout}s")
    
    # API Keys
    logger.info("\nLLM API Keys:")
    if config.available_api_keys:
        for key in sorted(config.available_api_keys):
            logger.info(f"  ✓ {key}")
    else:
        logger.warning("  ✗ No LLM API keys configured")
    
    # LLM Providers
    logger.info("\nLLM Providers:")
    if config.available_providers:
        for provider in sorted(config.available_providers):
            logger.info(f"  ✓ {provider} (AVAILABLE)")
        
        # List missing providers
        all_providers = {"OpenAI", "Anthropic Claude", "Google Gemini", "xAI Grok", "Cohere"}
        missing_providers = all_providers - config.available_providers
        if missing_providers:
            logger.info("\n  Missing (optional) providers:")
            for provider in sorted(missing_providers):
                logger.info(f"    ✗ {provider} (NOT CONFIGURED)")
    else:
        logger.warning("  ✗ No LLM providers configured")
    
    # Infrastructure
    logger.info("\nInfrastructure:")
    logger.info(f"  Redis:    {'CONFIGURED' if config.redis_url else 'NOT CONFIGURED'}")
    logger.info(f"  Database: {'CONFIGURED' if config.database_url else 'NOT CONFIGURED'}")
    logger.info(f"  Kafka:    {'ENABLED' if config.kafka_enabled else 'DISABLED'} "
                f"({'AVAILABLE' if config.kafka_available else 'NOT TESTED' if config.kafka_enabled else 'N/A'})")
    
    logger.info("=" * 80)


def get_missing_optional_dependencies() -> Dict[str, List[str]]:
    """
    Check for missing optional dependencies and return categorized results.
    
    Returns:
        Dictionary mapping feature name to list of missing dependencies
    """
    missing = {}
    
    # Check HSM dependencies
    try:
        import pkcs11
    except ImportError:
        missing["HSM Support"] = ["python-pkcs11"]
    
    # Check libvirt
    try:
        import libvirt
    except ImportError:
        missing["Libvirt Virtualization"] = ["libvirt-python", "system: libvirt-dev, pkg-config"]
    
    # Check Avro
    try:
        import fastavro
    except ImportError:
        missing["Avro Serialization"] = ["fastavro"]
    
    # Check PlantUML/Graphviz
    try:
        import subprocess
        result = subprocess.run(["dot", "-V"], capture_output=True, timeout=1)
        if result.returncode != 0:
            missing["PlantUML/Graphviz"] = ["system: graphviz"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        missing["PlantUML/Graphviz"] = ["system: graphviz"]
    
    # Check Sphinx
    try:
        import sphinx
    except ImportError:
        missing["Sphinx Documentation"] = ["sphinx", "sphinx-rtd-theme"]
    
    return missing


def log_optional_dependencies():
    """
    Log information about missing optional dependencies at INFO level.
    
    This provides visibility into which optional features are available
    without polluting logs with WARNING messages for intentionally
    disabled features.
    """
    missing = get_missing_optional_dependencies()
    
    if missing:
        logger.info("Optional dependencies status:")
        for feature, deps in missing.items():
            logger.info(f"  {feature}: Not available (missing: {', '.join(deps)})")
        logger.info("Install these dependencies only if you need the corresponding features.")
    else:
        logger.info("All optional dependencies are installed.")


# Initialize configuration on module import
_config: Optional[PlatformConfig] = None


def initialize_config(log_summary: bool = True) -> PlatformConfig:
    """
    Initialize and return the global configuration.
    
    Args:
        log_summary: Whether to log the configuration summary
    
    Returns:
        Initialized PlatformConfig instance
    """
    global _config
    
    if _config is None:
        _config = get_config()
        
        if log_summary:
            log_configuration_summary(_config)
            log_optional_dependencies()
    
    return _config
