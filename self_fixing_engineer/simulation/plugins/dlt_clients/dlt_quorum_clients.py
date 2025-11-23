# simulation/plugins/dlt_clients/dlt_quorum_clients.py

import asyncio
import atexit
import json
import os
import re
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager, suppress
from datetime import datetime
from typing import Any, Callable, Dict, Final, List, Literal, Optional
from urllib.parse import urlparse

from eth_account import Account
from pydantic import BaseModel, Field, ValidationError, validator

# --- Strict Dependency Check for web3.py ---
# WEB3_AVAILABLE is now determined by the critical import check in dlt_base.py
# If it's not available, dlt_base.py would have already aborted.
from web3 import Web3
from web3.eth import AsyncEth
from web3.exceptions import TimeExhausted
from web3.middleware import geth_poa_middleware

from .dlt_base import (
    AUDIT,
    PRODUCTION_MODE,
    TRACER,
    BaseOffChainClient,
    DLTClientAuthError,
    DLTClientCircuitBreakerError,
    DLTClientConfigurationError,
    DLTClientConnectivityError,
    DLTClientQueryError,
    DLTClientTimeoutError,
    DLTClientTransactionError,
    DLTClientValidationError,
    Status,
    StatusCode,
    _base_logger,
    alert_operator,
    async_retry,
    scrub_secrets,
)
from .dlt_evm_clients import EthereumClientWrapper  # Inherit from EVM client

# --- Secrets Backend Integrations (Strict Checks) ---
AWS_SECRETS_AVAILABLE = False
try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError

    AWS_SECRETS_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "boto3 not found. AWS Secrets Manager integration will be disabled."
    )

    class BotoClientError(Exception):
        pass  # Define for type hinting


AZURE_KEYVAULT_AVAILABLE = False
try:
    from azure.identity.aio import DefaultAzureCredential
    from azure.keyvault.secrets.aio import (
        SecretClient as AsyncSecretClient,  # Use async client + async credential
    )

    AZURE_KEYVAULT_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "azure-identity or azure-keyvault-secrets not found. Azure Key Vault integration will be disabled."
    )

GCP_SECRET_MANAGER_AVAILABLE = False
try:
    from google.cloud import (
        secretmanager_v1beta1 as secretmanager,
    )  # Use v1beta1 for consistency

    GCP_SECRET_MANAGER_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "google-cloud-secret-manager not found. GCP Secret Manager integration will be disabled."
    )

# --- Metrics (from dlt_base) ---
# Metrics are now imported and managed by dlt_base.py's Prometheus setup.

# Specific Quorum metrics
try:
    from prometheus_client import Counter

    QUORUM_METRICS = {
        "secrets_unavailable_total": Counter(
            "quorum_secrets_unavailable_total",
            "Total number of times a secrets backend was requested but unavailable",
            labelnames=["client_type", "backend"],
        ),
        "contract_abi_load_failure": Counter(
            "quorum_contract_abi_load_failure_total",
            "Total failures loading contract ABI from any source",
            labelnames=["client_type", "source_type"],
        ),
        "private_key_load_failure": Counter(
            "quorum_private_key_load_failure_total",
            "Total failures loading private key from any source",
            labelnames=["client_type", "source_type"],
        ),
        "privacy_config_invalid": Counter(
            "quorum_privacy_config_invalid_total",
            "Total times privacy config was incomplete/invalid",
            labelnames=["client_type"],
        ),
    }
except ImportError:
    _base_logger.warning("Prometheus client not available for Quorum specific metrics.")
    QUORUM_METRICS = {}  # Dummy if not available


# Temporary file cleanup (more robust)
_temp_files: Dict[str, float] = {}  # Store {file_path: creation_time}


def cleanup_temp_files() -> None:
    """Cleans up temporary files created by temp_file context manager."""
    global _temp_files
    files_to_clean = list(_temp_files.keys())  # Iterate over a copy
    for temp_file in files_to_clean:
        try:
            os.unlink(temp_file)
            _base_logger.info(f"Cleaned up temporary file: {temp_file}")
        except OSError as e:
            _base_logger.warning(f"Failed to clean up temporary file {temp_file}: {e}")
        finally:
            _temp_files.pop(
                temp_file, None
            )  # Remove from tracking regardless of success


# Register cleanup on process exit
atexit.register(cleanup_temp_files)


@contextmanager
def temp_file(content: str, ttl: float = 3600.0) -> str:
    """
    Creates a temporary file with specified content and registers it for cleanup.
    The file is created with restrictive permissions (0o600).
    Args:
        content: The string content to write to the temporary file.
        ttl: Time-to-live for the file (tracked for cleanup; enforced by periodic cleanup).
    Yields:
        str: The path to the created temporary file.
    """
    global _temp_files
    fd, path = tempfile.mkstemp(suffix=".json", prefix="quorum_abi_")
    try:
        with os.fdopen(fd, "w") as tmp:
            tmp.write(content)
        os.chmod(path, 0o600)  # Restrictive permissions: owner read/write only
        _temp_files[path] = time.time()  # Add to tracking
        _base_logger.info(f"Created temporary file: {path} with TTL {ttl}s")
        yield path
    except Exception as e:
        _base_logger.critical(
            f"CRITICAL: Failed to create or write to temporary file: {e}.",
            exc_info=True,
        )
        try:
            asyncio.get_running_loop().create_task(
                alert_operator(
                    f"CRITICAL: Failed to create or write temporary file for Quorum ABI: {e}.",
                    level="CRITICAL",
                )
            )
        except RuntimeError:
            pass  # No running loop
        raise DLTClientConfigurationError(
            "Failed to create or write temporary file for Quorum ABI.",
            "Quorum",
            original_exception=e,
        ) from e
    finally:
        # Cleanup is handled by atexit and QuorumClientWrapper.close()
        pass


