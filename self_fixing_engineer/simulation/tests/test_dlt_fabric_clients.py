# tests/test_dlt_fabric_clients.py

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from simulation.plugins.dlt_clients.dlt_fabric_clients import (
    FabricClientWrapper
)
from simulation.plugins.dlt_clients.dlt_base import (
    DLTClientValidationError,
    SECRETS_MANAGER
)

# A mock off-chain client that can be passed to the DLT client
@pytest.fixture
def mock_off_chain():
    mock = MagicMock()
    mock.client_type = "MockOffChain"
    mock.save_blob = AsyncMock(return_value="mock_off_chain_id")
    mock.get_blob = AsyncMock(return_value=b"mock_payload_data")
    mock.health_check = AsyncMock(return_value={"status": True, "message": "OK", "details": {}})
    mock.close = AsyncMock()
    return mock

# Mock the secrets manager to control credential loading
@pytest.fixture(autouse=True)
def mock_secrets_manager(mocker):
    def mock_get_secret(key, **kwargs):
        if "token" in key.lower():
            return "mock_auth_token"
        return f"mock_secret_{key.lower()}"
    
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', side_effect=mock_get_secret)

# Clean up any resources after each test
@pytest.fixture(autouse=True)
async def cleanup():
    """Clean up resources after each test."""
    yield
    # Give a moment for any pending async operations
    await asyncio.sleep(0.1)

@pytest.fixture
def mock_aiohttp_session(mocker):
    """Mock aiohttp session for REST mode tests."""
    # Create a mock response that acts as an async context manager
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"status": "ok", "result": {}})
    mock_response.text = AsyncMock(return_value="OK")
    mock_response.raise_for_status = MagicMock()
    mock_response.release = AsyncMock()
    
    # Make the response work as an async context manager
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=None)
    
    # Create mock session
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.post = MagicMock(return_value=mock_response)
    mock_session.closed = False
    mock_session.close = AsyncMock()
    
    # Mock the ClientSession class in the fabric module
    mock_client_session = mocker.patch('simulation.plugins.dlt_clients.dlt_fabric_clients.aiohttp.ClientSession')
    mock_client_session.return_value = mock_session
    
    return mock_session

@pytest.fixture
def mock_fabric_sdk(mocker):
    """Mocks the Fabric SDK for SDK mode tests."""
    # Mock the import check
    mocker.patch('simulation.plugins.dlt_clients.dlt_fabric_clients.FABRIC_NATIVE_AVAILABLE', True)
    
    # Create mock SDK client
    mock_client = MagicMock()
    mock_client.new_channel = MagicMock()
    mock_client.new_peer = MagicMock()
    mock_client.new_orderer = MagicMock()
    mock_client.new_user = MagicMock(return_value=MagicMock())
    mock_client.set_user = MagicMock()
    mock_client.get_user = MagicMock(return_value=MagicMock())
    mock_client.query_chaincode = MagicMock(return_value=b'{"status": "ok"}')
    mock_client.invoke_chaincode = MagicMock(return_value=(b'{"version": 1}', "mock_tx_id"))
    
    # Mock the SDK classes
    mock_sdk_class = mocker.patch('simulation.plugins.dlt_clients.dlt_fabric_clients.FabricSDKClient', create=True)
    mock_sdk_class.return_value = mock_client
    
    # Also mock the user class
    mocker.patch('simulation.plugins.dlt_clients.dlt_fabric_clients.FabricUser', create=True)
    
    return mock_client

