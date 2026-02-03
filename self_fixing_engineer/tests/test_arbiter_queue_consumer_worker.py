"""
Test suite for queue_consumer_worker.py
Focuses on critical functionality: message processing, poison detection, and health checks.
"""

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Store original modules for restoration
_ORIGINAL_MODULES = {}
_MOCKED_MODULE_NAMES = [
    "prometheus_client",
    "tenacity",
    "aiohttp",
    "aiohttp.web",
    "opentelemetry",
    "opentelemetry.exporter",
    "opentelemetry.exporter.otlp",
    "opentelemetry.exporter.otlp.proto",
    "opentelemetry.exporter.otlp.proto.grpc",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.trace",
    "opentelemetry.sdk",
    "opentelemetry.sdk.trace",
    "opentelemetry.sdk.trace.export",
    "opentelemetry.sdk.resources",
    "opentelemetry.instrumentation",
    "opentelemetry.instrumentation.requests",
    "opentelemetry.propagate",
    "opentelemetry.metrics",
    "self_fixing_engineer.arbiter.config",
    "self_fixing_engineer.arbiter.message_queue_service",
    "self_fixing_engineer.arbiter.bug_manager",
    "self_fixing_engineer.arbiter.logging_utils",
    "arbiter_plugin_registry",
]

# Save original modules before mocking
for mod_name in _MOCKED_MODULE_NAMES:
    if mod_name in sys.modules:
        _ORIGINAL_MODULES[mod_name] = sys.modules[mod_name]


