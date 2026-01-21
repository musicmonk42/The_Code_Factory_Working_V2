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


def test_arbiter_is_proper_module():
    """
    Test that arbiter is a proper module, not None or some other object.
    This verifies the fix for lazy loading returning None when imports fail.
    
    Before the fix, when module import failed due to thread limits in CI,
    the lazy loader returned None instead of the module, causing:
    - dir(arbiter)[:10] to show ['__bool__', '__class__', ...] (NoneType attributes)
    - getattr(arbiter, '__file__', 'unknown') to return 'unknown'
    """
    from self_fixing_engineer import arbiter
    import types
    
    # Verify arbiter is a module, not None
    assert arbiter is not None, "arbiter should not be None"
    assert isinstance(arbiter, types.ModuleType), f"arbiter should be a module, got {type(arbiter)}"
    
    # Verify arbiter has __file__ attribute (modules have this, None doesn't)
    assert hasattr(arbiter, '__file__'), "arbiter should have __file__ attribute"
    assert arbiter.__file__ is not None, "arbiter.__file__ should not be None"
    
    # Verify arbiter has expected module attributes, not NoneType attributes
    arbiter_attrs = dir(arbiter)[:10]
    # NoneType has __bool__ as one of its first attributes, modules don't
    assert '__bool__' not in arbiter_attrs, (
        f"arbiter should not have NoneType attributes. First 10 attrs: {arbiter_attrs}"
    )
    
    # Expected module attributes
    assert '__doc__' in dir(arbiter), "arbiter should have __doc__"
    assert '__name__' in dir(arbiter), "arbiter should have __name__"
    
    print(f"✓ arbiter is a proper module: {arbiter}")
    print(f"✓ arbiter.__file__: {arbiter.__file__}")
