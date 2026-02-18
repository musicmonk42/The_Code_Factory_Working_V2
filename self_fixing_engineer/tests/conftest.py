# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""Pytest configuration for self_fixing_engineer tests."""
import asyncio
import gc
import os
from pathlib import Path

import psutil
import pytest
from prometheus_client import REGISTRY


@pytest.fixture(scope="session", autouse=True)
def ensure_checkpoint_test_paths():
    """Ensure checkpoint test directories and files exist.
    
    This fixture creates the necessary directories and files that checkpoint
    tests expect to exist. It runs once per test session before any tests start.
    """
    # Create test directory if specified in environment
    checkpoint_dir = os.environ.get("CHECKPOINT_DIR")
    if checkpoint_dir:
        checkpoint_path = Path(checkpoint_dir)
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        
        # Create audit.log parent directory and touch the file
        # This ensures the file exists for tests that check file existence
        audit_log_path = os.environ.get("CHECKPOINT_AUDIT_LOG_PATH")
        if audit_log_path:
            audit_log = Path(audit_log_path)
            audit_log.parent.mkdir(parents=True, exist_ok=True)
            # Touch the file to ensure it exists for tests that check file existence
            audit_log.touch(exist_ok=True)
        
        # Create dlq.jsonl parent directory and touch the file
        # This ensures the file exists for tests that check file existence
        dlq_path = os.environ.get("CHECKPOINT_DLQ_PATH")
        if dlq_path:
            dlq_file = Path(dlq_path)
            dlq_file.parent.mkdir(parents=True, exist_ok=True)
            # Touch the file to ensure it exists for tests that check file existence
            dlq_file.touch(exist_ok=True)
    
    yield


@pytest.fixture(autouse=True, scope="function")
async def cleanup_async_resources():
    """Force cleanup of async resources after each test."""
    yield
    
    # Cancel all pending tasks
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop running
        return
    
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    for task in tasks:
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass
    
    # Force garbage collection
    gc.collect()


@pytest.fixture(autouse=True, scope="function")
def cleanup_prometheus_registry():
    """Clear Prometheus registry before each test."""
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
    yield


@pytest.fixture(autouse=True, scope="function")
def aggressive_memory_cleanup():
    """Enhanced memory cleanup between tests to prevent OOM.
    
    This fixture runs after each test and forces multiple garbage collection cycles
    to ensure that heavy objects (quantum circuits, large datasets, etc.) are
    properly cleaned up before the next test starts.
    
    IMPORTANT: Does NOT delete modules from sys.modules or clear mock patches,
    as this can interfere with active test fixtures and mock patches used by
    pytest-mock, causing flaky tests (e.g., missing env vars, broken mocks).
    """
    yield
    # Force multiple GC passes
    gc.collect()
    gc.collect()
    gc.collect()


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
    
    IMPORTANT: Only cleans up modules that were actually imported during this
    session to avoid triggering heavy side-effect imports at teardown.
    """
    yield
    
    import sys
    
    # Cleanup fixer_ast background loop - only if already imported
    fixer_ast_key = "self_fixing_engineer.self_healing_import_fixer.import_fixer.fixer_ast"
    if fixer_ast_key in sys.modules:
        try:
            fixer_ast = sys.modules[fixer_ast_key]
            if hasattr(fixer_ast, '_shutdown_background_loop'):
                fixer_ast._shutdown_background_loop()
        except Exception:
            pass  # Ignore all errors during cleanup
    
    # Cleanup PluginManager background loops - only if already imported
    pm_key = "self_fixing_engineer.simulation.plugins.plugin_manager"
    if pm_key in sys.modules:
        try:
            pm_mod = sys.modules[pm_key]
            PluginManager = getattr(pm_mod, 'PluginManager', None)
            if PluginManager and hasattr(PluginManager, '_instances'):
                for instance in PluginManager._instances:
                    if hasattr(instance, 'stop_background_loop'):
                        instance.stop_background_loop()
        except Exception:
            pass  # Ignore all errors during cleanup
    
    # Cleanup TamperEvidentLogger instances - only if already imported
    audit_key = "self_fixing_engineer.arbiter.audit_log"
    if audit_key in sys.modules:
        try:
            audit_mod = sys.modules[audit_key]
            TamperEvidentLogger = getattr(audit_mod, 'TamperEvidentLogger', None)
            if TamperEvidentLogger:
                instance = getattr(TamperEvidentLogger, '_instance', None)
                if instance and hasattr(instance, 'shutdown'):
                    instance.shutdown()
        except Exception:
            pass  # Ignore all errors during cleanup
