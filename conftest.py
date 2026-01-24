import os
import sys
import types

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
# Set environment variables to skip expensive initialization during test collection
os.environ["TESTING"] = "1"
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("PYTEST_CURRENT_TEST", "true")
os.environ.setdefault("PYTEST_COLLECTING", "1")
os.environ.setdefault("SKIP_AUDIT_INIT", "1")
os.environ.setdefault("SKIP_BACKGROUND_TASKS", "1")
os.environ.setdefault("NO_MONITORING", "1")
os.environ.setdefault("DISABLE_TELEMETRY", "1")

# ---- Add minimal stubs for missing modules (TEST ENVIRONMENT ONLY) ----
# Create stub modules with minimal functionality to prevent import errors during test collection
# IMPORTANT: Only create stubs during testing to avoid interfering with production imports
import importlib.util

# Only create stubs if we're in a test environment (TESTING=1 is set at the top of this file)
if os.environ.get("TESTING") == "1":
    _stub_modules = {
        'intent_capture': 'intent_capture',
        'audit_log': 'audit_log',
        'omnicore_engine.database': 'omnicore_engine.database',
        'omnicore_engine.message_bus': 'omnicore_engine.message_bus',
    }

    for module_name in _stub_modules.keys():
        if module_name not in sys.modules:
            # Create a minimal stub module
            stub = types.ModuleType(module_name)
            stub.__file__ = f"<stub {module_name}>"
            stub.__path__ = []
            stub.__spec__ = importlib.util.spec_from_loader(module_name, loader=None)
            
            # Add a __getattr__ that returns no-op callables
            def _stub_getattr(name):
                """Return a no-op callable for any attribute access."""
                return lambda *args, **kwargs: None
            
            stub.__getattr__ = _stub_getattr
            sys.modules[module_name] = stub
            
            # Create parent modules for dotted packages
            if "." in module_name:
                parts = module_name.split(".")
                for i in range(1, len(parts)):
                    parent_name = ".".join(parts[:i])
                    if parent_name not in sys.modules:
                        parent_stub = types.ModuleType(parent_name)
                        parent_stub.__file__ = f"<stub {parent_name}>"
                        parent_stub.__path__ = []
                        parent_stub.__spec__ = importlib.util.spec_from_loader(parent_name, loader=None)
                        parent_stub.__getattr__ = _stub_getattr
                        sys.modules[parent_name] = parent_stub

# ---- Import error handling ----
# Provide graceful fallbacks for common missing dependencies during test collection
# This allows pytest to collect tests even when optional dependencies are missing


def _create_mock_module(name):
    """Create a minimal mock module for missing dependencies."""
    import types
    import importlib.util

    # Create a mock class that can be used as decorator or callable
    class MockCallable:
        """
        A versatile mock object that supports multiple usage patterns:
        - As a decorator: @mock.method(args)
        - As a callable: mock.function()
        - As an attribute chain: mock.sub.module.attr
        - As a context manager: with mock.context():
        - As a generic type: mock.Type[str]
        - As a class instantiation: mock.Class(args)
        """

        def __init__(self, *args, **kwargs):
            """Accept any arguments during instantiation."""
            pass

        def __call__(self, *args, **kwargs):
            # When used as a decorator, handle Pydantic validators specially
            import inspect
            
            if len(args) == 1:
                arg = args[0]
                # If it's a function/method being decorated
                if callable(arg) and hasattr(arg, '__name__'):
                    # For Pydantic validators, ensure it's a classmethod
                    if not isinstance(arg, classmethod):
                        # Check if this looks like a validator (has 'cls' as first param)
                        try:
                            sig = inspect.signature(arg)
                            params = list(sig.parameters.keys())
                            if params and params[0] == 'cls':
                                # This is likely a Pydantic validator, wrap as classmethod
                                return classmethod(arg)
                        except (TypeError, ValueError, AttributeError):
                            pass
                    # Return the original function/classmethod unchanged
                    return arg
                # If it's a string (field name for validator), return a decorator
                elif isinstance(arg, str):
                    def validator_decorator(func):
                        # Wrap in classmethod if needed
                        if not isinstance(func, classmethod):
                            try:
                                sig = inspect.signature(func)
                                params = list(sig.parameters.keys())
                                if params and params[0] == 'cls':
                                    return classmethod(func)
                            except (TypeError, ValueError, AttributeError):
                                pass
                        return func
                    return validator_decorator
            
            # Otherwise return self to support chaining
            return self

        def __set_name__(self, owner, name):
            """Called when MockCallable is assigned as a class attribute.
            This prevents Pydantic from treating it as a field."""
            pass

        def __getattr__(self, attr):
            # Return another MockCallable for attribute access
            return MockCallable()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def __class_getitem__(cls, item):
            """Support for generic type annotations like Type[str]."""
            return cls

        def __mro_entries__(self, bases):
            """Support for use in class inheritance - return object as base."""
            return (object,)

    mock_module = types.ModuleType(name)
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
        class MockDynaconf:
            def __init__(self, *args, **kwargs):
                self._data = {}

            def get(self, key, default=None):
                return self._data.get(key, default)

            def set(self, key, value):
                self._data[key] = value

            def __getattr__(self, name):
                return self._data.get(name)

        class MockValidator:
            def __init__(self, *args, **kwargs):
                pass

        mock_module.Dynaconf = MockDynaconf
        mock_module.Validator = MockValidator
    elif name == "torch":
        # torch needs __version__ as a string (not MockCallable) to prevent errors
        # in packaging.version.Version() calls (e.g., from safetensors.torch)
        mock_module.__version__ = "2.9.1"
    elif name == "transformers":
        # transformers also needs __version__ as a string
        mock_module.__version__ = "4.30.0"
    elif name == "sentence_transformers":
        # sentence_transformers also needs __version__ as a string
        mock_module.__version__ = "2.2.0"
    elif name == "pydantic":
        # pydantic needs proper class definitions for inheritance
        class MockSecretStr:
            """Mock SecretStr that can be used as a base class."""

            def __init__(self, value: str):
                self._value = str(value)

            def get_secret_value(self) -> str:
                return self._value

            def __repr__(self) -> str:
                return "SecretStr('**********')"

            def __str__(self) -> str:
                return "**********"

        class MockBaseModel:
            """Mock BaseModel that can be used as a base class."""

            def __init__(self, **data):
                for key, value in data.items():
                    setattr(self, key, value)

            def model_dump(self, **kwargs):
                return self.__dict__

            def model_dump_json(self, **kwargs):
                import json

                return json.dumps(self.__dict__)

        mock_module.SecretStr = MockSecretStr
        mock_module.BaseModel = MockBaseModel
        mock_module.VERSION = "2.0.0"  # VERSION string (not __version__)
        # pydantic also needs __version__ as a string
        mock_module.__version__ = "2.0.0"
        
        # Add field_validator that works properly with Pydantic validators
        def field_validator(*fields, **kwargs):
            """Mock field_validator that preserves function behavior."""
            import inspect
            
            def decorator(func):
                if not isinstance(func, classmethod):
                    try:
                        sig = inspect.signature(func)
                        params = list(sig.parameters.keys())
                        if params and params[0] == 'cls':
                            return classmethod(func)
                    except (TypeError, ValueError, AttributeError):
                        pass
                return func
            
            # Handle @field_validator('field') and @field_validator syntax
            if fields and callable(fields[0]):
                return decorator(fields[0])
            return decorator
        
        mock_module.field_validator = field_validator
        mock_module.model_validator = field_validator
        mock_module.validator = field_validator

    return mock_module


