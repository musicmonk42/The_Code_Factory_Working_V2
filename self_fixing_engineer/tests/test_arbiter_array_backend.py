# Force pytest to not mock aiofiles for this test module
pytest_plugins = []

@pytest.fixture(scope="session", autouse=True)
def unmock_aiofiles_globally():
    """Ensure aiofiles is never mocked in this test session."""
    import sys
    from unittest.mock import MagicMock, Mock
    
    # Remove any existing mocks from sys.modules
    for key in list(sys.modules.keys()):
        if 'aiofiles' in key:
            mod = sys.modules.get(key)
            if mod and (isinstance(mod, (MagicMock, Mock)) or hasattr(mod, '_mock_name')):
                del sys.modules[key]
    
    # Import real aiofiles
    import aiofiles
    import aiofiles.os
    
    # Verify it's real
    assert not isinstance(aiofiles, (MagicMock, Mock))
    assert not isinstance(aiofiles.open, (MagicMock, Mock))
    
    yield
    
# Also add pytest.ini marker for isolation
@pytest.fixture(autouse=True)
def isolate_aiofiles_in_subprocess(request):
    """Mark tests to run in isolation if needed."""
    pass
