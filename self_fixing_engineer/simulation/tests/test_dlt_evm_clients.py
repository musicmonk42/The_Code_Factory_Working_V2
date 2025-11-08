# tests/test_dlt_evm_clients.py

import pytest
import asyncio
import json
import uuid
import os
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from eth_account import Account
from pydantic import ValidationError

from simulation.plugins.dlt_clients.dlt_evm_clients import (
    EthereumClientWrapper,
    EVMConfig,
    AWSSecretsBackend,
    AzureKeyVaultBackend,
    GCPSecretManagerBackend
)
from simulation.plugins.dlt_clients.dlt_base import (
    BaseOffChainClient,
    DLTClientConfigurationError,
    DLTClientTransactionError,
    DLTClientQueryError,
    DLTClientAuthError,
    DLTClientResourceError,
    DLTClientTimeoutError,
    DLTClientValidationError,
    DLTClientConnectivityError,
    DLTClientCircuitBreakerError,
    DLTClientError,
    PRODUCTION_MODE,
    _base_logger,
    SECRETS_MANAGER,
    AUDIT,
    scrub_secrets
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
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', side_effect=lambda key, **kwargs: f"mock_secret_{key.lower()}")
    # Default to returning a valid private key
    SECRETS_MANAGER.get_secret.side_effect = lambda key, **kwargs: "0x" + "1" * 64 if "private_key" in key.lower() else "mock_secret"

@pytest.fixture
def mock_web3_provider(mocker):
    """Mocks the web3.py provider and eth module methods."""
    mock_w3 = MagicMock()
    mock_w3.is_connected = MagicMock(return_value=True)
    mock_w3.eth.chain_id = 1
    mock_w3.eth.get_code = MagicMock(return_value=b'0x123')
    mock_w3.eth.get_balance = MagicMock(return_value=1000000000000000000)  # 1 ETH
    mock_w3.eth.get_transaction_count = MagicMock(return_value=0)
    mock_w3.eth.send_raw_transaction = MagicMock(return_value=b"mock_tx_hash")
    mock_w3.eth.get_transaction_receipt = MagicMock(return_value=MagicMock(status=1, blockNumber=123))
    mock_w3.eth.wait_for_transaction_receipt = MagicMock(return_value=MagicMock(status=1, blockNumber=123))
    mock_w3.to_wei = lambda value, unit: int(value * (10**9)) if unit == 'gwei' else int(value * (10**18)) if unit == 'ether' else value
    mock_w3.from_wei = lambda value, unit: value / (10**9) if unit == 'gwei' else value / (10**18) if unit == 'ether' else value
    mock_w3.to_hex = lambda x: "0x" + x.hex() if isinstance(x, bytes) else str(x)
    mock_w3.to_bytes = lambda hexstr: bytes.fromhex(hexstr[2:] if hexstr.startswith('0x') else hexstr)
    mock_w3.eth.get_block = MagicMock(return_value=MagicMock(baseFeePerGas=20000000000))  # EIP-1559 mock
    mock_w3.eth.gas_price = 30000000000  # 30 Gwei
    mock_w3.eth.account.sign_transaction = MagicMock(return_value=MagicMock(rawTransaction=b"signed_tx"))
    
    # Mock the contract
    mock_contract = MagicMock()
    mock_contract.functions.writeCheckpoint = MagicMock(return_value=MagicMock(build_transaction=MagicMock()))
    mock_contract.functions.getLatestCheckpoint = MagicMock(return_value=MagicMock(call=MagicMock()))
    mock_contract.functions.readCheckpoint = MagicMock(return_value=MagicMock(call=MagicMock()))
    mock_contract.functions.getCheckpointByHash = MagicMock(return_value=MagicMock(call=MagicMock()))
    mock_contract.functions.rollbackCheckpoint = MagicMock(return_value=MagicMock(build_transaction=MagicMock()))
    mock_w3.eth.contract = MagicMock(return_value=mock_contract)
    
    mocker.patch('simulation.plugins.dlt_clients.dlt_evm_clients.Web3', return_value=mock_w3)
    mocker.patch('simulation.plugins.dlt_clients.dlt_evm_clients.HTTPProvider', return_value=MagicMock())
    return mock_w3

@pytest.mark.asyncio
async def test_evm_init_success(mock_off_chain, mock_web3_provider, mocker):
    """
    Test that the EVM client initializes successfully with a valid configuration.
    """
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))
    
    # Provide a private key directly for non-production mode
    mock_config = {
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "a" * 64,  # Provide private key directly
        }
    }
    
    client = EthereumClientWrapper(mock_config, mock_off_chain)
    
    assert isinstance(client, EthereumClientWrapper)
    # Fix: pydantic HttpUrl normalizes URLs to include trailing slash
    assert client.rpc_url in ["http://localhost:8545", "http://localhost:8545/"]
    assert client.chain_id == 1
    assert client.contract_address == "0x1234567890123456789012345678901234567890"

