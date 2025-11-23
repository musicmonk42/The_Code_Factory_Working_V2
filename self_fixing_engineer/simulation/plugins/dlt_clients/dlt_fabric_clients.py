"""
Production-ready Hyperledger Fabric DLT client implementation.

Key features:
- Strong configuration validation with Pydantic.
- Proper circuit-breaker usage and async retry on transient errors.
- Secure credential handling with no hard-coded secrets.
- Structured logging with optional JSON format and audit trail integration.
- Explicit connection pooling and resource management.
- Comprehensive error handling with typed exceptions.
"""

import asyncio
import json
import time
import uuid
import sys
from typing import Any, Dict, Optional, Tuple, Union, List
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path

import aiohttp
from pydantic import (
    BaseModel,
    Field,
    validator,
    ValidationError,
    AnyHttpUrl,
    AnyUrl,
    model_validator,
)

from .dlt_base import (
    BaseDLTClient,
    BaseOffChainClient,
    DLTClientValidationError,
    DLTClientConfigurationError,
    DLTClientConnectivityError,
    DLTClientAuthError,
    DLTClientTransactionError,
    DLTClientQueryError,
    DLTClientTimeoutError,
    DLTClientCircuitBreakerError,
    DLTClientError,
    async_retry,
    TRACER,
    Status,
    StatusCode,
    SECRETS_MANAGER,
    AUDIT,
    PRODUCTION_MODE,
)
from .dlt_base import _base_logger, scrub_secrets

# Optional timeout helper for close() best-effort
try:
    import async_timeout

    ASYNC_TIMEOUT_AVAILABLE = True
except ImportError:
    ASYNC_TIMEOUT_AVAILABLE = False

# Check for Fabric SDK availability
FABRIC_NATIVE_AVAILABLE = False
try:
    from hfc.fabric import Client as FabricSDKClient
    from hfc.util.keyvaluestore import FileKeyValueStore
    from hfc.fabric.certificate import User as FabricUser

    FABRIC_NATIVE_AVAILABLE = True
except ImportError:
    _base_logger.warning("Native Fabric SDK not available. Will use REST gateway mode.")

# Optional Fabric-specific Prometheus metrics
try:
    from prometheus_client import Counter, Histogram, Gauge

    FABRIC_METRICS = {
        "chaincode_calls_total": Counter(
            "fabric_client_chaincode_calls_total",
            "Total number of chaincode calls made by Fabric client",
            labelnames=["client_type", "channel", "chaincode", "function", "status"],
        ),
        "chaincode_call_duration": Histogram(
            "fabric_client_chaincode_call_duration_seconds",
            "Duration of Fabric chaincode calls in seconds",
            labelnames=["client_type", "channel", "chaincode", "function"],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0),
        ),
        "peer_health": Gauge(
            "fabric_client_peer_health",
            "Health status of Fabric peers (1=healthy, 0=unhealthy)",
            labelnames=["client_type", "peer_url"],
        ),
    }
except Exception:
    _base_logger.warning("Prometheus client not available for Fabric-specific metrics.")
    FABRIC_METRICS = {}  # Guarded usage below


# ---------------------------
# Configuration schema
# ---------------------------


# Custom URL type that accepts grpc/grpcs schemes
class GrpcUrl(AnyUrl):
    allowed_schemes = {"grpc", "grpcs", "http", "https"}


class FabricPeerConfig(BaseModel):
    """Configuration for a single Fabric peer."""

    url: Union[AnyHttpUrl, GrpcUrl, str]  # Accept both HTTP and gRPC URLs
    ssl_target_name_override: Optional[str] = None
    tls_cacerts: Optional[str] = None

    @validator("url", pre=True)
    def validate_url(cls, v):
        if isinstance(v, str):
            # Basic validation for gRPC URLs
            parsed = urlparse(v)
            if parsed.scheme not in ("grpc", "grpcs", "http", "https"):
                raise ValueError(
                    f"URL scheme must be one of: grpc, grpcs, http, https. Got: {parsed.scheme}"
                )
        return v


class FabricChannelConfig(BaseModel):
    """Configuration for a Fabric channel."""

    name: str = Field(..., pattern=r"^[a-zA-Z0-9._-]+$")
    peers: List[str]  # References to peer URLs


