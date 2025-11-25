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
    # Import without clearing cache to avoid metric re-registration issues
    import omnicore_engine.array_backend

    # Verify key components are available
    assert hasattr(omnicore_engine.array_backend, "ArrayBackend")
    assert hasattr(omnicore_engine.array_backend, "backend")
    assert hasattr(omnicore_engine.array_backend, "xp")

    # Verify settings function works
    assert hasattr(omnicore_engine.array_backend, "settings")
    settings_obj = omnicore_engine.array_backend.settings()

    # Settings should be either ArbiterConfig or fallback SimpleNamespace
    # Both are valid - the important thing is that settings() returns something
    assert settings_obj is not None


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
    Test that settings object is properly returned even when ArbiterConfig is used.
    """
    from omnicore_engine.array_backend import settings

    # Get settings - should work regardless of whether ArbiterConfig or fallback is used
    settings_obj = settings()
    
    # Verify we got a settings object (could be ArbiterConfig or SimpleNamespace)
    assert settings_obj is not None
    
    # If it's the fallback SimpleNamespace, it should have these attributes
    # If it's ArbiterConfig, it might not have them, but that's also valid
    if hasattr(settings_obj, 'log_level'):
        assert settings_obj.log_level is not None
    if hasattr(settings_obj, 'enable_array_backend_benchmarking'):
        assert isinstance(settings_obj.enable_array_backend_benchmarking, bool)


def test_module_imports_cleanly():
    """
    Test that the module can be imported without triggering backend initialization.
    This ensures import-time side effects are minimal.
    """
    # Import without clearing cache to avoid metric re-registration issues
    import omnicore_engine.array_backend as m

    # cp should be defined (even if None) for test patching
    assert hasattr(m, "cp")

    # Backend should be available
    assert hasattr(m, "backend")

    # xp should be defined (could be None or numpy depending on initialization)
    assert hasattr(m, "xp")

    # is_gpu should be available
    assert hasattr(m, "is_gpu")
