import os
import sys
import types
from pathlib import Path

# Pre-configure matplotlib backend BEFORE any imports that might use it
# This prevents "can't start new thread" errors during font manager initialization
# Must be set before ANY matplotlib imports occur
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    import matplotlib
    matplotlib.use("Agg")
except (ImportError, RuntimeError):
    # If matplotlib is not installed or already initialized, continue
    pass

# Add the project root to Python path (highest priority)
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

# Add subdirectories at END of path to support relative imports in tests
# (e.g., "from agents.codegen_agent..." in generator/agents/tests/)
# Using append ensures package imports like "from omnicore_engine.meta_supervisor import X"
# still work because project_root (with full package hierarchy) is searched first.
for subdir in ["self_fixing_engineer", "omnicore_engine", "generator"]:
    subdir_path = os.path.join(project_root, subdir)
    if subdir_path not in sys.path:
        sys.path.append(subdir_path)

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
    # Define stubs only for modules that truly need stubbing (are optional/missing)
    # Do NOT create stubs for modules that exist and can be imported
    # NOTE: We no longer stub 'audit_log' because it exists in multiple locations
    # (guardrails/audit_log.py, arbiter/audit_log.py, etc.) and stubbing it
    # breaks tests that import the real module.
    _stub_modules = {}
    
    # Check if modules actually exist WITHOUT importing them
    # (which would trigger expensive initialization)
    # Use find_spec to check module existence
    if importlib.util.find_spec("intent_capture") is None:
        _stub_modules['intent_capture'] = 'intent_capture'
    
    if importlib.util.find_spec("omnicore_engine.database") is None:
        _stub_modules['omnicore_engine.database'] = 'omnicore_engine.database'
    
    if importlib.util.find_spec("omnicore_engine.message_bus") is None:
        _stub_modules['omnicore_engine.message_bus'] = 'omnicore_engine.message_bus'

    def _stub_getattr(name):
        """Return a no-op callable for any attribute access."""
        return lambda *args, **kwargs: None

    for module_name in _stub_modules.keys():
        if module_name not in sys.modules:
            # Create a minimal stub module
            stub = types.ModuleType(module_name)
            stub.__file__ = f"<stub {module_name}>"
            stub.__path__ = []
            stub.__spec__ = importlib.util.spec_from_loader(module_name, loader=None)
            stub.__getattr__ = _stub_getattr
            sys.modules[module_name] = stub
            
            # Create parent modules for dotted packages ONLY if they don't already exist
            if "." in module_name:
                parts = module_name.split(".")
                for i in range(1, len(parts)):
                    parent_name = ".".join(parts[:i])
                    # Don't replace existing modules - this would break package imports
                    if parent_name not in sys.modules:
                        try:
                            # Try to import the parent module first
                            importlib.import_module(parent_name)
                        except ImportError:
                            # Only create stub if parent truly doesn't exist
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
    "aiohttp_client_cache",  # Uses aiohttp.ClientResponse in type hints
    "pydantic",  # Decorators like field_validator must be real
    "pydantic_settings",  # Must work with real pydantic
    "pydantic_core",  # Core pydantic functionality
    "fastapi",  # __spec__ errors and type annotation issues
    "starlette",  # FastAPI dependency, needs proper __spec__
]

# Only mock if genuinely missing (not if already imported)
_OPTIONAL_DEPENDENCIES = [
    "aiohttp",  # HTTP client - required by many modules, needs comprehensive stub
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
    "psutil",  # Process and system utilities - required by transformers for accelerate support
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
    # Note: prometheus_client and aiosqlite should be installed
    # and should NOT be mocked as they are critical for proper type checking
    # Omnicore engine submodules that may have missing dependencies
    "omnicore_engine.database",  # May be missing aiosqlite or other dependencies
    "omnicore_engine.message_bus",  # May be missing structlog or other dependencies
    "analyzer",  # Self-healing import fixer analyzer package
    "analyzer.core_utils",  # Analyzer core utilities
    "analyzer.core_audit",  # Analyzer audit logging
    "analyzer.core_secrets",  # Analyzer secrets manager
]

# Special handling for botocore.exceptions - must be proper exception classes
# DEFERRED: Moved to _initialize_botocore_exceptions() to avoid expensive imports during test collection.
# Module-level imports were causing test collection to timeout after 120 seconds. These operations
# are now executed after collection completes via the setup_test_stubs fixture.
def _initialize_botocore_exceptions():
    """Initialize botocore.exceptions with proper exception classes."""
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
                
                elif dep == "analyzer":
                    # Create analyzer package stub with core submodules
                    sys.modules["analyzer"] = mock_module
                    
                    # Create core_utils mock
                    core_utils_mock = _create_mock_module("analyzer.core_utils")
                    core_utils_mock.alert_operator = lambda msg, level="INFO": None
                    core_utils_mock.scrub_secrets = lambda x: x
                    sys.modules["analyzer.core_utils"] = core_utils_mock
                    mock_module.core_utils = core_utils_mock
                    
                    # Create core_audit mock
                    class MockAuditLogger:
                        def log_event(self, event_type, **kwargs):
                            pass
                        def log(self, *args, **kwargs):
                            pass
                    
                    core_audit_mock = _create_mock_module("analyzer.core_audit")
                    core_audit_mock.audit_logger = MockAuditLogger()
                    core_audit_mock.get_audit_logger = lambda: MockAuditLogger()
                    sys.modules["analyzer.core_audit"] = core_audit_mock
                    mock_module.core_audit = core_audit_mock
                    
                    # Create core_secrets mock
                    class MockSecretsManager:
                        def get_secret(self, key, required=False):
                            return "mock_secret_value"
                    
                    core_secrets_mock = _create_mock_module("analyzer.core_secrets")
                    core_secrets_mock.SECRETS_MANAGER = MockSecretsManager()
                    sys.modules["analyzer.core_secrets"] = core_secrets_mock
                    mock_module.core_secrets = core_secrets_mock

# ---- Tenacity stub setup ----
# Tenacity requires special handling for its retry decorator and combinable conditions
# DEFERRED: Moved to _initialize_tenacity_stubs() to avoid expensive module-level execution
def _initialize_tenacity_stubs():
    """Initialize tenacity stub module - deferred to avoid expensive operations during collection."""
    if "tenacity" not in sys.modules:
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


