import asyncio
import logging
import os
from typing import Any, Dict

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import ClientError, ClientResponse
from aiohttp.client_exceptions import ClientResponseError

# Import the clients and related components
from arbiter.meta_learning_orchestrator.clients import AgentConfigurationService, MLPlatformClient

# Use centralized OpenTelemetry configuration
from arbiter.otel_config import get_tracer
from prometheus_client import CollectorRegistry, Counter, Histogram
from pytest_mock import MockerFixture
from tenacity import RetryError  # Import RetryError here

# Import or mock PIIRedactorFilter if logging_utils is available
try:
    from arbiter.meta_learning_orchestrator.logging_utils import PIIRedactorFilter
except ImportError:

    class PIIRedactorFilter:
        def _redact_dict(self, data: Dict[str, Any], seen=None, depth=0) -> Dict[str, Any]:
            """Mock redactor with matching signature"""
            return data.copy()  # No redaction for tests if not available

    logging.warning("PIIRedactorFilter not found, using a placeholder for tests.")

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Get tracer for this module
tracer = get_tracer(__name__)

# Sample environment variables
SAMPLE_ENV = {
    "ML_PLATFORM_API_KEY": "test_api_key",
    "LOG_LEVEL": "DEBUG",
    "DISABLE_HTTP_CACHE": "true",  # Disable cache for tests
}


@pytest_asyncio.fixture(autouse=True)
async def setup_env(mocker: MockerFixture):
    """Set up environment variables."""
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)


@pytest_asyncio.fixture
async def mock_response(mocker: MockerFixture):
    """Fixture for mocked aiohttp ClientResponse."""
    _mock_response = mocker.MagicMock(spec=ClientResponse)
    _mock_response.status = 200
    _mock_response.raise_for_status = mocker.MagicMock()
    _mock_response.json = mocker.AsyncMock(return_value={"success": True, "job_id": "mock_job_id"})
    _mock_response.headers = {"Content-Type": "application/json"}
    _mock_response.text = mocker.AsyncMock(return_value='{"success": true}')
    _mock_response.ok = True

    # Setup context manager
    _mock_response.__aenter__ = mocker.AsyncMock(return_value=_mock_response)
    _mock_response.__aexit__ = mocker.AsyncMock(return_value=None)

    return _mock_response


@pytest_asyncio.fixture
async def mock_session(mocker: MockerFixture, mock_response):
    """Fixture for mocked aiohttp ClientSession."""
    mock_session = mocker.MagicMock(spec=aiohttp.ClientSession)

    # Create a proper async context manager for request
    class MockRequestContext:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    # Make request return the context manager
    mock_session.request = mocker.MagicMock(return_value=MockRequestContext(mock_response))
    mock_session.close = mocker.AsyncMock()
    mock_session.closed = False

    yield mock_session


@pytest_asyncio.fixture
async def ml_client(mock_session):
    """Fixture for MLPlatformClient with mocked session."""
    client = MLPlatformClient(endpoint="http://mock-ml-platform.com", session=mock_session)
    yield client
    if hasattr(client, "close"):
        await client.close()


@pytest_asyncio.fixture
async def agent_client(mock_session):
    """Fixture for AgentConfigurationService with mocked session."""
    client = AgentConfigurationService(
        endpoint="http://mock-agent-config.com", session=mock_session
    )
    yield client
    if hasattr(client, "close"):
        await client.close()


@pytest_asyncio.fixture(autouse=True)
async def clear_metrics_and_traces():
    """Clear Prometheus metrics and OpenTelemetry traces using isolated registry."""
    # Create isolated registry for tests
    test_registry = CollectorRegistry()

    # Create test-specific metrics
    test_http_calls_total = Counter(
        "http_calls_total",
        "Total HTTP calls",
        ["client_name", "method", "status"],
        registry=test_registry,
    )
    test_http_call_latency_seconds = Histogram(
        "http_call_latency_seconds",
        "HTTP call latency",
        ["client_name", "method", "status"],
        registry=test_registry,
    )

    # Patch the metrics in the clients module
    import arbiter.meta_learning_orchestrator.clients as clients_module

    original_calls_total = getattr(clients_module, "HTTP_CALLS_TOTAL", None)
    original_latency_seconds = getattr(clients_module, "HTTP_CALL_LATENCY_SECONDS", None)

    clients_module.HTTP_CALLS_TOTAL = test_http_calls_total
    clients_module.HTTP_CALL_LATENCY_SECONDS = test_http_call_latency_seconds

    yield

    # Restore original metrics if they existed
    if original_calls_total:
        clients_module.HTTP_CALLS_TOTAL = original_calls_total
    if original_latency_seconds:
        clients_module.HTTP_CALL_LATENCY_SECONDS = original_latency_seconds