# Setup mock modules BEFORE any imports
def setup_test_environment():
    """Setup all mocks before importing the module under test."""
    from unittest.mock import MagicMock

    # Mock Prometheus metrics
    class MockMetric:
        def __init__(self, *args, **kwargs):
            self.labels_mock = Mock(
                return_value=Mock(inc=Mock(), observe=Mock(), set=Mock())
            )

        def labels(self, *args, **kwargs):
            return self.labels_mock(*args, **kwargs)

    mock_prometheus = MagicMock()
    mock_prometheus.__path__ = []  # Required for package imports
    mock_prometheus.__name__ = "prometheus_client"
    mock_prometheus.__file__ = "<mocked prometheus_client>"
    mock_prometheus.Counter = MockMetric
    mock_prometheus.Histogram = MockMetric
    mock_prometheus.Gauge = MockMetric
    mock_prometheus.REGISTRY = MagicMock()
    mock_prometheus.REGISTRY._names_to_collectors = {}
    mock_prometheus.start_http_server = Mock()
    sys.modules["prometheus_client"] = mock_prometheus

    # Mock tenacity
    mock_tenacity = MagicMock()
    mock_tenacity.retry = lambda **k: lambda f: f
    mock_tenacity.stop_after_attempt = lambda n: None
    mock_tenacity.wait_exponential = lambda **k: None
    mock_tenacity.RetryError = Exception  # Add RetryError as a real exception class
    sys.modules["tenacity"] = mock_tenacity

    # Mock aiohttp
    if "aiohttp" not in sys.modules or isinstance(
        sys.modules.get("aiohttp"), MagicMock
    ):
        sys.modules["aiohttp"] = MagicMock()
    if "aiohttp.web" not in sys.modules or isinstance(
        sys.modules.get("aiohttp.web"), MagicMock
    ):
        sys.modules["aiohttp.web"] = MagicMock()

    # Mock OpenTelemetry modules with proper __path__ attribute
    def create_mock_module(name):
        """Create a mock module with __path__ attribute."""
        module = ModuleType(name)
        module.__path__ = []
        module.__file__ = f"<mock {name}>"
        return module

    # Build the OpenTelemetry module hierarchy
    opentelemetry = create_mock_module("opentelemetry")
    opentelemetry.exporter = create_mock_module("opentelemetry.exporter")
    opentelemetry.exporter.otlp = create_mock_module("opentelemetry.exporter.otlp")
    opentelemetry.exporter.otlp.proto = create_mock_module(
        "opentelemetry.exporter.otlp.proto"
    )
    opentelemetry.exporter.otlp.proto.grpc = create_mock_module(
        "opentelemetry.exporter.otlp.proto.grpc"
    )
    opentelemetry.exporter.otlp.proto.grpc.trace_exporter = create_mock_module(
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
    )

    # Add the mock OTLPSpanExporter class
    opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter = MagicMock

    # Register all the modules in sys.modules
    sys.modules["opentelemetry"] = opentelemetry
    sys.modules["opentelemetry.exporter"] = opentelemetry.exporter
    sys.modules["opentelemetry.exporter.otlp"] = opentelemetry.exporter.otlp
    sys.modules["opentelemetry.exporter.otlp.proto"] = opentelemetry.exporter.otlp.proto
    sys.modules["opentelemetry.exporter.otlp.proto.grpc"] = (
        opentelemetry.exporter.otlp.proto.grpc
    )
    sys.modules["opentelemetry.exporter.otlp.proto.grpc.trace_exporter"] = (
        opentelemetry.exporter.otlp.proto.grpc.trace_exporter
    )

    # Mock other OpenTelemetry modules
    opentelemetry.trace = MagicMock()
    opentelemetry.trace.set_tracer_provider = MagicMock()
    opentelemetry.trace.get_tracer = MagicMock(return_value=MagicMock())
    sys.modules["opentelemetry.trace"] = opentelemetry.trace

    opentelemetry.sdk = create_mock_module("opentelemetry.sdk")
    opentelemetry.sdk.trace = MagicMock()
    opentelemetry.sdk.trace.TracerProvider = MagicMock
    opentelemetry.sdk.trace.export = MagicMock()
    opentelemetry.sdk.trace.export.BatchSpanProcessor = MagicMock
    sys.modules["opentelemetry.sdk"] = opentelemetry.sdk
    sys.modules["opentelemetry.sdk.trace"] = opentelemetry.sdk.trace
    sys.modules["opentelemetry.sdk.trace.export"] = opentelemetry.sdk.trace.export

    opentelemetry.sdk.resources = MagicMock()
    # Mock Resource class with create method
    mock_resource_class = MagicMock()
    mock_resource_class.create = MagicMock(return_value=MagicMock())
    opentelemetry.sdk.resources.Resource = mock_resource_class
    sys.modules["opentelemetry.sdk.resources"] = opentelemetry.sdk.resources

    opentelemetry.instrumentation = create_mock_module("opentelemetry.instrumentation")
    opentelemetry.instrumentation.requests = MagicMock()
    opentelemetry.instrumentation.requests.RequestsInstrumentor = MagicMock
    sys.modules["opentelemetry.instrumentation"] = opentelemetry.instrumentation
    sys.modules["opentelemetry.instrumentation.requests"] = (
        opentelemetry.instrumentation.requests
    )

    opentelemetry.propagate = MagicMock()
    opentelemetry.propagate.get_global_textmap = MagicMock(return_value=MagicMock())
    sys.modules["opentelemetry.propagate"] = opentelemetry.propagate

    opentelemetry.metrics = MagicMock()
    opentelemetry.metrics.get_meter = MagicMock(return_value=MagicMock())
    sys.modules["opentelemetry.metrics"] = opentelemetry.metrics


# Setup environment before any imports
setup_test_environment()

# Now we need to mock the arbiter modules BEFORE importing queue_consumer_worker
# The key is that when SFE_CORE_AVAILABLE is True, Settings is used as a CLASS not an instance
# So we need to provide a class that has LOG_LEVEL as a class attribute


