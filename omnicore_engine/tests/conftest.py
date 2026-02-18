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


# NOTE: The class_level_isolation fixture was removed because it caused race conditions
# when running tests in parallel with pytest-xdist. The fixture manipulated sys.modules
# at class scope, which created teardown conflicts between workers, resulting in errors
# like "previous item was not torn down properly". The existing function-level fixtures
# (reset_test_state and cleanup_event_loops) along with pytest_runtest_teardown hook
# provide sufficient test isolation for both serial and parallel execution.


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
