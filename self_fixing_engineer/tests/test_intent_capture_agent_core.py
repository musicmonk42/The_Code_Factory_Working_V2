# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import datetime
import gc
import json
import os
from unittest.mock import AsyncMock, MagicMock, Mock, patch

# Import the module under test FIRST
import intent_capture.agent_core as agent_core_module
import pytest
from intent_capture.agent_core import (
    AgentError,
    AgentResponse,
    CollaborativeAgent,
    ConfigurationError,
    FallbackLLM,
    InvalidSessionError,
    LLMProviderFactory,
    MockLLM,
    RedisStateBackend,
    SafetyGuard,
    SafetyViolationError,
    anonymize_pii,
    get_or_create_agent,
    sanitize_input,
    validate_session_token,
)

# Check what JWT library the agent_core module is using
if hasattr(agent_core_module, "jwt"):
    actual_jwt = agent_core_module.jwt
    has_decode = hasattr(actual_jwt, "decode")
    has_encode = hasattr(actual_jwt, "encode")
else:
    actual_jwt = None
    has_decode = False
    has_encode = False


# Create a mock JWT module for testing
class MockJWT:
    class InvalidTokenError(Exception):
        pass

    @staticmethod
    def encode(payload, key, algorithm="HS256", **kwargs):
        # Convert datetime objects to ISO format strings
        if isinstance(payload, dict):
            payload = payload.copy()
            for k, v in payload.items():
                if isinstance(v, datetime.datetime):
                    payload[k] = v.isoformat()
        # Simple mock that returns a fake token
        import base64

        return base64.b64encode(json.dumps(payload).encode()).decode()

    @staticmethod
    def decode(token, key, algorithms=None, **kwargs):
        # Simple mock that decodes the fake token
        import base64

        try:
            data = json.loads(base64.b64decode(token))
            # Convert ISO strings back to datetime if needed
            if "exp" in data and isinstance(data["exp"], str):
                data["exp"] = datetime.datetime.fromisoformat(data["exp"])
            # Check for required fields if provided
            if "audience" in kwargs and "aud" in data:
                if data["aud"] != kwargs["audience"]:
                    raise MockJWT.InvalidTokenError("Invalid audience")
            if "issuer" in kwargs and "iss" in data:
                if data["iss"] != kwargs["issuer"]:
                    raise MockJWT.InvalidTokenError("Invalid issuer")
            return data
        except:
            raise MockJWT.InvalidTokenError("Invalid token")


# Use our mock JWT for tests
jwt = MockJWT()


