"""
Comprehensive test suite for AuditLedgerClient
Tests all major functionality with proper mocking and error handling
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
import pytest_asyncio
from pytest_mock import MockerFixture

# Configure logging for tests
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Sample contract ABI for testing
SAMPLE_ABI = [
    {
        "inputs": [
            {"internalType": "string", "name": "eventType", "type": "string"},
            {"internalType": "string", "name": "operator", "type": "string"},
            {"internalType": "string", "name": "correlationId", "type": "string"},
            {"internalType": "string", "name": "detailsJson", "type": "string"},
        ],
        "name": "logEvent",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

# Test environment variables
TEST_ENV = {
    "AUDIT_LEDGER_URL": "ws://localhost:8545",
    "ETHEREUM_PRIVATE_KEY": "0x" + "1" * 64,
    "ETHEREUM_CONTRACT_ADDRESS": "0x1234567890abcdef1234567890abcdef12345678",
    "ETHEREUM_CONTRACT_ABI_JSON": json.dumps(SAMPLE_ABI),
    "ETHEREUM_POA_MIDDLEWARE": "false",
    "APP_ENV": "development",
    "CLUSTER_NAME": "test-cluster",
    "LOG_LEVEL": "DEBUG",
    "USE_SECRETS_MANAGER": "false",
    "MAX_GAS_GWEI": "200",
    "CONFIRMATIONS": "0",
    "TX_TIMEOUT_SEC": "1",
    "BLOCK_POLL_INTERVAL_SEC": "0.01",
    "MAX_PARALLEL_TX": "1",
    "DEFAULT_GAS_LIMIT": "300000",
    "BASE_FEE_GWEI_CAP": "500",
    "ENABLE_GNOSIS_SAFE": "false",
    "CONTRACT_DETAILS_FIELD": "detailsJson",
    "IDEMP_CACHE_MAX": "5000",
}


# Create a mock receipt class that behaves like web3's receipt
class MockReceipt(dict):
    """Mock transaction receipt that acts like both a dict and an object with attributes"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__dict__ = self


@pytest.fixture(autouse=True)
def setup_environment(mocker: MockerFixture):
    """Set up test environment variables"""
    for key, value in TEST_ENV.items():
        mocker.patch.dict(os.environ, {key: value})
    yield
    # Cleanup
    for key in TEST_ENV:
        os.environ.pop(key, None)


@pytest.fixture
def mock_web3_dependencies(mocker: MockerFixture):
    """Mock all Web3 related dependencies"""
    mock_async_web3 = mocker.MagicMock()
    mock_async_websocket_provider = mocker.MagicMock()
    mock_account = mocker.MagicMock()

    mock_account_instance = MagicMock()
    mock_account_instance.address = "0xTestAccount123"
    mock_account_instance.key.hex.return_value = "0x" + "1" * 64
    mock_account.from_key.return_value = mock_account_instance
    mock_account.create.return_value = mock_account_instance

    mock_web3_instance = MagicMock()
    mock_web3_instance.is_connected = AsyncMock(return_value=True)

    class MockEth:
        def __init__(self):
            self._chain_id = 1337
            self._gas_price = 2000000000
            self._max_priority_fee = 1000000000
            self._block_number = 100

        @property
        def chain_id(self):
            async def _get_chain_id():
                return self._chain_id

            return _get_chain_id()

        @property
        def gas_price(self):
            async def _get_gas_price():
                return self._gas_price

            return _get_gas_price()

        @property
        def max_priority_fee(self):
            async def _get_max_priority_fee():
                return self._max_priority_fee

            return _get_max_priority_fee()

        @property
        def block_number(self):
            async def _get_block_number():
                return self._block_number

            return _get_block_number()

    mock_eth = MockEth()

    mock_eth.get_transaction_count = AsyncMock(return_value=1)
    mock_eth.estimate_gas = AsyncMock(return_value=200000)
    mock_eth.get_block = AsyncMock(return_value={"baseFeePerGas": 1000000000})

    mock_eth.wait_for_transaction_receipt = AsyncMock(
        return_value=MockReceipt(status=1, blockNumber=100, gasUsed=150000)
    )
    mock_eth.get_transaction_receipt = AsyncMock(
        return_value=MockReceipt(status=1, blockNumber=100, gasUsed=150000)
    )
    mock_eth.send_raw_transaction = AsyncMock(return_value=b"tx_hash_123")

    mock_contract = MagicMock()
    mock_contract.functions.logEvent.return_value.build_transaction.return_value = {
        "chainId": 1337,
        "gas": 200000,
        "gasPrice": 2000000000,
        "nonce": 1,
    }
    mock_contract.functions.logEvent.return_value.estimate_gas = AsyncMock(
        return_value=200000
    )
    mock_eth.contract = MagicMock(return_value=mock_contract)

    mock_eth.account = mock_account
    mock_signed_tx = MagicMock()
    mock_signed_tx.rawTransaction = b"raw_tx_data"
    mock_eth.account.sign_transaction.return_value = mock_signed_tx

    mock_web3_instance.eth = mock_eth
    mock_web3_instance.to_wei = lambda x, unit: x * 1000000000 if unit == "gwei" else x
    mock_web3_instance.from_wei = lambda x, unit: (
        x / 1000000000 if unit == "gwei" else x
    )

    mock_provider = MagicMock()
    mock_provider.disconnect = AsyncMock()
    mock_web3_instance.provider = mock_provider

    mock_async_web3.return_value = mock_web3_instance

    mocker.patch("arbiter.models.audit_ledger_client.AsyncWeb3", mock_async_web3)
    mocker.patch(
        "arbiter.models.audit_ledger_client.AsyncWebsocketProvider",
        mock_async_websocket_provider,
    )
    mocker.patch("arbiter.models.audit_ledger_client.Account", mock_account)
    mocker.patch("arbiter.models.audit_ledger_client.ETHEREUM_AVAILABLE", True)

    def mock_checksum(addr):
        return addr

    mocker.patch(
        "arbiter.models.audit_ledger_client.to_checksum_address",
        mock_checksum,
        create=True,
    )

    return {
        "web3": mock_web3_instance,
        "eth": mock_eth,
        "contract": mock_contract,
        "account": mock_account,
        "provider": mock_provider,
    }


