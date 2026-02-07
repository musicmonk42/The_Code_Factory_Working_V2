# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import json
import logging
import os
import sys
import time
from typing import Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

# Set critical environment variables before importing slack_plugin
os.environ["SLACK_AUDIT_LOG_HMAC_KEY"] = "test-hmac-key"
os.environ["SLACK_WAL_HMAC_KEY"] = "test-wal-hmac-key"
os.environ["SLACK_GATEWAY_SIGNING_SECRET"] = "test-signing-secret"
os.environ["SLACK_GATEWAY_ADMIN_API_KEY"] = "test-admin-key"
os.environ["SLACK_GATEWAY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["SLACK_GATEWAY_TARGETS"] = json.dumps(
    [
        {"name": "alerts", "webhook_url": "https://hooks.slack.com/alerts"},
        {"name": "audit", "webhook_url": "https://hooks.slack.com/audit"},
    ]
)

# Set WindowsSelectorEventLoopPolicy for Windows compatibility
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add the parent directory to sys.path to allow imports
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

# Mock aiohttp at module level before importing slack_plugin
# Only mock if not already loaded with a real module to preserve type annotations
if "aiohttp" not in sys.modules or isinstance(sys.modules.get("aiohttp"), MagicMock):
    mock_aiohttp = MagicMock()
    sys.modules["aiohttp"] = mock_aiohttp

# Mock psutil at module level before importing slack_plugin
mock_psutil = MagicMock()
mock_psutil.cpu_percent = MagicMock(return_value=50.0)
mock_psutil.virtual_memory = MagicMock()
mock_psutil.virtual_memory.return_value.used = 1024 * 1024
mock_psutil.virtual_memory.return_value.percent = 50.0
sys.modules["psutil"] = mock_psutil

# Mock aiofiles at module level before importing slack_plugin
mock_aiofiles = MagicMock()
mock_aiofiles.open = AsyncMock()
sys.modules["aiofiles"] = mock_aiofiles
sys.modules["aiofiles.threadpool"] = MagicMock()
sys.modules["aiofiles.threadpool.binary"] = MagicMock()

# Import from the correct module path
from plugins.slack_plugin.slack_plugin import (
    CircuitBreaker,
    PersistentWALQueue,
    SlackBlockKitSerializer,
    SlackEvent,
    SlackGateway,
    SlackGatewayManager,
    SlackGatewaySettings,
    SlackMetrics,
    SlackTarget,
    TokenBucket,
    audit_logger,
    dead_letter_to_file,
    main_logger,
)
from pydantic import ValidationError


# Mock the global constants and functions from the original module for testing purposes
class AnalyzerCriticalError(RuntimeError):
    pass


class NonCriticalError(Exception):
    pass


# --- Test Setup ---
@pytest.fixture(scope="function", autouse=True)
def setup_environment(monkeypatch):
    """Set up additional environment variables for the test session if needed."""
    if hasattr(monkeypatch, "setenv"):
        monkeypatch.setenv("SLACK_AUDIT_LOG_HMAC_KEY", "test-hmac-key")
        monkeypatch.setenv("SLACK_WAL_HMAC_KEY", "test-wal-hmac-key")
        monkeypatch.setenv("SLACK_GATEWAY_SIGNING_SECRET", "test-signing-secret")
        monkeypatch.setenv("SLACK_GATEWAY_ADMIN_API_KEY", "test-admin-key")
        monkeypatch.setenv(
            "SLACK_GATEWAY_ENCRYPTION_KEY", Fernet.generate_key().decode()
        )
        monkeypatch.setenv(
            "SLACK_GATEWAY_TARGETS",
            json.dumps(
                [
                    {"name": "alerts", "webhook_url": "https://hooks.slack.com/alerts"},
                    {"name": "audit", "webhook_url": "https://hooks.slack.com/audit"},
                ]
            ),
        )


