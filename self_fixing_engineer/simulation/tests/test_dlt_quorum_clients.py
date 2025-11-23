# tests/test_dlt_quorum_clients.py

import pytest
import json
import re
from unittest.mock import AsyncMock, MagicMock, mock_open
from pydantic import ValidationError

# Check if web3 is available before importing anything that depends on it
try:
    import web3
    from web3.exceptions import (
        ContractLogicError,
        ContractCustomError,
        TransactionNotFound,
        TimeExhausted,
    )
    from web3.eth import AsyncEth
    from eth_account import Account

    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False

    # Create mock classes for the tests to run even without web3
    class ContractLogicError(Exception):
        pass

    class ContractCustomError(Exception):
        pass

    class TransactionNotFound(Exception):
        pass

    class TimeExhausted(Exception):
        pass

    class AsyncEth:
        pass

    class Account:
        @staticmethod
        def from_key(key):
            mock = MagicMock()
            mock.address = "0x" + "f" * 40
            return mock


# Import base classes that don't depend on web3
from simulation.plugins.dlt_clients.dlt_base import (
    BaseOffChainClient,
    SECRETS_MANAGER,
)

# Only import Quorum-specific modules if web3 is available
if WEB3_AVAILABLE:
    try:
        from simulation.plugins.dlt_clients.dlt_quorum_clients import (
            QuorumClientWrapper,
            QuorumConfig,
            _temp_files,
            temp_file,
            cleanup_temp_files,
            AWSSecretsBackend,
            AzureKeyVaultBackend,
            GCPSecretManagerBackend,
        )

        QUORUM_AVAILABLE = True
    except ImportError as e:
        QUORUM_AVAILABLE = False
        print(f"Could not import Quorum clients: {e}")
else:
    QUORUM_AVAILABLE = False
    # Create mock QuorumConfig for basic validation tests
    from pydantic import BaseModel, Field, validator
    from typing import Optional, List, Dict, Any, Literal

    class QuorumConfig(BaseModel):
        """Mock QuorumConfig for testing when web3 is not available."""

        rpc_url: str = Field(..., min_length=1)
        chain_id: int = Field(..., ge=1)
        contract_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{40}$")
        contract_abi_path: Optional[str] = None
        contract_abi_secret_id: Optional[str] = None
        private_key: Optional[str] = None
        private_key_secret_id: Optional[str] = None
        secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(default_factory=list)
        secrets_provider_config: Optional[Dict[str, Any]] = None
        poa_middleware: bool = True
        privacy_group_id: Optional[str] = None
        private_for: Optional[List[str]] = None
        default_gas_limit: int = Field(2_000_000, ge=21000)
        default_max_fee_per_gas_gwei: Optional[int] = Field(None, ge=1)
        default_max_priority_fee_per_gas_gwei: Optional[int] = Field(None, ge=1)
        fallback_gas_price_gwei: int = Field(5, ge=1)
        tx_confirm_timeout: int = Field(120, ge=10)
        log_format: str = "json"
        temp_file_ttl: float = Field(3600.0, ge=60.0)
        cleanup_interval: float = Field(300.0, ge=30.0)

        @validator("rpc_url")
        def validate_rpc_url_scheme(cls, v):
            from urllib.parse import urlparse

            parsed = urlparse(v)
            if parsed.scheme not in ("http", "https"):
                raise ValueError("rpc_url must use http or https scheme")
            return v

        @validator("contract_abi_path", pre=True, always=True)
        def validate_contract_abi_source(cls, v, values):
            if not v and not values.get("contract_abi_secret_id"):
                raise ValueError(
                    "Either contract_abi_path or contract_abi_secret_id must be provided."
                )
            return v

        @validator("private_key", pre=True, always=True)
        def validate_private_key_source(cls, v, values):
            if not v and not values.get("private_key_secret_id"):
                raise ValueError("Either private_key or private_key_secret_id must be provided.")
            if v and not re.match(r"^(0x)?[a-fA-F0-9]{64}$", v):
                raise ValueError("private_key must be a 64-character hex string.")
            return v

        @validator("privacy_group_id")
        def validate_privacy_group_id(cls, v):
            if v and not re.match(r"^[a-fA-F0-9]{64}$", v):
                raise ValueError("privacy_group_id must be a 64-character hex string.")
            return v

        @validator("private_for", always=True)
        def validate_private_for(cls, v, values):
            privacy_group_id = values.get("privacy_group_id")

            # Check completeness - both or neither
            # Note: v could be None (not provided) or an empty list
            has_privacy_group = privacy_group_id is not None
            has_private_for = v is not None and (not isinstance(v, list) or len(v) > 0)

            if has_privacy_group and not has_private_for:
                raise ValueError(
                    "Both privacy_group_id and private_for must be provided for private transactions, or neither."
                )
            if has_private_for and not has_privacy_group:
                raise ValueError(
                    "Both privacy_group_id and private_for must be provided for private transactions, or neither."
                )

            # Validate private_for format if provided
            if v:
                if not isinstance(v, list) or not all(
                    re.match(r"^[A-Za-z0-9+/]{44}$", pk) for pk in v
                ):
                    raise ValueError(
                        "private_for must be a list of valid 44-character base64 public keys."
                    )
            return v


