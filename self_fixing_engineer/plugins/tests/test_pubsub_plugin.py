import asyncio
import importlib
import json
import logging
import sys
import time
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.api_core import exceptions as google_exceptions
from google.cloud import pubsub_v1
from prometheus_client import CollectorRegistry
from pydantic import ValidationError

# Assuming these are available in a file named pubsub_plugin.py
# and we are mocking them for the purpose of testing this file in isolation.
# For a real test, these would be imported from the actual file.

PRODUCTION_MODE = False
logger = logging.getLogger(__name__)


class AnalyzerCriticalError(RuntimeError):
    pass


class NonCriticalError(Exception):
    pass


def alert_operator(message, level="CRITICAL"):
    pass


def scrub_sensitive_data(data):
    return data


class DummyAuditLogger:
    def log_event(self, *args, **kwargs):
        pass


audit_logger = DummyAuditLogger()
SECRETS_MANAGER = MagicMock()


class PubSubSettings:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    @classmethod
    def model_validate(cls, data):
        return cls(**data)


class PubSubMetrics:
    def __init__(self, registry=None):
        if registry is None:
            registry = CollectorRegistry(auto_describe=True)
        self.EVENTS_QUEUED = MagicMock()
        self.EVENTS_DROPPED = MagicMock()
        self.EVENTS_PUBLISHED_SUCCESS = MagicMock()
        self.EVENTS_FAILED_PERMANENTLY = MagicMock()
        self.PUBLISH_LATENCY = MagicMock()
        self.CIRCUIT_BREAKER_STATUS = MagicMock()
        self.QUEUE_SIZE = MagicMock()


class AuditEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    @classmethod
    def model_validate(cls, data):
        return cls(**data)

    def model_dump(self):
        return {}


class CircuitBreaker:
    def __init__(self, threshold, reset_seconds, metrics):
        self._is_open = False
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._metrics = metrics
        self._metrics.CIRCUIT_BREAKER_STATUS.set = MagicMock()
        self._metrics.CIRCUIT_BREAKER_STATUS.labels = MagicMock(
            return_value=self._metrics.CIRCUIT_BREAKER_STATUS
        )
        self._metrics.CIRCUIT_BREAKER_STATUS.labels().set(0)

    def check(self):
        if self._is_open:
            if time.monotonic() - self._last_failure_time > self._reset_seconds:
                self._is_open = False
                self._failure_count = 0
                self._metrics.CIRCUIT_BREAKER_STATUS.labels().set(0)
            else:
                raise ConnectionError("Circuit breaker is open")

    def record_failure(self):
        self._failure_count += 1
        if self._failure_count >= self._threshold and not self._is_open:
            self._is_open = True
            self._last_failure_time = time.monotonic()
            self._metrics.CIRCUIT_BREAKER_STATUS.labels().set(1)


class PubSubGateway:
    def __init__(self, settings, metrics):
        self.settings = PubSubSettings(**settings)
        self.metrics = metrics
        self._event_queue = asyncio.Queue(maxsize=self.settings.max_queue_size)
        self._publisher_client = None
        self._worker_task = None
        self._shutdown_event = asyncio.Event()

    async def startup(self):
        self._shutdown_event.clear()
        self._publisher_client = pubsub_v1.PublisherClient()
        self._worker_task = asyncio.create_task(self._worker())

    async def shutdown(self):
        self._shutdown_event.set()
        await self._event_queue.put(None)
        await self._worker_task
        await self._publisher_client.stop()

    def publish(self, event_name, service_name, details):
        if self._event_queue.full():
            raise RuntimeError("Queue is full")
        self._event_queue.put_nowait(
            AuditEvent(
                event_name=event_name, service_name=service_name, details=details
            )
        )

    async def _worker(self):
        while not self._shutdown_event.is_set():
            event = await self._event_queue.get()
            if event is None:
                break
            # Process event
            self._event_queue.task_done()

    async def _publish_batch(self, batch):
        if self.settings.dry_run:
            return

        for event in batch:
            self._publisher_client.publish(
                "topic_path", json.dumps(event.model_dump()).encode("utf-8")
            )


# --- Test Setup ---
@pytest.fixture(autouse=True)
def setup_logging():
    """Set up logging to capture output for tests."""
    logger.handlers = []
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield
    logger.handlers = []


