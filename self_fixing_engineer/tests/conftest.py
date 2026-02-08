# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Pytest configuration for self_fixing_engineer tests."""
import os

import psutil
import pytest


@pytest.fixture(autouse=True)
def monitor_memory(request):
    """Monitor memory usage per test.
    
    Only performs expensive memory measurement when PYTEST_MONITOR_MEMORY=1 is set,
    to avoid psutil overhead on every test in CI.
    
    Warns when a test consumes more than 500MB of memory.
    The 500MB threshold was chosen based on:
    - Typical test should use < 100MB
    - 500MB indicates potential memory leak or excessive resource usage
    - Helps identify tests that may cause OOM in CI environments
    """
    if not os.getenv('PYTEST_MONITOR_MEMORY'):
        yield
        return
    
    process = psutil.Process()
    mem_before = process.memory_info().rss / 1024 / 1024  # MB
    
    yield
    
    mem_after = process.memory_info().rss / 1024 / 1024  # MB
    mem_used = mem_after - mem_before
    
    # Warning threshold: 500MB - can be adjusted via PYTEST_MEMORY_THRESHOLD env var
    threshold = int(os.getenv('PYTEST_MEMORY_THRESHOLD', '500'))
    if mem_used > threshold:
        test_name = request.node.name
        print(f"\n⚠️ WARNING: Test '{test_name}' used {mem_used:.1f} MB of memory")
