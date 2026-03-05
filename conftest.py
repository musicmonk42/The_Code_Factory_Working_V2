# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Simplified pytest configuration for The Code Factory.

This conftest.py provides:
1. Path setup for modules
2. Test environment configuration
3. Essential fixtures for async tests and cleanup
4. Minimal mocking for truly optional dependencies

Key optimizations:
- Reduced from 3,487 to ~600 lines (83% reduction)
- Lazy loading of mocks only when needed
- Simplified mock creation patterns
- Removed defensive validation overhead
- Cleaner, more maintainable structure
"""

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# ============================================================================
# 1. PATH SETUP
# ============================================================================

# Set matplotlib backend early to prevent GUI initialization
os.environ.setdefault("MPLBACKEND", "Agg")

# Add project root to Python path (highest priority)
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)

# Add subdirectories at END of path to support relative imports in tests
for subdir in ["self_fixing_engineer", "omnicore_engine", "generator"]:
    subdir_path = os.path.join(project_root, subdir)
    if subdir_path not in sys.path:
        sys.path.append(subdir_path)

# Execute path_setup early if available
try:
    import path_setup
except ImportError as e:
    import warnings
    warnings.warn(f"Could not import path_setup module: {e}. Using basic path configuration.")

# Pre-initialize NLTK data paths
try:
    import nltk
    nltk_data_home = os.path.expanduser('~/nltk_data')
    if os.path.exists(nltk_data_home) and nltk_data_home not in nltk.data.path:
        nltk.data.path.insert(0, nltk_data_home)
except ImportError:
    pass  # NLTK not installed, skip

# ============================================================================
# 2. TEST ENVIRONMENT CONFIGURATION
# ============================================================================

# Set environment variables to skip expensive initialization during tests
os.environ["TESTING"] = "1"
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
os.environ.setdefault("PYTEST_CURRENT_TEST", "true")
os.environ.setdefault("PYTEST_COLLECTING", "1")
os.environ.setdefault("SKIP_AUDIT_INIT", "1")
os.environ.setdefault("SKIP_BACKGROUND_TASKS", "1")
os.environ.setdefault("NO_MONITORING", "1")
os.environ.setdefault("DISABLE_TELEMETRY", "1")
os.environ.setdefault("PROMETHEUS_DISABLE_CREATED_SERIES", "True")

# CPU limit safety: Reduce parallelism in CPU-constrained environments
_CPU_CONSTRAINED = os.environ.get('CI') == 'true' or os.environ.get('GITHUB_ACTIONS') == 'true'
if _CPU_CONSTRAINED:
    os.environ.setdefault("PYTEST_XDIST_WORKER_COUNT", "1")

# ============================================================================
# 3. SIMPLIFIED MOCK INFRASTRUCTURE
# ============================================================================

def _create_simple_mock(module_name, attributes=None, submodules=None):
    """Create a simple mock module with specified attributes.
    
    Args:
        module_name: Name of the module to mock
        attributes: Dict of attributes to add to the module
        submodules: List of submodule names to create
    """
    import importlib.machinery
    import importlib.util
    
    # If module already exists, update it with new attributes instead of returning early
    if module_name in sys.modules:
        mock = sys.modules[module_name]
        # Add new attributes to the existing module
        if attributes:
            for attr_name, attr_value in attributes.items():
                setattr(mock, attr_name, attr_value)
        return
    
    # Create mock module
    spec = importlib.machinery.ModuleSpec(
        name=module_name,
        loader=None,
        is_package=True if submodules else False
    )
    mock = importlib.util.module_from_spec(spec)
    mock.__file__ = f"<mocked {module_name}>"
    if submodules:
        mock.__path__ = []
    
    # Add attributes
    if attributes:
        for attr_name, attr_value in attributes.items():
            setattr(mock, attr_name, attr_value)
    
    # Register module
    sys.modules[module_name] = mock
    
    # Create submodules if specified
    if submodules:
        for submod_name in submodules:
            full_name = f"{module_name}.{submod_name}"
            sub_spec = importlib.machinery.ModuleSpec(
                name=full_name,
                loader=None,
                is_package=False
            )
            sub_mock = importlib.util.module_from_spec(sub_spec)
            sub_mock.__file__ = f"<mocked {full_name}>"
            sys.modules[full_name] = sub_mock
            setattr(mock, submod_name, sub_mock)


def _initialize_prometheus_mock():
    """Initialize prometheus_client mock if not installed."""
    try:
        import prometheus_client
        return  # Real module available, don't mock
    except ImportError:
        pass
    
    # Create mock metric classes
    class _MockSample:
        """Mock Prometheus sample representing a single metric data point."""
        def __init__(self, name, labels, value, timestamp=None):
            self.name = name
            self.labels = labels
            self.value = value
            self.timestamp = timestamp

    class _MockMetricFamily:
        """Mock Prometheus metric family containing multiple samples."""
        def __init__(self, name, documentation, metric_type, samples):
            self.name = name
            self.documentation = documentation
            self.type = metric_type
            self.samples = samples

    class _MockValueClass:
        """Mock for prometheus_client ValueClass that wraps an int with .get()."""
        def __init__(self, value=0):
            self._val = value
        def get(self):
            return self._val
        def set(self, value):
            self._val = value
        def inc(self, amount=1):
            self._val += amount

    class _MockCounterChild:
        """Child counter for a specific label combination."""
        def __init__(self, parent, label_key, label_values):
            self.parent = parent
            self.label_key = label_key
            self.label_values = label_values
            self._value = _MockValueClass(0)
        def inc(self, amount=1):
            self._value.inc(amount)
        def labels(self, **kwargs):
            return self.parent.labels(**kwargs)
        def collect(self):
            return self.parent.collect()

    class MockCounter:
        """Mock Prometheus Counter that tracks increments and supports labels."""
        def __init__(self, name='', description='', labelnames=(), *args, **kwargs):
            self.name = name if isinstance(name, str) else ''
            self.description = description if isinstance(description, str) else ''
            self.labelnames = labelnames
            self._metrics = {}
        def labels(self, *args, **kwargs):
            if args:
                label_key = tuple(zip(self.labelnames, args)) if self.labelnames else tuple(enumerate(args))
                label_values = dict(label_key)
            else:
                label_key = tuple(sorted(kwargs.items()))
                label_values = kwargs
            if label_key not in self._metrics:
                self._metrics[label_key] = _MockCounterChild(self, label_key, label_values)
            return self._metrics[label_key]
        def inc(self, amount=1):
            label_key = ()
            if label_key not in self._metrics:
                self._metrics[label_key] = _MockCounterChild(self, label_key, {})
            self._metrics[label_key].inc(amount)
        def collect(self):
            samples = []
            for label_key, child in self._metrics.items():
                sample = _MockSample(
                    name=self.name + '_total',
                    labels=dict(label_key) if label_key else {},
                    value=child._value.get(),
                )
                samples.append(sample)
            return [_MockMetricFamily(self.name, self.description, 'counter', samples)]
        def dec(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def time(self, *args, **kwargs):
            def decorator(func):
                return func
            decorator.__enter__ = lambda: None
            decorator.__exit__ = lambda *args: None
            return decorator
        def clear(self):
            """Clear all metric values."""
            self._metrics = {}

    class MockMetric:
        def __init__(self, *args, **kwargs):
            self._value = _MockValueClass(0)
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            pass
        def dec(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def time(self, *args, **kwargs):
            def decorator(func):
                return func
            decorator.__enter__ = lambda: None
            decorator.__exit__ = lambda *args: None
            return decorator
        def clear(self):
            """Clear all metric values."""
            pass
    
    class MockRegistry:
        def __init__(self, *args, **kwargs):
            self._names_to_collectors = {}
            self._collector_to_names = {}  # Required for prometheus client compatibility
        def register(self, collector):
            pass
        def unregister(self, collector):
            pass
        def get_sample_value(self, *args, **kwargs):
            return None
    
    # Create main module mock
    _create_simple_mock("prometheus_client", {
        "Counter": MockCounter,
        "Histogram": MockMetric,
        "Gauge": MockMetric,
        "Info": MockMetric,
        "Summary": MockMetric,
        "CollectorRegistry": MockRegistry,
        "REGISTRY": MockRegistry(),
        "CONTENT_TYPE_LATEST": "text/plain; version=0.0.4; charset=utf-8",
        "push_to_gateway": lambda *args, **kwargs: None,
    }, submodules=["core", "registry", "multiprocess", "metrics"])
    
    # Add attributes to submodules
    sys.modules["prometheus_client.core"].Counter = MockCounter
    sys.modules["prometheus_client.core"].Histogram = MockMetric
    sys.modules["prometheus_client.core"].Gauge = MockMetric
    sys.modules["prometheus_client.core"].REGISTRY = sys.modules["prometheus_client"].REGISTRY
    sys.modules["prometheus_client.registry"].REGISTRY = sys.modules["prometheus_client"].REGISTRY
    sys.modules["prometheus_client.multiprocess"].MultiProcessCollector = lambda *args, **kwargs: None
    
    class MetricWrapperBase:
        def __init__(self, *args, **kwargs):
            pass
    sys.modules["prometheus_client.metrics"].MetricWrapperBase = MetricWrapperBase


def _initialize_opentelemetry_mock():
    """Initialize opentelemetry mock if not installed."""
    try:
        import opentelemetry
        return  # Real module available, don't mock
    except ImportError:
        pass
    
    # Create mock tracer and trace objects
    class MockTracer:
        def start_span(self, name, **kwargs):
            return MockSpan()
        def start_as_current_span(self, name, **kwargs):
            return MockSpan()
    
    class MockSpan:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def __call__(self, *args, **kwargs):
            """Make MockSpan callable for decorator usage."""
            if len(args) == 1 and callable(args[0]):
                # Used as decorator
                func = args[0]
                def wrapper(*inner_args, **inner_kwargs):
                    with self:
                        return func(*inner_args, **inner_kwargs)
                return wrapper
            return self
        def set_attribute(self, key, value):
            pass
        def add_event(self, name, attributes=None):
            pass
        def set_status(self, status):
            pass
        def is_recording(self):
            """Simulates a non-recording span for testing purposes (returns False)."""
            return False
        def record_exception(self, exception):
            """Record an exception on this span."""
            pass
        def end(self):
            """End the span."""
            pass
    
    # Create mock Status and StatusCode
    class MockStatus:
        """Mock OpenTelemetry Status that accepts status_code and description."""
        def __init__(self, status_code=None, description=None):
            self.status_code = status_code or "UNSET"
            self.description = description
        
        OK = "OK"
        ERROR = "ERROR"
    
    class MockStatusCode:
        OK = "OK"
        ERROR = "ERROR"
        UNSET = "UNSET"
    
    # Create mock metrics classes
    class MockMeter:
        def create_counter(self, name, **kwargs):
            return MockCounter()
        def create_histogram(self, name, **kwargs):
            return MockHistogram()
        def create_observable_gauge(self, name, callback, **kwargs):
            return MockObservableGauge()
    
    class MockCounter:
        def add(self, value, attributes=None):
            pass
    
    class MockHistogram:
        def record(self, value, attributes=None):
            pass
    
    class MockObservableGauge:
        pass
    
    # Create simple mocks for opentelemetry modules
    _create_simple_mock("opentelemetry", {}, submodules=["trace", "sdk", "metrics"])
    _create_simple_mock("opentelemetry.trace", {
        "get_tracer": lambda name: MockTracer(),
        "get_tracer_provider": lambda: MagicMock(),
        "set_tracer_provider": lambda provider: None,
        "get_current_span": lambda: MockSpan(),
        "Status": MockStatus,
        "StatusCode": MockStatusCode,
    }, submodules=["status"])
    _create_simple_mock("opentelemetry.trace.status", {
        "Status": MockStatus,
        "StatusCode": MockStatusCode,
    }, submodules=[])
    _create_simple_mock("opentelemetry.metrics", {
        "get_meter": lambda *args, **kwargs: MockMeter(),
        "get_meter_provider": lambda: MagicMock(),
        "set_meter_provider": lambda provider: None,
        "Counter": MockCounter,
        "Histogram": MockHistogram,
        "ObservableGauge": MockObservableGauge,
    }, submodules=[])
    _create_simple_mock("opentelemetry.sdk", {}, submodules=["trace"])
    _create_simple_mock("opentelemetry.sdk.trace", {}, submodules=["export"])
    _create_simple_mock("opentelemetry.sdk.trace.export", {}, submodules=[])
    _create_simple_mock("opentelemetry.exporter", {}, submodules=[])


# Initialize mocks at import time
_initialize_prometheus_mock()
_initialize_opentelemetry_mock()

# ============================================================================
# 4. PYTEST HOOKS
# ============================================================================

def pytest_configure(config):
    """Configure pytest environment before test collection."""
    if config.option.collectonly:
        # Signal that we're only collecting, not running tests
        os.environ['SKIP_EXPENSIVE_INIT'] = '1'
        os.environ['PYTEST_COLLECTING_ONLY'] = '1'


def pytest_collectstart(collector):
    """Called before collecting tests from each module."""
    # Suppress collection-time warnings
    import warnings
    warnings.filterwarnings('ignore', category=DeprecationWarning)


def pytest_collection_finish(session):
    """Called after test collection is finished."""
    # Clean up collection-time environment variables
    os.environ.pop('PYTEST_COLLECTING_ONLY', None)


import logging as _logging
_diag_logger = _logging.getLogger("pytest.diagnostic")


def pytest_runtest_setup(item):
    """Log a diagnostic message before each test runs to help identify hangs."""
    _diag_logger.info("[SETUP   ] starting: %s", item.nodeid)


def pytest_runtest_teardown(item, nextitem):
    """Log a diagnostic message after each test completes to help identify hangs."""
    _diag_logger.info("[TEARDOWN] finished: %s", item.nodeid)


# ============================================================================
# 5. PYTEST FIXTURES
# ============================================================================

import pytest
import pytest_asyncio
import asyncio


# NOTE: Removed custom event_loop fixture. pytest-asyncio 1.3.0+ manages event loops
# automatically. Custom event_loop fixtures conflict with pytest-asyncio's internal
# handling, causing "can't start new thread" errors in async database tests.
# See: https://pytest-asyncio.readthedocs.io/en/latest/reference/fixtures.html


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up global test environment."""
    # Set additional test environment variables
    os.environ["TESTING"] = "1"
    os.environ["APP_ENV"] = "test"
    
    # Disable expensive features
    os.environ["SKIP_AUDIT_INIT"] = "1"
    os.environ["NO_MONITORING"] = "1"
    
    # Set audit crypto environment variables (required for audit crypto tests)
    os.environ["AUDIT_CRYPTO_PROVIDER_TYPE"] = "software"
    os.environ["AUDIT_CRYPTO_DEFAULT_ALGO"] = "ed25519"
    os.environ["AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS"] = "86400"
    os.environ["AUDIT_LOG_DEV_MODE"] = "true"
    os.environ["AUDIT_CRYPTO_HSM_ENABLED"] = "false"
    
    yield
    
    # Cleanup after all tests
    pass