# Tests that don't require web3 or Quorum imports
class TestQuorumConfigValidation:
    """Test QuorumConfig validation without requiring web3."""

    def test_valid_config(self):
        """Test that a valid configuration passes validation."""
        valid_config = {
            "rpc_url": "http://localhost:8545",
            "chain_id": 123,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "1" * 64,
        }
        config = QuorumConfig(**valid_config)
        assert config.rpc_url == "http://localhost:8545"
        assert config.chain_id == 123
        assert config.contract_address == "0x1234567890123456789012345678901234567890"

    def test_invalid_url_scheme(self):
        """Test that invalid URL scheme raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            invalid_config = {
                "rpc_url": "invalid://localhost:8545",
                "chain_id": 123,
                "contract_address": "0x1234567890123456789012345678901234567890",
                "contract_abi_path": "/path/to/abi.json",
                "private_key": "0x" + "1" * 64,
            }
            QuorumConfig(**invalid_config)
        assert "rpc_url must use http or https scheme" in str(exc_info.value)

    def test_missing_required_fields(self):
        """Test that missing required fields raise validation error."""
        with pytest.raises(ValidationError) as exc_info:
            QuorumConfig(rpc_url="http://localhost:8545")
        assert "chain_id" in str(exc_info.value)

    def test_invalid_contract_address(self):
        """Test that invalid contract address raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            invalid_config = {
                "rpc_url": "http://localhost:8545",
                "chain_id": 123,
                "contract_address": "invalid_address",
                "contract_abi_path": "/path/to/abi.json",
                "private_key": "0x" + "1" * 64,
            }
            QuorumConfig(**invalid_config)
        assert "contract_address" in str(exc_info.value)

    def test_incomplete_privacy_config(self):
        """Test that incomplete privacy configuration raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            invalid_config = {
                "rpc_url": "http://localhost:8545",
                "chain_id": 123,
                "contract_address": "0x1234567890123456789012345678901234567890",
                "contract_abi_path": "/path/to/abi.json",
                "private_key": "0x" + "1" * 64,
                "privacy_group_id": "a" * 64,
                # Missing private_for
            }
            QuorumConfig(**invalid_config)
        assert "Both privacy_group_id and private_for must be provided" in str(exc_info.value)

    def test_valid_privacy_config(self):
        """Test that valid privacy configuration passes validation."""
        valid_config = {
            "rpc_url": "http://localhost:8545",
            "chain_id": 123,
            "contract_address": "0x1234567890123456789012345678901234567890",
            "contract_abi_path": "/path/to/abi.json",
            "private_key": "0x" + "1" * 64,
            "privacy_group_id": "a" * 64,
            "private_for": ["b" * 44, "c" * 44],
        }
        config = QuorumConfig(**valid_config)
        assert config.privacy_group_id == "a" * 64
        assert len(config.private_for) == 2

    def test_missing_abi_source(self):
        """Test that missing ABI source raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            invalid_config = {
                "rpc_url": "http://localhost:8545",
                "chain_id": 123,
                "contract_address": "0x1234567890123456789012345678901234567890",
                # Missing both contract_abi_path and contract_abi_secret_id
                "private_key": "0x" + "1" * 64,
            }
            QuorumConfig(**invalid_config)
        assert "Either contract_abi_path or contract_abi_secret_id must be provided" in str(
            exc_info.value
        )

    def test_missing_private_key_source(self):
        """Test that missing private key source raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            invalid_config = {
                "rpc_url": "http://localhost:8545",
                "chain_id": 123,
                "contract_address": "0x1234567890123456789012345678901234567890",
                "contract_abi_path": "/path/to/abi.json",
                # Missing both private_key and private_key_secret_id
            }
            QuorumConfig(**invalid_config)
        assert "Either private_key or private_key_secret_id must be provided" in str(exc_info.value)


# Tests that require web3 and full Quorum implementation
@pytest.mark.skipif(not QUORUM_AVAILABLE, reason="Quorum client not available (web3 not installed)")
class TestQuorumClient:
    """Tests that require the full Quorum client implementation."""

    @pytest.fixture
    def mock_off_chain(self):
        mock = AsyncMock(spec=BaseOffChainClient)
        mock.client_type = "MockOffChain"
        mock.save_blob.return_value = "mock_off_chain_id"
        mock.get_blob.return_value = b"mock_payload_data"
        mock.health_check.return_value = {
            "status": True,
            "message": "OK",
            "details": {},
        }
        mock.close = AsyncMock()
        return mock

    @pytest.fixture(autouse=True)
    def mock_secrets_manager(self, mocker):
        mocker.patch.object(SECRETS_MANAGER, "get_secret")
        SECRETS_MANAGER.get_secret.side_effect = lambda key, **kwargs: (
            "0x" + "1" * 64
            if "private_key" in key.lower()
            else (
                json.dumps(
                    [
                        {
                            "anonymous": False,
                            "inputs": [],
                            "name": "writeCheckpoint",
                            "outputs": [],
                            "stateMutability": "nonpayable",
                            "type": "function",
                        }
                    ]
                )
                if "abi" in key.lower()
                else "mock_secret"
            )
        )

    @pytest.fixture
    def mock_web3_provider(self, mocker):
        """Mocks the web3.py provider and eth module methods for Quorum."""
        mock_w3 = MagicMock()
        mock_w3.is_connected = AsyncMock(return_value=True)
        mock_w3.eth = MagicMock()
        mock_w3.eth.chain_id = 123
        mock_w3.eth.get_code = AsyncMock(return_value=b"0x123")
        mock_w3.eth.get_balance = AsyncMock(return_value=1000000000000000000)
        mock_w3.eth.get_transaction_count = AsyncMock(return_value=0)
        mock_w3.eth.send_raw_transaction = AsyncMock(return_value=b"\x12\x34\x56\x78" * 8)
        mock_w3.eth.wait_for_transaction_receipt = AsyncMock(
            return_value=MagicMock(status=1, blockNumber=123)
        )
        mock_w3.eth.gas_price = 20000000000
        mock_w3.eth.get_block = AsyncMock(return_value={"baseFeePerGas": 20000000000})
        mock_w3.eth.default_account = None
        mock_w3.eth.contract = MagicMock()

        # Mock account operations
        mock_account = MagicMock()
        mock_account.address = "0x" + "f" * 40
        mock_account.sign_transaction = MagicMock(
            return_value=MagicMock(rawTransaction=b"mock_raw_tx")
        )
        mock_w3.eth.account = MagicMock()
        mock_w3.eth.account.from_key = MagicMock(return_value=mock_account)

        # Utility functions
        mock_w3.to_wei = lambda value, unit: (value * (10**9) if unit == "gwei" else value)
        mock_w3.from_wei = lambda value, unit: (value / (10**9) if unit == "gwei" else value)
        mock_w3.to_hex = lambda x: "0x" + x.hex() if isinstance(x, bytes) else str(x)
        mock_w3.to_bytes = lambda hexstr=None: (bytes.fromhex(hexstr[2:]) if hexstr else b"")
        mock_w3.keccak = lambda x: b"mock_keccak_hash"

        # Mock provider
        mock_provider = MagicMock()
        mock_w3.provider = mock_provider
        mock_w3.middleware_onion = MagicMock()
        mock_w3.middleware_onion.inject = MagicMock()
        mock_w3.manager = MagicMock()
        mock_w3.manager.coro_request = AsyncMock(return_value="0x" + "a" * 64)

        mocker.patch("web3.Web3", return_value=mock_w3)
        mocker.patch("web3.AsyncHTTPProvider", return_value=mock_provider)
        return mock_w3

    @pytest.fixture(autouse=True)
    def mock_filesystem(self, mocker):
        """Mocks file system operations."""
        mocker.patch("os.path.exists", return_value=True)
        mocker.patch("os.unlink", new=MagicMock())
        mocker.patch("os.chmod", new=MagicMock())
        mocker.patch("os.urandom", return_value=b"test-hmac-key-for-testing-only-123")

    @pytest.mark.asyncio
    async def test_quorum_init_success(self, mock_off_chain, mock_web3_provider, mocker):
        """Test that the Quorum client initializes successfully with a valid configuration."""
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock()
        mocker.patch("asyncio.get_running_loop", return_value=mock_loop)
        mocker.patch("builtins.open", mock_open(read_data="[]"))

        mock_config = {
            "quorum": {
                "rpc_url": "http://localhost:8545",
                "chain_id": 123,
                "contract_address": "0x1234567890123456789012345678901234567890",
                "contract_abi_path": "/path/to/abi.json",
                "private_key": "0x" + "1" * 64,
                "poa_middleware": True,
            }
        }

        client = QuorumClientWrapper(mock_config, mock_off_chain)
        await client.initialize()

        assert isinstance(client, QuorumClientWrapper)
        assert client.rpc_url == "http://localhost:8545"
        assert client.chain_id == 123
        assert client.contract_address == "0x1234567890123456789012345678901234567890"
