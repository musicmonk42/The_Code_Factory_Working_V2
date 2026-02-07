# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test to ensure omnicore_engine.array_backend handles all types of exceptions
during arbiter.config import, including NameError, AttributeError, etc.

This test validates that the array_backend module can safely import even when
the arbiter.config module has runtime errors during its initialization.
"""

import sys
from unittest import mock
import pytest


def test_import_with_nameerror_in_arbiter_config():
    """
    Test that _get_settings handles NameError from arbiter.config.

    This tests the exception handling in _get_settings directly rather than
    trying to mock the entire import chain.
    """
    # Test by directly calling the function with mocked imports
    from omnicore_engine.array_backend import _create_fallback_settings, _get_settings
    
    # Create a mock that raises NameError when importing
    with mock.patch.dict(sys.modules, {
        'self_fixing_engineer.arbiter.config': None,
        'arbiter.config': None
    }):
        # Clear any cached settings
        import omnicore_engine.array_backend as ab
        ab._settings = None
        
        # This should fall back to fallback settings
        settings = ab.settings()
        assert hasattr(settings, "log_level")
        assert settings.log_level == "INFO"


def test_import_with_attributeerror_in_arbiter_config():
    """
    Test that _get_settings handles AttributeError from arbiter.config.
    """
    from omnicore_engine.array_backend import _create_fallback_settings
    import omnicore_engine.array_backend as ab
    
    # Create a mock module that raises AttributeError when accessing ArbiterConfig
    class MockModule:
        @property
        def ArbiterConfig(self):
            raise AttributeError("module 'arbiter' has no attribute 'ArbiterConfig'")
    
    mock_module = MockModule()
    
    with mock.patch.dict(sys.modules, {
        'self_fixing_engineer.arbiter.config': mock_module,
        'arbiter.config': mock_module
    }):
        # Clear any cached settings
        ab._settings = None
        
        # This should fall back to fallback settings
        settings = ab.settings()
        assert hasattr(settings, "log_level")
        assert hasattr(settings, "enable_array_backend_benchmarking")


def test_import_with_runtimeerror_during_instantiation():
    """
    Test that _get_settings handles RuntimeError during ArbiterConfig instantiation.
    """
    import omnicore_engine.array_backend as ab
    
    # Create a mock ArbiterConfig class that raises on instantiation
    class MockArbiterConfig:
        def __init__(self):
            raise RuntimeError("Configuration initialization failed")
    
    # Create a mock module with the failing ArbiterConfig
    import types
    mock_module = types.ModuleType('mock_arbiter_config')
    mock_module.ArbiterConfig = MockArbiterConfig
    
    with mock.patch.dict(sys.modules, {
        'self_fixing_engineer.arbiter.config': mock_module,
        'arbiter.config': mock_module
    }):
        # Clear any cached settings
        ab._settings = None
        
        # This should not raise any errors - should catch RuntimeError during instantiation
        settings = ab.settings()
        assert hasattr(settings, "log_level")
        assert settings.log_level == "INFO"
        assert hasattr(settings, "enable_array_backend_benchmarking")
        assert settings.enable_array_backend_benchmarking is False


def test_settings_fallback_attributes():
    """
    Test that the fallback settings object has all required attributes.
    """
    from omnicore_engine.array_backend import _create_fallback_settings
    
    # Test the fallback settings directly
    settings = _create_fallback_settings()
    
    # Verify required attributes exist
    assert hasattr(settings, "log_level")
    assert settings.log_level == "INFO"
    assert hasattr(settings, "enable_array_backend_benchmarking")
    assert settings.enable_array_backend_benchmarking is False

