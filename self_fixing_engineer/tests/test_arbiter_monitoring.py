import sys
from unittest.mock import patch

def ensure_real_aiofiles():
    # Remove all aiofiles mocks from sys.modules
    if 'aiofiles' in sys.modules:
        del sys.modules['aiofiles']
    # Patch aiofiles with the real module
    with patch('aiofiles'):
        yield
