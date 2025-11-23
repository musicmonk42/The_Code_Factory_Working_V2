"""
Test to ensure omnicore_engine.array_backend handles all types of exceptions
during arbiter.config import, including NameError, AttributeError, etc.

This test validates that the array_backend module can safely import even when
the arbiter.config module has runtime errors during its initialization.
"""

import sys
import importlib
import pytest
from unittest import mock


def test_import_with_nameerror_in_arbiter_config():
    """
    Test that array_backend can be imported even when arbiter.config raises NameError.
    
    This simulates the scenario where arbiter.config has a NameError due to
    undefined variable (like config_instance) at module level.
    """
    # Clear the module from cache if it exists
    modules_to_clear = [
        "omnicore_engine.array_backend",
        "arbiter.config",
        "arbiter",
    ]
    for mod in modules_to_clear:
        if mod in sys.modules:
            del sys.modules[mod]
    
    # Get the original import function correctly
    import builtins
    original_import = builtins.__import__
    
    # Mock the arbiter.config import to raise NameError
    def mock_import(name, *args, **kwargs):
        if name == "arbiter.config":
            raise NameError("name 'config_instance' is not defined")
        return original_import(name, *args, **kwargs)
    
    with mock.patch("builtins.__import__", side_effect=mock_import):
        # This should not raise any errors - it should catch the NameError
        # and fall back to minimal settings
        import omnicore_engine.array_backend
        
        # Verify key components are still available
        assert hasattr(omnicore_engine.array_backend, "ArrayBackend")
        assert hasattr(omnicore_engine.array_backend, "settings")
        
        # Verify settings fallback works
        settings = omnicore_engine.array_backend.settings
        assert hasattr(settings, "log_level")
        assert settings.log_level == "INFO"


def test_import_with_attributeerror_in_arbiter_config():
    """
    Test that array_backend can be imported even when arbiter.config raises AttributeError.
    """
    # Clear the module from cache
    modules_to_clear = [
        "omnicore_engine.array_backend",
        "arbiter.config",
        "arbiter",
    ]
    for mod in modules_to_clear:
        if mod in sys.modules:
            del sys.modules[mod]
    
    # Get the original import function correctly
    import builtins
    original_import = builtins.__import__
    
    # Mock the arbiter.config import to raise AttributeError
    def mock_import(name, *args, **kwargs):
        if name == "arbiter.config":
            raise AttributeError("module 'arbiter' has no attribute 'config'")
        return original_import(name, *args, **kwargs)
    
    with mock.patch("builtins.__import__", side_effect=mock_import):
        # This should not raise any errors
        import omnicore_engine.array_backend
        
        # Verify fallback settings work
        settings = omnicore_engine.array_backend.settings
        assert hasattr(settings, "log_level")
        assert hasattr(settings, "enable_array_backend_benchmarking")


def test_import_with_runtimeerror_during_instantiation():
    """
    Test that array_backend can be imported even when ArbiterConfig() raises RuntimeError.
    
    This simulates the scenario where ArbiterConfig can be imported but its
    constructor raises an error.
    """
    # Clear the module from cache
    modules_to_clear = [
        "omnicore_engine.array_backend",
        "arbiter.config",
        "arbiter",
    ]
    for mod in modules_to_clear:
        if mod in sys.modules:
            del sys.modules[mod]
    
    # Create a mock ArbiterConfig class that raises on instantiation
    class MockArbiterConfig:
        def __init__(self):
            raise RuntimeError("Configuration initialization failed")
    
    # Get the original import function correctly
    import builtins
    original_import = builtins.__import__
    
    # Mock the import to return our mock class
    def mock_import(name, *args, **kwargs):
        if name == "arbiter.config":
            # Create a mock module
            import types
            mock_module = types.ModuleType("arbiter.config")
            mock_module.ArbiterConfig = MockArbiterConfig
            return mock_module
        return original_import(name, *args, **kwargs)
    
    with mock.patch("builtins.__import__", side_effect=mock_import):
        # This should not raise any errors - should catch RuntimeError during instantiation
        import omnicore_engine.array_backend
        
        # Verify fallback settings work
        settings = omnicore_engine.array_backend.settings
        assert hasattr(settings, "log_level")
        assert settings.log_level == "INFO"
        assert hasattr(settings, "enable_array_backend_benchmarking")
        assert settings.enable_array_backend_benchmarking is False


def test_settings_fallback_attributes():
    """
    Test that the fallback settings object has all required attributes.
    """
    from omnicore_engine.array_backend import _create_fallback_settings
    
    settings = _create_fallback_settings()
    
    # Verify required attributes
    assert hasattr(settings, "log_level")
    assert hasattr(settings, "enable_array_backend_benchmarking")
    
    # Verify default values
    assert settings.log_level == "INFO"
    assert settings.enable_array_backend_benchmarking is False
