# tests/test_dlt_corda_clients.py

import pytest
import asyncio
import json
import uuid
import aiohttp
from unittest.mock import AsyncMock, MagicMock, Mock
from aiohttp.client_exceptions import ClientResponseError, ClientConnectionError

from simulation.plugins.dlt_clients.dlt_corda_clients import (
    CordaClientWrapper,
    CordaConfig
)
from simulation.plugins.dlt_clients.dlt_base import (
    DLTClientValidationError,
    DLTClientAuthError,
    DLTClientTransactionError,
    DLTClientConnectivityError,
    DLTClientTimeoutError,
    DLTClientCircuitBreakerError,
    BaseOffChainClient,
    PRODUCTION_MODE,
    _base_logger,
    SECRETS_MANAGER,
    AUDIT
)

# A mock off-chain client that can be passed to the DLT client
@pytest.fixture
def mock_off_chain():
    mock = AsyncMock(spec=BaseOffChainClient)
    mock.client_type = "MockOffChain"
    mock.save_blob.return_value = "mock_off_chain_id"
    mock.get_blob.return_value = b"mock_payload_data"
    return mock

# A mock for aiohttp.ClientResponse to control response content and status
def create_mock_response(status, text=None, json_data=None, headers=None):
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.headers = headers or {}
    mock_resp.text = AsyncMock(return_value=text or "")
    # Important: json() should be an async method that returns the actual data
    mock_resp.json = AsyncMock(return_value=json_data if json_data is not None else {})
    mock_resp.raise_for_status = MagicMock()
    mock_resp.release = AsyncMock()  # Add release method
    mock_resp.closed = False
    if status >= 400:
        mock_resp.raise_for_status.side_effect = ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=status,
            message=text or 'Error'
        )
    return mock_resp

@pytest.fixture
def mock_aiohttp(mocker):
    """Mocks the aiohttp.ClientSession for controlled testing of HTTP requests."""
    # Create a mock session instance
    mock_session_instance = MagicMock(spec=aiohttp.ClientSession)
    
    # Make the instance itself async-context-manager compatible
    mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
    mock_session_instance.__aexit__ = AsyncMock(return_value=None)
    
    # Replace get/post methods on the instance with AsyncMocks
    mock_session_instance.get = AsyncMock()
    mock_session_instance.post = AsyncMock()
    mock_session_instance.close = AsyncMock()
    mock_session_instance.closed = False
    
    # Mock the ClientSession constructor in the module where it's used
    # This needs to patch it where it's imported, not where it's defined
    mocker.patch('simulation.plugins.dlt_clients.dlt_corda_clients.aiohttp.ClientSession', return_value=mock_session_instance)
    
    # The fixture returns a dictionary of the mocked methods for easy access in tests
    return {
        'get': mock_session_instance.get,
        'post': mock_session_instance.post,
        'close': mock_session_instance.close,
        'session': mock_session_instance
    }

# Mock the secrets manager to control credential loading
@pytest.fixture(autouse=True)
def mock_secrets_manager(mocker):
    # This mock will be active for all tests in this file
    mock_get_secret = mocker.patch.object(SECRETS_MANAGER, 'get_secret')
    # Default to returning valid secrets
    mock_get_secret.side_effect = lambda key, **kwargs: "testuser" if "user" in key.lower() else "testpass"
    return mock_get_secret

# Mock scrub_secrets to avoid the TypeError
@pytest.fixture(autouse=True) 
def mock_scrub_secrets(mocker):
    """Mock scrub_secrets to simply return the input unchanged."""
    return mocker.patch('simulation.plugins.dlt_clients.dlt_corda_clients.scrub_secrets', side_effect=lambda x: x)

@pytest.mark.asyncio
async def test_corda_init_success(mock_off_chain, mock_secrets_manager):
    """
    Test that the Corda client initializes successfully with a valid configuration.
    """
    mock_config = {
        "corda": {
            "rpc_url": "http://localhost:8080",
        }
    }
    
    # This test relies on the default side_effect of mock_secrets_manager
    client = CordaClientWrapper(mock_config, mock_off_chain)
    
    assert isinstance(client, CordaClientWrapper)
    assert client.rpc_url == "http://localhost:8080/"
    assert client.user == "testuser"
    assert client.password == "testpass"

@pytest.mark.asyncio
async def test_corda_init_failure_invalid_config(mock_off_chain, mock_secrets_manager):
    """
    Test that the Corda client fails fast with an invalid configuration.
    """
    invalid_config = {
        "corda": {
            "rpc_url": "invalid-url", # This will fail Pydantic validation
        }
    }
    
    with pytest.raises(DLTClientValidationError) as excinfo:
        CordaClientWrapper(invalid_config, mock_off_chain)
        
    assert "Invalid Corda client configuration" in str(excinfo.value)