class FabricOrdererConfig(BaseModel):
    """Configuration for a Fabric orderer."""

    url: Union[AnyHttpUrl, GrpcUrl, str]  # Accept both HTTP and gRPC URLs
    ssl_target_name_override: Optional[str] = None
    tls_cacerts: Optional[str] = None

    @validator("url", pre=True)
    def validate_url(cls, v):
        if isinstance(v, str):
            # Basic validation for gRPC URLs
            parsed = urlparse(v)
            if parsed.scheme not in ("grpc", "grpcs", "http", "https"):
                raise ValueError(
                    f"URL scheme must be one of: grpc, grpcs, http, https. Got: {parsed.scheme}"
                )
        return v


class FabricConfig(BaseModel):
    """Configuration schema for Fabric client."""

    # Mode: 'sdk' or 'rest'
    mode: str = Field("rest", pattern=r"^(sdk|rest)$")

    # REST API (used in rest mode)
    rest_api_url: Optional[AnyHttpUrl] = None
    rest_api_auth_token: Optional[str] = None

    # SDK configuration (used in sdk mode)
    msp_id: Optional[str] = None
    channel: Optional[str] = None
    chaincode_id: Optional[str] = None
    user_name: Optional[str] = None
    cert_path: Optional[str] = None
    key_path: Optional[str] = None
    peers: Optional[Dict[str, FabricPeerConfig]] = None
    channels: Optional[List[FabricChannelConfig]] = None
    orderers: Optional[Dict[str, FabricOrdererConfig]] = None

    # Common configuration
    default_timeout_seconds: int = Field(30, ge=1)
    health_check_timeout: int = Field(10, ge=1)
    invoke_timeout: int = Field(60, ge=1)
    query_timeout: int = Field(30, ge=1)
    close_timeout: float = Field(5.0, ge=0.1)
    max_connections: int = Field(10, ge=1)
    rate_limit_requests_per_second: float = Field(10.0, ge=0.1)
    log_format: str = Field("json", pattern=r"^(json|text)$")

    checkpoint_function_name: str = "WriteCheckpoint"
    query_function_name: str = "ReadCheckpoint"
    rollback_function_name: str = "RollbackCheckpoint"

    # Model validator that runs after all fields are parsed
    @model_validator(mode="after")
    def validate_mode_dependencies(self):
        """Validate mode-specific dependencies after all fields are set."""
        if self.mode == "sdk":
            if not FABRIC_NATIVE_AVAILABLE:
                raise ValueError("SDK mode requested but Fabric SDK is not available")
            required = [
                "msp_id",
                "channel",
                "chaincode_id",
                "user_name",
                "cert_path",
                "key_path",
                "peers",
            ]
            missing = [field for field in required if not getattr(self, field, None)]
            if missing:
                raise ValueError(f"SDK mode requires: {', '.join(missing)}")
        elif self.mode == "rest":
            if not self.rest_api_url:
                raise ValueError("REST mode requires: rest_api_url")
        return self

    @validator("cert_path", "key_path")
    def validate_paths(cls, v, values):
        if v and values.get("mode") == "sdk":
            # Only validate path existence in production mode
            # This allows tests to mock the paths
            if PRODUCTION_MODE:
                path = Path(v)
                if not path.exists():
                    raise ValueError(f"File not found: {v}")
        return v

    class Config:
        # This ensures validators run in the order fields are defined
        validate_assignment = True