# ---- aiohttp stub setup ----
# aiohttp requires comprehensive stubbing for async HTTP client functionality
# This is needed because many modules use aiohttp.ClientSession for HTTP requests
# DEFERRED: Moved to _initialize_aiohttp_stubs() to avoid expensive module-level execution
def _initialize_aiohttp_stubs():
    """Initialize aiohttp stub module - deferred to avoid expensive operations during collection."""
    if "aiohttp" not in sys.modules:
        # Create complete aiohttp stubs for test collection
        import types
        import importlib.util
        
        aiohttp_module = types.ModuleType("aiohttp")
        aiohttp_module.__file__ = "<mocked aiohttp>"
        aiohttp_module.__path__ = []
        aiohttp_module.__spec__ = importlib.util.spec_from_loader("aiohttp", loader=None)
        
        # Create ClientTimeout class
        class ClientTimeout:
            """Mock aiohttp.ClientTimeout for test collection."""
            def __init__(self, total=None, connect=None, sock_read=None, sock_connect=None):
                self.total = total
                self.connect = connect
                self.sock_read = sock_read
                self.sock_connect = sock_connect
        
        # Create ClientResponse class
        class ClientResponse:
            """Mock aiohttp.ClientResponse for test collection."""
            def __init__(self, *args, **kwargs):
                self.status = 200
                self.headers = {}
                self._content = b""
            
            async def json(self, *args, **kwargs):
                return {}
            
            async def text(self, *args, **kwargs):
                return ""
            
            async def read(self, *args, **kwargs):
                return b""
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, *args):
                pass
            
            def raise_for_status(self):
                pass
        
        # Create ClientSession class with full async context manager support
        class ClientSession:
            """
            Mock aiohttp.ClientSession for test collection.
            
            Supports async context manager protocol and returns mock responses
            for all HTTP methods. This allows test files to import modules that
            use aiohttp without requiring the actual dependency during collection.
            """
            
            def __init__(self, *args, **kwargs):
                self._closed = False
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                await self.close()
            
            async def close(self):
                self._closed = True
            
            @property
            def closed(self):
                return self._closed
            
            async def _make_request(self, *args, **kwargs):
                return ClientResponse()
            
            async def get(self, *args, **kwargs):
                return ClientResponse()
            
            async def post(self, *args, **kwargs):
                return ClientResponse()
            
            async def put(self, *args, **kwargs):
                return ClientResponse()
            
            async def patch(self, *args, **kwargs):
                return ClientResponse()
            
            async def delete(self, *args, **kwargs):
                return ClientResponse()
            
            async def head(self, *args, **kwargs):
                return ClientResponse()
            
            async def options(self, *args, **kwargs):
                return ClientResponse()
            
            async def request(self, *args, **kwargs):
                return ClientResponse()
        
        # Create exception classes
        class ClientError(Exception):
            """Base exception for aiohttp client errors."""
            pass
        
        class ClientResponseError(ClientError):
            """Exception for HTTP response errors."""
            def __init__(self, request_info=None, history=None, status=None, message=None, headers=None):
                self.request_info = request_info
                self.history = history or ()
                self.status = status or 0
                self.message = message or ""
                self.headers = headers or {}
                super().__init__(f"{self.status}: {self.message}")
        
        class ClientConnectionError(ClientError):
            """Exception for connection errors."""
            pass
        
        class ServerTimeoutError(ClientError):
            """Exception for server timeout errors."""
            pass
        
        class ContentTypeError(ClientError):
            """Exception for content type errors."""
            pass
        
        class InvalidURL(ClientError):
            """Exception for invalid URL errors."""
            pass
        
        # Create TCPConnector class
        class TCPConnector:
            """Mock aiohttp.TCPConnector for test collection."""
            def __init__(self, *args, **kwargs):
                pass
            
            async def close(self):
                pass
        
        # Create BasicAuth class
        class BasicAuth:
            """Mock aiohttp.BasicAuth for test collection."""
            def __init__(self, login, password="", encoding="latin1"):
                self.login = login
                self.password = password
                self.encoding = encoding
        
        # Create FormData class
        class FormData:
            """Mock aiohttp.FormData for test collection."""
            def __init__(self):
                self._fields = []
            
            def add_field(self, name, value, **kwargs):
                self._fields.append((name, value))
        
        # Register all classes and types on the module
        aiohttp_module.ClientTimeout = ClientTimeout
        aiohttp_module.ClientResponse = ClientResponse
        aiohttp_module.ClientSession = ClientSession
        aiohttp_module.ClientError = ClientError
        aiohttp_module.ClientResponseError = ClientResponseError
        aiohttp_module.ClientConnectionError = ClientConnectionError
        aiohttp_module.ServerTimeoutError = ServerTimeoutError
        aiohttp_module.ContentTypeError = ContentTypeError
        aiohttp_module.InvalidURL = InvalidURL
        aiohttp_module.TCPConnector = TCPConnector
        aiohttp_module.BasicAuth = BasicAuth
        aiohttp_module.FormData = FormData
        
        # Register the module
        sys.modules["aiohttp"] = aiohttp_module


# ---- Install Import Hook for Lazy Stub Creation ----
# Install import hooks to create stubs on-demand when modules are first imported
# This avoids creating all stubs at module level, speeding up conftest import significantly
from importlib.abc import MetaPathFinder, Loader
from importlib.machinery import ModuleSpec


class LazyStubImporter(MetaPathFinder):
    """
    Custom import hook that creates stub modules on-demand when they're first imported.
    This significantly speeds up conftest.py import by deferring stub creation until needed.
    
    When a test module tries to import 'tenacity' or 'aiohttp', this hook intercepts
    the import and creates the stub module lazily, avoiding the need to create all
    stubs during conftest import.
    """
    
    def __init__(self):
        self.stub_modules = {
            'tenacity': _initialize_tenacity_stubs,
            'aiohttp': _initialize_aiohttp_stubs,
        }
        self._importing = set()  # Track modules currently being imported to avoid recursion
    
    def find_spec(self, fullname, path, target=None):
        """Find module spec - called by Python's import system."""
        # Only handle modules we have stubs for
        if fullname in self.stub_modules:
            # Check if the module is already in sys.modules
            if fullname not in sys.modules:
                # Avoid recursion - if we're already trying to import this, return None
                if fullname in self._importing:
                    return None
                
                self._importing.add(fullname)
                try:
                    # Try to import the real module
                    __import__(fullname)
                    # If successful, don't create stub - use the real module
                    return None
                except ImportError:
                    # Real module not available, we'll create a stub
                    return ModuleSpec(fullname, LazyStubLoader(self.stub_modules[fullname]))
                finally:
                    self._importing.discard(fullname)
        return None