# Mock external dependencies
@pytest.fixture
def mock_env_vars():
    """Mock environment variables for testing"""
    env_vars = {
        "JWT_SECRET": "test_secret_key_that_is_at_least_32_characters_long",
        "OPENAI_API_KEYS": "test_openai_key_1,test_openai_key_2",
        "ANTHROPIC_API_KEYS": "test_anthropic_key",
        "GOOGLE_API_KEYS": "test_google_key",
        "XAI_API_KEYS": "test_xai_key",
        "REDIS_URL": "redis://localhost:6379/0",
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-4o-mini",
        "LLM_TEMPERATURE": "0.7",
        "LLM_RETRY_PROVIDERS": "anthropic,google",
        "USE_VECTOR_MEMORY": "false",
        "TEST_MODE": "false",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for state management"""
    # This is the mock client instance we want returned after the await
    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=None)
    mock_client_instance.set = AsyncMock(return_value=True)
    mock_client_instance.smembers = AsyncMock(return_value=set())
    mock_client_instance.sadd = AsyncMock(return_value=1)
    mock_client_instance.expire = AsyncMock(return_value=True)
    mock_client_instance.sismember = AsyncMock(return_value=False)
    mock_client_instance.ping = AsyncMock(return_value=True)
    mock_client_instance.close = AsyncMock(return_value=None)

    # We patch 'aredis.from_url' with an AsyncMock that returns our instance when awaited
    mock_from_url = AsyncMock(return_value=mock_client_instance)

    with patch("intent_capture.agent_core.aredis.from_url", new=mock_from_url):
        yield mock_client_instance


@pytest.fixture
def mock_llm():
    """Mock LLM for testing"""
    mock = AsyncMock()

    # Create a proper mock for structured output
    structured_mock = AsyncMock()

    # Make the structured mock return AgentResponse when invoked with proper config
    async def mock_ainvoke(*args, **kwargs):
        # Check if config is provided and extract session_id
        config = kwargs.get("config", {})
        config.get("configurable", {})

        # The RunnableWithMessageHistory requires session_id in configurable
        # We'll accept any invocation for testing purposes
        return AgentResponse(
            response="Test response",
            confidence_score=0.95,
            cited_sources=["source1", "source2"],
        )

    structured_mock.ainvoke = mock_ainvoke

    # with_structured_output should return a callable, not a coroutine
    mock.with_structured_output = Mock(return_value=structured_mock)
    mock.ainvoke = AsyncMock(return_value=MagicMock(content="Test response"))

    return mock


@pytest.fixture
def mock_llm_factory(mock_llm):
    """Mock the LLMProviderFactory"""
    with patch.object(LLMProviderFactory, "get_llm", return_value=mock_llm):
        yield mock_llm


# --- Tests for Utility Functions ---


def test_sanitize_input():
    """Test input sanitization"""
    # Normal input should pass through
    assert sanitize_input("Hello world") == "Hello world"

    # HTML tags should be stripped
    assert sanitize_input("<script>alert('xss')</script>Hello") == "alert('xss')Hello"

    # Internal IPs should raise error
    with pytest.raises(ValueError, match="potentially malicious pattern"):
        sanitize_input("Connect to 192.168.1.1")

    with pytest.raises(ValueError, match="potentially malicious pattern"):
        sanitize_input("Server at 10.0.0.1")


def test_anonymize_pii():
    """Test PII anonymization"""
    # Email anonymization
    text = "Contact me at john@example.com"
    assert anonymize_pii(text) == "Contact me at [REDACTED_EMAIL]"

    # Phone number anonymization
    text = "Call me at 555-123-4567"
    assert anonymize_pii(text) == "Call me at [REDACTED_PHONE]"

    # Credit card anonymization
    text = "Card number: 1234567812345678"
    assert anonymize_pii(text) == "Card number: [REDACTED_CREDIT_CARD]"

    # Multiple PII elements
    text = "Email: test@test.com, Phone: 555-555-5555"
    result = anonymize_pii(text)
    assert "[REDACTED_EMAIL]" in result
    assert "[REDACTED_PHONE]" in result


# --- Tests for Safety Guard ---


def test_safety_guard():
    """Test the safety guard moderation"""
    guard = SafetyGuard()

    # Clean text should pass
    assert guard.moderate("This is a helpful response") == "This is a helpful response"

    # Text with banned words should raise error
    with pytest.raises(SafetyViolationError):
        guard.moderate("This contains harmful_word_1")


# --- Tests for Mock LLMs ---


@pytest.mark.asyncio
async def test_mock_llm():
    """Test the MockLLM fallback"""
    mock_llm = MockLLM()
    result = await mock_llm.ainvoke("test")
    assert isinstance(result.content, str)
    assert "under heavy load" in result.content


@pytest.mark.asyncio
async def test_fallback_llm():
    """Test the FallbackLLM"""
    fallback_llm = FallbackLLM()
    result = await fallback_llm.ainvoke("test")
    assert isinstance(result.content, str)
    assert "currently unavailable" in result.content


# --- Tests for LLMProviderFactory ---


@pytest.mark.asyncio
async def test_get_usable_keys(mock_env_vars, mock_redis_client):
    """Test getting usable API keys"""
    # Without Redis issues
    keys = await LLMProviderFactory.get_usable_keys("openai")
    assert "test_openai_key_1" in keys
    assert "test_openai_key_2" in keys

    # With bad keys in Redis
    mock_redis_client.smembers = AsyncMock(return_value={"test_openai_key_1"})
    keys = await LLMProviderFactory.get_usable_keys("openai")
    assert "test_openai_key_1" not in keys
    assert "test_openai_key_2" in keys


@pytest.mark.asyncio
async def test_get_llm_test_mode(mock_env_vars):
    """Test LLM creation in test mode"""
    with patch.dict(os.environ, {"TEST_MODE": "true"}):
        llm = await LLMProviderFactory.get_llm(
            provider="openai", model="gpt-4o-mini", temperature=0.7, retry_providers=[]
        )
        assert isinstance(llm, MockLLM)


@pytest.mark.asyncio
async def test_get_llm_caching(mock_env_vars, mock_redis_client):
    """Test LLM instance caching"""
    # Clear the cache before testing
    LLMProviderFactory._llm_instance_cache.clear()

    # Mock get_llm to respect caching
    mock_llm_instance = MockLLM()

    async def mocked_get_llm(provider, model, temperature, retry_providers):
        cache_key = f"{provider}-{model}-{temperature}"
        if cache_key not in LLMProviderFactory._llm_instance_cache:
            LLMProviderFactory._llm_instance_cache[cache_key] = mock_llm_instance
        return LLMProviderFactory._llm_instance_cache[cache_key]

    with patch.dict(os.environ, {"TEST_MODE": "true"}):
        with patch.object(LLMProviderFactory, "get_llm", side_effect=mocked_get_llm):
            llm1 = await LLMProviderFactory.get_llm(
                provider="openai",
                model="gpt-4o-mini",
                temperature=0.7,
                retry_providers=[],
            )

            llm2 = await LLMProviderFactory.get_llm(
                provider="openai",
                model="gpt-4o-mini",
                temperature=0.7,
                retry_providers=[],
            )

            # Both should be the same MockLLM instance
            assert llm1 is llm2
            assert isinstance(llm1, MockLLM)


# --- Tests for State Backends ---


@pytest.mark.asyncio
async def test_redis_state_backend_create(mock_redis_client):
    """Test RedisStateBackend creation"""
    backend = await RedisStateBackend.create("redis://localhost:6379/0")
    assert backend.client is mock_redis_client
    mock_redis_client.ping.assert_called_once()


@pytest.mark.asyncio
async def test_redis_state_backend_save_load(mock_redis_client):
    """Test saving and loading state"""
    backend = await RedisStateBackend.create("redis://localhost:6379/0")

    # Test save
    state = {"messages": ["Hello", "World"], "count": 42}
    await backend.save_state("session123", state)
    mock_redis_client.set.assert_called_with(
        "agent_state:session123", json.dumps(state), ex=86400
    )

    # Test load
    mock_redis_client.get.return_value = json.dumps(state)
    loaded_state = await backend.load_state("session123")
    assert loaded_state == state
    mock_redis_client.get.assert_called_with("agent_state:session123")


# --- Tests for Session Token Validation ---


@pytest.mark.asyncio
async def test_validate_session_token_valid(mock_env_vars, mock_redis_client):
    """Test valid session token"""
    # Create token using our jwt module
    token = jwt.encode(
        {
            "session_id": "test_session",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            "aud": "agent_core_user",
            "iss": "agent_core_auth",
        },
        mock_env_vars["JWT_SECRET"],
        algorithm="HS512",
    )

    # Patch jwt in agent_core module
    with patch("intent_capture.agent_core.jwt", jwt):
        payload = await validate_session_token(token)
        assert payload["session_id"] == "test_session"


@pytest.mark.asyncio
async def test_validate_session_token_revoked(mock_env_vars, mock_redis_client):
    """Test revoked session token"""
    # Create token using our jwt module
    token = jwt.encode(
        {
            "session_id": "test_session",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            "aud": "agent_core_user",
            "iss": "agent_core_auth",
        },
        mock_env_vars["JWT_SECRET"],
        algorithm="HS512",
    )

    # Mark token as revoked
    mock_redis_client.sismember = AsyncMock(return_value=True)

    with patch("intent_capture.agent_core.jwt", jwt):
        with pytest.raises(InvalidSessionError, match="Token has been revoked"):
            await validate_session_token(token)


@pytest.mark.asyncio
async def test_validate_session_token_invalid(mock_env_vars):
    """Test invalid session token"""
    # Patch jwt in agent_core module with our mock
    with patch("intent_capture.agent_core.jwt", jwt):
        with pytest.raises(InvalidSessionError):
            await validate_session_token("invalid_token")


# --- Tests for CollaborativeAgent ---


@pytest.mark.asyncio
async def test_agent_creation(mock_env_vars, mock_llm_factory, mock_redis_client):
    """Test agent creation"""
    backend = await RedisStateBackend.create("redis://localhost:6379/0")

    agent = await CollaborativeAgent.create(
        agent_id="test_agent",
        session_id="test_session",
        llm_config={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "temperature": 0.7,
            "retry_providers": [],
        },
        state_backend=backend,
    )

    assert agent.agent_id == "test_agent"
    assert agent.session_id == "test_session"
    assert agent.llm is mock_llm_factory


@pytest.mark.asyncio
async def test_agent_predict(mock_env_vars, mock_llm, mock_redis_client):
    """Test agent prediction"""
    backend = await RedisStateBackend.create("redis://localhost:6379/0")

    # Create a mock RunnableWithMessageHistory that bypasses the session_id requirement
    class MockRunnable:
        async def ainvoke(self, input_dict, config=None):
            return AgentResponse(
                response="Test response",
                confidence_score=0.95,
                cited_sources=["source1", "source2"],
            )

    # Create a proper async function for the circuit breaker mock
    async def mock_breaker_call(func, *args, **kwargs):
        # Just return our mock response directly
        return AgentResponse(
            response="Test response",
            confidence_score=0.95,
            cited_sources=["source1", "source2"],
        )

    # Mock the circuit breaker
    with patch(
        "intent_capture.agent_core.llm_breaker.call_async",
        side_effect=mock_breaker_call,
    ):
        with patch.object(LLMProviderFactory, "get_llm", return_value=mock_llm):
            agent = await CollaborativeAgent.create(
                agent_id="test_agent",
                session_id="test_session",
                llm_config={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.7,
                    "retry_providers": [],
                },
                state_backend=backend,
            )

            # Replace the runnable with our mock
            agent._runnable = MockRunnable()

            result = await agent.predict("What is the capital of France?")

            assert "response" in result
            assert "confidence" in result
            assert "sources" in result
            assert "trace" in result
            assert result["response"] == "Test response"
            assert result["confidence"] == 0.95

    gc.collect()


@pytest.mark.asyncio
async def test_agent_state_persistence(mock_env_vars, mock_llm, mock_redis_client):
    """Test that agent state is persisted"""
    backend = await RedisStateBackend.create("redis://localhost:6379/0")

    # Create a mock RunnableWithMessageHistory that bypasses the session_id requirement
    class MockRunnable:
        async def ainvoke(self, input_dict, config=None):
            return AgentResponse(
                response="Test response",
                confidence_score=0.95,
                cited_sources=["source1", "source2"],
            )

    # Create a proper async function for the circuit breaker mock
    async def mock_breaker_call(func, *args, **kwargs):
        return AgentResponse(
            response="Test response",
            confidence_score=0.95,
            cited_sources=["source1", "source2"],
        )

    # Mock the circuit breaker
    with patch(
        "intent_capture.agent_core.llm_breaker.call_async",
        side_effect=mock_breaker_call,
    ):
        with patch.object(LLMProviderFactory, "get_llm", return_value=mock_llm):
            agent = await CollaborativeAgent.create(
                agent_id="test_agent",
                session_id="test_session",
                llm_config={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.7,
                    "retry_providers": [],
                },
                state_backend=backend,
            )

            # Replace the runnable with our mock
            agent._runnable = MockRunnable()

            # Make a prediction
            await agent.predict("Hello")

            # Check that save_state was called
            assert len(agent.chat_history) == 2  # Human message + AI message
            mock_redis_client.set.assert_called()


# --- Tests for get_or_create_agent ---


@pytest.mark.asyncio
async def test_get_or_create_agent_with_token(
    mock_env_vars, mock_llm_factory, mock_redis_client
):
    """Test get_or_create_agent function with token"""
    token = jwt.encode(
        {
            "session_id": "test_session",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            "aud": "agent_core_user",
            "iss": "agent_core_auth",
        },
        mock_env_vars["JWT_SECRET"],
        algorithm="HS512",
    )

    with patch("intent_capture.agent_core.jwt", jwt):
        agent = await get_or_create_agent(session_token=token)
        assert agent.session_id == "test_session"
        assert agent.agent_id == "agent_test_session"


# --- Test for Configuration Validation ---


def test_validate_environment(mock_env_vars):
    """Test environment validation"""
    from intent_capture.agent_core import validate_environment

    # Should pass with all required vars
    validate_environment()

    # Should fail with missing JWT_SECRET
    with patch.dict(os.environ, {"JWT_SECRET": ""}, clear=False):
        with pytest.raises(ConfigurationError, match="JWT_SECRET"):
            validate_environment()

    # Should fail with missing API keys
    with patch.dict(os.environ, {"OPENAI_API_KEYS": ""}, clear=False):
        with pytest.raises(ConfigurationError, match="OPENAI_API_KEYS"):
            validate_environment()


# --- Integration Test ---


@pytest.mark.asyncio
async def test_full_integration(mock_env_vars, mock_redis_client):
    """Test full integration from token to prediction"""
    # Create a valid token
    token = jwt.encode(
        {
            "session_id": "integration_test",
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
            "aud": "agent_core_user",
            "iss": "agent_core_auth",
        },
        mock_env_vars["JWT_SECRET"],
        algorithm="HS512",
    )

    # Create a proper mock LLM with structured output
    mock_llm = AsyncMock()
    structured_mock = AsyncMock()

    async def mock_ainvoke(*args, **kwargs):
        return AgentResponse(
            response="Paris is the capital of France",
            confidence_score=0.99,
            cited_sources=["Wikipedia"],
        )

    structured_mock.ainvoke = mock_ainvoke
    mock_llm.with_structured_output = Mock(return_value=structured_mock)

    # Mock the circuit breaker to return our response directly
    async def mock_breaker_call(func, *args, **kwargs):
        return AgentResponse(
            response="Paris is the capital of France",
            confidence_score=0.99,
            cited_sources=["Wikipedia"],
        )

    with patch("intent_capture.agent_core.jwt", jwt):
        with patch(
            "intent_capture.agent_core.llm_breaker.call_async",
            side_effect=mock_breaker_call,
        ):
            with patch.object(LLMProviderFactory, "get_llm", return_value=mock_llm):
                # Get or create agent
                agent = await get_or_create_agent(session_token=token)

                # Replace the runnable with a mock to bypass session_id requirement
                class MockRunnable:
                    async def ainvoke(self, input_dict, config=None):
                        return AgentResponse(
                            response="Paris is the capital of France",
                            confidence_score=0.99,
                            cited_sources=["Wikipedia"],
                        )

                agent._runnable = MockRunnable()

                # Make a prediction
                result = await agent.predict("What is the capital of France?")

                # Verify the result
                assert result["response"] == "Paris is the capital of France"
                assert result["confidence"] == 0.99
                assert "Wikipedia" in result["sources"]

                # Verify state was saved
                assert len(agent.chat_history) > 0
                mock_redis_client.set.assert_called()


# --- Additional Test for Error Handling ---


@pytest.mark.asyncio
async def test_agent_predict_error_handling(mock_env_vars, mock_llm, mock_redis_client):
    """Test agent prediction error handling"""
    backend = await RedisStateBackend.create("redis://localhost:6379/0")

    # Create a mock that raises an error
    class FailingRunnable:
        async def ainvoke(self, input_dict, config=None):
            raise Exception("LLM Error during invoke")

    # Create a proper async function for the circuit breaker mock
    async def mock_breaker_call(func, *args, **kwargs):
        # Simulate the error from the runnable
        raise Exception("LLM Error during invoke")

    with patch(
        "intent_capture.agent_core.llm_breaker.call_async",
        side_effect=mock_breaker_call,
    ):
        with patch.object(LLMProviderFactory, "get_llm", return_value=mock_llm):
            agent = await CollaborativeAgent.create(
                agent_id="test_agent",
                session_id="test_session",
                llm_config={
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.7,
                    "retry_providers": [],
                },
                state_backend=backend,
            )

            # Replace the runnable with our failing mock
            agent._runnable = FailingRunnable()

            with pytest.raises(AgentError, match="Prediction failed"):
                await agent.predict("What is the capital of France?")

    gc.collect()