@pytest.fixture(autouse=True)
def setup_logging():
    """Set up logging to capture output for tests."""
    main_logger.handlers = []
    audit_logger.handlers = []
    main_handler = logging.StreamHandler(sys.stdout)
    main_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    main_logger.addHandler(main_handler)
    main_logger.setLevel(logging.INFO)
    audit_handler = logging.StreamHandler(sys.stdout)
    audit_handler.setFormatter(logging.Formatter("%(asctime)s - AUDIT - %(message)s"))
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)
    yield
    main_logger.handlers = []
    audit_logger.handlers = []


@pytest.fixture
def mock_audit_logger():
    """Mock the audit logger to capture log events."""
    mock = MagicMock()
    with patch("plugins.slack_plugin.slack_plugin.audit_logger", mock):
        yield mock


@pytest.fixture
def mock_alert_operator():
    """Mock the alert_operator function."""
    mock = MagicMock()
    with patch("plugins.slack_plugin.slack_plugin.alert_operator", mock):
        yield mock


@pytest.fixture
def mock_secrets_manager():
    """Mock the SECRETS_MANAGER."""
    mock = MagicMock()
    mock.get_secret = MagicMock(return_value="test_secret")
    with patch("plugins.slack_plugin.slack_plugin.SECRETS_MANAGER", mock):
        yield mock


@pytest.fixture
def mock_aiohttp_session():
    """Mock aiohttp.ClientSession."""
    mock_session = MagicMock()
    mock_session.closed = False
    mock_post = AsyncMock()
    mock_session.post = mock_post

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.headers = {}
    mock_response.text = AsyncMock(return_value="OK")
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)

    mock_post.return_value = mock_response

    with patch("aiohttp.ClientSession", return_value=mock_session):
        yield mock_session, mock_post, mock_response


@pytest.fixture
def mock_psutil_fixture(monkeypatch):
    """Mock psutil for system metrics."""
    mock_cpu_percent = MagicMock(return_value=50.0)
    mock_virtual_memory = MagicMock()
    mock_virtual_memory.used = 1024 * 1024
    mock_virtual_memory.percent = 50.0
    monkeypatch.setattr("psutil.cpu_percent", mock_cpu_percent)
    monkeypatch.setattr("psutil.virtual_memory", lambda: mock_virtual_memory)
    yield mock_cpu_percent, mock_virtual_memory


@pytest.fixture
def mock_aiofiles_open():
    """Mock aiofiles.open."""
    mock_file = AsyncMock()
    mock_file.read = AsyncMock(return_value="0")
    mock_file.write = AsyncMock()
    mock_file.flush = AsyncMock()
    mock_file.close = AsyncMock()
    mock_file.closed = False
    mock_file.__aenter__ = AsyncMock(return_value=mock_file)
    mock_file.__aexit__ = AsyncMock(return_value=None)

    mock_open = AsyncMock(return_value=mock_file)
    with patch("aiofiles.open", mock_open):
        yield mock_open, mock_file


@pytest.fixture
def mock_tracer():
    """Mock OpenTelemetry tracer."""
    mock = MagicMock()
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=None)
    mock.start_as_current_span = MagicMock(return_value=mock_span)
    with patch("plugins.slack_plugin.slack_plugin.tracer", mock):
        yield mock, mock_span


@pytest.fixture
def set_env(monkeypatch):
    """Fixture to set environment variables for tests."""

    def _set_env(vars: Dict[str, str]):
        for key, value in vars.items():
            monkeypatch.setenv(key, value)

    return _set_env


@pytest.fixture
def temp_dir(tmp_path):
    """Fixture for a temporary directory."""
    return tmp_path


