# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Pytest configuration for self_fixing_engineer tests."""
import asyncio
import os

import psutil
import pytest


@pytest.fixture(autouse=True)
def monitor_memory(request):
    """Monitor memory usage per test.
    
    Only performs expensive memory measurement when PYTEST_MONITOR_MEMORY
    environment variable is set (e.g., PYTEST_MONITOR_MEMORY=1), to avoid
    psutil overhead on every test in CI.
    
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


@pytest.fixture(scope="session", autouse=True)
def cleanup_background_loops():
    """Clean up all background event loops after the test session.
    
    This fixture ensures that background loops from fixer_ast, plugin_manager,
    and audit_log are properly terminated to prevent test hangs.
    """
    yield
    
    # Cleanup fixer_ast background loop
    try:
        from self_fixing_engineer.self_healing_import_fixer.import_fixer import fixer_ast
        fixer_ast._shutdown_background_loop()
    except (ImportError, AttributeError):
        pass  # Module may not be imported in all test runs
    
    # Cleanup PluginManager background loops
    try:
        from self_fixing_engineer.simulation.plugins.plugin_manager import PluginManager
        # Try to stop any existing plugin manager instances
        if hasattr(PluginManager, '_instances'):
            for instance in PluginManager._instances:
                if hasattr(instance, 'stop_background_loop'):
                    instance.stop_background_loop()
    except (ImportError, AttributeError):
        pass  # Module may not be imported in all test runs
    
    # Cleanup TamperEvidentLogger instances
    try:
        from self_fixing_engineer.arbiter.audit_log import TamperEvidentLogger
        instance = TamperEvidentLogger._instance
        if instance and hasattr(instance, 'shutdown'):
            instance.shutdown()
    except (ImportError, AttributeError):
        pass  # Module may not be imported in all test runs