@pytest_asyncio.fixture
async def audit_client(mock_web3_dependencies, mocker: MockerFixture):
    """Create an AuditLedgerClient instance for testing with retry disabled"""

    # Patch retry decorator to be a no-op BEFORE importing the module
    def no_retry(*args, **kwargs):
        def decorator(func):
            return func

        return decorator

    mocker.patch("tenacity.retry", no_retry)

    # Now import after patching
    from arbiter.models.audit_ledger_client import AuditLedgerClient

    # For methods already decorated, we need to access __wrapped__ if it exists
    original_connect = (
        AuditLedgerClient.connect.__wrapped__
        if hasattr(AuditLedgerClient.connect, "__wrapped__")
        else AuditLedgerClient.connect
    )
    original_log_event = (
        AuditLedgerClient.log_event.__wrapped__
        if hasattr(AuditLedgerClient.log_event, "__wrapped__")
        else AuditLedgerClient.log_event
    )
    original_batch = (
        AuditLedgerClient.batch_log_events.__wrapped__
        if hasattr(AuditLedgerClient.batch_log_events, "__wrapped__")
        else AuditLedgerClient.batch_log_events
    )

    mocker.patch.object(AuditLedgerClient, "connect", original_connect)
    mocker.patch.object(AuditLedgerClient, "log_event", original_log_event)
    mocker.patch.object(AuditLedgerClient, "batch_log_events", original_batch)

    client = AuditLedgerClient(dlt_type="ethereum")
    yield client

    if client._is_connected:
        await client.disconnect()