class MockArbiterConfig:
    """Mock ArbiterConfig that behaves like the real settings class."""

    LOG_LEVEL = "INFO"
    MQ_BACKEND_TYPE = "mock"
    REDIS_URL = "redis://localhost:6379"
    KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
    ENCRYPTION_KEY_BYTES = b""
    MQ_TOPIC_PREFIX = "mock_events"
    MQ_DLQ_TOPIC_SUFFIX = "dlq"
    MQ_MAX_RETRIES = 3
    MQ_RETRY_DELAY_BASE = 0.5
    MQ_CONSUMER_GROUP_ID = "mock_consumer_group"
    MQ_KAFKA_PRODUCER_ACKS = "all"
    MQ_KAFKA_PRODUCER_RETRIES = 3
    MQ_KAFKA_CONSUMER_AUTO_OFFSET_RESET = "earliest"
    MQ_KAFKA_CONSUMER_ENABLE_AUTO_COMMIT = True
    MQ_KAFKA_CONSUMER_AUTO_COMMIT_INTERVAL_MS = 1000
    MQ_REDIS_STREAM_MAXLEN = 1000
    MQ_REDIS_STREAM_TRIM_STRATEGY = "~"
    MQ_POISON_MESSAGE_THRESHOLD = 5
    MQ_CONSUMER_CONCURRENCY = 10
    PROMETHEUS_PORT = 9090
    HEALTH_PORT = 8080
    CRITICAL_EVENTS_FOR_MQ = ["test_event"]
    SLACK_WEBHOOK_URL = None


# Mock arbiter.config module
mock_config_module = MagicMock()
mock_config_module.ArbiterConfig = MockArbiterConfig
sys.modules["self_fixing_engineer.arbiter.config"] = mock_config_module

# Mock other arbiter modules
sys.modules["self_fixing_engineer.arbiter.message_queue_service"] = MagicMock()
sys.modules["self_fixing_engineer.arbiter.message_queue_service"].MessageQueueService = AsyncMock
sys.modules["self_fixing_engineer.arbiter.message_queue_service"].MessageQueueServiceError = Exception
sys.modules["self_fixing_engineer.arbiter.message_queue_service"].SerializationError = Exception
sys.modules["self_fixing_engineer.arbiter.message_queue_service"].DecryptionError = Exception

sys.modules["self_fixing_engineer.arbiter.bug_manager"] = MagicMock()
sys.modules["self_fixing_engineer.arbiter.bug_manager"].AuditLogManager = AsyncMock

sys.modules["self_fixing_engineer.arbiter.logging_utils"] = MagicMock()
sys.modules["self_fixing_engineer.arbiter.logging_utils"].PIIRedactorFilter = MagicMock

sys.modules["arbiter_plugin_registry"] = MagicMock()
sys.modules["arbiter_plugin_registry"].registry = MagicMock()
sys.modules["arbiter_plugin_registry"].PlugInKind = MagicMock()
sys.modules["arbiter_plugin_registry"].PlugInKind.CORE_SERVICE = "core_service"

# Now import the actual module - it should work with proper mocks
import self_fixing_engineer.arbiter.queue_consumer_worker as queue_consumer_worker

# After import, we need to ensure the module state is correct
# Since SFE_CORE_AVAILABLE will be True (imports succeeded), we need to mock accordingly
queue_consumer_worker.SFE_CORE_AVAILABLE = False  # Force it to use mock mode for tests


def _restore_original_modules():
    """Restore original modules that were patched during test import."""
    for mod_name in _MOCKED_MODULE_NAMES:
        if mod_name in _ORIGINAL_MODULES:
            sys.modules[mod_name] = _ORIGINAL_MODULES[mod_name]
        elif mod_name in sys.modules:
            # Check if it's our mock (MagicMock or ModuleType we created)
            module = sys.modules[mod_name]
            if isinstance(module, MagicMock) or (
                isinstance(module, ModuleType)
                and hasattr(module, "__file__")
                and module.__file__
                and "<mock" in str(module.__file__)
            ):
                del sys.modules[mod_name]