# Packages that should NEVER be mocked, even if missing
# These packages cause type annotation errors or decorator issues when mocked
_NEVER_MOCK = [
    "aiohttp",  # Type annotations used in aiohttp_client_cache
    "aiohttp_client_cache",  # Uses aiohttp.ClientResponse in type hints
    "pydantic",  # Decorators like field_validator must be real
    "pydantic_settings",  # Must work with real pydantic
    "pydantic_core",  # Core pydantic functionality
    "fastapi",  # __spec__ errors and type annotation issues
    "starlette",  # FastAPI dependency, needs proper __spec__
]

# Only mock if genuinely missing (not if already imported)
_OPTIONAL_DEPENDENCIES = [
    "tiktoken",  # Often missing, used by LLM clients
    "aiofiles",  # Required by generator.main.api
    "aiofiles.os",  # Required by test_generation modules
    "backoff",  # Required by generator.main.api
    "uvicorn",  # Required by generator.main
    "jwt",  # Required by generator.main.api
    "sqlalchemy",  # Required by many modules
    "redis",  # Required by various modules
    "redis.asyncio",  # Required by generator.main.api
    "dotenv",  # Required by many modules
    "dynaconf",  # Required by runner modules
    "anthropic",  # Required by arbiter.plugins
    "google.generativeai",  # Required by arbiter.plugins
    "google.api_core",  # Required by arbiter.plugins
    "google.api_core.exceptions",  # Required by arbiter.plugins
    "openai",  # Required by LLM providers
    "neo4j",  # Required by knowledge_graph
    "chromadb",  # Required by knowledge_graph
    "chromadb.utils",  # Required by testgen_agent
    "httpx",  # Required by explainable_reasoner
    "freezegun",  # Required by test files
    "torch",  # PyTorch - causes DLL errors on Windows
    "sentence_transformers",  # Uses torch, causes DLL errors
    "transformers",  # Uses torch, causes DLL errors
    "spacy",  # Uses torch via thinc, causes DLL errors
    "presidio_analyzer",  # Uses spacy, causes DLL errors
    "presidio_anonymizer",  # Uses spacy, causes DLL errors
    "networkx",  # Graph library
    "defusedxml",  # XML parsing
    "defusedxml.ElementTree",  # XML parsing - required by test_generation
    "beautifulsoup4",  # HTML parsing
    "bs4",  # BeautifulSoup alias
    "portalocker",  # File locking - required by bug_manager
    "structlog",  # Structured logging - required by explainable_reasoner
    "circuitbreaker",  # Circuit breaker pattern - required by arbiter modules
    "gnosis",  # Gnosis safe - required by audit_ledger_client
    "sentry_sdk",  # Sentry error tracking
    "asyncpg",  # Async PostgreSQL - required by postgres_client
    "web3",  # Web3.py - Ethereum library
    "feast",  # Feature store
    "ray",  # Distributed computing
    "scipy",  # Scientific computing
    "great_expectations",  # Data validation
    "merklelib",  # Merkle tree library
    "gymnasium",  # Reinforcement learning environments
    "deap",  # Evolutionary algorithms
    "langchain_openai",  # LangChain OpenAI integration
    "cerberus",  # Schema validation - required by policy module
    "PIL",  # Pillow - image processing
    "pillow",  # Pillow alternative import name
    # Note: prometheus_client, aiohttp, and aiosqlite should be installed
    # and should NOT be mocked as they are critical for proper type checking
    # Omnicore engine submodules that may have missing dependencies
    "omnicore_engine.database",  # May be missing aiosqlite or other dependencies
    "omnicore_engine.message_bus",  # May be missing structlog or other dependencies
]