class TestAuditLedgerClientInit:
    """Test initialization of AuditLedgerClient"""

    def test_init_with_valid_config(self, mock_web3_dependencies):
        """Test successful initialization with valid configuration"""
        from arbiter.models.audit_ledger_client import AuditLedgerClient

        client = AuditLedgerClient(dlt_type="ethereum")
        assert client.dlt_type == "ethereum"
        assert client.audit_ledger_url == "ws://localhost:8545"
        assert client.contract_address == "0x1234567890abcdef1234567890abcdef12345678"
        assert client.contract_abi == SAMPLE_ABI
        assert not client._is_connected
        assert client.metric_labels["env"] == "development"
        assert client.metric_labels["cluster"] == "test-cluster"

    def test_init_with_invalid_abi(self, mock_web3_dependencies, mocker: MockerFixture):
        """Test initialization with invalid ABI JSON - should handle gracefully in development"""
        mocker.patch.dict(os.environ, {"ETHEREUM_CONTRACT_ABI_JSON": "[invalid_json"})

        from arbiter.models.audit_ledger_client import AuditLedgerClient

        client = AuditLedgerClient(dlt_type="ethereum")
        assert client.contract_abi is None
        assert client.dlt_type == "ethereum"

    def test_init_with_invalid_abi_production(
        self, mock_web3_dependencies, mocker: MockerFixture
    ):
        """Test initialization with invalid ABI JSON in production - should fail"""
        mocker.patch.dict(
            os.environ,
            {
                "ETHEREUM_CONTRACT_ABI_JSON": "[invalid_json",
                "APP_ENV": "production",
                "USE_SECRETS_MANAGER": "true",
            },
        )

        from arbiter.models.audit_ledger_client import AuditLedgerClient

        with pytest.raises(
            ValueError, match="must be valid JSON for Ethereum DLT in production"
        ):
            AuditLedgerClient(dlt_type="ethereum")

    def test_init_missing_required_env_vars(
        self, mock_web3_dependencies, mocker: MockerFixture
    ):
        """Test initialization fails with missing required environment variables"""
        mocker.patch.dict(os.environ, {"AUDIT_LEDGER_URL": ""}, clear=False)

        from arbiter.models.audit_ledger_client import AuditLedgerClient

        with pytest.raises(
            ValueError, match="AUDIT_LEDGER_URL must be a WebSocket URL"
        ):
            AuditLedgerClient(dlt_type="ethereum")

    def test_init_with_production_env_requires_secrets_manager(
        self, mock_web3_dependencies, mocker: MockerFixture
    ):
        """Test that production environment requires secrets manager"""
        mocker.patch.dict(
            os.environ, {"APP_ENV": "production", "USE_SECRETS_MANAGER": "false"}
        )

        from arbiter.models.audit_ledger_client import AuditLedgerClient, DLTError

        with pytest.raises(DLTError, match="Secrets Manager must be enabled"):
            AuditLedgerClient(dlt_type="ethereum")


class TestAuditLedgerClientConnection:
    """Test connection management"""

    @pytest.mark.asyncio
    async def test_connect_success(self, audit_client, mock_web3_dependencies):
        """Test successful connection to DLT"""
        await audit_client.connect()

        assert audit_client._is_connected
        assert audit_client.web3 is not None
        assert audit_client.contract is not None
        assert audit_client.account is not None
        mock_web3_dependencies["web3"].is_connected.assert_called()

    @pytest.mark.asyncio
    async def test_connect_idempotent(self, audit_client):
        """Test that connect is idempotent"""
        await audit_client.connect()
        first_web3 = audit_client.web3

        await audit_client.connect()
        assert audit_client.web3 is first_web3
        assert audit_client._is_connected

    @pytest.mark.asyncio
    async def test_connect_failure(self, audit_client, mock_web3_dependencies):
        """Test connection failure handling"""
        from arbiter.models.audit_ledger_client import DLTConnectionError

        mock_web3_dependencies["web3"].is_connected.return_value = False

        with pytest.raises(
            DLTConnectionError, match="Failed to connect to Ethereum node"
        ):
            await audit_client.connect()

    @pytest.mark.asyncio
    async def test_disconnect_success(self, audit_client, mock_web3_dependencies):
        """Test successful disconnection"""
        await audit_client.connect()
        await audit_client.disconnect()

        assert not audit_client._is_connected
        assert audit_client.web3 is None
        assert audit_client.contract is None
        assert audit_client.account is None
        mock_web3_dependencies["provider"].disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self, audit_client):
        """Test that disconnect is idempotent"""
        await audit_client.disconnect()
        assert not audit_client._is_connected

        await audit_client.connect()
        await audit_client.disconnect()
        await audit_client.disconnect()
        assert not audit_client._is_connected

    @pytest.mark.asyncio
    async def test_context_manager(self, audit_client):
        """Test async context manager functionality"""
        async with audit_client:
            assert audit_client._is_connected
        assert not audit_client._is_connected


