# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test configuration for omnicore_engine tests.
Provides fixtures and hooks to ensure proper test isolation.
"""

import gc
import threading
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
    """Ensure proper cleanup between tests.

    This fixture is intentionally synchronous so that it does not force
    an event-loop context onto sync tests (which would cause
    "previous item was not torn down properly" errors from pytest-asyncio
    when async and sync tests are interleaved).  pytest-asyncio >= 0.21
    with asyncio_default_fixture_loop_scope="function" automatically
    manages event-loop teardown for async tests, so explicit task
    cancellation here is no longer required.
    """
    yield

    # Force garbage collection to clean up any remaining async resources
    gc.collect()


@pytest.fixture(scope="function", autouse=True)
def check_no_leaked_nondaemon_threads():
    """Fail if a test leaks non-daemon threads that could outlive the event loop."""
    before = {t.ident for t in threading.enumerate() if not t.daemon}
    yield
    leaked = [
        t for t in threading.enumerate()
        if not t.daemon and t.ident not in before and t.is_alive()
    ]
    assert not leaked, (
        f"Test leaked {len(leaked)} non-daemon thread(s): "
        + ", ".join(f"{t.name} (ident={t.ident})" for t in leaked)
    )


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