@pytest.mark.asyncio
@pytest.mark.parametrize("rpc_url,user,password,is_prod,should_fail", [
    ("http://localhost:8080", "user", "securepass123", False, False),  # OK in non-prod
    ("ftp://mock.com", "user", "securepass123", True, True),  # Invalid scheme
    ("http://localhost:8080", "dummy", "securepass123", True, True),  # Dummy user in prod
    ("http://localhost:8080", "validuser", "password", True, True),  # "password" as password in prod
    ("http://localhost:8080", "validuser", "securepass123", True, False),  # OK in prod
])
async def test_corda_init_production_mode_validation(rpc_url, user, password, is_prod, should_fail, mock_off_chain, mocker):
    """
    Test production mode validation for RPC URL and credentials.
    """
    mocker.patch('simulation.plugins.dlt_clients.dlt_corda_clients.PRODUCTION_MODE', is_prod)
    
    # Configure the secrets manager mock for this specific test case
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', side_effect=[user, password])

    config = {"corda": {"rpc_url": rpc_url}}
    
    if should_fail:
        with pytest.raises(DLTClientValidationError):
            CordaClientWrapper(config, mock_off_chain)
    else:
        try:
            client = CordaClientWrapper(config, mock_off_chain)
            assert isinstance(client, CordaClientWrapper)
        except DLTClientValidationError:
            pytest.fail("Client initialization unexpectedly failed.")

@pytest.mark.asyncio
async def test_health_check_success(mock_off_chain, mock_aiohttp):
    """
    Test a successful health check returns a correct status dictionary.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Create the mock response with proper json data
    mock_response_data = {"me": {"legalIdentity": "O=NodeA,L=London,C=GB"}}
    mock_response = create_mock_response(200, json_data=mock_response_data)
    
    # Mock the session.get to return our response directly
    mock_aiohttp['get'].return_value = mock_response
    
    # Mock the circuit breaker to await async functions properly
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute
    
    result = await client.health_check()
    
    assert result["status"] is True
    assert "reachable and authenticated" in result["message"]
    assert result["details"]["node_identity"] == "O=NodeA,L=London,C=GB"
    
    mock_aiohttp['get'].assert_called_once_with("http://localhost:8080/api/rest/corda/v4/me", timeout=30)

@pytest.mark.asyncio
@pytest.mark.parametrize("status,error_type,message_part", [
    (401, DLTClientAuthError, "authentication failed"),
    (429, DLTClientTransactionError, "rate limit or service unavailable"),
    (503, DLTClientTransactionError, "rate limit or service unavailable"),
    (500, DLTClientConnectivityError, "responded with error"),
])
async def test_health_check_failures(mock_off_chain, mock_aiohttp, status, error_type, message_part):
    """
    Test that health check correctly raises specific exceptions for different HTTP errors.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Create the mock response with error status
    mock_response = create_mock_response(status, text=f"Error {status}")
    
    # Mock the session.get to return our response directly
    mock_aiohttp['get'].return_value = mock_response
    
    # Mock the circuit breaker to await async functions properly
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute
    
    with pytest.raises(error_type) as excinfo:
        await client.health_check()
    
    assert message_part in str(excinfo.value)

