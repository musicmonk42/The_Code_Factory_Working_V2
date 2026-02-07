# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Production-hardened EVM (Ethereum-compatible) DLT client using web3.py.

Key improvements over prior version:
- Clear separation of base vs client-specific config (self.config vs self.client_config).
- No sys.exit calls in library code; raises typed exceptions for callers to handle.
- Uses synchronous Web3(HTTPProvider) and runs ALL Web3 calls in a thread executor
  under a circuit breaker to avoid blocking the event loop.
- Correct handling of Web3 properties (e.g., chain_id, gas_price) via executor.
- Metrics calls are guarded when Prometheus is unavailable.
- Secrets providers (AWS/Azure/GCP) integrated; Azure async client properly closed.
- JSON-safe audit logging of receipts and values.
- Optional EIP-1559 fee calculation with configurable baseFee multiplier.
- Explicit, safe rate limiting and structured logging.
"""

import asyncio
import inspect
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, Final, Optional, Tuple, Union
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, ValidationError, validator

# web3 sync API (we will run calls in executor)
from web3 import HTTPProvider, Web3

from .dlt_base import (
    AUDIT,
    PRODUCTION_MODE,
    TRACER,
    BaseDLTClient,
    BaseOffChainClient,
    DLTClientAuthError,
    DLTClientCircuitBreakerError,
    DLTClientConfigurationError,
    DLTClientConnectivityError,
    DLTClientError,
    DLTClientQueryError,
    DLTClientResourceError,
    DLTClientTimeoutError,
    DLTClientTransactionError,
    DLTClientValidationError,
    Status,
    StatusCode,
    _base_logger,
    async_retry,
    scrub_secrets,
)

# Handle web3 version differences for middleware
try:
    # web3 v6+/v7+
    from web3.middleware import ExtraDataToPOAMiddleware

    POA_MIDDLEWARE = ExtraDataToPOAMiddleware
except ImportError:
    try:
        # web3 v5 and earlier
        from web3.middleware import geth_poa_middleware

        POA_MIDDLEWARE = geth_poa_middleware
    except ImportError:
        POA_MIDDLEWARE = None
        _base_logger.warning("POA middleware not available in this web3 version")

# Handle exception imports for different web3 versions
try:
    # web3 v7+ - exceptions are in web3.exceptions
    from web3.exceptions import ContractLogicError, TransactionNotFound

    # ContractCustomError might not exist in v7, use ContractLogicError as fallback
    try:
        from web3.exceptions import ContractCustomError
    except ImportError:
        ContractCustomError = ContractLogicError
except ImportError:
    # Fallback for older versions or if structure changes
    try:
        from web3.exceptions import (
            ContractCustomError,
            ContractLogicError,
            TransactionNotFound,
        )
    except ImportError:

        class TransactionNotFound(Exception):
            pass

        class ContractLogicError(Exception):
            pass

        ContractCustomError = ContractLogicError
        _base_logger.warning(
            "Could not import web3 exceptions; using generic fallbacks."
        )

from eth_account import Account

# Optional timeout helper for close() best-effort
try:
    import async_timeout

    ASYNC_TIMEOUT_AVAILABLE = True
except ImportError:
    ASYNC_TIMEOUT_AVAILABLE = False

# Optional EVM-specific Prometheus metrics
try:
    from prometheus_client import Counter

    EVM_METRICS = {
        "gas_fallback_total": Counter(
            "evm_client_gas_fallback_total",
            "Total number of times EVM client fell back to hardcoded gas price",
            labelnames=["client_type"],
        ),
        "secrets_unavailable_total": Counter(
            "evm_secrets_unavailable_total",
            "Total number of times a secrets backend was requested but unavailable",
            labelnames=["client_type", "backend"],
        ),
        "private_key_load_failure": Counter(
            "evm_private_key_load_failure_total",
            "Total failures loading private key from any source",
            labelnames=["client_type", "source_type"],
        ),
        "tx_pending_timeout": Counter(
            "evm_tx_pending_timeout_total",
            "Total transactions that timed out while pending confirmation",
            labelnames=["client_type"],
        ),
    }
except Exception:
    _base_logger.warning("Prometheus client not available for EVM-specific metrics.")
    EVM_METRICS = {}  # Guarded usage below


# ---------------------------
# Secrets Backend Interfaces
# ---------------------------
class SecretsBackend:
    async def get_secret(self, secret_id: str) -> str:
        raise NotImplementedError


AWS_SECRETS_AVAILABLE = False
try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError

    AWS_SECRETS_AVAILABLE = True

    class AWSSecretsBackend(SecretsBackend):
        def __init__(self):
            if not AWS_SECRETS_AVAILABLE:
                raise DLTClientConfigurationError(
                    "AWS Secrets Manager backend requested but boto3 is not available.",
                    "EVM",
                )
            self.client = boto3.client("secretsmanager")

        async def get_secret(self, secret_id: str) -> str:
            try:
                response = await asyncio.to_thread(
                    self.client.get_secret_value, SecretId=secret_id
                )
                return response["SecretString"]
            except BotoClientError as e:
                raise DLTClientConfigurationError(
                    f"Failed to fetch secret from AWS Secrets Manager: {e}",
                    "EVM",
                    original_exception=e,
                )

except Exception:

    class AWSSecretsBackend(SecretsBackend):  # type: ignore
        def __init__(self):
            raise DLTClientConfigurationError(
                "AWS Secrets Manager backend unavailable.", "EVM"
            )


AZURE_KEYVAULT_AVAILABLE = False
try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets.aio import SecretClient as AsyncSecretClient

    AZURE_KEYVAULT_AVAILABLE = True

    class AzureKeyVaultBackend(SecretsBackend):
        def __init__(self, vault_url: str):
            if not AZURE_KEYVAULT_AVAILABLE:
                raise DLTClientConfigurationError(
                    "Azure Key Vault backend requested but Azure SDK is not available.",
                    "EVM",
                )
            if not vault_url:
                raise DLTClientConfigurationError(
                    "Azure Key Vault URL is required.", "EVM"
                )
            self.client = AsyncSecretClient(
                vault_url=vault_url, credential=DefaultAzureCredential()
            )

        async def get_secret(self, secret_id: str) -> str:
            try:
                secret = await self.client.get_secret(secret_id)
                return secret.value
            except Exception as e:
                raise DLTClientConfigurationError(
                    f"Failed to fetch secret from Azure Key Vault: {e}",
                    "EVM",
                    original_exception=e,
                )
            finally:
                try:
                    await self.client.close()
                except Exception:
                    pass

except Exception:

    class AzureKeyVaultBackend(SecretsBackend):  # type: ignore
        def __init__(self, *_args, **_kwargs):
            raise DLTClientConfigurationError(
                "Azure Key Vault backend unavailable.", "EVM"
            )


GCP_SECRET_MANAGER_AVAILABLE = False
try:
    from google.cloud import secretmanager_v1beta1 as secretmanager

    GCP_SECRET_MANAGER_AVAILABLE = True

    class GCPSecretManagerBackend(SecretsBackend):
        def __init__(self, project_id: str):
            if not GCP_SECRET_MANAGER_AVAILABLE:
                raise DLTClientConfigurationError(
                    "GCP Secret Manager backend requested but Google Cloud SDK is not available.",
                    "EVM",
                )
            if not project_id:
                raise DLTClientConfigurationError(
                    "GCP Project ID is required for GCP Secret Manager.", "EVM"
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
                    "EVM",
                    original_exception=e,
                )

except Exception:

    class GCPSecretManagerBackend(SecretsBackend):  # type: ignore
        def __init__(self, *_args, **_kwargs):
            raise DLTClientConfigurationError(
                "GCP Secret Manager backend unavailable.", "EVM"
            )


# ---------------------------
# Configuration schema
# ---------------------------
class EVMConfig(BaseModel):
    """Configuration schema for EVM client."""

    rpc_url: HttpUrl
    chain_id: int = Field(..., ge=1)
    contract_address: str = Field(..., pattern=r"^0x[a-fA-F0-9]{40}$")
    contract_abi_path: str = Field(..., min_length=1)

    private_key: Optional[str] = None  # Non-prod only; prod requires secrets backend
    secrets_provider: Optional[str] = None  # "aws", "azure", "gcp"
    private_key_secret_id: Optional[str] = None
    secrets_provider_config: Optional[Dict[str, Any]] = (
        None  # e.g., {"vault_url":"..."}, {"project_id":"..."}
    )

    poa_middleware: bool = False
    default_gas_limit: int = Field(2_000_000, ge=21_000)

    # EIP-1559 gas parameters and tuning
    default_max_fee_per_gas_gwei: Optional[int] = Field(None, ge=1)
    default_max_priority_fee_per_gas_gwei: Optional[int] = Field(None, ge=1)
    eip1559_base_fee_multiplier: float = Field(2.0, ge=1.0, le=5.0)

    # Legacy gas fallback
    fallback_gas_price_gwei: int = Field(5, ge=1)

    tx_confirm_timeout: int = Field(120, ge=10)
    min_eth_balance_for_tx: float = Field(0.001, ge=0.0)
    rate_limit_requests_per_second: float = Field(10.0, ge=0.1)
    close_timeout: float = Field(5.0, ge=0.1)
    log_format: str = Field("json", pattern=r"^(json|text)$")

    # Security
    allow_insecure_http: bool = False

    @validator("rpc_url")
    def validate_rpc_url_scheme(cls, v, values):
        parsed = urlparse(str(v))
        if parsed.scheme not in ("http", "https"):
            raise ValueError("rpc_url must use http or https scheme")
        if (
            PRODUCTION_MODE
            and parsed.scheme == "http"
            and not values.get("allow_insecure_http", False)
        ):
            raise ValueError(
                "In PRODUCTION_MODE, HTTPS is required for rpc_url unless allow_insecure_http is true."
            )
        return v

    @validator("private_key", pre=True, always=True)
    def validate_private_key_presence(cls, v, values):
        # In production, require secrets provider; no inline private key allowed
        if PRODUCTION_MODE:
            if not values.get("secrets_provider") or not values.get(
                "private_key_secret_id"
            ):
                raise ValueError(
                    "In PRODUCTION_MODE, private_key must be loaded via 'secrets_provider' and 'private_key_secret_id'."
                )
            return None

        # Non-prod: allow direct key, env fallback, or secrets backend
        if (
            v is None
            and not values.get("private_key_secret_id")
            and not values.get("secrets_provider")
        ):
            env_key = os.getenv("ETHEREUM_PRIVATE_KEY")
            if not env_key:
                raise ValueError(
                    "Provide private_key, or secrets_provider + private_key_secret_id, or set ETHEREUM_PRIVATE_KEY."
                )
            return env_key

        if v and not re.match(r"^(0x)?[a-fA-F0-9]{64}$", v):
            raise ValueError(
                "private_key must be a 64-character hex string (optionally prefixed with 0x)."
            )
        return v

    @validator("secrets_provider")
    def validate_secrets_provider_type(cls, v, values):
        if v and v not in ("aws", "azure", "gcp"):
            raise ValueError("secrets_provider must be one of 'aws', 'azure', 'gcp'.")
        if v == "azure" and not values.get("secrets_provider_config", {}).get(
            "vault_url"
        ):
            raise ValueError(
                "secrets_provider_config.vault_url required for Azure Key Vault."
            )
        if v == "gcp" and not values.get("secrets_provider_config", {}).get(
            "project_id"
        ):
            raise ValueError(
                "secrets_provider_config.project_id required for GCP Secret Manager."
            )
        return v

    @validator("rpc_url")
    def validate_rpc_url_not_mock(cls, v):
        s = str(v).lower()
        if PRODUCTION_MODE and any(
            tok in s for tok in ("mock", "test", "example.com", "localhost")
        ):
            raise ValueError(
                f"Mock/test RPC URL detected: {v}. Not allowed in production."
            )
        return v


# ---------------------------
# Client implementation
# ---------------------------
class EthereumClientWrapper(BaseDLTClient):
    """
    Ethereum/EVM-compatible client using web3.py for on-chain checkpointing.
    """

    client_type: Final[str] = "EVM"

    def __init__(self, config: Dict[str, Any], off_chain_client: "BaseOffChainClient"):
        # 1) Validate client-specific config
        try:
            evm_cfg: Dict[str, Any] = dict(config.get("evm", {}))
            validated_evm = EVMConfig(**evm_cfg).dict(exclude_unset=False)
        except ValidationError as e:
            raise DLTClientValidationError(
                f"Invalid EVM client configuration: {e}", "EVM"
            )
        except Exception as e:
            raise DLTClientValidationError(
                f"Failed to load EVM client configuration: {e}",
                "EVM",
                original_exception=e,
            )

        # 2) Build base (common) config subset for BaseDLTClient
        base_keys = (
            "default_timeout_seconds",
            "retry_policy",
            "circuit_breaker_threshold",
            "circuit_breaker_reset_timeout",
        )
        base_cfg = {k: config[k] for k in base_keys if k in config}
        base_cfg.setdefault("default_timeout_seconds", 30)

        super().__init__(base_cfg, off_chain_client)

        # Store client-specific config separately
        self.client_config: Dict[str, Any] = validated_evm

        # Core fields
        self.rpc_url: str = str(self.client_config["rpc_url"])
        self.chain_id: int = int(self.client_config["chain_id"])
        self.contract_address: str = self.client_config["contract_address"]
        self.contract_abi_path: str = self.client_config["contract_abi_path"]
        self.poa_middleware: bool = bool(self.client_config["poa_middleware"])
        self._log_format: str = self.client_config["log_format"]

        # Gas and limits
        self.gas_limit: int = int(self.client_config["default_gas_limit"])
        self.max_fee_per_gas_gwei: Optional[int] = self.client_config.get(
            "default_max_fee_per_gas_gwei"
        )
        self.max_priority_fee_per_gas_gwei: Optional[int] = self.client_config.get(
            "default_max_priority_fee_per_gas_gwei"
        )
        self.base_fee_multiplier: float = float(
            self.client_config.get("eip1559_base_fee_multiplier", 2.0)
        )
        self.fallback_gas_price_gwei: int = int(
            self.client_config["fallback_gas_price_gwei"]
        )
        self.tx_confirm_timeout: int = int(self.client_config["tx_confirm_timeout"])
        self.min_eth_balance_for_tx: float = float(
            self.client_config["min_eth_balance_for_tx"]
        )

        # Rate limiter
        self._rate_limit_delay: float = 1.0 / float(
            self.client_config["rate_limit_requests_per_second"]
        )
        self._last_request_time: float = 0.0

        # Initialize web3 (sync provider) and contract ABI
        if not os.path.exists(self.contract_abi_path):
            raise DLTClientConfigurationError(
                f"Contract ABI not found at {self.contract_abi_path}.", self.client_type
            )
        try:
            with open(self.contract_abi_path, "r") as f:
                self.contract_abi = json.load(f)
        except Exception as e:
            raise DLTClientConfigurationError(
                f"Failed to load contract ABI: {e}",
                self.client_type,
                original_exception=e,
            )

        self.w3 = Web3(HTTPProvider(self.rpc_url))
        if self.poa_middleware:
            if POA_MIDDLEWARE:
                self.w3.middleware_onion.inject(POA_MIDDLEWARE, layer=0)
            else:
                self._format_log(
                    "warning", "POA middleware requested but not available"
                )

        self.contract = self.w3.eth.contract(
            address=self.contract_address, abi=self.contract_abi
        )

        # Signer (lazy-load on first use/health_check)
        self.private_key: Optional[str] = None
        self.account: Optional[Account] = None

        # Logging context
        self.logger.extra.update(
            {
                "rpc_url": self.rpc_url,
                "contract_address": self.contract_address,
                "chain_id": self.chain_id,
            }
        )
        self._format_log(
            "info",
            f"EVM client initialized for RPC: {self.rpc_url}, Contract: {self.contract_address}",
            {"rpc_url": self.rpc_url, "contract_address": self.contract_address},
        )

    # ------------- internal helpers -------------

    def _format_log(
        self, level: str, message: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Structured logging, with optional JSON and audit on critical paths.
        """
        extra = extra or {}
        extra.update({"client_type": self.client_type})

        if self._log_format == "json":
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "level": level.upper(),
                "message": message,
                **extra,
            }
            # Fix: Handle potential TypeError from scrub_secrets with nested dicts
            try:
                safe_entry = scrub_secrets(log_entry)
            except TypeError:
                # Fallback: manually create a safe copy without using scrub_secrets
                safe_entry = self._safe_copy_dict(log_entry)

            getattr(self.logger, level.lower())(json.dumps(safe_entry))
            if level.upper() in ("ERROR", "CRITICAL"):
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"evm_client_error.{level.lower()}",
                            message=message,
                            details=safe_entry,
                        )
                    )
                except RuntimeError:
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=extra)
            if level.upper() in ("ERROR", "CRITICAL"):
                try:
                    # Fix: Handle potential TypeError here too
                    try:
                        safe_extra = scrub_secrets(extra)
                    except TypeError:
                        safe_extra = self._safe_copy_dict(extra)
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"evm_client_error.{level.lower()}",
                            message=message,
                            details=safe_extra,
                        )
                    )
                except RuntimeError:
                    pass

    def _safe_copy_dict(self, obj: Any, visited: Optional[set] = None) -> Any:
        """
        Create a safe copy of an object, handling cycles and redacting sensitive data.
        This is a fallback when scrub_secrets fails due to unhashable types.
        """
        if visited is None:
            visited = set()

        # Use id() for cycle detection since objects themselves may not be hashable
        obj_id = id(obj)
        if obj_id in visited:
            return "... [cycle detected] ..."

        if isinstance(obj, dict):
            visited.add(obj_id)
            result = {}
            for k, v in obj.items():
                # Redact sensitive keys
                if isinstance(k, str) and any(
                    sensitive in k.lower()
                    for sensitive in [
                        "password",
                        "secret",
                        "key",
                        "token",
                        "auth",
                        "credential",
                    ]
                ):
                    result[k] = "***REDACTED_BY_KEY***"
                else:
                    result[k] = self._safe_copy_dict(v, visited)
            visited.remove(obj_id)
            return result
        elif isinstance(obj, list):
            visited.add(obj_id)
            result = [self._safe_copy_dict(item, visited) for item in obj]
            visited.remove(obj_id)
            return result
        elif isinstance(obj, tuple):
            visited.add(obj_id)
            result = tuple(self._safe_copy_dict(item, visited) for item in obj)
            visited.remove(obj_id)
            return result
        else:
            # For non-container types, return as-is
            return obj

    async def _rate_limit(self) -> None:
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    async def _exec_w3(self, func: Callable, *args, **kwargs):
        """
        Execute a synchronous web3 function in a thread executor under the circuit breaker.
        """
        return await self._circuit_breaker.execute(
            lambda: self._run_blocking_in_executor(func, *args, **kwargs)
        )

    async def _exec_w3_prop(self, getter: Callable[[], Any]):
        """
        Execute a synchronous property access via executor under the circuit breaker.
        Pass a zero-arg callable that returns the property (e.g., lambda: self.w3.eth.chain_id)
        """
        return await self._circuit_breaker.execute(
            lambda: self._run_blocking_in_executor(lambda: getter())
        )

    async def _ensure_initialized(self):
        """
        Lazily load private key/account from configured secrets if not already loaded.
        """
        if self.account and self.private_key:
            return

        cfg = self.client_config
        provider = cfg.get("secrets_provider")
        secret_id = cfg.get("private_key_secret_id")
        direct_key = cfg.get("private_key")

        try:
            if provider:
                if provider == "aws" and AWS_SECRETS_AVAILABLE:
                    backend = AWSSecretsBackend()
                elif provider == "azure" and AZURE_KEYVAULT_AVAILABLE:
                    backend = AzureKeyVaultBackend(
                        cfg.get("secrets_provider_config", {}).get("vault_url")
                    )
                elif provider == "gcp" and GCP_SECRET_MANAGER_AVAILABLE:
                    backend = GCPSecretManagerBackend(
                        cfg.get("secrets_provider_config", {}).get("project_id")
                    )
                else:
                    if EVM_METRICS:
                        EVM_METRICS["secrets_unavailable_total"].labels(
                            client_type=self.client_type, backend=str(provider)
                        ).inc()
                    raise DLTClientConfigurationError(
                        f"Secrets backend '{provider}' requested but is not available or misconfigured.",
                        self.client_type,
                    )
                self.private_key = await backend.get_secret(secret_id)
                self._format_log(
                    "info", f"Private key loaded from secrets backend: {provider}."
                )
            elif direct_key:
                # Non-prod only (validated by schema)
                self.private_key = direct_key
                self._format_log(
                    "warning",
                    "Private key loaded directly from config. Not recommended for production.",
                )
            else:
                if EVM_METRICS:
                    EVM_METRICS["private_key_load_failure"].labels(
                        client_type=self.client_type, source_type="none_provided"
                    ).inc()
                raise DLTClientConfigurationError(
                    "No private key source configured for EVM client.", self.client_type
                )

            # Initialize signer and contract defaults
            self.account = Account.from_key(self.private_key)
            self.w3.eth.default_account = self.account.address
            # Contract already constructed; nothing to update here.
            self.logger.extra.update({"wallet_address": self.account.address})
            self._format_log(
                "info",
                f"EVM wallet address initialized: {self.account.address}",
                {"wallet_address": self.account.address},
            )

        except Exception as e:
            if EVM_METRICS:
                EVM_METRICS["private_key_load_failure"].labels(
                    client_type=self.client_type, source_type=provider or "direct"
                ).inc()
            raise DLTClientConfigurationError(
                f"Failed to initialize EVM account: {e}",
                self.client_type,
                original_exception=e,
            )

    # ----------------- public API -----------------

    async def health_check(
        self, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Checks EVM client connectivity and basic contract interaction.
        Returns a dict with status, message, and details (does not raise on expected failures).
        """
        await self._ensure_initialized()

        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": correlation_id or ""},
        ) as span:
            try:
                await self._rate_limit()
                is_connected = await self._exec_w3(self.w3.is_connected)
                if not is_connected:
                    raise DLTClientConnectivityError(
                        "Not connected to EVM RPC.",
                        self.client_type,
                        correlation_id=correlation_id,
                    )
                span.set_attribute("evm.rpc_connected", True)

                chain_id = await self._exec_w3_prop(lambda: self.w3.eth.chain_id)
                if int(chain_id) != self.chain_id:
                    msg = f"Connected to wrong chain_id. Expected {self.chain_id}, got {chain_id}."
                    span.set_status(Status(StatusCode.ERROR, description=msg))
                    self._format_log("error", msg, {"correlation_id": correlation_id})
                    raise DLTClientValidationError(
                        msg, self.client_type, correlation_id=correlation_id
                    )
                span.set_attribute("evm.chain_id_match", True)

                code = await self._exec_w3(self.w3.eth.get_code, self.contract_address)
                # get_code may return HexBytes/bytes; treat empty as not deployed
                empty_code = code in (b"", b"0x", bytes(), bytearray()) or (
                    hasattr(code, "hex") and code.hex() in ("0x", "")
                )
                if empty_code:
                    msg = f"No contract code found at {self.contract_address}."
                    span.set_status(Status(StatusCode.ERROR, description=msg))
                    self._format_log("error", msg, {"correlation_id": correlation_id})
                    raise DLTClientResourceError(
                        msg, self.client_type, correlation_id=correlation_id
                    )
                span.set_attribute("evm.contract_deployed", True)

                balance_wei = await self._exec_w3(
                    self.w3.eth.get_balance, self.account.address
                )
                balance_eth = float(self.w3.from_wei(balance_wei, "ether"))
                if balance_wei < self.w3.to_wei(self.min_eth_balance_for_tx, "ether"):
                    self._format_log(
                        "warning",
                        f"EVM wallet {self.account.address} has low balance ({balance_eth} ETH).",
                        {"correlation_id": correlation_id, "balance_eth": balance_eth},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "evm_wallet.low_balance",
                                wallet_address=self.account.address,
                                balance_eth=balance_eth,
                                min_required_eth=self.min_eth_balance_for_tx,
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                span.set_attribute("evm.wallet_balance_eth", balance_eth)

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    "EVM client is connected and contract is reachable",
                    {
                        "correlation_id": correlation_id,
                        "chain_id": int(chain_id),
                        "balance_eth": balance_eth,
                    },
                )
                return {
                    "status": True,
                    "message": "EVM client is connected and contract is reachable.",
                    "details": {"chain_id": int(chain_id), "balance_eth": balance_eth},
                }
            except DLTClientCircuitBreakerError:
                # Already tracked by CB; return structured failure
                span.set_status(
                    Status(StatusCode.ERROR, description="Circuit breaker open")
                )
                return {
                    "status": False,
                    "message": "Circuit breaker open",
                    "details": {},
                }
            except DLTClientError as e:
                span.set_status(Status(StatusCode.ERROR, description=str(e)))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM health check failed: {e}",
                    {"correlation_id": correlation_id},
                )
                return {
                    "status": False,
                    "message": f"EVM health check failed: {e}",
                    "details": {"error": str(e)},
                }
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Unexpected error: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM health check failed unexpectedly: {e}",
                    {"correlation_id": correlation_id},
                )
                return {
                    "status": False,
                    "message": f"EVM health check failed unexpectedly: {e}",
                    "details": {"error": str(e)},
                }

    async def _build_and_send_tx(
        self,
        tx_builder_method: Any,
        gas_limit: Optional[int] = None,
        gas_price_gwei: Optional[int] = None,
        max_fee_per_gas_gwei: Optional[int] = None,
        max_priority_fee_per_gas_gwei: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> str:
        """
        Build, sign, and send a transaction, waiting for confirmation.
        Returns tx hash hex string.
        """
        await self._ensure_initialized()

        with TRACER.start_as_current_span(
            f"{self.client_type}.send_transaction",
            attributes={"correlation_id": correlation_id or ""},
        ) as span:
            try:
                await self._rate_limit()
                nonce = await self._exec_w3(
                    self.w3.eth.get_transaction_count, self.account.address
                )

                tx_params: Dict[str, Any] = {
                    "chainId": self.chain_id,
                    "from": self.account.address,
                    "nonce": nonce,
                    "gas": gas_limit or self.gas_limit,
                    "value": 0,
                }

                # EIP-1559 vs Legacy Gas handling
                priority_gwei = (
                    max_priority_fee_per_gas_gwei or self.max_priority_fee_per_gas_gwei
                )
                max_fee_gwei = max_fee_per_gas_gwei or self.max_fee_per_gas_gwei

                if priority_gwei is not None and max_fee_gwei is not None:
                    # Explicit EIP-1559 settings
                    tx_params["maxPriorityFeePerGas"] = self.w3.to_wei(
                        priority_gwei, "gwei"
                    )
                    tx_params["maxFeePerGas"] = self.w3.to_wei(max_fee_gwei, "gwei")
                    self._format_log(
                        "debug",
                        "Using EIP-1559 gas parameters from config/args.",
                        {"correlation_id": correlation_id},
                    )
                else:
                    # Try to infer EIP-1559; otherwise fallback to legacy gasPrice
                    try:
                        latest_block = await self._exec_w3(
                            self.w3.eth.get_block, "latest", False
                        )
                        base_fee = getattr(latest_block, "baseFeePerGas", None)
                        if base_fee is not None:
                            # Compute fee with multiplier and priority
                            use_priority_gwei = (
                                priority_gwei if priority_gwei is not None else 2
                            )
                            priority_wei = self.w3.to_wei(use_priority_gwei, "gwei")
                            max_fee = int(base_fee * self.base_fee_multiplier) + int(
                                priority_wei
                            )
                            tx_params["maxPriorityFeePerGas"] = priority_wei
                            tx_params["maxFeePerGas"] = max_fee
                            self._format_log(
                                "debug",
                                f"EIP-1559: baseFee={self.w3.from_wei(base_fee, 'gwei')} Gwei, priority={use_priority_gwei} Gwei, maxFee={self.w3.from_wei(max_fee, 'gwei')} Gwei",
                                {"correlation_id": correlation_id},
                            )
                        else:
                            # Legacy fallback
                            gas_price = None
                            try:
                                gas_price = await self._exec_w3_prop(
                                    lambda: self.w3.eth.gas_price
                                )
                            except Exception:
                                pass
                            if gas_price is None:
                                if EVM_METRICS:
                                    EVM_METRICS["gas_fallback_total"].labels(
                                        client_type=self.client_type
                                    ).inc()
                                gas_price = self.w3.to_wei(
                                    self.fallback_gas_price_gwei, "gwei"
                                )
                            tx_params["gasPrice"] = gas_price
                            self._format_log(
                                "debug",
                                f"Using legacy gasPrice: {self.w3.from_wei(tx_params['gasPrice'], 'gwei')} Gwei",
                                {"correlation_id": correlation_id},
                            )
                    except Exception as fee_e:
                        # Fee estimation failed, fallback to legacy with configured fallback
                        if EVM_METRICS:
                            EVM_METRICS["gas_fallback_total"].labels(
                                client_type=self.client_type
                            ).inc()
                        tx_params["gasPrice"] = self.w3.to_wei(
                            self.fallback_gas_price_gwei, "gwei"
                        )
                        self._format_log(
                            "warning",
                            f"Failed to estimate EIP-1559 fees; using fallback gasPrice. Reason: {fee_e}",
                            {"correlation_id": correlation_id},
                        )

                # Build transaction (sync)
                transaction = await self._exec_w3(
                    tx_builder_method.build_transaction, tx_params
                )

                # Sign transaction (sync)
                signed_tx = await self._circuit_breaker.execute(
                    lambda: self._run_blocking_in_executor(
                        self.w3.eth.account.sign_transaction,
                        transaction,
                        self.private_key,
                    )
                )

                # Audit (JSON-safe): log unsigned hash-ish marker and signer address
                try:
                    # Fix: Handle potential TypeError from scrub_secrets
                    try:
                        safe_tx_params = scrub_secrets(
                            {
                                k: (str(v) if isinstance(v, bytes) else v)
                                for k, v in transaction.items()
                            }
                        )
                    except TypeError:
                        safe_tx_params = self._safe_copy_dict(
                            {
                                k: (str(v) if isinstance(v, bytes) else v)
                                for k, v in transaction.items()
                            }
                        )

                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_tx.signed",
                            tx_params=safe_tx_params,
                            signer_address=self.account.address,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass

                # Send raw transaction (sync)
                tx_hash_hex = await self._circuit_breaker.execute(
                    lambda: self._run_blocking_in_executor(
                        lambda: self.w3.to_hex(
                            self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                        )
                    )
                )
                self._format_log(
                    "info",
                    f"Transaction sent: {tx_hash_hex}",
                    {"tx_hash": tx_hash_hex, "correlation_id": correlation_id},
                )
                span.set_attribute("tx.hash", tx_hash_hex)

                # Wait for receipt (sync)
                try:
                    receipt = await self._exec_w3(
                        self.w3.eth.wait_for_transaction_receipt,
                        tx_hash_hex,
                        self.tx_confirm_timeout,
                    )
                except asyncio.TimeoutError as e:
                    if EVM_METRICS:
                        EVM_METRICS["tx_pending_timeout"].labels(
                            client_type=self.client_type
                        ).inc()
                    self._format_log(
                        "error",
                        f"Transaction confirmation timed out: {e}",
                        {"correlation_id": correlation_id},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "evm_tx.confirmation_timeout",
                                tx_hash=tx_hash_hex,
                                error_message=str(e),
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

                # Verify status
                receipt_status = int(getattr(receipt, "status", 0))
                if receipt_status == 0:
                    self._format_log(
                        "error",
                        f"Transaction failed on-chain. Receipt block={getattr(receipt, 'blockNumber', None)}",
                        {"correlation_id": correlation_id},
                    )
                    try:
                        asyncio.get_running_loop().create_task(
                            AUDIT.log_event(
                                "evm_tx.failed_on_chain",
                                tx_hash=tx_hash_hex,
                                receipt_block_number=int(
                                    getattr(receipt, "blockNumber", 0) or 0
                                ),
                                correlation_id=correlation_id,
                            )
                        )
                    except RuntimeError:
                        pass
                    raise DLTClientTransactionError(
                        "Transaction failed on-chain.",
                        self.client_type,
                        details={},
                        correlation_id=correlation_id,
                    )

                # Success
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Transaction confirmed: {tx_hash_hex}",
                    {"tx_hash": tx_hash_hex, "correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_tx.confirmed",
                            tx_hash=tx_hash_hex,
                            receipt_block_number=int(
                                getattr(receipt, "blockNumber", 0) or 0
                            ),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass

                return tx_hash_hex

            except (TransactionNotFound, ContractCustomError, ContractLogicError) as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Transaction error: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM contract/transaction error: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_tx.contract_error",
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"EVM contract/transaction error: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Transaction failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Failed to send EVM transaction: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_tx.send_failure",
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"Failed to send EVM transaction: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientAuthError,
            DLTClientTransactionError,
            DLTClientTimeoutError,
            DLTClientCircuitBreakerError,
        )
    )
    async def write_checkpoint(
        self,
        checkpoint_name: str,
        hash: str,
        prev_hash: str,
        metadata: Dict[str, Any],
        payload_blob: bytes,
        correlation_id: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """
        Writes a checkpoint to the EVM smart contract.
        Returns (tx_hash, off_chain_id, version).
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.write_checkpoint",
            attributes={
                "checkpoint_name": checkpoint_name,
                "hash": hash,
                "correlation_id": correlation_id or "",
                "payload_size": len(payload_blob),
            },
        ) as span:
            try:
                off_chain_id = await self._circuit_breaker.execute(
                    lambda: self.off_chain_client.save_blob(
                        checkpoint_name, payload_blob, correlation_id=correlation_id
                    )
                )
                span.set_attribute("off_chain.id", off_chain_id)

                hash_bytes = self.w3.to_bytes(hexstr=hash) if hash else b"\x00" * 32
                prev_hash_bytes = (
                    self.w3.to_bytes(hexstr=prev_hash) if prev_hash else b"\x00" * 32
                )

                tx_builder = self.contract.functions.writeCheckpoint(
                    checkpoint_name,
                    hash_bytes,
                    prev_hash_bytes,
                    json.dumps(metadata),
                    off_chain_id,
                )

                tx_hash = await self._build_and_send_tx(
                    tx_builder, correlation_id=correlation_id
                )

                # Derive version from block number of receipt (read receipt again; or use best-effort get_transaction_receipt)
                receipt = await self._exec_w3(
                    self.w3.eth.get_transaction_receipt, tx_hash
                )
                version = int(getattr(receipt, "blockNumber", 0) or 0)

                span.set_attribute("tx_hash", tx_hash)
                span.set_attribute("version", version)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"EVM checkpoint written: {checkpoint_name} [tx_hash={tx_hash}, version={version}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_hash": tx_hash,
                        "version": version,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.written",
                            checkpoint_name=checkpoint_name,
                            tx_hash=tx_hash,
                            hash=hash,
                            prev_hash=prev_hash,
                            off_chain_id=off_chain_id,
                            version=version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return tx_hash, off_chain_id, version
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"EVM write failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM write_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.write_failure",
                            checkpoint_name=checkpoint_name,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"EVM write_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientQueryError,
            FileNotFoundError,
            DLTClientTimeoutError,
            DLTClientCircuitBreakerError,
        )
    )
    async def read_checkpoint(
        self,
        name: str,
        version: Optional[Union[int, str]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Reads a checkpoint from the EVM smart contract.
        Returns dict with metadata, payload_blob, tx_id.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.read_checkpoint",
            attributes={
                "checkpoint_name": name,
                "version": str(version or ""),
                "correlation_id": correlation_id or "",
            },
        ) as span:
            try:
                await self._rate_limit()
                if version is None or version == "latest":
                    entry_tuple = await self._exec_w3(
                        self.contract.functions.getLatestCheckpoint(name).call
                    )
                    retrieved_version = (
                        int(entry_tuple[4])
                        if len(entry_tuple) > 4 and entry_tuple[4] is not None
                        else None
                    )
                elif isinstance(version, int):
                    entry_tuple = await self._exec_w3(
                        self.contract.functions.readCheckpoint(name, version).call
                    )
                    retrieved_version = int(version)
                else:
                    raise DLTClientValidationError(
                        "Version must be int or 'latest'.",
                        self.client_type,
                        correlation_id=correlation_id,
                    )

                # entry_tuple layout: (hash_bytes, prev_hash_bytes, metadata_json_str, off_chain_ref, version?)
                try:
                    metadata_json = json.loads(entry_tuple[2])
                except Exception:
                    metadata_json = {}

                entry = {
                    "hash": self.w3.to_hex(entry_tuple[0]),
                    "prev_hash": self.w3.to_hex(entry_tuple[1]),
                    "metadata": metadata_json,
                    "off_chain_ref": entry_tuple[3],
                    "version": retrieved_version,
                    "tx_id": None,  # not available from read calls
                }
                span.set_attribute("dlt.entry_hash", entry.get("hash"))

                off_chain_id = entry["off_chain_ref"]
                payload_blob = await self._circuit_breaker.execute(
                    lambda: self.off_chain_client.get_blob(
                        off_chain_id, correlation_id=correlation_id
                    )
                )

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"EVM checkpoint read: {name} v{retrieved_version}",
                    {"correlation_id": correlation_id, "version": retrieved_version},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.read",
                            checkpoint_name=name,
                            version=retrieved_version,
                            hash=entry.get("hash"),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return {
                    "metadata": entry,
                    "payload_blob": payload_blob,
                    "tx_id": entry.get("tx_id"),
                }
            except FileNotFoundError:
                span.set_status(
                    Status(StatusCode.ERROR, description="Off-chain blob not found")
                )
                self._format_log(
                    "error",
                    f"Off-chain blob not found: {name} v{version}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.read_failure",
                            checkpoint_name=name,
                            version=version,
                            error_message="Off-chain blob not found",
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"EVM read failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM read_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.read_failure",
                            checkpoint_name=name,
                            version=version,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientQueryError(
                    f"EVM read_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def get_version_tx(
        self, name: str, version: int, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves a specific version's metadata and payload reference from EVM.
        """
        return await self.read_checkpoint(name, version, correlation_id=correlation_id)

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientAuthError,
            DLTClientTransactionError,
            DLTClientTimeoutError,
            DLTClientCircuitBreakerError,
        )
    )
    async def _rotate_credentials(
        self, new_private_key_hex: str, correlation_id: Optional[str] = None
    ) -> None:
        """
        Rotates EVM private key at runtime. Intended for use by a rotation controller.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.rotate_credentials",
            attributes={"correlation_id": correlation_id or ""},
        ) as span:
            try:
                new_account = Account.from_key(new_private_key_hex)
                self.private_key = new_private_key_hex
                self.account = new_account
                self.w3.eth.default_account = self.account.address
                span.set_attribute("new_wallet_address", self.account.address)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"EVM private key updated. New address: {self.account.address}",
                    {
                        "correlation_id": correlation_id,
                        "new_wallet_address": self.account.address,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_credentials.rotated",
                            new_wallet_address=self.account.address,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
            except Exception as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description=f"EVM credential rotation failed: {e}",
                    )
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM credential rotation failed: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_credentials.rotation_failure",
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientAuthError(
                    f"Failed to rotate EVM private key: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientAuthError,
            DLTClientTransactionError,
            DLTClientTimeoutError,
            DLTClientCircuitBreakerError,
        )
    )
    async def rollback_checkpoint(
        self, name: str, rollback_hash: str, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs a logical rollback by creating a new checkpoint pointing to an older hash.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.rollback_checkpoint",
            attributes={
                "checkpoint_name": name,
                "rollback_hash": rollback_hash,
                "correlation_id": correlation_id or "",
            },
        ) as span:
            try:
                entry_tuple = await self._exec_w3(
                    self.contract.functions.getCheckpointByHash(
                        self.w3.to_bytes(hexstr=rollback_hash)
                    ).call
                )
                entry_to_rollback_to = {
                    "hash": self.w3.to_hex(entry_tuple[0]),
                    "prev_hash": self.w3.to_hex(entry_tuple[1]),
                    "metadata": json.loads(entry_tuple[2]) if entry_tuple[2] else {},
                    "off_chain_ref": entry_tuple[3],
                    "version": (
                        int(entry_tuple[4])
                        if len(entry_tuple) > 4 and entry_tuple[4] is not None
                        else None
                    ),
                }

                tx_builder = self.contract.functions.rollbackCheckpoint(
                    name, self.w3.to_bytes(hexstr=rollback_hash)
                )
                tx_hash = await self._build_and_send_tx(
                    tx_builder, correlation_id=correlation_id
                )

                receipt = await self._exec_w3(
                    self.w3.eth.get_transaction_receipt, tx_hash
                )
                new_version = int(getattr(receipt, "blockNumber", 0) or 0)

                span.set_attribute("tx_hash", tx_hash)
                span.set_attribute("new_version", new_version)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"EVM checkpoint rolled back: {name} to hash {rollback_hash} [tx_hash={tx_hash}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_hash,
                        "new_version": new_version,
                    },
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.rolled_back",
                            checkpoint_name=name,
                            rollback_hash=rollback_hash,
                            tx_hash=tx_hash,
                            new_version=new_version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                return {
                    "metadata": entry_to_rollback_to,
                    "tx_id": tx_hash,
                    "version": new_version,
                }
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"EVM rollback failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"EVM rollback_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "evm_checkpoint.rollback_failure",
                            checkpoint_name=name,
                            rollback_hash=rollback_hash,
                            error_message=str(e),
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass
                raise DLTClientTransactionError(
                    f"EVM rollback_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        """
        Best-effort provider cleanup (HTTPProvider may not expose a close, but guard if present).
        """
        await super().close()
        provider = getattr(self, "w3", None)
        provider = getattr(provider, "provider", None)
        if provider and hasattr(provider, "close"):
            self._format_log(
                "info",
                f"{self.client_type} web3.py provider closing",
                {"client_type": self.client_type},
            )
            try:
                if ASYNC_TIMEOUT_AVAILABLE:
                    try:
                        async with async_timeout.timeout(
                            self.client_config["close_timeout"]
                        ):
                            res = provider.close()
                            if inspect.isawaitable(res):
                                await res
                    except TypeError:
                        res = provider.close()
                        if inspect.isawaitable(res):
                            await res
                else:
                    res = provider.close()
                    if inspect.isawaitable(res):
                        await res
                self._format_log(
                    "info",
                    f"{self.client_type} web3.py provider closed",
                    {"client_type": self.client_type},
                )
            except Exception as e:
                self._format_log(
                    "warning",
                    f"Failed to close web3.py provider cleanly: {e}",
                    {"client_type": self.client_type},
                )

    def __del__(self) -> None:
        """
        Best-effort cleanup if GC'd; skip if no running loop.
        """
        provider = getattr(self, "w3", None)
        provider = getattr(provider, "provider", None)
        if provider and hasattr(provider, "close"):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.close())
            except (RuntimeError, asyncio.exceptions.InvalidStateError):
                # No running event loop; skip
                pass


# --- Plugin System Integration ---
PLUGIN_MANIFEST = {
    "name": "ethereum_client",
    "version": "1.0.0",
    "description": "Ethereum/EVM DLT client using web3.py",
    "type": "dlt_client",
    "capabilities": ["dlt_operations"],
    "entry_points": ["register_plugin_entrypoints"],
    "dependencies": ["dlt_base"],
}


def register_plugin_entrypoints(register_func: Callable):
    """Register Ethereum client plugin entry points with the plugin manager."""
    register_func(
        name="ethereum_client_create",
        executor_func=lambda config, off_chain_client, **kwargs: EthereumClientWrapper(
            config, off_chain_client
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="ethereum_client_health_check",
        executor_func=lambda client, **kwargs: client.health_check(**kwargs),
        capabilities=["dlt_operations"],
    )


def create_ethereum_client(
    config: Dict[str, Any], off_chain_client: "BaseOffChainClient"
) -> EthereumClientWrapper:
    """
    Factory function to create a new EVM client instance with the given configuration.

    Args:
        config: Configuration dictionary for the EVM client
        off_chain_client: An initialized off-chain storage client

    Returns:
        An initialized EthereumClientWrapper instance
    """
    return EthereumClientWrapper(config, off_chain_client)


# --- Plugin Manager Registration ---
try:
    from ..plugin_manager import PluginManager

    # Auto-register with plugin manager if available
    def _register_with_plugin_manager():
        try:
            plugin_manager = PluginManager.get_instance()
            plugin_manager.register_plugin(
                name="ethereum_client",
                module=sys.modules[__name__],
                manifest=PLUGIN_MANIFEST,
            )
            _base_logger.info("Ethereum/EVM DLT client registered with plugin manager.")
        except Exception as e:
            _base_logger.warning(
                f"Could not auto-register Ethereum/EVM DLT client with plugin manager: {e}"
            )

    # Only register in production mode
    if PRODUCTION_MODE:
        _register_with_plugin_manager()
except ImportError:
    _base_logger.debug(
        "Plugin manager not available, skipping auto-registration of Ethereum/EVM client."
    )
