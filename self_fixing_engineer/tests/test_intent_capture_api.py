import asyncio
import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# FIX: Import from intent_capture.agent_core to use absolute path
from intent_capture.agent_core import AgentError, ConfigurationError
from fastapi import status
from httpx import ASGITransport, AsyncClient

# Use the robust 'jose' library for creating test tokens to avoid library conflicts.
from jose import jwt

# --- Test Fixtures ---


@pytest.fixture(scope="session")
def test_secret_key():
    """Provides a consistent secret key for tests."""
    return "a_very_strong_and_long_secret_key_for_unit_tests_thirty_two_chars"


@pytest.fixture(scope="function")
def app(test_secret_key):
    """Fixture to create a fresh FastAPI app instance for each test.
    
    Changed from scope="module" to scope="function" to:
    - Force cleanup after each test
    - Prevent memory accumulation
    - Avoid long-lived heavy objects (FastAPI app, HuggingFace models, Redis connections)
    """
    # Set environment variables BEFORE importing to prevent heavy initialization
    env_overrides = {
        "JWT_SECRET": test_secret_key,
        "REDIS_URL": "redis://mock-redis:6379/0",
        "TEST_MODE": "true",
        "USE_VECTOR_MEMORY": "false",
        "DISABLE_SENTRY": "1",
        "OTEL_SDK_DISABLED": "1",
        "TRUSTED_HOSTS": "localhost,127.0.0.1,testserver,test",  # Allow test client
    }
    
    with patch.dict(os.environ, env_overrides):
        # FIX: Correctly mock aredis.from_url to be awaitable
        mock_redis_client = AsyncMock()
        mock_redis_client.sismember.return_value = False
        mock_from_url = AsyncMock(return_value=mock_redis_client)

        # FIX: Mock the rate limiter's hit method to be async
        mock_limiter_hit = AsyncMock(return_value=True)
        
        # Mock HuggingFace pipeline to prevent model loading
        mock_hf_pipeline = MagicMock(return_value=[{"label": "SAFE", "score": 0.99}])

        # Import here to ensure environment variables are set
        import self_fixing_engineer.intent_capture.api as api_module
        
        # Apply patches after import using the actual module reference
        with patch.object(api_module, "aredis") as mock_aredis:
            mock_aredis.from_url = mock_from_url
            with patch.object(api_module, "hf_pipeline", mock_hf_pipeline):
                # Patch the limiter's hit method
                with patch.object(api_module.limiter.limiter, "hit", mock_limiter_hit):
                    app_instance = api_module.create_app()
                    yield app_instance


