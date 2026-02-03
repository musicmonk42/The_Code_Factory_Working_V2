"""Test that background loop is not created during test collection."""
import os
import threading


def test_no_background_loop_in_test_mode():
    """Verify background loop is not created when TESTING=1."""
    # Ensure TESTING is set (should be set by conftest.py)
    assert os.getenv("TESTING") == "1", "TESTING env var should be set by conftest.py"
    
    # Import happens here, after TESTING is set
    from self_fixing_engineer.self_healing_import_fixer.import_fixer import fixer_ast
    
    # Check that no background thread is running
    bg_threads = [t for t in threading.enumerate() if "fixer-bg-loop" in t.name]
    assert len(bg_threads) == 0, f"Found unexpected background threads: {bg_threads}"
    
    # Check that _ensure_background_loop returns None
    loop = fixer_ast._ensure_background_loop()
    assert loop is None, "Background loop should be None in test mode"


def test_run_async_in_sync_fallback():
    """Verify _run_async_in_sync falls back to asyncio.run() in test mode."""
    import asyncio
    from self_fixing_engineer.self_healing_import_fixer.import_fixer import fixer_ast
    
    async def sample_coro():
        await asyncio.sleep(0.001)
        return "test_result"
    
    # This should use asyncio.run() instead of background loop
    result = fixer_ast._run_async_in_sync(sample_coro())
    assert result == "test_result", f"Expected 'test_result', got {result}"
    
    # Still no background threads should exist
    bg_threads = [t for t in threading.enumerate() if "fixer-bg-loop" in t.name]
    assert len(bg_threads) == 0, f"Found unexpected background threads after async call: {bg_threads}"
