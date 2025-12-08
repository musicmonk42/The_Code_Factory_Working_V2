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
    elif name == 'prometheus_client':
        # prometheus_client needs specific classes and submodules
        # Create REGISTRY object
        class MockRegistry:
            def __init__(self):
                self._names_to_collectors = {}
            def register(self, *args, **kwargs):
                pass
            def unregister(self, *args, **kwargs):
                pass
            def collect(self):
                return []
        
        mock_module.REGISTRY = MockRegistry()
        
        # Create CollectorRegistry class
        class CollectorRegistry:
            def __init__(self, *args, **kwargs):
                self._names_to_collectors = {}
            def register(self, *args, **kwargs):
                pass
            def unregister(self, *args, **kwargs):
                pass
            def collect(self):
                return []
        
        mock_module.CollectorRegistry = CollectorRegistry
        
        # Create metric classes
        class Counter(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def inc(self, *args, **kwargs):
                pass
            def labels(self, *args, **kwargs):
                return self
        
        class Gauge(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def set(self, *args, **kwargs):
                pass
            def inc(self, *args, **kwargs):
                pass
            def dec(self, *args, **kwargs):
                pass
            def labels(self, *args, **kwargs):
                return self
        
        class Histogram(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def observe(self, *args, **kwargs):
                pass
            def labels(self, *args, **kwargs):
                return self
            def time(self):
                from contextlib import nullcontext
                return nullcontext()
        
        class Summary(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def observe(self, *args, **kwargs):
                pass
            def labels(self, *args, **kwargs):
                return self
            def time(self):
                from contextlib import nullcontext
                return nullcontext()
        
        mock_module.Counter = Counter
        mock_module.Gauge = Gauge
        mock_module.Histogram = Histogram
        mock_module.Summary = Summary
        mock_module.start_http_server = lambda *args, **kwargs: None
        
        # Create core submodule
        core_module = ModuleType('prometheus_client.core')
        core_module.__file__ = '<mocked prometheus_client.core>'
        core_module.__path__ = []
        core_module.__spec__ = importlib.util.spec_from_loader('prometheus_client.core', loader=None)
        
        class HistogramMetricFamily(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def add_metric(self, *args, **kwargs):
                pass
        
        core_module.HistogramMetricFamily = HistogramMetricFamily
        core_module.__getattr__ = _mock_getattr
        mock_module.core = core_module
        sys.modules['prometheus_client.core'] = core_module
        
        # Create registry submodule
        registry_module = ModuleType('prometheus_client.registry')
        registry_module.__file__ = '<mocked prometheus_client.registry>'
        registry_module.__path__ = []
        registry_module.__spec__ = importlib.util.spec_from_loader('prometheus_client.registry', loader=None)
        registry_module.REGISTRY = MockRegistry()
        registry_module.CollectorRegistry = CollectorRegistry
        registry_module.__getattr__ = _mock_getattr
        mock_module.registry = registry_module
        sys.modules['prometheus_client.registry'] = registry_module
    elif name == 'aiohttp':
        # aiohttp needs ClientSession and related classes
        class ClientSession(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, *args, **kwargs):
                return MockCallable()
            async def post(self, *args, **kwargs):
                return MockCallable()
            async def close(self):
                pass
        
        class ClientTimeout:
            def __init__(self, *args, **kwargs):
                pass
        
        class Request(MockCallable):
            """Mock aiohttp Request for web server routes"""
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        class Response(MockCallable):
            """Mock aiohttp Response for web server routes"""
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        mock_module.ClientSession = ClientSession
        mock_module.ClientTimeout = ClientTimeout
        mock_module.ClientError = type('ClientError', (Exception,), {})
        
        # Create web_request submodule
        web_request_module = ModuleType('aiohttp.web_request')
        web_request_module.__file__ = '<mocked aiohttp.web_request>'
        web_request_module.__path__ = []
        web_request_module.__spec__ = importlib.util.spec_from_loader('aiohttp.web_request', loader=None)
        web_request_module.Request = Request
        web_request_module.__getattr__ = _mock_getattr
        mock_module.web_request = web_request_module
        sys.modules['aiohttp.web_request'] = web_request_module
        
        # Create web_response submodule
        web_response_module = ModuleType('aiohttp.web_response')
        web_response_module.__file__ = '<mocked aiohttp.web_response>'
        web_response_module.__path__ = []
        web_response_module.__spec__ = importlib.util.spec_from_loader('aiohttp.web_response', loader=None)
        web_response_module.Response = Response
        web_response_module.__getattr__ = _mock_getattr
        mock_module.web_response = web_response_module
        sys.modules['aiohttp.web_response'] = web_response_module
        
        # Create web module (parent of web_request and web_response)
        web_module = ModuleType('aiohttp.web')
        web_module.__file__ = '<mocked aiohttp.web>'
        web_module.__path__ = []
        web_module.__spec__ = importlib.util.spec_from_loader('aiohttp.web', loader=None)
        web_module.Request = Request
        web_module.Response = Response
        web_module.__getattr__ = _mock_getattr
        mock_module.web = web_module
        sys.modules['aiohttp.web'] = web_module
    elif name == 'redis':
        # redis needs Redis class and asyncio submodule
        class Redis(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            async def get(self, *args, **kwargs):
                return None
            async def set(self, *args, **kwargs):
                return True
            async def delete(self, *args, **kwargs):
                return True
            async def close(self):
                pass
            def pipeline(self, *args, **kwargs):
                return self
            async def execute(self):
                return []
        
        mock_module.Redis = Redis
        mock_module.from_url = lambda *args, **kwargs: Redis()
        
        # Create asyncio submodule
        asyncio_module = ModuleType('redis.asyncio')
        asyncio_module.__file__ = '<mocked redis.asyncio>'
        asyncio_module.__path__ = []
        asyncio_module.__spec__ = importlib.util.spec_from_loader('redis.asyncio', loader=None)
        asyncio_module.Redis = Redis
        asyncio_module.from_url = lambda *args, **kwargs: Redis()
        asyncio_module.__getattr__ = _mock_getattr
        mock_module.asyncio = asyncio_module
        sys.modules['redis.asyncio'] = asyncio_module
    elif name == 'pydantic':
        # pydantic needs specific classes
        from typing import Any
        
        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
            def dict(self, *args, **kwargs):
                return {}
            def model_dump(self, *args, **kwargs):
                return {}
            def model_validate(cls, *args, **kwargs):
                return cls()
            @classmethod
            def parse_obj(cls, obj):
                return cls()
        
        class Field:
            def __init__(self, *args, **kwargs):
                pass
        
        mock_module.BaseModel = BaseModel
        mock_module.Field = Field
        mock_module.field_validator = lambda *args, **kwargs: lambda f: f
        mock_module.model_validator = lambda *args, **kwargs: lambda f: f
        mock_module.root_validator = lambda *args, **kwargs: lambda f: f
        mock_module.validator = lambda *args, **kwargs: lambda f: f
        mock_module.__version__ = "2.10.6"
        mock_module.VERSION = "2.10.6"  # Some code checks VERSION instead of __version__
    elif name in ('pydantic_settings', 'pydantic-settings'):
        # pydantic_settings needs BaseSettings class
        class BaseSettings:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
            def dict(self, *args, **kwargs):
                return {}
            def model_dump(self, *args, **kwargs):
                return {}
        
        mock_module.BaseSettings = BaseSettings
    elif name == 'sqlalchemy':
        # sqlalchemy needs specific classes and orm submodule
        class Column(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        class String(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        class Integer(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        mock_module.Column = Column
        mock_module.String = String
        mock_module.Integer = Integer
        mock_module.create_engine = lambda *args, **kwargs: MockCallable()
        mock_module.__version__ = "2.0.0"
        
        # Create orm submodule
        orm_module = ModuleType('sqlalchemy.orm')
        orm_module.__file__ = '<mocked sqlalchemy.orm>'
        orm_module.__path__ = []
        orm_module.__spec__ = importlib.util.spec_from_loader('sqlalchemy.orm', loader=None)
        orm_module.sessionmaker = lambda *args, **kwargs: MockCallable()
        orm_module.declarative_base = lambda *args, **kwargs: MockCallable()
        orm_module.Session = MockCallable
        orm_module.__getattr__ = _mock_getattr
        mock_module.orm = orm_module
        sys.modules['sqlalchemy.orm'] = orm_module
    elif name == 'fastapi':
        # fastapi needs FastAPI class and testclient submodule
        class FastAPI(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def get(self, *args, **kwargs):
                return lambda f: f
            def post(self, *args, **kwargs):
                return lambda f: f
            def put(self, *args, **kwargs):
                return lambda f: f
            def delete(self, *args, **kwargs):
                return lambda f: f
            def add_middleware(self, *args, **kwargs):
                pass
        
        class HTTPException(Exception):
            def __init__(self, status_code, detail=""):
                self.status_code = status_code
                self.detail = detail
        
        mock_module.FastAPI = FastAPI
        mock_module.HTTPException = HTTPException
        
        # Create testclient submodule
        testclient_module = ModuleType('fastapi.testclient')
        testclient_module.__file__ = '<mocked fastapi.testclient>'
        testclient_module.__path__ = []
        testclient_module.__spec__ = importlib.util.spec_from_loader('fastapi.testclient', loader=None)
        
        class TestClient(MockCallable):
            def __init__(self, app, *args, **kwargs):
                super().__init__()
                self.app = app
            def get(self, *args, **kwargs):
                return MockCallable()
            def post(self, *args, **kwargs):
                return MockCallable()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        
        testclient_module.TestClient = TestClient
        testclient_module.__getattr__ = _mock_getattr
        mock_module.testclient = testclient_module
        sys.modules['fastapi.testclient'] = testclient_module
        
        # Create security submodule
        security_module = ModuleType('fastapi.security')
        security_module.__file__ = '<mocked fastapi.security>'
        security_module.__path__ = []
        security_module.__spec__ = importlib.util.spec_from_loader('fastapi.security', loader=None)
        
        class HTTPBearer(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        class HTTPAuthorizationCredentials(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
        
        security_module.HTTPBearer = HTTPBearer
        security_module.HTTPAuthorizationCredentials = HTTPAuthorizationCredentials
        security_module.__getattr__ = _mock_getattr
        mock_module.security = security_module
        sys.modules['fastapi.security'] = security_module
    elif name in ('pytest_asyncio', 'pytest-asyncio'):
        # pytest_asyncio needs fixture decorator
        mock_module.fixture = lambda *args, **kwargs: lambda f: f
    elif name == 'faker':
        # faker needs Faker class
        class Faker(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def name(self):
                return "John Doe"
            def email(self):
                return "john.doe@example.com"
            def address(self):
                return "123 Main St"
            def text(self):
                return "Lorem ipsum dolor sit amet"
        
        mock_module.Faker = Faker
    elif name == 'tenacity':
        # tenacity needs retry decorator and related functions
        # Create a mock retry condition that supports the | operator
        class MockRetryCondition:
            def __init__(self, *args, **kwargs):
                pass
            def __or__(self, other):
                return MockRetryCondition()
            def __call__(self, *args, **kwargs):
                return False
        
        def retry(*args, **kwargs):
            return lambda f: f
        
        mock_module.retry = retry
        mock_module.stop_after_attempt = lambda *args, **kwargs: None
        mock_module.wait_exponential = lambda *args, **kwargs: None
        mock_module.retry_if_exception_type = lambda *args, **kwargs: MockRetryCondition()
        mock_module.before_sleep_log = lambda *args, **kwargs: None
        mock_module.after_log = lambda *args, **kwargs: None
        
        # Add exception classes that should be proper exceptions
        class RetryError(Exception):
            """Raised when all retry attempts have failed."""
            pass
        class TryAgain(Exception):
            """Signal to retry the operation."""
            pass
        mock_module.RetryError = RetryError
        mock_module.TryAgain = TryAgain
    elif name == 'httpx':
        # httpx needs AsyncClient and related classes
        class AsyncClient(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, *args, **kwargs):
                return MockCallable()
            async def post(self, *args, **kwargs):
                return MockCallable()
            async def close(self):
                pass
        
        mock_module.AsyncClient = AsyncClient
        mock_module.HTTPStatusError = type('HTTPStatusError', (Exception,), {})
    elif name == 'freezegun':
        # freezegun needs freeze_time decorator
        from contextlib import contextmanager
        
        @contextmanager
        def freeze_time(*args, **kwargs):
            yield
        
        mock_module.freeze_time = freeze_time
    elif name in ('grpc', 'grpcio'):
        # grpc needs aio submodule and various classes
        # Create aio submodule
        aio_module = ModuleType('grpc.aio')
        aio_module.__file__ = '<mocked grpc.aio>'
        aio_module.__path__ = []
        aio_module.__spec__ = importlib.util.spec_from_loader('grpc.aio', loader=None)
        
        class Channel(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def close(self):
                pass
        
        aio_module.insecure_channel = lambda *args, **kwargs: Channel()
        aio_module.__getattr__ = _mock_getattr
        mock_module.aio = aio_module
        sys.modules['grpc.aio'] = aio_module
    elif name == 'typer':
        # typer needs Typer class and related functions
        class Typer(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def command(self, *args, **kwargs):
                return lambda f: f
        
        mock_module.Typer = Typer
        mock_module.Option = lambda *args, **kwargs: None
        mock_module.Argument = lambda *args, **kwargs: None
    elif name == 'numpy':
        # numpy needs array and common functions
        class ndarray(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
                self.shape = ()
                self.dtype = None
            def __array__(self):
                return self
        
        mock_module.array = lambda *args, **kwargs: ndarray()
        mock_module.zeros = lambda *args, **kwargs: ndarray()
        mock_module.ones = lambda *args, **kwargs: ndarray()
        mock_module.ndarray = ndarray
        mock_module.mean = lambda *args, **kwargs: 0.0
        mock_module.median = lambda *args, **kwargs: 0.0
        mock_module.std = lambda *args, **kwargs: 0.0
        mock_module.percentile = lambda *args, **kwargs: 0.0
        mock_module.__version__ = "1.26.0"
    elif name == 'hypothesis':
        # hypothesis needs strategies submodule
        # Create strategies submodule
        strategies_module = ModuleType('hypothesis.strategies')
        strategies_module.__file__ = '<mocked hypothesis.strategies>'
        strategies_module.__path__ = []
        strategies_module.__spec__ = importlib.util.spec_from_loader('hypothesis.strategies', loader=None)
        strategies_module.text = lambda *args, **kwargs: MockCallable()
        strategies_module.dictionaries = lambda *args, **kwargs: MockCallable()
        strategies_module.integers = lambda *args, **kwargs: MockCallable()
        strategies_module.lists = lambda *args, **kwargs: MockCallable()
        strategies_module.__getattr__ = _mock_getattr
        mock_module.strategies = strategies_module
        sys.modules['hypothesis.strategies'] = strategies_module
        
        # Add common hypothesis decorators
        mock_module.given = lambda *args, **kwargs: lambda f: f
        mock_module.settings = lambda *args, **kwargs: lambda f: f
    elif name == 'docutils':
        # docutils needs core submodule
        # Create core submodule
        core_module = ModuleType('docutils.core')
        core_module.__file__ = '<mocked docutils.core>'
        core_module.__path__ = []
        core_module.__spec__ = importlib.util.spec_from_loader('docutils.core', loader=None)
        core_module.publish_doctree = lambda *args, **kwargs: None
        core_module.publish_string = lambda *args, **kwargs: b''
        core_module.publish_parts = lambda *args, **kwargs: {}
        core_module.__getattr__ = _mock_getattr
        mock_module.core = core_module
        sys.modules['docutils.core'] = core_module
    elif name == 'nltk':
        # nltk needs sentiment submodule
        # Create sentiment submodule
        sentiment_module = ModuleType('nltk.sentiment')
        sentiment_module.__file__ = '<mocked nltk.sentiment>'
        sentiment_module.__path__ = []
        sentiment_module.__spec__ = importlib.util.spec_from_loader('nltk.sentiment', loader=None)
        
        # Create vader_lexicon submodule
        vader_module = ModuleType('nltk.sentiment.vader')
        vader_module.__file__ = '<mocked nltk.sentiment.vader>'
        vader_module.__spec__ = importlib.util.spec_from_loader('nltk.sentiment.vader', loader=None)
        
        class SentimentIntensityAnalyzer(MockCallable):
            def __init__(self, *args, **kwargs):
                super().__init__()
            def polarity_scores(self, text):
                return {'neg': 0.0, 'neu': 1.0, 'pos': 0.0, 'compound': 0.0}
        
        vader_module.SentimentIntensityAnalyzer = SentimentIntensityAnalyzer
        vader_module.__getattr__ = _mock_getattr
        sentiment_module.vader = vader_module
        sentiment_module.__getattr__ = _mock_getattr
        mock_module.sentiment = sentiment_module
        sys.modules['nltk.sentiment'] = sentiment_module
        sys.modules['nltk.sentiment.vader'] = vader_module
        
        # Create tokenize submodule
        tokenize_module = ModuleType('nltk.tokenize')
        tokenize_module.__file__ = '<mocked nltk.tokenize>'
        tokenize_module.__path__ = []
        tokenize_module.__spec__ = importlib.util.spec_from_loader('nltk.tokenize', loader=None)
        tokenize_module.word_tokenize = lambda text: text.split()
        tokenize_module.sent_tokenize = lambda text: [text]
        tokenize_module.__getattr__ = _mock_getattr
        mock_module.tokenize = tokenize_module
        sys.modules['nltk.tokenize'] = tokenize_module
    
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
    'aiohttp',  # Async HTTP client
    'prometheus_client',  # Prometheus metrics
    'aiokafka',  # Async Kafka client
    'kafka',  # Kafka client
    'redis',  # Redis client
    'sqlalchemy',  # SQL toolkit
    'pydantic',  # Data validation
    'pydantic_core',  # Pydantic core
    'pydantic-settings',  # Pydantic settings
    'pydantic_settings',  # Pydantic settings (alternate import name)
    'pytest_asyncio',  # Pytest async support
    'pytest-asyncio',  # Pytest async support (alternate name)
    'grpc',  # gRPC
    'grpcio',  # gRPC IO
    'fastapi',  # FastAPI framework
    'uvicorn',  # ASGI server
    'faker',  # Fake data generator
    'httpx',  # HTTP client
    'tenacity',  # Retry library
    'freezegun',  # Time mocking library
    'typer',  # CLI library
    'numpy',  # Numerical computing
    'docutils',  # Documentation utilities (RST parsing)
    'nltk',  # Natural Language Toolkit
    'beautifulsoup4',  # HTML parsing
    'bs4',  # BeautifulSoup alias
    'git',  # GitPython
    'gitpython',  # GitPython alternate name
    'filelock',  # File locking
    'sphinx',  # Documentation generator
    'lxml',  # XML/HTML parser
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
        import importlib.util
        
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
        trace_module.__path__ = []
        trace_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.trace', loader=None)
        trace_module.get_tracer = lambda *args, **kwargs: _NoOpTracer()
        trace_module.get_current_span = lambda: _NoOpSpan()
        trace_module.get_tracer_provider = lambda: None
        trace_module.set_tracer_provider = lambda *args, **kwargs: None
        trace_module.Status = Status
        trace_module.StatusCode = StatusCode
        
        # Create trace.status submodule
        trace_status_module = ModuleType('opentelemetry.trace.status')
        trace_status_module.__file__ = '<mocked opentelemetry.trace.status>'
        trace_status_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.trace.status', loader=None)
        trace_status_module.Status = Status
        trace_status_module.StatusCode = StatusCode
        trace_module.status = trace_status_module
        
        # Create metrics module
        class _NoOpMeter:
            def create_counter(self, *args, **kwargs):
                class _NoOpCounter:
                    def add(self, *args, **kwargs):
                        pass
                return _NoOpCounter()
            def create_histogram(self, *args, **kwargs):
                class _NoOpHistogram:
                    def record(self, *args, **kwargs):
                        pass
                return _NoOpHistogram()
        
        metrics_module = ModuleType('opentelemetry.metrics')
        metrics_module.__file__ = '<mocked opentelemetry.metrics>'
        metrics_module.__path__ = []
        metrics_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.metrics', loader=None)
        metrics_module.get_meter = lambda *args, **kwargs: _NoOpMeter()
        metrics_module.get_meter_provider = lambda: None
        metrics_module.set_meter_provider = lambda *args, **kwargs: None
        
        # Create main opentelemetry module
        otel_module = ModuleType('opentelemetry')
        otel_module.__file__ = '<mocked opentelemetry>'
        otel_module.__path__ = []
        otel_module.__spec__ = importlib.util.spec_from_loader('opentelemetry', loader=None)
        otel_module.trace = trace_module
        otel_module.metrics = metrics_module
        
        # Create instrumentation module
        instrumentation_module = ModuleType('opentelemetry.instrumentation')
        instrumentation_module.__file__ = '<mocked opentelemetry.instrumentation>'
        instrumentation_module.__path__ = []  # This is required for submodule imports
        instrumentation_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation', loader=None)
        otel_module.instrumentation = instrumentation_module
        
        # Create common instrumentation submodules
        instrumentation_fastapi = ModuleType('opentelemetry.instrumentation.fastapi')
        instrumentation_fastapi.__file__ = '<mocked opentelemetry.instrumentation.fastapi>'
        instrumentation_fastapi.__path__ = []
        instrumentation_fastapi.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.fastapi', loader=None)
        
        class FastAPIInstrumentor:
            @classmethod
            def instrument_app(cls, *args, **kwargs):
                pass
        
        instrumentation_fastapi.FastAPIInstrumentor = FastAPIInstrumentor
        
        # Create grpc instrumentation module
        instrumentation_grpc = ModuleType('opentelemetry.instrumentation.grpc')
        instrumentation_grpc.__file__ = '<mocked opentelemetry.instrumentation.grpc>'
        instrumentation_grpc.__path__ = []
        instrumentation_grpc.__spec__ = importlib.util.spec_from_loader('opentelemetry.instrumentation.grpc', loader=None)
        
        class GrpcAioInstrumentor:
            @classmethod
            def instrument(cls, *args, **kwargs):
                pass
        
        instrumentation_grpc.GrpcAioInstrumentor = GrpcAioInstrumentor
        
        # Create sdk modules
        sdk_module = ModuleType('opentelemetry.sdk')
        sdk_module.__file__ = '<mocked opentelemetry.sdk>'
        sdk_module.__path__ = []  # Parent module for submodules
        sdk_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.sdk', loader=None)
        otel_module.sdk = sdk_module
        
        sdk_trace_module = ModuleType('opentelemetry.sdk.trace')
        sdk_trace_module.__file__ = '<mocked opentelemetry.sdk.trace>'
        sdk_trace_module.__path__ = []  # Parent module for submodules
        sdk_trace_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.sdk.trace', loader=None)
        sdk_trace_module.TracerProvider = lambda *args, **kwargs: None
        sdk_module.trace = sdk_trace_module
        
        sdk_trace_export_module = ModuleType('opentelemetry.sdk.trace.export')
        sdk_trace_export_module.__file__ = '<mocked opentelemetry.sdk.trace.export>'
        sdk_trace_export_module.__path__ = []
        sdk_trace_export_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.sdk.trace.export', loader=None)
        sdk_trace_export_module.ConsoleSpanExporter = lambda *args, **kwargs: None
        sdk_trace_export_module.SimpleSpanProcessor = lambda *args, **kwargs: None
        sdk_trace_export_module.BatchSpanProcessor = lambda *args, **kwargs: None
        sdk_trace_module.export = sdk_trace_export_module
        
        sdk_resources_module = ModuleType('opentelemetry.sdk.resources')
        sdk_resources_module.__file__ = '<mocked opentelemetry.sdk.resources>'
        sdk_resources_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.sdk.resources', loader=None)
        sdk_resources_module.Resource = lambda **kwargs: None
        sdk_module.resources = sdk_resources_module
        
        # Create exporter modules
        exporter_module = ModuleType('opentelemetry.exporter')
        exporter_module.__file__ = '<mocked opentelemetry.exporter>'
        exporter_module.__path__ = []
        exporter_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter', loader=None)
        otel_module.exporter = exporter_module
        
        exporter_jaeger_module = ModuleType('opentelemetry.exporter.jaeger')
        exporter_jaeger_module.__file__ = '<mocked opentelemetry.exporter.jaeger>'
        exporter_jaeger_module.__path__ = []
        exporter_jaeger_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter.jaeger', loader=None)
        exporter_module.jaeger = exporter_jaeger_module
        
        exporter_jaeger_thrift_module = ModuleType('opentelemetry.exporter.jaeger.thrift')
        exporter_jaeger_thrift_module.__file__ = '<mocked opentelemetry.exporter.jaeger.thrift>'
        exporter_jaeger_thrift_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter.jaeger.thrift', loader=None)
        exporter_jaeger_thrift_module.JaegerExporter = lambda *args, **kwargs: None
        exporter_jaeger_module.thrift = exporter_jaeger_thrift_module
        
        exporter_otlp_module = ModuleType('opentelemetry.exporter.otlp')
        exporter_otlp_module.__file__ = '<mocked opentelemetry.exporter.otlp>'
        exporter_otlp_module.__path__ = []
        exporter_otlp_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter.otlp', loader=None)
        exporter_module.otlp = exporter_otlp_module
        
        exporter_otlp_proto_module = ModuleType('opentelemetry.exporter.otlp.proto')
        exporter_otlp_proto_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto>'
        exporter_otlp_proto_module.__path__ = []
        exporter_otlp_proto_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter.otlp.proto', loader=None)
        exporter_otlp_module.proto = exporter_otlp_proto_module
        
        exporter_otlp_proto_grpc_module = ModuleType('opentelemetry.exporter.otlp.proto.grpc')
        exporter_otlp_proto_grpc_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.grpc>'
        exporter_otlp_proto_grpc_module.__path__ = []
        exporter_otlp_proto_grpc_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter.otlp.proto.grpc', loader=None)
        exporter_otlp_proto_module.grpc = exporter_otlp_proto_grpc_module
        
        exporter_otlp_proto_grpc_trace_exporter_module = ModuleType('opentelemetry.exporter.otlp.proto.grpc.trace_exporter')
        exporter_otlp_proto_grpc_trace_exporter_module.__file__ = '<mocked opentelemetry.exporter.otlp.proto.grpc.trace_exporter>'
        exporter_otlp_proto_grpc_trace_exporter_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.exporter.otlp.proto.grpc.trace_exporter', loader=None)
        exporter_otlp_proto_grpc_trace_exporter_module.OTLPSpanExporter = lambda *args, **kwargs: None
        exporter_otlp_proto_grpc_module.trace_exporter = exporter_otlp_proto_grpc_trace_exporter_module
        
        sdk_trace_sampling_module = ModuleType('opentelemetry.sdk.trace.sampling')
        sdk_trace_sampling_module.__file__ = '<mocked opentelemetry.sdk.trace.sampling>'
        sdk_trace_sampling_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.sdk.trace.sampling', loader=None)
        sdk_trace_sampling_module.ParentBased = lambda *args, **kwargs: None
        sdk_trace_sampling_module.TraceIdRatioBased = lambda *args, **kwargs: None
        sdk_trace_sampling_module.ALWAYS_ON = lambda *args, **kwargs: None
        sdk_trace_module.sampling = sdk_trace_sampling_module
        
        # Create propagate module
        propagate_module = ModuleType('opentelemetry.propagate')
        propagate_module.__file__ = '<mocked opentelemetry.propagate>'
        propagate_module.__path__ = []
        propagate_module.__spec__ = importlib.util.spec_from_loader('opentelemetry.propagate', loader=None)
        propagate_module.extract = lambda *args, **kwargs: {}
        propagate_module.inject = lambda *args, **kwargs: None
        propagate_module.get_global_textmap = lambda *args, **kwargs: None
        propagate_module.set_global_textmap = lambda *args, **kwargs: None
        otel_module.propagate = propagate_module
        
        # Register modules
        sys.modules['opentelemetry'] = otel_module
        sys.modules['opentelemetry.trace'] = trace_module
        sys.modules['opentelemetry.trace.status'] = trace_status_module
        sys.modules['opentelemetry.metrics'] = metrics_module
        sys.modules['opentelemetry.propagate'] = propagate_module
        sys.modules['opentelemetry.instrumentation'] = instrumentation_module
        sys.modules['opentelemetry.instrumentation.fastapi'] = instrumentation_fastapi
        sys.modules['opentelemetry.instrumentation.grpc'] = instrumentation_grpc
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