# Special handling for botocore.exceptions - must be proper exception classes
if "botocore.exceptions" not in sys.modules:
    try:
        import botocore.exceptions
    except ImportError:
        import types
        import importlib.util
        
        # Create botocore.exceptions with REAL exception classes
        botocore_exc_module = types.ModuleType("botocore.exceptions")
        botocore_exc_module.__file__ = "<mocked botocore.exceptions>"
        botocore_exc_module.__path__ = []
        botocore_exc_module.__spec__ = importlib.util.spec_from_loader(
            "botocore.exceptions", loader=None
        )
        
        # Create proper exception classes that inherit from BaseException
        # These are independent exceptions - they don't need to inherit from each other
        class BotoCoreError(Exception):
            """Base exception for botocore errors."""
            pass
        
        class NoCredentialsError(Exception):
            """Raised when AWS credentials are not found."""
            pass
        
        class ClientError(Exception):
            """Raised when AWS service returns an error."""
            def __init__(self, error_response=None, operation_name=None):
                self.response = error_response or {}
                self.operation_name = operation_name
                super().__init__(f"An error occurred ({operation_name}): {error_response}")
        
        botocore_exc_module.BotoCoreError = BotoCoreError
        botocore_exc_module.NoCredentialsError = NoCredentialsError
        botocore_exc_module.ClientError = ClientError
        
        # Create parent botocore module if needed
        if "botocore" not in sys.modules:
            botocore_module = _create_mock_module("botocore")
            sys.modules["botocore"] = botocore_module
        
        sys.modules["botocore.exceptions"] = botocore_exc_module

# ---- Deferred optional dependency mock initialization ----
# Skip expensive import loop at module level - defer to pytest fixture
# This prevents CPU timeout during conftest.py import in CI
_mocks_initialized = False

