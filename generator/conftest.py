# generator/conftest.py
"""
Root conftest.py for generator tests.
Adds the generator directory to sys.path to allow imports like 'from main.api import ...'
Sets up mocks for Windows DLL issues and missing dependencies.

IMPORTANT: This file has been refactored to avoid CPU timeout issues in CI.
- Removed redundant OpenTelemetry setup (duplicated from root conftest)
- Removed unused LazyModuleAliasFinder and import_timeout utilities  
- Optimized mock setup to avoid expensive __import__() attempts
- Mocks are created immediately at module-level without import attempts
"""

import sys
import os
from pathlib import Path
from types import ModuleType
import importlib.util

# Set testing environment variables EARLY
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("PYTEST_CURRENT_TEST", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# Add the generator directory to sys.path
generator_root = Path(__file__).parent.resolve()
generator_root_str = str(generator_root)

# Insert at the beginning only if not already there
if not sys.path or sys.path[0] != generator_root_str:
    if generator_root_str in sys.path:
        sys.path.remove(generator_root_str)
    sys.path.insert(0, generator_root_str)


# ---- Lightweight mock setup for optional dependencies ----
# Create mocks immediately WITHOUT expensive __import__() attempts.
# This avoids CPU timeout while still allowing test files to import dependencies.

def _create_mock_module(name):
    """Create a minimal mock module for missing dependencies."""
    
    # Create a mock class that can be used as decorator or callable
    class MockCallable:
        """
        A versatile mock object that supports multiple usage patterns:
        - As a decorator: @mock.method(args)
        - As a callable: mock.function()
        - As an attribute chain: mock.sub.module.attr
        - As a context manager: with mock.context():
        - As an iterable: for item in mock
        - As __mro_entries__ for class inheritance
        """

        def __call__(self, *args, **kwargs):
            # When called directly, return self to support chaining
            return self

        def __getattr__(self, attr):
            # Return another MockCallable for attribute access
            return MockCallable()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __iter__(self):
            # Return an empty iterator to support "for x in mock" patterns
            return iter(())

        def __mro_entries__(self, bases):
            # Return empty tuple when used as a base class in type() or types.new_class()
            # The 'bases' parameter is the tuple of base classes passed to the class creation
            # This fixes: "__mro_entries__ must return a tuple" errors
            return ()

    mock_module = ModuleType(name)
    mock_module.__file__ = f"<mocked {name}>"
    # Add __path__ attribute to support submodule imports (packages need this)
    mock_module.__path__ = []
    # Add __spec__ attribute to satisfy importlib.util.find_spec checks
    # This prevents "ValueError: torch.__spec__ is None" errors
    mock_module.__spec__ = importlib.util.spec_from_loader(name, loader=None)

    # Add a __getattr__ to handle submodule/attribute access gracefully
    def _mock_getattr(attr_name):
        """Return a mock object for any attribute access."""
        # Return a MockCallable that can be used as decorator or function
        return MockCallable()

    mock_module.__getattr__ = _mock_getattr

    # Add common attributes for specific modules
    if name == "dotenv":
        # dotenv needs load_dotenv and find_dotenv functions
        mock_module.load_dotenv = lambda *args, **kwargs: None
        mock_module.find_dotenv = lambda *args, **kwargs: None
    elif name == "dynaconf":
        # dynaconf needs Dynaconf class and Validator
        class MockValidators:
            def __init__(self):
                self.validators = []

            def validate(self):
                pass  # No-op in test mode

        class MockDynaconf:
            def __init__(self, *args, **kwargs):
                self._data = {}
                self.validators = MockValidators()

            def get(self, key, default=None):
                return self._data.get(key, default)

            def set(self, key, value):
                self._data[key] = value

            def __getattr__(self, name):
                return self._data.get(name, None)

            def __setattr__(self, name, value):
                if name.startswith("_"):
                    object.__setattr__(self, name, value)
                else:
                    self._data[name] = value

        class MockValidator:
            def __init__(self, *args, **kwargs):
                pass

        mock_module.Dynaconf = MockDynaconf
        mock_module.Validator = MockValidator
    elif name == "torch":
        # torch needs __version__ as a string (not MockCallable) to prevent errors
        mock_module.__version__ = "2.0.0+cpu"
        mock_module.cuda = MockCallable()
        mock_module.cuda.is_available = lambda: False
        mock_module.nn = MockCallable()
        mock_module.optim = MockCallable()
        # Add torch.Tensor to prevent AttributeError
        mock_module.Tensor = MockCallable
    elif name == "transformers":
        # transformers needs specific classes
        mock_module.AutoTokenizer = MockCallable()
        mock_module.AutoModel = MockCallable()
        mock_module.pipeline = MockCallable()
    elif name == "sentence_transformers":
        # sentence_transformers needs SentenceTransformer
        mock_module.SentenceTransformer = MockCallable
    elif name == "redis":
        # redis needs Redis class with specific methods
        class MockRedis:
            def __init__(self, *args, **kwargs):
                self._data = {}

            def get(self, key):
                return self._data.get(key)

            def set(self, key, value, *args, **kwargs):
                self._data[key] = value
                return True

            def delete(self, *keys):
                for key in keys:
                    self._data.pop(key, None)
                return len(keys)

            def ping(self):
                return True

            def close(self):
                pass

        mock_module.Redis = MockRedis
        mock_module.StrictRedis = MockRedis
    elif name == "sqlalchemy":
        # SQLAlchemy needs specific classes
        mock_module.create_engine = lambda *args, **kwargs: MockCallable()
        mock_module.Column = MockCallable
        mock_module.Integer = MockCallable()
        mock_module.String = MockCallable()
        mock_module.Text = MockCallable()
        mock_module.DateTime = MockCallable()
        mock_module.Boolean = MockCallable()
        mock_module.ForeignKey = MockCallable()
    elif name == "pydantic":
        # Pydantic needs BaseModel
        class MockBaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

            def dict(self):
                return {}

            def json(self):
                return "{}"

            @classmethod
            def parse_obj(cls, obj):
                return cls(**obj)

        mock_module.BaseModel = MockBaseModel
        mock_module.Field = lambda *args, **kwargs: None
        mock_module.validator = lambda *args, **kwargs: lambda f: f
    elif name == "fastapi":
        # FastAPI needs specific classes
        mock_module.FastAPI = MockCallable
        mock_module.APIRouter = MockCallable
        mock_module.HTTPException = Exception
        mock_module.Depends = lambda *args, **kwargs: None
        mock_module.Request = MockCallable
        mock_module.Response = MockCallable
    elif name == "httpx":
        # httpx needs Client and AsyncClient
        mock_module.Client = MockCallable
        mock_module.AsyncClient = MockCallable
        mock_module.get = MockCallable()
        mock_module.post = MockCallable()
    elif name == "prometheus_client":
        # Prometheus needs specific classes
        mock_module.Counter = MockCallable
        mock_module.Gauge = MockCallable
        mock_module.Histogram = MockCallable
        mock_module.Summary = MockCallable
        mock_module.Info = MockCallable
        mock_module.Enum = MockCallable
    elif name == "pytest_asyncio":
        # pytest_asyncio needs fixture decorator
        mock_module.fixture = lambda *args, **kwargs: lambda f: f

    return mock_module


# List of optional dependencies to mock
_OPTIONAL_DEPENDENCIES = [
    "torch", "sentence_transformers", "transformers", "spacy",
    "presidio_analyzer", "presidio_anonymizer", "networkx", "tiktoken",
    "defusedxml", "openai", "chromadb", "anthropic", "dotenv", "backoff",
    "hypothesis", "psutil", "xattr", "hvac", "pkcs11", "python-pkcs11",
    "faiss", "dynaconf", "watchdog", "aiofiles", "aiohttp",
    "prometheus_client", "aiokafka", "kafka", "redis", "sqlalchemy",
    "pydantic", "pydantic_core", "pydantic-settings", "pydantic_settings",
    "pytest_asyncio", "pytest-asyncio", "grpc", "grpcio", "fastapi",
    "uvicorn", "faker", "httpx", "tenacity", "freezegun", "typer",
    "numpy", "docutils", "nltk", "beautifulsoup4", "bs4", "git",
    "gitpython", "filelock", "sphinx", "lxml", "langchain", "aiosqlite",
    "google.cloud.storage", "google.cloud", "google.protobuf",
    "azure.storage.blob", "azure.storage.blob.aio", "azure.core.exceptions",
    "boto3", "botocore.exceptions",
]

# Set up mocks WITHOUT expensive __import__() attempts
# Only create mocks for dependencies that aren't already in sys.modules
for dep in _OPTIONAL_DEPENDENCIES:
    if dep not in sys.modules:
        # Create mock immediately without trying to import
        mock_module = _create_mock_module(dep)
        sys.modules[dep] = mock_module

        # For packages that are commonly accessed as submodules, create parent stubs
        if "." in dep:
            parts = dep.split(".")
            for i in range(1, len(parts)):
                parent_name = ".".join(parts[:i])
                if parent_name not in sys.modules:
                    parent_mock = _create_mock_module(parent_name)
                    sys.modules[parent_name] = parent_mock

        # Special handling for packages that need specific submodules
        if dep == "watchdog":
            # Create watchdog.events submodule
            watchdog_events = _create_mock_module("watchdog.events")
            sys.modules["watchdog.events"] = watchdog_events

            # Add FileSystemEventHandler class
            class FileSystemEventHandler:
                def on_modified(self, event):
                    pass

                def on_created(self, event):
                    pass

                def on_deleted(self, event):
                    pass

            watchdog_events.FileSystemEventHandler = FileSystemEventHandler
            mock_module.events = watchdog_events

            # Create watchdog.observers submodule
            watchdog_observers = _create_mock_module("watchdog.observers")
            sys.modules["watchdog.observers"] = watchdog_observers

            # Add Observer class
            class Observer:
                def __init__(self):
                    pass

                def schedule(self, *args, **kwargs):
                    pass

                def start(self):
                    pass

                def stop(self):
                    pass

                def join(self):
                    pass

            watchdog_observers.Observer = Observer
            mock_module.observers = watchdog_observers
        elif dep == "defusedxml":
            # Create defusedxml.ElementTree submodule
            defusedxml_et = _create_mock_module("defusedxml.ElementTree")
            sys.modules["defusedxml.ElementTree"] = defusedxml_et
            mock_module.ElementTree = defusedxml_et
            # Add common ElementTree functions
            defusedxml_et.parse = lambda *args, **kwargs: None
            defusedxml_et.fromstring = lambda *args, **kwargs: None
            defusedxml_et.XML = lambda *args, **kwargs: None
        elif dep in ("grpc", "grpcio"):
            # Create grpc.aio submodule for async gRPC (handle both grpc and grpcio idempotently)
            if "grpc.aio" not in sys.modules:
                grpc_aio = _create_mock_module("grpc.aio")
                sys.modules["grpc.aio"] = grpc_aio
                mock_module.aio = grpc_aio
                # insecure_channel will be handled by __getattr__


# ---- Optional: Pytest fixture for any additional test setup ----
try:
    import pytest

    @pytest.fixture(scope="session", autouse=True)
    def _test_setup():
        """
        Optional pytest fixture for any additional test setup.
        Mocks are already set up at module level, so this is just a placeholder.
        """
        yield

except ImportError:
    # pytest not available (e.g., when conftest is imported outside of pytest context)
    pass