class LazyStubLoader(Loader):
    """Loader that creates stub modules on-demand."""
    
    def __init__(self, stub_creator):
        self.stub_creator = stub_creator
    
    def create_module(self, spec):
        """Create the stub module by calling its creator function."""
        # Call the stub creator function
        self.stub_creator()
        # Return the module from sys.modules (created by stub_creator)
        return sys.modules.get(spec.name)
    
    def exec_module(self, module):
        """Execute module - no-op since stub is already fully created."""
        pass


# Install the lazy stub importer at the FRONT of sys.meta_path
# This ensures it's checked before other import finders
if not os.environ.get('DISABLE_LAZY_IMPORTER'):
    _lazy_importer = LazyStubImporter()
    sys.meta_path.insert(0, _lazy_importer)
else:
    print("⚠️  LazyStubImporter disabled via DISABLE_LAZY_IMPORTER env var")

# NOTE: Stubs are NO LONGER created immediately - they're created on-demand when first imported
# This dramatically speeds up conftest.py import time from ~6s to <1s


# ---- OpenTelemetry stub setup ----
# NOTE: OpenTelemetry stubs are handled by generator/conftest.py
# Removed duplicate OpenTelemetry setup (433 lines) to reduce import time

# ---- Pydantic decorator safety shim ----
# Prevents test collection-time errors when pydantic decorators are replaced with non-callables
# DEFERRED: Moved to _initialize_pydantic_safety() to avoid import during collection
def _initialize_pydantic_safety():
    """Initialize pydantic decorator safety shim - deferred to avoid expensive operations during collection."""
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
# DEFERRED: Moved to _initialize_tenacity_safety() to avoid import during collection  
def _initialize_tenacity_safety():
    """Initialize tenacity exception safety - deferred to avoid expensive operations during collection."""
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
# DEFERRED: Moved to _initialize_aiohttp_protection() called from fixture
_ORIGINAL_AIOHTTP_TYPES = {}

def _initialize_aiohttp_protection():
    """Initialize aiohttp type protection - deferred to avoid expensive imports during collection."""
    global _ORIGINAL_AIOHTTP_TYPES
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
        
        # Add http_exceptions compatibility shim for older aiohttp_client_cache versions
        # Modern aiohttp (3.9+) removed http_exceptions module, but some libraries still expect it
        if not hasattr(aiohttp, 'http_exceptions'):
            import types as _types
            http_exceptions = _types.ModuleType("aiohttp.http_exceptions")
            http_exceptions.__file__ = "<aiohttp.http_exceptions compatibility shim>"
            http_exceptions.__path__ = []
            
            # Add common HTTP exceptions that were in aiohttp.http_exceptions
            class HttpProcessingError(Exception):
                """HTTP processing error."""
                pass
            
            class BadHttpMessage(HttpProcessingError):
                """Bad HTTP message error."""
                pass
            
            class HttpBadRequest(BadHttpMessage):
                """HTTP 400 Bad Request error."""
                pass
            
            class PayloadEncodingError(BadHttpMessage):
                """Payload encoding error."""
                pass
            
            class ContentEncodingError(BadHttpMessage):
                """Content encoding error."""
                pass
            
            class TransferEncodingError(BadHttpMessage):
                """Transfer encoding error."""
                pass
            
            class LineTooLong(BadHttpMessage):
                """Line too long error."""
                pass
            
            class InvalidHeader(BadHttpMessage):
                """Invalid header error."""
                pass
            
            class BadStatusLine(BadHttpMessage):
                """Bad status line error."""
                pass
            
            class InvalidURLError(BadHttpMessage):
                """Invalid URL error."""
                pass
            
            http_exceptions.HttpProcessingError = HttpProcessingError
            http_exceptions.BadHttpMessage = BadHttpMessage
            http_exceptions.HttpBadRequest = HttpBadRequest
            http_exceptions.PayloadEncodingError = PayloadEncodingError
            http_exceptions.ContentEncodingError = ContentEncodingError
            http_exceptions.TransferEncodingError = TransferEncodingError
            http_exceptions.LineTooLong = LineTooLong
            http_exceptions.InvalidHeader = InvalidHeader
            http_exceptions.BadStatusLine = BadStatusLine
            http_exceptions.InvalidURLError = InvalidURLError
            
            aiohttp.http_exceptions = http_exceptions
            sys.modules["aiohttp.http_exceptions"] = http_exceptions
            
    except ImportError:
        _ORIGINAL_AIOHTTP_TYPES = {}


# ---- Protect common exception types from being mocked ----
# Store references to common exception types before they can be mocked
# DEFERRED: Moved to _initialize_crypto_protection() called from fixture
_ORIGINAL_CRYPTO_EXCEPTIONS = {}

def _initialize_crypto_protection():
    """Initialize cryptography exception protection - deferred to avoid expensive imports during collection."""
    global _ORIGINAL_CRYPTO_EXCEPTIONS
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
# Track if module specs have been ensured
_module_specs_ensured = False


def _ensure_module_specs():
    """
    Ensure all modules in sys.modules have __spec__ attribute.
    Some test files create modules with types.ModuleType() without setting __spec__,
    which causes 'AttributeError: __spec__' or 'ValueError: xxx.__spec__ is not set'.
    
    Performance Note:
        This function is O(n) where n is the number of modules. We only run it once
        to avoid expensive iteration on every collector.
    """
    global _module_specs_ensured
    
    # Only run once to avoid O(n²) complexity during collection
    if _module_specs_ensured:
        return
    _module_specs_ensured = True
    
    # Build set of parent module names for efficient lookup
    # This avoids O(n²) complexity from checking every module against every other
    module_names = set(sys.modules.keys())
    parent_modules = set()
    for name in module_names:
        if '.' in name:
            parts = name.split('.')
            for i in range(1, len(parts)):
                parent_modules.add('.'.join(parts[:i]))
    
    for name, module in list(sys.modules.items()):
        if module is not None and isinstance(module, types.ModuleType):
            if not hasattr(module, '__spec__') or module.__spec__ is None:
                try:
                    module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
                except Exception:
                    pass  # Some modules can't have spec set
            if not hasattr(module, '__path__'):
                # Only set __path__ if this looks like a package (has submodules)
                # Using precomputed set for O(1) lookup instead of O(n) scan
                if name in parent_modules:
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
    # Also ensure aiohttp compatibility for libraries that expect http_exceptions
    _initialize_aiohttp_protection()


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