def _initialize_optional_dependency_mocks():
    """
    Initialize mocks for optional dependencies.
    Called from session-scoped fixture to defer expensive operations.
    """
    global _mocks_initialized
    if _mocks_initialized:
        return
    _mocks_initialized = True

    for dep in _OPTIONAL_DEPENDENCIES:
        # Skip packages that should never be mocked
        if any(dep == never_mock or dep.startswith(never_mock + ".") for never_mock in _NEVER_MOCK):
            continue
    
        if dep not in sys.modules:
            try:
                __import__(dep)
            except (ImportError, OSError, AttributeError):
                # Create a more sophisticated mock that handles submodule access
                # OSError is caught to handle DLL initialization failures on Windows (e.g., torch)
                # AttributeError is caught to handle bugs in packages like gnosis-py that use removed Python 2 functions (e.g., string.join() which was removed in Python 3.0)
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
                if dep == "sqlalchemy":
                    # Create common sqlalchemy submodules
                    for submod in ["orm", "exc", "dialects", "dialects.sqlite", "dialects.postgresql", 
                                   "engine", "ext", "ext.asyncio", "sql", "sql.expression", "types"]:
                        submod_name = f"sqlalchemy.{submod}"
                        if submod_name not in sys.modules:
                            submod_mock = _create_mock_module(submod_name)
                            sys.modules[submod_name] = submod_mock
                            # Set as attribute on parent module
                            parts = submod.split(".")
                            parent = mock_module
                            for part in parts[:-1]:
                                parent = getattr(parent, part, _create_mock_module(f"sqlalchemy.{part}"))
                            setattr(parent, parts[-1], submod_mock)
                
                    # Add common SQLAlchemy components
                    # DeclarativeBase for model definitions
                    class MockDeclarativeBase:
                        pass
                
                    # Column types
                    class MockColumn:
                        def __init__(self, *args, **kwargs):
                            pass
                
                    class MockInteger:
                        pass
                
                    class MockString:
                        def __init__(self, *args, **kwargs):
                            pass
                
                    class MockText:
                        pass
                
                    class MockDateTime:
                        pass
                
                    class MockBoolean:
                        pass
                
                    class MockFloat:
                        pass
                
                    class MockJSON:
                        pass
                
                    class MockForeignKey:
                        def __init__(self, *args, **kwargs):
                            pass
                
                    # Session and engine mocks
                    class MockSession:
                        def __init__(self, *args, **kwargs):
                            pass
                        def add(self, *args, **kwargs):
                            pass
                        def commit(self, *args, **kwargs):
                            pass
                        def rollback(self, *args, **kwargs):
                            pass
                        def query(self, *args, **kwargs):
                            return self
                        def filter(self, *args, **kwargs):
                            return self
                        def all(self, *args, **kwargs):
                            return []
                        def first(self, *args, **kwargs):
                            return None
                        def close(self, *args, **kwargs):
                            pass
                        def __enter__(self):
                            return self
                        def __exit__(self, *args):
                            pass
                
                    class MockEngine:
                        def __init__(self, *args, **kwargs):
                            pass
                        def connect(self, *args, **kwargs):
                            return MockSession()
                        def dispose(self, *args, **kwargs):
                            pass
                        def begin(self, *args, **kwargs):
                            return MockSession()
                
                    # Add to orm submodule
                    if "sqlalchemy.orm" in sys.modules:
                        orm_mod = sys.modules["sqlalchemy.orm"]
                        orm_mod.Session = MockSession
                        orm_mod.declarative_base = lambda *args, **kwargs: type('Base', (), {'metadata': type('Metadata', (), {'clear': lambda self: None, 'create_all': lambda self, *a, **kw: None})()})
                        orm_mod.sessionmaker = lambda *args, **kwargs: MockSession
                        orm_mod.relationship = lambda *args, **kwargs: None
                
                    # Add to ext.asyncio submodule
                    if "sqlalchemy.ext.asyncio" in sys.modules:
                        ext_asyncio_mod = sys.modules["sqlalchemy.ext.asyncio"]
                        ext_asyncio_mod.create_async_engine = lambda *args, **kwargs: MockEngine()
                        ext_asyncio_mod.AsyncSession = MockSession
                        ext_asyncio_mod.async_sessionmaker = lambda *args, **kwargs: MockSession
                
                    # Add common functions/classes to main module
                    mock_module.Column = MockColumn
                    mock_module.Integer = MockInteger
                    mock_module.String = MockString
                    mock_module.Text = MockText
                    mock_module.DateTime = MockDateTime
                    mock_module.Boolean = MockBoolean
                    mock_module.Float = MockFloat
                    mock_module.JSON = MockJSON
                    mock_module.ForeignKey = MockForeignKey
                    mock_module.create_engine = lambda *args, **kwargs: MockEngine()
                
                    # Add insert function to dialects.sqlite
                    if "sqlalchemy.dialects.sqlite" in sys.modules:
                        sqlite_mod = sys.modules["sqlalchemy.dialects.sqlite"]
                        sqlite_mod.insert = lambda *args, **kwargs: type('Insert', (), {
                            'on_conflict_do_update': lambda *a, **kw: None,
                            'on_conflict_do_nothing': lambda *a, **kw: None,
                        })()
            
                elif dep == "structlog":
                    # structlog needs a mock logger with .bind() method
                    class MockBoundLogger:
                        def __init__(self, *args, **kwargs):
                            pass
                    
                        def bind(self, *args, **kwargs):
                            return self
                    
                        def unbind(self, *args, **kwargs):
                            return self
                    
                        def new(self, *args, **kwargs):
                            return self
                    
                        def debug(self, *args, **kwargs):
                            pass
                    
                        def info(self, *args, **kwargs):
                            pass
                    
                        def warning(self, *args, **kwargs):
                            pass
                    
                        def warn(self, *args, **kwargs):
                            pass
                    
                        def error(self, *args, **kwargs):
                            pass
                    
                        def critical(self, *args, **kwargs):
                            pass
                    
                        def exception(self, *args, **kwargs):
                            pass
                    
                        def msg(self, *args, **kwargs):
                            pass
                    
                        def log(self, *args, **kwargs):
                            pass
                
                    mock_module.get_logger = lambda *args, **kwargs: MockBoundLogger()
                    mock_module.configure = lambda *args, **kwargs: None
                    mock_module.wrap_logger = lambda *args, **kwargs: MockBoundLogger()
                    mock_module.BoundLogger = MockBoundLogger
                
                    # Create stdlib submodule
                    stdlib_module = _create_mock_module("structlog.stdlib")
                    sys.modules["structlog.stdlib"] = stdlib_module
                    stdlib_module.add_logger_name = lambda *args, **kwargs: None
                    stdlib_module.add_log_level = lambda *args, **kwargs: None
                    stdlib_module.LoggerFactory = lambda *args, **kwargs: None
                    stdlib_module.BoundLogger = MockBoundLogger
                    mock_module.stdlib = stdlib_module
                
                    # Create processors submodule
                    processors_module = _create_mock_module("structlog.processors")
                    sys.modules["structlog.processors"] = processors_module
                    processors_module.TimeStamper = lambda *args, **kwargs: None
                    processors_module.StackInfoRenderer = lambda *args, **kwargs: None
                    processors_module.JSONRenderer = lambda *args, **kwargs: None
                    mock_module.processors = processors_module
            
                elif dep == "PIL" or dep == "pillow":
                    # PIL/Pillow needs mock Image class
                    class MockImage:
                        def __init__(self, *args, **kwargs):
                            pass
                    
                        @staticmethod
                        def open(*args, **kwargs):
                            return MockImage()
                    
                        @staticmethod
                        def new(*args, **kwargs):
                            return MockImage()
                    
                        @staticmethod
                        def frombytes(*args, **kwargs):
                            return MockImage()
                    
                        def save(self, *args, **kwargs):
                            pass
                    
                        def convert(self, *args, **kwargs):
                            return self
                    
                        def resize(self, *args, **kwargs):
                            return self
                    
                        def crop(self, *args, **kwargs):
                            return self
                    
                        @property
                        def size(self):
                            return (100, 100)
                    
                        @property
                        def mode(self):
                            return "RGB"
                
                    mock_module.Image = MockImage
            
                elif dep == "asyncpg":
                    # asyncpg needs pool submodule
                    pool_module = _create_mock_module("asyncpg.pool")
                    sys.modules["asyncpg.pool"] = pool_module
                
                    class MockPool:
                        async def acquire(self):
                            return MockConnection()
                    
                        async def release(self, *args, **kwargs):
                            pass
                    
                        async def close(self):
                            pass
                
                    class MockConnection:
                        async def execute(self, *args, **kwargs):
                            return None
                    
                        async def fetch(self, *args, **kwargs):
                            return []
                    
                        async def fetchrow(self, *args, **kwargs):
                            return None
                    
                        async def fetchval(self, *args, **kwargs):
                            return None
                    
                        async def close(self):
                            pass
                
                    pool_module.Pool = MockPool
                    mock_module.pool = pool_module
                    mock_module.Pool = MockPool
                    mock_module.create_pool = lambda *args, **kwargs: MockPool()
                    mock_module.connect = lambda *args, **kwargs: MockConnection()