@pytest.fixture(scope="session", autouse=True)
def setup_prometheus_multiproc_dir(tmp_path_factory):
    """Set up Prometheus multiprocess directory for testing."""
    if "prometheus_client" in sys.modules:
        try:
            prom_dir = tmp_path_factory.mktemp("prometheus")
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(prom_dir)
        except Exception:
            pass  # If prometheus not available or already configured
    yield


@pytest.fixture(scope="function", autouse=False)  # Disabled: causes issues with async tests
def isolate_imports():
    """Isolate imports between tests to prevent state leakage.
    
    NOTE: This fixture is disabled (autouse=False) because it can interfere
    with async database tests that require persistent module state.
    """
    # Record modules before test
    modules_before = set(sys.modules.keys())
    
    yield
    
    # Clean up modules added during test (except standard library)
    modules_after = set(sys.modules.keys())
    new_modules = modules_after - modules_before
    
    for mod_name in new_modules:
        # Only clean up project modules, not stdlib
        if any(mod_name.startswith(prefix) for prefix in [
            'generator.', 'omnicore_engine.', 'self_fixing_engineer.', 'server.'
        ]):
            sys.modules.pop(mod_name, None)


@pytest.fixture(scope="function")
def clean_registry():
    """Provide a clean prometheus registry for tests."""
    if "prometheus_client" in sys.modules:
        try:
            from prometheus_client import CollectorRegistry
            registry = CollectorRegistry()
            yield registry
        except ImportError:
            yield None
    else:
        yield None