@pytest.fixture(scope="function")
def event_loop():
    """Create an event loop for each test."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def setup_test_stubs():
    """
    Session-scoped fixture that runs ALL expensive stub/mock initialization.
    This runs AFTER test collection is complete, keeping collection fast.
    
    Includes:
    - Tenacity stub setup
    - Aiohttp stub setup
    - Botocore exceptions setup
    - Aiohttp type protection
    - Cryptography exception protection
    - Prometheus client stub setup
    - Optional dependency mocks
    - Omnicore engine mocks
    - Module spec fixing
    """
    # Note: Tenacity and aiohttp stubs are created on-demand via LazyStubImporter
    # when test modules first import them
    
    # Initialize pydantic safety (deferred from module level)
    _initialize_pydantic_safety()
    
    # Initialize tenacity safety (deferred from module level)
    _initialize_tenacity_safety()
    
    # Initialize botocore exceptions (deferred from module level)
    _initialize_botocore_exceptions()
    
    # Initialize aiohttp protection (deferred from module level)
    _initialize_aiohttp_protection()
    
    # Initialize cryptography protection (deferred from module level)
    _initialize_crypto_protection()
    
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


# ============================================================================
# CONSOLIDATED FIXTURES FROM NESTED conftest.py FILES
# ============================================================================
# The following fixtures have been consolidated from various nested conftest.py
# files to centralize test configuration. Redundant path setup and environment
# variable configuration have been removed since they're handled above.
# ============================================================================


# ============ Fixtures from generator/agents/tests/conftest.py ============

@pytest.fixture
def codegen_env():
    """
    Provides a temporary environment for code generation tests with
    config file, database, and template directory.
    """
    import shutil
    import tempfile
    import yaml
    
    dir_path = Path(tempfile.mkdtemp(prefix="codegen_test_"))
    config = dir_path / "config.yaml"
    db = dir_path / "feedback.db"
    templates = dir_path / "templates"
    templates.mkdir()

    (templates / "python.jinja2").write_text(
        "Generate: {{ requirements.features }}. "
        'JSON: {"files": {"main.py": "def x(): pass"}}',
        encoding="utf-8",
    )

    cfg = {
        "backend": "openai",
        "api_keys": {"openai": "sk-test"},
        "model": {"openai": "gpt-4o"},
        "allow_interactive_hitl": True,
        "enable_security_scan": True,
        "feedback_store": {"type": "sqlite", "path": str(db)},
        "template_dir": str(templates),
        "compliance": {
            "banned_functions": ["eval"],
            "max_line_length": 100,
        },
    }

    with open(config, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)

    yield {
        "config": str(config),
        "db": str(db),
        "req": {"features": ["fib"], "target_language": "python"},
    }

    shutil.rmtree(dir_path, ignore_errors=True)


@pytest.fixture
def generator_mock_llm():
    """Mock LLM for testing code generation in generator module."""
    from unittest.mock import AsyncMock, patch
    
    with patch(
        "generator.agents.codegen_agent.codegen_agent.call_llm_api",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = {"content": '{"files": {"main.py": "def fib(n): return n"}}'}
        yield m


@pytest.fixture(autouse=True)
def cleanup_chromadb():
    """
    Clean up ChromaDB singleton instances between tests to prevent
    'An instance of Chroma already exists' errors.
    """
    yield
    try:
        import chromadb
        from chromadb.api.shared_system_client import SharedSystemClient
        if hasattr(SharedSystemClient, "_identifier_to_system"):
            SharedSystemClient._identifier_to_system.clear()
    except (ImportError, AttributeError):
        pass


# ============ Fixtures from generator/main/tests/conftest.py ============

@pytest.fixture
def mock_modules(monkeypatch):
    """
    Fixture to mock modules needed by tests.
    Use this in tests that need specific modules mocked.
    
    Usage:
        def test_something(mock_modules):
            mock_modules(['runner.runner_core', 'intent_parser.intent_parser'])
    """
    from unittest.mock import MagicMock

    def _mock_modules(module_names):
        for name in module_names:
            monkeypatch.setitem(sys.modules, name, MagicMock(name=f"mock_{name}"))

    return _mock_modules


# ============ Fixtures from generator/runner/tests/conftest.py ============

@pytest.fixture(scope="session")
def runner_event_loop():
    """
    Session-scoped event loop for runner tests.
    Provides a clean event loop for cleaner async finalizers.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    yield loop

    try:
        loop.close()
    except Exception:
        pass


# ============ Fixtures from omnicore_engine/database/tests/conftest.py ============

@pytest.fixture(autouse=True)
def clear_sqlalchemy_metadata(request):
    """Clear SQLAlchemy metadata before each test to avoid table redefinition errors.
    
    Note: This is skipped for database tests that need the metadata to create tables.
    TODO: Consider using pytest markers (@pytest.mark.database) instead of path/fixture matching
    for more robust and maintainable test detection.
    """
    # Skip clearing for database tests that need the metadata to create tables
    if "database" in request.fixturenames or "test_database" in str(request.fspath):
        yield
        return
        
    try:
        from omnicore_engine.database import models
        if hasattr(models, "Base") and hasattr(models.Base, "metadata"):
            models.Base.metadata.clear()
    except ImportError:
        pass

    yield

    try:
        from omnicore_engine.database import models
        if hasattr(models, "Base") and hasattr(models.Base, "metadata"):
            models.Base.metadata.clear()
    except ImportError:
        pass


# ============ Fixtures from omnicore_engine/tests/conftest.py ============