@pytest.fixture(scope="module", autouse=True)
def cleanup_mocked_modules():
    """Restore original modules when this test module finishes."""
    yield
    _restore_original_modules()


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module state before each test."""
    queue_consumer_worker._EXTERNAL_NOTIFIER_HANDLERS.clear()
    queue_consumer_worker.mq_service_instance = None
    queue_consumer_worker.audit_logger_instance = None
    queue_consumer_worker.shutdown_event.clear()
    queue_consumer_worker.SFE_CORE_AVAILABLE = False  # Ensure mock mode
    yield


@pytest.fixture
def mock_mq_service():
    """Mock message queue service."""
    service = AsyncMock()
    service.redis_client = AsyncMock()
    service.redis_client.get = AsyncMock(return_value=None)
    service.redis_client.incr = AsyncMock()
    service.redis_client.delete = AsyncMock()
    service.redis_client.expire = AsyncMock()
    service._send_to_dlq = AsyncMock()
    service.healthcheck = AsyncMock(return_value={"status": "healthy"})
    service.connect = AsyncMock()
    service.disconnect = AsyncMock()
    return service


@pytest.fixture
def mock_audit_logger():
    """Mock audit logger."""
    logger = AsyncMock()
    logger.audit = AsyncMock()
    logger.shutdown = AsyncMock()
    return logger


class TestRedactSensitive:
    """Test sensitive data redaction."""

    def test_redact_sensitive_keys(self):
        """Test that sensitive keys are properly redacted."""
        data = {
            "user_id": "123",
            "api_key": "secret",
            "password": "pass",
            "normal": "visible",
        }

        result = queue_consumer_worker.redact_sensitive(data)

        assert result["user_id"] == "123"
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"
        assert result["normal"] == "visible"

    def test_redact_nested_data(self):
        """Test nested sensitive data is redacted."""
        data = {"nested": {"token": "secret"}}
        result = queue_consumer_worker.redact_sensitive(data)
        assert result["nested"]["token"] == "[REDACTED]"

    def test_non_dict_passthrough(self):
        """Test non-dict values pass through unchanged."""
        assert queue_consumer_worker.redact_sensitive("string") == "string"
        assert queue_consumer_worker.redact_sensitive(123) == 123
        assert queue_consumer_worker.redact_sensitive(None) is None


class TestProcessEvent:
    """Test event processing logic."""

    @pytest.mark.asyncio
    async def test_successful_delivery(self, mock_mq_service, mock_audit_logger):
        """Test successful message delivery."""
        event_data = {"event_id": "123", "correlation_id": "abc"}

        with patch.object(
            queue_consumer_worker, "send_to_external_notifier", return_value=True
        ) as mock_send:
            await queue_consumer_worker.process_event(
                "test_event", event_data, mock_mq_service, mock_audit_logger
            )

            # Verify the message was processed successfully
            mock_send.assert_called_once()
            mock_mq_service.redis_client.delete.assert_called_once()
            mock_mq_service._send_to_dlq.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_delivery(self, mock_mq_service, mock_audit_logger):
        """Test failed message delivery."""
        event_data = {"event_id": "456", "correlation_id": "def"}

        with patch.object(
            queue_consumer_worker, "send_to_external_notifier", return_value=False
        ) as mock_send:
            await queue_consumer_worker.process_event(
                "test_event", event_data, mock_mq_service, mock_audit_logger
            )

            # Verify retry counter was incremented and message sent to DLQ
            mock_send.assert_called_once()
            mock_mq_service.redis_client.incr.assert_called_once()
            mock_mq_service.redis_client.expire.assert_called_once()
            mock_mq_service._send_to_dlq.assert_called_once()

            # Verify audit log was called
            mock_audit_logger.audit.assert_called_with(
                "message_delivery_failed",
                {
                    "event_type": "test_event",
                    "message_id": "456",
                    "correlation_id": "def",
                    "reason": "external_delivery_failed",
                },
            )

    @pytest.mark.asyncio
    async def test_poison_message_detection(self, mock_mq_service, mock_audit_logger):
        """Test poison message is quarantined."""
        mock_mq_service.redis_client.get.return_value = b"5"  # At threshold
        event_data = {"event_id": "poison", "correlation_id": "xyz"}

        queue_consumer_worker.POISON_MESSAGE_THRESHOLD = 5

        await queue_consumer_worker.process_event(
            "test_event", event_data, mock_mq_service, mock_audit_logger
        )

        # Should not attempt delivery
        mock_mq_service.redis_client.incr.assert_not_called()

        # Should audit and send to DLQ
        mock_audit_logger.audit.assert_called_with(
            "poison_message_quarantined",
            {
                "event_type": "test_event",
                "message_id": "poison",
                "correlation_id": "xyz",
                "data_summary": str(queue_consumer_worker.redact_sensitive(event_data))[
                    :200
                ],
                "reason": "exceeded_poison_threshold",
            },
        )
        mock_mq_service._send_to_dlq.assert_called_with(
            "test_event", event_data, "Poison: retries exceeded"
        )


class TestExternalNotifiers:
    """Test external notification functionality."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self):
        """Test successful notification delivery."""
        mock_handler = MagicMock()
        mock_handler.audit_hook = AsyncMock()
        queue_consumer_worker._EXTERNAL_NOTIFIER_HANDLERS["test"] = mock_handler

        result = await queue_consumer_worker.send_to_external_notifier(
            "test_event", {"data": "test"}
        )

        assert result is True
        mock_handler.audit_hook.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_notification_failure(self):
        """Test notification delivery failure."""
        mock_handler = MagicMock()
        mock_handler.audit_hook = AsyncMock(side_effect=Exception("API error"))
        queue_consumer_worker._EXTERNAL_NOTIFIER_HANDLERS["test"] = mock_handler

        result = await queue_consumer_worker.send_to_external_notifier(
            "test_event", {"data": "test"}
        )

        assert result is False