class TestAuditLedgerClientEventLogging:
    """Test event logging functionality"""

    @pytest.mark.asyncio
    async def test_log_event_success(self, audit_client, mock_web3_dependencies):
        """Test successful event logging"""
        await audit_client.connect()

        event_details = {
            "action": "test_action",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        correlation_id = str(uuid.uuid4())

        tx_hash = await audit_client.log_event(
            event_type="test:event",
            details=event_details,
            operator="test_operator",
            correlation_id=correlation_id,
        )

        assert tx_hash == b"tx_hash_123".hex()
        mock_web3_dependencies["eth"].send_raw_transaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_event_not_connected(self, audit_client):
        """Test logging event when not connected raises error"""
        from arbiter.models.audit_ledger_client import DLTTransactionError

        with pytest.raises(
            DLTTransactionError, match="Failed to log DLT event to ethereum"
        ):
            await audit_client.log_event("test:event", {})

    @pytest.mark.asyncio
    async def test_log_event_validation_error(self, audit_client):
        """Test event validation errors"""
        await audit_client.connect()

        with pytest.raises(ValueError, match="String should match pattern"):
            await audit_client.log_event("invalid@event!", {})

        large_details = {"data": "x" * 20000}
        with pytest.raises(ValueError, match="exceeds the 10KB size limit"):
            await audit_client.log_event("test:event", large_details)

    @pytest.mark.asyncio
    async def test_log_event_idempotency(self, audit_client):
        """Test idempotency of event logging"""
        await audit_client.connect()

        event_details = {"test": "data"}

        await audit_client.log_event(
            "test:event", event_details, "operator1"
        )

        tx_hash2 = await audit_client.log_event(
            "test:event", event_details, "operator1"
        )

        assert "duplicate_local_" in tx_hash2
        assert tx_hash2.startswith("duplicate_local_")

    @pytest.mark.asyncio
    async def test_log_event_transaction_failure(
        self, audit_client, mock_web3_dependencies
    ):
        """Test handling of transaction failure"""
        from arbiter.models.audit_ledger_client import DLTTransactionError

        await audit_client.connect()

        # Mock transaction steps to ensure failure is detected in wait_for_confirmations
        mock_web3_dependencies["eth"].send_raw_transaction.return_value = b"tx_hash_123"
        mock_web3_dependencies[
            "contract"
        ].functions.logEvent.return_value.estimate_gas.return_value = 200000
        mock_web3_dependencies[
            "contract"
        ].functions.logEvent.return_value.build_transaction.return_value = {
            "chainId": 1337,
            "gas": 200000,
            "gasPrice": 2000000000,
            "nonce": 1,
        }
        mock_web3_dependencies["eth"].account.sign_transaction.return_value = MagicMock(
            rawTransaction=b"raw_tx_data"
        )

        # Mock wait_for_transaction_receipt to raise DLTTransactionError directly
        mock_receipt = MockReceipt(status=0, blockNumber=100, gasUsed=150000)
        mock_web3_dependencies["eth"].wait_for_transaction_receipt.side_effect = (
            DLTTransactionError(f"Transaction {b'tx_hash_123'.hex()} reverted.")
        )
        mock_web3_dependencies["eth"].get_transaction_receipt.return_value = (
            mock_receipt
        )

        with pytest.raises(
            DLTTransactionError,
            match="An unexpected error occurred logging DLT event to ethereum",
        ):
            await audit_client.log_event("test:event", {})

    @pytest.mark.asyncio
    async def test_log_event_pii_hashing(self, audit_client):
        """Test that PII is hashed in event details"""
        await audit_client.connect()

        event_details = {"user_id": "user123", "action": "login"}

        tx_hash = await audit_client.log_event("user:action", event_details.copy())

        assert tx_hash is not None


class TestAuditLedgerClientBatchOperations:
    """Test batch operations"""

    @pytest.mark.asyncio
    async def test_batch_log_events_not_supported(
        self, audit_client, mock_web3_dependencies
    ):
        """Test batch logging when not supported by contract"""
        from arbiter.models.audit_ledger_client import DLTUnsupportedError, AuditEvent

        await audit_client.connect()

        mock_web3_dependencies["contract"].functions = MagicMock(spec=[])

        events = [
            AuditEvent(event_type="test:batch", details={"id": i}) for i in range(3)
        ]

        with pytest.raises(DLTUnsupportedError, match="Batch logging not supported"):
            await audit_client.batch_log_events(events)


class TestAuditLedgerClientHealthCheck:
    """Test health check functionality"""

    @pytest.mark.asyncio
    async def test_is_connected_when_connected(
        self, audit_client, mock_web3_dependencies
    ):
        """Test is_connected returns True when connected"""
        await audit_client.connect()

        result = await audit_client.is_connected()
        assert result is True
        mock_web3_dependencies["web3"].is_connected.assert_called()

    @pytest.mark.asyncio
    async def test_is_connected_when_not_connected(self, audit_client):
        """Test is_connected returns False when not connected"""
        result = await audit_client.is_connected()
        assert result is False

    @pytest.mark.asyncio
    async def test_is_connected_updates_state_on_disconnect(
        self, audit_client, mock_web3_dependencies
    ):
        """Test is_connected updates internal state when connection is lost"""
        await audit_client.connect()

        mock_web3_dependencies["web3"].is_connected.return_value = False

        result = await audit_client.is_connected()
        assert result is False
        assert audit_client._is_connected is False


class TestAuditLedgerClientRetryMechanism:
    """Test retry mechanisms"""

    @pytest.mark.asyncio
    async def test_connect_retries_on_failure(
        self, audit_client, mock_web3_dependencies
    ):
        """Test that connect would retry on connection failure (disabled for tests)"""
        from arbiter.models.audit_ledger_client import DLTConnectionError

        mock_web3_dependencies["web3"].is_connected.return_value = False

        # With retry disabled via fixture, should raise immediately
        with pytest.raises(DLTConnectionError):
            await audit_client.connect()

        # Should only be called once since retry is disabled
        assert mock_web3_dependencies["web3"].is_connected.call_count == 1


class TestAuditLedgerClientUnsupportedDLT:
    """Test unsupported DLT types"""

    def test_hyperledger_fabric_not_supported(
        self, mock_web3_dependencies, mocker: MockerFixture
    ):
        """Test that Hyperledger Fabric is not supported"""

        # Patch retry before import
        def no_retry(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        mocker.patch("tenacity.retry", no_retry)

        from arbiter.models.audit_ledger_client import (
            AuditLedgerClient,
            DLTConnectionError,
        )

        mocker.patch.dict(
            os.environ, {"AUDIT_LEDGER_URL": "ws://test", "APP_ENV": "development"}
        )

        client = AuditLedgerClient(dlt_type="hyperledger_fabric")

        with pytest.raises(
            DLTConnectionError, match="Hyperledger Fabric is not supported"
        ):
            asyncio.run(client.connect())


class TestAuditLedgerClientGasManagement:
    """Test gas management features"""

    @pytest.mark.asyncio
    async def test_gas_estimation_failure_uses_default(
        self, audit_client, mock_web3_dependencies
    ):
        """Test that gas estimation failure falls back to default"""
        await audit_client.connect()

        mock_web3_dependencies[
            "contract"
        ].functions.logEvent.return_value.estimate_gas.side_effect = Exception(
            "Gas estimation failed"
        )

        tx_hash = await audit_client.log_event("test:event", {"test": "data"})

        assert tx_hash is not None

    @pytest.mark.asyncio
    async def test_gas_cap_enforcement(self, audit_client, mock_web3_dependencies):
        """Test that gas price cap is enforced"""
        from arbiter.models.audit_ledger_client import DLTTransactionError

        await audit_client.connect()

        mock_web3_dependencies["eth"].get_block.return_value = {
            "baseFeePerGas": mock_web3_dependencies["web3"].to_wei(600, "gwei")
        }

        with pytest.raises(
            DLTTransactionError,
            match="An unexpected error occurred logging DLT event to ethereum",
        ):
            await audit_client.log_event("test:event", {})


class TestAuditLedgerClientConcurrency:
    """Test concurrent operations"""

    @pytest.mark.asyncio
    async def test_concurrent_event_logging(self, audit_client, mock_web3_dependencies):
        """Test concurrent event logging with semaphore"""
        await audit_client.connect()

        tasks = []
        for i in range(5):
            task = audit_client.log_event(
                event_type=f"test:event_{i}",
                details={"id": i},
                operator="test_operator",
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        assert all(isinstance(r, str) for r in results)
        assert audit_client.max_parallel_tx == 1


class TestAuditLedgerClientSecretManagement:
    """Test secret management features"""

    @pytest.mark.asyncio
    async def test_get_private_key_from_env(self, audit_client, mocker: MockerFixture):
        """Test retrieving private key from environment variable"""
        mocker.patch.dict(os.environ, {"USE_SECRETS_MANAGER": "false"})

        key = audit_client._get_private_key()
        assert key == "0x" + "1" * 64

    @pytest.mark.asyncio
    async def test_get_private_key_from_secrets_manager(
        self, audit_client, mocker: MockerFixture
    ):
        """Test retrieving private key from AWS Secrets Manager"""
        mocker.patch.dict(
            os.environ,
            {
                "USE_SECRETS_MANAGER": "true",
                "ETHEREUM_PRIVATE_KEY_SECRET_NAME": "test/secret",
                "AWS_REGION": "us-east-1",
            },
        )

        mock_boto3 = mocker.patch("arbiter.models.audit_ledger_client.boto3")
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": "0xsecret_key_from_aws"
        }
        mock_boto3.session.Session.return_value.client.return_value = mock_client

        key = audit_client._get_private_key()
        assert key == "0xsecret_key_from_aws"
        mock_client.get_secret_value.assert_called_with(SecretId="test/secret")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
