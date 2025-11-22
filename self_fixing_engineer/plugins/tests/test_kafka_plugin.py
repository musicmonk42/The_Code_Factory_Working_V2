import sys
import json
import logging
import asyncio
import time
import hashlib
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from typing import Dict
from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from pydantic import ValidationError
from prometheus_client import CollectorRegistry

# Assuming these are available in a file named kafka_plugin.py
# and we are mocking them for the purpose of testing this file in isolation.

# Mocks of the original module's components
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
PLUGIN_MANIFEST = {
    "name": "kafka_plugin",
    "version": "1.0.0",
    "description": "Kafka audit plugin",
    "entrypoint": "kafka_plugin.py",
    "type": "python",
    "author": "Author",
    "capabilities": ["audit_hook"],
    "permissions": ["network_access_limited"],
    "dependencies": ["aiokafka"],
    "min_core_version": "1.0.0",
    "max_core_version": "2.0.0",
    "health_check": "plugin_health",
    "api_version": "v1",
    "license": "MIT",
    "homepage": "https://example.com/kafka",
    "tags": ["audit", "kafka"],
    "is_demo_plugin": False,
    "signature": "PLACEHOLDER_FOR_HMAC_SIGNATURE",
}


class KafkaSettings:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    @classmethod
    def model_validate(cls, data):
        return cls(**data)


class AuditMetrics:
    def __init__(self, registry=None):
        if registry is None:
            registry = CollectorRegistry(auto_describe=True)
        self.EVENTS_SENT = MagicMock()
        self.EVENTS_FAILED = MagicMock()
        self.PRODUCER_STATUS = MagicMock()
        self.SEND_LATENCY = MagicMock()
        self.QUEUE_SIZE = MagicMock()


class AuditEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def model_dump(self, exclude):
        return {}

    def _sign_event(self):
        return ""


class KafkaAuditProducer:
    def __init__(self, settings, metrics):
        self._settings = settings
        self._metrics = metrics
        self._producer = AsyncMock()
        self._started = False

    async def start(self):
        self._started = True
        await self._producer.start()

    async def stop(self):
        await self._producer.flush()
        await self._producer.stop()
        self._started = False

    async def send(self, event_name, details):
        await self._producer.send(self._settings.audit_topic, value=b"")

    async def send_batch(self, events):
        for event in events:
            await self.send(event.event, event.details)


kafka_audit_producer = None


async def kafka_audit_hook(event_name, details):
    global kafka_audit_producer
    if kafka_audit_producer:
        await kafka_audit_producer.send(event_name, details)


async def shutdown_kafka_producer():
    global kafka_audit_producer
    if kafka_audit_producer:
        await kafka_audit_producer.stop()


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
    with patch("kafka_plugin.audit_logger", mock):
        yield mock


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    with patch("kafka_plugin.alert_operator") as mock:
        yield mock


@pytest.fixture
def mock_scrub_sensitive_data():
    """Mock the scrub_sensitive_data function."""
    with patch("kafka_plugin.scrub_sensitive_data") as mock:
        mock.side_effect = lambda x: x  # Return input as-is for testing
        yield mock


@pytest.fixture
def mock_secrets_manager():
    """Mock the SECRETS_MANAGER."""
    mock = MagicMock()
    with patch("kafka_plugin.SECRETS_MANAGER", mock):
        mock.get_secret.return_value = "hmac_key"
        yield mock


@pytest.fixture
def mock_aiokafka_producer(monkeypatch):
    """Mock AIOKafkaProducer."""
    mock_producer = AsyncMock(spec=AIOKafkaProducer)
    mock_producer.start = AsyncMock()
    mock_producer.stop = AsyncMock()
    mock_producer.flush = AsyncMock()
    mock_producer.send = AsyncMock()

    with patch("aiokafka.AIOKafkaProducer", return_value=mock_producer) as mock_class:
        yield mock_producer, mock_class


@pytest.fixture
def mock_prometheus_registry():
    """Mock Prometheus registry."""
    registry = CollectorRegistry(auto_describe=True)
    with patch("kafka_plugin.CollectorRegistry", return_value=registry):
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
    """Sample KafkaSettings dictionary for testing."""
    return {
        "brokers": "localhost:9092",
        "audit_topic": "audit_topic",
        "security_protocol": "PLAINTEXT",
        "enable_idempotence": True,
        "acks": "all",
        "linger_ms": 5,
        "batch_size": 16384,
        "compression_type": "gzip",
        "audit_max_retries": 5,
        "audit_retry_backoff": 1.5,
        "audit_schema_version": 1,
        "allowed_brokers": ["localhost:9092"],
        "allowed_topics": ["audit_topic"],
    }


