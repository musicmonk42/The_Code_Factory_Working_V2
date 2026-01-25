#!/usr/bin/env python3
"""
Integration test to simulate PII redaction and logging together.
"""

import logging
import sys
import os
import re

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'generator', 'runner'))

from logging_config import LevelPrefixFormatter


class SimpleRedactionFilter(logging.Filter):
    """Simplified version of RedactionFilter for testing."""
    
    def __init__(self):
        super().__init__()
        # Simulate what happens when %d and %s get redacted
        self.patterns = [
            re.compile(r'%[ds]'),  # Simulate redaction of format placeholders
        ]
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Redact sensitive patterns from log messages."""
        try:
            # Redact the message string
            if isinstance(record.msg, str):
                for pattern in self.patterns:
                    record.msg = pattern.sub('[REDACTED]', record.msg)
        except Exception as e:
            print(f"Error in SimpleRedactionFilter: {e}", file=sys.stderr)
        return True


def test_logging_with_redaction():
    """Test that logging works correctly even when redaction corrupts format strings."""
    print("=" * 70)
    print("Integration Test: Logging with PII Redaction")
    print("=" * 70)
    
    # Create a logger with our formatter
    logger = logging.getLogger('integration.test')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    
    # Create handler with our custom formatter
    handler = logging.StreamHandler(sys.stdout)
    formatter = LevelPrefixFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    
    # Add redaction filter BEFORE formatter
    handler.addFilter(SimpleRedactionFilter())
    
    logger.addHandler(handler)
    
    print("\n1. Testing with format string that will be corrupted by redaction:")
    print("   Original message: 'Loaded %d plugins from %s'")
    print("   After redaction: 'Loaded [REDACTED] plugins from [REDACTED]'")
    print("   Expected: Should not crash, should log gracefully\n")
    
    try:
        # This log call uses format string with %d and %s
        # The redaction filter will corrupt these to [REDACTED]
        # The formatter should handle this gracefully
        logger.info("Loaded %d plugins from %s", 5, "/path/to/plugins")
        print("   ✓ Log call with corrupted format string succeeded\n")
    except Exception as e:
        print(f"   ✗ Log call failed: {e}\n")
        return False
    
    print("2. Testing with normal string (no format args):")
    try:
        logger.warning("This is a simple warning message")
        print("   ✓ Simple log call succeeded\n")
    except Exception as e:
        print(f"   ✗ Simple log call failed: {e}\n")
        return False
    
    print("3. Testing with error level:")
    try:
        logger.error("An error occurred")
        print("   ✓ Error log call succeeded\n")
    except Exception as e:
        print(f"   ✗ Error log call failed: {e}\n")
        return False
    
    print("=" * 70)
    print("✓ All integration tests passed!")
    print("=" * 70)
    
    return True


if __name__ == '__main__':
    success = test_logging_with_redaction()
    sys.exit(0 if success else 1)
