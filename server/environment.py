"""
Centralized Environment Detection Module
=========================================

This module provides a singleton-based environment detector that ensures consistent
environment detection across the entire application, preventing test mode from
leaking into production and eliminating race conditions.

**Thread Safety**: Fully thread-safe using lazy initialization and cached results.
**Performance**: O(1) lookups after first detection with zero overhead.
**Priority Order** (highest to lowest):
    1. FORCE_PRODUCTION_MODE=true (explicit override for troubleshooting)
    2. APP_ENV=production/staging/development/test (primary configuration)
    3. PRODUCTION_MODE=true (legacy support)
    4. CI/CD environment detection (GitHub Actions, GitLab CI, Jenkins, CircleCI)
    5. pytest detection (test framework)
    6. Default to development (fail-safe)

**Security Considerations**:
    - Production mode requires explicit configuration
    - No implicit production mode activation
    - Clear audit trail in logs for environment detection

**Examples**:
    >>> from server.environment import is_production, get_environment, Environment
    >>> 
    >>> # Check environment
    >>> if is_production():
    ...     initialize_production_monitoring()
    >>> 
    >>> # Get detailed environment info
    >>> env = get_environment()
    >>> print(f"Running in: {env.value}")
    >>> 
    >>> # Use in conditional logic
    >>> if get_environment() in [Environment.PRODUCTION, Environment.STAGING]:
    ...     enable_rate_limiting()

**Best Practices**:
    - Always use these functions instead of checking environment variables directly
    - Never override environment in production code (only in tests)
    - Log environment detection for audit trails
    
**Module Version**: 1.0.0
**Author**: Code Factory Platform Team
**Last Updated**: 2026-01-23
"""
import os
import sys
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class Environment(Enum):
    """
    Enumeration of supported runtime environments.
    
    Attributes:
        PRODUCTION: Production environment with full monitoring and security
        STAGING: Pre-production environment for final validation
        DEVELOPMENT: Local development environment with debug features
        TEST: Test/CI environment with mocked external dependencies
    """
    PRODUCTION = "production"
    STAGING = "staging"
    DEVELOPMENT = "development"
    TEST = "test"
    
    def __str__(self) -> str:
        """Return human-readable environment name."""
        return self.value
    
    @property
    def is_production_like(self) -> bool:
        """Check if environment requires production-grade behavior."""
        return self in (Environment.PRODUCTION, Environment.STAGING)


