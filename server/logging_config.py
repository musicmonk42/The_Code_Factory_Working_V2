"""
Enterprise-Grade Logging Configuration
=======================================

This module provides production-ready logging configuration with accurate level
prefixes, proper stream separation, and comprehensive formatting for debugging
and monitoring in distributed systems.

**Problem Solved**:
    The default logging configuration was mixing log levels (INFO logs going to
    stderr with [err] prefix), breaking alerting systems that rely on stderr
    for error detection and causing false positives in monitoring dashboards.

**Solution**:
    - Separate handlers for INFO/DEBUG (stdout) and WARNING/ERROR/CRITICAL (stderr)
    - Accurate [inf]/[err] prefixes based on actual log level
    - Structured formatting with timestamp, module, level, and message
    - Thread-safe configuration (no race conditions)

**Benefits**:
    ✅ Correct alerting (only real errors trigger alerts)
    ✅ Clean log aggregation (easy filtering by stream)
    ✅ Debugging-friendly (clear visual distinction between info and errors)
    ✅ Production-ready (follows industry best practices)

**Usage**:
    >>> from server.logging_config import configure_logging
    >>> configure_logging()  # Call once at application startup
    >>> 
    >>> import logging
    >>> logger = logging.getLogger(__name__)
    >>> logger.info("This goes to stdout with [inf] prefix")
    >>> logger.error("This goes to stderr with [err] prefix")

**Module Version**: 1.0.0
**Author**: Code Factory Platform Team
**Last Updated**: 2026-01-23
**Standards Compliance**: Follows 12-Factor App logging guidelines
"""
import logging
import sys
from typing import Optional

# Loggers that should have their handlers cleared to prevent duplicates
# These are the main module-level loggers in the application
MANAGED_LOGGERS = [
    'generator',
    'arbiter', 
    'runner',
    'omnicore_engine',
    'server',
]


class LevelPrefixFormatter(logging.Formatter):
    """
    Custom formatter that adds accurate [inf] or [err] prefix based on log level.
    
    This formatter ensures log prefixes correctly match the severity level,
    making it easy to filter logs visually and programmatically.
    
    **Prefix Rules**:
        [inf] = DEBUG (10), INFO (20)
        [err] = WARNING (30), ERROR (40), CRITICAL (50)
    
    **Format Template**:
        [prefix]  YYYY-MM-DD HH:MM:SS,mmm - module.name - LEVEL - message
    
    **Thread Safety**: Fully thread-safe (no shared mutable state).
    **Performance**: O(1) level check and string formatting.
    
    Examples:
        >>> formatter = LevelPrefixFormatter('%(asctime)s - %(message)s')
        >>> record = logging.LogRecord(
        ...     name='test', level=logging.INFO, pathname='', lineno=0,
        ...     msg='Hello', args=(), exc_info=None
        ... )
        >>> formatted = formatter.format(record)
        >>> assert formatted.startswith('[inf]')
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record with appropriate level prefix.
        
        Args:
            record: LogRecord instance containing log data
        
        Returns:
            Formatted string with [inf] or [err] prefix
        
        Note:
            This method is called for every log message, so it must be fast.
            Current implementation is O(1) with minimal overhead.
        """
        # Determine prefix based on level (fast integer comparison)
        if record.levelno <= logging.INFO:  # DEBUG=10, INFO=20
            prefix = "[inf]"
        else:  # WARNING=30, ERROR=40, CRITICAL=50
            prefix = "[err]"
        
        # Format message using parent class formatter
        formatted = super().format(record)
        
        # Prepend prefix with two spaces for visual alignment
        return f"{prefix}  {formatted}"


class InfoFilter(logging.Filter):
    """
    Filter that only allows INFO and DEBUG level messages.
    
    This filter is used with the stdout handler to ensure only
    informational messages go to stdout, while warnings and errors
    go to stderr via a separate handler.
    
    **Performance**: O(1) level check per log record.
    **Thread Safety**: Fully thread-safe (stateless).
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Determine if record should be logged.
        
        Args:
            record: LogRecord to evaluate
        
        Returns:
            True if level is INFO or DEBUG, False otherwise
        """
        return record.levelno <= logging.INFO


