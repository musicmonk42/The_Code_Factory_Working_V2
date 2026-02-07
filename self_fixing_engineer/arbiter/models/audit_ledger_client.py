# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Final, List, Optional, Set, Type, Union

# Import tenacity for retries with exponential backoff
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

# requirements.txt (create separately):
# web3==7.13.0
# eth-account==0.13.3
# tenacity==8.5.0
# prometheus-client==0.21.0
# opentelemetry-sdk==1.27.0
# opentelemetry-exporter-otlp==1.27.0
# pydantic==2.9.0  # Latest for 2025, with Rust core
# boto3==1.35.0  # For AWS Secrets Manager
# pytest-asyncio==0.24.0  # For testing
# sentry-sdk==2.11.0 # For error reporting
# gnosis-py==4.1.0 # For multi-sig
# postgres_client.py # Assumed to be available from SFE codebase
# fabric_sdk_py==0.9.0 # Optional for Hyperledger Fabric


# Web3.py for Ethereum/EVM integration
try:
    # Use AsyncWebsocketProvider for real-time updates and more efficient polling
    from eth_account import Account
    from eth_utils import to_checksum_address
    from web3 import AsyncWeb3
    from web3.exceptions import (
        ContractCustomError,
        ContractLogicError,
        TimeExhausted,
        TransactionNotFound,
    )

    # In web3 v7+, geth_poa_middleware was renamed to ExtraDataToPOAMiddleware
    try:
        from web3.middleware import ExtraDataToPOAMiddleware as geth_poa_middleware
    except ImportError:
        from web3.middleware import geth_poa_middleware
    # In web3 v7+, AsyncWebsocketProvider was renamed/moved to WebSocketProvider
    try:
        from web3.providers import WebSocketProvider as AsyncWebsocketProvider
    except ImportError:
        from web3.providers.async_websocket import AsyncWebsocketProvider

    ETHEREUM_AVAILABLE: Final[bool] = True
    Account.enable_unaudited_hdwallet_features()
except ImportError:
    logging.getLogger(__name__).info(
        "web3.py library not found. Ethereum DLT integration will operate in mock mode."
    )
    ETHEREUM_AVAILABLE: Final[bool] = False

    class AsyncWeb3:
        def __init__(self, *args, **kwargs):
            raise ImportError("web3.py is not installed.")

    class AsyncWebsocketProvider:
        def __init__(self, *args, **kwargs):
            raise ImportError("web3.py is not installed.")

    def geth_poa_middleware(w3: Any) -> Any:
        raise ImportError("web3.py is not installed.")

    class TransactionNotFound(Exception):
        pass

    class ContractCustomError(Exception):
        pass

    class ContractLogicError(Exception):
        pass

    class TimeExhausted(Exception):
        pass

    class Account:
        @staticmethod
        def create() -> Any:
            raise ImportError()


# Import gnosis-py for multi-sig support (optional)
try:
    from gnosis.safe import Safe, SafeOperation

    MULTI_SIG_AVAILABLE: Final[bool] = True
except (ImportError, AttributeError) as e:
    # AttributeError catches the gnosis package bug with string.join
    MULTI_SIG_AVAILABLE: Final[bool] = False
    logging.getLogger(__name__).debug(
        f"gnosis-py library not available ({type(e).__name__}); multi-sig support disabled."
    )

# Import postgres_client for off-chain redaction flagging
try:
    from postgres_client import PostgresClient

    POSTGRES_CLIENT_AVAILABLE: Final[bool] = True
except ImportError:
    POSTGRES_CLIENT_AVAILABLE: Final[bool] = False
    logging.getLogger(__name__).debug(
        "postgres_client.py not found; redaction flagging disabled."
    )


# Import OmniCore's ExplainAudit with a mock fallback for dependency resilience
try:
    from omnicore_engine.audit import ExplainAudit
    _EXPLAIN_AUDIT_REAL = True
except ImportError:
    logging.getLogger(__name__).debug(
        "omnicore_engine.audit.ExplainAudit not found; using mock implementation."
    )
    _EXPLAIN_AUDIT_REAL = False

    class ExplainAudit:  # type: ignore
        """Mock class for ExplainAudit if the library is not installed."""

        def __init__(self, **kwargs):
            # Accept any keyword arguments for compatibility
            pass


# Hyperledger Fabric SDK (conceptual import - will raise NotImplementedError)
try:
    from fabric_sdk_py import FabricClient

    HYPERLEDGER_FABRIC_AVAILABLE: Final[bool] = True
except ImportError:
    HYPERLEDGER_FABRIC_AVAILABLE: Final[bool] = False
if not HYPERLEDGER_FABRIC_AVAILABLE:
    logging.getLogger(__name__).debug(
        "Hyperledger Fabric SDK not supported; operations will raise NotImplementedError."
    )

# AWS SDK for Secrets Manager
import boto3

# Sentry for Error Reporting
import sentry_sdk
from self_fixing_engineer.arbiter.otel_config import get_tracer
from botocore.exceptions import ClientError

# OpenTelemetry Tracing - Use centralized configuration
from opentelemetry.trace import Status, StatusCode

# Prometheus Metrics
from prometheus_client import REGISTRY, Counter, Gauge, Histogram