class EnvironmentDetector:
    """
    Thread-safe singleton environment detector with priority-based logic.
    
    This class implements the Singleton pattern to ensure consistent environment
    detection across all modules and threads, preventing test mode from leaking
    into production and eliminating race conditions in environment detection.
    
    **Thread Safety**: Uses lazy initialization with instance caching. Safe for
    concurrent access due to Python's GIL and atomic reference assignment.
    
    **Performance**: First call performs detection (O(n) where n=number of env vars),
    subsequent calls are O(1) lookups with zero overhead.
    
    **Design Pattern**: Singleton with lazy initialization and cached results.
    
    **Usage**:
        >>> detector = EnvironmentDetector()
        >>> env = detector.detect()  # First call: performs detection
        >>> env2 = detector.detect()  # Subsequent: returns cached result
        >>> assert env is env2  # Same instance
    
    **Attributes**:
        _instance: Class-level singleton instance (None until first instantiation)
        _environment: Cached environment result (None until first detection)
    
    **Methods**:
        detect(): Main detection method with priority-based logic
        is_production(): Quick check for production mode
        is_test(): Quick check for test mode
        is_staging(): Quick check for staging mode
        is_development(): Quick check for development mode
        reset(): Clear cache (for testing only)
    """
    _instance: Optional['EnvironmentDetector'] = None
    _environment: Optional[Environment] = None
    
    def __new__(cls) -> 'EnvironmentDetector':
        """
        Implement singleton pattern with thread-safe lazy initialization.
        
        Returns:
            The singleton EnvironmentDetector instance
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def detect(self) -> Environment:
        """
        Detect current runtime environment using priority-based logic.
        
        This method implements a deterministic priority system that checks
        environment variables and runtime conditions in a specific order,
        caching the result for subsequent calls.
        
        **Priority Order**:
            1. FORCE_PRODUCTION_MODE=true (explicit override)
            2. APP_ENV={production,staging,development,test} (primary config)
            3. PRODUCTION_MODE=true (legacy support)
            4. CI environment variables (CI, GITHUB_ACTIONS, GITLAB_CI, etc.)
            5. pytest in sys.modules (test framework detection)
            6. Default to development (safe default)
        
        **Caching Strategy**: Result is cached after first detection to ensure:
            - Consistent results throughout application lifecycle
            - Zero performance overhead on subsequent calls
            - No race conditions from changing environment variables
        
        **Thread Safety**: Safe for concurrent access due to:
            - Atomic read/write of _environment reference
            - Idempotent detection logic
            - No mutable shared state
        
        Returns:
            Environment enum value representing the detected environment
        
        Examples:
            >>> detector = EnvironmentDetector()
            >>> env = detector.detect()
            >>> print(f"Running in {env.value} mode")
            Running in production mode
        
        Note:
            Once an environment is detected, it cannot be changed without calling
            reset() first. This is intentional to prevent inconsistent behavior.
        """
        if self._environment is not None:
            return self._environment
        
        # Priority 1: Explicit override (for troubleshooting)
        if os.getenv("FORCE_PRODUCTION_MODE", "").lower() == "true":
            self._environment = Environment.PRODUCTION
            logger.info("Environment: PRODUCTION (FORCE_PRODUCTION_MODE=true)")
            return self._environment
        
        # Priority 2: APP_ENV (primary config)
        app_env = os.getenv("APP_ENV", "").lower()
        if app_env in ["production", "prod"]:
            self._environment = Environment.PRODUCTION
            logger.info("Environment: PRODUCTION (APP_ENV=production)")
            return self._environment
        elif app_env == "staging":
            self._environment = Environment.STAGING
            logger.info("Environment: STAGING (APP_ENV=staging)")
            return self._environment
        elif app_env in ["development", "dev"]:
            self._environment = Environment.DEVELOPMENT
            logger.info("Environment: DEVELOPMENT (APP_ENV=development)")
            return self._environment
        elif app_env == "test":
            self._environment = Environment.TEST
            logger.info("Environment: TEST (APP_ENV=test)")
            return self._environment
        
        # Priority 3: PRODUCTION_MODE (legacy support)
        if os.getenv("PRODUCTION_MODE", "").lower() == "true" or os.getenv("PRODUCTION_MODE") == "1":
            self._environment = Environment.PRODUCTION
            logger.info("Environment: PRODUCTION (PRODUCTION_MODE=true)")
            return self._environment
        
        # Priority 4: Heuristics
        if self._is_ci_environment():
            self._environment = Environment.TEST
            logger.info("Environment: TEST (CI environment detected)")
            return self._environment
        
        if self._is_pytest_running():
            self._environment = Environment.TEST
            logger.info("Environment: TEST (pytest detected)")
            return self._environment
        
        # Default: Development (safest for unknown)
        self._environment = Environment.DEVELOPMENT
        logger.info("Environment: DEVELOPMENT (default)")
        return self._environment
    
    def _is_ci_environment(self) -> bool:
        """
        Detect if running in a CI/CD environment.
        
        Checks for presence of common CI/CD environment variables used by
        major CI platforms including GitHub Actions, GitLab CI, Jenkins,
        CircleCI, Travis CI, and others.
        
        Returns:
            True if any CI environment variable is set, False otherwise
        
        Note:
            This is a heuristic check. Add more CI platforms as needed.
        """
        ci_vars = [
            "CI",              # Generic CI flag (most platforms)
            "GITHUB_ACTIONS",  # GitHub Actions
            "GITLAB_CI",       # GitLab CI
            "JENKINS_HOME",    # Jenkins
            "CIRCLECI",        # CircleCI
            "TRAVIS",          # Travis CI
            "BUILDKITE",       # Buildkite
        ]
        return any(os.getenv(var) for var in ci_vars)
    
    def _is_pytest_running(self) -> bool:
        """
        Detect if running under pytest test framework.
        
        Checks both sys.modules for pytest import and TESTING environment
        variable for explicit test mode indication.
        
        Returns:
            True if pytest is detected or TESTING=1, False otherwise
        
        Security Note:
            This should never be used as the sole production check, only as
            a fallback heuristic.
        """
        return "pytest" in sys.modules or os.getenv("TESTING") == "1"
    
    def is_production(self) -> bool:
        """
        Check if running in production environment.
        
        Returns:
            True if environment is production, False otherwise
        
        Examples:
            >>> if env_detector.is_production():
            ...     enable_production_monitoring()
        """
        return self.detect() == Environment.PRODUCTION
    
    def is_test(self) -> bool:
        """
        Check if running in test/CI environment.
        
        Returns:
            True if environment is test, False otherwise
        
        Examples:
            >>> if env_detector.is_test():
            ...     use_mock_external_services()
        """
        return self.detect() == Environment.TEST
    
    def is_staging(self) -> bool:
        """
        Check if running in staging environment.
        
        Returns:
            True if environment is staging, False otherwise
        
        Examples:
            >>> if env_detector.is_staging():
            ...     enable_debug_endpoints()
        """
        return self.detect() == Environment.STAGING
    
    def is_development(self) -> bool:
        """
        Check if running in development environment.
        
        Returns:
            True if environment is development, False otherwise
        
        Examples:
            >>> if env_detector.is_development():
            ...     enable_hot_reload()
        """
        return self.detect() == Environment.DEVELOPMENT
    
    def reset(self) -> None:
        """
        Reset cached environment detection.
        
        **WARNING**: This method should ONLY be used in test code to reset
        the detector state between test cases. Never call this in production code.
        
        Raises:
            RuntimeError: If called when is_production() is True (safety check)
        
        Examples:
            >>> # In test code only:
            >>> detector.reset()
            >>> os.environ["APP_ENV"] = "test"
            >>> assert detector.detect() == Environment.TEST
        """
        # Safety check: prevent reset in production
        if self._environment == Environment.PRODUCTION:
            raise RuntimeError(
                "Cannot reset environment detector in production mode. "
                "This operation is only allowed in test/development environments."
            )
        self._environment = None
        logger.debug("Environment detector cache cleared")


# ============================================================================
# Module-Level Singleton Instance
# ============================================================================
# Global singleton instance for convenient access throughout the application.
# This ensures a single source of truth for environment detection.

env_detector = EnvironmentDetector()


# ============================================================================
# Public API - Convenience Functions
# ============================================================================
# These functions provide a clean, functional API for environment detection
# without requiring direct instantiation of the EnvironmentDetector class.

def is_production() -> bool:
    """
    Check if application is running in production environment.
    
    This is the recommended way to check for production mode throughout
    the application. It uses the singleton detector for consistent results.
    
    Returns:
        True if environment is production, False otherwise
    
    Examples:
        >>> from server.environment import is_production
        >>> if is_production():
        ...     initialize_monitoring()
        ...     enable_rate_limiting()
    
    Note:
        This function is thread-safe and has O(1) performance after first call.
    """
    return env_detector.is_production()


def is_test() -> bool:
    """
    Check if application is running in test/CI environment.
    
    Use this to enable test-specific behavior such as mocking external
    services, using in-memory databases, or skipping expensive operations.
    
    Returns:
        True if environment is test, False otherwise
    
    Examples:
        >>> from server.environment import is_test
        >>> if is_test():
        ...     db_url = "sqlite:///:memory:"
        ... else:
        ...     db_url = os.getenv("DATABASE_URL")
    
    Security Note:
        Never use test mode for production deployments. This function helps
        ensure test-specific code doesn't run in production.
    """
    return env_detector.is_test()


def is_staging() -> bool:
    """
    Check if application is running in staging environment.
    
    Staging environments should mirror production as closely as possible
    while allowing additional debugging and testing capabilities.
    
    Returns:
        True if environment is staging, False otherwise
    
    Examples:
        >>> from server.environment import is_staging
        >>> if is_staging():
        ...     enable_detailed_logging()
        ...     allow_debug_endpoints()
    """
    return env_detector.is_staging()


def is_development() -> bool:
    """
    Check if application is running in development environment.
    
    Development mode enables features like hot reload, verbose logging,
    and relaxed validation for faster iteration.
    
    Returns:
        True if environment is development, False otherwise
    
    Examples:
        >>> from server.environment import is_development
        >>> if is_development():
        ...     app.debug = True
        ...     enable_auto_reload()
    """
    return env_detector.is_development()


def get_environment() -> Environment:
    """
    Get the current runtime environment.
    
    Returns the detected environment as an enum value, which is useful for
    switch-case style logic or when you need the actual environment value.
    
    Returns:
        Environment enum value (PRODUCTION, STAGING, DEVELOPMENT, or TEST)
    
    Examples:
        >>> from server.environment import get_environment, Environment
        >>> env = get_environment()
        >>> 
        >>> if env in (Environment.PRODUCTION, Environment.STAGING):
        ...     use_production_database()
        >>> 
        >>> log_level = {
        ...     Environment.PRODUCTION: "WARNING",
        ...     Environment.STAGING: "INFO",
        ...     Environment.DEVELOPMENT: "DEBUG",
        ...     Environment.TEST: "ERROR",
        ... }[env]
    
    Note:
        For simple boolean checks, prefer is_production(), is_test(), etc.
        Use this function when you need the actual environment value or are
        implementing complex conditional logic.
    """
    return env_detector.detect()


# ============================================================================
# Module Exports
# ============================================================================
__all__ = [
    "Environment",
    "EnvironmentDetector",
    "env_detector",
    "is_production",
    "is_test",
    "is_staging",
    "is_development",
    "get_environment",
]
