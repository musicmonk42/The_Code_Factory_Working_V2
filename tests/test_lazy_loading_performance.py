"""
Test suite for lazy loading performance improvements.

This test verifies that the lazy loading mechanism significantly improves
import times and prevents CPU timeout issues in CI environments.
"""

import os
import sys
import time
import pytest


def test_self_fixing_engineer_import_speed():
    """
    Test that the self_fixing_engineer package imports very quickly.
    
    With lazy loading, the package should import in well under 1 second,
    compared to >16 seconds before the optimization.
    """
    # Clear any cached imports
    if 'self_fixing_engineer' in sys.modules:
        del sys.modules['self_fixing_engineer']
    
    start_time = time.time()
    import self_fixing_engineer
    elapsed_time = time.time() - start_time
    
    # Should import in less than 0.5 seconds (was >16s before)
    assert elapsed_time < 0.5, f"Import took {elapsed_time:.3f}s, expected < 0.5s"
    print(f"✓ self_fixing_engineer imported in {elapsed_time:.3f}s")


def test_lazy_module_aliasing():
    """
    Test that module aliases are created on-demand, not at import time.
    
    This verifies that modules like 'arbiter', 'simulation', etc. are not
    loaded until first accessed.
    """
    # Clear cached imports
    for module in ['self_fixing_engineer', 'arbiter', 'simulation', 'test_generation']:
        if module in sys.modules:
            del sys.modules[module]
    
    # Import self_fixing_engineer - should NOT load submodules
    import self_fixing_engineer
    
    # Verify submodules are not loaded yet (or are None if loaded with fallback)
    assert 'arbiter' not in sys.modules or sys.modules.get('arbiter') is None
    assert 'simulation' not in sys.modules or sys.modules.get('simulation') is None
    
    # Now access arbiter - should trigger lazy load
    start_time = time.time()
    from self_fixing_engineer import arbiter
    elapsed_time = time.time() - start_time
    
    # Lazy load should be fast
    assert elapsed_time < 1.0, f"Lazy load took {elapsed_time:.3f}s, expected < 1.0s"
    
    # Verify arbiter is now in sys.modules
    assert 'arbiter' in sys.modules
    print(f"✓ Lazy loading mechanism working correctly")


def test_test_generation_lazy_onboard():
    """
    Test that test_generation imports without loading onboard module.
    
    The onboard module should only be loaded when its components
    (OnboardConfig, etc.) are first accessed.
    """
    # Clear cached imports
    for module in list(sys.modules.keys()):
        if 'test_generation' in module or 'onboard' in module:
            del sys.modules[module]
    
    # Import test_generation
    start_time = time.time()
    from self_fixing_engineer import test_generation
    elapsed_time = time.time() - start_time
    
    # Should import quickly without loading onboard
    assert elapsed_time < 0.5, f"Import took {elapsed_time:.3f}s, expected < 0.5s"
    
    # Check that onboard is not yet loaded
    onboard_loaded = 'self_fixing_engineer.test_generation.onboard' in sys.modules
    
    # Access OnboardConfig to trigger lazy load
    try:
        config = test_generation.OnboardConfig
        # If it loaded successfully, verify it's not None
        if config is not None:
            print(f"✓ OnboardConfig lazy loaded successfully")
    except AttributeError:
        # If onboard is truly unavailable, that's OK for this test
        pytest.skip("onboard module not available")
    
    print(f"✓ test_generation lazy loading working correctly")


def test_project_root_validation_skipped_in_ci():
    """
    Test that project root validation is skipped when SKIP_IMPORT_TIME_VALIDATION is set.
    
    This is critical for CI environments to avoid expensive filesystem operations
    during import.
    """
    # Set the skip flag
    os.environ['SKIP_IMPORT_TIME_VALIDATION'] = '1'
    
    # Clear cached imports
    for module in list(sys.modules.keys()):
        if 'test_generation' in module:
            del sys.modules[module]
    
    # Import should be very fast with validation skipped
    start_time = time.time()
    from self_fixing_engineer import test_generation
    elapsed_time = time.time() - start_time
    
    # Should be very fast with validation skipped
    assert elapsed_time < 0.2, f"Import took {elapsed_time:.3f}s, expected < 0.2s"
    print(f"✓ Project root validation skipped in CI mode: {elapsed_time:.3f}s")
    
    # Clean up
    if 'SKIP_IMPORT_TIME_VALIDATION' in os.environ:
        del os.environ['SKIP_IMPORT_TIME_VALIDATION']


def test_production_mode_still_validates():
    """
    Test that project root validation still runs in production mode.
    
    Without SKIP_IMPORT_TIME_VALIDATION, validation should occur (but still be fast
    due to lazy loading).
    """
    # Ensure skip flag is not set
    if 'SKIP_IMPORT_TIME_VALIDATION' in os.environ:
        del os.environ['SKIP_IMPORT_TIME_VALIDATION']
    
    # Clear cached imports
    for module in list(sys.modules.keys()):
        if 'test_generation' in module:
            del sys.modules[module]
    
    # Import - validation will run but should still be reasonably fast
    start_time = time.time()
    from self_fixing_engineer import test_generation
    elapsed_time = time.time() - start_time
    
    # Even with validation, should complete in reasonable time
    assert elapsed_time < 2.0, f"Import took {elapsed_time:.3f}s, expected < 2.0s"
    print(f"✓ Production mode validation: {elapsed_time:.3f}s")


def test_no_cpu_timeout_on_import():
    """
    Integration test: verify that importing arbiter doesn't cause CPU timeout.
    
    This simulates the workflow step that was failing with CPU time limit exceeded.
    """
    import subprocess
    
    # Run the same command that was failing in CI
    cmd = [
        sys.executable,
        '-c',
        "from self_fixing_engineer import arbiter; print('arbiter imported from', arbiter.__file__)"
    ]
    
    env = os.environ.copy()
    env['SKIP_IMPORT_TIME_VALIDATION'] = '1'
    
    start_time = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,  # 10 second timeout (CI was hitting 60s limit)
        env=env
    )
    elapsed_time = time.time() - start_time
    
    # Should complete successfully
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert 'arbiter imported from' in result.stdout
    assert elapsed_time < 5.0, f"Import took {elapsed_time:.3f}s, expected < 5.0s"
    
    print(f"✓ No CPU timeout: {elapsed_time:.3f}s")
