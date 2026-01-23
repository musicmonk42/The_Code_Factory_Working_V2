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

Usage:
    from server.config_utils import get_config, validate_required_api_keys
    
    config = get_config()
    if config.is_production:
        validate_required_api_keys()
"""

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


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
        required_api_keys: Set of required API keys for production
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
    required_api_keys: Set[str] = field(default_factory=set)
    
    # Additional metadata
    startup_timeout: int = 90  # seconds
    redis_url: Optional[str] = None
    database_url: Optional[str] = None


def detect_environment() -> tuple[bool, bool, bool]:
    """
    Detect the current runtime environment.
    
    Uses a proper hierarchy for environment detection:
    1. Check PRODUCTION_MODE env var first (explicit production flag)
    2. Check APP_ENV variable (production, development, staging)
    3. Check TESTING env var (CI/test mode)
    4. Default to development
    
    Returns:
        Tuple of (is_production, is_testing, is_development)
    """
    # Explicit production mode flag (highest priority)
    if os.getenv("PRODUCTION_MODE", "0") == "1":
        logger.info("Environment: PRODUCTION (PRODUCTION_MODE=1)")
        return True, False, False
    
    # Check APP_ENV variable
    app_env = os.getenv("APP_ENV", "").lower()
    if app_env == "production":
        logger.info("Environment: PRODUCTION (APP_ENV=production)")
        return True, False, False
    
    # Check for testing mode
    is_testing = os.getenv("TESTING", "0") == "1"
    if is_testing:
        logger.info("Environment: TESTING (TESTING=1)")
        return False, True, False
    
    # Check if running under pytest (but don't use this for production detection!)
    # This is informational only
    if "pytest" in sys.modules:
        logger.info("Environment: TESTING (pytest detected)")
        return False, True, False
    
    # Default to development
    logger.info("Environment: DEVELOPMENT (default)")
    return False, False, True


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
    config.enable_feature_store = os.getenv("ENABLE_FEATURE_STORE", "0") == "1"
    
    # Feature Flags - Observability (default enabled in production)
    config.enable_sentry = bool(os.getenv("SENTRY_DSN")) if config.is_production else False
    config.enable_prometheus = not config.is_testing  # Enabled except in test mode
    config.enable_audit_logging = config.is_production or os.getenv("ENABLE_AUDIT_LOGGING", "0") == "1"
    
    # Feature Flags - Optional Features
    config.enable_hsm = os.getenv("ENABLE_HSM", "0") == "1"
    config.enable_libvirt = os.getenv("ENABLE_LIBVIRT", "0") == "1"
    
    # Performance Flags
    config.parallel_agent_loading = os.getenv("PARALLEL_AGENT_LOADING", "1") == "1"
    config.lazy_load_ml = os.getenv("LAZY_LOAD_ML", "1") == "1"
    
    # Startup Configuration
    try:
        config.startup_timeout = int(os.getenv("STARTUP_TIMEOUT", "90"))
    except ValueError:
        logger.warning(f"Invalid STARTUP_TIMEOUT value, using default: 90")
        config.startup_timeout = 90
    
    # Database & Redis
    config.database_url = os.getenv("DATABASE_URL")
    config.redis_url = os.getenv("REDIS_URL")
    
    # API Keys Detection
    api_key_vars = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "XAI_API_KEY",
        "GROK_API_KEY",
        "COHERE_API_KEY",
    ]
    
    for key_var in api_key_vars:
        if os.getenv(key_var):
            config.available_api_keys.add(key_var)
    
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
            logger.warning(message)
            logger.warning("LLM functionality may be disabled or limited.")
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
    
    # Connections
    logger.info("\nConnections:")
    logger.info(f"  Redis:    {'CONFIGURED' if config.redis_url else 'NOT CONFIGURED'}")
    logger.info(f"  Database: {'CONFIGURED' if config.database_url else 'NOT CONFIGURED'}")
    
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