@pytest.mark.asyncio
async def test_evm_init_with_secrets_provider(mock_off_chain, mock_web3_provider, mocker):
    """
    Test initialization with secrets provider configuration.
    """
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))
    mocker.patch.dict(os.environ, {'ETHEREUM_PRIVATE_KEY': '0x' + 'b' * 64})
    
    mock_config = {
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            # No private_key provided, should fallback to env var
        }
    }
    
    client = EthereumClientWrapper(mock_config, mock_off_chain)
    assert isinstance(client, EthereumClientWrapper)

@pytest.mark.asyncio
async def test_evm_init_failure_missing_abi(mock_off_chain, mocker):
    """
    Test that initialization fails if the contract ABI file is not found.
    """
    mocker.patch('os.path.exists', return_value=False)  # ABI file doesn't exist
    
    mock_config = {
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "1" * 64,
        }
    }

    with pytest.raises(DLTClientConfigurationError) as excinfo:
        EthereumClientWrapper(mock_config, mock_off_chain)
    
    assert "Contract ABI not found" in str(excinfo.value)

@pytest.mark.asyncio
async def test_evm_init_failure_private_key_source_in_prod(mock_off_chain, mocker):
    """
    Test that initialization fails in production mode if a private key is provided directly.
    """
    mocker.patch('simulation.plugins.dlt_clients.dlt_evm_clients.PRODUCTION_MODE', True)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))
    
    mock_config = {
        "evm": {
            "rpc_url": "https://mainnet.infura.io/v3/YOUR-PROJECT-ID",  # Use HTTPS in prod
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "1" * 64,
        }
    }

    with pytest.raises(DLTClientValidationError) as excinfo:
        EthereumClientWrapper(mock_config, mock_off_chain)
    
    assert "In PRODUCTION_MODE, private_key must be loaded via 'secrets_provider'" in str(excinfo.value)

@pytest.mark.asyncio
async def test_evm_init_secrets_backend_unavailable(mock_off_chain, mock_web3_provider, mocker):
    """
    Test that the client fails if a configured secrets backend is unavailable.
    """
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))
    mocker.patch('simulation.plugins.dlt_clients.dlt_evm_clients.AWS_SECRETS_AVAILABLE', False)
    
    # Set environment to avoid validation error
    mocker.patch.dict(os.environ, {'ETHEREUM_PRIVATE_KEY': '0x' + 'c' * 64})
    
    mock_config = {
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "secrets_provider": "aws",
            "private_key_secret_id": "evm/privatekey",
        }
    }

    client = EthereumClientWrapper(mock_config, mock_off_chain)
    with pytest.raises(DLTClientConfigurationError) as excinfo:
        await client._ensure_initialized()
    
    assert "Secrets backend 'aws' requested but is not available" in str(excinfo.value)

@pytest.mark.asyncio
async def test_health_check_success(mock_off_chain, mock_web3_provider, mocker):
    """
    Test a successful health check returns a correct status dictionary.
    """
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', return_value='0x' + 'a' * 64)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))
    
    client = EthereumClientWrapper({
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "a" * 64,
            "min_eth_balance_for_tx": 0.001
        }
    }, mock_off_chain)
    
    await client._ensure_initialized()
    
    result = await client.health_check()
    
    assert result["status"] is True
    assert "connected and contract is reachable" in result["message"]
    assert result["details"]["chain_id"] == 1
    assert result["details"]["balance_eth"] == 1.0

@pytest.mark.asyncio
@pytest.mark.parametrize("chain_id,connected,contract_code,balance,expected_exception,message_part", [
    (2, True, b'0x123', 10**18, None, "wrong chain_id"),
    (1, False, b'0x123', 10**18, None, "Not connected"),
    (1, True, b'', 10**18, None, "No contract code"),
    (1, True, b'0x123', 10**14, None, "connected"),  # Low balance - should warn, not fail
])
async def test_health_check_failures(chain_id, connected, contract_code, balance, expected_exception, message_part, mock_off_chain, mock_web3_provider, mocker):
    """
    Test that health check correctly handles different failure scenarios.
    """
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', return_value='0x' + 'a' * 64)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))
    _base_logger.error = MagicMock()
    _base_logger.warning = MagicMock()

    client = EthereumClientWrapper({
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "a" * 64,
            "min_eth_balance_for_tx": 0.001
        }
    }, mock_off_chain)
    
    await client._ensure_initialized()
    
    mock_web3_provider.is_connected.return_value = connected
    mock_web3_provider.eth.chain_id = chain_id
    mock_web3_provider.eth.get_code.return_value = contract_code
    mock_web3_provider.eth.get_balance.return_value = balance

    result = await client.health_check()
    
    # Health check returns status dict instead of raising exceptions
    if not connected or chain_id != 1 or contract_code == b'':
        assert result["status"] is False
        assert message_part.lower() in result["message"].lower()
    else:
        assert result["status"] is True

