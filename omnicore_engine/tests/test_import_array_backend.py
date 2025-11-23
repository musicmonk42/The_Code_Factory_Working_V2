"""
Test to ensure omnicore_engine.array_backend can be imported without errors.

This test validates that the array_backend module can be imported safely
even in test environments where ArbiterConfig may not be fully initialized
or where the config_instance global may not exist.
"""

import importlib
import sys


def test_import_array_backend():
    """
    Test that array_backend can be imported without NameError or other import-time errors.
    
    This test ensures that the defensive import pattern in array_backend.py
    works correctly and doesn't fail with NameError: config_instance not defined.
    """
    # Clear the module from cache if it exists to test fresh import
    if "omnicore_engine.array_backend" in sys.modules:
        del sys.modules["omnicore_engine.array_backend"]
    
    # This should not raise any errors
    import omnicore_engine.array_backend
    
    # Verify key components are available
    assert hasattr(omnicore_engine.array_backend, "ArrayBackend")
    assert hasattr(omnicore_engine.array_backend, "backend")
    assert hasattr(omnicore_engine.array_backend, "xp")
    
    # Verify settings fallback works
    assert hasattr(omnicore_engine.array_backend, "settings")
    settings_obj = omnicore_engine.array_backend.settings()
    
    # Should have at least log_level attribute (either from ArbiterConfig or SimpleNamespace)
    assert hasattr(settings_obj, "log_level")


def test_array_backend_instantiation():
    """
    Test that ArrayBackend can be instantiated without errors.
    """
    from omnicore_engine.array_backend import ArrayBackend
    
    # Should be able to create an instance
    backend = ArrayBackend(mode="numpy")
    
    # Verify basic functionality
    assert backend.xp is not None
    arr = backend.zeros((3, 3))
    assert arr.shape == (3, 3)


def test_defensive_settings():
    """
    Test that settings object has required attributes even when ArbiterConfig fails.
    """
    from omnicore_engine.array_backend import settings
    
    # These should always be available (either from ArbiterConfig or fallback)
    settings_obj = settings()
    assert hasattr(settings_obj, "log_level")
    assert hasattr(settings_obj, "enable_array_backend_benchmarking")


def test_module_imports_cleanly():
    """
    Test that the module can be imported without triggering backend initialization.
    This ensures import-time side effects are minimal.
    """
    import importlib
    
    # Clear module cache
    if "omnicore_engine.array_backend" in sys.modules:
        del sys.modules["omnicore_engine.array_backend"]
    
    # Import should not trigger backend creation
    m = importlib.import_module("omnicore_engine.array_backend")
    
    # cp should be defined (even if None) for test patching
    assert hasattr(m, "cp")
    
    # Backend should be a proxy, not the actual backend instance yet
    assert hasattr(m, "backend")
    
    # xp should be None initially (not yet initialized)
    assert m.xp is None
    
    # is_gpu should be False initially
    assert m.is_gpu is False
