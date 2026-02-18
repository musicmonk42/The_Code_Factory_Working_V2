# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test configuration for omnicore_engine tests.
Provides fixtures and hooks to ensure proper test isolation.
"""

import gc
import sys
import asyncio
import pytest


@pytest.fixture(scope="function", autouse=True)
def reset_test_state():
    """Reset test state between test functions to prevent side effects."""
    # Run before each test
    gc.collect()
    
    yield
    
    # Run after each test
    gc.collect()


@pytest.fixture(scope="function", autouse=True)
def cleanup_event_loops():
    """Ensure event loops are properly cleaned up between tests."""
    yield
    
    # Close any remaining event loops
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.stop()
        if not loop.is_closed():
            loop.close()
    except RuntimeError:
        # No event loop in current thread
        pass
    
    # Force garbage collection to clean up any remaining async resources
    gc.collect()


@pytest.fixture(scope="class", autouse=True)
def class_level_isolation():
    """Provide isolation at the class level for test classes."""
    # Setup
    modules_before = set(sys.modules.keys())
    
    yield
    
    # Teardown - clean up any test-specific module imports
    modules_after = set(sys.modules.keys())
    new_modules = modules_after - modules_before
    
    for mod_name in new_modules:
        # Only clean up test-related modules
        if 'test_' in mod_name or '_mock' in mod_name:
            sys.modules.pop(mod_name, None)
    
    gc.collect()


def pytest_runtest_teardown(item, nextitem):
    """Hook that runs after each test item.
    
    Ensures proper cleanup between tests, especially important for
    pytest-xdist parallel execution.
    """
    # Force garbage collection after each test
    gc.collect()
    
    # If moving to a different test file, do more aggressive cleanup
    if nextitem is None or item.fspath != nextitem.fspath:
        gc.collect()
        gc.collect()  # Run twice for thorough cleanup