@pytest.mark.asyncio
async def test_base_http_client_initialization(ml_client):
    """Test initialization of _BaseHTTPClient."""
    assert ml_client.endpoint == "http://mock-ml-platform.com"
    assert "Authorization" in ml_client.headers
    assert ml_client.headers["Authorization"] == "Bearer test_api_key"


@pytest.mark.asyncio
async def test_ml_client_train_model_success(ml_client, mock_session):
    """Test successful train_model (formerly trigger_training_job)."""
    training_data = {"data_path": "/path/to/data", "params": {"epochs": 10}}

    # Configure mock response
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True, "job_id": "mock_job_id"}

    job_id = await ml_client.train_model(training_data)
    assert job_id == "mock_job_id"

    # Verify the request was made correctly
    mock_session.request.assert_called_once()
    args, kwargs = mock_session.request.call_args
    assert args[0] == "POST"
    assert args[1] == "http://mock-ml-platform.com/train"


@pytest.mark.asyncio
async def test_ml_client_train_model_failure(ml_client, mocker: MockerFixture, mock_session):
    """Test train_model failure with retry."""
    # Configure mock_session.request to raise ClientError for all attempts
    mock_session.request.side_effect = ClientError("Simulated HTTP error")

    # Tenacity will wrap the exception in RetryError after all retries fail
    with pytest.raises(RetryError) as exc_info:
        await ml_client.train_model({"data_path": "/path/to/data", "params": {"epochs": 10}})

    # Verify the underlying exception is our ClientError
    assert isinstance(exc_info.value.last_attempt.exception(), ClientError)

    # Tenacity retries 5 times by default for MLPlatformClient
    assert mock_session.request.call_count == 5


@pytest.mark.asyncio
async def test_ml_client_get_training_status_success(ml_client, mock_session):
    """Test successful get_training_status (formerly get_training_job_status)."""
    job_id = "mock_job_id"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"status": "completed", "job_id": job_id}

    status = await ml_client.get_training_status(job_id)
    assert status == {"status": "completed", "job_id": job_id}

    # Verify the request
    mock_session.request.assert_called_once()
    args, kwargs = mock_session.request.call_args
    assert args[0] == "GET"
    assert args[1] == f"http://mock-ml-platform.com/training/{job_id}"


@pytest.mark.asyncio
async def test_ml_client_evaluate_model_success(ml_client, mock_session):
    """Test successful evaluate_model."""
    model_id = "mock_model_id"
    eval_data = {"test_data": "path/to/test"}
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"metrics": {"accuracy": 0.95}, "success": True}

    result = await ml_client.evaluate_model(model_id, eval_data)
    assert result["metrics"]["accuracy"] == 0.95
    assert result["success"] is True


@pytest.mark.asyncio
async def test_ml_client_deploy_model_success(ml_client, mock_session):
    """Test successful deploy_model."""
    model_id = "mock_model_id"
    version = "v1.0"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True}

    success = await ml_client.deploy_model(model_id, version)
    assert success is True


@pytest.mark.asyncio
async def test_agent_client_update_prioritization_weights_success(agent_client, mock_session):
    """Test successful update_prioritization_weights."""
    weights = {"weight1": 0.5}
    version = "v1"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True}

    success = await agent_client.update_prioritization_weights(weights, version)
    assert success


@pytest.mark.asyncio
async def test_agent_client_update_policy_rules_success(agent_client, mock_session):
    """Test successful update_policy_rules."""
    rules = {"rule1": "value"}
    version = "v1"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True}

    success = await agent_client.update_policy_rules(rules, version)
    assert success


@pytest.mark.asyncio
async def test_agent_client_update_rl_policy_success(agent_client, mock_session):
    """Test successful update_rl_policy."""
    policy_model_id = "mock_policy_id"
    version = "v1"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True}

    success = await agent_client.update_rl_policy(policy_model_id, version)
    assert success


@pytest.mark.asyncio
async def test_agent_client_delete_config_success(agent_client, mock_session):
    """Test successful delete_config."""
    config_type = "policy"
    config_id = "mock_id"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True}

    success = await agent_client.delete_config(config_type, config_id)
    assert success


@pytest.mark.asyncio
async def test_agent_client_rollback_config_success(agent_client, mock_session):
    """Test successful rollback_config."""
    config_type = "policy"
    config_id = "mock_id"
    version = "v0"
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"success": True}

    success = await agent_client.rollback_config(config_type, config_id, version)
    assert success


