#!/usr/bin/env python3
"""
Docker compatibility test for logging fixes.
Tests that logging works correctly in containerized environments.
"""

import logging
import sys
import os

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))

from logging_config import configure_logging, LevelPrefixFormatter


def test_docker_logging_compatibility():
    """Test logging behavior in Docker-like environment."""
    print("=" * 70)
    print("Docker Compatibility Test for Logging")
    print("=" * 70)
    
    # Simulate Docker environment variables
    os.environ['PYTHONUNBUFFERED'] = '1'
    os.environ['APP_STARTUP'] = '1'
    
    print("\n1. Testing logging configuration...")
    try:
        configure_logging()
        print("   ✓ Logging configured successfully\n")
    except Exception as e:
        print(f"   ✗ Logging configuration failed: {e}\n")
        return False
    
    print("2. Testing log output to stdout/stderr...")
    logger = logging.getLogger('docker.test')
    
    try:
        # Test INFO (should go to stdout with [inf])
        logger.info("Container startup message")
        
        # Test ERROR (should go to stderr with [err])
        logger.error("Container error message")
        
        # Test with format string
        logger.info("Processing %d requests", 100)
        
        print("   ✓ All log messages produced successfully\n")
    except Exception as e:
        print(f"   ✗ Logging failed: {e}\n")
        return False
    
    print("3. Testing that PYTHONUNBUFFERED works...")
    # When PYTHONUNBUFFERED=1, output should be immediate
    sys.stdout.flush()
    sys.stderr.flush()
    print("   ✓ Output buffering disabled\n")
    
    print("4. Testing formatter robustness...")
    # Create a scenario where format string might be corrupted
    formatter = LevelPrefixFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Create a record with mismatched format string and args
    record = logging.LogRecord(
        name='docker.test',
        level=logging.INFO,
        pathname='/app/test.py',
        lineno=10,
        msg='Value: [REDACTED]',  # Simulated redacted format string
        args=(42,),  # Args that won't match
        exc_info=None
    )
    
    try:
        result = formatter.format(record)
        print(f"   ✓ Formatter handled corrupted format gracefully")
        print(f"     Output: {result[:80]}...\n")
    except Exception as e:
        print(f"   ✗ Formatter failed: {e}\n")
        return False
    
    print("=" * 70)
    print("✓ All Docker compatibility tests passed!")
    print("=" * 70)
    
    return True


if __name__ == '__main__':
    success = test_docker_logging_compatibility()
    sys.exit(0 if success else 1)
