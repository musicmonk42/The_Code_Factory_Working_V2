# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Pytest configuration for self_fixing_engineer tests."""
import psutil
import pytest


@pytest.fixture(autouse=True)
def monitor_memory(request):
    """Monitor memory usage per test."""
    process = psutil.Process()
    mem_before = process.memory_info().rss / 1024 / 1024  # MB
    
    yield
    
    mem_after = process.memory_info().rss / 1024 / 1024  # MB
    mem_used = mem_after - mem_before
    
    if mem_used > 500:  # Warn if test used > 500 MB
        test_name = request.node.name
        print(f"\n⚠️ WARNING: Test '{test_name}' used {mem_used:.1f} MB of memory")