@pytest.fixture
def sample_settings_dict():
    """Sample SlackGatewaySettings dictionary for testing."""
    return {
        "signing_secret": "test-signing-secret",
        "admin_api_key": "test-admin-key",
        "encryption_key": Fernet.generate_key().decode(),
        "targets": [
            SlackTarget(name="alerts", webhook_url="https://hooks.slack.com/alerts"),
            SlackTarget(name="audit", webhook_url="https://hooks.slack.com/audit"),
        ],
        "persistence_dir": "/tmp/slack_queue",
        "min_workers": 1,
        "max_workers": 4,
        "queue_size_per_worker": 250,
        "worker_scaling_interval": 5,
        "max_queue_size": 10000,
        "worker_linger_sec": 1.0,
        "max_concurrent_requests": 5,
        "requests_per_second_limit": 1.0,
        "max_retries": 3,
        "retry_backoff_factor": 2.0,
        "circuit_breaker_threshold": 5,
        "circuit_breaker_reset_sec": 60,
        "dry_run": False,
        "dry_run_failure_rate": 0.0,
        "url_allowlist": ["https://hooks.slack.com/.*"],
        "verify_ssl": True,
        "admin_api_enabled": True,
        "admin_api_port": 9877,
        "admin_api_host": "127.0.0.1",
    }


@pytest.fixture
def sample_metrics():
    """Sample SlackMetrics instance."""
    return SlackMetrics()


# --- Settings Validation Tests ---
def test_slack_target_validate_url_protocol_prod(monkeypatch):
    """Test HTTPS requirement for SlackTarget in production."""
    monkeypatch.setattr("plugins.slack_plugin.slack_plugin.PROD_MODE", True)
    with pytest.raises(ValidationError, match="All Slack webhook URLs must use HTTPS"):
        SlackTarget(name="test", webhook_url="http://hooks.slack.com/test")


def test_slack_gateway_settings_success(sample_settings_dict):
    """Test successful SlackGatewaySettings validation."""
    settings = SlackGatewaySettings(**sample_settings_dict)
    assert len(settings.targets) == 2
    assert settings.signing_secret == "test-signing-secret"


def test_slack_gateway_settings_default_secret(sample_settings_dict):
    """Test default secret validation fails."""
    sample_settings_dict["signing_secret"] = "default-slack-secret-key-change-me"
    with pytest.raises(
        ValidationError,
        match="CRITICAL: The signing_secret must not be the default value",
    ):
        SlackGatewaySettings(**sample_settings_dict)


def test_slack_gateway_settings_admin_api_host_prod(monkeypatch, sample_settings_dict):
    """Test non-localhost admin API host fails in production."""
    monkeypatch.setattr("plugins.slack_plugin.slack_plugin.PROD_MODE", True)
    sample_settings_dict["admin_api_host"] = "0.0.0.0"
    with pytest.raises(
        ValidationError, match="admin API must only be exposed on localhost"
    ):
        SlackGatewaySettings(**sample_settings_dict)


def test_slack_gateway_settings_immutable_prod(monkeypatch, sample_settings_dict):
    """Test settings are immutable in production."""
    monkeypatch.setattr("plugins.slack_plugin.slack_plugin.PROD_MODE", True)
    settings = SlackGatewaySettings(**sample_settings_dict)
    with pytest.raises(
        AttributeError, match="Configuration is immutable in production mode"
    ):
        settings.dry_run = True


@pytest.mark.asyncio
async def test_slack_gateway_settings_dry_run_prod(
    monkeypatch, sample_settings_dict, mock_secrets_manager
):
    """Test dry_run enabled fails in production."""
    monkeypatch.setattr("plugins.slack_plugin.slack_plugin.PROD_MODE", True)
    sample_settings_dict["dry_run"] = True

    with pytest.raises(SystemExit):
        settings = SlackGatewaySettings(**sample_settings_dict)
        manager = SlackGatewayManager(settings, SlackMetrics(), None)
        await manager.startup()


def test_slack_gateway_settings_url_not_in_allowlist_prod(
    monkeypatch, sample_settings_dict
):
    """Test URL not in allowlist fails in production."""
    monkeypatch.setattr("plugins.slack_plugin.slack_plugin.PROD_MODE", True)
    sample_settings_dict["url_allowlist"] = ["https://other.slack.com/.*"]
    with pytest.raises(ValueError, match="not in allowed_urls list"):
        SlackGatewaySettings.load_from_secure_vault()


