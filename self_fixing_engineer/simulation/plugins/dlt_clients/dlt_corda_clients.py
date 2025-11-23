"""
Production-ready Corda DLT client using aiohttp against a REST gateway.

Key properties:
- Strong configuration validation with Pydantic.
- Secrets pulled from centralized SecretsManager (no plain-text creds in config).
- Proper circuit-breaker usage and async retry on transient errors.
- Structured logging with optional JSON format and audit trail integration.
- Explicit rate limiting and connection pooling.
- No sys.exit in library code; raises typed exceptions for callers/CLI to handle.
"""

import asyncio
import json
import time
import uuid
import sys
from typing import Any, Dict, Optional, Tuple, Union
from datetime import datetime

import aiohttp

try:
    import async_timeout

    ASYNC_TIMEOUT_AVAILABLE = True
except ImportError:
    ASYNC_TIMEOUT_AVAILABLE = False

from pydantic import (
    BaseModel,
    HttpUrl,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


from .dlt_base import (
    BaseDLTClient,
    BaseOffChainClient,
    DLTClientValidationError,
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


# ---------------------------
# Configuration schema
# ---------------------------
class CordaConfig(BaseModel):
    rpc_url: HttpUrl
    user: str
    password: str
    timeout_seconds: int = Field(30, ge=1, le=300)

    model_config = {"extra": "ignore"}  # tolerate extra keys

    @field_validator("rpc_url", mode="after")
    @classmethod
    def validate_rpc_url_scheme(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme not in ("http", "https"):
            raise ValueError("rpc_url must use http or https")
        return v

    @model_validator(mode="after")
    def _post_validate(self) -> "CordaConfig":
        # add any cross-field checks here if needed
        return self


# ---------------------------
# Client implementation
# ---------------------------
class CordaClientWrapper(BaseDLTClient):
    """
    R3 Corda DLT client using an HTTP-based REST API gateway.
    Supports configurable flows, secure credential handling, and production-grade observability.
    """

    def __init__(self, config: Dict[str, Any], off_chain_client: "BaseOffChainClient"):
        # 1) Build and validate client-specific config
        try:
            corda_cfg: Dict[str, Any] = dict(config.get("corda", {}))

            # Enforce secrets from manager (no plain-text in config)
            corda_cfg["user"] = corda_cfg.get("user") or SECRETS_MANAGER.get_secret(
                "CORDA_USER", required=True
            )
            corda_cfg["password"] = corda_cfg.get(
                "password"
            ) or SECRETS_MANAGER.get_secret("CORDA_PASSWORD", required=True)

            validated_corda = CordaConfig(**corda_cfg).model_dump()

            # Conditionally validate dummy credentials only in production mode.
            # This logic is moved from the Pydantic model to here to be aware of the runtime PRODUCTION_MODE flag.
            if PRODUCTION_MODE:
                forbidden = {"", "dummy", "changeme", "password"}
                user_lower = (validated_corda.get("user") or "").strip().lower()
                password_lower = (validated_corda.get("password") or "").strip().lower()
                if user_lower in forbidden:
                    raise ValueError(
                        "Corda user appears unset or dummy in production mode"
                    )
                if password_lower in forbidden:
                    raise ValueError(
                        "Corda password appears unset or dummy in production mode"
                    )

        except ValidationError as e:
            raise DLTClientValidationError(
                f"Invalid Corda client configuration: {e}", "Corda"
            )
        except Exception as e:
            raise DLTClientValidationError(
                f"Failed to load Corda client secrets or configuration: {e}",
                "Corda",
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
        # Fallback: ensure default_timeout_seconds is present from client config if not provided at base level
        base_cfg.setdefault(
            "default_timeout_seconds", validated_corda["timeout_seconds"]
        )

        super().__init__(base_cfg, off_chain_client)

        # Store client-specific validated config separately
        self.client_config: Dict[str, Any] = validated_corda
        self.client_type = "Corda"

        # Derived/shortcut fields
        self.rpc_url: str = str(self.client_config["rpc_url"])
        self.user: str = self.client_config["user"]
        self.password: str = self.client_config["password"]
        self.flows_api_path: str = "/api/rest/corda/v4/flows"
        self.query_api_path: str = "/api/rest/corda/v4/vault/query"
        self.issue_flow_name: str = "com.example.flow.IssueCheckpointFlow"
        self.query_flow_name: str = "com.example.flow.QueryCheckpointFlow"
        self.rollback_flow_name: str = "com.example.flow.RollbackCheckpointFlow"
        self.max_connections: int = 10
        self.rate_limit_requests_per_second: float = 10.0
        self.log_format: str = "json"

        # HTTP session and rate limiter
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        self._rate_limit_delay = 1.0 / float(self.rate_limit_requests_per_second)
        self._last_request_time = 0.0

        # Logging format
        self._log_format = self.log_format
        self._format_log(
            "info",
            "CordaClientWrapper initialized (REST API)",
            {"rpc_url": self.rpc_url, "max_connections": self.max_connections},
        )

    # ------------- internal helpers -------------

    def _format_log(
        self, level: str, message: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
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
            safe_entry = scrub_secrets(log_entry)
            # Logger adapter already injects client_type in formatter; we just log the JSON string as message.
            getattr(self.logger, level.lower())(json.dumps(safe_entry))
            if level.upper() in ("ERROR", "CRITICAL"):
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"corda_client_error.{level.lower()}",
                            message=message,
                            details=safe_entry,
                        )
                    )
                except RuntimeError:
                    # no running loop; best-effort sync log only
                    pass
        else:
            getattr(self.logger, level.lower())(message, extra=extra)
            if level.upper() in ("ERROR", "CRITICAL"):
                try:
                    asyncio.get_running_loop().create_task(
                        AUDIT.log_event(
                            f"corda_client_error.{level.lower()}",
                            message=message,
                            details=scrub_secrets(extra),
                        )
                    )
                except RuntimeError:
                    pass

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Ensures a single aiohttp client session with connection pooling.
        """
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(
                        total=self.client_config["timeout_seconds"]
                    ),
                    auth=aiohttp.BasicAuth(self.user, self.password),
                    connector=aiohttp.TCPConnector(limit=self.max_connections),
                )
            return self._session

    async def _rate_limit(self) -> None:
        """
        Enforces client-side rate limiting to prevent overloading the Corda node.
        """
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    # ------------- public API -------------

    async def health_check(
        self, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs a health check by accessing a Corda endpoint.
        Returns status, message, and details.
        """
        with TRACER.start_as_current_span(
            f"{self.client_type}.health_check",
            attributes={"correlation_id": correlation_id or ""},
        ) as span:
            try:
                await self._rate_limit()
                session = await self._get_session()
                health_url = f"{self.rpc_url.rstrip('/')}/api/rest/corda/v4/me"

                # Perform request under circuit breaker; manage response manually and release it.
                resp = await self._circuit_breaker.execute(
                    session.get,
                    health_url,
                    timeout=self.client_config["timeout_seconds"],
                )
                try:
                    resp.raise_for_status()
                    node_info = await resp.json()
                finally:
                    # Ensure connection is released back to pool
                    try:
                        await resp.release()
                    except Exception:
                        pass

                if "me" not in node_info:
                    msg = f"Corda Node responded with unexpected data: {node_info}"
                    span.set_status(Status(StatusCode.ERROR, description=msg))
                    self._format_log("error", msg, {"correlation_id": correlation_id})
                    return {
                        "status": False,
                        "message": msg,
                        "details": {"response": scrub_secrets(node_info)},
                    }

                span.set_status(Status(StatusCode.OK))
                node_identity = node_info.get("me", {}).get("legalIdentity", "Unknown")
                self._format_log(
                    "info",
                    f"Corda Node is reachable and authenticated. Node: {node_identity}",
                    {"correlation_id": correlation_id, "node_identity": node_identity},
                )
                return {
                    "status": True,
                    "message": f"Corda Node is reachable and authenticated. Node: {node_identity}",
                    "details": {"node_identity": node_identity},
                }

            except aiohttp.ClientResponseError as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"HTTP Error: {e.status}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Corda health check failed with HTTP {e.status}: {e.message}",
                    {"correlation_id": correlation_id},
                )
                if e.status == 401:
                    raise DLTClientAuthError(
                        f"Corda authentication failed: {e.message}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )
                if e.status in (429, 503):
                    raise DLTClientTransactionError(
                        f"Corda rate limit or service unavailable: {e.status} - {e.message}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )
                raise DLTClientConnectivityError(
                    f"Corda RPC responded with error: {e.status} - {e.message}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )
            except asyncio.TimeoutError as e:
                span.set_status(Status(StatusCode.ERROR, description="Timeout"))
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Corda health check timed out: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientTimeoutError(
                    f"Corda health check timed out: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )
            except DLTClientCircuitBreakerError:
                # Already logged/escalated by CB/exception base
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Unexpected error: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Unexpected error during Corda health check: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientError(
                    f"Unexpected error during Corda health check: {e}",
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
        Writes a checkpoint to Corda via a REST API call to a custom flow.
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

                await self._rate_limit()
                session = await self._get_session()
                flow_url = f"{self.rpc_url.rstrip('/')}{self.flows_api_path}/start"

                flow_args = {
                    "name": self.issue_flow_name,
                    "args": {
                        "checkpointName": checkpoint_name,
                        "dataHash": hash,
                        "prevHash": prev_hash,
                        "metadataJson": json.dumps(metadata),
                        "offChainRef": off_chain_id,
                        "correlationId": correlation_id or str(uuid.uuid4()),
                    },
                }

                resp = await self._circuit_breaker.execute(
                    session.post,
                    flow_url,
                    json=flow_args,
                    timeout=self.client_config["timeout_seconds"],
                )
                try:
                    response_text = await resp.text()
                    try:
                        response_json = json.loads(response_text)
                    except json.JSONDecodeError:
                        response_json = {"message": response_text}

                    if resp.status >= 400:
                        msg = f"Corda Flow failed with HTTP status {resp.status}: {response_json.get('message', 'Unknown error')}"
                        self._format_log(
                            "error", msg, {"correlation_id": correlation_id}
                        )
                        raise DLTClientTransactionError(
                            msg,
                            self.client_type,
                            details=scrub_secrets(response_json),
                            correlation_id=correlation_id,
                        )

                finally:
                    try:
                        await resp.release()
                    except Exception:
                        pass

                tx_id = response_json.get("id", str(uuid.uuid4()))
                return_value = response_json.get("returnValue")
                if return_value and "result" in return_value:
                    version = int(
                        return_value["result"].get("version", int(time.time() * 1000))
                    )
                else:
                    version = int(time.time() * 1000)

                span.set_attribute("tx_id", tx_id)
                span.set_attribute("version", version)
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Corda checkpoint written: {checkpoint_name} [Tx ID={tx_id}, Version={version}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_id,
                        "version": version,
                    },
                )
                return tx_id, off_chain_id, version

            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Corda write failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Corda write_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientTransactionError(
                    f"Corda write_checkpoint failed: {e}",
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
        Reads a checkpoint from Corda via a REST API call to a custom query flow.
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
                session = await self._get_session()
                flow_url = f"{self.rpc_url.rstrip('/')}{self.flows_api_path}/start"

                flow_args = {
                    "name": self.query_flow_name,
                    "args": {
                        "checkpointName": name,
                        "correlationId": correlation_id or str(uuid.uuid4()),
                    },
                }
                if version is not None and version != "latest":
                    flow_args["args"]["version"] = str(version)

                resp = await self._circuit_breaker.execute(
                    session.post,
                    flow_url,
                    json=flow_args,
                    timeout=self.client_config["timeout_seconds"],
                )
                try:
                    response_text = await resp.text()
                    try:
                        response_json = json.loads(response_text)
                    except json.JSONDecodeError:
                        response_json = {"message": response_text}

                    if resp.status >= 400:
                        msg = f"Corda Query Flow failed with HTTP {resp.status}: {response_json.get('message', 'Unknown error')}"
                        self._format_log(
                            "error", msg, {"correlation_id": correlation_id}
                        )
                        raise DLTClientQueryError(
                            msg,
                            self.client_type,
                            details=scrub_secrets(response_json),
                            correlation_id=correlation_id,
                        )
                finally:
                    try:
                        await resp.release()
                    except Exception:
                        pass

                result_data = response_json.get("returnValue", {}).get("result", {})
                if not result_data:
                    raise FileNotFoundError(
                        f"Checkpoint '{name}' (version {version}) not found or query returned empty result."
                    )

                entry = {
                    "hash": result_data.get("dataHash", ""),
                    "prev_hash": result_data.get("prevHash", ""),
                    "metadata": json.loads(result_data.get("metadataJson", "{}")),
                    "off_chain_ref": result_data.get("offChainRef", ""),
                    "version": int(result_data.get("version", 0)),
                    "tx_id": result_data.get("txId", None),
                }
                span.set_attribute("dlt.entry_hash", entry.get("hash"))

                off_chain_id = entry["off_chain_ref"]
                payload_blob = await self._circuit_breaker.execute(
                    self.off_chain_client.get_blob,
                    off_chain_id,
                    correlation_id=correlation_id,
                )

                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Corda checkpoint read: {name} v{version if version is not None else entry.get('version')} [tx_id={entry.get('tx_id')}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": entry.get("tx_id"),
                        "version": entry.get("version"),
                    },
                )
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
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Corda read failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Corda read_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientQueryError(
                    f"Corda read_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def get_version_tx(
        self, name: str, version: int, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Retrieves a specific version's metadata and payload reference from Corda."""
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
        Performs a logical rollback on Corda via a custom flow.
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
                await self._rate_limit()
                session = await self._get_session()
                flow_url = f"{self.rpc_url.rstrip('/')}{self.flows_api_path}/start"

                flow_args = {
                    "name": self.rollback_flow_name,
                    "args": {
                        "checkpointName": name,
                        "targetHash": rollback_hash,
                        "correlationId": correlation_id or str(uuid.uuid4()),
                    },
                }

                resp = await self._circuit_breaker.execute(
                    session.post,
                    flow_url,
                    json=flow_args,
                    timeout=self.client_config["timeout_seconds"],
                )
                try:
                    response_text = await resp.text()
                    try:
                        response_json = json.loads(response_text)
                    except json.JSONDecodeError:
                        response_json = {"message": response_text}

                    if resp.status >= 400:
                        msg = f"Corda Rollback Flow failed with HTTP {resp.status}: {response_json.get('message', 'Unknown error')}"
                        self._format_log(
                            "error", msg, {"correlation_id": correlation_id}
                        )
                        raise DLTClientTransactionError(
                            msg,
                            self.client_type,
                            details=scrub_secrets(response_json),
                            correlation_id=correlation_id,
                        )
                finally:
                    try:
                        await resp.release()
                    except Exception:
                        pass

                tx_id = response_json.get("id", str(uuid.uuid4()))
                rolled_back_entry = response_json.get("returnValue", {}).get(
                    "result", {}
                )

                span.set_attribute("tx_id", tx_id)
                span.set_attribute(
                    "new_version", rolled_back_entry.get("version", "N/A")
                )
                span.set_status(Status(StatusCode.OK))
                self._format_log(
                    "info",
                    f"Corda checkpoint rolled back: {name} to hash {rollback_hash} [Tx ID={tx_id}]",
                    {
                        "correlation_id": correlation_id,
                        "tx_id": tx_id,
                        "version": rolled_back_entry.get("version", "N/A"),
                    },
                )
                return {
                    "metadata": rolled_back_entry,
                    "tx_id": tx_id,
                    "version": rolled_back_entry.get("version", None),
                }

            except DLTClientCircuitBreakerError:
                raise
            except Exception as e:
                span.set_status(
                    Status(StatusCode.ERROR, description=f"Corda rollback failed: {e}")
                )
                span.record_exception(e)
                self._format_log(
                    "error",
                    f"Corda rollback_checkpoint failed: {e}",
                    {"correlation_id": correlation_id},
                )
                raise DLTClientTransactionError(
                    f"Corda rollback_checkpoint failed: {e}",
                    self.client_type,
                    original_exception=e,
                    correlation_id=correlation_id,
                )

    async def close(self) -> None:
        """
        Closes the aiohttp client session for Corda with a timeout.
        Idempotent to handle multiple calls safely.
        """
        await super().close()
        async with self._session_lock:
            if self._session and not self._session.closed:
                self._format_log(
                    "info",
                    f"{self.client_type} aiohttp session closing",
                    {"client_type": self.client_type},
                )
                try:
                    if ASYNC_TIMEOUT_AVAILABLE:
                        try:
                            # async-timeout 4.x supports context manager
                            async with async_timeout.timeout(
                                self.client_config["timeout_seconds"]
                            ):
                                await self._session.close()
                        except TypeError:
                            # If version mismatch, fall back to a simple wait
                            await self._session.close()
                    else:
                        await self._session.close()
                    self._format_log(
                        "info",
                        f"{self.client_type} aiohttp session closed",
                        {"client_type": self.client_type},
                    )
                except Exception as e:
                    self._format_log(
                        "warning",
                        f"Failed to close aiohttp session cleanly: {e}",
                        {"client_type": self.client_type},
                    )
                finally:
                    self._session = None

    def __del__(self):
        """
        Best-effort session cleanup if object is garbage collected.
        Avoids raising if no running loop.
        """
        if hasattr(self, "_session") and self._session and not self._session.closed:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.close())
            except (RuntimeError, asyncio.exceptions.InvalidStateError):
                # No running event loop; skip
                pass


# --- Plugin System Integration ---
PLUGIN_MANIFEST = {
    "name": "corda_client",
    "version": "1.0.0",
    "description": "Corda DLT client using REST API gateway",
    "type": "dlt_client",
    "capabilities": ["dlt_operations"],
    "entry_points": ["register_plugin_entrypoints"],
    "dependencies": ["dlt_base"],
}


def register_plugin_entrypoints(register_func):
    """Register Corda client plugin entry points with the plugin manager."""
    register_func(
        name="corda_client_create",
        executor_func=lambda config, off_chain_client, **kwargs: CordaClientWrapper(
            config, off_chain_client
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="corda_client_health_check",
        executor_func=lambda client, **kwargs: client.health_check(**kwargs),
        capabilities=["dlt_operations"],
    )


# Factory function for creating a new Corda client
def create_corda_client(config: Dict[str, Any], off_chain_client):
    """
    Create a new Corda client instance with the given configuration.

    Args:
        config: Configuration dictionary for the Corda client
        off_chain_client: An initialized off-chain storage client

    Returns:
        An initialized CordaClientWrapper instance
    """
    return CordaClientWrapper(config, off_chain_client)


# --- Plugin Manager Registration ---
try:
    from ..plugin_manager import PluginManager

    # Auto-register with plugin manager if available
    def _register_with_plugin_manager():
        try:
            plugin_manager = PluginManager.get_instance()
            plugin_manager.register_plugin(
                name="corda_client",
                module=sys.modules[__name__],
                manifest=PLUGIN_MANIFEST,
            )
            _base_logger.info("Corda DLT client registered with plugin manager.")
        except Exception as e:
            _base_logger.warning(
                f"Could not auto-register Corda DLT client with plugin manager: {e}"
            )

    # Only register in production mode
    if PRODUCTION_MODE:
        _register_with_plugin_manager()
except ImportError:
    _base_logger.debug(
        "Plugin manager not available, skipping auto-registration of Corda client."
    )
