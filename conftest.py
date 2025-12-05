import os
import sys

# Add the project root to Python path
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

# Add the self_fixing_engineer directory so arbiter can be imported
sys.path.insert(0, os.path.join(project_root, "self_fixing_engineer"))

# Add omnicore_engine directory
sys.path.insert(0, os.path.join(project_root, "omnicore_engine"))

# Add generator directory
sys.path.insert(0, os.path.join(project_root, "generator"))

# ---- Set TESTING environment variable early ----
# This should be set before any module imports to prevent side effects
os.environ["TESTING"] = "1"
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("PYTEST_CURRENT_TEST", "true")

# ---- Import error handling ----
# Provide graceful fallbacks for common missing dependencies during test collection
# This allows pytest to collect tests even when optional dependencies are missing

def _create_mock_module(name):
    """Create a minimal mock module for missing dependencies."""
    import types
    mock_module = types.ModuleType(name)
    mock_module.__file__ = f"<mocked {name}>"
    return mock_module

# Only mock if genuinely missing (not if already imported)
_OPTIONAL_DEPENDENCIES = [
    'tiktoken',  # Often missing, used by LLM clients
    'aiofiles',  # Required by generator.main.api
    'backoff',  # Required by generator.main.api
    'fastapi',  # Required by generator.main.api
    'fastapi.security',  # Required by generator.main.api
    'uvicorn',  # Required by generator.main
    'jwt',  # Required by generator.main.api
    'sqlalchemy',  # Required by many modules
    'redis',  # Required by various modules
    'redis.asyncio',  # Required by generator.main.api
    'dotenv',  # Required by many modules
    'prometheus_client',  # Required by many modules
    'aiohttp',  # Required by many modules
    'opentelemetry',  # Required by many modules
    'opentelemetry.trace',  # Required by many modules
    'opentelemetry.sdk',  # Required by many modules
    'opentelemetry.sdk.trace',  # Required by many modules
    'opentelemetry.sdk.trace.export',  # Required by many modules
    'tenacity',  # Required by many modules
]

for dep in _OPTIONAL_DEPENDENCIES:
    if dep not in sys.modules:
        try:
            __import__(dep)
        except ImportError:
            # Create a more sophisticated mock that handles submodule access
            mock_module = _create_mock_module(dep)
            sys.modules[dep] = mock_module
            
            # For packages that are commonly accessed as submodules, create parent stubs
            if '.' in dep:
                parts = dep.split('.')
                for i in range(1, len(parts)):
                    parent_name = '.'.join(parts[:i])
                    if parent_name not in sys.modules:
                        sys.modules[parent_name] = _create_mock_module(parent_name)

