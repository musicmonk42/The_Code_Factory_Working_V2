"""Test configuration for omnicore_engine tests."""

import os
import sys
from pathlib import Path

# ---- CRITICAL: Ensure project root is in sys.path FIRST ----
# This MUST be done before any imports to avoid ModuleNotFoundError during pytest collection
# Calculate project root: omnicore_engine/tests/conftest.py -> project root is 2 levels up
_tests_dir = Path(__file__).parent.absolute()
_omnicore_dir = _tests_dir.parent
_project_root = _omnicore_dir.parent

# Add project root to sys.path if not already present (highest priority)
# This ensures that "import omnicore_engine" and "import path_setup" work correctly
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Verify omnicore_engine package can be discovered
# This helps pytest resolve "omnicore_engine.tests" module path during collection
# Use find_spec instead of actual import to avoid expensive import-time initialization
import importlib.util
spec = importlib.util.find_spec('omnicore_engine')
if spec is None:
    import warnings
    error_msg = (
        f"\n{'='*80}\n"
        f"CRITICAL: omnicore_engine package not found!\n"
        f"{'='*80}\n"
        f"The omnicore_engine package must be installed for pytest to collect tests.\n\n"
        f"SOLUTION:\n"
        f"  Install the unified Code Factory platform from project root:\n"
        f"     pip install -e .\n\n"
        f"  This installs all packages (generator, omnicore_engine, self_fixing_engineer, server)\n"
        f"  in a unified manner, ensuring proper package resolution for pytest.\n\n"
        f"Project root: {_project_root}\n"
        f"Current sys.path:\n{chr(10).join(f'  - {p}' for p in sys.path[:5])}\n"
        f"{'='*80}\n"
    )
    warnings.warn(error_msg, ImportWarning, stacklevel=2)
    # Also print to stderr for visibility
    print(error_msg, file=sys.stderr)

# Verify that omnicore_engine.tests can be discovered
# This is critical for pytest collection to work properly
spec_tests = importlib.util.find_spec('omnicore_engine.tests')
if spec_tests is None:
    import warnings
    error_msg = (
        f"\n{'='*80}\n"
        f"CRITICAL: omnicore_engine.tests module cannot be imported!\n"
        f"{'='*80}\n"
        f"This usually means the package was installed from the wrong location.\n\n"
        f"SOLUTION:\n"
        f"  Install the unified platform from project root:\n"
        f"     pip install -e .\n\n"
        f"  NOT from subdirectories like:\n"
        f"     pip install -e ./omnicore_engine  # WRONG - causes this error\n\n"
        f"Project root: {_project_root}\n"
        f"Tests directory: {_tests_dir}\n"
        f"{'='*80}\n"
    )
    warnings.warn(error_msg, ImportWarning, stacklevel=2)
    print(error_msg, file=sys.stderr)

# Now we can safely import pytest and other modules
import pytest

# Import path_setup module to ensure all component paths are configured
try:
    import path_setup
except ImportError as e:
    # If path_setup is not available, continue without it
    # The root conftest.py should have already set up paths
    import warnings
    warnings.warn(
        f"omnicore_engine/tests/conftest.py: Could not import path_setup module: {e}. Using basic path configuration.",
        ImportWarning,
        stacklevel=2
    )

# FIX: Lazy import prometheus_client to avoid collection-time failures
# This prevents AttributeError: __spec__ when the root conftest mocks prometheus_client
def _get_prometheus_registry():
    """Lazy getter for Prometheus registry."""
    try:
        from prometheus_client import REGISTRY
        return REGISTRY
    except (ImportError, AttributeError):
        # Return a mock registry if prometheus is not available
        class MockRegistry:
            def __init__(self):
                self._collector_to_names = {}
            def unregister(self, collector):
                pass
        return MockRegistry()


def pytest_configure(config):
    """Skip expensive initialization during collection phase."""
    if config.option.collectonly:
        os.environ['SKIP_EXPENSIVE_INIT'] = '1'
        os.environ['PYTEST_COLLECTING_ONLY'] = '1'


@pytest.fixture(autouse=True, scope="session")
def reset_prometheus_collectors():
    """Reset Prometheus collectors once per test session to reduce memory overhead.
    
    Note: Uses private API `_collector_to_names` as the public API doesn't
    provide a way to iterate collectors for cleanup. This is wrapped in
    defensive try-except blocks to handle potential API changes gracefully.
    """
    REGISTRY = _get_prometheus_registry()
    
    # Only clean up at session start
    try:
        collectors = list(REGISTRY._collector_to_names.keys())
    except (AttributeError, KeyError):
        collectors = []
    
    # Unregister all collectors at session start
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
    
    yield
    
    # Skip cleanup after tests - let process exit handle it
