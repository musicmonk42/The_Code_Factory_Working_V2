"""Pytest configuration for generator tests.

This module provides pytest configuration and fixtures for generator tests,
including mock setup for expensive dependencies to avoid timeouts during
test collection and execution.

Mocked Dependencies
-------------------
The following modules are mocked to prevent expensive initialization:

1. Simulation modules (simulation, simulation_module, etc.)
   - Reason: Heavy database connections and message bus initialization
   
2. ChromaDB (chromadb, chromadb.utils.embedding_functions)
   - Reason: Large vector database initialization, file I/O
   - Impact: Prevents 1-2 minute startup delay
   
3. Presidio (presidio_analyzer, presidio_anonymizer)
   - Reason: Downloads SpaCy NLP models (100+ MB) on first use
   - Impact: Prevents network timeouts and CPU exhaustion
   
4. SpaCy (spacy)
   - Reason: Large NLP model loading
   - Impact: Prevents memory pressure and long initialization

Tests that genuinely need these dependencies should use real implementations
with appropriate fixtures, or skip tests with @pytest.mark.skipif decorators.

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
from pathlib import Path
import pytest

# ---- Ensure paths are set up correctly ----
# This is defensive: the root conftest.py should handle path setup,
# but we ensure it here in case pytest is run from the generator/ directory
_generator_dir = Path(__file__).parent.absolute()
_project_root = _generator_dir.parent

# Add project root to sys.path if not already present (highest priority)
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Import path_setup module to ensure all component paths are configured
try:
    import path_setup
except ImportError as e:
    # If path_setup is not available, continue without it
    # The root conftest.py should have already set up paths
    import warnings
    warnings.warn(f"generator/conftest.py: Could not import path_setup module: {e}. Using basic path configuration.")


# List of modules that need to be mocked during test collection
# to avoid expensive initialization (database connections, message bus, ML/NLP models, etc.)
SIMULATION_MODULES_TO_MOCK = [
    "simulation",
    "simulation.simulation_module",
    "simulation.runners",
    "simulation.core",
    "omnicore_engine.engines",
    # Add heavy ML/NLP dependencies that cause timeouts
    "chromadb",
    "chromadb.config",
    "chromadb.utils",
    "chromadb.utils.embedding_functions",
    "presidio_analyzer",
    "presidio_analyzer.analyzer_engine",
    "presidio_anonymizer",
    "presidio_anonymizer.anonymizer_engine",
    # Add SpaCy to prevent model downloads
    "spacy",
]


def _create_mock_module(name: str) -> types.ModuleType:
    """Create a minimal mock module for expensive simulation dependencies.
    
    This creates a mock that:
    - Can be imported normally
    - Returns mock objects for all attribute accesses
    - Can be called as a function
    - Supports nested attribute access (e.g., module.submodule.Class())
    
    Args:
        name: The module name to mock.
        
    Returns:
        A mock module object that behaves like the real module for testing.
    """
    import importlib.util
    
    mock_module = types.ModuleType(name)
    mock_module.__file__ = f"<mocked {name}>"
    mock_module.__path__ = []
    # Add __spec__ for compatibility with Python's import system
    mock_module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
    
    class MockCallable:
        """A mock object that can be called or accessed as an attribute.
        
        This mock supports:
        - Being called as a function/constructor
        - Attribute access (returns another mock)
        - Context managers (for with statements)
        - Iteration (for loops)
        - String representation
        """
        def __init__(self, name="MockCallable"):
            self._mock_name = name
            
        def __call__(self, *args, **kwargs):
            return MockCallable(f"{self._mock_name}()")
            
        def __getattr__(self, attr):
            return MockCallable(f"{self._mock_name}.{attr}")
            
        def __enter__(self):
            return self
            
        def __exit__(self, *args):
            return False
            
        def __iter__(self):
            return iter([])
            
        def __repr__(self):
            return f"<Mock: {self._mock_name}>"
            
        def __str__(self):
            return self._mock_name
    
    # Make the module itself callable and attribute-accessible
    def module_getattr(attr):
        return MockCallable(f"{name}.{attr}")
    
    mock_module.__getattr__ = module_getattr
    return mock_module


def pytest_configure(config):
    """Skip expensive initialization during collection phase."""
    if config.option.collectonly:
        os.environ['SKIP_EXPENSIVE_INIT'] = '1'
        os.environ['PYTEST_COLLECTING_ONLY'] = '1'


@pytest.fixture(scope="session")
def _ensure_mocks():
    """Ensure expensive dependencies are mocked to avoid timeouts.
    
    This fixture mocks heavy modules (simulation, ML/NLP dependencies) that would
    otherwise cause timeouts during test collection. It is NOT autouse to give
    tests explicit control over mock setup.
    
    Set PYTEST_NO_MOCK=1 to disable mocking for debugging.
    
    Usage:
        def test_my_generator_feature(_ensure_mocks):
            # Test code here - expensive modules are mocked
            ...
    """
    # Allow disabling mocks for debugging
    if os.environ.get('PYTEST_NO_MOCK') == '1':
        yield
        return
    
    original_modules = {}
    
    # Save and mock expensive dependencies
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