# ---- Tenacity stub setup ----
# Tenacity requires special handling for its retry decorator and combinable conditions
if "tenacity" not in sys.modules:
    try:
        import tenacity as _test_tenacity
    except ImportError:
        # Create complete tenacity stubs
        import types
        
        # Create a retry predicate that supports the | operator
        class _RetryPredicate:
            def __or__(self, other):
                return _RetryPredicate()
            
            def __and__(self, other):
                return _RetryPredicate()
        
        # Create the tenacity module
        import importlib.util
        tenacity_module = types.ModuleType("tenacity")
        tenacity_module.__file__ = "<mocked tenacity>"
        tenacity_module.__path__ = []
        tenacity_module.__spec__ = importlib.util.spec_from_loader("tenacity", loader=None)
        
        # Retry decorator - returns the function unchanged
        def mock_retry(*args, **kwargs):
            def decorator(func):
                return func
            if len(args) == 1 and callable(args[0]):
                return args[0]
            return decorator
        
        # Wait strategy class that supports + operator
        class _WaitStrategy:
            def __add__(self, other):
                return _WaitStrategy()
            def __radd__(self, other):
                return _WaitStrategy()
            def __call__(self, *args, **kwargs):
                return 0
        
        # Stop strategy class
        class _StopStrategy:
            def __call__(self, *args, **kwargs):
                return True
        
        tenacity_module.retry = mock_retry
        tenacity_module.stop_after_attempt = lambda *args, **kwargs: _StopStrategy()
        tenacity_module.stop_after_delay = lambda *args, **kwargs: _StopStrategy()
        tenacity_module.wait_exponential = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.wait_exponential_jitter = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.wait_random = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.wait_random_exponential = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.wait_fixed = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.wait_chain = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.retry_if_exception_type = lambda *args, **kwargs: _RetryPredicate()
        tenacity_module.retry_if_exception = lambda *args, **kwargs: _RetryPredicate()
        tenacity_module.retry_if_result = lambda *args, **kwargs: _RetryPredicate()
        tenacity_module.before_sleep_log = lambda *args, **kwargs: None
        tenacity_module.after_log = lambda *args, **kwargs: None
        tenacity_module.before_log = lambda *args, **kwargs: None
        
        # Create wait submodule for tenacity.wait.wait_exponential style access
        wait_module = types.ModuleType("tenacity.wait")
        wait_module.__file__ = "<mocked tenacity.wait>"
        wait_module.__path__ = []
        wait_module.wait_exponential = lambda *args, **kwargs: _WaitStrategy()
        wait_module.wait_exponential_jitter = lambda *args, **kwargs: _WaitStrategy()
        wait_module.wait_random = lambda *args, **kwargs: _WaitStrategy()
        wait_module.wait_random_exponential = lambda *args, **kwargs: _WaitStrategy()
        wait_module.wait_fixed = lambda *args, **kwargs: _WaitStrategy()
        wait_module.wait_chain = lambda *args, **kwargs: _WaitStrategy()
        tenacity_module.wait = wait_module
        sys.modules["tenacity.wait"] = wait_module
        
        # Create stop submodule for tenacity.stop.stop_after_attempt style access
        stop_module = types.ModuleType("tenacity.stop")
        stop_module.__file__ = "<mocked tenacity.stop>"
        stop_module.__path__ = []
        stop_module.stop_after_attempt = lambda *args, **kwargs: _StopStrategy()
        stop_module.stop_after_delay = lambda *args, **kwargs: _StopStrategy()
        stop_module.stop_never = lambda *args, **kwargs: _StopStrategy()
        tenacity_module.stop = stop_module
        sys.modules["tenacity.stop"] = stop_module
        
        # Exception classes
        class RetryError(Exception):
            pass
        
        class TryAgain(Exception):
            pass
        
        tenacity_module.RetryError = RetryError
        tenacity_module.TryAgain = TryAgain
        
        # Additional classes needed by some modules
        class RetryCallState:
            def __init__(self, *args, **kwargs):
                pass
        
        tenacity_module.RetryCallState = RetryCallState
        
        # Create Retrying class
        class Retrying:
            def __init__(self, *args, **kwargs):
                pass
            def __call__(self, *args, **kwargs):
                return args[0] if args else lambda x: x
            def __iter__(self):
                return iter([])
        
        tenacity_module.Retrying = Retrying
        
        # Register the module
        sys.modules["tenacity"] = tenacity_module