# Secrets Backend Interface
class SecretsBackend(ABC):
    """Abstract base class for secrets backends."""

    @abstractmethod
    async def get_secret(self, secret_id: str) -> str:
        """
        Retrieves a secret by ID asynchronously.
        Args:
            secret_id: The identifier of the secret.
        Returns:
            str: The secret value.
        Raises:
            DLTClientConfigurationError: If the secret cannot be retrieved.
        """
        pass


class AWSSecretsBackend(SecretsBackend):
    """AWS Secrets Manager backend."""

    def __init__(self):
        if not AWS_SECRETS_AVAILABLE:
            if QUORUM_METRICS:
                QUORUM_METRICS["secrets_unavailable_total"].labels(
                    client_type="Quorum", backend="aws"
                ).inc()
            raise DLTClientConfigurationError(
                "AWS Secrets Manager backend requested but boto3 is not available.",
                "Quorum",
            )
        self.client = boto3.client("secretsmanager")

    async def get_secret(self, secret_id: str) -> str:
        try:
            # boto3 client methods are typically blocking, run in executor
            response = await asyncio.to_thread(
                self.client.get_secret_value, SecretId=secret_id
            )
            return response["SecretString"]
        except BotoClientError as e:
            raise DLTClientConfigurationError(
                f"Failed to fetch secret from AWS Secrets Manager: {e}",
                "Quorum",
                original_exception=e,
            )


class AzureKeyVaultBackend(SecretsBackend):
    """Azure Key Vault backend (async)."""

    def __init__(self, vault_url: str):
        if not AZURE_KEYVAULT_AVAILABLE:
            if QUORUM_METRICS:
                QUORUM_METRICS["secrets_unavailable_total"].labels(
                    client_type="Quorum", backend="azure"
                ).inc()
            raise DLTClientConfigurationError(
                "Azure Key Vault backend requested but Azure SDK is not available.",
                "Quorum",
            )
        if not vault_url:
            raise DLTClientConfigurationError(
                "Azure Key Vault URL is required.", "Quorum"
            )
        self.credential = DefaultAzureCredential()
        self.client = AsyncSecretClient(vault_url=vault_url, credential=self.credential)

    async def get_secret(self, secret_id: str) -> str:
        try:
            secret = await self.client.get_secret(secret_id)
            return secret.value
        except Exception as e:
            raise DLTClientConfigurationError(
                f"Failed to fetch secret from Azure Key Vault: {e}",
                "Quorum",
                original_exception=e,
            )
        finally:
            with suppress(Exception):
                await self.client.close()
                await self.credential.close()


class GCPSecretManagerBackend(SecretsBackend):
    """GCP Secret Manager backend."""

    def __init__(self, project_id: str):
        if not GCP_SECRET_MANAGER_AVAILABLE:
            if QUORUM_METRICS:
                QUORUM_METRICS["secrets_unavailable_total"].labels(
                    client_type="Quorum", backend="gcp"
                ).inc()
            raise DLTClientConfigurationError(
                "GCP Secret Manager backend requested but Google Cloud SDK is not available.",
                "Quorum",
            )
        if not project_id:
            raise DLTClientConfigurationError(
                "GCP Project ID is required for GCP Secret Manager.", "Quorum"
            )
        self.client = secretmanager.SecretManagerServiceClient()
        self.project_id = project_id

    async def get_secret(self, secret_id: str) -> str:
        try:
            name = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
            response = await asyncio.to_thread(
                self.client.access_secret_version, name=name
            )
            return response.payload.data.decode("UTF-8")
        except Exception as e:
            raise DLTClientConfigurationError(
                f"Failed to fetch secret from GCP Secret Manager: {e}",
                "Quorum",
                original_exception=e,
            )