@pytest.mark.asyncio
async def test_pii_redaction(ml_client, mocker: MockerFixture):
    """Test PII redaction in request payloads."""
    data = {
        "data_path": "/path",
        "params": {
            "email": "test@example.com",
            "password": "secret",
            "user_id": "user123",
        },
    }

    # Mock the PIIRedactorFilter
    mock_redactor_instance = mocker.MagicMock(spec=PIIRedactorFilter)
    mock_redactor_instance._redact_dict.return_value = {
        "data_path": "/path",
        "params": {
            "email": "[REDACTED]",
            "password": "[REDACTED]",
            "user_id": "[REDACTED]",
        },
    }
    mocker.patch(
        "arbiter.meta_learning_orchestrator.clients.PIIRedactorFilter",
        return_value=mock_redactor_instance,
    )

    # Mock successful response through the context manager
    mock_response = ml_client.session.request.return_value.response
    mock_response.json.return_value = {"success": True, "job_id": "test_id"}

    # Call a method that uses _request_with_redaction
    await ml_client.train_model(data)

    # Verify redaction was applied with correct signature
    mock_redactor_instance._redact_dict.assert_called_with(data, seen=set(), depth=0)


@pytest.mark.asyncio
async def test_timeout_handling(ml_client, mocker: MockerFixture, caplog):
    """Test timeout handling in requests."""
    mocker.patch.object(
        ml_client.session,
        "request",
        side_effect=asyncio.TimeoutError("Connection timed out"),
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(asyncio.TimeoutError):
            await ml_client.train_model({"data_path": "/path/to/data", "params": {"epochs": 10}})

    assert "Timeout in train_model for endpoint: http://mock-ml-platform.com" in caplog.text


@pytest.mark.asyncio
async def test_http_error_handling(ml_client, mocker: MockerFixture, caplog):
    """Test HTTP client error handling."""

    # Create a mock response that raises an error
    class MockErrorContext:
        def __init__(self, mocker):
            self.response = mocker.MagicMock(spec=ClientResponse)
            self.response.raise_for_status.side_effect = ClientResponseError(
                request_info=mocker.MagicMock(),
                history=(),
                status=500,
                message="Internal Server Error",
            )

        async def __aenter__(self):
            self.response.raise_for_status()  # Raise the error when entering context
            return self.response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    ml_client.session.request = mocker.MagicMock(return_value=MockErrorContext(mocker))

    with caplog.at_level(logging.ERROR):
        # Tenacity will wrap the ClientResponseError in RetryError after retries
        with pytest.raises(RetryError) as exc_info:
            await ml_client.get_training_status("mock_job_id")

        # Verify the underlying exception is our ClientResponseError
        assert isinstance(exc_info.value.last_attempt.exception(), ClientResponseError)

    assert "HTTP client error in get_training_status" in caplog.text


@pytest.mark.asyncio
async def test_concurrent_requests(ml_client, mock_session):
    """Test concurrent HTTP requests."""
    mock_response = mock_session.request.return_value.response
    mock_response.json.return_value = {"status": "running", "job_id": "concurrent_job"}

    async def concurrent_task(i):
        return await ml_client.get_training_status(f"job_{i}")

    tasks = [concurrent_task(i) for i in range(10)]
    statuses = await asyncio.gather(*tasks)

    assert len(statuses) == 10
    assert all(s == {"status": "running", "job_id": "concurrent_job"} for s in statuses)
    assert mock_session.request.call_count == 10


@pytest.mark.asyncio
async def test_session_close(mocker: MockerFixture):
    """Test session close in context manager."""
    # Test with externally managed session (should not close)
    mock_external_session = mocker.MagicMock(spec=aiohttp.ClientSession)
    mock_external_session.close = mocker.AsyncMock()
    mock_external_session.closed = False

    client_external = MLPlatformClient(endpoint="http://mock.com", session=mock_external_session)
    async with client_external:
        pass
    mock_external_session.close.assert_not_called()

    # Test with internally managed session (should close)
    # Mock the creation of the session
    mock_internal_session = mocker.MagicMock(spec=aiohttp.ClientSession)
    mock_internal_session.close = mocker.AsyncMock()
    mock_internal_session.closed = False

    with mocker.patch("aiohttp.ClientSession", return_value=mock_internal_session):
        client_internal = MLPlatformClient(endpoint="http://mock.com")
        async with client_internal:
            pass
        mock_internal_session.close.assert_called_once()


@pytest.mark.asyncio
async def test_no_api_key_warning(mocker: MockerFixture, caplog):
    """Test warning for missing API key."""
    mocker.patch.dict(os.environ, {"ML_PLATFORM_API_KEY": ""})

    with caplog.at_level(logging.WARNING):
        # Need to mock the session creation since we're not providing one
        mock_session = mocker.MagicMock(spec=aiohttp.ClientSession)
        with mocker.patch("aiohttp.ClientSession", return_value=mock_session):
            client_no_key = MLPlatformClient(endpoint="http://mock.com")

    assert "ML_PLATFORM_API_KEY environment variable not set" in caplog.text
    assert "Authorization" not in client_no_key.headers

    # Clean up
    if hasattr(client_no_key.session, "close"):
        await client_no_key.session.close()