@pytest.fixture(scope="function")
def temp_db_path(tmp_path):
    """Provide a temporary database path for tests."""
    return tmp_path / "test.db"


# ============ Fixtures from arbiter/bug_manager/tests/conftest.py ============

def _bug_manager_setup_logging():
    """Configure logging to write to a file to avoid I/O errors on closed streams."""
    import logging
    logger = logging.getLogger()
    logger.handlers = []
    handler = logging.FileHandler("test.log", mode="w")
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============ Fixtures from arbiter/conftest.py ============

# Variables for plugin registry isolation
_ARBITER_TEST_TEMP_DIR = None
_ARBITER_TEST_PLUGIN_FILE = None


@pytest.fixture(scope="session", autouse=True)
def isolate_arbiter_plugin_registry():
    """
    Isolate the arbiter plugin registry for testing to prevent persistence conflicts.
    Creates temporary directory INSIDE fixture, not at module level.
    """
    global _ARBITER_TEST_TEMP_DIR, _ARBITER_TEST_PLUGIN_FILE
    import tempfile
    import shutil
    import logging
    
    logger = logging.getLogger(__name__)
    
    _ARBITER_TEST_TEMP_DIR = tempfile.mkdtemp(prefix="arbiter_test_")
    _ARBITER_TEST_PLUGIN_FILE = os.path.join(_ARBITER_TEST_TEMP_DIR, "test_plugins.json")
    
    try:
        import arbiter.arbiter_plugin_registry as registry_module
        if hasattr(registry_module, "PluginRegistry"):
            if registry_module.PluginRegistry._instance:
                registry_module.PluginRegistry._instance._persist_path = _ARBITER_TEST_PLUGIN_FILE
    except ImportError:
        pass
    
    logger.info(f"Arbiter plugin registry isolated to: {_ARBITER_TEST_PLUGIN_FILE}")
    
    yield
    
    try:
        if _ARBITER_TEST_TEMP_DIR and os.path.exists(_ARBITER_TEST_TEMP_DIR):
            shutil.rmtree(_ARBITER_TEST_TEMP_DIR, ignore_errors=True)
    except Exception as e:
        logger.warning(f"Cleanup error (non-critical): {e}")


@pytest.fixture
def mock_plugin_registry(monkeypatch):
    """
    Provides a mock plugin registry for tests that need complete isolation.
    Use this fixture when you want to prevent any plugin registration.
    """
    from unittest.mock import MagicMock

    mock_registry = MagicMock()
    mock_registry.get_metadata.return_value = None
    mock_registry.register.return_value = lambda x: x
    mock_registry.register_instance.return_value = None

    try:
        monkeypatch.setattr("arbiter.arbiter_plugin_registry.registry", mock_registry)
        monkeypatch.setattr("arbiter.arbiter_plugin_registry.PLUGIN_REGISTRY", {})

        def mock_register(kind, name, version, author):
            def decorator(func):
                return func
            return decorator

        monkeypatch.setattr("arbiter.arbiter_plugin_registry.register", mock_register)
    except Exception:
        pass

    return mock_registry


@pytest.fixture
def sample_decision_context():
    """Provides a sample decision context for testing arbiter decisions."""
    return {
        "decision_id": "test_decision_123",
        "action": "deploy_model",
        "risk_level": "high",
        "details": {
            "model_name": "test_model",
            "environment": "production",
            "confidence": 0.95,
        },
    }


@pytest.fixture
def sample_feedback():
    """Provides sample feedback data for testing arbiter feedback."""
    return {
        "decision_id": "test_decision_123",
        "approved": True,
        "user_id": "test_user",
        "comment": "Looks good for deployment",
        "timestamp": "2024-01-01T00:00:00Z",
        "signature": "test_signature",
    }


@pytest.fixture
def mock_opentelemetry(monkeypatch):
    """Provides a mock OpenTelemetry setup for tests that need it."""

    class MockSpan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_attribute(self, key, value):
            pass

        def set_status(self, status):
            pass

        def get_span_context(self):
            class MockSpanContext:
                def __init__(self):
                    self.trace_id = 0
                    self.span_id = 0
                    self.is_remote = False
                    self.trace_flags = 0
                    self.trace_state = None
                    self.is_valid = False
            return MockSpanContext()

    class MockTracer:
        def start_as_current_span(self, name, **kwargs):
            return MockSpan()

        def start_span(self, name, **kwargs):
            return MockSpan()

    class MockTrace:
        @staticmethod
        def get_tracer(name):
            return MockTracer()

    try:
        monkeypatch.setattr("opentelemetry.trace.get_tracer", lambda x: MockTracer())
    except Exception:
        pass

    return MockTrace()


@pytest.fixture(autouse=True)
def cleanup_arbiter_test_files():
    """Clean up any arbiter test-generated files after each test."""
    yield
    import logging
    logger = logging.getLogger(__name__)
    
    test_files = [
        "test_feedback.db",
        "feedback.db",
        "feedback_log.json",
        "test_plugins.json",
        "omnicore.db",
        "arbiter_knowledge.db",
        "arrays.json",
        "arrays.db",
        "test_arrays.json",
    ]
    for file in test_files:
        if os.path.exists(file):
            try:
                os.remove(file)
            except Exception as e:
                logger.debug(f"Could not remove {file}: {e}")


@pytest.fixture(scope="session", autouse=True)
def arbiter_session_cleanup():
    """Final cleanup after all arbiter tests complete."""
    yield
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info("Running final arbiter session cleanup")

    for db_file in Path(".").glob("*.db"):
        if "test" in db_file.name.lower() or db_file.name in [
            "feedback.db",
            "omnicore.db",
            "arrays.db",
        ]:
            try:
                db_file.unlink()
            except:
                pass

    for json_file in Path(".").glob("*test*.json"):
        try:
            json_file.unlink()
        except:
            pass

    if Path("arrays.json").exists():
        try:
            Path("arrays.json").unlink()
        except:
            pass


# ============ Fixtures from arbiter/explainable_reasoner/tests/conftest.py ============