# ---- OpenTelemetry stub setup ----
# NOTE: OpenTelemetry stubs are handled by generator/conftest.py
# Removed duplicate OpenTelemetry setup (433 lines) to reduce import time

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
        "ClientResponse": getattr(aiohttp, "ClientResponse", None),
        "ClientSession": getattr(aiohttp, "ClientSession", None),
    }
    
    # Ensure they are not replaced during test collection
    def _protect_aiohttp():
        """Restore aiohttp types if they've been replaced with mocks."""
        for name, original_type in _ORIGINAL_AIOHTTP_TYPES.items():
            if original_type and hasattr(aiohttp, name):
                current_type = getattr(aiohttp, name)
                # Check if it's been replaced with a mock
                if hasattr(current_type, '_mock_name') or 'Mock' in str(type(current_type).__name__):
                    setattr(aiohttp, name, original_type)
    
    _protect_aiohttp()
except ImportError:
    _ORIGINAL_AIOHTTP_TYPES = {}


# ---- Protect common exception types from being mocked ----
# Store references to common exception types before they can be mocked
try:
    import cryptography.fernet

    _ORIGINAL_CRYPTO_EXCEPTIONS = {
        "InvalidToken": getattr(cryptography.fernet, "InvalidToken", Exception),
    }
except ImportError:
    _ORIGINAL_CRYPTO_EXCEPTIONS = {}


# ---- Runner module stub setup ----
# NOTE: Do NOT create a runner stub here. The generator/conftest.py adds generator/
# to sys.path which makes generator/runner importable as 'runner'. Creating a stub
# here would shadow the real module and cause import errors.
# If runner tests fail, the generator/conftest.py will handle the path setup.


# ---- Prometheus Client stub setup ----
# Moved to setup_test_stubs fixture to defer expensive operations

def _initialize_prometheus_stubs():
    """
    Initialize prometheus_client stub modules.
    
    This function can be called from the setup_test_stubs session fixture
    or at module level (when not in collection mode). It's safe to call multiple
    times - it will only create stubs if prometheus_client is not already available.
    
    If not called, tests that import prometheus_client will fail with ImportError,
    and any code using Prometheus metrics will fail at runtime.
    
    This is deferred during test collection (PYTEST_COLLECTING=1) to avoid import-time
    overhead, which was causing timeout issues.
    """
    # prometheus_client needs special handling for its .core submodule
    if "prometheus_client" not in sys.modules:
        try:
            import prometheus_client
        except ImportError:
            # Create prometheus_client package stub
            prom_module = types.ModuleType("prometheus_client")
            prom_module.__file__ = "<mocked prometheus_client>"
            prom_module.__path__ = []  # Make it a package
            prom_module.__spec__ = importlib.util.spec_from_loader(
                "prometheus_client", loader=None
            )

            # Create core submodule
            prom_core = types.ModuleType("prometheus_client.core")
            prom_core.__file__ = "<mocked prometheus_client.core>"
            prom_core.__path__ = []  # Make it a package
            prom_core.__spec__ = importlib.util.spec_from_loader(
                "prometheus_client.core", loader=None
            )
            prom_module.core = prom_core

            # Create registry submodule
            prom_registry = types.ModuleType("prometheus_client.registry")
            prom_registry.__file__ = "<mocked prometheus_client.registry>"
            prom_registry.__path__ = []  # Make it a package
            prom_registry.__spec__ = importlib.util.spec_from_loader(
                "prometheus_client.registry", loader=None
            )
            prom_module.registry = prom_registry

            # Add common classes/functions to core
            class _MockHistogramMetricFamily:
                def __init__(self, *args, **kwargs):
                    pass

            prom_core.HistogramMetricFamily = _MockHistogramMetricFamily

            # Add common classes/functions to main module
            class _MockCollectorRegistry:
                def __init__(self, *args, **kwargs):
                    self._names_to_collectors = {}
                    self._collector_to_names = {}

                def register(self, collector):
                    pass

                def unregister(self, collector):
                    pass

                def get_sample_value(self, *args, **kwargs):
                    return None

            class _MockCounter:
                def __init__(self, *args, **kwargs):
                    pass

                def labels(self, *args, **kwargs):
                    return self

                def inc(self, *args, **kwargs):
                    pass

            class _MockHistogram:
                DEFAULT_BUCKETS = (
                    0.005,
                    0.01,
                    0.025,
                    0.05,
                    0.075,
                    0.1,
                    0.25,
                    0.5,
                    0.75,
                    1.0,
                    2.5,
                    5.0,
                    7.5,
                    10.0,
                    float("inf"),
                )

                def __init__(self, *args, **kwargs):
                    pass

                def labels(self, *args, **kwargs):
                    return self

                def observe(self, *args, **kwargs):
                    pass

                def time(self, *args, **kwargs):
                    # Return a decorator/context manager that works for both @decorator and with statement
                    from contextlib import nullcontext

                    def decorator(func):
                        return func

                    # Make the decorator also work as a context manager
                    decorator.__enter__ = lambda: None
                    decorator.__exit__ = lambda *args: None
                    return decorator

            class _MockGauge:
                def __init__(self, *args, **kwargs):
                    pass

                def labels(self, *args, **kwargs):
                    return self

                def set(self, *args, **kwargs):
                    pass

                def inc(self, *args, **kwargs):
                    pass

                def dec(self, *args, **kwargs):
                    pass

            class _MockInfo:
                def __init__(self, *args, **kwargs):
                    pass

                def labels(self, *args, **kwargs):
                    return self

                def info(self, *args, **kwargs):
                    pass

            prom_module.CollectorRegistry = _MockCollectorRegistry
            prom_module.Counter = _MockCounter
            prom_module.Histogram = _MockHistogram
            prom_module.Gauge = _MockGauge
            prom_module.Info = _MockInfo
            prom_module.Summary = _MockHistogram  # Summary is similar to Histogram
            prom_module.ProcessCollector = lambda *args, **kwargs: None
            prom_module.PROCESS_COLLECTOR = None  # Process collector singleton
            prom_module.PLATFORM_COLLECTOR = lambda *args, **kwargs: None
            prom_module.GC_COLLECTOR = None  # GC collector singleton
            prom_module.generate_latest = lambda *args, **kwargs: b""
            prom_module.start_http_server = lambda *args, **kwargs: None
            prom_module.REGISTRY = _MockCollectorRegistry()
            prom_module.CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

            # Create multiprocess submodule
            prom_multiprocess = types.ModuleType("prometheus_client.multiprocess")
            prom_multiprocess.__file__ = "<mocked prometheus_client.multiprocess>"
            prom_multiprocess.__path__ = []
            prom_multiprocess.__spec__ = importlib.util.spec_from_loader(
                "prometheus_client.multiprocess", loader=None
            )
            prom_multiprocess.MultiProcessCollector = lambda *args, **kwargs: None
            prom_module.multiprocess = prom_multiprocess

            # Create metrics submodule
            prom_metrics = types.ModuleType("prometheus_client.metrics")
            prom_metrics.__file__ = "<mocked prometheus_client.metrics>"
            prom_metrics.__path__ = []  # Make it a package
            prom_metrics.__spec__ = importlib.util.spec_from_loader(
                "prometheus_client.metrics", loader=None
            )

            # Create a base class for metric wrappers
            class MetricWrapperBase:
                def __init__(self, *args, **kwargs):
                    pass

            prom_metrics.MetricWrapperBase = MetricWrapperBase
            prom_module.metrics = prom_metrics

            # Register modules
            sys.modules["prometheus_client"] = prom_module
            sys.modules["prometheus_client.core"] = prom_core
            sys.modules["prometheus_client.registry"] = prom_registry
            sys.modules["prometheus_client.metrics"] = prom_metrics
            sys.modules["prometheus_client.multiprocess"] = prom_multiprocess