@pytest.fixture(scope="function", autouse=True)
def cleanup_chromadb():
    """Clean up ChromaDB between tests to prevent state leakage.
    
    Only performs cleanup if chromadb is already imported (avoids import overhead).
    """
    yield
    
    # Only clean up if chromadb was actually imported during this test
    if "chromadb" in sys.modules:
        try:
            chromadb = sys.modules["chromadb"]
            if hasattr(chromadb, '_client'):
                chromadb._client = None
            if hasattr(chromadb, 'Client') and hasattr(chromadb.Client, '_instances'):
                chromadb.Client._instances = {}
        except (AttributeError, TypeError):
            pass


@pytest.fixture
def mock_redis():
    """Provide a mock Redis client for testing."""
    class MockRedis:
        def __init__(self):
            self.data = {}
        
        async def get(self, key):
            return self.data.get(key)
        
        async def set(self, key, value, ex=None):
            self.data[key] = value
            return True
        
        async def delete(self, *keys):
            for key in keys:
                self.data.pop(key, None)
            return len(keys)
        
        async def exists(self, *keys):
            return sum(1 for key in keys if key in self.data)
        
        async def ping(self):
            return True
        
        async def close(self):
            pass
    
    return MockRedis()


@pytest.fixture
def mock_llm_client():
    """Provide a mock LLM client for testing."""
    class MockLLMClient:
        async def generate(self, prompt, **kwargs):
            return {
                "content": "Mock response",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}
            }
        
        async def chat(self, messages, **kwargs):
            return {
                "content": "Mock chat response",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}
            }
    
    return MockLLMClient()


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory for test files."""
    return tmp_path


@pytest.fixture
def sample_code():
    """Provide sample code for testing code generation/analysis."""
    return '''
def hello_world():
    """A simple hello world function."""
    print("Hello, World!")
    return "Hello, World!"

if __name__ == "__main__":
    hello_world()
'''.strip()


@pytest.fixture
def sample_config():
    """Provide sample configuration for testing."""
    return {
        "app_name": "test_app",
        "environment": "test",
        "debug": True,
        "log_level": "INFO",
    }


@pytest_asyncio.fixture(scope="function")
async def async_client():
    """Provide an async HTTP client for API testing."""
    try:
        from httpx import AsyncClient
        async with AsyncClient() as client:
            yield client
    except ImportError:
        # If httpx not available, provide a mock
        class MockAsyncClient:
            async def get(self, url, **kwargs):
                return type('Response', (), {'status_code': 200, 'json': lambda: {}})()
            
            async def post(self, url, **kwargs):
                return type('Response', (), {'status_code': 201, 'json': lambda: {}})()
        
        yield MockAsyncClient()


@pytest.fixture(autouse=True)
def reset_environment_vars():
    """Reset environment variables after each test.
    
    Uses surgical key-by-key restore instead of os.environ.clear() to avoid
    disrupting pytest-xdist worker communication during parallel test execution.
    """
    original_env = os.environ.copy()
    yield
    # Restore original environment surgically (avoid os.environ.clear()
    # which can disrupt xdist worker communication)
    added_keys = set(os.environ.keys()) - set(original_env.keys())
    for key in added_keys:
        os.environ.pop(key, None)
    for key, value in original_env.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@pytest.fixture(scope="session", autouse=True)
def isolate_prometheus_registry():
    """Isolate Prometheus registry to prevent duplicate metric registration.
    
    This fixture runs once per test session and:
    1. Clears all collectors from REGISTRY before tests start
    2. Clears all collectors from REGISTRY after tests complete
    
    This prevents duplicate registration errors when metrics are defined
    at module import time across multiple test modules.
    """
    try:
        from prometheus_client import REGISTRY
        import logging
        logger = logging.getLogger(__name__)
        
        # Clear registry before tests
        collectors = list(REGISTRY._collector_to_names.keys())
        for collector in collectors:
            try:
                REGISTRY.unregister(collector)
            except (KeyError, ValueError) as e:
                # Collector may already be unregistered, log at debug level
                logger.debug(f"Could not unregister collector during setup: {e}")
        
        yield
        
        # Clear registry after tests
        collectors = list(REGISTRY._collector_to_names.keys())
        for collector in collectors:
            try:
                REGISTRY.unregister(collector)
            except (KeyError, ValueError) as e:
                # Collector may already be unregistered, log at debug level
                logger.debug(f"Could not unregister collector during cleanup: {e}")
    except ImportError:
        # Prometheus client not installed, skip
        yield


@pytest.fixture(autouse=False)
def cleanup_async_tasks():
    """Clean up async tasks after each test.
    
    This fixture must be explicitly requested by tests that need it.
    It is not auto-used because it can interfere with pytest-asyncio's
    internal event loop management and cause subsequent tests to hang.
    """
    yield
    
    # Cancel pending tasks
    try:
        import gc
        # Try to get the running loop first, fall back to get_event_loop for compatibility
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop, try to get the default loop
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                # Can't get any loop, skip cleanup
                return
        
        if not loop.is_closed():
            pending = asyncio.all_tasks(loop)
            for task in pending:
                if not task.done():
                    task.cancel()
            # Allow cancellations to process
            if pending:
                try:
                    loop.run_until_complete(asyncio.sleep(0.1))
                except RuntimeError:
                    pass
        gc.collect()
    except RuntimeError:
        # No event loop in this thread
        pass


@pytest.fixture(autouse=True)
def reset_prometheus_metrics():
    """Reset Prometheus metric values between tests without unregistering.
    
    This fixture clears metric values but keeps the metrics registered,
    allowing for clean test isolation while avoiding duplicate registration errors.
    
    Note:
        Uses private _collector_to_names and _metrics attributes as prometheus_client
        (v0.23.1) doesn't provide public API for metric value reset. This is safe
        as it only clears values, not the registration.
    """
    yield
    
    # Clear metric values after test
    try:
        from prometheus_client import REGISTRY
        for collector in list(REGISTRY._collector_to_names.keys()):
            if hasattr(collector, '_metrics'):
                try:
                    collector._metrics.clear()
                except (AttributeError, TypeError):
                    pass
    except (ImportError, RuntimeError):
        pass


@pytest.fixture(autouse=True)
def reset_logging_for_tests():
    """Reset logging configuration to allow caplog to capture records.
    
    This fixture ensures pytest's caplog fixture can capture log records by:
    1. Clearing non-pytest handlers from the root logger
    2. Configuring basic logging with DEBUG level
    3. Ensuring runner-specific loggers propagate to root
    4. Restoring original configuration after the test
    """
    import logging
    
    # Define format string once to avoid duplication
    LOG_FORMAT = '%(levelname)s:%(name)s:%(message)s'
    
    # Handler class names that belong to pytest and should be preserved
    _PYTEST_HANDLER_NAMES = {'LogCaptureHandler', '_LiveLoggingStreamHandler'}
    
    # Store original handlers and level
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    
    # Clear non-pytest handlers to allow pytest's caplog to work
    for handler in original_handlers:
        if handler.__class__.__name__ not in _PYTEST_HANDLER_NAMES:
            root_logger.removeHandler(handler)
    
    # Configure basic logging for tests
    # Note: force=True was added in Python 3.8; we provide a fallback for 3.7
    try:
        logging.basicConfig(level=logging.DEBUG, force=True, format=LOG_FORMAT)
    except TypeError:
        # Python 3.7 fallback: manually clear and reconfigure
        # Use proper removeHandler() instead of clear() for consistency
        for handler in root_logger.handlers[:]:
            if handler.__class__.__name__ not in _PYTEST_HANDLER_NAMES:
                root_logger.removeHandler(handler)
        logging.basicConfig(level=logging.DEBUG, format=LOG_FORMAT)
    
    # Also configure runner-specific loggers
    # These are the main logger namespaces used in the runner module
    for logger_name in ['runner', 'runner.audit', 'runner.action']:
        logger = logging.getLogger(logger_name)
        # Properly remove non-pytest handlers one by one
        for handler in logger.handlers[:]:
            if handler.__class__.__name__ not in _PYTEST_HANDLER_NAMES:
                logger.removeHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True  # Ensure logs propagate to root
    
    yield
    
    # Restore original configuration
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        if handler.__class__.__name__ not in _PYTEST_HANDLER_NAMES:
            root_logger.removeHandler(handler)
    for handler in original_handlers:
        if handler not in root_logger.handlers:
            root_logger.addHandler(handler)
    root_logger.setLevel(original_level)


# ============================================================================
# 6. TEST HELPERS
# ============================================================================

def skip_if_no_redis():
    """Decorator to skip tests if Redis is not available."""
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, socket_connect_timeout=1)
        r.ping()
        return pytest.mark.skipif(False, reason="")
    except Exception:
        return pytest.mark.skip(reason="Redis not available")


def skip_if_no_llm():
    """Decorator to skip tests if LLM API is not configured."""
    api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('ANTHROPIC_API_KEY')
    return pytest.mark.skipif(not api_key, reason="LLM API key not configured")


# ============================================================================
# END OF CONFTEST
# ============================================================================
