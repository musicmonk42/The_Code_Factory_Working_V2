import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import sys
import time
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiormq.exceptions import AMQPConnectionError
from prometheus_client import CollectorRegistry
from pydantic import ValidationError

# Assuming these are available in a file named rabbitmq_plugin.py
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


class RabbitMQSettings:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class RabbitMQMetrics:
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

    def model_dump(self, exclude):
        return {}

    def _sign_event(self):
        return "mock_signature"


class CircuitBreaker:
    def __init__(self, threshold, reset_seconds, metrics):
        self._is_open = False
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._metrics = metrics
        self._metrics.CIRCUIT_BREAKER_STATUS.labels = MagicMock(
            return_value=MagicMock()
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

    def record_success(self):
        self._failure_count = 0


class RabbitMQGateway:
    def __init__(self, settings, metrics):
        self.settings = settings
        self.metrics = metrics
        self._event_queue = asyncio.Queue(maxsize=settings.max_queue_size)
        self._connection = None
        self._worker_task = None
        self._shutdown_event = asyncio.Event()
        self._circuit_breaker = CircuitBreaker(
            settings.circuit_breaker_threshold,
            settings.circuit_breaker_reset_sec,
            metrics,
        )

    async def startup(self):
        self._connection = MagicMock()
        self._connection.channel = AsyncMock(return_value=MagicMock())
        self._connection.close = AsyncMock()
        self._worker_task = asyncio.create_task(self._worker())

    async def shutdown(self):
        self._shutdown_event.set()
        await self._event_queue.put(None)
        await self._worker_task
        await self._connection.close()

    def publish(self, event_name, service_name, details, routing_key):
        if not self._event_queue.full():
            self._event_queue.put_nowait(
                (
                    AuditEvent(
                        event_name=event_name,
                        service_name=service_name,
                        details=details,
                    ),
                    routing_key,
                )
            )
        else:
            alert_operator("RabbitMQ event queue is FULL", level="CRITICAL")

    async def _publish_batch(self, batch):
        pass

    async def _worker(self):
        while True:
            item = await self._event_queue.get()
            if item is None:
                break
            # Process item
            self._event_queue.task_done()


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
    with patch("rabbitmq_plugin.audit_logger", mock):
        yield mock


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    with patch("rabbitmq_plugin.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_scrub_sensitive_data():
    """Mock the scrub_sensitive_data function."""
    with patch("rabbitmq_plugin.scrub_sensitive_data") as mock:
        mock.side_effect = lambda x: x
        yield mock


@pytest.fixture
def mock_secrets_manager():
    """Mock the SECRETS_MANAGER."""
    mock = MagicMock()
    with patch("rabbitmq_plugin.SECRETS_MANAGER", mock):
        mock.get_secret.return_value = "amqps://user:pass@host:port/vhost"
        yield mock


@pytest.fixture
def mock_aiormq(monkeypatch):
    """Mock aiormq components."""
    mock_connection = MagicMock()
    mock_connection.channel = AsyncMock(return_value=MagicMock())
    mock_connection.close = AsyncMock()
    mock_connect = AsyncMock(return_value=mock_connection)

    with patch("aiormq.connect", mock_connect):
        yield mock_connect, mock_connection


@pytest.fixture
def mock_prometheus_registry():
    """Mock Prometheus registry."""
    registry = CollectorRegistry(auto_describe=True)
    with patch("rabbitmq_plugin.CollectorRegistry", return_value=registry):
        yield registry


@pytest.fixture
def set_env(monkeypatch):
    """Fixture to set environment variables for tests."""

    def _set_env(vars: Dict[str, str]):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)

    return _set_env


@pytest.fixture
def sample_settings_dict():
    """Sample RabbitMQSettings dictionary for testing."""
    return {
        "url_secret_id": "RABBITMQ_URL",
        "exchange_name": "test_exchange",
        "exchange_type": "topic",
        "connection_pool_size": 5,
        "channel_pool_size_per_connection": 20,
        "max_queue_size": 10000,
        "worker_batch_size": 100,
        "worker_linger_sec": 0.1,
        "circuit_breaker_threshold": 10,
        "circuit_breaker_reset_sec": 30,
        "allowed_exchange_names": ["test_exchange"],
        "allowed_routing_keys": ["test.*"],
        "dry_run": False,
    }


@pytest.fixture
def sample_metrics(mock_prometheus_registry):
    """Sample RabbitMQMetrics instance."""
    return RabbitMQMetrics(mock_prometheus_registry)


# --- Settings Validation Tests ---
def test_rabbitmq_settings_success(sample_settings_dict, mock_secrets_manager):
    """Test successful RabbitMQSettings validation."""
    settings = RabbitMQSettings(**sample_settings_dict)
    assert settings.exchange_name == "test_exchange"
    assert settings.url == "amqps://user:pass@host:port/vhost"


def test_rabbitmq_settings_insecure_url_prod(
    set_env, sample_settings_dict, mock_secrets_manager
):
    """Test insecure URL fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    mock_secrets_manager.get_secret.return_value = "amqp://guest:guest@localhost/"
    with pytest.raises(ValidationError, match="Non-TLS URL"):
        RabbitMQSettings(**sample_settings_dict)


def test_rabbitmq_settings_default_credentials_prod(
    set_env, sample_settings_dict, mock_secrets_manager
):
    """Test default credentials fail in production."""
    set_env({"PRODUCTION_MODE": "true"})
    mock_secrets_manager.get_secret.return_value = "amqps://guest:guest@host:port/vhost"
    with pytest.raises(ValidationError, match="Default 'guest:guest' credentials"):
        RabbitMQSettings(**sample_settings_dict)


def test_rabbitmq_settings_not_in_allowlist_prod(
    set_env, sample_settings_dict, mock_secrets_manager
):
    """Test URL not in allowlist fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["allowed_exchange_names"] = ["allowed_exchange"]
    with pytest.raises(
        ValidationError, match="not in the 'allowed_exchange_names' list"
    ):
        RabbitMQSettings(**sample_settings_dict)


def test_rabbitmq_settings_wildcard_exchange_prod(set_env, sample_settings_dict):
    """Test wildcard exchange name fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["exchange_name"] = "test_*"
    with pytest.raises(ValidationError, match="Wildcard characters are not allowed"):
        RabbitMQSettings(**sample_settings_dict)


def test_rabbitmq_settings_invalid_routing_keys_regex(sample_settings_dict):
    """Test invalid routing key regex fails."""
    sample_settings_dict["allowed_routing_keys"] = ["invalid_regex["]
    with pytest.raises(ValidationError, match="Invalid regex pattern"):
        RabbitMQSettings(**sample_settings_dict)


def test_rabbitmq_settings_dry_run_prod(set_env, sample_settings_dict):
    """Test dry_run=True fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["dry_run"] = True
    with pytest.raises(ValidationError, match="'dry_run' must be False"):
        RabbitMQSettings(**sample_settings_dict)


# --- Metrics Tests ---
def test_rabbitmq_metrics_init(sample_metrics):
    """Test RabbitMQMetrics initialization."""
    metrics = sample_metrics
    assert metrics.EVENTS_QUEUED is not None
    assert metrics.EVENTS_DROPPED is not None
    assert metrics.EVENTS_PUBLISHED_SUCCESS is not None
    assert metrics.EVENTS_FAILED_PERMANENTLY is not None
    assert metrics.PUBLISH_LATENCY is not None
    assert metrics.CIRCUIT_BREAKER_STATUS is not None
    assert metrics.QUEUE_SIZE is not None


def test_metrics_init_failure(mock_prometheus_registry, mock_alert_operator):
    """Test metrics initialization failure aborts."""
    with patch("prometheus_client.Counter", side_effect=Exception("Metrics error")):
        with pytest.raises(SystemExit):
            RabbitMQMetrics(mock_prometheus_registry)
    alert_args, _ = mock_alert_operator.call_args
    assert "Prometheus metrics initialization failed" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


# --- AuditEvent Tests ---
def test_audit_event_success(mock_secrets_manager):
    """Test successful AuditEvent creation."""
    event = AuditEvent(
        event_name="test", service_name="test-service", details={"key": "value"}
    )
    assert event.event_name == "test"
    assert event.service_name == "test-service"


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


def test_audit_event_sign(mock_secrets_manager):
    """Test event signing."""
    mock_secrets_manager.get_secret.return_value = "hmac_key"
    event = AuditEvent(
        event_name="test", service_name="test-service", details={"key": "value"}
    )
    sig = event._sign_event()
    expected = hmac.new(
        b"hmac_key",
        json.dumps(event.model_dump(exclude={"signature"}), sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected


def test_audit_event_sign_missing_key_prod(set_env, mock_secrets_manager):
    """Test missing HMAC key in production aborts."""
    set_env({"PRODUCTION_MODE": "true"})
    mock_secrets_manager.get_secret.return_value = None
    event = AuditEvent(event_name="test", service_name="test-service", details={})
    with pytest.raises(RuntimeError):
        event._sign_event()


# --- CircuitBreaker Tests ---
def test_circuit_breaker_initial_state(sample_metrics):
    """Test circuit breaker initial state."""
    cb = CircuitBreaker(threshold=5, reset_seconds=30, metrics=sample_metrics)
    assert cb._is_open is False
    assert cb._failure_count == 0
    assert cb._last_failure_time == 0.0
    sample_metrics.CIRCUIT_BREAKER_STATUS.labels().set.assert_called_with(0)


def test_circuit_breaker_trip(sample_metrics, mock_alert_operator):
    """Test circuit breaker tripping."""
    cb = CircuitBreaker(threshold=3, reset_seconds=30, metrics=sample_metrics)
    for _ in range(3):
        cb.record_failure()
    assert cb._is_open is True
    alert_args, _ = mock_alert_operator.call_args
    assert "RabbitMQ Circuit Breaker TRIPPED" in alert_args[0]
    assert alert_args[1] == "CRITICAL"
    sample_metrics.CIRCUIT_BREAKER_STATUS.labels().set.assert_called_with(1)


def test_circuit_breaker_reset(sample_metrics, mock_alert_operator):
    """Test circuit breaker reset after timeout."""
    cb = CircuitBreaker(threshold=3, reset_seconds=0.1, metrics=sample_metrics)
    cb._is_open = True
    cb._last_failure_time = time.monotonic() - 0.2
    cb.check()
    assert cb._is_open is False
    alert_args, _ = mock_alert_operator.call_args
    assert "RabbitMQ Circuit Breaker RESET" in alert_args[0]
    assert alert_args[1] == "INFO"


# --- RabbitMQGateway Tests ---
@pytest.mark.asyncio
async def test_gateway_init_success(
    sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test successful gateway initialization."""
    settings = RabbitMQSettings(**sample_settings_dict)
    gateway = RabbitMQGateway(settings, sample_metrics)
    assert gateway.settings.exchange_name == "test_exchange"
    assert gateway._event_queue.maxsize == 10000
    assert gateway._connection is None


@pytest.mark.asyncio
async def test_gateway_init_insecure_url_prod(
    set_env, sample_settings_dict, mock_secrets_manager, mock_alert_operator
):
    """Test insecure URL aborts in production."""
    set_env({"PRODUCTION_MODE": "true"})
    mock_secrets_manager.get_secret.return_value = "amqp://guest:guest@localhost/"
    with pytest.raises(SystemExit):
        RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    alert_args, _ = mock_alert_operator.call_args
    assert "RabbitMQ URL insecure/default in PRODUCTION_MODE" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_gateway_startup_success(
    mock_aiormq, sample_settings_dict, sample_metrics
):
    """Test successful gateway startup."""
    mock_connect, mock_connection = mock_aiormq
    gateway = RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    mock_connect.assert_called_once()
    mock_connection.channel.return_value.exchange_declare.assert_called_once()
    assert gateway._worker_task is not None
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_gateway_startup_connection_failure(
    mock_aiormq, sample_settings_dict, sample_metrics, mock_alert_operator
):
    """Test connection failure during startup aborts."""
    mock_connect, _ = mock_aiormq
    mock_connect.side_effect = AMQPConnectionError("Connection failed")
    gateway = RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    gateway._connect = mock_connect
    with pytest.raises(SystemExit):
        await gateway.startup()
    alert_args, _ = mock_alert_operator.call_args
    assert "Failed to connect to RabbitMQ after retries" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_gateway_shutdown_success(
    mock_aiormq, sample_settings_dict, sample_metrics
):
    """Test successful gateway shutdown."""
    mock_connect, mock_connection = mock_aiormq
    gateway = RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    await gateway.shutdown()
    mock_connection.close.assert_called_once()
    assert gateway._worker_task.done()


@pytest.mark.asyncio
async def test_gateway_shutdown_timeout(
    mock_aiormq, sample_settings_dict, sample_metrics, mock_alert_operator
):
    """Test shutdown timeout escalates."""
    mock_connect, mock_connection = mock_aiormq
    gateway = RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    # Simulate slow worker
    with patch.object(gateway, "_worker", AsyncMock(side_effect=asyncio.sleep(20))):
        gateway._worker_task = asyncio.create_task(gateway._worker())
        with pytest.raises(SystemExit):
            await gateway.shutdown()
    alert_args, _ = mock_alert_operator.call_args
    assert "RabbitMQ Gateway worker NOT finished" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_publish_success(
    mock_aiormq, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test successful publish."""
    mock_connect, mock_connection = mock_aiormq
    settings = RabbitMQSettings(**sample_settings_dict)
    settings.url = mock_secrets_manager.get_secret("RABBITMQ_URL")
    gateway = RabbitMQGateway(settings, sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    gateway.publish("test_event", "test-service", {"key": "value"}, "test.routing")
    assert gateway._event_queue.qsize() == 1
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_publish_queue_full(
    mock_aiormq,
    sample_settings_dict,
    sample_metrics,
    mock_alert_operator,
    mock_secrets_manager,
):
    """Test queue full drops event."""
    sample_settings_dict["max_queue_size"] = 1
    mock_connect, mock_connection = mock_aiormq
    settings = RabbitMQSettings(**sample_settings_dict)
    settings.url = mock_secrets_manager.get_secret("RABBITMQ_URL")
    gateway = RabbitMQGateway(settings, sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    gateway.publish("test_event1", "test-service", {}, "test.routing")
    gateway.publish("test_event2", "test-service", {}, "test.routing")
    alert_args, _ = mock_alert_operator.call_args
    assert "RabbitMQ event queue is FULL" in alert_args[0]
    assert alert_args[1] == "CRITICAL"
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_publish_invalid_routing_key_prod(
    set_env, sample_settings_dict, mock_alert_operator
):
    """Test invalid routing key aborts in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["allowed_routing_keys"] = ["allowed.*"]
    gateway = RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    with pytest.raises(SystemExit):
        gateway.publish("test_event", "test-service", {}, "forbidden.routing")
    alert_args, _ = mock_alert_operator.call_args
    assert "Routing key 'forbidden.routing' forbidden" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_publish_batch_success(
    mock_aiormq, sample_settings_dict, sample_metrics, mock_tracer, mock_secrets_manager
):
    """Test successful batch publish."""
    mock_connect, mock_connection = mock_aiormq
    settings = RabbitMQSettings(**sample_settings_dict)
    settings.url = mock_secrets_manager.get_secret("RABBITMQ_URL")
    gateway = RabbitMQGateway(settings, sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    batch = [
        (
            AuditEvent(event_name="test", service_name="test-service", details={}),
            "test.routing",
        )
    ]
    gateway.channel = mock_connection.channel.return_value
    gateway.channel.basic_publish = AsyncMock()
    await gateway._publish_batch(batch)
    gateway.channel.basic_publish.assert_called_once()
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_publish_batch_connection_error(
    mock_aiormq,
    sample_settings_dict,
    sample_metrics,
    mock_alert_operator,
    mock_secrets_manager,
):
    """Test batch publish connection error aborts."""
    mock_connect, mock_connection = mock_aiormq
    gateway = RabbitMQGateway(RabbitMQSettings(**sample_settings_dict), sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    batch = [
        (
            AuditEvent(event_name="test", service_name="test-service", details={}),
            "test.routing",
        )
    ]
    gateway.channel = mock_connection.channel.return_value
    gateway.channel.basic_publish = AsyncMock(
        side_effect=AMQPConnectionError("Connection error")
    )
    with pytest.raises(SystemExit):
        await gateway._publish_batch(batch)
    alert_args, _ = mock_alert_operator.call_args
    assert "RabbitMQ publish failed" in alert_args[0]
    assert alert_args[1] == "CRITICAL"
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_worker_success(
    mock_aiormq, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test worker processes events successfully."""
    mock_connect, mock_connection = mock_aiormq
    settings = RabbitMQSettings(**sample_settings_dict)
    settings.url = mock_secrets_manager.get_secret("RABBITMQ_URL")
    gateway = RabbitMQGateway(settings, sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    event = AuditEvent(event_name="test", service_name="test-service", details={})
    await gateway._event_queue.put((event, "test.routing"))
    await gateway._event_queue.put(None)  # Shutdown sentinel
    gateway.channel = mock_connection.channel.return_value
    gateway.channel.basic_publish = AsyncMock()
    await gateway._worker()
    assert gateway._event_queue.qsize() == 0
    gateway.channel.basic_publish.assert_called_once()
    await gateway.shutdown()


@pytest.mark.asyncio
async def test_worker_dry_run(
    mock_aiormq, sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test worker in dry run mode."""
    sample_settings_dict["dry_run"] = True
    mock_connect, mock_connection = mock_aiormq
    settings = RabbitMQSettings(**sample_settings_dict)
    settings.url = mock_secrets_manager.get_secret("RABBITMQ_URL")
    gateway = RabbitMQGateway(settings, sample_metrics)
    gateway._connect = mock_connect
    await gateway.startup()
    event = AuditEvent(event_name="test", service_name="test-service", details={})
    await gateway._event_queue.put((event, "test.routing"))
    await gateway._event_queue.put(None)  # Shutdown sentinel
    gateway.channel = mock_connection.channel.return_value
    gateway.channel.basic_publish = AsyncMock()
    await gateway._worker()
    gateway.channel.basic_publish.assert_not_called()
    await gateway.shutdown()


# --- Main Block Tests ---
def test_main_block_prod(set_env, mock_alert_operator):
    """Test main block aborts in production."""
    set_env({"PRODUCTION_MODE": "true"})
    with pytest.raises(SystemExit):
        import rabbitmq_plugin

        rabbitmq_plugin.__name__ = "__main__"
        importlib.reload(rabbitmq_plugin)
    alert_args, _ = mock_alert_operator.call_args
    assert (
        "CRITICAL: RabbitMQ plugin example code executed in PRODUCTION_MODE. Aborting."
        in alert_args[0]
    )
    assert alert_args[1] == "CRITICAL"


# --- Run Tests ---
if __name__ == "__main__":
    pytest.main(["-v", __file__])