# Pydantic for input validation
from pydantic import (
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# Logger initialization
logger = logging.getLogger(__name__)

# Get tracer from centralized configuration
tracer = get_tracer(__name__)


# Helper function to get or create a metric (idempotent)
def _get_or_create_metric(
    metric_class: type[Counter] | type[Gauge] | type[Histogram],
    name: str,
    documentation: str,
    labelnames: list[str],
) -> Counter | Gauge | Histogram:
    """Idempotently create or retrieve a Prometheus metric."""
    if name in REGISTRY._names_to_collectors:
        existing_metric = REGISTRY._names_to_collectors[name]
        if not isinstance(existing_metric, metric_class):
            logger.warning(
                f"Metric '{name}' already registered with type "
                f"{type(existing_metric).__name__}, but requested as "
                f"{metric_class.__name__}. Reusing existing."
            )
        return existing_metric
    return metric_class(name, documentation, labelnames)


# Ensure metrics are registered only once using REGISTRY check
DLT_CALLS_TOTAL: Final[Counter] = _get_or_create_metric(
    Counter,
    "dlt_calls_total",
    "Total DLT API calls",
    ["dlt_type", "operation", "status", "env", "cluster"],
)
DLT_CALLS_ERRORS: Final[Counter] = _get_or_create_metric(
    Counter,
    "dlt_calls_errors",
    "DLT API call errors",
    ["dlt_type", "operation", "error_type", "env", "cluster"],
)
DLT_CALL_LATENCY_SECONDS: Final[Histogram] = _get_or_create_metric(
    Histogram,
    "dlt_call_latency_seconds",
    "DLT API call latency in seconds",
    ["dlt_type", "operation", "env", "cluster"],
)
DLT_TRANSACTIONS_PENDING: Final[Gauge] = _get_or_create_metric(
    Gauge,
    "dlt_transactions_pending",
    "Number of DLT transactions pending confirmation",
    ["dlt_type", "env", "cluster"],
)
DLT_GAS_USED: Final[Histogram] = _get_or_create_metric(
    Histogram,
    "dlt_gas_used",
    "Gas used per successful transaction",
    ["dlt_type", "operation", "env", "cluster"],
)
DLT_REVERT_REASON: Final[Counter] = _get_or_create_metric(
    Counter,
    "dlt_revert_reason",
    "Transaction revert reasons",
    ["dlt_type", "reason", "env", "cluster"],
)


# Richer Exception Types
class DLTError(Exception):
    """Base class for all DLT-related errors."""

    pass


class DLTConnectionError(DLTError):
    """Custom exception for DLT connection failures."""

    pass


class DLTContractError(DLTError):
    """Custom exception for smart contract interaction failures."""

    pass


class DLTTransactionError(DLTError):
    """Custom exception for DLT transaction failures."""

    pass


class DLTUnsupportedError(DLTError):
    """Custom exception for unsupported DLT types or operations."""

    pass


class SecretScrubber(logging.Filter):
    """A logging filter to redact sensitive information."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact dictionary values
        if isinstance(record.args, dict):
            record.args = {
                k: ("<redacted>" if "key" in str(k).lower() else v)
                for k, v in record.args.items()
            }
        # Redact hex strings that look like private keys
        record.msg = re.sub(r"0x[a-fA-F0-9]{64}", "<redacted_key>", str(record.msg))
        return True


# Pydantic model for validating audit event data before sending to DLT
class AuditEvent(BaseModel):
    """
    Represents a single audit event with validation rules.
    """

    event_type: Annotated[
        str, StringConstraints(pattern=r"^[a-zA-Z0-9:_.-]+$", max_length=50)
    ] = Field(
        ...,
        description="Event category, e.g., 'agent:code_update' or 'system:config.change'",
    )
    details: Dict[str, Any] = Field(
        ...,
        description="Event payload (details). The JSON representation should not exceed 10KB.",
    )
    operator: Annotated[str, StringConstraints(max_length=50)] = Field(
        default="system", description="Identifier of the entity performing the action."
    )
    correlation_id: Optional[str] = Field(
        default=None,
        max_length=36,
        description="A unique ID (e.g., UUID) to link related events.",
    )

    @field_validator("details")
    @classmethod
    def validate_details_size(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        """Validates that the JSON-serialized details payload is within size limits."""
        # Canonicalize JSON for deterministic size calculation
        if (
            len(json.dumps(v, sort_keys=True, default=str, separators=(",", ":")))
            > 10240
        ):  # 10 KB limit
            raise ValueError("Details JSON payload exceeds the 10KB size limit.")
        return v

    @model_validator(mode="before")
    def hash_pii(cls, data: Any) -> Any:
        if (
            isinstance(data, dict)
            and "details" in data
            and "user_id" in data["details"]
        ):
            details = data["details"]
            details["user_id_hash"] = hashlib.sha256(
                str(details["user_id"]).encode()
            ).hexdigest()
            del details["user_id"]
            data["details"] = details
        return data


class AuditLedgerClient:
    """
    A client for DLT-based audit logging. Supports different DLT types
    (Ethereum/EVM). Hyperledger Fabric is not supported.

    Integrates with Web3.py for Ethereum and provides observability through
    Prometheus metrics and OpenTelemetry tracing. This version uses native
    asyncio support, EIP-1559 transactions, and production-ready features like
    secrets management, input validation, and reorg resilience. Tested on Python 3.12.3+.
    For Hyperledger, see stub implementation.
    """

    def __init__(
        self,
        dlt_type: str = "ethereum",
        extra_metric_labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Initializes the AuditLedgerClient.

        Configuration values (audit_ledger_url, private_key, contract_address, contract_abi)
        are loaded from environment variables or a secrets manager.

        Args:
            dlt_type (str): Type of DLT to use ('ethereum').
            extra_metric_labels (Optional[Dict[str, str]]): Additional labels for Prometheus metrics (e.g., {'env': 'prod'}).
        """
        self.dlt_type = dlt_type.lower()
        self.metric_labels: Dict[str, str] = {
            "env": os.getenv("APP_ENV", "development"),
            "cluster": os.getenv("CLUSTER_NAME", "default"),
            **(extra_metric_labels or {}),
        }

        # Load configuration from environment variables
        self.audit_ledger_url = os.getenv("AUDIT_LEDGER_URL")
        self.private_key = None  # Will be loaded on connect()
        self.contract_address = os.getenv("ETHEREUM_CONTRACT_ADDRESS")
        self.safe_address = os.getenv("ETHEREUM_SAFE_ADDRESS")

        abi_json_str = os.getenv("ETHEREUM_CONTRACT_ABI_JSON")
        self.contract_abi: Optional[List[Dict[str, Any]]] = None
        if abi_json_str:
            try:
                self.contract_abi = json.loads(abi_json_str)
            except json.JSONDecodeError as e:
                logger.error(
                    f"Failed to parse ETHEREUM_CONTRACT_ABI_JSON from environment variable: {e}. Contract ABI will be empty.",
                    exc_info=True,
                )

        self.poa_middleware: Final[bool] = (
            os.getenv("ETHEREUM_POA_MIDDLEWARE", "false").lower() == "true"
        )
        self.gas_cap_gwei: Final[int] = int(
            os.getenv("MAX_GAS_GWEI", "200")
        )  # Env-configurable gas price cap
        self.confirmations: Final[int] = int(
            os.getenv("CONFIRMATIONS", "12")
        )  # Blocks to wait for reorg safety
        self.tx_timeout_sec: Final[int] = int(os.getenv("TX_TIMEOUT_SEC", "180"))
        self.block_poll_interval_sec: Final[float] = float(
            os.getenv("BLOCK_POLL_INTERVAL_SEC", "1.0")
        )
        self.max_parallel_tx: Final[int] = int(os.getenv("MAX_PARALLEL_TX", "1"))
        self.default_gas_limit: Final[int] = int(
            os.getenv("DEFAULT_GAS_LIMIT", "300000")
        )
        self.base_fee_gwei_cap: Final[int] = int(os.getenv("BASE_FEE_GWEI_CAP", "500"))
        self.enable_multisig: Final[bool] = (
            os.getenv("ENABLE_GNOSIS_SAFE", "false").lower() == "true"
        )
        self.details_field: Final[str] = os.getenv(
            "CONTRACT_DETAILS_FIELD", "detailsJson"
        )

        self.tracer = tracer

        # In-memory LRU idempotency cache
        self._recent_ids: OrderedDict[str, float] = OrderedDict()
        self._recent_ids_max: Final[int] = int(os.getenv("IDEMP_CACHE_MAX", "5000"))
        self._idempotency_lock = asyncio.Lock()

        # Validate essential configurations for Ethereum
        if self.dlt_type == "ethereum":
            if not (self.audit_ledger_url or "").startswith(("ws://", "wss://")):
                raise ValueError(
                    "Environment variable AUDIT_LEDGER_URL must be a WebSocket URL (ws:// or wss://) for Ethereum DLT."
                )
            if not self.contract_address:
                raise ValueError(
                    "Environment variable ETHEREUM_CONTRACT_ADDRESS must be set for Ethereum DLT."
                )
            # Only require valid ABI if one was provided and successfully parsed
            # Don't fail if JSON parsing failed - that's already logged as an error
            if abi_json_str and not self.contract_abi:
                # JSON was provided but failed to parse - this is a warning, not a fatal error in dev
                if self.metric_labels["env"] == "production":
                    raise ValueError(
                        "Environment variable ETHEREUM_CONTRACT_ABI_JSON must be valid JSON for Ethereum DLT in production."
                    )
                else:
                    logger.warning(
                        "Contract ABI failed to parse. Some functionality may be limited."
                    )
            elif not abi_json_str:
                # No ABI provided at all
                raise ValueError(
                    "Environment variable ETHEREUM_CONTRACT_ABI_JSON must be set for Ethereum DLT."
                )

        # Enforce Secrets Manager in production
        if (
            self.metric_labels["env"] != "development"
            and os.getenv("USE_SECRETS_MANAGER", "false").lower() != "true"
        ):
            raise DLTError(
                "Secrets Manager must be enabled in production for private key security."
            )

        self.web3: Optional[AsyncWeb3] = None
        self.contract = None
        self.account = None
        self._is_connected = False  # Internal state for connection status
        self.semaphore = asyncio.Semaphore(
            self.max_parallel_tx
        )  # Limit concurrent transactions

        # Initialize OmniCore's ExplainAudit client
        # Real ExplainAudit takes system_audit_merkle_tree, mock takes anything
        if _EXPLAIN_AUDIT_REAL:
            self.explain_audit = ExplainAudit()
        else:
            self.explain_audit = ExplainAudit(dlt_client=self)

        logger.info(
            f"AuditLedgerClient initialized for DLT type: {self.dlt_type}, URL: {self.audit_ledger_url}, Metrics Labels: {self.metric_labels}"
        )
        if not any(isinstance(f, SecretScrubber) for f in logger.filters):
            logger.addFilter(SecretScrubber())

    def _get_private_key(self) -> Optional[str]:
        """
        Retrieves the Ethereum private key, prioritizing AWS Secrets Manager over environment variables.

        Security Note: For production, it is highly recommended to implement key rotation.
        For example, rotate the secret stored in ETHEREUM_PRIVATE_KEY_SECRET_NAME via an
        automated AWS Lambda function on a schedule (e.g., every 90 days).
        """
        if os.getenv("USE_SECRETS_MANAGER", "false").lower() == "true":
            secret_name = os.getenv(
                "ETHEREUM_PRIVATE_KEY_SECRET_NAME", "ethereum/audit_private_key"
            )
            region_name = os.getenv("AWS_REGION", "us-east-1")
            logger.info(
                f"Attempting to retrieve private key from AWS Secrets Manager (secret: {secret_name}, region: {region_name})."
            )
            try:
                session = boto3.session.Session()
                client = session.client(
                    service_name="secretsmanager", region_name=region_name
                )
                get_secret_value_response = client.get_secret_value(
                    SecretId=secret_name
                )
                secret = get_secret_value_response.get("SecretString")

                if not secret:
                    raise ValueError(
                        "SecretString is empty in AWS Secrets Manager response."
                    )

                # Try to parse JSON to handle different secret formats
                try:
                    maybe_json = json.loads(secret)
                    secret = maybe_json.get("private_key") or maybe_json.get(
                        "ETHEREUM_PRIVATE_KEY"
                    )
                    if not secret:
                        raise ValueError(
                            "Neither 'private_key' nor 'ETHEREUM_PRIVATE_KEY' found in secret JSON"
                        )
                except json.JSONDecodeError:
                    # Treat as plain string
                    pass

                logger.info(
                    "Successfully retrieved private key from AWS Secrets Manager."
                )
                return secret
            except ClientError as e:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("secret_name", secret_name)
                    sentry_sdk.capture_exception(e)
                logger.error(
                    f"Failed to retrieve private key from AWS Secrets Manager: {e}",
                    exc_info=True,
                )
                raise ValueError(
                    f"Failed to retrieve private key from AWS Secrets Manager: {e}"
                ) from e

        # Fallback to environment variable for local development
        private_key = os.getenv("ETHEREUM_PRIVATE_KEY")
        if private_key:
            logger.warning(
                "Using ETHEREUM_PRIVATE_KEY environment variable. This is not recommended for production. Use a secrets manager."
            )
        return private_key

    def rotate_private_key(self) -> None:
        """
        Rotates the Ethereum private key by generating a new one and updating Secrets Manager.
        For env fallback, logs a warning as rotation isn't automated.
        Call this via a scheduled task (e.g., AWS Lambda every 90 days).
        """
        if os.getenv("USE_SECRETS_MANAGER", "false").lower() != "true":
            logger.warning(
                "Key rotation not supported for env var fallback. Use Secrets Manager for production."
            )
            return
        secret_name = os.getenv(
            "ETHEREUM_PRIVATE_KEY_SECRET_NAME", "ethereum/audit_private_key"
        )
        region_name = os.getenv("AWS_REGION", "us-east-1")
        try:
            # Generate new key using eth-account for offline generation
            new_key = Account.create().key.hex()
            session = boto3.session.Session()
            client = session.client(
                service_name="secretsmanager", region_name=region_name
            )
            # Update the secret value
            client.put_secret_value(SecretId=secret_name, SecretString=new_key)
            self.private_key = new_key
            if self.account:
                self.account = Account.from_key(new_key)
            logger.info("Private key rotated successfully in Secrets Manager.")
        except ClientError as e:
            logger.error(f"Failed to rotate private key: {e}", exc_info=True)
            raise DLTError(f"Key rotation failed: {e}") from e

    async def __aenter__(self) -> "AuditLedgerClient":
        """Async context manager entry point: connects to the DLT."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> Optional[bool]:
        """Async context manager exit point: disconnects from the DLT."""
        await self.disconnect()
        return None  # Returning None means "do not suppress exception"

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1, max=10),
        retry=retry_if_exception_type((ConnectionError, DLTConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def connect(self) -> None:
        """
        Establishes connection to the DLT and initializes necessary components
        (e.g., AsyncWeb3 instance, contract, account). Includes retries.

        Raises:
            DLTConnectionError: If connection fails or required configurations are missing.
            DLTContractError: If the configured contract ABI is missing required functions.
            ImportError: If a required DLT library is not installed.
            ValueError: If configuration is invalid.
        """
        if self._is_connected:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"{self.dlt_type} client already connected.")
            return

        with self.tracer.start_as_current_span(f"{self.dlt_type}_dlt_connect") as span:
            span.set_attribute("dlt.type", self.dlt_type)
            start_time = time.monotonic()
            DLT_CALLS_TOTAL.labels(
                dlt_type=self.dlt_type,
                operation="connect",
                status="attempt",
                **self.metric_labels,
            ).inc()
            try:
                if self.dlt_type == "ethereum":
                    if not ETHEREUM_AVAILABLE:
                        raise ImportError(
                            "web3.py is not installed. Cannot connect to Ethereum DLT."
                        )

                    if self.private_key is None:
                        self.private_key = self._get_private_key()
                    if not self.private_key:
                        raise ValueError(
                            "Private key not found. Check environment or secrets manager configuration."
                        )

                    self.web3 = AsyncWeb3(AsyncWebsocketProvider(self.audit_ledger_url))

                    if self.poa_middleware:
                        self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)

                    if not await self.web3.is_connected():
                        raise DLTConnectionError(
                            f"Failed to connect to Ethereum node at {self.audit_ledger_url}"
                        )

                    # Validate ABI before using it
                    if not isinstance(self.contract_abi, list):
                        raise ValueError(
                            "ETHEREUM_CONTRACT_ABI_JSON must be a JSON array (contract ABI)."
                        )
                    required_funcs: Set[str] = {"logEvent"}
                    abi_names = {
                        item.get("name")
                        for item in self.contract_abi
                        if item.get("type") == "function"
                    }
                    missing = required_funcs - abi_names
                    if missing:
                        raise DLTContractError(
                            f"Contract ABI missing required functions: {sorted(list(missing))}"
                        )

                    # Normalize address to checksummed format
                    addr = to_checksum_address(self.contract_address)
                    self.contract = self.web3.eth.contract(
                        address=addr, abi=self.contract_abi
                    )
                    logger.info(f"Ethereum contract loaded: {self.contract_address}")

                    self.account = Account.from_key(self.private_key)
                    logger.info(f"Ethereum account loaded: {self.account.address}")

                    self._is_connected = True
                    logger.info(
                        f"Successfully connected to Ethereum DLT at {self.audit_ledger_url}"
                    )
                elif self.dlt_type == "hyperledger_fabric":
                    raise DLTUnsupportedError(
                        "Hyperledger Fabric is not supported in this build."
                    )
                else:
                    raise ValueError(f"Unsupported DLT type: {self.dlt_type}")

                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="connect",
                    status="success",
                    **self.metric_labels,
                ).inc()
                span.set_status(Status(StatusCode.OK))
            except (
                ConnectionError,
                ValueError,
                ImportError,
                DLTUnsupportedError,
                DLTContractError,
            ) as e:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("dlt_type", self.dlt_type)
                    scope.set_tag("network_url", self.audit_ledger_url)
                    sentry_sdk.capture_exception(e)
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="connect",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                DLT_CALLS_ERRORS.labels(
                    dlt_type=self.dlt_type,
                    operation="connect",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Failed to connect: {e}"))
                logger.error(
                    f"Failed to connect to DLT {self.dlt_type} at {self.audit_ledger_url}: {e}",
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                raise DLTConnectionError(
                    f"Failed to connect to {self.dlt_type} DLT: {e}"
                ) from e
            except Exception as e:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("dlt_type", self.dlt_type)
                    scope.set_tag("network_url", self.audit_ledger_url)
                    sentry_sdk.capture_exception(e)
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="connect",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                DLT_CALLS_ERRORS.labels(
                    dlt_type=self.dlt_type,
                    operation="connect",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Failed to connect: {e}"))
                logger.error(
                    f"Failed to connect to DLT {self.dlt_type} at {self.audit_ledger_url}: {e}",
                    exc_info=True,
                )
                raise DLTConnectionError(
                    f"An unexpected error occurred during {self.dlt_type} DLT connection: {e}"
                ) from e
            finally:
                DLT_CALL_LATENCY_SECONDS.labels(
                    dlt_type=self.dlt_type, operation="connect", **self.metric_labels
                ).observe(time.monotonic() - start_time)

    async def disconnect(self) -> None:
        """
        Closes the DLT connection by clearing internal state.
        With AsyncWebsocketProvider, explicit closing is required.
        """
        if not self._is_connected:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"{self.dlt_type} client is not connected.")
            return

        with self.tracer.start_as_current_span(
            f"{self.dlt_type}_dlt_disconnect"
        ) as span:
            start_time = time.monotonic()
            DLT_CALLS_TOTAL.labels(
                dlt_type=self.dlt_type,
                operation="disconnect",
                status="attempt",
                **self.metric_labels,
            ).inc()
            try:
                if self.web3 and self.dlt_type == "ethereum":
                    await self.web3.provider.disconnect()

                self.web3 = None
                self.contract = None
                self.account = None
                self._is_connected = False

                logger.info("DLT client disconnected and state cleared.")
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="disconnect",
                    status="success",
                    **self.metric_labels,
                ).inc()
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("dlt_type", self.dlt_type)
                    sentry_sdk.capture_exception(e)
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="disconnect",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                DLT_CALLS_ERRORS.labels(
                    dlt_type=self.dlt_type,
                    operation="disconnect",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Failed to disconnect: {e}"))
                logger.error(
                    f"Failed to disconnect from DLT {self.dlt_type}: {e}", exc_info=True
                )
                raise DLTConnectionError(
                    f"Failed to disconnect from {self.dlt_type} DLT: {e}"
                ) from e
            finally:
                DLT_CALL_LATENCY_SECONDS.labels(
                    dlt_type=self.dlt_type, operation="disconnect", **self.metric_labels
                ).observe(time.monotonic() - start_time)

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1, max=10),
        retry=retry_if_exception_type(
            (
                ConnectionError,
                DLTTransactionError,
                DLTContractError,
                TransactionNotFound,
                TimeExhausted,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def log_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        operator: str = "system",
        correlation_id: Optional[str] = None,
        use_multi_sig: bool = False,
    ) -> str:
        """
        Logs an audit event to the DLT using an EIP-1559 transaction.
        Includes retries, dynamic gas management, and reorg-safe confirmations.

        Compliance Note: Logs on a blockchain are immutable. For GDPR or other data privacy compliance,
        avoid storing sensitive PII directly on-chain. Instead, use hashes for PII (e.g., details['user_id_hash'] = hashlib.sha256(user_id.encode()).hexdigest()) or
        a mechanism for data redaction/flagging in a separate system.

        Args:
            event_type (str): Categorization of the event (e.g., "agent:prediction", "system:config_update").
            details (Dict[str, Any]): Detailed payload of the event. For Ethereum, this will be JSON-serialized.
            operator (str): Identifier of the entity performing the action (e.g., "user_abc", "system").
            correlation_id (Optional[str]): A unique ID to link related events across systems/traces.
            use_multi_sig (bool): If True, attempts to use a Gnosis Safe for the transaction.

        Returns:
            str: The transaction hash for the logged event on the DLT.

        Raises:
            DLTTransactionError: If the DLT transaction fails.
            DLTContractError: If there's an issue with smart contract interaction.
            DLTUnsupportedError: If the DLT type is not supported.
            ValueError: If input validation fails.
        """
        # Validate inputs using Pydantic model
        event = AuditEvent(
            event_type=event_type,
            details=details,
            operator=operator,
            correlation_id=correlation_id,
        )

        # Create an idempotency key to prevent duplicate submissions on retry
        event_payload_hash = hashlib.sha256(
            json.dumps(
                event.model_dump(), sort_keys=True, default=str, separators=(",", ":")
            ).encode()
        ).hexdigest()

        async with self._idempotency_lock:
            if event_payload_hash in self._recent_ids:
                logger.info(
                    f"Dropping duplicate event submission (idempotency key {event_payload_hash})."
                )
                return f"duplicate_local_{event_payload_hash}"
            self._recent_ids[event_payload_hash] = time.monotonic()
            if len(self._recent_ids) > self._recent_ids_max:
                self._recent_ids.popitem(last=False)

        async with self.semaphore:
            with self.tracer.start_as_current_span(
                f"{self.dlt_type}_dlt_log_event"
            ) as span:
                span.set_attribute("dlt.event_type", event.event_type)
                span.set_attribute("dlt.operator", event.operator)
                span.set_attribute(
                    "dlt.correlation_id",
                    event.correlation_id if event.correlation_id else "N/A",
                )
                span.set_attribute("dlt.idempotency_key", event_payload_hash)

                start_time = time.monotonic()
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="log_event",
                    status="attempt",
                    **self.metric_labels,
                ).inc()
                DLT_TRANSACTIONS_PENDING.labels(
                    dlt_type=self.dlt_type, **self.metric_labels
                ).inc()

                tx_hash = ""  # Default mock hash
                try:
                    if self.dlt_type == "ethereum":
                        if not self.web3 or not self.contract or not self.account:
                            raise RuntimeError(
                                "Ethereum DLT not fully initialized. Call connect() first."
                            )

                        event_data_for_contract = json.dumps(
                            event.details, sort_keys=True, default=str
                        )

                        if use_multi_sig and self.enable_multisig:
                            if not MULTI_SIG_AVAILABLE:
                                raise DLTUnsupportedError(
                                    "Multi-sig support not available. Gnosis Safe library not found."
                                )
                            if not self.safe_address:
                                raise ValueError(
                                    "Safe address not configured for multi-sig. Set ETHEREUM_SAFE_ADDRESS env var."
                                )

                            # The following is a stub, as full multi-sig tx building is complex
                            logger.warning(
                                "Gnosis Safe execution is a conceptual stub and not fully implemented."
                            )
                            raise DLTUnsupportedError(
                                "Gnosis Safe execution is not supported in this version."
                            )

                        # Use 'pending' for nonce to handle concurrent transactions from the same account
                        nonce = await self.web3.eth.get_transaction_count(
                            self.account.address, "pending"
                        )
                        chain_id = await self.web3.eth.chain_id
                        span.set_attribute("dlt.chain_id", int(chain_id))
                        span.set_attribute(
                            "dlt.contract_address",
                            to_checksum_address(self.contract_address),
                        )

                        # EIP-1559 Dynamic Gas Fee Calculation
                        latest_block = await self.web3.eth.get_block("latest")
                        base_fee_per_gas = latest_block["baseFeePerGas"]

                        if base_fee_per_gas > self.web3.to_wei(
                            self.base_fee_gwei_cap, "gwei"
                        ):
                            raise DLTTransactionError(
                                f"Base fee ({self.web3.from_wei(base_fee_per_gas, 'gwei')} gwei) exceeds configured cap ({self.base_fee_gwei_cap} gwei)."
                            )

                        max_priority_fee_per_gas = await self.web3.eth.max_priority_fee
                        suggested_max_fee = (
                            2 * base_fee_per_gas
                        ) + max_priority_fee_per_gas
                        max_fee_per_gas = min(
                            suggested_max_fee,
                            self.web3.to_wei(self.gas_cap_gwei, "gwei"),
                        )

                        transaction_dict = {
                            "from": self.account.address,
                            "chainId": chain_id,
                            "nonce": nonce,
                            "maxPriorityFeePerGas": max_priority_fee_per_gas,
                            "maxFeePerGas": max_fee_per_gas,
                        }

                        try:
                            gas_limit = await self.contract.functions.logEvent(
                                event.event_type,
                                event.operator,
                                event.correlation_id if event.correlation_id else "",
                                event_data_for_contract,
                            ).estimate_gas(transaction_dict)
                        except Exception as e:
                            logger.warning(
                                f"Gas estimation failed ({e}). Using default gas limit of {self.default_gas_limit}."
                            )
                            gas_limit = self.default_gas_limit
                            span.set_attribute("dlt.gas_estimate_failed", True)

                        gas_buffer = 1.1  # Default 10% buffer for L1
                        if chain_id in {10, 42161}:  # Optimism, Arbitrum
                            gas_buffer = 1.05  # Lower 5% buffer for L2
                        transaction_dict["gas"] = int(gas_limit * gas_buffer)

                        span.set_attribute("dlt.gas_limit", transaction_dict["gas"])
                        span.set_attribute(
                            "dlt.max_priority_fee_gwei",
                            self.web3.from_wei(max_priority_fee_per_gas, "gwei"),
                        )
                        span.set_attribute(
                            "dlt.max_fee_gwei",
                            self.web3.from_wei(max_fee_per_gas, "gwei"),
                        )

                        built_txn = self.contract.functions.logEvent(
                            event.event_type,
                            event.operator,
                            event.correlation_id if event.correlation_id else "",
                            event_data_for_contract,
                        ).build_transaction(transaction_dict)

                        signed_txn = self.web3.eth.account.sign_transaction(
                            built_txn, private_key=self.private_key
                        )

                        tx_hash_bytes = await self.web3.eth.send_raw_transaction(
                            signed_txn.rawTransaction
                        )
                        tx_hash = tx_hash_bytes.hex()
                        del signed_txn, built_txn  # Clean up memory

                        # Wait for transaction to be mined and confirmed
                        await self.wait_for_confirmations(tx_hash)

                        logger.info(
                            f"Ethereum DLT event logged and confirmed. Tx Hash: {tx_hash}."
                        )
                        span.set_attribute("dlt.tx_hash", tx_hash)

                        receipt = await self.web3.eth.get_transaction_receipt(tx_hash)
                        span.set_attribute("dlt.block_number", receipt.blockNumber)
                        DLT_GAS_USED.labels(
                            dlt_type=self.dlt_type,
                            operation="log_event",
                            **self.metric_labels,
                        ).observe(receipt.gasUsed)

                    elif self.dlt_type == "hyperledger_fabric":
                        raise DLTUnsupportedError(
                            "Hyperledger Fabric is not supported in this build."
                        )

                    else:
                        raise ValueError(
                            f"Unsupported DLT type for logging: {self.dlt_type}"
                        )

                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        status="success",
                        **self.metric_labels,
                    ).inc()
                    span.set_status(Status(StatusCode.OK))
                    return tx_hash
                except (ContractCustomError, ContractLogicError) as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "log_event")
                        scope.set_extra("event_payload", event.model_dump())
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    DLT_REVERT_REASON.labels(
                        dlt_type=self.dlt_type,
                        reason="contract_logic_error",
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, f"Contract error: {e}"))
                    logger.error(
                        f"DLT Contract Error for event {event.event_type} to {self.dlt_type}: {e}",
                        exc_info=logger.isEnabledFor(logging.DEBUG),
                    )
                    raise DLTContractError(
                        f"Failed to log DLT event due to contract error: {e}"
                    ) from e
                except (TransactionNotFound, TimeExhausted) as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "log_event")
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Transaction timeout/not found: {e}")
                    )
                    logger.warning(
                        f"DLT Transaction Error (timeout/not found) for event {event.event_type} to {self.dlt_type}. Tenacity will retry: {e}"
                    )
                    raise DLTTransactionError(
                        f"Failed to log DLT event (transaction timeout/not found): {e}"
                    ) from e
                except (
                    ConnectionError,
                    ValueError,
                    RuntimeError,
                    DLTUnsupportedError,
                ) as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "log_event")
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to log event: {e}")
                    )
                    logger.error(
                        f"Failed to log DLT event {event.event_type} to {self.dlt_type}: {e}",
                        exc_info=logger.isEnabledFor(logging.DEBUG),
                    )
                    raise DLTTransactionError(
                        f"Failed to log DLT event to {self.dlt_type}: {e}"
                    ) from e
                except Exception as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "log_event")
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to log event: {e}")
                    )
                    logger.error(
                        f"An unexpected error occurred logging DLT event {event.event_type} to {self.dlt_type}: {e}",
                        exc_info=True,
                    )
                    raise DLTTransactionError(
                        f"An unexpected error occurred logging DLT event to {self.dlt_type}: {e}"
                    ) from e
                finally:
                    DLT_CALL_LATENCY_SECONDS.labels(
                        dlt_type=self.dlt_type,
                        operation="log_event",
                        **self.metric_labels,
                    ).observe(time.monotonic() - start_time)
                    DLT_TRANSACTIONS_PENDING.labels(
                        dlt_type=self.dlt_type, **self.metric_labels
                    ).dec()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1, max=10),
        retry=retry_if_exception_type(
            (
                ConnectionError,
                DLTTransactionError,
                DLTContractError,
                TransactionNotFound,
                TimeExhausted,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def batch_log_events(self, events: List[AuditEvent]) -> str:
        """
        Logs a batch of audit events in a single transaction to reduce gas costs.
        Assumes the smart contract has a 'logEvents' function that accepts arrays.
        """
        if not self._is_connected or not self.web3 or not self.contract:
            raise DLTConnectionError("Not connected to DLT")
        if not hasattr(self.contract.functions, "logEvents"):
            raise DLTUnsupportedError("Batch logging not supported by the contract ABI")

        async with self.semaphore:
            with self.tracer.start_as_current_span(
                f"{self.dlt_type}_dlt_batch_log_events"
            ) as span:
                span.set_attribute("batch_size", len(events))
                start_time = time.monotonic()
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="batch_log_events",
                    status="attempt",
                    **self.metric_labels,
                ).inc()
                DLT_TRANSACTIONS_PENDING.labels(
                    dlt_type=self.dlt_type, **self.metric_labels
                ).inc()
                try:
                    # Prepare batch arrays for contract call
                    event_types = [e.event_type for e in events]
                    operators = [e.operator for e in events]
                    correlation_ids = [e.correlation_id or "" for e in events]
                    details_jsons = [
                        json.dumps(e.details, sort_keys=True, default=str)
                        for e in events
                    ]

                    nonce = await self.web3.eth.get_transaction_count(
                        self.account.address, "pending"
                    )
                    chain_id = await self.web3.eth.chain_id
                    latest_block = await self.web3.eth.get_block("latest")
                    base_fee_per_gas = latest_block["baseFeePerGas"]

                    if base_fee_per_gas > self.web3.to_wei(
                        self.base_fee_gwei_cap, "gwei"
                    ):
                        raise DLTTransactionError(
                            f"Base fee ({self.web3.from_wei(base_fee_per_gas, 'gwei')} gwei) exceeds configured cap ({self.base_fee_gwei_cap} gwei)."
                        )

                    max_priority_fee_per_gas = await self.web3.eth.max_priority_fee
                    suggested_max_fee = (
                        2 * base_fee_per_gas
                    ) + max_priority_fee_per_gas
                    max_fee_per_gas = min(
                        suggested_max_fee, self.web3.to_wei(self.gas_cap_gwei, "gwei")
                    )

                    transaction_dict = {
                        "from": self.account.address,
                        "chainId": chain_id,
                        "nonce": nonce,
                        "maxPriorityFeePerGas": max_priority_fee_per_gas,
                        "maxFeePerGas": max_fee_per_gas,
                    }

                    try:
                        gas_limit = await self.contract.functions.logEvents(
                            event_types, operators, correlation_ids, details_jsons
                        ).estimate_gas(transaction_dict)
                    except Exception as e:
                        logger.warning(
                            f"Gas estimation failed ({e}). Using default gas limit of {self.default_gas_limit}."
                        )
                        gas_limit = self.default_gas_limit
                        span.set_attribute("dlt.gas_estimate_failed", True)

                    gas_buffer = (
                        1.1 if chain_id not in {10, 42161} else 1.05
                    )  # L2 optimization
                    transaction_dict["gas"] = int(gas_limit * gas_buffer)

                    built_txn = self.contract.functions.logEvents(
                        event_types, operators, correlation_ids, details_jsons
                    ).build_transaction(transaction_dict)
                    signed_txn = self.web3.eth.account.sign_transaction(
                        built_txn, private_key=self.private_key
                    )
                    tx_hash_bytes = await self.web3.eth.send_raw_transaction(
                        signed_txn.rawTransaction
                    )
                    tx_hash = tx_hash_bytes.hex()
                    del signed_txn, built_txn  # Clean up memory

                    # Wait for transaction to be mined and confirmed
                    await self.wait_for_confirmations(tx_hash)

                    receipt = await self.web3.eth.get_transaction_receipt(tx_hash)
                    if receipt.status == 0:
                        DLT_REVERT_REASON.labels(
                            dlt_type=self.dlt_type,
                            reason="on-chain_revert",
                            **self.metric_labels,
                        ).inc()
                        raise DLTTransactionError(
                            f"Batch transaction reverted. Tx Hash: {tx_hash}. Receipt: {receipt}"
                        )

                    logger.info(
                        f"Batch event logged to {self.dlt_type}. Transaction ID: {tx_hash}"
                    )
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        status="success",
                        **self.metric_labels,
                    ).inc()
                    DLT_GAS_USED.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        **self.metric_labels,
                    ).observe(receipt.gasUsed)
                    span.set_status(Status(StatusCode.OK))
                    return tx_hash
                except (ContractCustomError, ContractLogicError) as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "batch_log_events")
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    DLT_REVERT_REASON.labels(
                        dlt_type=self.dlt_type,
                        reason="contract_logic_error",
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, f"Contract error: {e}"))
                    raise DLTContractError(
                        f"Failed to batch log events due to contract error: {e}"
                    ) from e
                except (TransactionNotFound, TimeExhausted) as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "batch_log_events")
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Transaction timeout/not found: {e}")
                    )
                    raise DLTTransactionError(
                        f"Failed to batch log events (transaction timeout/not found): {e}"
                    ) from e
                except Exception as e:
                    with sentry_sdk.push_scope() as scope:
                        scope.set_tag("dlt_type", self.dlt_type)
                        scope.set_tag("network_url", self.audit_ledger_url)
                        scope.set_tag("operation", "batch_log_events")
                        sentry_sdk.capture_exception(e)
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        status="failure",
                        **self.metric_labels,
                    ).inc()
                    DLT_CALLS_ERRORS.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        error_type=type(e).__name__,
                        **self.metric_labels,
                    ).inc()
                    span.record_exception(e)
                    span.set_status(
                        Status(StatusCode.ERROR, f"Failed to batch log events: {e}")
                    )
                    logger.error(
                        f"Failed to batch log events to {self.dlt_type}: {e}",
                        exc_info=True,
                    )
                    raise DLTTransactionError(
                        f"Failed to batch log events to {self.dlt_type}: {e}"
                    ) from e
                finally:
                    DLT_CALL_LATENCY_SECONDS.labels(
                        dlt_type=self.dlt_type,
                        operation="batch_log_events",
                        **self.metric_labels,
                    ).observe(time.monotonic() - start_time)
                    DLT_TRANSACTIONS_PENDING.labels(
                        dlt_type=self.dlt_type, **self.metric_labels
                    ).dec()

    async def get_event(self, tx_hash: str) -> Dict[str, Any]:
        """
        Retrieves and decodes an audit event log from a given transaction hash.
        Assumes the smart contract emits a 'LogEvent' event.
        """
        if not self._is_connected or not self.web3 or not self.contract:
            raise DLTConnectionError("Client not connected.")

        try:
            receipt = await self.web3.eth.get_transaction_receipt(tx_hash)
            # Assumes the event name in the contract is 'LogEvent'
            logs = self.contract.events.LogEvent().process_receipt(receipt)
            if not logs:
                raise TransactionNotFound(
                    f"No 'LogEvent' logs found for transaction hash: {tx_hash}"
                )
            # Return the arguments of the first event log found
            return logs[0]["args"]
        except TransactionNotFound as e:
            logger.warning(
                f"Could not find transaction or logs for hash {tx_hash}: {e}"
            )
            raise
        except Exception as e:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("tx_hash", tx_hash)
                sentry_sdk.capture_exception(e)
            logger.error(
                f"Error retrieving event for tx_hash {tx_hash}: {e}", exc_info=True
            )
            raise DLTTransactionError(f"Failed to retrieve event data: {e}") from e

    async def get_events_by_type(
        self,
        event_type: str,
        start_block: int,
        end_block: Union[int, str] = "latest",
        chunk_size: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves events of a specific type within a block range, handling large ranges by chunking.

        Args:
            event_type (str): The event type to filter by.
            start_block (int): The starting block number to search from.
            end_block (Union[int, str]): The ending block number (or 'latest').
            chunk_size (int): The number of blocks to query in each chunk to avoid RPC limits.

        Returns:
            List[Dict[str, Any]]: A list of event argument dictionaries matching the type.
        """
        if not self._is_connected or not self.web3 or not self.contract:
            raise DLTConnectionError("Client not connected.")

        all_logs: List[Dict[str, Any]] = []
        try:
            current_block = start_block
            final_block = (
                await self.web3.eth.block_number
                if end_block == "latest"
                else int(end_block)
            )

            while current_block <= final_block:
                chunk_end = min(current_block + chunk_size, final_block)
                logger.debug(
                    "Fetching logs from block %s to %s...", current_block, chunk_end
                )

                event_filter_params = {
                    "fromBlock": current_block,
                    "toBlock": chunk_end,
                    "argument_filters": {"eventType": event_type},
                }

                logs = await self.contract.events.LogEvent.get_logs(
                    **event_filter_params
                )

                all_logs.extend([log["args"] for log in logs])
                current_block = chunk_end + 1

            return all_logs

        except Exception as e:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("event_type", event_type)
                scope.set_tag("start_block", start_block)
                scope.set_tag("end_block", end_block)
                sentry_sdk.capture_exception(e)
            logger.error(
                f"Error retrieving events by type '{event_type}': {e}", exc_info=True
            )
            raise DLTTransactionError(
                f"Failed to retrieve event data by type: {e}"
            ) from e

    async def verify_event(
        self, tx_hash: str, expected_details: Dict[str, Any]
    ) -> bool:
        """
        Verifies that an on-chain event's details payload matches an expected dictionary.
        """
        try:
            event_args = await self.get_event(tx_hash)
            on_chain_details = json.loads(event_args.get(self.details_field, "{}"))
            return on_chain_details == expected_details
        except (TransactionNotFound, DLTTransactionError):
            return False
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode on-chain details for verification: {e}")
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=0.5, max=5))
    async def flag_for_redaction(self, tx_hash: str, reason: str) -> None:
        """
        Flags an on-chain event for redaction by storing its hash off-chain (e.g., in Postgres).
        For GDPR compliance, this is used with an external redaction service to handle 'right to be forgotten' requests
        without altering the immutable blockchain record.
        """
        if not POSTGRES_CLIENT_AVAILABLE:
            logger.warning(
                "Postgres client not available. Cannot flag event for redaction."
            )
            return

        # Simple length validation for sanity
        if len(reason) > 200:
            raise ValueError("Redaction reason is too long.")

        details_hash = hashlib.sha256(tx_hash.encode()).hexdigest()
        try:
            pg_client = PostgresClient()  # Assumes configured from SFE codebase
            async with pg_client:
                await pg_client.save(
                    "audit_redactions",
                    {
                        "id": details_hash,
                        "tx_hash": tx_hash,
                        "reason": reason,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    id_field="id",
                )
            logger.info(
                f"Flagged tx {tx_hash} for redaction: {reason}. Hash: {details_hash}"
            )
        except Exception as e:
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("tx_hash", tx_hash)
                scope.set_tag("operation", "flag_redaction")
                sentry_sdk.capture_exception(e)
            logger.error(
                f"Failed to flag event {tx_hash} for redaction: {e}", exc_info=True
            )
            raise DLTError(f"Failed to flag event for redaction: {e}") from e

    async def wait_for_confirmations(self, tx_hash: str) -> None:
        """
        Waits for a specified number of block confirmations for a transaction to mitigate reorg risk.
        Uses polling for new blocks.
        """
        logger.debug(
            f"Waiting for {self.confirmations} confirmations for tx {tx_hash}..."
        )

        try:
            # First, wait for the transaction to be included in a block
            receipt = await self.web3.eth.wait_for_transaction_receipt(
                tx_hash, timeout=self.tx_timeout_sec
            )
            if receipt.status == 0:
                raise DLTTransactionError(f"Transaction {tx_hash} reverted.")

            # Then, poll for additional confirmations
            target_block = receipt.blockNumber + self.confirmations
            while True:
                latest_block = await self.web3.eth.block_number
                if latest_block >= target_block:
                    logger.debug(
                        f"Transaction {tx_hash} confirmed with {self.confirmations} blocks."
                    )
                    return
                await asyncio.sleep(self.block_poll_interval_sec)

        except TransactionNotFound:
            raise DLTTransactionError(
                f"Transaction {tx_hash} was not found, likely reorged."
            )

    async def is_connected(self) -> bool:
        """
        Performs a health check to determine if the client is connected to the DLT.
        """
        with self.tracer.start_as_current_span(
            f"{self.dlt_type}_dlt_is_connected"
        ) as span:
            start_time = time.monotonic()
            DLT_CALLS_TOTAL.labels(
                dlt_type=self.dlt_type,
                operation="is_connected",
                status="attempt",
                **self.metric_labels,
            ).inc()
            try:
                if not self._is_connected or not self.web3:
                    return False

                if self.dlt_type == "ethereum":
                    connected = await self.web3.is_connected()
                    if not connected:
                        self._is_connected = (
                            False  # Update internal state if connection dropped
                        )
                    DLT_CALLS_TOTAL.labels(
                        dlt_type=self.dlt_type,
                        operation="is_connected",
                        status="success" if connected else "failure",
                        **self.metric_labels,
                    ).inc()
                    span.set_status(
                        Status(StatusCode.OK if connected else StatusCode.ERROR)
                    )
                    return connected
                elif self.dlt_type == "hyperledger_fabric":
                    raise DLTUnsupportedError(
                        "Hyperledger Fabric is not supported in this build."
                    )
                else:
                    return False  # For unsupported types, assume not connected
            except DLTUnsupportedError as e:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("dlt_type", self.dlt_type)
                    scope.set_tag("operation", "is_connected")
                    sentry_sdk.capture_exception(e)
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="is_connected",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                DLT_CALLS_ERRORS.labels(
                    dlt_type=self.dlt_type,
                    operation="is_connected",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Health check failed: {e}"))
                logger.error(
                    f"Health check failed for {self.dlt_type}: {e}",
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                return False
            except Exception as e:
                with sentry_sdk.push_scope() as scope:
                    scope.set_tag("dlt_type", self.dlt_type)
                    scope.set_tag("operation", "is_connected")
                    sentry_sdk.capture_exception(e)
                DLT_CALLS_TOTAL.labels(
                    dlt_type=self.dlt_type,
                    operation="is_connected",
                    status="failure",
                    **self.metric_labels,
                ).inc()
                DLT_CALLS_ERRORS.labels(
                    dlt_type=self.dlt_type,
                    operation="is_connected",
                    error_type=type(e).__name__,
                    **self.metric_labels,
                ).inc()
                span.record_exception(e)
                span.set_status(Status(StatusCode.ERROR, f"Health check failed: {e}"))
                logger.error(
                    f"An unexpected error occurred during health check for {self.dlt_type}: {e}",
                    exc_info=True,
                )
                return False
            finally:
                DLT_CALL_LATENCY_SECONDS.labels(
                    dlt_type=self.dlt_type,
                    operation="is_connected",
                    **self.metric_labels,
                ).observe(time.monotonic() - start_time)


# Example Usage (for testing purposes)
async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    # Initialize Sentry if DSN is provided
    sentry_dsn = os.getenv("SENTRY_DSN")
    if sentry_dsn:
        sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=1.0)
        logger.info("Sentry SDK initialized.")

    # --- Configuration for Ethereum DLT ---
    # Set these environment variables before running for real Ethereum tests:
    # os.environ["AUDIT_LEDGER_URL"] = "ws://127.0.0.1:8546"  # Use WebSocket URL
    # os.environ["ETHEREUM_PRIVATE_KEY"] = "0x..."
    # os.environ["ETHEREUM_CONTRACT_ADDRESS"] = "0x..."
    # os.environ["ETHEREUM_CONTRACT_ABI_JSON"] = json.dumps([...])

    ethereum_client: Optional[AuditLedgerClient] = None
    try:
        ethereum_client = AuditLedgerClient(dlt_type="ethereum")
    except ValueError as e:
        logger.warning(f"Skipping Ethereum tests due to missing configuration: {e}.")
        ethereum_client = None
    except DLTError as e:
        logger.warning(
            f"Skipping Ethereum tests due to production config enforcement: {e}"
        )
        ethereum_client = None

    # --- Test Clients ---
    clients: List[AuditLedgerClient] = []
    if ethereum_client:
        clients.append(ethereum_client)
    else:
        logger.warning(
            "No Ethereum client configured. A mock client will be used for demonstration."
        )

        class MockAuditLedgerClient(AuditLedgerClient):
            def __init__(self, *args, **kwargs):
                self.dlt_type = "mock"
                self._is_connected = False
                self.metric_labels = {"env": "test", "cluster": "mock"}
                self._recent_ids: OrderedDict[str, float] = OrderedDict()
                self._recent_ids_max: Final[int] = 5000
                self.semaphore = asyncio.Semaphore(1)
                self.tracer = tracer
                self._idempotency_lock = asyncio.Lock()

            async def connect(self) -> None:
                logger.info("Mock DLT connected.")
                await asyncio.sleep(0.01)
                self._is_connected = True

            async def disconnect(self) -> None:
                logger.info("Mock DLT disconnected.")
                await asyncio.sleep(0.01)
                self._is_connected = False
                self.private_key = None  # Mimic real client behavior

            async def log_event(
                self,
                event_type: str,
                details: Dict[str, Any],
                operator: str = "system",
                correlation_id: Optional[str] = None,
                use_multi_sig: bool = False,
            ) -> str:
                if not self._is_connected:
                    raise DLTConnectionError("Mock DLT not connected.")

                event = AuditEvent(
                    event_type=event_type,
                    details=details,
                    operator=operator,
                    correlation_id=correlation_id,
                )
                event_payload_hash = hashlib.sha256(
                    json.dumps(
                        event.model_dump(),
                        sort_keys=True,
                        default=str,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()

                async with self._idempotency_lock:
                    if event_payload_hash in self._recent_ids:
                        logger.info(
                            f"Mock dropping duplicate event (idempotency key {event_payload_hash})."
                        )
                        return f"duplicate_local_{event_payload_hash}"
                    self._recent_ids[event_payload_hash] = time.monotonic()
                    if len(self._recent_ids) > self._recent_ids_max:
                        self._recent_ids.popitem(last=False)

                async with self.semaphore:
                    logger.info(f"Mock DLT event logged: {event.model_dump_json()}")
                    await asyncio.sleep(0.05)
                    return f"mock_tx_{str(uuid.uuid4())}"

            async def batch_log_events(self, events: List[AuditEvent]) -> str:
                if not self._is_connected:
                    raise DLTConnectionError("Mock DLT not connected.")
                async with self.semaphore:
                    logger.info(f"Mock DLT batch logging {len(events)} events.")
                    await asyncio.sleep(0.05)
                    return f"mock_batch_tx_{str(uuid.uuid4())}"

            async def is_connected(self) -> bool:
                return self._is_connected

            async def wait_for_confirmations(self, tx_hash: str) -> None:
                logger.info(f"Mock waiting for confirmations for {tx_hash}")
                await asyncio.sleep(0.1)

            async def flag_for_redaction(self, tx_hash: str, reason: str) -> None:
                logger.info(
                    f"Mock flagging tx {tx_hash} for redaction. Reason: {reason}"
                )
                await asyncio.sleep(0.01)

        clients.append(MockAuditLedgerClient())

    for client in clients:
        logger.info(f"\n--- Testing {client.dlt_type} DLT Client ---")
        async with client:
            if not await client.is_connected():
                logger.error(
                    f"Client {client.dlt_type} failed to connect. Skipping tests."
                )
                continue

            try:
                # Test successful event logging
                event_details: Dict[str, Any] = {
                    "agent_id": "test_agent_123",
                    "action": "code_change",
                    "file": "main.py",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                tx_hash = await client.log_event(
                    "agent:code_update", event_details, "sfe_system"
                )
                logger.info(
                    f"Logged event to {client.dlt_type}. Transaction ID: {tx_hash}"
                )

                # Test idempotency
                idempotent_tx_hash = await client.log_event(
                    "agent:code_update", event_details, "sfe_system"
                )
                logger.info(
                    f"Second log with same payload returned: {idempotent_tx_hash}"
                )
                assert "duplicate_local" in idempotent_tx_hash

                # Test Pydantic validation failure
                try:
                    logger.info("Testing invalid event type...")
                    await client.log_event("invalid@event_type!", {"data": "test"})
                except ValueError as e:
                    logger.info(f"Successfully caught expected validation error: {e}")

                try:
                    logger.info("Testing oversized details payload...")
                    large_payload = {"key": "a" * 20000}
                    await client.log_event("test:large_payload", large_payload)
                except ValueError as e:
                    logger.info(
                        f"Successfully caught expected validation error for large payload: {e}"
                    )

                # Test batch logging (if supported)
                try:
                    if hasattr(client, "batch_log_events"):
                        logger.info("Testing batch logging...")
                        batch_events = [
                            AuditEvent(
                                event_type="test:batch", details={"id": i}
                            ).model_dump()
                            for i in range(5)
                        ]
                        batch_tx_hash = await client.batch_log_events(
                            [AuditEvent(**e) for e in batch_events]
                        )
                        logger.info(
                            f"Batch logged successfully. Tx ID: {batch_tx_hash}"
                        )
                except DLTUnsupportedError as e:
                    logger.info(f"Batch logging not supported: {e}")

                # Test redaction flagging
                await client.flag_for_redaction(tx_hash, "GDPR Right to be Forgotten")

            except DLTError as e:
                logger.error(
                    f"Test failed for {client.dlt_type} DLT Client due to DLT error: {e}",
                    exc_info=True,
                )
            except Exception as e:
                logger.error(
                    f"Test failed for {client.dlt_type} DLT Client due to unexpected error: {e}",
                    exc_info=True,
                )


if __name__ == "__main__" and os.getenv("RUN_EXAMPLE", "0") == "1":
    asyncio.run(main())