@pytest.fixture(autouse=True)
def isolated_reasoner_metrics():
    """
    Ensures every test runs with a clean Prometheus registry and metrics dictionary.
    This prevents state from leaking between tests.
    """
    from unittest.mock import MagicMock, patch
    
    try:
        from prometheus_client import CollectorRegistry
    except ImportError:
        yield None, None
        return
    
    try:
        from arbiter.explainable_reasoner.metrics import initialize_metrics
    except ImportError:
        yield None, None
        return
    
    mock_metric = MagicMock()
    mock_metric.labels.return_value = MagicMock(
        inc=MagicMock(), dec=MagicMock(), set=MagicMock(), observe=MagicMock()
    )

    mock_metrics_dict = {
        "reasoner_history_operations_total": mock_metric,
        "reasoner_history_operation_latency_seconds": mock_metric,
        "reasoner_history_db_connection_failures_total": mock_metric,
        "reasoner_history_pruned_entries_total": mock_metric,
        "reasoner_history_entries_current": mock_metric,
        "reasoner_requests_total": mock_metric,
        "reasoner_inference_success": mock_metric,
        "reasoner_inference_errors": mock_metric,
        "reasoner_prompt_truncations": mock_metric,
        "reasoner_cache_hits": mock_metric,
        "reasoner_cache_misses": mock_metric,
        "reasoner_cache_errors": mock_metric,
        "reasoner_model_reload_attempts": mock_metric,
        "reasoner_model_reload_success": mock_metric,
        "reasoner_model_load_errors": mock_metric,
        "reasoner_health_check_success": mock_metric,
        "reasoner_health_check_errors": mock_metric,
        "reasoner_instances": mock_metric,
        "reasoner_shutdown_duration_seconds": mock_metric,
        "reasoner_prompt_size_bytes": mock_metric,
        "reasoner_inference_duration_seconds": mock_metric,
        "reasoner_history_entries_used": mock_metric,
        "reasoner_sensitive_data_redaction_total": mock_metric,
        "reasoner_executor_restarts_total": mock_metric,
        "reasoner_executor_queue_size": mock_metric,
        "reasoner_model_load_success": mock_metric,
        "reasoner_model_unload_total": mock_metric,
        "reasoner_init_duration_seconds": mock_metric,
        "prompt_size_bytes": mock_metric,
        "inference_duration_seconds": mock_metric,
    }

    try:
        with (
            patch(
                "arbiter.explainable_reasoner.metrics.METRICS_REGISTRY",
                new=CollectorRegistry(),
            ) as registry,
            patch(
                "arbiter.explainable_reasoner.metrics.METRICS", new=mock_metrics_dict
            ) as metrics_dict,
        ):
            initialize_metrics()
            yield registry, metrics_dict
    except Exception:
        yield None, None


# ============ Fixtures from arbiter/knowledge_graph/tests/conftest.py ============

@pytest.fixture(autouse=True)
def mock_knowledge_graph_agent_metrics(monkeypatch):
    """Automatically mock AGENT_METRICS for knowledge graph tests."""
    from unittest.mock import MagicMock
    
    mock_metrics = {
        "agent_predict_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "agent_predict_success": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "agent_predict_errors": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "agent_predict_duration_seconds": MagicMock(labels=MagicMock(return_value=MagicMock(observe=MagicMock()))),
        "agent_step_duration_seconds": MagicMock(labels=MagicMock(return_value=MagicMock(observe=MagicMock()))),
        "agent_team_task_duration_seconds": MagicMock(observe=MagicMock()),
        "agent_team_task_errors_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "agent_creation_duration_seconds": MagicMock(observe=MagicMock()),
        "llm_calls_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "llm_errors_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "llm_call_latency_seconds": MagicMock(labels=MagicMock(return_value=MagicMock(observe=MagicMock()))),
        "state_backend_operations_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "state_backend_errors_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "state_backend_latency_seconds": MagicMock(labels=MagicMock(return_value=MagicMock(observe=MagicMock()))),
        "meta_learning_corrections_logged_total": MagicMock(inc=MagicMock()),
        "meta_learning_train_duration_seconds": MagicMock(observe=MagicMock()),
        "meta_learning_train_errors_total": MagicMock(inc=MagicMock()),
        "sensitive_data_redaction_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "multimodal_data_processed_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "mm_processor_failures_total": MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "agent_last_success_timestamp": MagicMock(labels=MagicMock(return_value=MagicMock(set=MagicMock()))),
        "agent_last_error_timestamp": MagicMock(labels=MagicMock(return_value=MagicMock(set=MagicMock()))),
        "agent_active_sessions_current": MagicMock(inc=MagicMock(), dec=MagicMock()),
        "agent_heartbeat_timestamp": MagicMock(labels=MagicMock(return_value=MagicMock(set=MagicMock()))),
    }
    
    try:
        monkeypatch.setattr("arbiter.knowledge_graph.core.AGENT_METRICS", mock_metrics)
        monkeypatch.setattr("arbiter.knowledge_graph.utils.AGENT_METRICS", mock_metrics)
    except Exception:
        pass
    
    return mock_metrics


@pytest.fixture(autouse=True)
def mock_meta_learning_persistence(monkeypatch):
    """Mock file operations for MetaLearning to prevent loading persisted data."""
    import builtins

    original_open = builtins.open

    def mock_open_wrapper(*args, **kwargs):
        if args and "meta_learning.pkl" in str(args[0]):
            if "rb" in str(args[1] if len(args) > 1 else kwargs.get("mode", "r")):
                raise FileNotFoundError("No persisted meta-learning data")
        return original_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open_wrapper)


