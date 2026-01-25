#!/usr/bin/env python3
"""
Test script to validate logging fixes for PII redaction issues.
"""

import logging
import sys
import os

# Add the server directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))

from logging_config import LevelPrefixFormatter


def test_formatter_handles_corrupted_format_string():
    """Test that LevelPrefixFormatter handles corrupted format strings gracefully."""
    print("Testing formatter with corrupted format string...")
    
    formatter = LevelPrefixFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Create a log record with a format string that will fail
    # Simulate what happens when Presidio redacts %d or %s
    record = logging.LogRecord(
        name='test.module',
        level=logging.INFO,
        pathname='/test/path.py',
        lineno=10,
        msg='Loaded [REDACTED] plugins from [REDACTED]',  # Corrupted format string
        args=(5, '/path/to/plugins'),  # Args that won't match the corrupted string
        exc_info=None
    )
    
    # This should not raise an exception
    try:
        result = formatter.format(record)
        print(f"✓ Formatter handled corrupted format string successfully")
        print(f"  Result: {result}")
        assert '[inf]' in result or '[err]' in result, "Expected prefix in formatted message"
        print(f"✓ Prefix correctly added")
        return True
    except Exception as e:
        print(f"✗ Formatter raised exception: {e}")
        return False


def test_formatter_handles_normal_format_string():
    """Test that formatter still works correctly with normal format strings."""
    print("\nTesting formatter with normal format string...")
    
    formatter = LevelPrefixFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Create a normal log record
    record = logging.LogRecord(
        name='test.module',
        level=logging.INFO,
        pathname='/test/path.py',
        lineno=10,
        msg='Loaded %d plugins from %s',
        args=(5, '/path/to/plugins'),
        exc_info=None
    )
    
    try:
        result = formatter.format(record)
        print(f"✓ Formatter handled normal format string successfully")
        print(f"  Result: {result}")
        assert 'Loaded 5 plugins from /path/to/plugins' in result, "Expected formatted message"
        assert '[inf]' in result, "Expected [inf] prefix"
        print(f"✓ Message correctly formatted")
        return True
    except Exception as e:
        print(f"✗ Formatter raised exception: {e}")
        return False


def test_formatter_with_error_level():
    """Test that formatter uses correct prefix for error level."""
    print("\nTesting formatter with error level...")
    
    formatter = LevelPrefixFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    record = logging.LogRecord(
        name='test.module',
        level=logging.ERROR,
        pathname='/test/path.py',
        lineno=10,
        msg='Something went wrong',
        args=(),
        exc_info=None
    )
    
    try:
        result = formatter.format(record)
        print(f"✓ Formatter handled error level successfully")
        print(f"  Result: {result}")
        assert '[err]' in result, "Expected [err] prefix for ERROR level"
        print(f"✓ Correct [err] prefix for ERROR level")
        return True
    except Exception as e:
        print(f"✗ Formatter raised exception: {e}")
        return False


def test_logger_api_usage():
    """Test that logger calls work without 'message=' keyword argument."""
    print("\nTesting logger API usage...")
    
    # Configure a simple logger
    test_logger = logging.getLogger('test.api')
    test_logger.setLevel(logging.DEBUG)
    
    # Remove any existing handlers
    test_logger.handlers.clear()
    
    # Add a handler with our formatter
    handler = logging.StreamHandler(sys.stdout)
    formatter = LevelPrefixFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    test_logger.addHandler(handler)
    
    try:
        # This should work (correct usage)
        test_logger.warning("This is a warning message")
        print(f"✓ Logger call without keyword argument works")
        
        # This should also work (with format args)
        test_logger.info("Processing %d items", 10)
        print(f"✓ Logger call with format args works")
        
        return True
    except Exception as e:
        print(f"✗ Logger call raised exception: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing Logging Fixes for PII Redaction Issues")
    print("=" * 70)
    
    results = []
    
    results.append(("Corrupted format string handling", test_formatter_handles_corrupted_format_string()))
    results.append(("Normal format string handling", test_formatter_handles_normal_format_string()))
    results.append(("Error level prefix", test_formatter_with_error_level()))
    results.append(("Logger API usage", test_logger_api_usage()))
    
    print("\n" + "=" * 70)
    print("Test Results Summary")
    print("=" * 70)
    
    all_passed = True
    for test_name, passed in results:
        status = "PASS" if passed else "FAIL"
        symbol = "✓" if passed else "✗"
        print(f"{symbol} {test_name}: {status}")
        if not passed:
            all_passed = False
    
    print("=" * 70)
    
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed!")
        return 1


if __name__ == '__main__':
    sys.exit(main())