# --- Metrics Tests ---
def test_slack_metrics_init():
    """Test SlackMetrics initialization."""
    metrics = SlackMetrics()
    assert metrics.NOTIFICATIONS_QUEUED is not None
    assert metrics.NOTIFICATIONS_DROPPED is not None
    assert metrics.NOTIFICATIONS_SENT_SUCCESS is not None
    assert metrics.NOTIFICATIONS_FAILED_PERMANENTLY is not None
    assert metrics.DEAD_LETTER_NOTIFICATIONS is not None
    assert metrics.SEND_LATENCY is not None
    assert metrics.CIRCUIT_BREAKER_STATUS is not None
    assert metrics.RATE_LIMIT_THROTTLED_SECONDS is not None
    assert metrics.ACTIVE_WORKERS is not None
    assert metrics.NON_TRACED_NOTIFICATIONS is not None
    assert metrics.QUEUE_SIZE is not None
    assert metrics.QUEUE_LATENCY is not None
    assert metrics.SYSTEM_CPU_USAGE is not None
    assert metrics.SYSTEM_MEMORY_USAGE is not None


def test_slack_metrics_update_system_metrics(sample_metrics):
    """Test updating system metrics."""
    # Mock the Gauge.set method
    sample_metrics.SYSTEM_CPU_USAGE.set = MagicMock()
    sample_metrics.SYSTEM_MEMORY_USAGE.set = MagicMock()

    with (
        patch("psutil.cpu_percent", return_value=50.0),
        patch("psutil.virtual_memory") as mock_vm,
    ):
        mock_vm_obj = MagicMock()
        mock_vm_obj.used = 1024 * 1024
        mock_vm.return_value = mock_vm_obj
        sample_metrics.update_system_metrics()
        sample_metrics.SYSTEM_CPU_USAGE.set.assert_called_with(50.0)
        sample_metrics.SYSTEM_MEMORY_USAGE.set.assert_called_with(1024 * 1024)


# --- SlackEvent Tests ---
def test_slack_event_success():
    """Test successful SlackEvent creation."""
    # Create event with non-sensitive field names
    event = SlackEvent(event_name="test", details={"data": "value"}, severity="info")
    assert event.event_name == "test"
    assert event.severity == "info"
    assert event.details == {"data": "value"}


def test_slack_event_pii_scrubbing():
    """Test PII scrubbing in details."""
    details = {
        "password": "secret",
        "email": "user@example.com",
        "phone": "+1-555-555-5555",
        "safe_data": "this is safe",
    }
    # Create an event which will automatically scrub the details
    event = SlackEvent(event_name="test", details=details, severity="info")
    # Check that sensitive fields were scrubbed
    assert event.details["password"] == "[REDACTED]"
    assert event.details["email"] == "[REDACTED]"
    assert event.details["phone"] == "[REDACTED]"
    assert event.details["safe_data"] == "this is safe"


# --- Serializer Tests ---
def test_slack_block_kit_serializer():
    """Test SlackBlockKitSerializer encoding."""
    serializer = SlackBlockKitSerializer()
    event = SlackEvent(
        event_name="test",
        details={"data": "value"},
        severity="info",
        sequence_id=1,
        signature="sig",
    )
    # Explicitly set username in target to avoid environment override
    target = SlackTarget(
        name="alerts",
        webhook_url="https://hooks.slack.com/alerts",
        username="Audit Gateway",  # Explicitly set
    )
    payload = serializer.encode_payload(event, target, "hostname")
    assert payload["username"] == "Audit Gateway"
    assert payload["attachments"][0]["color"] == "#36a64f"
    assert (
        "test alert from hostname"
        in payload["attachments"][0]["blocks"][3]["text"]["text"]
    )