# ---- Omnicore Engine submodule import protection ----
# Handle omnicore_engine.database and omnicore_engine.message_bus gracefully
# These submodules may have missing dependencies (aiosqlite, structlog, etc.) during test collection
# Deferred to _initialize_omnicore_mocks() to avoid import-time overhead

def _initialize_omnicore_mocks():
    """
    Initialize mocks for omnicore_engine submodules.
    Called from session-scoped fixture to defer expensive import attempts.
    """
    if "omnicore_engine.database" not in sys.modules:
        try:
            import omnicore_engine.database
        except (ImportError, ModuleNotFoundError, OSError) as e:
            print(f"omnicore_engine.database not found. Database functionality disabled. Error: {e}")
            # Create a mock module if not already mocked by optional dependencies
            if "omnicore_engine.database" not in sys.modules:
                database_mock = _create_mock_module("omnicore_engine.database")
                sys.modules["omnicore_engine.database"] = database_mock
                # Ensure parent module exists and has the attribute
                if "omnicore_engine" in sys.modules:
                    sys.modules["omnicore_engine"].database = database_mock

    if "omnicore_engine.message_bus" not in sys.modules:
        try:
            import omnicore_engine.message_bus
        except (ImportError, ModuleNotFoundError, OSError) as e:
            print(f"omnicore_engine.message_bus not found. Message bus functionality disabled. Error: {e}")
            # Create a mock module if not already mocked by optional dependencies
            if "omnicore_engine.message_bus" not in sys.modules:
                message_bus_mock = _create_mock_module("omnicore_engine.message_bus")
                sys.modules["omnicore_engine.message_bus"] = message_bus_mock
                # Ensure parent module exists and has the attribute
                if "omnicore_engine" in sys.modules:
                    sys.modules["omnicore_engine"].message_bus = message_bus_mock

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
        if hasattr(chromadb, "api"):
            if hasattr(chromadb.api, "client"):
                if hasattr(chromadb.api.client, "SharedSystemClient"):
                    client_class = chromadb.api.client.SharedSystemClient
                    if hasattr(client_class, "_identifier_to_system"):
                        client_class._identifier_to_system.clear()

        # Alternative path for different ChromaDB versions
        try:
            from chromadb.api.shared_system_client import SharedSystemClient

            if hasattr(SharedSystemClient, "_identifier_to_system"):
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


# ---- Fix modules without __spec__ ----
def _ensure_module_specs():
    """
    Ensure all modules in sys.modules have __spec__ attribute.
    Some test files create modules with types.ModuleType() without setting __spec__,
    which causes 'AttributeError: __spec__' or 'ValueError: xxx.__spec__ is not set'.
    """
    for name, module in list(sys.modules.items()):
        if module is not None and isinstance(module, types.ModuleType):
            if not hasattr(module, '__spec__') or module.__spec__ is None:
                try:
                    module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
                except Exception:
                    pass  # Some modules can't have spec set
            if not hasattr(module, '__path__'):
                # Only set __path__ if this looks like a package (has submodules)
                if any(m.startswith(name + '.') for m in sys.modules):
                    module.__path__ = []


