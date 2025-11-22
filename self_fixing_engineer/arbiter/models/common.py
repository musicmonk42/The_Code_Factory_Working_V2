"""
Common models and enums shared across the arbiter module.

This module provides canonical definitions for enums and data structures
used throughout the arbiter system to prevent duplication and inconsistencies.
"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    """
    Canonical severity enum for the arbiter system.

    This enum consolidates severity levels used across different components:
    - DEBUG: Diagnostic information for troubleshooting
    - INFO: General informational messages
    - LOW: Low severity issues (from bug tracking)
    - MEDIUM: Medium severity issues (from bug tracking)
    - HIGH: High severity issues (from bug tracking)
    - WARN: Warning messages (from feedback handlers)
    - ERROR: Error conditions (from feedback handlers)
    - CRITICAL: Critical issues requiring immediate attention
    """

    DEBUG = "debug"
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"

    @classmethod
    def from_string(cls, s: str) -> "Severity":
        """
        Converts a string to a Severity enum member.

        Args:
            s: String representation of severity level

        Returns:
            Severity enum member

        Raises:
            KeyError: If the string doesn't match any severity level
        """
        try:
            # Try by name first (uppercase)
            return cls[s.upper()]
        except KeyError:
            try:
                # Try by value (lowercase)
                return cls(s.lower())
            except ValueError:
                logger.warning(f"Invalid severity string '{s}', defaulting to MEDIUM.")
                return cls.MEDIUM