def configure_logging(
    level: int = logging.INFO,
    format_string: Optional[str] = None
) -> None:
    """
    Configure root logger with proper level prefixes and stream separation.
    
    This function sets up enterprise-grade logging with:
    - Separate stdout handler for INFO/DEBUG (with [inf] prefix)
    - Separate stderr handler for WARNING/ERROR/CRITICAL (with [err] prefix)
    - Proper formatting with timestamps and module names
    - Thread-safe configuration
    
    **Call Once**: This should be called exactly once at application startup,
    before any logging occurs. Calling multiple times will add duplicate handlers.
    
    **Stream Separation Benefits**:
        - Monitoring systems can watch stderr for errors only
        - Log aggregation tools can separate info from errors
        - Shell redirection works intuitively (2>&1 to combine)
        - Container orchestrators can route streams differently
    
    Args:
        level: Minimum log level (default: INFO)
        format_string: Custom format string (default: industry standard format)
    
    Examples:
        >>> # Basic usage (call at app startup)
        >>> configure_logging()
        >>> 
        >>> # Custom format
        >>> configure_logging(
        ...     level=logging.DEBUG,
        ...     format_string='%(asctime)s [%(levelname)s] %(message)s'
        ... )
    
    Note:
        This function modifies the root logger, affecting all loggers in the
        application. If you need module-specific configuration, do it after
        calling this function.
    
    Security:
        Ensure log messages don't contain sensitive data (passwords, tokens, PII).
        Use logger.debug() for verbose output that may contain sensitive info.
    """
    # Default format follows industry best practices
    if format_string is None:
        format_string = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Create formatter instance
    formatter = LevelPrefixFormatter(format_string)
    
    # ========================================================================
    # INFO Handler: stdout for informational messages
    # ========================================================================
    info_handler = logging.StreamHandler(sys.stdout)
    info_handler.setLevel(logging.DEBUG)  # Allow DEBUG and INFO
    info_handler.addFilter(InfoFilter())   # Filter out WARNING+
    info_handler.setFormatter(formatter)
    
    # ========================================================================
    # ERROR Handler: stderr for warnings and errors
    # ========================================================================
    error_handler = logging.StreamHandler(sys.stderr)
    error_handler.setLevel(logging.WARNING)  # Only WARNING and above
    error_handler.setFormatter(formatter)
    
    # ========================================================================
    # Configure Root Logger
    # ========================================================================
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Clear existing handlers to prevent duplicates
    # This is important if configure_logging() is called multiple times
    root_logger.handlers.clear()
    
    # Add new handlers
    root_logger.handlers = [info_handler, error_handler]
    
    # Prevent propagation for specific loggers to avoid duplicates
    # Use the MANAGED_LOGGERS constant for maintainability
    for logger_name in MANAGED_LOGGERS:
        specific_logger = logging.getLogger(logger_name)
        # Don't set propagate=False as it would prevent logging
        # Just ensure no duplicate handlers at this level
        specific_logger.handlers = []
    
    # Log configuration success
    logger = logging.getLogger(__name__)
    logger.info("Logging configuration applied successfully")
    logger.info(f"Log level: {logging.getLevelName(level)}")
    logger.info("Stream separation: INFO→stdout, WARNING+→stderr")
    logger.debug("Debug logging is enabled")


def reset_logging() -> None:
    """
    Reset logging configuration to defaults.
    
    **WARNING**: This is intended for testing only. Never call in production.
    
    This function removes all handlers and resets the root logger to its
    default state, which is useful for test isolation but dangerous in
    production where it could cause log loss.
    
    Raises:
        RuntimeError: If called in production environment
    
    Examples:
        >>> # In test code only:
        >>> reset_logging()
        >>> # Reconfigure with custom settings
        >>> configure_logging(level=logging.DEBUG)
    """
    # Safety check: prevent reset in production
    try:
        from server.environment import is_production
        if is_production():
            raise RuntimeError(
                "Cannot reset logging in production. "
                "This operation is only allowed in test/development."
            )
    except ImportError:
        # Environment module not available, allow reset
        pass
    
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.WARNING)  # Back to default


# ============================================================================
# Module Exports
# ============================================================================
__all__ = [
    "configure_logging",
    "reset_logging",
    "LevelPrefixFormatter",
    "InfoFilter",
]