@pytest.fixture
async def async_client(app):
    """Asynchronous TestClient for making API calls."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def mock_get_or_create_agent():
    """Mocks the agent creation logic to avoid heavy operations."""
    mock_agent = MagicMock()
    mock_agent.predict = AsyncMock(
        return_value={
            "response": "mocked agent response",
            "trace": {"status": "mocked_success"},
        }
    )

    import self_fixing_engineer.intent_capture.api as api_module
    with patch.object(
        api_module, "get_or_create_agent", AsyncMock(return_value=mock_agent)
    ) as mock_func:
        yield mock_func


def create_test_token(secret_key, overrides=None):
    """Helper function to create JWT tokens for testing."""
    payload = {
        "sub": "test_user",
        "session_id": f"session_{uuid.uuid4().hex}",
        "tier": "standard",
        "consent_prune": True,
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    if overrides:
        payload.update(overrides)
    return jwt.encode(payload, secret_key, algorithm="HS512")


@pytest.fixture
def valid_token(test_secret_key):
    """Fixture for a valid session token."""
    return create_test_token(test_secret_key)


# --- Tests for /token Endpoint ---


@pytest.mark.asyncio
async def test_create_token_endpoint(async_client: AsyncClient):
    """Test that the /token endpoint successfully creates a JWT token."""
    response = await async_client.post("/token")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


# --- Tests for /predict Endpoint ---


@pytest.mark.asyncio
async def test_predict_success(
    async_client: AsyncClient,
    valid_token: str,
    mock_get_or_create_agent: AsyncMock,
    test_secret_key: str,
):
    """Test a successful call to the /predict endpoint with valid authentication and payload."""
    auth_token_payload = {
        "sub": "test_user",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    auth_token = jwt.encode(auth_token_payload, test_secret_key, algorithm="HS512")

    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {"user_input": "Hello, agent!", "session_token": valid_token}

    response = await async_client.post("/predict", json=payload, headers=headers)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["response"] == "mocked agent response"
    assert data["trace"]["status"] == "mocked_success"
    mock_get_or_create_agent.assert_awaited_with(valid_token)
    mock_get_or_create_agent.return_value.predict.assert_awaited_with(
        "Hello, agent!", timeout=30
    )


@pytest.mark.asyncio
async def test_predict_no_auth_header(async_client: AsyncClient, valid_token: str):
    """Test that /predict returns 401 Unauthorized without an Authorization header."""
    payload = {"user_input": "test", "session_token": valid_token}
    response = await async_client.post("/predict", json=payload)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_predict_invalid_token(async_client: AsyncClient):
    """Test that /predict returns 401 Unauthorized with a malformed token."""
    headers = {"Authorization": "Bearer invalidtoken"}
    payload = {"user_input": "test", "session_token": "invalidtoken"}
    response = await async_client.post("/predict", json=payload, headers=headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_predict_invalid_payload(async_client: AsyncClient, test_secret_key: str):
    """Test that /predict returns 422 Unprocessable Entity for a bad payload."""
    auth_token_payload = {
        "sub": "test_user",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    auth_token = jwt.encode(auth_token_payload, test_secret_key, algorithm="HS512")
    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {"user_input": "", "session_token": "some_token"}  # Fails validation
    response = await async_client.post("/predict", json=payload, headers=headers)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_predict_agent_error(
    async_client: AsyncClient,
    valid_token: str,
    mock_get_or_create_agent: AsyncMock,
    test_secret_key: str,
):
    """Test that /predict returns 400 Bad Request when the agent raises an AgentError."""
    auth_token_payload = {
        "sub": "test_user",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    auth_token = jwt.encode(auth_token_payload, test_secret_key, algorithm="HS512")

    mock_get_or_create_agent.side_effect = AgentError(
        "Something went wrong in the agent"
    )

    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {"user_input": "trigger error", "session_token": valid_token}

    response = await async_client.post("/predict", json=payload, headers=headers)

    # AgentError is a server-side error, returns HTTP 500
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    # The handler returns a generic error message for security
    assert "internal server error occurred in the agent" in response.json()["detail"]


@pytest.mark.asyncio
async def test_predict_timeout_error(
    async_client: AsyncClient,
    valid_token: str,
    mock_get_or_create_agent: AsyncMock,
    test_secret_key: str,
):
    """Test that /predict returns 504 Gateway Timeout on an asyncio.TimeoutError."""
    auth_token_payload = {
        "sub": "test_user",
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    auth_token = jwt.encode(auth_token_payload, test_secret_key, algorithm="HS512")
    mock_get_or_create_agent.return_value.predict.side_effect = asyncio.TimeoutError
    headers = {"Authorization": f"Bearer {auth_token}"}
    payload = {"user_input": "trigger timeout", "session_token": valid_token}

    response = await async_client.post("/predict", json=payload, headers=headers)

    assert response.status_code == status.HTTP_504_GATEWAY_TIMEOUT
    assert "Prediction timed out" in response.json()["detail"]


# --- Tests for /prune_sessions Endpoint ---


@pytest.mark.asyncio
async def test_prune_sessions_success(async_client: AsyncClient, test_secret_key: str):
    """Test successful data pruning when user has given consent."""
    token_payload = {
        "sub": "test_user",
        "consent_prune": True,
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    token = jwt.encode(token_payload, test_secret_key, algorithm="HS512")
    headers = {"Authorization": f"Bearer {token}"}
    response = await async_client.post("/prune_sessions", headers=headers)
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.asyncio
async def test_prune_sessions_forbidden(
    async_client: AsyncClient, test_secret_key: str
):
    """Test for 403 Forbidden when user has not consented to pruning."""
    token_payload = {
        "sub": "test_user",
        "consent_prune": False,
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iss": "agent_core_auth",
        "aud": "agent_core_user",
    }
    token = jwt.encode(token_payload, test_secret_key, algorithm="HS512")
    headers = {"Authorization": f"Bearer {token}"}
    response = await async_client.post("/prune_sessions", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "User has not consented" in response.json()["detail"]


# --- Tests for AppConfig ---


def test_app_config_secret_handling(monkeypatch, test_secret_key):
    """Test that AppConfig correctly loads secrets from the environment."""
    import self_fixing_engineer.intent_capture.api as api_module
    
    monkeypatch.setenv("JWT_SECRET", test_secret_key)

    config = api_module.AppConfig()

    assert config.JWT_SECRET_KEY == test_secret_key
    # Test the private helper method correctly
    assert config._get_secret("JWT_SECRET", "dummy/path") == test_secret_key

    # Test fallback to default
    assert (
        config._get_secret("NON_EXISTENT_SECRET", "dummy/path", default="default_val")
        == "default_val"
    )

    # FIX: Import is now corrected at the top of the file
    with pytest.raises(ConfigurationError):
        config._get_secret("NON_EXISTENT_SECRET", "dummy/path")


# --- Tests for Health Endpoint ---
@pytest.mark.asyncio
async def test_health_check(async_client: AsyncClient):
    """Test the /health endpoint returns correct status."""
    response = await async_client.get("/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "ok"
    assert "timestamp" in data
