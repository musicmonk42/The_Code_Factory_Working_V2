"""Pytest configuration for generator tests.

This module provides pytest configuration and fixtures for generator tests,
including mock setup for simulation modules to avoid expensive initialization
during test collection.

================================================================================
BREAKING CHANGE NOTICE
================================================================================
The _test_setup fixture has been renamed to _ensure_mocks and is no longer
autouse. Tests that previously relied on automatic mock setup must now
explicitly request the fixture.

Migration Guide:
    Old (deprecated):
        def test_something():
            # mocks were automatically applied
            ...
    
    New (recommended):
        def test_something(_ensure_mocks):
            # explicitly request mock fixture
            ...
    
    Or use the legacy alias:
        def test_something(_test_setup):
            # still works for backward compatibility
            ...
================================================================================
"""
import os
import sys
import types
import pytest


# List of simulation modules that need to be mocked during test collection
# to avoid expensive initialization (database connections, message bus, etc.)
SIMULATION_MODULES_TO_MOCK = [
    "simulation",
    "simulation.simulation_module",
    "simulation.runners",
    "simulation.core",
    "omnicore_engine.engines",
]


def _create_mock_module(name: str) -> types.ModuleType:
    """Create a minimal mock module for expensive simulation dependencies.
    
    Args:
        name: The module name to mock.
        
    Returns:
        A mock module object.
    """
    import importlib.util
    
    mock_module = types.ModuleType(name)
    mock_module.__file__ = f"<mocked {name}>"
    mock_module.__path__ = []
    # Add __spec__ for compatibility with Python's import system
    mock_module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
    
    class MockCallable:
        """A mock object that can be called or accessed as an attribute."""
        def __call__(self, *args, **kwargs):
            return self
        def __getattr__(self, attr):
            return MockCallable()
    
    mock_module.__getattr__ = lambda attr: MockCallable()
    return mock_module


def pytest_configure(config):
    """Skip expensive initialization during collection phase."""
    if config.option.collectonly:
        os.environ['SKIP_EXPENSIVE_INIT'] = '1'
        os.environ['PYTEST_COLLECTING_ONLY'] = '1'


@pytest.fixture(scope="session")
def _ensure_mocks():
    """Ensure simulation modules are mocked to avoid expensive initialization.
    
    This fixture mocks heavy simulation modules that would otherwise cause
    timeouts during test collection. It is NOT autouse to give tests explicit
    control over mock setup.
    
    Usage:
        def test_my_generator_feature(_ensure_mocks):
            # Test code here - simulation modules are mocked
            ...
    """
    original_modules = {}
    
    # Save and mock simulation modules
    for module_name in SIMULATION_MODULES_TO_MOCK:
        if module_name in sys.modules:
            original_modules[module_name] = sys.modules[module_name]
        sys.modules[module_name] = _create_mock_module(module_name)
    
    yield
    
    # Restore original modules
    for module_name, original in original_modules.items():
        sys.modules[module_name] = original
    
    # Remove mocked modules that weren't originally present
    for module_name in SIMULATION_MODULES_TO_MOCK:
        if module_name not in original_modules and module_name in sys.modules:
            del sys.modules[module_name]


# Legacy alias for backward compatibility
_test_setup = _ensure_mocks