# --- PersistentWALQueue Tests ---
@pytest.mark.asyncio
async def test_persistent_wal_queue_startup(temp_dir, mock_secrets_manager):
    """Test PersistentWALQueue initialization."""
    mock_secrets_manager.get_secret.return_value = "test-signing-secret"

    # Mock os.listdir to return empty (no existing files)
    with (
        patch("os.listdir", return_value=[]),
        patch("os.makedirs"),
        patch("os.chmod"),
        patch("os.rename"),
        patch("aiofiles.open", new_callable=AsyncMock) as mock_open,
    ):

        mock_file = AsyncMock()
        mock_file.flush = AsyncMock()
        mock_file.close = AsyncMock()
        mock_file.closed = False
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=None)
        mock_open.return_value = mock_file

        queue = PersistentWALQueue("test_target", str(temp_dir), 1000)
        await queue.startup()
        assert queue.qsize() == 0
        await queue.shutdown()


@pytest.mark.asyncio
async def test_persistent_wal_queue_put_get(temp_dir, mock_secrets_manager):
    """Test putting and getting events from the queue."""
    mock_secrets_manager.get_secret.return_value = "test-signing-secret"

    # Create a mock stat result
    mock_stat_result = MagicMock()
    mock_stat_result.st_size = 100

    # Create an async function that returns the stat result
    async def mock_stat_func(*args, **kwargs):
        return mock_stat_result

    with (
        patch("os.listdir", return_value=[]),
        patch("os.makedirs"),
        patch("os.chmod"),
        patch("os.rename"),
        patch("aiofiles.os.stat", mock_stat_func),
    ):

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        mock_file.flush = AsyncMock()
        mock_file.close = AsyncMock()
        mock_file.closed = False
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=None)

        mock_open = AsyncMock(return_value=mock_file)
        with patch("aiofiles.open", mock_open):

            queue = PersistentWALQueue("test_target", str(temp_dir), 1000)
            await queue.startup()

            event = SlackEvent(
                event_name="test", details={"data": "value"}, sequence_id=1
            )
            await queue.put(event)
            assert queue.qsize() == 1

            retrieved_event = await queue.get()
            assert retrieved_event.event_name == "test"

            await queue.shutdown()


# --- CircuitBreaker Tests ---
def test_circuit_breaker_initial_state(sample_metrics):
    """Test circuit breaker initial state."""
    cb = CircuitBreaker(
        threshold=5, reset_seconds=60, metrics=sample_metrics, target_name="test"
    )
    assert cb._is_open is False
    assert cb._failure_count == 0


def test_circuit_breaker_trip(sample_metrics):
    """Test circuit breaker tripping."""
    with patch("plugins.slack_plugin.slack_plugin.alert_operator") as mock_alert:
        cb = CircuitBreaker(
            threshold=3, reset_seconds=60, metrics=sample_metrics, target_name="test"
        )
        for _ in range(3):
            cb.record_failure()
        assert cb._is_open is True
        mock_alert.assert_called_once()
        assert "Slack circuit breaker tripped for test" in mock_alert.call_args[0][0]


def test_circuit_breaker_reset(sample_metrics):
    """Test circuit breaker reset after timeout."""
    cb = CircuitBreaker(
        threshold=3, reset_seconds=0.1, metrics=sample_metrics, target_name="test"
    )
    cb._is_open = True
    cb._last_failure_time = time.monotonic() - 0.2
    cb.check()
    assert cb._is_open is False


def test_circuit_breaker_check_open_raises(sample_metrics):
    """Test circuit breaker check raises when open."""
    cb = CircuitBreaker(
        threshold=3, reset_seconds=60, metrics=sample_metrics, target_name="test"
    )
    cb._is_open = True
    cb._last_failure_time = time.monotonic()
    with pytest.raises(
        ConnectionAbortedError, match="Circuit breaker for test is open"
    ):
        cb.check()