@pytest.fixture
def mock_knowledge_graph_config(monkeypatch):
    """Mock Config for knowledge graph tests."""
    try:
        from arbiter.knowledge_graph import config
        
        monkeypatch.setattr(config.Config, "REDIS_URL", None)
        monkeypatch.setattr(config.Config, "POSTGRES_DB_URL", None)
        monkeypatch.setattr(config.Config, "MAX_MM_DATA_SIZE_MB", 100)
        monkeypatch.setattr(config.Config, "CACHE_EXPIRATION_SECONDS", 3600)
        monkeypatch.setattr(config.Config, "PII_SENSITIVE_KEYS", ["password", "email", "ssn"])
        monkeypatch.setattr(config.Config, "GDPR_MODE", True)
        monkeypatch.setattr(config.Config, "DEFAULT_PROVIDER", "openai")
        monkeypatch.setattr(config.Config, "DEFAULT_LLM_MODEL", "gpt-3.5-turbo")
        monkeypatch.setattr(config.Config, "DEFAULT_TEMP", 0.7)
        monkeypatch.setattr(config.Config, "DEFAULT_LANGUAGE", "en")
        monkeypatch.setattr(config.Config, "MEMORY_WINDOW", 5)
        monkeypatch.setattr(config.Config, "MAX_META_LEARNING_CORRECTIONS", 10)
        monkeypatch.setattr(config.Config, "MAX_CORRECTION_ENTRY_SIZE", 10000)
        monkeypatch.setattr(config.Config, "MIN_RECORDS_FOR_TRAINING", 2)
        monkeypatch.setattr(config.Config, "LLM_RATE_LIMIT_CALLS", 10)
        monkeypatch.setattr(config.Config, "LLM_RATE_LIMIT_PERIOD", 60)
        monkeypatch.setattr(config.Config, "FALLBACK_PROVIDER", None)
        monkeypatch.setattr(config.Config, "FALLBACK_LLM_CONFIG", {"model": "claude-2", "temperature": 0.7})
        monkeypatch.setattr(config.Config, "AUDIT_LEDGER_URL", "http://localhost:8000/audit")
        monkeypatch.setattr(config.Config, "AUDIT_SIGNING_PUBLIC_KEY", None)
        
        return config.Config
    except ImportError:
        return None


@pytest.fixture
def mock_knowledge_graph_external_clients(monkeypatch):
    """Mock external client classes for knowledge graph tests."""
    from unittest.mock import MagicMock, AsyncMock
    
    mock_redis_client = MagicMock()
    mock_redis_instance = AsyncMock()
    mock_redis_instance.ping = AsyncMock(return_value=True)
    mock_redis_instance.get = AsyncMock(return_value=None)
    mock_redis_instance.set = AsyncMock(return_value=True)
    mock_redis_instance.setex = AsyncMock(return_value=True)
    mock_redis_client.return_value = mock_redis_instance

    mock_postgres_client = MagicMock()
    mock_postgres_instance = AsyncMock()
    mock_postgres_instance.connect = AsyncMock()
    mock_postgres_instance.save = AsyncMock()
    mock_postgres_instance.load = AsyncMock(return_value=None)
    mock_postgres_client.return_value = mock_postgres_instance

    mock_audit_client = MagicMock()
    mock_audit_instance = AsyncMock()
    mock_audit_instance.log_event = AsyncMock(return_value=True)
    mock_audit_client.return_value = mock_audit_instance

    try:
        monkeypatch.setattr("arbiter.knowledge_graph.core.RedisClient", mock_redis_client)
        monkeypatch.setattr("arbiter.knowledge_graph.core.PostgresClient", mock_postgres_client)
        monkeypatch.setattr("arbiter.knowledge_graph.core.AuditLedgerClient", mock_audit_client)
    except Exception:
        pass

    return {
        "redis": mock_redis_client,
        "postgres": mock_postgres_client,
        "audit": mock_audit_client,
    }


@pytest.fixture
def mock_knowledge_graph_llm_providers(monkeypatch):
    """Mock LLM provider classes for knowledge graph tests."""
    from unittest.mock import MagicMock

    mock_openai = MagicMock()
    mock_openai.return_value = MagicMock()

    mock_anthropic = MagicMock()
    mock_anthropic.return_value = MagicMock()

    mock_google = MagicMock()
    mock_google.return_value = MagicMock()

    mock_xai = MagicMock()
    mock_xai.return_value = MagicMock()

    try:
        monkeypatch.setattr("arbiter.knowledge_graph.core.ChatOpenAI", mock_openai)
        monkeypatch.setattr("arbiter.knowledge_graph.core.ChatAnthropic", mock_anthropic)
        monkeypatch.setattr("arbiter.knowledge_graph.core.ChatGoogleGenerativeAI", mock_google)
        monkeypatch.setattr("arbiter.knowledge_graph.core.ChatXAI", mock_xai)
    except Exception:
        pass

    return {
        "openai": mock_openai,
        "anthropic": mock_anthropic,
        "google": mock_google,
        "xai": mock_xai,
    }


# ============ Fixtures from arbiter/learner/tests/conftest.py ============

# Mock botocore exceptions for learner tests
class MockNoCredentialsError(Exception):
    """Mock AWS NoCredentialsError exception."""
    pass


class MockClientError(Exception):
    """Mock AWS ClientError exception."""
    pass


@pytest.fixture(autouse=True)
def mock_learner_aws_ssm(monkeypatch):
    """Mock AWS SSM client for learner tests to prevent real AWS API calls."""
    from unittest.mock import MagicMock, patch
    
    with patch("boto3.client") as mock_client:
        mock_ssm = MagicMock()
        mock_ssm.get_parameter.return_value = {
            "Parameter": {
                "Value": "dGVzdC1rZXktZm9yLXB5dGVzdC0zMi1ieXRlczEyMzQ="  # base64 32-byte key
            }
        }
        mock_client.return_value = mock_ssm
        yield mock_client


# ============ Fixtures from arbiter/policy/tests/conftest.py ============

@pytest.fixture(autouse=True)
def reset_policy_singletons():
    """Reset singleton instances between tests to ensure test isolation."""
    yield
    
    try:
        from arbiter.policy import core
        if hasattr(core, "_policy_engine_instance"):
            core._policy_engine_instance = None
    except ImportError:
        pass
    
    try:
        from arbiter.policy import config
        if hasattr(config, "_instance"):
            config._instance = None
    except ImportError:
        pass
    
    try:
        from arbiter.policy import circuit_breaker
        if hasattr(circuit_breaker, "_breaker_states"):
            circuit_breaker._breaker_states.clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def mock_policy_arbiter_dependencies(monkeypatch):
    """Mock external dependencies for policy tests."""
    from unittest.mock import MagicMock

    mock_audit_log = MagicMock()

    mock_compliance = MagicMock()
    mock_compliance.load_compliance_map = lambda config_path=None: {
        "FAKE-1": {"name": "FakeControl", "status": "enforced", "required": True},
        "FAKE-2": {"name": "FakeOptional", "status": "logged", "required": False},
        "PC-1": {"name": "PolicyControl", "status": "enforced", "required": True},
        "NIST_AC-1": {"name": "Access Control Policy", "status": "enforced", "required": True},
        "NIST_AC-2": {"name": "Account Management", "status": "enforced", "required": True},
        "NIST_AC-3": {"name": "Access Enforcement", "status": "enforced", "required": True},
        "NIST_AC-6": {"name": "Least Privilege", "status": "enforced", "required": True},
    }

    mock_llm_client = MagicMock()

    monkeypatch.setitem(sys.modules, "arbiter.policy.guardrails.audit_log", mock_audit_log)
    monkeypatch.setitem(sys.modules, "arbiter.policy.guardrails.compliance_mapper", mock_compliance)
    monkeypatch.setitem(sys.modules, "arbiter.policy.plugins.llm_client", mock_llm_client)

    return {
        "audit_log": mock_audit_log,
        "compliance_mapper": mock_compliance,
        "llm_client": mock_llm_client,
    }


