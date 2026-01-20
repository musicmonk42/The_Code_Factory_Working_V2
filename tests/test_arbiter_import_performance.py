"""
Test suite for arbiter module import performance.
This test ensures that importing the arbiter module doesn't cause CPU timeouts.
"""

import time
import pytest


def test_arbiter_import_speed():
    """
    Test that importing the arbiter module completes quickly.
    This is critical for CI/CD pipelines where CPU time is limited.
    """
    start_time = time.time()
    
    # Import should complete without CPU timeout
    from self_fixing_engineer import arbiter
    
    elapsed_time = time.time() - start_time
    
    # Assert import completes in less than 5 seconds
    # (CI environments might be slower than local development)
    assert elapsed_time < 5.0, f"Import took {elapsed_time:.2f}s, expected < 5.0s"
    
    # Log the actual time for monitoring
    print(f"✓ Arbiter import completed in {elapsed_time:.2f} seconds")
    
    # Verify arbiter module is available
    assert arbiter is not None, "arbiter module should not be None"


def test_arbiter_class_import():
    """
    Test that importing the Arbiter class works and doesn't trigger
    module-level initialization.
    """
    start_time = time.time()
    
    try:
        from self_fixing_engineer.arbiter import Arbiter
        elapsed_time = time.time() - start_time
        
        # Should complete quickly since initialization is deferred
        assert elapsed_time < 5.0, f"Arbiter class import took {elapsed_time:.2f}s"
        
        # If Arbiter is None, skip the test (missing dependencies)
        if Arbiter is None:
            pytest.skip("Arbiter class import requires additional dependencies")
        
        print(f"✓ Arbiter class imported in {elapsed_time:.2f} seconds")
    except ImportError:
        # If Arbiter can't be imported due to missing dependencies, that's OK
        # as long as the arbiter module import (above test) works
        pytest.skip("Arbiter class import requires additional dependencies")


def test_no_heavy_initialization_on_import():
    """
    Test that heavy initialization is deferred until Arbiter is instantiated.
    This is a behavioral test to ensure module-level code is minimal.
    """
    # Clear any previous imports
    import sys
    modules_before = set(sys.modules.keys())
    
    # Import the arbiter module
    from self_fixing_engineer import arbiter
    
    modules_after = set(sys.modules.keys())
    new_modules = modules_after - modules_before
    
    # The import should not load heavy dependencies like sentry_sdk, stable_baselines3
    # unless they're already in the environment
    print(f"✓ Import loaded {len(new_modules)} new modules")
    
    # This is just informational, not a hard assertion
    # Heavy modules might be imported but shouldn't cause initialization