def test_circuit_breaker_record_success(sample_metrics):
    """Test circuit breaker success recording."""
    cb = CircuitBreaker(
        threshold=3, reset_seconds=60, metrics=sample_metrics, target_name="test"
    )
    cb._failure_count = 2
    cb.record_success()
    assert cb._failure_count == 0


# --- TokenBucket Tests ---
@pytest.mark.asyncio
async def test_token_bucket_acquire(sample_metrics):
    """Test token bucket rate limiting."""
    tb = TokenBucket(
        rate=100.0, capacity=100.0, metrics=sample_metrics, target_name="test"
    )
    initial_tokens = tb._tokens
    await tb.acquire()
    assert tb._tokens < initial_tokens


@pytest.mark.asyncio
async def test_token_bucket_rate_limit_429(sample_metrics):
    """Test token bucket adjusts rate on 429 response."""
    tb = TokenBucket(
        rate=1.0, capacity=10.0, metrics=sample_metrics, target_name="test"
    )
    initial_rate = tb._rate
    tb.record_status(429)
    # The rate should be reduced by half on a 429 response
    assert tb._rate == initial_rate * 0.5


@pytest.mark.asyncio
async def test_token_bucket_refill(sample_metrics):
    """Test token bucket refill mechanism."""
    tb = TokenBucket(
        rate=10.0, capacity=10.0, metrics=sample_metrics, target_name="test"
    )
    tb._tokens = 0
    tb._last_refill = time.monotonic() - 1  # 1 second ago
    tb._refill()
    assert tb._tokens > 0  # Should have refilled some tokens


# --- SlackGateway Tests ---
@pytest.mark.asyncio
async def test_slack_gateway_init(
    sample_settings_dict, sample_metrics, mock_secrets_manager
):
    """Test successful SlackGateway initialization."""
    target = SlackTarget(name="alerts", webhook_url="https://hooks.slack.com/alerts")
    settings = SlackGatewaySettings(**sample_settings_dict)
    rate_limiter = TokenBucket(
        rate=1.0, capacity=10.0, metrics=sample_metrics, target_name="alerts"
    )
    gateway = SlackGateway(
        target, settings, sample_metrics, SlackBlockKitSerializer(), rate_limiter, None
    )
    assert gateway.target_config.name == "alerts"
    assert gateway._is_paused is False


def test_slack_gateway_pause_resume(sample_settings_dict, sample_metrics):
    """Test pause and resume functionality."""
    target = SlackTarget(name="alerts", webhook_url="https://hooks.slack.com/alerts")
    settings = SlackGatewaySettings(**sample_settings_dict)
    rate_limiter = TokenBucket(
        rate=1.0, capacity=10.0, metrics=sample_metrics, target_name="alerts"
    )
    gateway = SlackGateway(
        target, settings, sample_metrics, SlackBlockKitSerializer(), rate_limiter, None
    )

    assert gateway._is_paused is False
    gateway.pause()
    assert gateway._is_paused is True
    gateway.resume()
    assert gateway._is_paused is False


# --- SlackGatewayManager Tests ---
def test_slack_gateway_manager_init(sample_settings_dict, sample_metrics):
    """Test successful SlackGatewayManager initialization."""
    settings = SlackGatewaySettings(**sample_settings_dict)
    manager = SlackGatewayManager(settings, sample_metrics, None)
    assert len(manager._gateways) == 0
    assert "block_kit_serializer" in manager._serializers


def test_slack_gateway_manager_register_serializer(
    sample_settings_dict, sample_metrics
):
    """Test registering a custom serializer."""
    settings = SlackGatewaySettings(**sample_settings_dict)
    manager = SlackGatewayManager(settings, sample_metrics, None)

    custom_serializer = SlackBlockKitSerializer()
    manager.register_serializer("custom", custom_serializer)
    assert "custom" in manager._serializers
    assert manager._serializers["custom"] == custom_serializer