# ---------------------------
# Client implementation
# ---------------------------
class FabricClientWrapper(BaseDLTClient):
    """
    Hyperledger Fabric client with support for both native SDK and REST modes.
    Provides high-level DLT operations for checkpoint management.
    """

    def __init__(self, config: Dict[str, Any], off_chain_client: "BaseOffChainClient"):
        # 1) Build and validate client-specific config
        try:
            fabric_cfg: Dict[str, Any] = dict(config.get("fabric", {}))

            # Handle secrets if present
            if fabric_cfg.get("mode") == "rest" and not fabric_cfg.get("rest_api_auth_token"):
                fabric_cfg["rest_api_auth_token"] = SECRETS_MANAGER.get_secret(
                    "FABRIC_REST_TOKEN", required=False
                )

            # Use dict() instead of dict(exclude_unset=True) to include defaults
            validated_fabric = FabricConfig(**fabric_cfg).dict()
        except ValidationError as e:
            raise DLTClientValidationError(f"Invalid Fabric client configuration: {e}", "Fabric")
        except Exception as e:
            raise DLTClientValidationError(
                f"Failed to load Fabric client configuration: {e}",
                "Fabric",
                original_exception=e,
            )

        # 2) Prepare base/common config for BaseDLTClient (timeouts/retry/circuit-breaker)
        base_cfg_keys = (
            "default_timeout_seconds",
            "retry_policy",
            "circuit_breaker_threshold",
            "circuit_breaker_reset_timeout",
        )
        base_cfg = {k: config[k] for k in base_cfg_keys if k in config}
        # Use get() with default to safely access the timeout value
        base_cfg.setdefault(
            "default_timeout_seconds",
            validated_fabric.get("default_timeout_seconds", 30),
        )

        super().__init__(base_cfg, off_chain_client)

        # Store client-specific validated config separately
        self.client_config: Dict[str, Any] = validated_fabric
        self.client_type = "Fabric"

        # Set up client-specific fields based on mode
        self.mode = self.client_config["mode"]

        # Common fields
        self.checkpoint_function = self.client_config["checkpoint_function_name"]
        self.query_function = self.client_config["query_function_name"]
        self.rollback_function = self.client_config["rollback_function_name"]

        # Mode-specific initialization
        if self.mode == "sdk":
            # Native SDK mode
            self.msp_id = self.client_config["msp_id"]
            self.channel_name = self.client_config["channel"]
            self.chaincode_id = self.client_config["chaincode_id"]
            self.user_name = self.client_config["user_name"]
            self.cert_path = self.client_config["cert_path"]
            self.key_path = self.client_config["key_path"]

            # Set up SDK client
            self._sdk_client = None
            self._sdk_initialized = False

        else:
            # REST API mode
            self.rest_api_url = str(self.client_config["rest_api_url"])
            self.auth_token = self.client_config.get("rest_api_auth_token")

            # HTTP client for REST mode
            self._session: Optional[aiohttp.ClientSession] = None
            self._session_lock = asyncio.Lock()

        # Rate limiter
        self._rate_limit_delay = 1.0 / float(self.client_config["rate_limit_requests_per_second"])
        self._last_request_time = 0.0

        # Logging format
        self._log_format = self.client_config["log_format"]
        self._format_log(
            "info",
            f"FabricClientWrapper initialized ({self.mode.upper()} mode)",
            {"mode": self.mode},
        )

    # ------------- internal helpers -------------

    def _format_log(self, level: str, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        """
        Formats logs as JSON or text based on configuration.
        Also emits critical events to the AUDIT trail.
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
            # Fix: Handle potential TypeError from scrub_secrets
            try:
                safe_entry = scrub_secrets(log_entry)
            except TypeError:
                # Fallback to manual safe copy if scrub_secrets fails
                safe_entry = self._safe_copy_dict(log_entry)

            getattr(self.logger, level.lower())(json.dumps(safe_entry))
            if level.upper() in ("ERROR", "CRITICAL"):
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"fabric_client_error.{level.lower()}",
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
                            f"fabric_client_error.{level.lower()}",
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
        """
        Enforces client-side rate limiting.
        """
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Ensures a single aiohttp client session with connection pooling for REST mode.
        """
        async with self._session_lock:
            if self._session is None or self._session.closed:
                headers = {}
                if self.auth_token:
                    headers["Authorization"] = f"Bearer {self.auth_token}"

                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=self.client_config["default_timeout_seconds"]
                    ),
                    headers=headers,
                    connector=aiohttp.TCPConnector(limit=self.client_config["max_connections"]),
                )
            return self._session

    async def _init_sdk_client(self) -> None:
        """
        Initialize the native Fabric SDK client (only in SDK mode).
        """
        if self._sdk_initialized:
            return

        try:
            # This work is done in a thread since SDK operations are blocking
            self._sdk_client = await self._run_blocking_in_executor(self._create_fabric_sdk_client)
            self._sdk_initialized = True
            self._format_log("info", "Fabric SDK client initialized successfully")
        except Exception as e:
            self._format_log("error", f"Failed to initialize Fabric SDK client: {e}")
            raise DLTClientConfigurationError(
                f"Failed to initialize Fabric SDK client: {e}",
                self.client_type,
                original_exception=e,
            )

    def _create_fabric_sdk_client(self):
        """
        Creates a Fabric SDK client (runs in executor thread).
        """
        try:
            if not FABRIC_NATIVE_AVAILABLE:
                raise ImportError("Fabric SDK is not available")

            # Create the client
            client = FabricSDKClient(net_profile=None)

            # Set up crypto material
            client.new_channel(self.channel_name)

            # Add peers
            for peer_name, peer_cfg in self.client_config["peers"].items():
                kwargs = {"peer_url": str(peer_cfg["url"]), "peer_name": peer_name}
                if peer_cfg.get("tls_cacerts"):
                    kwargs["tls_cacerts"] = peer_cfg["tls_cacerts"]
                if peer_cfg.get("ssl_target_name_override"):
                    kwargs["ssl_target_name_override"] = peer_cfg["ssl_target_name_override"]

                client.new_peer(**kwargs)

            # Add orderers if provided
            if self.client_config.get("orderers"):
                for orderer_name, orderer_cfg in self.client_config["orderers"].items():
                    kwargs = {
                        "orderer_url": str(orderer_cfg["url"]),
                        "orderer_name": orderer_name,
                    }
                    if orderer_cfg.get("tls_cacerts"):
                        kwargs["tls_cacerts"] = orderer_cfg["tls_cacerts"]
                    if orderer_cfg.get("ssl_target_name_override"):
                        kwargs["ssl_target_name_override"] = orderer_cfg["ssl_target_name_override"]

                    client.new_orderer(**kwargs)

            # Set up user identity
            user = self._create_user(client)
            client.set_user(self.msp_id, user)

            return client
        except Exception as e:
            self._format_log("error", f"Error creating Fabric SDK client: {e}")
            raise DLTClientConfigurationError(
                f"Error creating Fabric SDK client: {e}", self.client_type
            )

    def _create_user(self, client):
        """
        Creates a user identity for the Fabric SDK client.
        """
        # Read certificate and private key
        with open(self.cert_path, "rb") as f:
            cert = f.read()
        with open(self.key_path, "rb") as f:
            key = f.read()

        # Create a user object
        user = client.new_user(self.user_name)
        user.enroll(
            name=self.user_name,
            secret=None,
            mspid=self.msp_id,
            cert=cert,
            private_key=key,
        )
        return user

    # ------------- public API -------------

    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Performs a health check by accessing the Fabric network.
        Returns status, message, and details.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": correlation_id or ""},
        ) as span:
            try:
                await self._rate_limit()

                if self.mode == "sdk":
                    # Initialize SDK client if needed
                    await self._init_sdk_client()

                    # Query a simple chaincode function (e.g., GetVersion or similar)
                    # This is a blocking call, so we run it in the executor
                    try:
                        result = await self._run_blocking_in_executor(
                            lambda: self._sdk_client.query_chaincode(
                                requestor=self._sdk_client.get_user(org_name=self.msp_id),
                                channel_name=self.channel_name,
                                peers=[list(self.client_config["peers"].keys())[0]],
                                args=["GetMetadata"],
                                cc_name=self.chaincode_id,
                            )
                        )

                        # Process the result (depends on chaincode implementation)
                        if isinstance(result, bytes):
                            result_str = result.decode("utf-8")
                            result_data = json.loads(result_str)
                        else:
                            result_data = {"raw_result": str(result)}

                        span.set_status(Status(StatusCode.OK))
                        self._format_log(
                            "info",
                            f"Fabric network is reachable. Channel: {self.channel_name}, Chaincode: {self.chaincode_id}",
                            {"correlation_id": correlation_id},
                        )

                        return {
                            "status": True,
                            "message": f"Fabric network is reachable. Channel: {self.channel_name}, Chaincode: {self.chaincode_id}",
                            "details": {"metadata": result_data},
                        }

                    except Exception as e:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                description=f"Chaincode query failed: {e}",
                            )
                        )
                        span.record_exception(e)
                        self._format_log(
                            "error",
                            f"Fabric health check failed: {e}",
                            {"correlation_id": correlation_id},
                        )
                        return {
                            "status": False,
                            "message": f"Fabric health check failed: {e}",
                            "details": {"error": str(e)},
                        }

                else:  # REST mode
                    session = await self._get_session()
                    health_url = f"{self.rest_api_url.rstrip('/')}/health"

                    # Perform request under circuit breaker
                    resp = await self._circuit_breaker.execute(
                        session.get,
                        health_url,
                        timeout=self.client_config["health_check_timeout"],
                    )
                    try:
                        resp.raise_for_status()
                        health_data = await resp.json()
                    except aiohttp.ContentTypeError:
                        health_data = {"status": "ok" if resp.status == 200 else "error"}
                    finally:
                        # Ensure connection is released back to pool
                        try:
                            await resp.release()
                        except Exception:
                            pass

                    if resp.status != 200:
                        msg = f"Fabric REST Gateway responded with status {resp.status}"
                        span.set_status(Status(StatusCode.ERROR, description=msg))
                        self._format_log("error", msg, {"correlation_id": correlation_id})
                        return {
                            "status": False,
                            "message": msg,
                            "details": {"response": health_data},
                        }

                    span.set_status(Status(StatusCode.OK))
                    self._format_log(
                        "info",
                        "Fabric REST Gateway is reachable and responding.",
                        {"correlation_id": correlation_id},
                    )
                    return {
                        "status": True,
                        "message": "Fabric REST Gateway is reachable and responding.",
                        "details": health_data,
                    }

            except aiohttp.ClientResponseError as e:
                span.set_status(Status(StatusCode.ERROR, description=f"HTTP Error: {e.status}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Fabric health check failed with HTTP {e.status}: {e.message}",
                    {"correlation_id": correlation_id},
                )
                if e.status == 401:
                    raise DLTClientAuthError(
                        f"Fabric authentication failed: {e.message}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )
                if e.status in (429, 503):
                    raise DLTClientTransactionError(
                        f"Fabric rate limit or service unavailable: {e.status} - {e.message}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )
                raise DLTClientConnectivityError(
                    f"Fabric REST API responded with error: {e.status} - {e.message}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )
            except asyncio.TimeoutError as e:
                span.set_status(Status(StatusCode.ERROR, description="Timeout"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Fabric health check timed out: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientTimeoutError(
                    f"Fabric health check timed out: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )
            except DLTClientCircuitBreakerError:
                # Already logged/escalated by CB/exception base
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, description=f"Unexpected error: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Unexpected error during Fabric health check: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientError(
                    f"Unexpected error during Fabric health check: {e}",
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
        Writes a checkpoint to the Fabric ledger.
        Returns (transaction_id, off_chain_id, version).
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.write_checkpoint",
            attributes={
                "checkpoint_name": checkpoint_name,
                "hash": hash,
                "prev_hash": prev_hash,
                "correlation_id": correlation_id or "",
                "payload_size": len(payload_blob),
            },
        ) as span:
            try:
                # Save payload off-chain first
                off_chain_id = await self._circuit_breaker.execute(
                    self.off_chain_client.save_blob,
                    checkpoint_name,
                    payload_blob,
                    correlation_id=correlation_id,
                )
                span.set_attribute("off_chain.id", off_chain_id)

                # Start timing the transaction
                start_time = time.time()

                # Prepare transaction arguments
                args = [
                    self.checkpoint_function,
                    checkpoint_name,
                    hash,
                    prev_hash,
                    json.dumps(metadata),
                    off_chain_id,
                    correlation_id or str(uuid.uuid4()),
                ]

                # Execute transaction based on mode
                if self.mode == "sdk":
                    # Initialize SDK client if needed
                    await self._init_sdk_client()

                    # This is a blocking call, so we run it in the executor
                    result = await self._run_blocking_in_executor(
                        lambda: self._sdk_client.invoke_chaincode(
                            requestor=self._sdk_client.get_user(org_name=self.msp_id),
                            channel_name=self.channel_name,
                            peers=[list(self.client_config["peers"].keys())[0]],  # Use first peer
                            args=args,
                            cc_name=self.chaincode_id,
                            wait_for_event=True,
                            transient_map=None,
                            wait_for_event_timeout=self.client_config["invoke_timeout"],
                        )
                    )

                    # Process result
                    if isinstance(result[0], bytes):
                        tx_result = json.loads(result[0].decode("utf-8"))
                    else:
                        tx_result = {"raw_result": str(result[0])}

                    tx_id = str(result[1])

                    # Extract version from result
                    version = tx_result.get("version", int(time.time() * 1000))

                else:  # REST mode
                    session = await self._get_session()
                    invoke_url = f"{self.rest_api_url.rstrip('/')}/v1/invoke"

                    # Prepare request payload
                    payload = {
                        "channelID": self.client_config.get("channel", "mychannel"),
                        "chaincodeName": self.client_config.get("chaincode_id", "checkpoint"),
                        "function": self.checkpoint_function,
                        "args": args[1:],  # First arg was the function name
                    }

                    resp = await self._circuit_breaker.execute(
                        session.post,
                        invoke_url,
                        json=payload,
                        timeout=self.client_config["invoke_timeout"],
                    )
                    try:
                        resp.raise_for_status()
                        response_json = await resp.json()

                        # Extract transaction ID and result
                        tx_id = response_json.get("txid", str(uuid.uuid4()))
                        tx_result = response_json.get("result", {})
                        version = tx_result.get("version", int(time.time() * 1000))

                    except aiohttp.ContentTypeError:
                        # Handle non-JSON response
                        response_text = await resp.text()
                        raise DLTClientTransactionError(
                            f"Invalid response format from Fabric REST API: {response_text}",
                            self.client_type,
                            correlation_id=correlation_id,
                        )
                    finally:
                        try:
                            await resp.release()
                        except Exception:
                            pass

                # Calculate and record transaction duration
                duration = time.time() - start_time
                if FABRIC_METRICS:
                    FABRIC_METRICS["chaincode_calls_total"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.checkpoint_function,
                        status="success",
                    ).inc()
                    FABRIC_METRICS["chaincode_call_duration"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.checkpoint_function,
                    ).observe(duration)

                span.set_attribute("tx_id", tx_id)
                span.set_attribute("version", version)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Fabric checkpoint written: {checkpoint_name} [Tx ID={tx_id}, Version={version}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_id,
                        "version": version,
                    },
                )

                # Audit the transaction
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "fabric_checkpoint.written",
                            checkpoint_name=checkpoint_name,
                            tx_id=tx_id,
                            hash=hash,
                            prev_hash=prev_hash,
                            off_chain_id=off_chain_id,
                            version=version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass

                return tx_id, off_chain_id, version

            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                if FABRIC_METRICS:
                    FABRIC_METRICS["chaincode_calls_total"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.checkpoint_function,
                        status="error",
                    ).inc()
                span.set_status(Status(StatusCode.ERROR, description=f"Fabric write failed: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Fabric write_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientTransactionError(
                    f"Fabric write_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    @async_retry(
        catch_exceptions=(
            DLTClientConnectivityError,
            DLTClientAuthError,
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
        Reads a checkpoint from the Fabric ledger.
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

                # Prepare query arguments
                query_args = [self.query_function, name]

                # Add version if specified and not "latest"
                if version is not None and version != "latest":
                    query_args.append(str(version))

                # Add correlation ID
                query_args.append(correlation_id or str(uuid.uuid4()))

                # Start timing the query
                start_time = time.time()

                # Execute query based on mode
                if self.mode == "sdk":
                    # Initialize SDK client if needed
                    await self._init_sdk_client()

                    # This is a blocking call, so we run it in the executor
                    result = await self._run_blocking_in_executor(
                        lambda: self._sdk_client.query_chaincode(
                            requestor=self._sdk_client.get_user(org_name=self.msp_id),
                            channel_name=self.channel_name,
                            peers=[list(self.client_config["peers"].keys())[0]],  # Use first peer
                            args=query_args,
                            cc_name=self.chaincode_id,
                        )
                    )

                    # Process result
                    if isinstance(result, bytes):
                        result_data = json.loads(result.decode("utf-8"))
                    else:
                        raise DLTClientQueryError(
                            f"Unexpected result type from Fabric query: {type(result)}",
                            self.client_type,
                            correlation_id=correlation_id,
                        )

                else:  # REST mode
                    session = await self._get_session()
                    query_url = f"{self.rest_api_url.rstrip('/')}/v1/query"

                    # Prepare request payload
                    payload = {
                        "channelID": self.client_config.get("channel", "mychannel"),
                        "chaincodeName": self.client_config.get("chaincode_id", "checkpoint"),
                        "function": self.query_function,
                        "args": query_args[1:],  # First arg was the function name
                    }

                    resp = await self._circuit_breaker.execute(
                        session.post,
                        query_url,
                        json=payload,
                        timeout=self.client_config["query_timeout"],
                    )
                    try:
                        resp.raise_for_status()
                        response_json = await resp.json()
                        result_data = response_json.get("result", {})

                    except aiohttp.ContentTypeError:
                        # Handle non-JSON response
                        response_text = await resp.text()
                        raise DLTClientQueryError(
                            f"Invalid response format from Fabric REST API: {response_text}",
                            self.client_type,
                            correlation_id=correlation_id,
                        )
                    finally:
                        try:
                            await resp.release()
                        except Exception:
                            pass

                # Calculate and record query duration
                duration = time.time() - start_time
                if FABRIC_METRICS:
                    FABRIC_METRICS["chaincode_calls_total"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.query_function,
                        status="success",
                    ).inc()
                    FABRIC_METRICS["chaincode_call_duration"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.query_function,
                    ).observe(duration)

                # Check if result is empty or invalid
                if not result_data or not isinstance(result_data, dict):
                    raise FileNotFoundError(
                        f"Checkpoint '{name}' (version {version}) not found or query returned empty result."
                    )

                # Extract metadata
                entry = {
                    "hash": result_data.get("dataHash", ""),
                    "prev_hash": result_data.get("prevHash", ""),
                    "metadata": json.loads(result_data.get("metadataJson", "{}")),
                    "off_chain_ref": result_data.get("offChainRef", ""),
                    "version": int(result_data.get("version", 0)),
                    "tx_id": result_data.get("txId", None),
                }
                span.set_attribute("dlt.entry_hash", entry.get("hash"))

                # Retrieve blob from off-chain storage
                off_chain_id = entry["off_chain_ref"]
                payload_blob = await self._circuit_breaker.execute(
                    self.off_chain_client.get_blob,
                    off_chain_id,
                    correlation_id=correlation_id,
                )

                span.set_status(Status(StatusCode.OK))
                retrieved_version = version if version is not None else entry.get("version")
                self._format_log(
                    "info",
                    f"Fabric checkpoint read: {name} v{retrieved_version} [tx_id={entry.get('tx_id')}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": entry.get("tx_id"),
                        "version": entry.get("version"),
                    },
                )

                # Audit the read
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "fabric_checkpoint.read",
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

            except FileNotFoundError as e:
                span.set_status(
                    Status(
                        StatusCode.ERROR,
                        description="Off-chain blob or entry not found",
                    )
                )
                self._format_log("error", str(e), {"correlation_id": correlation_id})
                raise
            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                if FABRIC_METRICS:
                    FABRIC_METRICS["chaincode_calls_total"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.query_function,
                        status="error",
                    ).inc()
                span.set_status(Status(StatusCode.ERROR, description=f"Fabric read failed: {e}"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Fabric read_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientQueryError(
                    f"Fabric read_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def get_version_tx(
        self, name: str, version: int, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Retrieves a specific version's metadata and payload reference from Fabric."""
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
    async def rollback_checkpoint(
        self, name: str, rollback_hash: str, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs a logical rollback on Fabric.
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
                # Start timing the transaction
                start_time = time.time()

                # Prepare transaction arguments
                args = [
                    self.rollback_function,
                    name,
                    rollback_hash,
                    correlation_id or str(uuid.uuid4()),
                ]

                # Execute transaction based on mode
                if self.mode == "sdk":
                    # Initialize SDK client if needed
                    await self._init_sdk_client()

                    # This is a blocking call, so we run it in the executor
                    result = await self._run_blocking_in_executor(
                        lambda: self._sdk_client.invoke_chaincode(
                            requestor=self._sdk_client.get_user(org_name=self.msp_id),
                            channel_name=self.channel_name,
                            peers=[list(self.client_config["peers"].keys())[0]],  # Use first peer
                            args=args,
                            cc_name=self.chaincode_id,
                            wait_for_event=True,
                            transient_map=None,
                            wait_for_event_timeout=self.client_config["invoke_timeout"],
                        )
                    )

                    # Process result
                    if isinstance(result[0], bytes):
                        tx_result = json.loads(result[0].decode("utf-8"))
                    else:
                        tx_result = {"raw_result": str(result[0])}

                    tx_id = str(result[1])

                else:  # REST mode
                    session = await self._get_session()
                    invoke_url = f"{self.rest_api_url.rstrip('/')}/v1/invoke"

                    # Prepare request payload
                    payload = {
                        "channelID": self.client_config.get("channel", "mychannel"),
                        "chaincodeName": self.client_config.get("chaincode_id", "checkpoint"),
                        "function": self.rollback_function,
                        "args": args[1:],  # First arg was the function name
                    }

                    resp = await self._circuit_breaker.execute(
                        session.post,
                        invoke_url,
                        json=payload,
                        timeout=self.client_config["invoke_timeout"],
                    )
                    try:
                        resp.raise_for_status()
                        response_json = await resp.json()

                        # Extract transaction ID and result
                        tx_id = response_json.get("txid", str(uuid.uuid4()))
                        tx_result = response_json.get("result", {})

                    except aiohttp.ContentTypeError:
                        # Handle non-JSON response
                        response_text = await resp.text()
                        raise DLTClientTransactionError(
                            f"Invalid response format from Fabric REST API: {response_text}",
                            self.client_type,
                            correlation_id=correlation_id,
                        )
                    finally:
                        try:
                            await resp.release()
                        except Exception:
                            pass

                # Calculate and record transaction duration
                duration = time.time() - start_time
                if FABRIC_METRICS:
                    FABRIC_METRICS["chaincode_calls_total"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.rollback_function,
                        status="success",
                    ).inc()
                    FABRIC_METRICS["chaincode_call_duration"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.rollback_function,
                    ).observe(duration)

                # Extract metadata and version from result
                rolled_back_entry = tx_result.get("entry", {})
                new_version = tx_result.get("version", int(time.time() * 1000))

                span.set_attribute("tx_id", tx_id)
                span.set_attribute("new_version", new_version)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Fabric checkpoint rolled back: {name} to hash {rollback_hash} [Tx ID={tx_id}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_id,
                        "version": new_version,
                    },
                )

                # Audit the rollback
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            "fabric_checkpoint.rolled_back",
                            checkpoint_name=name,
                            rollback_hash=rollback_hash,
                            tx_id=tx_id,
                            new_version=new_version,
                            correlation_id=correlation_id,
                        )
                    )
                except RuntimeError:
                    pass

                return {
                    "metadata": rolled_back_entry,
                    "tx_id": tx_id,
                    "version": new_version,
                }

            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                if FABRIC_METRICS:
                    FABRIC_METRICS["chaincode_calls_total"].labels(
                        client_type=self.client_type,
                        channel=self.client_config.get("channel", "mychannel"),
                        chaincode=self.client_config.get("chaincode_id", "checkpoint"),
                        function=self.rollback_function,
                        status="error",
                    ).inc()
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Fabric rollback failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Fabric rollback_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientTransactionError(
                    f"Fabric rollback_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        """
        Closes connections and cleans up resources.
        """
        await super().close()

        # For REST mode, close the aiohttp session
        if self.mode == "rest":
            async with self._session_lock:
                if self._session and not self._session.closed:
                    self._format_log(
                        "info",
                        "Closing Fabric REST client session",
                        {"client_type": self.client_type},
                    )
                    try:
                        if ASYNC_TIMEOUT_AVAILABLE:
                            try:
                                async with async_timeout.timeout(
                                    self.client_config["close_timeout"]
                                ):
                                    await self._session.close()
                            except TypeError:
                                await self._session.close()
                        else:
                            await self._session.close()
                        self._format_log(
                            "info",
                            "Fabric REST client session closed",
                            {"client_type": self.client_type},
                        )
                    except Exception as e:
                        self._format_log(
                            "warning",
                            f"Failed to close Fabric REST client session cleanly: {e}",
                            {"client_type": self.client_type},
                        )
                    finally:
                        self._session = None

        # For SDK mode, no explicit cleanup needed (SDK doesn't expose close methods)
        if self.mode == "sdk" and self._sdk_client:
            self._format_log(
                "info",
                "Fabric SDK client resources released",
                {"client_type": self.client_type},
            )
            self._sdk_client = None
            self._sdk_initialized = False

    def __del__(self):
        """
        Best-effort cleanup if object is garbage collected.
        Avoids raising if no running loop or if object not fully initialized.
        """
        # Check if the object was fully initialized
        if not hasattr(self, "mode"):
            return

        if (
            self.mode == "rest"
            and hasattr(self, "_session")
            and self._session
            and not self._session.closed
        ):
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.close())
            except (RuntimeError, asyncio.exceptions.InvalidStateError):
                # No running event loop; skip
                pass


# --- Plugin System Integration ---
PLUGIN_MANIFEST = {
    "name": "fabric_client",
    "version": "1.0.0",
    "description": "Hyperledger Fabric DLT client supporting both native SDK and REST modes",
    "type": "dlt_client",
    "capabilities": ["dlt_operations"],
    "entry_points": ["register_plugin_entrypoints"],
    "dependencies": ["dlt_base"],
}


def register_plugin_entrypoints(register_func):
    """Register Fabric client plugin entry points with the plugin manager."""
    register_func(
        name="fabric_client_create",
        executor_func=lambda config, off_chain_client, **kwargs: FabricClientWrapper(
            config, off_chain_client
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="fabric_client_health_check",
        executor_func=lambda client, **kwargs: client.health_check(**kwargs),
        capabilities=["dlt_operations"],
    )


# Factory function for creating a new Fabric client
def create_fabric_client(config: Dict[str, Any], off_chain_client) -> FabricClientWrapper:
    """
    Create a new Fabric client instance with the given configuration.

    Args:
        config: Configuration dictionary for the Fabric client
        off_chain_client: An initialized off-chain storage client

    Returns:
        An initialized FabricClientWrapper instance
    """
    return FabricClientWrapper(config, off_chain_client)


# --- Plugin Manager Registration ---
try:
    from ..plugin_manager import PluginManager

    # Auto-register with plugin manager if available
    def _register_with_plugin_manager():
        try:
            plugin_manager = PluginManager.get_instance()
            plugin_manager.register_plugin(
                name="fabric_client",
                module=sys.modules[__name__],
                manifest=PLUGIN_MANIFEST,
            )
            _base_logger.info("Fabric DLT client registered with plugin manager.")
        except Exception as e:
            _base_logger.warning(
                f"Could not auto-register Fabric DLT client with plugin manager: {e}"
            )

    # Only register in production mode
    if PRODUCTION_MODE:
        _register_with_plugin_manager()

except ImportError:
    _base_logger.debug("Plugin manager not available, skipping auto-registration of Fabric client.")
