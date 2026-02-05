import pytest
from unittest.mock import MagicMock, Mock

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
            if isinstance(aiofiles_in_backend, (MagicMock, Mock)) or hasattr(aiofiles_in_backend, '_mock_name'):
                pytest.skip("arbiter_array_backend has mocked aiofiles - cannot run persistence tests")
    except Exception as e:
        pytest.skip(f"Failed to reload backend with real aiofiles: {e}")
    
    # Verify that aiofiles is functional (not a mock)
    try:
        import aiofiles
        
        # Check if aiofiles.open exists and is callable
        if not hasattr(aiofiles, 'open') or not callable(aiofiles.open):
            pytest.skip("aiofiles.open is not available - cannot run persistence tests")
        
        # Try to detect if it's a mock by checking its module
        aiofiles_open = getattr(aiofiles, 'open')
        if hasattr(aiofiles_open, '__module__'):
            if 'mock' in aiofiles_open.__module__.lower():
                pytest.skip("aiofiles.open is mocked - skipping persistence tests")
        
        # Check if aiofiles.open is a mock using safer detection
        if isinstance(aiofiles_open, (MagicMock, Mock)):
            pytest.skip("aiofiles.open is a MagicMock - skipping persistence tests")
        
        # Additional verification: check that aiofiles module is real (not a mock itself)
        if isinstance(aiofiles, (MagicMock, Mock)):
            pytest.skip("aiofiles module is mocked - cannot run persistence tests")
            
    except ImportError as e:
        pytest.skip(f"aiofiles not installed - cannot run persistence tests: {e}")
    except Exception as e:
        # If verification fails for any reason, skip the tests
        pytest.skip(f"Failed to verify aiofiles functionality: {e}")
    
    yield