@pytest.mark.asyncio
async def test_fabric_rest_mode_init_success(mock_off_chain, mock_aiohttp_session, mocker):
    """
    Test that the Fabric client initializes successfully in REST mode.
    """
    mock_config = {
        "fabric": {
            "mode": "rest",
            "rest_api_url": "http://localhost:4000",
            "rest_api_auth_token": "mock_token",
        }
    }
    
    client = FabricClientWrapper(mock_config, mock_off_chain)
    
    try:
        assert isinstance(client, FabricClientWrapper)
        assert client.mode == "rest"
        # Pydantic's AnyHttpUrl normalizes URLs by adding a trailing slash
        assert client.rest_api_url == "http://localhost:4000/"
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_fabric_sdk_mode_init_success(mock_off_chain, mock_fabric_sdk, mocker):
    """
    Test that the Fabric client initializes successfully in SDK mode.
    """
    # Mock PRODUCTION_MODE to False to skip path validation
    mocker.patch('simulation.plugins.dlt_clients.dlt_fabric_clients.PRODUCTION_MODE', False)
    
    mock_config = {
        "fabric": {
            "mode": "sdk",
            "msp_id": "Org1MSP",
            "channel": "mychannel",
            "chaincode_id": "mycc",
            "user_name": "user1",
            "cert_path": "/path/to/cert.pem",
            "key_path": "/path/to/key.pem",
            "peers": {
                "peer0.org1.example.com": {
                    "url": "grpcs://localhost:7051"
                }
            }
        }
    }
    
    client = FabricClientWrapper(mock_config, mock_off_chain)
    
    try:
        assert isinstance(client, FabricClientWrapper)
        assert client.mode == "sdk"
        assert client.msp_id == "Org1MSP"
        assert client.channel_name == "mychannel"
        assert client.chaincode_id == "mycc"
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_fabric_init_failure_invalid_mode(mock_off_chain, mocker):
    """
    Test that initialization fails with an invalid mode.
    """
    mock_config = {
        "fabric": {
            "mode": "invalid_mode",  # Invalid
            "rest_api_url": "http://localhost:4000"
        }
    }
    
    with pytest.raises(DLTClientValidationError) as excinfo:
        FabricClientWrapper(mock_config, mock_off_chain)
    
    assert "Invalid Fabric client configuration" in str(excinfo.value)

@pytest.mark.asyncio
async def test_fabric_init_failure_missing_rest_url(mock_off_chain, mocker):
    """
    Test that REST mode fails without rest_api_url.
    """
    mock_config = {
        "fabric": {
            "mode": "rest"
            # Missing rest_api_url - this should fail validation
        }
    }
    
    with pytest.raises(DLTClientValidationError) as excinfo:
        FabricClientWrapper(mock_config, mock_off_chain)
    
    # The error message will mention that REST mode requires rest_api_url
    assert "REST mode requires" in str(excinfo.value) or "rest_api_url" in str(excinfo.value)

@pytest.mark.asyncio
async def test_fabric_init_failure_missing_sdk_fields(mock_off_chain, mock_fabric_sdk, mocker):
    """
    Test that SDK mode fails without required fields.
    """
    mock_config = {
        "fabric": {
            "mode": "sdk",
            "msp_id": "Org1MSP"
            # Missing other required fields for SDK mode
        }
    }
    
    with pytest.raises(DLTClientValidationError) as excinfo:
        FabricClientWrapper(mock_config, mock_off_chain)
    
    assert "SDK mode requires" in str(excinfo.value)