@pytest.fixture
def mock_audit_logger():
    """Mock the audit logger to capture log events."""
    mock = MagicMock()
    with patch("pubsub_plugin.audit_logger", mock):
        yield mock


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    with patch("pubsub_plugin.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_scrub_sensitive_data():
    """Mock the scrub_sensitive_data function."""
    with patch("pubsub_plugin.scrub_sensitive_data") as mock:
        mock.side_effect = lambda x: x
        yield mock


@pytest.fixture
def mock_secrets_manager():
    """Mock the SECRETS_MANAGER."""
    mock = MagicMock()
    with patch("pubsub_plugin.SECRETS_MANAGER", mock):
        mock.get_secret.return_value = json.dumps(
            {"type": "service_account", "project_id": "test-project"}
        )
        yield mock


@pytest.fixture
def mock_pubsub_publisher(monkeypatch):
    """Mock PubSub PublisherClient."""
    mock_client = MagicMock(spec=pubsub_v1.PublisherClient)
    mock_publish = MagicMock()
    mock_get_topic = AsyncMock()
    mock_stop = AsyncMock()

    mock_future = MagicMock()
    mock_future.result = MagicMock(return_value="message_id")
    mock_publish.return_value = mock_future

    mock_client.publish = mock_publish
    mock_client.get_topic = mock_get_topic
    mock_client.stop = mock_stop

    with patch(
        "google.cloud.pubsub_v1.PublisherClient", return_value=mock_client
    ) as mock_class:
        yield mock_client, mock_publish, mock_get_topic, mock_stop, mock_class


@pytest.fixture
def mock_redis(monkeypatch):
    """Mock Redis client."""
    mock_redis = AsyncMock()
    monkeypatch.setattr("pubsub_plugin.REDIS_CLIENT", mock_redis)
    yield mock_redis


@pytest.fixture
def mock_tracer():
    """Mock OpenTelemetry tracer."""
    with patch("pubsub_plugin.tracer") as mock:
        yield mock


@pytest.fixture
def set_env(monkeypatch):
    """Fixture to set environment variables for tests."""

    def _set_env(vars: Dict[str, str]):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)

    return _set_env


@pytest.fixture
def sample_settings_dict():
    """Sample PubSubSettings dictionary for testing."""
    return {
        "project_id": "test-project",
        "topic_id": "test-topic",
        "batch_max_messages": 1000,
        "batch_max_bytes": 1024 * 1024,
        "batch_max_latency_sec": 0.5,
        "max_queue_size": 10000,
        "worker_batch_size": 500,
        "worker_linger_sec": 0.2,
        "max_publish_retries": 3,
        "circuit_breaker_threshold": 5,
        "circuit_breaker_reset_sec": 30,
        "dry_run": False,
        "allowed_project_ids": ["test-project"],
        "allowed_topic_ids": ["test-topic"],
        "gcp_credentials_secret_id": "GCP_CREDENTIALS",
    }


@pytest.fixture
def sample_metrics():
    """Sample PubSubMetrics instance."""
    return PubSubMetrics(CollectorRegistry())


# --- Settings Validation Tests ---
def test_pubsub_settings_success(sample_settings_dict):
    """Test successful PubSubSettings validation."""
    settings = PubSubSettings(**sample_settings_dict)
    assert settings.project_id == "test-project"
    assert settings.topic_id == "test-topic"


def test_pubsub_settings_invalid_project_id_prod(set_env, sample_settings_dict):
    """Test project ID not in allowlist fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["project_id"] = "forbidden-project"
    with pytest.raises(ValidationError, match="not in the 'allowed_project_ids' list"):
        PubSubSettings(**sample_settings_dict)


def test_pubsub_settings_invalid_topic_id_prod(set_env, sample_settings_dict):
    """Test topic ID not in allowlist fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["topic_id"] = "forbidden-topic"
    with pytest.raises(ValidationError, match="not in the 'allowed_topic_ids' list"):
        PubSubSettings(**sample_settings_dict)


def test_pubsub_settings_dry_run_prod(set_env, sample_settings_dict):
    """Test dry_run=True fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["dry_run"] = True
    with pytest.raises(ValidationError, match="'dry_run' must be False"):
        PubSubSettings(**sample_settings_dict)


def test_pubsub_settings_missing_credentials_prod(set_env, sample_settings_dict):
    """Test missing GCP credentials secret ID fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["gcp_credentials_secret_id"] = None
    with pytest.raises(
        ValidationError, match="'gcp_credentials_secret_id' must be provided"
    ):
        PubSubSettings(**sample_settings_dict)