@pytest.mark.asyncio
async def test_write_checkpoint_success(mock_off_chain, mock_aiohttp):
    """
    Test a successful write operation.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Mock the circuit breaker to handle async operations properly
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute

    mock_response_data = {
        "id": "mock_tx_id",
        "returnValue": {"result": {"version": 1}}
    }
    
    # Create mock response
    mock_response = create_mock_response(200, json_data=mock_response_data, text=json.dumps(mock_response_data))
    
    mock_aiohttp['post'].return_value = mock_response

    tx_id, off_chain_id, version = await client.write_checkpoint(
        checkpoint_name="test-checkpoint",
        hash="mock_hash",
        prev_hash="mock_prev_hash",
        metadata={"key": "value"},
        payload_blob=b"mock_data"
    )

    assert tx_id == "mock_tx_id"
    assert off_chain_id == "mock_off_chain_id"
    assert version == 1
    
    mock_off_chain.save_blob.assert_called_once()
    mock_aiohttp['post'].assert_called_once()

@pytest.mark.asyncio
async def test_write_checkpoint_retry_on_transient_error(mock_off_chain, mock_aiohttp, mocker):
    """
    Test that the client correctly handles a transient error by retrying.
    This test relies on the async_retry decorator working as expected.
    """
    # Configure retry policy for fast testing
    config = {
        "corda": {"rpc_url": "http://localhost:8080"},
        "retry_policy": {"retries": 2, "delay": 0.01}
    }
    client = CordaClientWrapper(config, mock_off_chain)
    
    # Mock circuit breaker
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute
    
    # Create error response
    error_response = create_mock_response(503, text="Service Unavailable")
    
    # Create success response
    mock_success_data = {"id": "mock_tx_id", "returnValue": {"result": {"version": 1}}}
    success_response = create_mock_response(200, json_data=mock_success_data, text=json.dumps(mock_success_data))
    
    # Simulate a 503 error followed by success
    mock_aiohttp['post'].side_effect = [error_response, success_response]
    
    await client.write_checkpoint("test", "hash", "prev_hash", {}, b"data")
    
    # The call should have happened twice due to the retry
    assert mock_aiohttp['post'].call_count == 2

@pytest.mark.asyncio
async def test_read_checkpoint_not_found_on_dlt(mock_off_chain, mock_aiohttp):
    """
    Test that a read operation correctly raises FileNotFoundError when a checkpoint is not found.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Mock the circuit breaker
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute
    
    # Simulate an empty result from the Corda node
    mock_response_data = {"returnValue": {"result": {}}}
    mock_response = create_mock_response(200, json_data=mock_response_data, text=json.dumps(mock_response_data))
    
    mock_aiohttp['post'].return_value = mock_response
    
    with pytest.raises(FileNotFoundError) as excinfo:
        await client.read_checkpoint("nonexistent-checkpoint")
    
    assert "not found or query returned empty result" in str(excinfo.value)

@pytest.mark.asyncio
async def test_read_checkpoint_off_chain_blob_not_found(mock_off_chain, mock_aiohttp):
    """
    Test that a read operation correctly raises FileNotFoundError when the off-chain blob is missing.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Mock circuit breaker to raise FileNotFoundError for get_blob
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        if func == mock_off_chain.get_blob:
            raise FileNotFoundError("Blob not found")
        
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute

    mock_response_data = {
        "returnValue": {
            "result": {
                "offChainRef": "missing_blob",
                "version": 1,
                "dataHash": "mock_hash",
                "prevHash": "mock_prev_hash",
                "metadataJson": "{}"
            }
        }
    }
    
    mock_response = create_mock_response(200, json_data=mock_response_data, text=json.dumps(mock_response_data))
    
    mock_aiohttp['post'].return_value = mock_response

    with pytest.raises(FileNotFoundError) as excinfo:
        await client.read_checkpoint("test-checkpoint")

    assert "Blob not found" in str(excinfo.value)

@pytest.mark.asyncio
async def test_rollback_checkpoint_success(mock_off_chain, mock_aiohttp):
    """
    Test a successful rollback operation.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Mock circuit breaker
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute
    
    mock_response_data = {
        "id": "mock_rollback_tx_id",
        "returnValue": {"result": {"version": 5, "dataHash": "rollback_hash"}}
    }
    
    mock_response = create_mock_response(200, json_data=mock_response_data, text=json.dumps(mock_response_data))
    
    mock_aiohttp['post'].return_value = mock_response

    result = await client.rollback_checkpoint(
        name="test-checkpoint",
        rollback_hash="rollback_hash"
    )
    
    assert result["tx_id"] == "mock_rollback_tx_id"
    assert result["version"] == 5

@pytest.mark.asyncio
async def test_session_management_and_closing(mock_off_chain, mock_aiohttp, mocker):
    """
    Test that a single aiohttp.ClientSession is reused and closed properly.
    """
    client = CordaClientWrapper({"corda": {"rpc_url": "http://localhost:8080"}}, mock_off_chain)
    
    # Mock circuit breaker
    async def mock_circuit_breaker_execute(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result
    
    client._circuit_breaker.execute = mock_circuit_breaker_execute
    
    # Mock health check to be successful
    mock_response_data = {"me": {"legalIdentity": "NodeA"}}
    mock_response = create_mock_response(200, json_data=mock_response_data)
    
    mock_aiohttp['get'].return_value = mock_response
    
    # Call a method that uses the session
    await client.health_check()
    
    # Get the session instance that was created
    session_instance = client._session
    assert session_instance is not None
    assert not session_instance.closed

    # Call again, should reuse the session
    await client.health_check()
    assert client._session is session_instance

    # Now, close the client
    await client.close()
    
    # Verify the session's close method was called
    session_instance.close.assert_called_once()
    assert client._session is None # Session should be cleared
    
    # Verify idempotency of close
    session_instance.close.reset_mock()
    await client.close()
    session_instance.close.assert_not_called()