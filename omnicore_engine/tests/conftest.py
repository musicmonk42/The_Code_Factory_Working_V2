# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test configuration for omnicore_engine tests.
Provides fixtures and hooks to ensure proper test isolation.
"""

import gc
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
async def cleanup_event_loops():
    """Ensure async tasks are properly cleaned up between tests.

    Using an async fixture ensures teardown runs within the same event-loop
    that the test used, so pending tasks are visible and can be cancelled
    before pytest-asyncio finalises the loop.  Running a sync fixture for
    this purpose is unreliable in Python 3.11 + pytest-asyncio ≥ 0.23
    because asyncio.get_event_loop() no longer reliably returns the loop
    that was active during the async test.
    """
    yield

    # Cancel every task that was left running by the test (but not our own task).
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if not t.done() and t is not current]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

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