def test_pubsub_settings_dummy_project_id_prod(set_env, sample_settings_dict):
    """Test dummy project ID fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["project_id"] = "test-dummy"
    with pytest.raises(ValidationError, match="Dummy/test Project ID"):
        PubSubSettings(**sample_settings_dict)


# --- Metrics Tests ---
def test_pubsub_metrics_init(sample_metrics):
    """Test PubSubMetrics initialization."""
    metrics = sample_metrics
    assert metrics.EVENTS_QUEUED is not None
    assert metrics.EVENTS_DROPPED is not None
    assert metrics.EVENTS_PUBLISHED_SUCCESS is not None
    assert metrics.EVENTS_FAILED_PERMANENTLY is not None
    assert metrics.PUBLISH_LATENCY is not None
    assert metrics.CIRCUIT_BREAKER_STATUS is not None
    assert metrics.QUEUE_SIZE is not None


def test_metrics_init_failure(mock_alert_operator):
    """Test metrics initialization failure aborts."""
    with patch("prometheus_client.Counter", side_effect=Exception("Metrics error")):
        with pytest.raises(SystemExit):
            PubSubMetrics(CollectorRegistry())
    alert_args, _ = mock_alert_operator.call_args
    assert "Prometheus metrics initialization failed" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


# --- AuditEvent Tests ---
def test_audit_event_success():
    """Test successful AuditEvent creation."""
    event = AuditEvent(
        event_name="test", service_name="test-service", details={"key": "value"}
    )
    assert event.event_name == "test"
    assert event.service_name == "test-service"
    assert event.schema_version == 1


def test_audit_event_pii_scrubbing(mock_scrub_sensitive_data):
    """Test PII scrubbing in details."""
    mock_scrub_sensitive_data.side_effect = lambda x: (
        {"scrubbed": True} if x == {"sensitive": "data"} else x
    )
    event = AuditEvent(
        event_name="test", service_name="test-service", details={"sensitive": "data"}
    )
    assert event.details == {"scrubbed": True}


def test_audit_event_pii_detection_aborts(
    mock_scrub_sensitive_data, mock_alert_operator
):
    """Test PII detection aborts."""
    mock_scrub_sensitive_data.side_effect = lambda x: {"changed": True}
    with pytest.raises(RuntimeError):
        AuditEvent(
            event_name="test",
            service_name="test-service",
            details={"sensitive": "data"},
        )
    alert_args, _ = mock_alert_operator.call_args
    assert "Sensitive data detected in audit event details" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


# --- CircuitBreaker Tests ---
def test_circuit_breaker_initial_state(sample_metrics):
    """Test circuit breaker initial state."""
    cb = CircuitBreaker(threshold=5, reset_seconds=30, metrics=sample_metrics)
    assert cb._is_open is False
    assert cb._failure_count == 0
    assert cb._last_failure_time == 0.0
    cb._metrics.CIRCUIT_BREAKER_STATUS.labels().set.assert_called_with(0)


def test_circuit_breaker_trip(sample_metrics, mock_alert_operator):
    """Test circuit breaker tripping."""
    cb = CircuitBreaker(threshold=3, reset_seconds=30, metrics=sample_metrics)
    for _ in range(3):
        cb.record_failure()
    assert cb._is_open is True
    alert_args, _ = mock_alert_operator.call_args
    assert "Pub/Sub Circuit Breaker TRIPPED" in alert_args[0]
    assert alert_args[1] == "CRITICAL"
    cb._metrics.CIRCUIT_BREAKER_STATUS.labels().set.assert_called_with(1)


def test_circuit_breaker_reset(sample_metrics, mock_alert_operator):
    """Test circuit breaker reset after timeout."""
    cb = CircuitBreaker(threshold=3, reset_seconds=0.1, metrics=sample_metrics)
    cb._is_open = True
    cb._last_failure_time = time.monotonic() - 0.2
    cb.check()
    assert cb._is_open is False
    alert_args, _ = mock_alert_operator.call_args
    assert "Pub/Sub Circuit Breaker RESET" in alert_args[0]
    assert alert_args[1] == "INFO"


# --- PubSubGateway Tests ---
@pytest.mark.asyncio
async def test_gateway_init_success(sample_settings_dict, sample_metrics):
    """Test successful gateway initialization."""
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    assert gateway.settings.project_id == "test-project"
    assert gateway._event_queue.maxsize == 10000
    assert gateway._publisher_client is None


@pytest.mark.asyncio
async def test_gateway_startup_success(
    mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test successful gateway startup."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    mock_get_topic.assert_called_once()
    assert gateway._worker_task is not None
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_gateway_startup_topic_not_found(
    mock_pubsub_publisher,
    sample_settings_dict,
    mock_alert_operator,
    mock_secrets_manager,
):
    """Test startup fails if topic not found."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    mock_get_topic.side_effect = google_exceptions.NotFound("Topic not found")
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    with pytest.raises(SystemExit):
        await gateway.startup()
    alert_args, _ = mock_alert_operator.call_args
    assert "Pub/Sub Topic 'test-topic' not found" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_gateway_startup_credentials_failure(
    mock_pubsub_publisher,
    sample_settings_dict,
    mock_secrets_manager,
    mock_alert_operator,
):
    """Test startup fails if credentials invalid."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    mock_secrets_manager.get_secret.side_effect = Exception("Invalid credentials")
    set_env({"PRODUCTION_MODE": "true"})
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    with pytest.raises(SystemExit):
        await gateway.startup()
    alert_args, _ = mock_alert_operator.call_args
    assert "Failed to load GCP credentials from secret manager" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_gateway_shutdown_success(
    mock_pubsub_publisher, sample_settings_dict, sample_metrics
):
    """Test successful gateway shutdown."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    await gateway.shutdown()
    assert gateway._worker_task.done()
    mock_stop.assert_called_once()


