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

# CRITICAL: Set up mocks BEFORE any imports that might trigger DLL errors
# This prevents torch DLL initialization errors on Windows

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
                return self._data.get(name, None)
        class MockValidator:
            def __init__(self, *args, **kwargs):
                pass
        mock_module.Dynaconf = MockDynaconf
        mock_module.Validator = MockValidator
    
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
]

for dep in _OPTIONAL_DEPENDENCIES:
    if dep not in sys.modules:
        try:
            __import__(dep)
        except Exception:
            # Create a more sophisticated mock that handles submodule access
            # Catch all exceptions (not just ImportError) to handle DLL errors on Windows
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
        
        # Register modules
        sys.modules['opentelemetry'] = otel_module
        sys.modules['opentelemetry.trace'] = trace_module
        sys.modules['opentelemetry.instrumentation'] = instrumentation_module
        sys.modules['opentelemetry.instrumentation.fastapi'] = instrumentation_fastapi