class TestHealthCheck:
    """Test health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, mock_mq_service):
        """Test health check returns healthy status."""
        queue_consumer_worker.mq_service_instance = mock_mq_service

        request = MagicMock()
        response = await queue_consumer_worker.health_check_handler(request)

        assert response.status == 200
        data = json.loads(response.text)
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert data["mq_service_status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_degraded(self, mock_mq_service):
        """Test health check returns degraded when MQ unhealthy."""
        mock_mq_service.healthcheck.return_value = {"status": "unhealthy"}
        queue_consumer_worker.mq_service_instance = mock_mq_service

        request = MagicMock()
        response = await queue_consumer_worker.health_check_handler(request)

        assert response.status == 503
        data = json.loads(response.text)
        assert data["status"] == "degraded"


class TestHandleMessage:
    """Test message handling with concurrency control."""

    @pytest.mark.asyncio
    async def test_handle_message_calls_process(
        self, mock_mq_service, mock_audit_logger
    ):
        """Test handle_message calls process_event with semaphore."""
        event_data = {"test": "data"}

        with patch.object(
            queue_consumer_worker, "process_event", new_callable=AsyncMock
        ) as mock_process:
            await queue_consumer_worker.handle_message(
                "test_event", event_data, mock_mq_service, mock_audit_logger
            )

            mock_process.assert_called_once_with(
                "test_event", event_data, mock_mq_service, mock_audit_logger
            )


class TestQueueConsumerWorker:
    """Test the worker class."""

    @pytest.mark.asyncio
    async def test_worker_initialization(self):
        """Test worker initializes correctly in mock mode."""
        # Create a mock settings with required attributes
        mock_settings = MagicMock()
        mock_settings.PROMETHEUS_PORT = 9090
        mock_settings.HEALTH_PORT = 8080
        mock_settings.CRITICAL_EVENTS_FOR_MQ = []

        worker = queue_consumer_worker.QueueConsumerWorker(settings=mock_settings)

        # Mock web app components
        with patch("aiohttp.web.Application"):
            with patch("aiohttp.web.AppRunner") as mock_runner_class:
                mock_runner = AsyncMock()
                mock_runner_class.return_value = mock_runner

                with patch("aiohttp.web.TCPSite") as mock_site_class:
                    mock_site = AsyncMock()
                    mock_site_class.return_value = mock_site

                    async with worker:
                        assert worker.mq_service is not None
                        assert worker.audit_logger is not None
                        assert (
                            queue_consumer_worker.mq_service_instance
                            == worker.mq_service
                        )
                        assert (
                            queue_consumer_worker.audit_logger_instance
                            == worker.audit_logger
                        )

                        # Verify services were initialized
                        worker.mq_service.connect.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