@pytest.mark.asyncio
async def test_gateway_shutdown_timeout(
    mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_alert_operator
):
    """Test shutdown timeout escalates."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()

    # Simulate slow worker
    with patch.object(gateway, "_worker", AsyncMock(side_effect=asyncio.sleep(20))):
        gateway._worker_task = asyncio.create_task(gateway._worker())
        with pytest.raises(SystemExit):
            await gateway.shutdown()
    alert_args, _ = mock_alert_operator.call_args
    assert "Pub/Sub Gateway worker NOT finished" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_publish_success(
    mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test successful publish."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    gateway.publish("test_event", "test-service", {"key": "value"})
    assert gateway._event_queue.qsize() == 1
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_publish_queue_full(
    mock_pubsub_publisher,
    sample_settings_dict,
    sample_metrics,
    mock_alert_operator,
    mock_secrets_manager,
):
    """Test queue full drops event."""
    sample_settings_dict["max_queue_size"] = 1
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    gateway.publish("test_event1", "test-service", {})
    with pytest.raises(RuntimeError):
        gateway.publish("test_event2", "test-service", {})
    alert_args, _ = mock_alert_operator.call_args
    assert "Pub/Sub event queue is FULL" in alert_args[0]
    assert alert_args[1] == "CRITICAL"
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_publish_batch_success(
    mock_pubsub_publisher,
    sample_settings_dict,
    sample_metrics,
    mock_tracer,
    mock_secrets_manager,
):
    """Test successful batch publish."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    batch = [
        AuditEvent(
            event_name="test", service_name="test-service", details={"key": "value"}
        )
    ]
    await gateway._publish_batch(batch)
    mock_publish.assert_called_once()
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_publish_batch_service_unavailable(
    mock_pubsub_publisher,
    sample_settings_dict,
    sample_metrics,
    mock_alert_operator,
    mock_secrets_manager,
):
    """Test batch publish service unavailable failure."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    mock_publish.side_effect = google_exceptions.ServiceUnavailable("Service down")
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    batch = [
        AuditEvent(
            event_name="test", service_name="test-service", details={"key": "value"}
        )
    ]
    with pytest.raises(SystemExit):
        await gateway._publish_batch(batch)
    alert_args, _ = mock_alert_operator.call_args
    assert "Pub/Sub publish failed after retries" in alert_args[0]
    assert alert_args[1] == "CRITICAL"
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_worker_success(
    mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test worker processes events successfully."""
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    event = AuditEvent(
        event_name="test", service_name="test-service", details={"key": "value"}
    )
    await gateway._event_queue.put(event)
    await gateway._event_queue.put(None)  # Shutdown sentinel
    await gateway._worker()
    assert gateway._event_queue.qsize() == 0
    mock_publish.assert_called_once()


@pytest.mark.asyncio
async def test_worker_dry_run(
    mock_pubsub_publisher, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test worker in dry run mode."""
    sample_settings_dict["dry_run"] = True
    mock_client, mock_publish, mock_get_topic, mock_stop, _ = mock_pubsub_publisher
    gateway = PubSubGateway(settings=sample_settings_dict, metrics=sample_metrics)
    gateway._publisher_client = mock_client
    await gateway.startup()
    event = AuditEvent(
        event_name="test", service_name="test-service", details={"key": "value"}
    )
    await gateway._event_queue.put(event)
    await gateway._event_queue.put(None)  # Shutdown sentinel
    await gateway._worker()
    mock_publish.assert_not_called()
    await gateway.shutdown()


# --- Main Block Tests ---
def test_main_block_prod(set_env, mock_alert_operator):
    """Test main block aborts in production."""
    set_env({"PRODUCTION_MODE": "true"})
    with pytest.raises(SystemExit):
        import pubsub_plugin

        pubsub_plugin.__name__ = "__main__"
        importlib.reload(pubsub_plugin)
    alert_args, _ = mock_alert_operator.call_args
    assert (
        "CRITICAL: Pub/Sub plugin example code executed in PRODUCTION_MODE. Aborting."
        in alert_args[0]
    )
    assert alert_args[1] == "CRITICAL"


# --- Run Tests ---
if __name__ == "__main__":
    pytest.main(["-v", __file__])
