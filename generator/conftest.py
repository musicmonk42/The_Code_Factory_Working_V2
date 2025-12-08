# generator/conftest.py
"""
Root conftest.py for generator tests.
Adds the generator directory to sys.path to allow imports like 'from main.api import ...'
Sets up mocks for Windows DLL issues and missing dependencies.
"""
import sys
import os
from pathlib import Path
from types import ModuleType

# Set testing environment variables EARLY
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("PYTEST_CURRENT_TEST", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# CRITICAL: Set up mocks BEFORE any imports that might trigger DLL errors
# This prevents torch DLL initialization errors on Windows

def _create_mock_module(name):
    """Create a minimal mock module for missing dependencies."""
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
    if name == 'dotenv':
        # dotenv needs load_dotenv and find_dotenv functions
        mock_module.load_dotenv = lambda *args, **kwargs: None
        mock_module.find_dotenv = lambda *args, **kwargs: None
    elif name == 'dynaconf':
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
        class MockValidator:
            def __init__(self, *args, **kwargs):
                pass
        class ValidationError(Exception):
            pass
        mock_module.Dynaconf = MockDynaconf
        mock_module.Validator = MockValidator
        mock_module.ValidationError = ValidationError
        # Create validator submodule
        validator_module = ModuleType('dynaconf.validator')
        validator_module.__file__ = f"<mocked dynaconf.validator>"
        validator_module.__path__ = []
        validator_module.Validator = MockValidator
        validator_module.ValidationError = ValidationError
        mock_module.validator = validator_module
        # Register the validator submodule
        sys.modules['dynaconf.validator'] = validator_module
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
    elif name == 'google.protobuf':
        # google.protobuf needs special descriptors for generated protobuf files
        mock_module.descriptor = MockCallable()
        mock_module.descriptor_pool = MockCallable()
        mock_module.symbol_database = MockCallable()
        class InternalModule:
            builder = MockCallable()
        mock_module.internal = InternalModule()
    elif name == 'azure.core.exceptions':
        # Azure exceptions need to be proper exception classes
        class AzureError(Exception):
            pass
        class ResourceExistsError(AzureError):
            pass
        class ResourceNotFoundError(AzureError):
            pass
        mock_module.AzureError = AzureError
        mock_module.ResourceExistsError = ResourceExistsError
        mock_module.ResourceNotFoundError = ResourceNotFoundError
    elif name == 'botocore.exceptions':
        # Botocore exceptions need to be proper exception classes
        class BotoCoreError(Exception):
            pass
        class ClientError(BotoCoreError):
            pass
        mock_module.BotoCoreError = BotoCoreError
        mock_module.ClientError = ClientError
    
    return mock_module

# Only mock if genuinely missing (not if already imported)
_OPTIONAL_DEPENDENCIES = [
    'torch',  # PyTorch - causes DLL errors on Windows
    'sentence_transformers',  # Uses torch, causes DLL errors
    'transformers',  # Uses torch, causes DLL errors
    'spacy',  # Uses torch via thinc, causes DLL errors
    'presidio_analyzer',  # Uses spacy, causes DLL errors
    'presidio_anonymizer',  # Uses spacy, causes DLL errors
    'networkx',  # Graph library
    'tiktoken',  # Often missing, used by LLM clients
    'defusedxml',  # XML parsing
    'openai',  # OpenAI API
    'chromadb',  # Vector database
    'anthropic',  # Anthropic API
    'dotenv',  # Environment variables
    'backoff',  # Retry library
    'hypothesis',  # Property-based testing
    'psutil',  # System utilities
    'xattr',  # Extended attributes
    'hvac',  # Hashicorp Vault
    'pkcs11',  # HSM integration
    'python-pkcs11',  # HSM integration
    'faiss',  # Vector search
    'dynaconf',  # Configuration management
    'watchdog',  # File system events
    'aiofiles',  # Async file operations
    # Cloud SDK packages
    'google.cloud.storage',  # Google Cloud Storage
    'google.cloud',  # Google Cloud base
    'google.protobuf',  # Protocol Buffers
    'azure.storage.blob',  # Azure Blob Storage
    'azure.storage.blob.aio',  # Azure Blob Storage async
    'azure.core.exceptions',  # Azure exceptions
    'boto3',  # AWS SDK
    'botocore.exceptions',  # AWS SDK exceptions
]

for dep in _OPTIONAL_DEPENDENCIES:
    if dep not in sys.modules:
        try:
            __import__(dep)
        except (ImportError, OSError):
            # Create a more sophisticated mock that handles submodule access
            # Catch ImportError (not installed) and OSError (DLL errors on Windows)
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
            
            # Special handling for packages that need specific submodules
            if dep == 'watchdog':
                # Create watchdog.events submodule
                watchdog_events = _create_mock_module('watchdog.events')
                sys.modules['watchdog.events'] = watchdog_events
                
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
                watchdog_observers = _create_mock_module('watchdog.observers')
                sys.modules['watchdog.observers'] = watchdog_observers
                
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
            elif dep == 'defusedxml':
                # Create defusedxml.ElementTree submodule
                defusedxml_et = _create_mock_module('defusedxml.ElementTree')
                sys.modules['defusedxml.ElementTree'] = defusedxml_et
                mock_module.ElementTree = defusedxml_et
                # Add common ElementTree functions
                defusedxml_et.parse = lambda *args, **kwargs: None
                defusedxml_et.fromstring = lambda *args, **kwargs: None
                defusedxml_et.XML = lambda *args, **kwargs: None
        except Exception:
            # Catch any other errors and create a mock
            mock_module = _create_mock_module(dep)
            sys.modules[dep] = mock_module
            
            if '.' in dep:
                parts = dep.split('.')
                for i in range(1, len(parts)):
                    parent_name = '.'.join(parts[:i])
                    if parent_name not in sys.modules:
                        parent_mock = _create_mock_module(parent_name)
                        sys.modules[parent_name] = parent_mock

# Add the generator directory to sys.path
generator_root = Path(__file__).parent.resolve()
generator_root_str = str(generator_root)

# Insert at the beginning only if not already there
if not sys.path or sys.path[0] != generator_root_str:
    if generator_root_str in sys.path:
        sys.path.remove(generator_root_str)
    sys.path.insert(0, generator_root_str)

# ---- OpenTelemetry stub setup ----
# OpenTelemetry requires special handling because it has specific methods that must exist
# and be callable, not just module stubs
if 'opentelemetry' not in sys.modules:
    try:
        __import__('opentelemetry')
    except ImportError:
        # Create a complete OpenTelemetry stub with all required attributes
        
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
        
        # Create Status and StatusCode classes
        class Status:
            def __init__(self, status_code, description=""):
                self.status_code = status_code
                self.description = description
        
        class StatusCode:
            OK = "OK"
            ERROR = "ERROR"
            UNSET = "UNSET"
        
        # Create trace module with all required methods
        trace_module = ModuleType('opentelemetry.trace')
        trace_module.__file__ = '<mocked opentelemetry.trace>'
        trace_module.get_tracer = lambda *args, **kwargs: _NoOpTracer()
        trace_module.get_current_span = lambda: _NoOpSpan()
        trace_module.get_tracer_provider = lambda: None
        trace_module.set_tracer_provider = lambda *args, **kwargs: None
        trace_module.Status = Status
        trace_module.StatusCode = StatusCode
        
        # Create main opentelemetry module
        otel_module = ModuleType('opentelemetry')
        otel_module.__file__ = '<mocked opentelemetry>'
        otel_module.__path__ = []
        otel_module.trace = trace_module
        
        # Create instrumentation module
        instrumentation_module = ModuleType('opentelemetry.instrumentation')
        instrumentation_module.__file__ = '<mocked opentelemetry.instrumentation>'
        instrumentation_module.__path__ = []  # This is required for submodule imports
        otel_module.instrumentation = instrumentation_module
        
        # Create common instrumentation submodules
        instrumentation_fastapi = ModuleType('opentelemetry.instrumentation.fastapi')
        instrumentation_fastapi.__file__ = '<mocked opentelemetry.instrumentation.fastapi>'
        
        class FastAPIInstrumentor:
            @classmethod
            def instrument_app(cls, *args, **kwargs):
                pass
        
        instrumentation_fastapi.FastAPIInstrumentor = FastAPIInstrumentor
        
        # Create sdk modules
        sdk_module = ModuleType('opentelemetry.sdk')
        sdk_module.__file__ = '<mocked opentelemetry.sdk>'
        sdk_module.__path__ = []  # Parent module for submodules
        otel_module.sdk = sdk_module
        
        sdk_trace_module = ModuleType('opentelemetry.sdk.trace')
        sdk_trace_module.__file__ = '<mocked opentelemetry.sdk.trace>'
        sdk_trace_module.__path__ = []  # Parent module for submodules
        sdk_trace_module.TracerProvider = lambda *args, **kwargs: None
        sdk_module.trace = sdk_trace_module
        
        sdk_trace_export_module = ModuleType('opentelemetry.sdk.trace.export')
        sdk_trace_export_module.__file__ = '<mocked opentelemetry.sdk.trace.export>'
        sdk_trace_export_module.__path__ = []
        sdk_trace_export_module.ConsoleSpanExporter = lambda *args, **kwargs: None
        sdk_trace_export_module.SimpleSpanProcessor = lambda *args, **kwargs: None
        sdk_trace_export_module.BatchSpanProcessor = lambda *args, **kwargs: None
        sdk_trace_module.export = sdk_trace_export_module
        
        sdk_resources_module = ModuleType('opentelemetry.sdk.resources')
        sdk_resources_module.__file__ = '<mocked opentelemetry.sdk.resources>'
        sdk_resources_module.Resource = lambda **kwargs: None
        sdk_module.resources = sdk_resources_module
        
        # Create exporter modules
        exporter_module = ModuleType('opentelemetry.exporter')
        exporter_module.__file__ = '<mocked opentelemetry.exporter>'
        exporter_module.__path__ = []
        otel_module.exporter = exporter_module
        
        exporter_jaeger_module = ModuleType('opentelemetry.exporter.jaeger')
        exporter_jaeger_module.__file__ = '<mocked opentelemetry.exporter.jaeger>'
        exporter_jaeger_module.__path__ = []
        exporter_module.jaeger = exporter_jaeger_module
        
        exporter_jaeger_thrift_module = ModuleType('opentelemetry.exporter.jaeger.thrift')
        exporter_jaeger_thrift_module.__file__ = '<mocked opentelemetry.exporter.jaeger.thrift>'
        exporter_jaeger_thrift_module.JaegerExporter = lambda *args, **kwargs: None
        exporter_jaeger_module.thrift = exporter_jaeger_thrift_module
        
        exporter_otlp_module = ModuleType('opentelemetry.exporter.otlp')
        exporter_otlp_module.__file__ = '<mocked opentelemetry.exporter.otlp>'
        exporter_otlp_module.__path__ = []
        exporter_module.otlp = exporter_otlp_module
        
        exporter_otlp_proto_module = ModuleType('opentelemetry.exporter.otlp.proto')
        exporter_otlp_proto_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto>'
        exporter_otlp_proto_module.__path__ = []
        exporter_otlp_module.proto = exporter_otlp_proto_module
        
        exporter_otlp_proto_grpc_module = ModuleType('opentelemetry.exporter.otlp.proto.grpc')
        exporter_otlp_proto_grpc_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.grpc>'
        exporter_otlp_proto_grpc_module.__path__ = []
        exporter_otlp_proto_module.grpc = exporter_otlp_proto_grpc_module
        
        exporter_otlp_proto_grpc_trace_exporter_module = ModuleType('opentelemetry.exporter.otlp.proto.grpc.trace_exporter')
        exporter_otlp_proto_grpc_trace_exporter_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.grpc.trace_exporter>'
        exporter_otlp_proto_grpc_trace_exporter_module.OTLPSpanExporter = lambda *args, **kwargs: None
        exporter_otlp_proto_grpc_module.trace_exporter = exporter_otlp_proto_grpc_trace_exporter_module
        
        sdk_trace_sampling_module = ModuleType('opentelemetry.sdk.trace.sampling')
        sdk_trace_sampling_module.__file__ = '<mocked opentelemetry.sdk.trace.sampling>'
        sdk_trace_sampling_module.ParentBased = lambda *args, **kwargs: None
        sdk_trace_sampling_module.TraceIdRatioBased = lambda *args, **kwargs: None
        sdk_trace_sampling_module.ALWAYS_ON = lambda *args, **kwargs: None
        sdk_trace_module.sampling = sdk_trace_sampling_module
        
        # Register modules
        sys.modules['opentelemetry'] = otel_module
        sys.modules['opentelemetry.trace'] = trace_module
        sys.modules['opentelemetry.instrumentation'] = instrumentation_module
        sys.modules['opentelemetry.instrumentation.fastapi'] = instrumentation_fastapi
        sys.modules['opentelemetry.sdk'] = sdk_module
        sys.modules['opentelemetry.sdk.trace'] = sdk_trace_module
        sys.modules['opentelemetry.sdk.trace.sampling'] = sdk_trace_sampling_module
        sys.modules['opentelemetry.sdk.trace.export'] = sdk_trace_export_module
        sys.modules['opentelemetry.sdk.resources'] = sdk_resources_module
        sys.modules['opentelemetry.exporter'] = exporter_module
        sys.modules['opentelemetry.exporter.jaeger'] = exporter_jaeger_module
        sys.modules['opentelemetry.exporter.jaeger.thrift'] = exporter_jaeger_thrift_module
        sys.modules['opentelemetry.exporter.otlp'] = exporter_otlp_module
        sys.modules['opentelemetry.exporter.otlp.proto'] = exporter_otlp_proto_module
        sys.modules['opentelemetry.exporter.otlp.proto.grpc'] = exporter_otlp_proto_grpc_module
        sys.modules['opentelemetry.exporter.otlp.proto.grpc.trace_exporter'] = exporter_otlp_proto_grpc_trace_exporter_module