# Configuration schema
class QuorumConfig(BaseModel):
    """Configuration schema for Quorum client."""

    rpc_url: str = Field(..., min_length=1)
    chain_id: int = Field(..., ge=1)
    contract_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{40}$")
    contract_abi_path: Optional[str] = None  # Path to ABI file (for non-prod)
    contract_abi_secret_id: Optional[str] = (
        None  # Secret ID for ABI JSON in secrets backend
    )
    private_key: Optional[str] = None  # Should not be used in prod directly
    private_key_secret_id: Optional[str] = (
        None  # Secret ID for private key in secrets backend
    )
    secrets_providers: List[Literal["aws", "azure", "gcp"]] = Field(
        default_factory=list
    )  # Prioritized list of secrets backends
    secrets_provider_config: Optional[Dict[str, Any]] = (
        None  # Config for secrets backends (e.g., vault_url, project_id)
    )
    poa_middleware: bool = True
    privacy_group_id: Optional[str] = None  # Quorum privacy group ID
    private_for: Optional[List[str]] = (
        None  # List of public keys for private transaction recipients
    )
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
        if v and values.get("contract_abi_secret_id") and PRODUCTION_MODE:
            _base_logger.warning(
                "Both contract_abi_path and contract_abi_secret_id are provided. Prioritizing secret ID in production."
            )
        return v

    @validator("private_key", pre=True, always=True)
    def validate_private_key_source(cls, v, values):
        if not v and not values.get("private_key_secret_id"):
            raise ValueError(
                "Either private_key or private_key_secret_id must be provided."
            )
        if v and values.get("private_key_secret_id") and PRODUCTION_MODE:
            _base_logger.warning(
                "Both private_key and private_key_secret_id are provided. Prioritizing secret ID in production."
            )
        if v and not re.match(r"^(0x)?[a-fA-F0-9]{64}$", v):
            raise ValueError("private_key must be a 64-character hex string.")
        return v

    @validator("privacy_group_id", "private_for", always=True)
    def validate_privacy_settings_completeness(cls, v, values):
        privacy_group_id = values.get("privacy_group_id")
        private_for = values.get("private_for")

        if (privacy_group_id or private_for) and not (privacy_group_id and private_for):
            if QUORUM_METRICS:
                QUORUM_METRICS["privacy_config_invalid"].labels(
                    client_type="Quorum"
                ).inc()
            raise ValueError(
                "Both privacy_group_id and private_for must be provided for private transactions, or neither."
            )

        if privacy_group_id and not re.match(r"^[a-fA-F0-9]{64}$", privacy_group_id):
            if QUORUM_METRICS:
                QUORUM_METRICS["privacy_config_invalid"].labels(
                    client_type="Quorum"
                ).inc()
            raise ValueError("privacy_group_id must be a 64-character hex string.")

        if private_for:
            if not isinstance(private_for, list) or not all(
                re.match(r"^[A-Za-z0-9+/]{44}$", pk) for pk in private_for
            ):
                if QUORUM_METRICS:
                    QUORUM_METRICS["privacy_config_invalid"].labels(
                        client_type="Quorum"
                    ).inc()
                raise ValueError(
                    "private_for must be a list of valid 44-character base64 public keys."
                )
        return v

    @validator("secrets_providers")
    def validate_secrets_providers_list(cls, v, values):
        if values.get("contract_abi_secret_id") or values.get("private_key_secret_id"):
            if not v:
                raise ValueError(
                    "secrets_providers list must not be empty if secret_ids are provided."
                )
            for provider in v:
                if provider not in ("aws", "azure", "gcp"):
                    raise ValueError(
                        f"Invalid secrets_provider: {provider}. Must be one of 'aws', 'azure', 'gcp'."
                    )
                if provider == "azure" and not values.get(
                    "secrets_provider_config", {}
                ).get("vault_url"):
                    raise ValueError(
                        "secrets_provider_config.vault_url required for Azure Key Vault."
                    )
                if provider == "gcp" and not values.get(
                    "secrets_provider_config", {}
                ).get("project_id"):
                    raise ValueError(
                        "secrets_provider_config.project_id required for GCP Secret Manager."
                    )
        return v


