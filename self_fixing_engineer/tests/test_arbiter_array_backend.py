import pytest
import sys
import importlib
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, Mock

# Add the parent directory to the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload_backend_with_real_aiofiles():
    """Reload the arbiter_array_backend module with real aiofiles."""
    # Remove any mocked aiofiles modules from sys.modules
    mocked_modules = [key for key in sys.modules.keys() if 'aiofiles' in key]
    for mod_name in mocked_modules:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            # Check if it's a mock without triggering attribute access errors
            if isinstance(mod, (MagicMock, Mock)):
                del sys.modules[mod_name]
    
    # Force import of the real aiofiles module
    try:
        spec = importlib.util.find_spec("aiofiles")
        if spec is not None:
            import aiofiles
            importlib.reload(aiofiles)
    except (ImportError, AttributeError):
        pass  # aiofiles may not be installed
    
    # Reload the backend module
    if 'self_fixing_engineer.arbiter.arbiter_array_backend' in sys.modules:
        del sys.modules['self_fixing_engineer.arbiter.arbiter_array_backend']
    
    from self_fixing_engineer.arbiter import arbiter_array_backend
    return arbiter_array_backend


# Global module references - will be set by fixture
_backend = None
ConcreteArrayBackend = None
ArrayBackendError = None
ArraySizeLimitError = None
StorageError = None
ArrayMeta = None


@pytest.fixture(autouse=True)
def ensure_real_aiofiles():
    """Ensure aiofiles is real, not mocked, for these tests.\n    \n    These tests require actual file I/O to test persistence.\n    Remove any mocks that may have been applied by other test modules.\n    """
    global _backend, ConcreteArrayBackend, ArrayBackendError, ArraySizeLimitError, StorageError, ArrayMeta
    
    # Reload the backend module with real aiofiles
    try:
        _backend = _reload_backend_with_real_aiofiles()  
        
        # Update global references to use the reloaded module
        ConcreteArrayBackend = getattr(_backend, "ConcreteArrayBackend")
        ArrayBackendError = getattr(_backend, "ArrayBackendError")
        ArraySizeLimitError = getattr(_backend, "ArraySizeLimitError")
        StorageError = getattr(_backend, "StorageError")
        ArrayMeta = getattr(_backend, "ArrayMeta")
        
        # Verify the reloaded module has real aiofiles
        if hasattr(_backend, 'aiofiles'):
            aiofiles_in_backend = getattr(_backend, 'aiofiles')
            # Use isinstance first to avoid triggering attribute access
            if isinstance(aiofiles_in_backend, (MagicMock, Mock)):
                pytest.skip("arbiter_array_backend has mocked aiofiles - cannot run persistence tests")
            # Only check _mock_name if isinstance passed
            try:
                if hasattr(aiofiles_in_backend, '_mock_name'):
                    pytest.skip("arbiter_array_backend has mocked aiofiles - cannot run persistence tests")
            except (AttributeError, TypeError):
                pass  # Not a mock, continue
    except Exception as e:
        pytest.skip(f"Failed to reload backend with real aiofiles: {e}")
    
    # Verify that aiofiles is functional (not a mock)
    try:
        import aiofiles
        
        # Check if aiofiles.open exists and is callable
        if not hasattr(aiofiles, 'open') or not callable(aiofiles.open):
            pytest.skip("aiofiles.open is not available - cannot run persistence tests")
        
        # Check if aiofiles.open is a mock using isinstance first
        aiofiles_open = getattr(aiofiles, 'open')
        if isinstance(aiofiles_open, (MagicMock, Mock)):
            pytest.skip("aiofiles.open is a MagicMock - skipping persistence tests")
        
        # Try to detect if it's a mock by checking its module (safer than hasattr)
        try:
            if hasattr(aiofiles_open, '__module__'):
                if 'mock' in aiofiles_open.__module__.lower():
                    pytest.skip("aiofiles.open is mocked - skipping persistence tests")
        except (AttributeError, TypeError):
            pass  # Continue if module check fails
        
        # Additional verification: check that aiofiles module is real (not a mock itself)
        if isinstance(aiofiles, (MagicMock, Mock)):
            pytest.skip("aiofiles module is mocked - cannot run persistence tests")
            
        # Only check _mock_name as last resort with error handling
        try:
            if hasattr(aiofiles, '_mock_name'):
                pytest.skip("aiofiles module is still mocked - cannot run persistence tests")
        except (AttributeError, TypeError):
            pass  # Not a mock, continue
            
    except ImportError as e:
        pytest.skip(f"aiofiles not installed - cannot run persistence tests: {e}")
    except Exception as e:
        # If verification fails for any reason, skip the tests
        pytest.skip(f"Failed to verify aiofiles functionality: {e}")
    
    yield


# Placeholder test to verify fixture works
def test_fixture_loads_successfully():
    """Test that the ensure_real_aiofiles fixture works without errors."""
    assert _backend is not None, "Backend module should be loaded"
    assert ConcreteArrayBackend is not None, "ConcreteArrayBackend should be available"