# ---- Pydantic decorator safety shim ----
# Prevents test collection-time errors when pydantic decorators are replaced with non-callables
try:
    import pydantic

    # No-op decorator that preserves function/class behavior used by Pydantic decorators
    def _noop_validator(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    # Helper function to safely set pydantic decorators
    def _set_pydantic_decorator_safely(decorator_name):
        """Set a pydantic decorator to no-op if it's not callable."""
        try:
            if not callable(getattr(pydantic, decorator_name, None)):
                setattr(pydantic, decorator_name, _noop_validator)
        except (AttributeError, TypeError):
            # Attribute doesn't exist or has unexpected type
            setattr(pydantic, decorator_name, _noop_validator)  # best-effort

    # Apply to commonly mocked decorators
    _set_pydantic_decorator_safely("field_validator")
    _set_pydantic_decorator_safely("model_validator")
    # If your tests also mock other pydantic decorators, add them here:
    # _set_pydantic_decorator_safely("field_serializer")
    # _set_pydantic_decorator_safely("validator")

except ImportError:
    # pydantic not installed, skip shim
    pass

# ---- Tenacity exception safety ----
# Ensure tenacity exceptions are proper Exception classes
try:
    from tenacity import RetryError, TryAgain
    # Verify these are actual exception classes
    if not issubclass(RetryError, BaseException):
        # If somehow mocked, restore proper exception behavior
        class RetryError(Exception):
            """Raised when all retry attempts have failed."""
            pass
        import tenacity
        tenacity.RetryError = RetryError
    if not issubclass(TryAgain, BaseException):
        class TryAgain(Exception):
            """Signal to retry the operation."""
            pass
        import tenacity
        tenacity.TryAgain = TryAgain
except ImportError:
    # tenacity not installed, skip
    pass
except TypeError:
    # If issubclass check fails, create proper exceptions
    try:
        import tenacity
        class RetryError(Exception):
            """Raised when all retry attempts have failed."""
            pass
        class TryAgain(Exception):
            """Signal to retry the operation."""
            pass
        tenacity.RetryError = RetryError
        tenacity.TryAgain = TryAgain
    except:
        pass


# ---- Protect aiohttp types from being mocked ----
# Ensure aiohttp types remain as proper classes for type annotations
try:
    import aiohttp
    # Store original types before any mocking can happen
    _ORIGINAL_AIOHTTP_TYPES = {
        'ClientResponse': getattr(aiohttp, 'ClientResponse', None),
        'ClientSession': getattr(aiohttp, 'ClientSession', None),
    }
except ImportError:
    _ORIGINAL_AIOHTTP_TYPES = {}


# ---- Protect common exception types from being mocked ----
# Store references to common exception types before they can be mocked
try:
    import cryptography.fernet
    _ORIGINAL_CRYPTO_EXCEPTIONS = {
        'InvalidToken': getattr(cryptography.fernet, 'InvalidToken', Exception),
    }
except ImportError:
    _ORIGINAL_CRYPTO_EXCEPTIONS = {}


# ---- ChromaDB singleton cleanup ----
# Global cleanup of ChromaDB singleton between test sessions
def _cleanup_chromadb_singleton():
    """
    Clean up ChromaDB singleton instances to prevent
    'An instance of Chroma already exists' errors.
    """
    try:
        import chromadb
        # Try multiple ways to access the singleton registry
        # ChromaDB's internal API varies by version
        if hasattr(chromadb, 'api'):
            if hasattr(chromadb.api, 'client'):
                if hasattr(chromadb.api.client, 'SharedSystemClient'):
                    client_class = chromadb.api.client.SharedSystemClient
                    if hasattr(client_class, '_identifier_to_system'):
                        client_class._identifier_to_system.clear()
            
        # Alternative path for different ChromaDB versions
        try:
            from chromadb.api.shared_system_client import SharedSystemClient
            if hasattr(SharedSystemClient, '_identifier_to_system'):
                SharedSystemClient._identifier_to_system.clear()
        except (ImportError, AttributeError):
            pass
            
    except (ImportError, AttributeError):
        # ChromaDB not installed or API changed, skip cleanup
        pass


# ---- SQLAlchemy metadata cleanup ----
def _cleanup_sqlalchemy_metadata():
    """
    Clean up SQLAlchemy metadata to prevent table redefinition errors.
    """
    try:
        # Clear metadata for arbiter.agent_state Base
        from arbiter.agent_state import Base as ArbiterBase
        ArbiterBase.metadata.clear()
    except (ImportError, AttributeError):
        pass


# ---- pytest_plugins configuration ----
# Move from nested conftest files to top-level to avoid pytest deprecation warning
pytest_plugins = ["pytest_asyncio"]

# ---- Global pytest fixtures ----
import pytest


@pytest.fixture(scope="function", autouse=True)
def cleanup_chromadb():
    """
    Clean up ChromaDB singleton instances between tests to prevent
    'An instance of Chroma already exists' errors.
    """
    yield
    _cleanup_chromadb_singleton()


@pytest.fixture(scope="session", autouse=True)
def cleanup_chromadb_session():
    """Clean up ChromaDB at session start and end."""
    _cleanup_chromadb_singleton()
    yield
    _cleanup_chromadb_singleton()


@pytest.fixture(scope="function", autouse=True)
def cleanup_sqlalchemy():
    """
    Clean up SQLAlchemy metadata between tests to prevent
    'Table already defined' errors.
    """
    yield
    # Cleanup after test - don't cleanup before as it breaks table definitions
    # The metadata.clear() is intentionally not called to avoid breaking
    # tests that rely on table definitions persisting within a session


@pytest.fixture(scope="function", autouse=True)
def protect_sys_modules():
    """
    Protect sys.modules from being permanently modified by test-level mocks.
    Saves a snapshot before the test and restores critical modules after.
    """
    # Save references to critical modules that should not be mocked permanently
    critical_modules = [
        'runner', 'runner.runner_core', 'runner.runner_config', 
        'runner.runner_logging', 'runner.runner_metrics', 'runner.runner_utils',
        'generator.runner', 'intent_parser', 'intent_parser.intent_parser',
        'tenacity', 'aiohttp', 'pydantic'
    ]
    saved_modules = {}
    for mod_name in critical_modules:
        if mod_name in sys.modules:
            saved_modules[mod_name] = sys.modules[mod_name]
    
    yield
    
    # Restore critical modules if they were replaced with mocks
    for mod_name, original_module in saved_modules.items():
        if mod_name in sys.modules:
            current_module = sys.modules[mod_name]
            # Check if it was replaced with a Mock
            if hasattr(current_module, '_mock_name') or str(type(current_module).__name__) == 'MagicMock':
                # Restore the original
                sys.modules[mod_name] = original_module