class QuorumClientWrapper(EthereumClientWrapper):
    """
    Quorum blockchain client, extending EthereumClientWrapper for Quorum-specific features.
    Supports private transactions, secrets management, and health checks for compliance (e.g., SOC2, PCI).
    """

    client_type: Final[str] = "Quorum"

    def __init__(self, config: Dict[str, Any], off_chain_client: "BaseOffChainClient"):
        # Validate configuration
        try:
            quorum_config_data = config.get("quorum", {})
            validated_config = QuorumConfig(**quorum_config_data).dict(
                exclude_unset=True
            )
        except ValidationError as e:
            _base_logger.critical(
                f"CRITICAL: Invalid Quorum client configuration: {e}."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        f"CRITICAL: Invalid Quorum client configuration: {e}.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                f"Invalid Quorum client configuration: {e}",
                "Quorum",
                original_exception=e,
            )

        # Initialize base Ethereum wrapper (EthereumClientWrapper expects 'evm' key); Quorum is a superset
        super().__init__({"evm": validated_config}, off_chain_client)

        self.privacy_group_id: Optional[str] = validated_config.get("privacy_group_id")
        self.private_for: Optional[List[str]] = validated_config.get("private_for", [])
        self.poa_middleware: bool = validated_config.get(
            "poa_middleware", True
        )  # Quorum often uses PoA

        self._temp_files: Dict[str, float] = {}  # To track temp ABI file
        self._temp_file_ttl: float = validated_config["temp_file_ttl"]
        self._cleanup_interval: float = validated_config["cleanup_interval"]
        self._cleanup_task: Optional[asyncio.Task] = None

        # Ensure AsyncEth module and optional PoA middleware
        self.w3 = Web3(self.w3.provider, modules={"eth": (AsyncEth,)}, middlewares=[])
        if self.poa_middleware:
            self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        # Contract/account init placeholders (set during initialize)
        self._temp_contract_abi_path: Optional[str] = None

        self.logger.extra.update(
            {
                "rpc_url": str(self.rpc_url),
                "contract_address": self.contract_address,
                "chain_id": self.chain_id,
                "privacy_group_id": self.privacy_group_id or "N/A",
            }
        )
        self._format_log(
            "info",
            f"Quorum DLT Client initialized for RPC: {self.rpc_url}, Contract: {self.contract_address}",
            {"rpc_url": str(self.rpc_url), "contract_address": self.contract_address},
        )

    async def initialize(self) -> None:
        """
        Initializes the client by loading ABI and private key, setting up the contract and account,
        starting background cleanup, and performing a health check.
        """
        # Load contract ABI
        contract_abi_path = await self._load_contract_abi(self.config.get("quorum", {}))
        try:
            with open(contract_abi_path, "r") as f:
                self.contract_abi = json.load(f)
        except Exception as e:
            self._format_log(
                "error", f"Failed to read contract ABI from {contract_abi_path}: {e}"
            )
            raise DLTClientConfigurationError(
                f"Failed to read contract ABI: {e}",
                self.client_type,
                original_exception=e,
            )

        # Load private key
        await self._load_private_key_quorum(self.config.get("quorum", {}))

        # Initialize contract and account after loading ABI and private key
        try:
            self.account = Account.from_key(self.private_key)
            self.w3.eth.default_account = self.account.address
            self.contract = self.w3.eth.contract(
                address=self.contract_address, abi=self.contract_abi
            )
            self.logger.extra.update({"wallet_address": self.account.address})
            self._format_log(
                "info",
                f"Quorum wallet address: {self.account.address}",
                {"wallet_address": self.account.address},
            )
        except Exception as e:
            self._format_log("error", f"Failed to initialize contract/account: {e}")
            raise DLTClientConfigurationError(
                f"Failed to initialize contract/account: {e}",
                self.client_type,
                original_exception=e,
            )

        # Start background cleanup task
        try:
            loop = asyncio.get_running_loop()
            self._cleanup_task = loop.create_task(self._cleanup_temp_files_periodic())
        except RuntimeError:
            # No running loop; caller must start later if needed
            self._cleanup_task = None

        # Perform health check
        hc = await self.health_check(correlation_id=str(uuid.uuid4()))
        if not hc.get("status"):
            msg = hc.get("message", "Unknown init health check failure")
            self._format_log("error", f"Initial Quorum health check failed: {msg}")
            raise DLTClientConfigurationError(
                f"Initial Quorum health check failed: {msg}", self.client_type
            )

    async def _load_contract_abi(self, config: Dict[str, Any]) -> str:
        """Loads contract ABI from path or secrets manager and returns a filesystem path."""
        contract_abi_path = config.get("contract_abi_path")
        contract_abi_secret_id = config.get("contract_abi_secret_id")
        secrets_providers = config.get("secrets_providers", [])
        secrets_provider_config = config.get("secrets_provider_config", {})

        if PRODUCTION_MODE and contract_abi_secret_id:
            for provider_name in secrets_providers:
                try:
                    if provider_name == "aws" and AWS_SECRETS_AVAILABLE:
                        backend = AWSSecretsBackend()
                    elif provider_name == "azure" and AZURE_KEYVAULT_AVAILABLE:
                        backend = AzureKeyVaultBackend(
                            secrets_provider_config.get("vault_url")
                        )
                    elif provider_name == "gcp" and GCP_SECRET_MANAGER_AVAILABLE:
                        backend = GCPSecretManagerBackend(
                            secrets_provider_config.get("project_id")
                        )
                    else:
                        if QUORUM_METRICS:
                            QUORUM_METRICS["secrets_unavailable_total"].labels(
                                client_type=self.client_type, backend=provider_name
                            ).inc()
                        self._format_log(
                            "warning",
                            f"Secrets backend '{provider_name}' requested but unavailable for contract ABI.",
                            {"secrets_provider": provider_name},
                        )
                        continue

                    abi_json = await backend.get_secret(contract_abi_secret_id)
                    with temp_file(
                        abi_json, ttl=self.config["quorum"]["temp_file_ttl"]
                    ) as path:
                        self._temp_contract_abi_path = path
                        self._temp_files[path] = time.time()
                    self._format_log(
                        "info",
                        f"Contract ABI loaded from secrets backend: {provider_name} into temporary file.",
                    )
                    return self._temp_contract_abi_path
                except Exception as e:
                    if QUORUM_METRICS:
                        QUORUM_METRICS["contract_abi_load_failure"].labels(
                            client_type=self.client_type, source_type=provider_name
                        ).inc()
                    self._format_log(
                        "warning",
                        f"Failed to fetch contract ABI from {provider_name}: {e}",
                        {"secrets_provider": provider_name},
                    )
                    continue

            if QUORUM_METRICS:
                QUORUM_METRICS["contract_abi_load_failure"].labels(
                    client_type=self.client_type, source_type="all_secrets_failed"
                ).inc()
            _base_logger.critical(
                "CRITICAL: Failed to load contract ABI from any configured secrets backend."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        "CRITICAL: Failed to load Quorum contract ABI from secrets.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                "Failed to load contract ABI from any configured secrets backend.",
                self.client_type,
            )

        if contract_abi_path:
            if not os.path.exists(contract_abi_path):
                if QUORUM_METRICS:
                    QUORUM_METRICS["contract_abi_load_failure"].labels(
                        client_type=self.client_type, source_type="file_not_found"
                    ).inc()
                _base_logger.critical(
                    f"CRITICAL: Contract ABI not found at {contract_abi_path}."
                )
                try:
                    asyncio.get_running_loop().create_task(
                        alert_operator(
                            f"CRITICAL: Contract ABI not found at {contract_abi_path}.",
                            level="CRITICAL",
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientConfigurationError(
                    f"Contract ABI not found at {contract_abi_path}.", self.client_type
                )
            self._format_log(
                "warning",
                "Contract ABI loaded from local file path. Not recommended for production if secrets are available.",
            )
            return contract_abi_path

        if QUORUM_METRICS:
            QUORUM_METRICS["contract_abi_load_failure"].labels(
                client_type=self.client_type, source_type="no_source"
            ).inc()
        _base_logger.critical("CRITICAL: No contract ABI path or secret ID provided.")
        try:
            asyncio.get_running_loop().create_task(
                alert_operator(
                    "CRITICAL: No Quorum contract ABI source configured.",
                    level="CRITICAL",
                )
            )
        except RuntimeError:
            pass
        raise DLTClientConfigurationError(
            "No Quorum contract ABI source configured.", self.client_type
        )

    async def _load_private_key_quorum(self, config: Dict[str, Any]):
        """Loads the private key from the configured secrets provider or direct config (non-prod)."""
        private_key_direct = config.get("private_key")
        private_key_secret_id = config.get("private_key_secret_id")
        secrets_providers = config.get("secrets_providers", [])
        secrets_provider_config = config.get("secrets_provider_config", {})

        if PRODUCTION_MODE and not private_key_secret_id:
            if QUORUM_METRICS:
                QUORUM_METRICS["private_key_load_failure"].labels(
                    client_type=self.client_type, source_type="missing_secret_id"
                ).inc()
            _base_logger.critical(
                "CRITICAL: In PRODUCTION_MODE, private_key_secret_id must be provided for Quorum private key."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        "CRITICAL: Quorum private key secret ID not configured in production.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                "private_key_secret_id is required in PRODUCTION_MODE.",
                self.client_type,
            )

        if private_key_secret_id:
            for provider_name in secrets_providers:
                try:
                    if provider_name == "aws" and AWS_SECRETS_AVAILABLE:
                        backend = AWSSecretsBackend()
                    elif provider_name == "azure" and AZURE_KEYVAULT_AVAILABLE:
                        backend = AzureKeyVaultBackend(
                            secrets_provider_config.get("vault_url")
                        )
                    elif provider_name == "gcp" and GCP_SECRET_MANAGER_AVAILABLE:
                        backend = GCPSecretManagerBackend(
                            secrets_provider_config.get("project_id")
                        )
                    else:
                        if QUORUM_METRICS:
                            QUORUM_METRICS["secrets_unavailable_total"].labels(
                                client_type=self.client_type, backend=provider_name
                            ).inc()
                        self._format_log(
                            "warning",
                            f"Secrets backend '{provider_name}' requested but unavailable for private key.",
                            {"secrets_provider": provider_name},
                        )
                        continue

                    self.private_key = await backend.get_secret(private_key_secret_id)
                    self._format_log(
                        "info",
                        f"Private key loaded from secrets backend: {provider_name}.",
                    )
                    return  # Successfully loaded
                except Exception as e:
                    if QUORUM_METRICS:
                        QUORUM_METRICS["private_key_load_failure"].labels(
                            client_type=self.client_type, source_type=provider_name
                        ).inc()
                    self._format_log(
                        "warning",
                        f"Failed to fetch private key from {provider_name}: {e}",
                        {"secrets_provider": provider_name},
                    )
                    continue

            if QUORUM_METRICS:
                QUORUM_METRICS["private_key_load_failure"].labels(
                    client_type=self.client_type, source_type="all_secrets_failed"
                ).inc()
            _base_logger.critical(
                "CRITICAL: Failed to load private key from any configured secrets backend."
            )
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        "CRITICAL: Failed to load Quorum private key from secrets.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                "Failed to load private key from any configured secrets backend.",
                self.client_type,
            )

        # Fallback to direct private_key (only allowed if PRODUCTION_MODE is False)
        if private_key_direct:
            self.private_key = private_key_direct
            self._format_log(
                "warning",
                "Private key loaded directly from config. Not recommended for production.",
            )
        else:
            if QUORUM_METRICS:
                QUORUM_METRICS["private_key_load_failure"].labels(
                    client_type=self.client_type, source_type="no_source"
                ).inc()
            _base_logger.critical("CRITICAL: No private key source configured.")
            try:
                asyncio.get_running_loop().create_task(
                    alert_operator(
                        "CRITICAL: No Quorum private key source configured.",
                        level="CRITICAL",
                    )
                )
            except RuntimeError:
                pass
            raise DLTClientConfigurationError(
                "No Quorum private key source configured.", self.client_type
            )

    async def _initial_startup_health_check(self):
        """Deprecated: use initialize(). Kept for backward compatibility."""
        await self.initialize()

    def _format_log(
        self, level: str, message: str, extra: Dict[str, Any] = None
    ) -> None:
        """
        Formats logs as JSON or text based on configuration.
        Args:
            level: Log level (e.g., "info", "error", "warning", "audit").
            message: The log message.
            extra: Additional metadata to include in the log.
        """
        if level.lower() == "audit":
            level = "info"
        extra = extra or {}
        extra.update({"client_type": self.client_type})
        if self._log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **extra,
            }
            getattr(self.logger, level.lower())(json.dumps(scrub_secrets(log_entry)))
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"quorum_client_error.{level.lower()}",
                            message=message,
                            details=scrub_secrets(extra),
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=extra)
            if level.upper() in ["ERROR", "CRITICAL"]:
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"quorum_client_error.{level.lower()}",
                            message=message,
                            details=scrub_secrets(extra),
                        )
                    )
                except RuntimeError:
                    pass

    async def _cleanup_temp_files_periodic(self) -> None:
        """Background coroutine to clean up expired temporary files."""
        while True:
            try:
                current_time = time.time()
                # Iterate over a copy of the list to allow modification during iteration
                for temp_file_path in list(self._temp_files):
                    creation_time = self._temp_files.get(temp_file_path)
                    if (
                        creation_time
                        and current_time - creation_time > self._temp_file_ttl
                    ):
                        try:
                            os.unlink(temp_file_path)
                            self._temp_files.pop(
                                temp_file_path, None
                            )  # Remove from tracking
                            _base_logger.info(
                                f"Cleaned up expired temporary file: {temp_file_path}"
                            )
                        except OSError as e:
                            _base_logger.warning(
                                f"Failed to clean up temporary file {temp_file_path}: {e}"
                            )
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                break  # Exit gracefully if task is cancelled
            except Exception as e:
                self._format_log(
                    "warning",
                    f"Error in temp file cleanup task: {e}",
                    {"error_code": "TEMP_CLEANUP_FAILED"},
                )
                # Do not re-raise, allow cleanup task to continue running

    @async_retry(
        catch_exceptions=(
            DLTClientConfigurationError,
            asyncio.TimeoutError,
            DLTClientCircuitBreakerError,
        )
    )
    async def _rotate_credentials(
        self, new_private_key: str, correlation_id: Optional[str] = None
    ) -> None:
        """
        Rotates Quorum private key at runtime. This method is internal and would be called
        by a secrets rotation service.
        """
        with TRACER.start_as_current_span(
            "quorum.rotate_credentials", attributes={"correlation_id": correlation_id}
        ) as span:
            try:
                if not new_private_key or not re.match(
                    r"^(0x)?[a-fA-F0-9]{64}$", new_private_key
                ):
                    if QUORUM_METRICS:
                        QUORUM_METRICS["private_key_load_failure"].labels(
                            client_type=self.client_type,
                            source_type="rotation_invalid_key",
                        ).inc()
                    self._format_log(
                        "error",
                        "New private key is empty or invalid format for rotation",
                        {
                            "correlation_id": correlation_id,
                            "error_code": "INVALID_PRIVATE_KEY",
                        },
                    )
                    raise DLTClientValidationError(
                        "New private key cannot be empty or invalid format",
                        self.client_type,
                        correlation_id=correlation_id,
                    )
                self._format_log(
                    "info",
                    "Initiating Quorum private key rotation",
                    {"correlation_id": correlation_id},
                )
                self.private_key = new_private_key
                self.account = self.w3.eth.account.from_key(self.private_key)
                self.w3.eth.default_account = self.account.address
                self.logger.extra.update({"wallet_address": self.account.address})
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Quorum private key rotated successfully. New address: {self.account.address}",
                    {
                        "correlation_id": correlation_id,
                        "new_wallet_address": self.account.address,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "quorum_credentials.rotated",
                            new_wallet_address=self.account.address,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=f"Quorum credential rotation error: {e}",
                    )
                )
                span.record_exception(e)
                if QUORUM_METRICS:
                    QUORUM_METRICS["private_key_load_failure"].labels(
                        client_type=self.client_type, source_type="rotation_failed"
                    ).inc()
                self._format_log(
                    "error",
                    f"Failed to rotate Quorum private key: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "QUORUM_ROTATE_FAILED",
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "quorum_credentials.rotation_failure",
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientAuthError(
                    f"Failed to rotate Quorum private key: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientAuthError,
            DLTClientTransactionError,
            DLTClientQueryError,
            DLTClientCircuitBreakerError,
        )
    )
    async def health_check(
        self, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verifies Quorum client connectivity, contract accessibility, and privacy settings.
        Args:
            correlation_id: Optional unique ID for tracing.
        Returns:
            Dict[str, Any]: Structured health check result with status, message, and details.
        """
        if not getattr(self, "account", None):
            return {
                "status": False,
                "message": "Quorum client not fully initialized (private key/account issue).",
                "details": {"error": "Account not loaded."},
            }

        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check_logic",
            attributes={"correlation_id": correlation_id},
        ) as span:
            try:
                # Perform standard EVM health check (from base class)
                result = await super().health_check(correlation_id=correlation_id)
                if not result["status"]:
                    return result

                # Additional Quorum-specific privacy checks
                privacy_details = {}
                if (
                    self.privacy_group_id and self.private_for
                ):  # Both must be present for privacy
                    try:
                        privacy_details = {
                            "privacy_group_id": self.privacy_group_id,
                            "private_for_count": len(self.private_for),
                            "privacy_config_valid": True,
                        }
                        self._format_log(
                            "info",
                            "Quorum privacy configuration is valid.",
                            {"correlation_id": correlation_id},
                        )
                    except Exception as e:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                description=f"Privacy check failed: {e}",
                            )
                        )
                        span.record_exception(e)
                        if QUORUM_METRICS:
                            QUORUM_METRICS["privacy_config_invalid"].labels(
                                client_type=self.client_type
                            ).inc()
                        self._format_log(
                            "error",
                            f"Quorum privacy check failed: {e}",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "QUORUM_PRIVACY_CHECK_FAILED",
                            },
                        )
                        return {
                            "status": False,
                            "message": f"Quorum privacy check failed: {str(e)}",
                            "details": {"error_code": "QUORUM_PRIVACY_CHECK_FAILED"},
                        }
                elif self.privacy_group_id or self.private_for:
                    span.set_status(
                        Status(
                            StatusCode.ERROR,
                            description="Incomplete privacy configuration",
                        )
                    )
                    if QUORUM_METRICS:
                        QUORUM_METRICS["privacy_config_invalid"].labels(
                            client_type=self.client_type
                        ).inc()
                    self._format_log(
                        "error",
                        "Incomplete Quorum privacy configuration: both privacy_group_id and private_for are required for private transactions.",
                        {
                            "correlation_id": correlation_id,
                            "error_code": "QUORUM_PRIVACY_INCOMPLETE",
                        },
                    )
                    return {
                        "status": False,
                        "message": "Incomplete Quorum privacy configuration.",
                        "details": {"error_code": "QUORUM_PRIVACY_INCOMPLETE"},
                    }

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    "Quorum client is connected, contract is reachable, and privacy settings are valid",
                    {"correlation_id": correlation_id, **privacy_details},
                )
                return {
                    "status": True,
                    "message": "Quorum client is connected, contract is reachable, and privacy settings are valid",
                    "details": {
                        "rpc_url": str(self.rpc_url),
                        "contract_address": self.contract_address,
                        "chain_id": self.chain_id,
                        "wallet_address": self.account.address,
                        **privacy_details,
                    },
                }
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Unexpected error: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Quorum health check failed unexpectedly: {e}",
                    {
                        "correlation_id": correlation_id,
                        "error_code": "QUORUM_HEALTHCHECK_FAILED",
                    },
                )
                return {
                    "status": False,
                    "message": f"Quorum health check failed unexpectedly: {str(e)}",
                    "details": {"error_code": "QUORUM_HEALTHCHECK_FAILED"},
                }

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientAuthError,
            DLTClientTransactionError,
            DLTClientTimeoutError,
            DLTClientCircuitBreakerError,
        )
    )
    async def _send_transaction(
        self,
        tx_builder_method: Callable,
        gas_limit: Optional[int] = None,
        gas_price: Optional[int] = None,
        max_fee_per_gas: Optional[int] = None,
        max_priority_fee_per_gas: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Sends a transaction to the Quorum network, supporting private transactions when configured.
        """
        if not getattr(self, "account", None):
            raise DLTClientConfigurationError(
                "Quorum account not initialized. Private key likely failed to load.",
                self.client_type,
            )

        with TRACER.start_as_current_span(
            f"{self.client_type}.send_transaction",
            attributes={"correlation_id": correlation_id},
        ) as span:
            try:
                nonce = await self._circuit_breaker.execute(
                    lambda: self.w3.eth.get_transaction_count(self.account.address)
                )
                tx_params = {
                    "chainId": self.chain_id,
                    "from": self.account.address,
                    "nonce": nonce,
                    "gas": gas_limit or self.default_gas_limit,
                    "value": 0,
                }

                # Gas pricing
                if (
                    max_fee_per_gas is not None and max_priority_fee_per_gas is not None
                ) or (
                    self.default_max_fee_per_gas_gwei is not None
                    and self.default_max_priority_fee_per_gas_gwei is not None
                ):
                    tx_params["maxFeePerGas"] = self.w3.to_wei(
                        max_fee_per_gas or self.default_max_fee_per_gas_gwei, "gwei"
                    )
                    tx_params["maxPriorityFeePerGas"] = self.w3.to_wei(
                        max_priority_fee_per_gas
                        or self.default_max_priority_fee_per_gas_gwei,
                        "gwei",
                    )
                    self._format_log(
                        "debug", "Using EIP-1559 gas parameters from config/args."
                    )
                else:
                    try:
                        latest_block = await self._circuit_breaker.execute(
                            lambda: self.w3.eth.get_block(
                                "latest", full_transactions=False
                            )
                        )
                        if (
                            "baseFeePerGas" in latest_block
                            and latest_block.baseFeePerGas is not None
                        ):
                            base_fee_per_gas = latest_block.baseFeePerGas
                            priority_fee_gwei = (
                                self.default_max_priority_fee_per_gas_gwei or 2
                            )
                            tx_params["maxPriorityFeePerGas"] = self.w3.to_wei(
                                priority_fee_gwei, "gwei"
                            )
                            tx_params["maxFeePerGas"] = (
                                base_fee_per_gas + tx_params["maxPriorityFeePerGas"]
                            )
                            self._format_log(
                                "debug",
                                f"Using EIP-1559 estimation: MaxFeePerGas={self.w3.from_wei(tx_params['maxFeePerGas'], 'gwei')} Gwei, PriorityFee={priority_fee_gwei} Gwei",
                                {"correlation_id": correlation_id},
                            )
                        else:
                            tx_params["gasPrice"] = (
                                gas_price
                                or await self._circuit_breaker.execute(
                                    lambda: self.w3.eth.gas_price
                                )
                            )
                            self._format_log(
                                "debug",
                                f"Using legacy gasPrice: {self.w3.from_wei(tx_params['gasPrice'], 'gwei')} Gwei",
                                {"correlation_id": correlation_id},
                            )
                    except Exception as gas_e:
                        self._format_log(
                            "warning",
                            f"Failed to estimate gas fees, falling back to fixed gasPrice: {gas_e}",
                            {
                                "correlation_id": correlation_id,
                                "error_code": "GAS_ESTIMATION_FAILED",
                            },
                        )
                        tx_params["gasPrice"] = gas_price or self.w3.to_wei(
                            self.fallback_gas_price_gwei, "gwei"
                        )

                # Add Quorum-specific privacy parameters (for RPCs that support it)
                is_private_tx = bool(self.privacy_group_id and self.private_for)
                span.set_attribute("private_transaction", is_private_tx)
                if is_private_tx:
                    span.set_attribute("privacy_group_id", self.privacy_group_id)
                    span.set_attribute(
                        "private_for_recipients", ",".join(self.private_for)
                    )

                # Build transaction
                transaction = await self._circuit_breaker.execute(
                    lambda: tx_builder_method.build_transaction(tx_params)
                )

                # Audit: Transaction Signing (hash of unsigned tx for audit purposes)
                unsigned_tx_json = json.dumps(
                    transaction, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                signed_tx = self.w3.eth.account.sign_transaction(
                    transaction, private_key=self.private_key
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "quorum_tx.signed",
                            tx_hash_unsigned=self.w3.to_hex(
                                self.w3.keccak(unsigned_tx_json)
                            ),
                            signer_address=self.account.address,
                            is_private=is_private_tx,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass

                # Send transaction: support Quorum/Tessera private tx RPC when configured, else standard path
                if is_private_tx:
                    raw_tx_hex = self.w3.to_hex(signed_tx.rawTransaction)
                    tx_hash_hex: Optional[str] = None
                    # Try eth_sendRawPrivateTransaction (Quorum/Tessera)
                    try:
                        result = await self._circuit_breaker.execute(
                            lambda: self.w3.manager.coro_request(
                                "eth_sendRawPrivateTransaction",
                                [
                                    raw_tx_hex,
                                    {
                                        "privateFor": self.private_for,
                                        "privacyGroupId": self.privacy_group_id,
                                    },
                                ],
                            )
                        )
                        if isinstance(result, dict) and "result" in result:
                            tx_hash_hex = result["result"]
                        elif isinstance(result, str):
                            tx_hash_hex = result
                    except Exception as e1:
                        # Fallback to eea_sendRawTransaction (Besu/EEA)
                        try:
                            result = await self._circuit_breaker.execute(
                                lambda: self.w3.manager.coro_request(
                                    "eea_sendRawTransaction",
                                    [
                                        raw_tx_hex,
                                        {
                                            "privateFor": self.private_for,
                                            "privacyGroupId": self.privacy_group_id,
                                        },
                                    ],
                                )
                            )
                            if isinstance(result, dict) and "result" in result:
                                tx_hash_hex = result["result"]
                            elif isinstance(result, str):
                                tx_hash_hex = result
                        except Exception as e2:
                            # Final fallback to public send (not private) with explicit warning
                            self._format_log(
                                "warning",
                                f"Private tx RPC not supported (errors: {e1} | {e2}). Falling back to public send_raw_transaction.",
                                {"correlation_id": correlation_id},
                            )
                    if not tx_hash_hex:
                        # Either RPCs failed or returned unexpected structure; try public send
                        tx_hash = await self._circuit_breaker.execute(
                            lambda: self.w3.eth.send_raw_transaction(
                                signed_tx.rawTransaction
                            )
                        )
                        tx_hash_hex = tx_hash.hex()
                else:
                    tx_hash = await self._circuit_breaker.execute(
                        lambda: self.w3.eth.send_raw_transaction(
                            signed_tx.rawTransaction
                        )
                    )
                    tx_hash_hex = tx_hash.hex()

                self._format_log(
                    "info",
                    f"Transaction sent: {tx_hash_hex} (Private: {is_private_tx})",
                    {"tx_hash": tx_hash_hex, "correlation_id": correlation_id},
                )
                span.set_attribute("tx.hash", tx_hash_hex)

                # Wait for confirmation
                try:
                    receipt = await self._circuit_breaker.execute(
                        lambda: self.w3.eth.wait_for_transaction_receipt(
                            tx_hash_hex, timeout=self.tx_confirm_timeout
                        )
                    )
                except TimeExhausted as e:
                    span.set_status(Status(StatusCode.ERROR, description="Timeout"))
                    span.record_exception(e)
                    self._format_log(
                        "error",
                        f"Transaction confirmation timed out: {e}",
                        {"correlation_id": correlation_id},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "quorum_tx.confirmation_timeout",
                                error_message=str(e),
                                is_private=is_private_tx,
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                    raise DLTClientTimeoutError(
                        f"Transaction confirmation timed out: {e}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )

                if getattr(receipt, "status", 1) == 0:
                    span.set_status(
                        Status(
                            StatusCode.ERROR, description="Transaction failed on-chain"
                        )
                    )
                    self._format_log(
                        "error",
                        f"Transaction failed on-chain. Receipt: {receipt}",
                        {
                            "correlation_id": correlation_id,
                            "receipt": scrub_secrets(receipt),
                        },
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "quorum_tx.failed_on_chain",
                                tx_hash=tx_hash_hex,
                                receipt=scrub_secrets(receipt),
                                is_private=is_private_tx,
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                    raise DLTClientTransactionError(
                        f"Transaction failed on-chain. Receipt: {receipt}",
                        self.client_type,
                        details=scrub_secrets(receipt),
                        correlation_id=correlation_id,
                    )

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Transaction confirmed: {tx_hash_hex}",
                    {"tx_hash": tx_hash_hex, "correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "quorum_tx.confirmed",
                            tx_hash=tx_hash_hex,
                            receipt_block_number=getattr(receipt, "blockNumber", None),
                            is_private=is_private_tx,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return tx_hash_hex
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Transaction failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to send Quorum transaction: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "quorum_tx.send_failure",
                            error_message=str(e),
                            is_private=bool(self.privacy_group_id and self.private_for),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to send Quorum transaction: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        """
        Closes any underlying client connections for Quorum.
        Also securely deletes any temporary files created.
        """
        try:
            await super().close()  # Call parent's close method
            if self._cleanup_task:
                self._cleanup_task.cancel()
                try:
                    await self._cleanup_task  # Wait for cleanup task to finish
                except asyncio.CancelledError:
                    pass

            # Explicitly clean up any remaining temporary files
            if self._temp_contract_abi_path and os.path.exists(
                self._temp_contract_abi_path
            ):
                try:
                    os.unlink(self._temp_contract_abi_path)
                    _base_logger.info(
                        f"Cleaned up temporary Quorum ABI file: {self._temp_contract_abi_path}"
                    )
                except OSError as e:
                    _base_logger.warning(
                        f"Failed to delete temporary Quorum ABI file {self._temp_contract_abi_path}: {e}"
                    )
                finally:
                    self._temp_contract_abi_path = None

            self.w3 = None
            self.contract = None
            self.account = None
            self._format_log(
                "info", "Quorum DLT Client closed", {"client_type": self.client_type}
            )
        except Exception as e:
            self._format_log(
                "warning",
                f"Failed to close Quorum client cleanly: {e}",
                {"client_type": self.client_type, "error_code": "QUORUM_CLOSE_FAILED"},
            )
            # Do not re-raise, allow graceful shutdown to continue
        finally:
            self._format_log(
                "audit",
                "Quorum DLT Client cleanup attempted",
                {"client_type": self.client_type},
            )