@pytest.mark.asyncio
async def test_write_checkpoint_success(mock_off_chain, mock_web3_provider, mocker):
    """
    Test a successful write operation.
    """
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', return_value='0x' + 'a' * 64)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))

    client = EthereumClientWrapper({
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "a" * 64,
        }
    }, mock_off_chain)
    await client._ensure_initialized()
    
    mock_tx_hash = b'b' * 32
    mock_web3_provider.eth.send_raw_transaction.return_value = mock_tx_hash
    mock_web3_provider.eth.wait_for_transaction_receipt.return_value = MagicMock(status=1, blockNumber=123)
    mock_web3_provider.eth.get_transaction_receipt.return_value = MagicMock(status=1, blockNumber=123)
    
    tx_hash, off_chain_id, version = await client.write_checkpoint(
        checkpoint_name="test-checkpoint",
        hash="0x" + "c" * 64,
        prev_hash="0x" + "d" * 64,
        metadata={"key": "value"},
        payload_blob=b"mock_data"
    )

    assert tx_hash == "0x" + mock_tx_hash.hex()
    assert off_chain_id == "mock_off_chain_id"
    assert version == 123
    mock_off_chain.save_blob.assert_called_with("test-checkpoint", b"mock_data", correlation_id=None)

@pytest.mark.asyncio
async def test_read_checkpoint_success(mock_off_chain, mock_web3_provider, mocker):
    """
    Test a successful read operation.
    """
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', return_value='0x' + 'a' * 64)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))

    client = EthereumClientWrapper({
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "a" * 64,
        }
    }, mock_off_chain)
    await client._ensure_initialized()
    
    mock_entry_tuple = (
        bytes.fromhex("c" * 64),
        bytes.fromhex("d" * 64),
        json.dumps({"key": "value"}),
        "mock_off_chain_id",
        123,
    )
    mock_web3_provider.eth.contract().functions.getLatestCheckpoint.return_value.call.return_value = mock_entry_tuple
    
    result = await client.read_checkpoint("test-checkpoint")
    
    assert result["metadata"]["hash"] == "0x" + "c" * 64
    assert result["metadata"]["version"] == 123
    assert result["payload_blob"] == b"mock_payload_data"
    mock_off_chain.get_blob.assert_called_with("mock_off_chain_id", correlation_id=None)

@pytest.mark.asyncio
async def test_client_close_method(mock_off_chain, mock_web3_provider, mocker):
    """
    Test that the close method correctly handles cleanup.
    """
    mocker.patch.object(SECRETS_MANAGER, 'get_secret', return_value='0x' + 'a' * 64)
    mocker.patch('os.path.exists', return_value=True)
    mocker.patch('builtins.open', mocker.mock_open(read_data='[]'))

    client = EthereumClientWrapper({
        "evm": {
            "rpc_url": "http://localhost:8545",
            "chain_id": 1,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "a" * 64,
            "close_timeout": 0.5
        }
    }, mock_off_chain)

    await client.close()
    mock_off_chain.close.assert_called()

def test_safe_copy_dict_with_cycle():
    """
    Tests that _safe_copy_dict correctly handles dictionaries with cyclical references.
    """
    client = EthereumClientWrapper.__new__(EthereumClientWrapper)
    cyclic = {'a': 1}
    cyclic['b'] = cyclic  # Create cycle
    result = client._safe_copy_dict(cyclic)
    assert result['a'] == 1
    assert result['b'] == "... [cycle detected] ..."

def test_safe_copy_dict_redacts_sensitive():
    """
    Tests that _safe_copy_dict redacts dictionary values based on sensitive key names.
    """
    client = EthereumClientWrapper.__new__(EthereumClientWrapper)
    entry = {'private_key': 'secret_value', 'normal': 'value'}
    result = client._safe_copy_dict(entry)
    assert result['private_key'] == '***REDACTED_BY_KEY***'
    assert result['normal'] == 'value'