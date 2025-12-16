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
    import importlib.util
    
    # Create a mock class that can be used as decorator or callable
    class MockCallable:
        """
        A versatile mock object that supports multiple usage patterns:
        - As a decorator: @mock.method(args)
        - As a callable: mock.function()
        - As an attribute chain: mock.sub.module.attr
        - As a context manager: with mock.context():
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
    if name == 'dotenv':
        # dotenv needs load_dotenv and find_dotenv functions
        mock_module.load_dotenv = lambda *args, **kwargs: None
        mock_module.find_dotenv = lambda *args, **kwargs: None
    elif name == 'dynaconf':
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
    elif name == 'torch':
        # torch needs __version__ as a string (not MockCallable) to prevent errors
        # in packaging.version.Version() calls (e.g., from safetensors.torch)
        mock_module.__version__ = "2.9.1"
    elif name == 'transformers':
        # transformers also needs __version__ as a string
        mock_module.__version__ = "4.30.0"
    elif name == 'sentence_transformers':
        # sentence_transformers also needs __version__ as a string
        mock_module.__version__ = "2.2.0"
    
    return mock_module

# Only mock if genuinely missing (not if already imported)
_OPTIONAL_DEPENDENCIES = [
    'tiktoken',  # Often missing, used by LLM clients
    'aiofiles',  # Required by generator.main.api
    'aiofiles.os',  # Required by test_generation modules
    'backoff',  # Required by generator.main.api
    'fastapi',  # Required by generator.main.api
    'fastapi.security',  # Required by generator.main.api
    'fastapi.testclient',  # Required by test files
    'uvicorn',  # Required by generator.main
    'jwt',  # Required by generator.main.api
    'sqlalchemy',  # Required by many modules
    'redis',  # Required by various modules
    'redis.asyncio',  # Required by generator.main.api
    'dotenv',  # Required by many modules
    'dynaconf',  # Required by runner modules
    'anthropic',  # Required by arbiter.plugins
    'google.generativeai',  # Required by arbiter.plugins
    'google.api_core',  # Required by arbiter.plugins
    'google.api_core.exceptions',  # Required by arbiter.plugins
    'openai',  # Required by LLM providers
    'neo4j',  # Required by knowledge_graph
    'chromadb',  # Required by knowledge_graph
    'chromadb.utils',  # Required by testgen_agent
    'httpx',  # Required by explainable_reasoner
    'freezegun',  # Required by test files
    'torch',  # PyTorch - causes DLL errors on Windows
    'sentence_transformers',  # Uses torch, causes DLL errors
    'transformers',  # Uses torch, causes DLL errors
    'spacy',  # Uses torch via thinc, causes DLL errors
    'presidio_analyzer',  # Uses spacy, causes DLL errors
    'presidio_anonymizer',  # Uses spacy, causes DLL errors
    'networkx',  # Graph library
    'defusedxml',  # XML parsing
    'defusedxml.ElementTree',  # XML parsing - required by test_generation
    'beautifulsoup4',  # HTML parsing
    'bs4',  # BeautifulSoup alias
    'portalocker',  # File locking - required by bug_manager
    'structlog',  # Structured logging - required by explainable_reasoner
    'circuitbreaker',  # Circuit breaker pattern - required by arbiter modules
    'gnosis',  # Gnosis safe - required by audit_ledger_client
    'sentry_sdk',  # Sentry error tracking
    'asyncpg',  # Async PostgreSQL - required by postgres_client
    'web3',  # Web3.py - Ethereum library
    'feast',  # Feature store
    'ray',  # Distributed computing
    'scipy',  # Scientific computing
    'great_expectations',  # Data validation
    'merklelib',  # Merkle tree library
    'gymnasium',  # Reinforcement learning environments
    'deap',  # Evolutionary algorithms
    'langchain_openai',  # LangChain OpenAI integration
    'cerberus',  # Schema validation - required by policy module
    # Note: prometheus_client, aiohttp, pydantic, tenacity, and aiosqlite should be installed
    # and should NOT be mocked as they are critical for proper type checking
]

for dep in _OPTIONAL_DEPENDENCIES:
    if dep not in sys.modules:
        try:
            __import__(dep)
        except (ImportError, OSError, AttributeError):
            # Create a more sophisticated mock that handles submodule access
            # OSError is caught to handle DLL initialization failures on Windows (e.g., torch)
            # AttributeError is caught to handle bugs in packages like gnosis-py that use deprecated Python 2 syntax (e.g., string.join)
            mock_module = _create_mock_module(dep)
            sys.modules[dep] = mock_module
            
            # For packages that are commonly accessed as submodules, create parent stubs
            if '.' in dep:
                parts = dep.split('.')
                for i in range(1, len(parts)):
                    parent_name = '.'.join(parts[:i])
                    if parent_name not in sys.modules:
                        parent_mock = _create_mock_module(parent_name)
                        sys.modules[parent_name] = parent_mock

# ---- OpenTelemetry stub setup ----
# OpenTelemetry requires special handling because it has specific methods that must exist
# and be callable, not just module stubs
if 'opentelemetry' not in sys.modules:
    try:
        __import__('opentelemetry')
    except ImportError:
        # Create a complete OpenTelemetry stub with all required attributes
        import types
        
        # Create a no-op tracer
        class _NoOpTracer:
            def start_as_current_span(self, name, **kwargs):
                from contextlib import nullcontext
                return nullcontext()
        
        # Create a no-op span
        class _NoOpSpan:
            def set_attribute(self, *args, **kwargs):
                pass
            def add_event(self, *args, **kwargs):
                pass
            def set_status(self, *args, **kwargs):
                pass
            def record_exception(self, *args, **kwargs):
                pass
        
        # Create trace module with all required methods
        trace_module = types.ModuleType('opentelemetry.trace')
        trace_module.__file__ = '<mocked opentelemetry.trace>'
        trace_module.get_tracer = lambda *args, **kwargs: _NoOpTracer()
        trace_module.get_current_span = lambda: _NoOpSpan()
        trace_module.get_tracer_provider = lambda: None
        
        # Create main opentelemetry module
        import importlib.util
        otel_module = types.ModuleType('opentelemetry')
        otel_module.__file__ = '<mocked opentelemetry>'
        otel_module.__path__ = []  # Make it a package
        otel_module.__spec__ = importlib.util.spec_from_loader('opentelemetry', loader=None)
        otel_module.trace = trace_module
        
        # Create instrumentation module
        instrumentation_module = types.ModuleType('opentelemetry.instrumentation')
        instrumentation_module.__file__ = '<mocked opentelemetry.instrumentation>'
        instrumentation_module.__path__ = []  # This is required for submodule imports
        instrumentation_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation', loader=None)
        otel_module.instrumentation = instrumentation_module
        
        # Create common instrumentation submodules
        instrumentation_fastapi = types.ModuleType('opentelemetry.instrumentation.fastapi')
        instrumentation_fastapi.__file__ = '<mocked opentelemetry.instrumentation.fastapi>'
        instrumentation_fastapi.__path__ = []
        instrumentation_fastapi.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.fastapi', loader=None)
        
        # FastAPIInstrumentor must be a proper class with instrument_app method
        class FastAPIInstrumentor:
            @classmethod
            def instrument_app(cls, *args, **kwargs):
                pass
        
        instrumentation_fastapi.FastAPIInstrumentor = FastAPIInstrumentor
        
        # Create grpc instrumentation module
        instrumentation_grpc = types.ModuleType('opentelemetry.instrumentation.grpc')
        instrumentation_grpc.__file__ = '<mocked opentelemetry.instrumentation.grpc>'
        instrumentation_grpc.__path__ = []
        instrumentation_grpc.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.grpc', loader=None)
        
        # GrpcAioInstrumentor must be a proper class
        class GrpcAioInstrumentor:
            @classmethod
            def instrument(cls, *args, **kwargs):
                pass
        
        instrumentation_grpc.GrpcAioInstrumentor = GrpcAioInstrumentor
        
        # Create instrumentation.utils module (required by instrumentation._semconv)
        class _MockCallable:
            """Mock callable for module attributes."""
            def __call__(self, *args, **kwargs):
                return self
            def __getattr__(self, attr):
                return _MockCallable()
        
        instrumentation_utils = types.ModuleType('opentelemetry.instrumentation.utils')
        instrumentation_utils.__file__ = '<mocked opentelemetry.instrumentation.utils>'
        instrumentation_utils.__path__ = []
        instrumentation_utils.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.utils', loader=None)
        instrumentation_utils.http_status_to_status_code = lambda *args, **kwargs: None
        instrumentation_utils.__getattr__ = lambda attr: _MockCallable()
        
        # Create instrumentation._semconv module (required by instrumentation.fastapi)
        instrumentation_semconv = types.ModuleType('opentelemetry.instrumentation._semconv')
        instrumentation_semconv.__file__ = '<mocked opentelemetry.instrumentation._semconv>'
        instrumentation_semconv.__path__ = []
        instrumentation_semconv.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation._semconv', loader=None)
        instrumentation_semconv.__getattr__ = lambda attr: _MockCallable()
        
        instrumentation_logging = types.ModuleType('opentelemetry.instrumentation.logging')
        instrumentation_logging.__file__ = '<mocked opentelemetry.instrumentation.logging>'
        instrumentation_logging.__path__ = []
        instrumentation_logging.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.logging', loader=None)
        instrumentation_logging.LoggingInstrumentor = lambda *args, **kwargs: None
        
        instrumentation_requests = types.ModuleType('opentelemetry.instrumentation.requests')
        instrumentation_requests.__file__ = '<mocked opentelemetry.instrumentation.requests>'
        instrumentation_requests.__path__ = []
        instrumentation_requests.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.requests', loader=None)
        instrumentation_requests.RequestsInstrumentor = lambda *args, **kwargs: None
        
        instrumentation_system_metrics = types.ModuleType('opentelemetry.instrumentation.system_metrics')
        instrumentation_system_metrics.__file__ = '<mocked opentelemetry.instrumentation.system_metrics>'
        instrumentation_system_metrics.__path__ = []
        instrumentation_system_metrics.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.system_metrics', loader=None)
        instrumentation_system_metrics.SystemMetricsInstrumentor = lambda *args, **kwargs: None
        
        # Create sdk modules
        sdk_module = types.ModuleType('opentelemetry.sdk')
        sdk_module.__file__ = '<mocked opentelemetry.sdk>'
        sdk_module.__path__ = []  # Parent module for submodules
        otel_module.sdk = sdk_module
        
        sdk_trace_module = types.ModuleType('opentelemetry.sdk.trace')
        sdk_trace_module.__file__ = '<mocked opentelemetry.sdk.trace>'
        sdk_trace_module.__path__ = []  # Parent module for submodules
        sdk_trace_module.TracerProvider = lambda *args, **kwargs: None
        sdk_module.trace = sdk_trace_module
        
        sdk_trace_export_module = types.ModuleType('opentelemetry.sdk.trace.export')
        sdk_trace_export_module.__file__ = '<mocked opentelemetry.sdk.trace.export>'
        sdk_trace_export_module.__path__ = []  # Parent module for submodules
        sdk_trace_export_module.ConsoleSpanExporter = lambda *args, **kwargs: None
        sdk_trace_export_module.SimpleSpanProcessor = lambda *args, **kwargs: None
        sdk_trace_export_module.BatchSpanProcessor = lambda *args, **kwargs: None
        sdk_trace_module.export = sdk_trace_export_module
        
        # Create in_memory_span_exporter submodule
        in_memory_exporter = types.ModuleType('opentelemetry.sdk.trace.export.in_memory_span_exporter')
        in_memory_exporter.__file__ = '<mocked opentelemetry.sdk.trace.export.in_memory_span_exporter>'
        in_memory_exporter.InMemorySpanExporter = lambda *args, **kwargs: None
        sdk_trace_export_module.in_memory_span_exporter = in_memory_exporter
        
        sdk_resources_module = types.ModuleType('opentelemetry.sdk.resources')
        sdk_resources_module.__file__ = '<mocked opentelemetry.sdk.resources>'
        sdk_resources_module.Resource = lambda **kwargs: None
        sdk_module.resources = sdk_resources_module
        
        # Create additional OpenTelemetry modules used in the codebase
        
        # trace.status module
        trace_status_module = types.ModuleType('opentelemetry.trace.status')
        trace_status_module.__file__ = '<mocked opentelemetry.trace.status>'
        
        # Create Status and StatusCode classes that can be used in the codebase
        class _MockStatus:
            def __init__(self, *args, **kwargs):
                pass
        
        class _MockStatusCode:
            ERROR = "ERROR"
            OK = "OK"
            UNSET = "UNSET"
        
        trace_status_module.Status = _MockStatus
        trace_status_module.StatusCode = _MockStatusCode
        
        # Also add Status and StatusCode directly to trace module for imports like: from opentelemetry.trace import Status
        trace_module.Status = _MockStatus
        trace_module.StatusCode = _MockStatusCode
        trace_module.status = trace_status_module
        
        # trace.propagation module
        trace_propagation_module = types.ModuleType('opentelemetry.trace.propagation')
        trace_propagation_module.__file__ = '<mocked opentelemetry.trace.propagation>'
        trace_propagation_module.__path__ = []
        trace_module.propagation = trace_propagation_module
        
        trace_propagation_tracecontext = types.ModuleType('opentelemetry.trace.propagation.tracecontext')
        trace_propagation_tracecontext.__file__ = '<mocked opentelemetry.trace.propagation.tracecontext>'
        trace_propagation_tracecontext.TraceContextTextMapPropagator = lambda *args, **kwargs: None
        trace_propagation_module.tracecontext = trace_propagation_tracecontext
        
        # Create propagate module (required by instrumentation.utils)
        propagate_module = types.ModuleType('opentelemetry.propagate')
        propagate_module.__file__ = '<mocked opentelemetry.propagate>'
        propagate_module.__path__ = []
        propagate_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.propagate', loader=None)
        propagate_module.extract = lambda *args, **kwargs: {}
        propagate_module.inject = lambda *args, **kwargs: None
        propagate_module.get_global_textmap = lambda *args, **kwargs: None
        propagate_module.set_global_textmap = lambda *args, **kwargs: None
        otel_module.propagate = propagate_module
        
        # sdk.trace.sampling module
        sdk_trace_sampling_module = types.ModuleType('opentelemetry.sdk.trace.sampling')
        sdk_trace_sampling_module.__file__ = '<mocked opentelemetry.sdk.trace.sampling>'
        sdk_trace_sampling_module.ParentBased = lambda *args, **kwargs: None
        sdk_trace_sampling_module.TraceIdRatioBased = lambda *args, **kwargs: None
        sdk_trace_sampling_module.ALWAYS_ON = lambda *args, **kwargs: None
        sdk_trace_sampling_module.ALWAYS_OFF = lambda *args, **kwargs: None
        sdk_trace_module.sampling = sdk_trace_sampling_module
        
        # exporter modules
        exporter_module = types.ModuleType('opentelemetry.exporter')
        exporter_module.__file__ = '<mocked opentelemetry.exporter>'
        exporter_module.__path__ = []
        otel_module.exporter = exporter_module
        
        exporter_jaeger_module = types.ModuleType('opentelemetry.exporter.jaeger')
        exporter_jaeger_module.__file__ = '<mocked opentelemetry.exporter.jaeger>'
        exporter_jaeger_module.__path__ = []
        exporter_module.jaeger = exporter_jaeger_module
        
        exporter_jaeger_thrift_module = types.ModuleType('opentelemetry.exporter.jaeger.thrift')
        exporter_jaeger_thrift_module.__file__ = '<mocked opentelemetry.exporter.jaeger.thrift>'
        exporter_jaeger_thrift_module.JaegerExporter = lambda *args, **kwargs: None
        exporter_jaeger_module.thrift = exporter_jaeger_thrift_module
        
        exporter_otlp_module = types.ModuleType('opentelemetry.exporter.otlp')
        exporter_otlp_module.__file__ = '<mocked opentelemetry.exporter.otlp>'
        exporter_otlp_module.__path__ = []
        exporter_module.otlp = exporter_otlp_module
        
        exporter_otlp_proto_module = types.ModuleType('opentelemetry.exporter.otlp.proto')
        exporter_otlp_proto_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto>'
        exporter_otlp_proto_module.__path__ = []
        exporter_otlp_module.proto = exporter_otlp_proto_module
        
        exporter_otlp_proto_grpc_module = types.ModuleType('opentelemetry.exporter.otlp.proto.grpc')
        exporter_otlp_proto_grpc_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.grpc>'
        exporter_otlp_proto_grpc_module.__path__ = []
        exporter_otlp_proto_module.grpc = exporter_otlp_proto_grpc_module
        
        exporter_otlp_proto_grpc_trace_exporter_module = types.ModuleType('opentelemetry.exporter.otlp.proto.grpc.trace_exporter')
        exporter_otlp_proto_grpc_trace_exporter_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.grpc.trace_exporter>'
        exporter_otlp_proto_grpc_trace_exporter_module.OTLPSpanExporter = lambda *args, **kwargs: None
        exporter_otlp_proto_grpc_module.trace_exporter = exporter_otlp_proto_grpc_trace_exporter_module
        
        # Add HTTP exporter module
        exporter_otlp_proto_http_module = types.ModuleType('opentelemetry.exporter.otlp.proto.http')
        exporter_otlp_proto_http_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.http>'
        exporter_otlp_proto_http_module.__path__ = []
        exporter_otlp_proto_module.http = exporter_otlp_proto_http_module
        
        exporter_otlp_proto_http_trace_exporter_module = types.ModuleType('opentelemetry.exporter.otlp.proto.http.trace_exporter')
        exporter_otlp_proto_http_trace_exporter_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.http.trace_exporter>'
        exporter_otlp_proto_http_trace_exporter_module.OTLPSpanExporter = lambda *args, **kwargs: None
        exporter_otlp_proto_http_module.trace_exporter = exporter_otlp_proto_http_trace_exporter_module
        
        # semconv module
        semconv_module = types.ModuleType('opentelemetry.semconv')
        semconv_module.__file__ = '<mocked opentelemetry.semconv>'
        semconv_module.__path__ = []
        otel_module.semconv = semconv_module
        
        semconv_trace_module = types.ModuleType('opentelemetry.semconv.trace')
        semconv_trace_module.__file__ = '<mocked opentelemetry.semconv.trace>'
        semconv_trace_module.SpanAttributes = lambda *args, **kwargs: None
        semconv_module.trace = semconv_trace_module
        
        # metrics module
        metrics_module = types.ModuleType('opentelemetry.metrics')
        metrics_module.__file__ = '<mocked opentelemetry.metrics>'
        metrics_module.__path__ = []
        metrics_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.metrics', loader=None)
        metrics_module.get_meter_provider = lambda: None
        metrics_module.get_meter = lambda *args, **kwargs: None
        metrics_module.set_meter_provider = lambda *args, **kwargs: None
        otel_module.metrics = metrics_module
        
        # Register all modules in sys.modules
        sys.modules['opentelemetry'] = otel_module
        sys.modules['opentelemetry.trace'] = trace_module
        sys.modules['opentelemetry.trace.status'] = trace_status_module
        sys.modules['opentelemetry.trace.propagation'] = trace_propagation_module
        sys.modules['opentelemetry.trace.propagation.tracecontext'] = trace_propagation_tracecontext
        sys.modules['opentelemetry.propagate'] = propagate_module
        sys.modules['opentelemetry.metrics'] = metrics_module
        sys.modules['opentelemetry.instrumentation'] = instrumentation_module
        sys.modules['opentelemetry.instrumentation.fastapi'] = instrumentation_fastapi
        sys.modules['opentelemetry.instrumentation.grpc'] = instrumentation_grpc
        sys.modules['opentelemetry.instrumentation.utils'] = instrumentation_utils
        sys.modules['opentelemetry.instrumentation._semconv'] = instrumentation_semconv
        sys.modules['opentelemetry.instrumentation.logging'] = instrumentation_logging
        sys.modules['opentelemetry.instrumentation.requests'] = instrumentation_requests
        sys.modules['opentelemetry.instrumentation.system_metrics'] = instrumentation_system_metrics
        sys.modules['opentelemetry.sdk'] = sdk_module
        sys.modules['opentelemetry.sdk.trace'] = sdk_trace_module
        sys.modules['opentelemetry.sdk.trace.sampling'] = sdk_trace_sampling_module
        sys.modules['opentelemetry.sdk.trace.export'] = sdk_trace_export_module
        sys.modules['opentelemetry.sdk.trace.export.in_memory_span_exporter'] = in_memory_exporter
        sys.modules['opentelemetry.sdk.resources'] = sdk_resources_module
        sys.modules['opentelemetry.exporter'] = exporter_module
        sys.modules['opentelemetry.exporter.jaeger'] = exporter_jaeger_module
        sys.modules['opentelemetry.exporter.jaeger.thrift'] = exporter_jaeger_thrift_module
        sys.modules['opentelemetry.exporter.otlp'] = exporter_otlp_module
        sys.modules['opentelemetry.exporter.otlp.proto'] = exporter_otlp_proto_module
        sys.modules['opentelemetry.exporter.otlp.proto.grpc'] = exporter_otlp_proto_grpc_module
        sys.modules['opentelemetry.exporter.otlp.proto.grpc.trace_exporter'] = exporter_otlp_proto_grpc_trace_exporter_module
        sys.modules['opentelemetry.exporter.otlp.proto.http'] = exporter_otlp_proto_http_module
        sys.modules['opentelemetry.exporter.otlp.proto.http.trace_exporter'] = exporter_otlp_proto_http_trace_exporter_module
        sys.modules['opentelemetry.semconv'] = semconv_module
        sys.modules['opentelemetry.semconv.trace'] = semconv_trace_module

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


# ---- Runner module stub setup ----
# NOTE: Do NOT create a runner stub here. The generator/conftest.py adds generator/
# to sys.path which makes generator/runner importable as 'runner'. Creating a stub
# here would shadow the real module and cause import errors.
# If runner tests fail, the generator/conftest.py will handle the path setup.


# ---- Prometheus Client stub setup ----
# prometheus_client needs special handling for its .core submodule
if 'prometheus_client' not in sys.modules:
    try:
        import prometheus_client
    except ImportError:
        # Create prometheus_client package stub
        import types
        import importlib.util
        prom_module = types.ModuleType('prometheus_client')
        prom_module.__file__ = '<mocked prometheus_client>'
        prom_module.__path__ = []  # Make it a package
        prom_module.__spec__ = importlib.util.spec_from_loader('prometheus_client', loader=None)
        
        # Create core submodule
        prom_core = types.ModuleType('prometheus_client.core')
        prom_core.__file__ = '<mocked prometheus_client.core>'
        prom_core.__path__ = []  # Make it a package
        prom_core.__spec__ = importlib.util.spec_from_loader('prometheus_client.core', loader=None)
        prom_module.core = prom_core
        
        # Create registry submodule
        prom_registry = types.ModuleType('prometheus_client.registry')
        prom_registry.__file__ = '<mocked prometheus_client.registry>'
        prom_registry.__path__ = []  # Make it a package
        prom_registry.__spec__ = importlib.util.spec_from_loader('prometheus_client.registry', loader=None)
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
            DEFAULT_BUCKETS = (.005, .01, .025, .05, .075, .1, .25, .5, .75, 1.0, 2.5, 5.0, 7.5, 10.0, float("inf"))
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
        prom_module.PLATFORM_COLLECTOR = lambda *args, **kwargs: None
        prom_module.generate_latest = lambda *args, **kwargs: b''
        prom_module.start_http_server = lambda *args, **kwargs: None
        prom_module.REGISTRY = _MockCollectorRegistry()
        
        # Create multiprocess submodule
        prom_multiprocess = types.ModuleType('prometheus_client.multiprocess')
        prom_multiprocess.__file__ = '<mocked prometheus_client.multiprocess>'
        prom_multiprocess.__path__ = []
        prom_multiprocess.__spec__ = importlib.util.spec_from_loader('prometheus_client.multiprocess', loader=None)
        prom_multiprocess.MultiProcessCollector = lambda *args, **kwargs: None
        prom_module.multiprocess = prom_multiprocess
        
        # Create metrics submodule
        prom_metrics = types.ModuleType('prometheus_client.metrics')
        prom_metrics.__file__ = '<mocked prometheus_client.metrics>'
        prom_metrics.__path__ = []  # Make it a package
        prom_metrics.__spec__ = importlib.util.spec_from_loader('prometheus_client.metrics', loader=None)
        
        # Create a base class for metric wrappers
        class MetricWrapperBase:
            def __init__(self, *args, **kwargs):
                pass
        
        prom_metrics.MetricWrapperBase = MetricWrapperBase
        prom_module.metrics = prom_metrics
        
        # Register modules
        sys.modules['prometheus_client'] = prom_module
        sys.modules['prometheus_client.core'] = prom_core
        sys.modules['prometheus_client.registry'] = prom_registry
        sys.modules['prometheus_client.metrics'] = prom_metrics
        sys.modules['prometheus_client.multiprocess'] = prom_multiprocess


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