# --- Settings Validation Tests ---
def test_kafka_settings_success(sample_settings_dict):
    """Test successful KafkaSettings validation."""
    settings = KafkaSettings(**sample_settings_dict)
    assert settings.brokers == "localhost:9092"
    assert settings.audit_topic == "audit_topic"


def test_kafka_settings_plaintext_prod(set_env, sample_settings_dict):
    """Test PLAINTEXT forbidden in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["security_protocol"] = "PLAINTEXT"
    with pytest.raises(
        ValidationError, match="security_protocol cannot be 'PLAINTEXT'"
    ):
        KafkaSettings(**sample_settings_dict)


def test_kafka_settings_missing_ssl_cafile_prod(set_env, sample_settings_dict):
    """Test missing SSL CA file in production fails."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["security_protocol"] = "SSL"
    with pytest.raises(
        ValidationError, match="ssl_cafile or ssl_cafile_secret_id must be provided"
    ):
        KafkaSettings(**sample_settings_dict)


def test_kafka_settings_invalid_brokers_allowlist(set_env, sample_settings_dict):
    """Test brokers not in allowlist fails in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["allowed_brokers"] = ["other:9092"]
    with pytest.raises(
        ValidationError, match="configured brokers .* not in the 'allowed_brokers' list"
    ):
        KafkaSettings(**sample_settings_dict)


def test_kafka_settings_wildcard_topic_prod(set_env, sample_settings_dict):
    """Test wildcard topic forbidden in production."""
    set_env({"PRODUCTION_MODE": "true"})
    sample_settings_dict["audit_topic"] = "audit_*"
    with pytest.raises(ValidationError, match="Wildcard characters are not allowed"):
        KafkaSettings(**sample_settings_dict)


# --- Metrics Tests ---
def test_audit_metrics_init(mock_prometheus_registry):
    """Test AuditMetrics initialization."""
    metrics = AuditMetrics(mock_prometheus_registry)
    assert metrics.EVENTS_SENT is not None
    assert metrics.EVENTS_FAILED is not None
    assert metrics.PRODUCER_STATUS is not None
    assert metrics.SEND_LATENCY is not None
    assert metrics.QUEUE_SIZE is not None


def test_metrics_init_failure(mock_alert_operator):
    """Test metrics initialization failure aborts."""
    with patch("prometheus_client.Counter", side_effect=Exception("Metrics error")):
        with pytest.raises(SystemExit):
            AuditMetrics(CollectorRegistry())


# --- AuditEvent Tests ---
def test_audit_event_success(mock_secrets_manager):
    """Test successful AuditEvent creation."""
    event = AuditEvent(event="test", details={"key": "value"}, ts=time.time())
    assert event.event == "test"
    assert event.schema_version == 1


def test_audit_event_pii_scrubbing(mock_scrub_sensitive_data):
    """Test PII scrubbing in details."""
    mock_scrub_sensitive_data.side_effect = lambda x: (
        {"scrubbed": True} if x == {"sensitive": "data"} else x
    )
    event = AuditEvent(event="test", details={"sensitive": "data"}, ts=time.time())
    assert event.details == {"scrubbed": True}


def test_audit_event_pii_detection_aborts(
    mock_scrub_sensitive_data, mock_alert_operator
):
    """Test PII detection aborts."""
    mock_scrub_sensitive_data.side_effect = lambda x: {"changed": True}
    with pytest.raises(RuntimeError):
        AuditEvent(event="test", details={"sensitive": "data"}, ts=time.time())
    alert_args, _ = mock_alert_operator.call_args
    assert "Sensitive data detected in audit event details" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


def test_audit_event_sign(mock_secrets_manager):
    """Test event signing."""
    mock_secrets_manager.get_secret.return_value = "hmac_key"
    event = AuditEvent(event="test", details={"key": "value"}, ts=1234567890)
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
    event = AuditEvent(event="test", details={}, ts=time.time())
    with pytest.raises(RuntimeError):
        event._sign_event()


# --- KafkaAuditProducer Tests ---
@pytest.mark.asyncio
async def test_producer_start_success(mock_aiokafka_producer, mock_prometheus_registry):
    """Test successful producer start."""
    mock_producer, _ = mock_aiokafka_producer
    producer = KafkaAuditProducer(
        KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"}),
        AuditMetrics(mock_prometheus_registry),
    )
    producer._producer = mock_producer
    await producer.start()
    mock_producer.start.assert_called_once()
    assert producer._started is True


@pytest.mark.asyncio
async def test_producer_start_failure(mock_aiokafka_producer, mock_alert_operator):
    """Test producer start failure aborts."""
    mock_producer, mock_class = mock_aiokafka_producer
    mock_producer.start.side_effect = KafkaError("Connection failed")
    mock_class.return_value = mock_producer

    producer = KafkaAuditProducer(
        KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"}),
        AuditMetrics(CollectorRegistry()),
    )
    producer._producer = mock_producer

    with pytest.raises(AnalyzerCriticalError):
        await producer.start()
    alert_args, _ = mock_alert_operator.call_args
    assert "Failed to start Kafka producer" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_producer_stop_success(mock_aiokafka_producer, mock_prometheus_registry):
    """Test successful producer stop."""
    mock_producer, _ = mock_aiokafka_producer
    producer = KafkaAuditProducer(
        KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"}),
        AuditMetrics(mock_prometheus_registry),
    )
    producer._producer = mock_producer
    await producer.start()
    await producer.stop()
    mock_producer.flush.assert_called_once()
    mock_producer.stop.assert_called_once()
    assert producer._started is False


@pytest.mark.asyncio
async def test_producer_stop_flush_timeout(mock_aiokafka_producer, mock_alert_operator):
    """Test flush timeout during stop aborts."""
    mock_producer, _ = mock_aiokafka_producer
    mock_producer.flush.side_effect = asyncio.TimeoutError

    producer = KafkaAuditProducer(
        KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"}),
        AuditMetrics(CollectorRegistry()),
    )
    producer._producer = mock_producer

    await producer.start()
    with pytest.raises(AnalyzerCriticalError):
        await producer.stop()
    alert_args, _ = mock_alert_operator.call_args
    assert "Kafka producer flush timed out during shutdown" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


@pytest.mark.asyncio
async def test_producer_send_success(mock_aiokafka_producer, mock_prometheus_registry):
    """Test successful single send."""
    mock_producer, _ = mock_aiokafka_producer
    producer = KafkaAuditProducer(
        KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"}),
        AuditMetrics(mock_prometheus_registry),
    )
    producer._producer = mock_producer
    await producer.start()
    await producer.send("test_event", {"key": "value"})
    mock_producer.send.assert_called_once()
    mock_producer.flush.assert_called_once()


@pytest.mark.asyncio
async def test_producer_send_batch_success(
    mock_aiokafka_producer, mock_prometheus_registry
):
    """Test successful batch send."""
    mock_producer, _ = mock_aiokafka_producer
    producer = KafkaAuditProducer(
        KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"}),
        AuditMetrics(mock_prometheus_registry),
    )
    producer._producer = mock_producer
    await producer.start()
    events = [AuditEvent(event="test", details={}, ts=time.time()) for _ in range(3)]
    await producer.send_batch(events)
    assert mock_producer.send.call_count == 3
    mock_producer.flush.assert_called_once()


@pytest.mark.asyncio
async def test_producer_send_retry_failure(mock_aiokafka_producer, mock_alert_operator):
    """Test send failure after retries aborts."""
    mock_producer, _ = mock_aiokafka_producer
    mock_producer.send.side_effect = KafkaError("Send error")
    settings = KafkaSettings(
        **{"brokers": "localhost:9092", "audit_topic": "audit", "audit_max_retries": 2}
    )
    producer = KafkaAuditProducer(settings, AuditMetrics(CollectorRegistry()))
    producer._producer = mock_producer
    await producer.start()

    with pytest.raises(KafkaError):
        await producer.send("test_event", {"key": "value"})

    assert mock_producer.send.call_count == 2
    alert_args, _ = mock_alert_operator.call_args
    assert "Unexpected error sending Kafka batch" in alert_args[0]
    assert alert_args[1] == "CRITICAL"


# --- Kafka Audit Hook Tests ---
@pytest.mark.asyncio
async def test_kafka_audit_hook_success(mock_aiokafka_producer):
    """Test successful audit hook."""
    mock_producer, _ = mock_aiokafka_producer
    global kafka_audit_producer
    settings = KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"})
    metrics = AuditMetrics()
    kafka_audit_producer = KafkaAuditProducer(settings, metrics)
    kafka_audit_producer._producer = mock_producer
    await kafka_audit_producer.start()

    await kafka_audit_hook("test_event", {"key": "value"})

    mock_producer.send.assert_called_once()
    await kafka_audit_producer.stop()
    kafka_audit_producer = None


# --- Shutdown Tests ---
@pytest.mark.asyncio
async def test_shutdown_kafka_producer_success(mock_aiokafka_producer):
    """Test successful shutdown."""
    mock_producer, _ = mock_aiokafka_producer
    global kafka_audit_producer
    settings = KafkaSettings(**{"brokers": "localhost:9092", "audit_topic": "audit"})
    metrics = AuditMetrics()
    kafka_audit_producer = KafkaAuditProducer(settings, metrics)
    kafka_audit_producer._producer = mock_producer
    await kafka_audit_producer.start()

    await shutdown_kafka_producer()

    mock_producer.stop.assert_called_once()
    kafka_audit_producer = None


# --- Main block for running tests ---
if __name__ == "__main__":
    pytest.main(["-v", __file__])