@pytest.mark.asyncio
async def test_health_check_rest_mode_success(mock_off_chain, mock_aiohttp_session, mocker):
    """
    Test successful health check in REST mode.
    """
    client = FabricClientWrapper({
        "fabric": {
            "mode": "rest",
            "rest_api_url": "http://localhost:4000",
            "health_check_timeout": 10
        }
    }, mock_off_chain)
    
    try:
        # Configure the mock response for health check
        mock_response = mock_aiohttp_session.get.return_value
        mock_response.status = 200
        mock_response.json.return_value = {"status": "healthy", "version": "2.0"}
        
        result = await client.health_check()
        
        assert result["status"] is True
        assert "reachable and responding" in result["message"]
        assert result["details"]["status"] == "healthy"
        
        # Verify the session was called with the correct URL
        mock_aiohttp_session.get.assert_called_once()
        call_args = mock_aiohttp_session.get.call_args
        assert "health" in str(call_args)
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_health_check_sdk_mode_success(mock_off_chain, mock_fabric_sdk, mocker):
    """
    Test successful health check in SDK mode.
    """
    # Mock PRODUCTION_MODE to False to skip path validation
    mocker.patch('simulation.plugins.dlt_clients.dlt_fabric_clients.PRODUCTION_MODE', False)
    
    # Mock file operations for certificates
    mocker.patch('builtins.open', mocker.mock_open(read_data=b'cert_data'))
    
    client = FabricClientWrapper({
        "fabric": {
            "mode": "sdk",
            "msp_id": "Org1MSP",
            "channel": "mychannel",
            "chaincode_id": "mycc",
            "user_name": "user1",
            "cert_path": "/path/to/cert.pem",
            "key_path": "/path/to/key.pem",
            "peers": {
                "peer0.org1.example.com": {
                    "url": "grpcs://localhost:7051"
                }
            }
        }
    }, mock_off_chain)
    
    try:
        # Configure the SDK mock response
        mock_fabric_sdk.query_chaincode.return_value = b'{"version": "1.0", "status": "ok"}'
        
        result = await client.health_check()
        
        assert result["status"] is True
        assert "reachable" in result["message"]
        
        # Verify the SDK was called
        mock_fabric_sdk.query_chaincode.assert_called_once()
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_write_checkpoint_rest_mode_success(mock_off_chain, mock_aiohttp_session, mocker):
    """
    Test successful write operation in REST mode.
    """
    client = FabricClientWrapper({
        "fabric": {
            "mode": "rest",
            "rest_api_url": "http://localhost:4000",
            "invoke_timeout": 60
        }
    }, mock_off_chain)
    
    try:
        # Configure the mock response for write operation
        mock_response = mock_aiohttp_session.post.return_value
        mock_response.status = 200
        mock_response.json.return_value = {
            "txid": "mock_tx_id",
            "result": {"version": 123}
        }
        
        tx_id, off_chain_id, version = await client.write_checkpoint(
            checkpoint_name="test-checkpoint",
            hash="0x" + "a" * 64,
            prev_hash="0x" + "b" * 64,
            metadata={"key": "value"},
            payload_blob=b"test_data"
        )
        
        assert tx_id == "mock_tx_id"
        assert off_chain_id == "mock_off_chain_id"
        assert version == 123
        mock_off_chain.save_blob.assert_called_once()
        
        # Verify the session was called with the correct URL
        mock_aiohttp_session.post.assert_called_once()
        call_args = mock_aiohttp_session.post.call_args
        assert "invoke" in str(call_args)
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_read_checkpoint_rest_mode_success(mock_off_chain, mock_aiohttp_session, mocker):
    """
    Test successful read operation in REST mode.
    """
    client = FabricClientWrapper({
        "fabric": {
            "mode": "rest",
            "rest_api_url": "http://localhost:4000",
            "query_timeout": 30
        }
    }, mock_off_chain)
    
    try:
        # Configure the mock response for read operation
        mock_response = mock_aiohttp_session.post.return_value
        mock_response.status = 200
        mock_response.json.return_value = {
            "result": {
                "dataHash": "0x" + "c" * 64,
                "prevHash": "0x" + "d" * 64,
                "metadataJson": '{"key": "value"}',
                "offChainRef": "mock_off_chain_id",
                "version": 789,
                "txId": "read_tx_id"
            }
        }
        
        result = await client.read_checkpoint("test-checkpoint")
        
        assert result["metadata"]["hash"] == "0x" + "c" * 64
        assert result["metadata"]["version"] == 789
        assert result["payload_blob"] == b"mock_payload_data"
        mock_off_chain.get_blob.assert_called_with("mock_off_chain_id", correlation_id=None)
        
        # Verify the session was called with the correct URL
        mock_aiohttp_session.post.assert_called_once()
        call_args = mock_aiohttp_session.post.call_args
        assert "query" in str(call_args)
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_client_close_rest_mode(mock_off_chain, mock_aiohttp_session, mocker):
    """
    Test that close method properly cleans up in REST mode.
    """
    client = FabricClientWrapper({
        "fabric": {
            "mode": "rest",
            "rest_api_url": "http://localhost:4000",
            "close_timeout": 5.0
        }
    }, mock_off_chain)
    
    # Get a session to initialize it
    await client._get_session()
    
    # Close the client
    await client.close()
    
    # Verify close was called on session
    mock_aiohttp_session.close.assert_called()
    mock_off_chain.close.assert_called()

@pytest.mark.asyncio
async def test_rate_limiting(mock_off_chain, mock_aiohttp_session, mocker):
    """
    Test that rate limiting is enforced.
    """
    mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)
    
    client = FabricClientWrapper({
        "fabric": {
            "mode": "rest",
            "rest_api_url": "http://localhost:4000",
            "rate_limit_requests_per_second": 2.0  # 2 requests per second
        }
    }, mock_off_chain)
    
    try:
        # Configure the mock response for health check
        mock_response = mock_aiohttp_session.get.return_value
        mock_response.status = 200
        mock_response.json.return_value = {"status": "ok"}
        
        # Make two rapid health checks
        await client.health_check()
        await client.health_check()
        
        # The second call should trigger rate limiting
        mock_sleep.assert_called()
    finally:
        await client.close()