# Module spec fixing is deferred to session fixture to avoid import-time overhead


# ---- Initialize Prometheus stubs with collection guard ----
# Defer to fixture during collection to avoid expensive initialization
# Only run at module level if NOT in collection phase
if os.environ.get("PYTEST_COLLECTING") != "1":
    _initialize_prometheus_stubs()


# ---- Pytest hooks for collection-time fixes ----
def pytest_collectstart(collector):
    """
    Hook called before collection starts for a collector.
    Ensures all modules have __spec__ to prevent collection errors.
    """
    _ensure_module_specs()


def pytest_collection_finish(session):
    """
    Hook called after test collection is complete.
    Final pass to ensure all modules have __spec__.
    """
    _ensure_module_specs()


# ---- pytest_plugins configuration ----
# Move from nested conftest files to top-level to avoid pytest deprecation warning
pytest_plugins = ["pytest_asyncio"]

# ---- Global pytest fixtures ----
import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_stubs():
    """
    Session-scoped fixture that runs ALL expensive stub/mock initialization.
    This runs AFTER test collection is complete, keeping collection fast.
    
    Includes:
    - Prometheus client stub setup
    - Optional dependency mocks
    - Omnicore engine mocks
    - Module spec fixing
    """
    # Initialize Prometheus stubs (deferred if we were in collection mode)
    _initialize_prometheus_stubs()
    
    # Initialize optional dependency mocks
    _initialize_optional_dependency_mocks()
    
    # Initialize omnicore mocks
    _initialize_omnicore_mocks()
    
    # Fix module specs for all loaded modules
    _ensure_module_specs()
    
    yield
    
    # Also run at end in case modules were added during tests
    _ensure_module_specs()


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


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """
    Set environment variables for all tests to skip expensive initialization.
    
    This fixture ensures that:
    - Event loops are not created during test collection
    - Background tasks are not started
    - Audit logging is minimal
    - Monitoring/telemetry is disabled
    
    These settings dramatically improve test collection speed and prevent
    RuntimeError: no running event loop during pytest collection.
    """
    # Store original values in case they were set
    original_env = {}
    env_vars = {
        "PYTEST_COLLECTING": "1",
        "SKIP_AUDIT_INIT": "1",
        "SKIP_BACKGROUND_TASKS": "1",
        "NO_MONITORING": "1",
        "DISABLE_TELEMETRY": "1",
        "OTEL_SDK_DISABLED": "1",
    }
    
    for key, value in env_vars.items():
        if key in os.environ:
            original_env[key] = os.environ[key]
        os.environ[key] = value
    
    yield
    
    # Restore original environment (cleanup)
    for key in env_vars:
        if key in original_env:
            os.environ[key] = original_env[key]
        elif key in os.environ:
            del os.environ[key]


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


@pytest.fixture(autouse=True)
def protect_pydantic_decorators(monkeypatch):
    """
    Ensure pydantic decorators remain callable and return proper values.
    """
    try:
        import pydantic
        import inspect
        
        # Create a proper validator decorator
        def create_validator_decorator(*fields, **kwargs):
            """Create a validator decorator that preserves classmethod behavior."""
            def decorator(func):
                # Ensure the function is a classmethod for Pydantic v2
                if not isinstance(func, classmethod):
                    try:
                        sig = inspect.signature(func)
                        params = list(sig.parameters.keys())
                        # If first param is 'cls', wrap as classmethod
                        if params and params[0] == 'cls':
                            return classmethod(func)
                    except (TypeError, ValueError, AttributeError):
                        pass
                return func
            
            # Handle both @decorator and @decorator() usage
            if fields and callable(fields[0]) and not kwargs:
                return decorator(fields[0])
            return decorator
        
        # Only patch if pydantic decorators have been replaced with non-callables
        if not callable(getattr(pydantic, 'field_validator', None)):
            monkeypatch.setattr(pydantic, 'field_validator', create_validator_decorator)
        if not callable(getattr(pydantic, 'model_validator', None)):
            monkeypatch.setattr(pydantic, 'model_validator', create_validator_decorator)
        if not callable(getattr(pydantic, 'validator', None)):
            monkeypatch.setattr(pydantic, 'validator', create_validator_decorator)
    except ImportError:
        pass
    
    yield


@pytest.fixture(scope="function", autouse=True)
def protect_sys_modules():
    """
    Protect sys.modules from being permanently modified by test-level mocks.
    Saves a snapshot before the test and restores critical modules after.
    """
    # Save references to critical modules that should not be mocked permanently
    critical_modules = [
        "runner",
        "runner.runner_core",
        "runner.runner_config",
        "runner.runner_logging",
        "runner.runner_metrics",
        "runner.runner_utils",
        "generator.runner",
        "intent_parser",
        "intent_parser.intent_parser",
        "tenacity",
        "aiohttp",
        "pydantic",
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
            if (
                hasattr(current_module, "_mock_name")
                or str(type(current_module).__name__) == "MagicMock"
            ):
                # Restore the original
                sys.modules[mod_name] = original_module