@pytest.fixture
def mock_policy_redis(monkeypatch):
    """Mock Redis for policy tests that don't need actual Redis."""
    from unittest.mock import MagicMock

    mock_redis_client = MagicMock()
    mock_redis_client.ping = MagicMock(return_value=True)
    mock_redis_client.hgetall = MagicMock(return_value={})
    mock_redis_client.hset = MagicMock(return_value=True)
    mock_redis_client.expire = MagicMock(return_value=True)
    mock_redis_client.pipeline = MagicMock()
    mock_redis_client.close = MagicMock()

    mock_redis_module = MagicMock()
    mock_redis_module.Redis.from_url = MagicMock(return_value=mock_redis_client)
    mock_redis_module.ConnectionPool.from_url = MagicMock()
    mock_redis_module.RedisError = Exception

    monkeypatch.setattr("redis.asyncio", mock_redis_module)
    return mock_redis_client


@pytest.fixture
def clean_policy_environment(monkeypatch):
    """Provide a clean environment for policy tests."""
    original_env = os.environ.copy()

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("APP_ENV", "test")

    yield

    os.environ.clear()
    os.environ.update(original_env)


# ============ Fixtures from self_fixing_engineer/conftest.py ============

# Deferred imports for OpenTelemetry
_SFE_OTEL_AVAILABLE = None
_SFE_OTEL_IMPORTS = {}


def _check_sfe_otel_availability():
    """Check if OpenTelemetry is available (deferred to fixture time)."""
    global _SFE_OTEL_AVAILABLE, _SFE_OTEL_IMPORTS
    
    if _SFE_OTEL_AVAILABLE is not None:
        return _SFE_OTEL_AVAILABLE
    
    import threading
    if not hasattr(_check_sfe_otel_availability, '_lock'):
        _check_sfe_otel_availability._lock = threading.Lock()
    
    with _check_sfe_otel_availability._lock:
        if _SFE_OTEL_AVAILABLE is not None:
            return _SFE_OTEL_AVAILABLE
        
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
            
            _SFE_OTEL_IMPORTS['trace'] = trace
            _SFE_OTEL_IMPORTS['TracerProvider'] = TracerProvider
            _SFE_OTEL_IMPORTS['ConsoleSpanExporter'] = ConsoleSpanExporter
            _SFE_OTEL_IMPORTS['SimpleSpanProcessor'] = SimpleSpanProcessor
            _SFE_OTEL_AVAILABLE = True
        except ImportError:
            _SFE_OTEL_AVAILABLE = False
        
        return _SFE_OTEL_AVAILABLE


@pytest.fixture(scope="session", autouse=True)
def setup_sfe_otel():
    """
    Initializes a minimal OpenTelemetry SDK for the SFE test session.
    Runs AFTER test collection to avoid slow imports.
    """
    if _check_sfe_otel_availability():
        trace = _SFE_OTEL_IMPORTS['trace']
        TracerProvider = _SFE_OTEL_IMPORTS['TracerProvider']
        ConsoleSpanExporter = _SFE_OTEL_IMPORTS['ConsoleSpanExporter']
        SimpleSpanProcessor = _SFE_OTEL_IMPORTS['SimpleSpanProcessor']
        
        try:
            provider = TracerProvider()
            exporter = ConsoleSpanExporter()
            processor = SimpleSpanProcessor(exporter)
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)
        except RuntimeError as e:
            # Skip OTEL setup if thread creation fails (e.g., in pytest-asyncio test context)
            if "can't start new thread" in str(e):
                pass
            else:
                raise

    yield


# ============ Fixtures from intent_capture/tests/conftest.py ============

@pytest.fixture(scope="session", autouse=True)
def setup_intent_capture_logging_and_warnings():
    """Configure logging and warning filters for intent_capture tests."""
    import logging
    import warnings
    
    logging.basicConfig(level=logging.ERROR, force=True)
    logging.getLogger("streamlit").setLevel(logging.ERROR)
    logging.getLogger("intent_capture").setLevel(logging.ERROR)
    
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=".*pkg_resources.*")
    warnings.filterwarnings("ignore", message=".*missing ScriptRunContext.*")
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    
    yield
    
    try:
        logging.shutdown()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def mock_streamlit_setup():
    """Mock Streamlit session state globally for intent_capture tests."""
    import unittest.mock as mock
    
    mock_session_state = mock.MagicMock()
    mock_session_state.get.return_value = "test_user"
    
    with mock.patch.dict(sys.modules, {"streamlit": mock.MagicMock()}):
        sys.modules["streamlit"].session_state = mock_session_state
        yield


@pytest.fixture(autouse=True)
def mock_streamlit_for_tests(mock_streamlit_setup):
    """Mock Streamlit components that cause issues in tests."""
    import unittest.mock as mock
    
    mock_session_state = mock.MagicMock()
    mock_session_state.get.return_value = "test_user"
    
    with mock.patch("streamlit.session_state", mock_session_state):
        with mock.patch(
            "streamlit.runtime.scriptrunner_utils.script_run_context.get_script_run_ctx",
            return_value=None,
        ):
            yield


@pytest.fixture(autouse=True)
def cleanup_intent_capture_logging():
    """Ensure logging doesn't cause issues in intent_capture tests."""
    import logging
    yield
    for handler in logging.root.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        logging.root.removeHandler(handler)


# ============================================================================
# END OF CONSOLIDATED FIXTURES
# ============================================================================