@pytest.mark.asyncio
async def test_slack_gateway_manager_startup_prod_no_opentelemetry(
    monkeypatch, sample_settings_dict, sample_metrics
):
    """Test startup fails in production without OpenTelemetry."""
    monkeypatch.setattr("plugins.slack_plugin.slack_plugin.PROD_MODE", True)
    monkeypatch.setattr(
        "plugins.slack_plugin.slack_plugin.OPENTELEMETRY_AVAILABLE", False
    )

    settings = SlackGatewaySettings(**sample_settings_dict)
    manager = SlackGatewayManager(settings, sample_metrics, None)
    with pytest.raises(SystemExit):
        await manager.startup()


@pytest.mark.asyncio
async def test_slack_gateway_manager_health_check(
    sample_settings_dict, sample_metrics, temp_dir
):
    """Test health check."""
    sample_settings_dict["persistence_dir"] = str(temp_dir)
    settings = SlackGatewaySettings(**sample_settings_dict)
    manager = SlackGatewayManager(settings, sample_metrics, None)

    # Create a proper mock for aiofiles that returns an async context manager
    mock_file = MagicMock()
    mock_file.read = AsyncMock(return_value="0")
    mock_file.__aenter__ = AsyncMock(return_value=mock_file)
    mock_file.__aexit__ = AsyncMock(return_value=None)

    # Create a mock for aiofiles.open that directly returns the context manager
    mock_open = MagicMock()
    mock_open.return_value = mock_file

    with (
        patch.object(SlackGateway, "startup", new_callable=AsyncMock),
        patch("aiofiles.open", mock_open),
    ):

        await manager.reload_config(settings)
        health = await manager.health_check()
        assert health["status"] == "ok"
        assert "alerts" in health["targets"]
        assert "audit" in health["targets"]
        assert health["version"] == 1
        assert health["targets"]["alerts"]["status"] == "healthy"
        assert health["targets"]["audit"]["status"] == "healthy"
        assert health["targets"]["alerts"]["queue_size"] == 0
        assert health["targets"]["audit"]["queue_size"] == 0
        await manager.shutdown()


# --- Dead Letter Tests ---
@pytest.mark.asyncio
async def test_dead_letter_to_file(temp_dir, mock_secrets_manager):
    """Test dead letter to file."""
    # Mock to return None for encryption key so data isn't encrypted
    mock_secrets_manager.get_secret.return_value = None

    event = SlackEvent(event_name="test", details={"data": "value"}, severity="info")

    # Track if the file operations were called
    file_operations = {
        "open_called": False,
        "write_called": False,
        "chmod_called": False,
    }

    # Create a mock file-like object
    class MockFile:
        async def write(self, data):
            file_operations["write_called"] = True
            # Verify the data contains expected content
            data_str = data if isinstance(data, str) else data.decode()
            assert "test_reason" in data_str
            assert "test" in data_str  # event_name
            return len(data)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    # Create a function that returns the mock file
    def mock_aiofiles_open(filepath, mode):
        file_operations["open_called"] = True
        # Verify the filepath is in the expected directory
        assert str(temp_dir) in str(filepath)
        return MockFile()

    # Mock os.chmod to prevent permission errors on Windows
    def mock_chmod(path, mode):
        file_operations["chmod_called"] = True

    # Apply mocks
    with (
        patch("plugins.slack_plugin.slack_plugin.DEAD_LETTER_DIR", str(temp_dir)),
        patch("aiofiles.open", mock_aiofiles_open),
        patch("os.chmod", mock_chmod),
    ):

        # Ensure the temp_dir exists
        temp_dir.mkdir(exist_ok=True)

        # Run the function
        await dead_letter_to_file(event, "test_reason")

        # Verify that file operations were performed
        assert file_operations["open_called"], "aiofiles.open was not called"
        assert file_operations["write_called"], "File write was not called"
        assert file_operations["chmod_called"], "os.chmod was not called"


# --- Run Tests ---
if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])
