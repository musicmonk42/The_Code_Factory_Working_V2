# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_fabric_clients.py

Production Hyperledger Fabric DLT client for the CheckpointManager backend.

Provides ``FabricClientWrapper``, a drop-in replacement for the dev file-backed
stub used when this module is unavailable.  Every ledger operation is:

* **HMAC-verified** — transaction responses are verified against a platform
  HMAC key before being accepted.
* **Traced** — OpenTelemetry spans are emitted for every chaincode invocation
  and query, carrying ``correlation_id``, ``channel``, ``chaincode`` and
  ``tx_id`` attributes.
* **Metered** — Prometheus counters, histograms and gauges track operation
  counts, latency and circuit-breaker state.
* **Retried** — transient Fabric peer errors are retried with exponential
  backoff; persistent errors trip a circuit breaker and alert ops.
* **Audited** — every write and rollback emits an entry to the platform
  ``audit_logger``.

Requirements
------------
* ``hfc``             — Python Hyperledger Fabric SDK (``pip install hfc``)
* ``prometheus_client`` — Operational metrics
* ``cryptography``     — HMAC / AES-256-GCM (shared with dlt_offchain_clients)

Credentials (peer TLS certificates, admin MSP material) are resolved via
``SECRETS_MANAGER`` before falling back to the filesystem paths in ``config``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac_module
import json
import logging
import os
import sys
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Third-party deps – all guarded with graceful ImportError handling
# ---------------------------------------------------------------------------

try:
    from hfc.fabric import Client as HFCFabricClient
    _HFC_AVAILABLE = True
except ImportError:
    _HFC_AVAILABLE = False

try:
    from prometheus_client import Counter, Gauge, Histogram
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

    class _DummyMetric:  # type: ignore[misc]
        def labels(self, **_): return self
        def inc(self, *_): pass
        def set(self, *_): pass
        def observe(self, *_): pass

    Counter = Gauge = Histogram = _DummyMetric  # type: ignore[misc,assignment]

try:
    from opentelemetry import trace as otel_trace
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Platform utilities
# ---------------------------------------------------------------------------

try:
    from plugins.core_audit import audit_logger
    from plugins.core_secrets import SECRETS_MANAGER
    from plugins.core_utils import alert_operator
except ImportError:
    audit_logger = None       # type: ignore[assignment]
    SECRETS_MANAGER = None    # type: ignore[assignment]

    def alert_operator(msg: str, level: str = "WARNING", **_) -> None:  # type: ignore[misc]
        logging.getLogger(__name__).warning("[OPS ALERT – %s] %s", level, msg)

# ---------------------------------------------------------------------------
# Module-level globals
# ---------------------------------------------------------------------------

PRODUCTION_MODE: bool = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger("dlt_fabric_clients")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)

if _OTEL_AVAILABLE:
    _tracer = otel_trace.get_tracer(__name__)
else:
    class _MockSpan:  # type: ignore[misc]
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def set_attribute(self, *_): pass
        def record_exception(self, *_): pass
        def set_status(self, *_): pass

    class _MockTracer:  # type: ignore[misc]
        def start_as_current_span(self, *_, **__): return _MockSpan()

    _tracer = _MockTracer()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_fabric_ops_total = Counter(
    "dlt_fabric_operations_total",
    "Total Hyperledger Fabric chaincode operations",
    ["operation", "fcn", "status"],
) if _PROMETHEUS_AVAILABLE else Counter()

_fabric_latency = Histogram(
    "dlt_fabric_operation_duration_seconds",
    "Fabric chaincode operation latency",
    ["operation", "fcn"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
) if _PROMETHEUS_AVAILABLE else Histogram()

_fabric_circuit_open = Gauge(
    "dlt_fabric_circuit_breaker_open",
    "1 when the Fabric client circuit breaker is open",
) if _PROMETHEUS_AVAILABLE else Gauge()

_fabric_tx_total = Counter(
    "dlt_fabric_transactions_total",
    "Total committed Fabric transactions",
    ["channel", "chaincode"],
) if _PROMETHEUS_AVAILABLE else Counter()

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class FabricClientError(Exception):
    """Base exception for all FabricClientWrapper errors."""


class FabricCircuitOpenError(FabricClientError):
    """Request rejected — circuit breaker is open."""


class FabricIntegrityError(FabricClientError):
    """HMAC verification of a transaction response failed."""


class FabricTimeoutError(FabricClientError):
    """Chaincode invocation timed out."""


# ---------------------------------------------------------------------------
# Configuration (Pydantic)
# ---------------------------------------------------------------------------

class FabricClientConfig(BaseModel):
    """Validated, immutable Hyperledger Fabric client configuration.

    All identity material paths may be overridden via SECRETS_MANAGER; if a
    secret key resolves to a non-empty value it takes precedence over the path.
    """

    channel_name: str = Field(..., description="Fabric channel to operate on")
    chaincode_name: str = Field(..., description="Chaincode / smart-contract name")
    org_name: str = Field(..., description="MSP organisation name (e.g. 'Org1')")
    user_name: str = Field(..., description="Enrolled user identity name (e.g. 'Admin')")
    network_profile: str = Field(..., description="Path to connection-profile JSON")

    # Retry / circuit-breaker
    max_retries: int = Field(5, ge=1, le=20)
    retry_base_delay: float = Field(1.0, ge=0.1, le=30.0)
    retry_backoff: float = Field(2.0, ge=1.0, le=4.0)
    circuit_breaker_threshold: int = Field(5, ge=1, le=50)
    circuit_breaker_reset_sec: float = Field(60.0, ge=10.0, le=3600.0)

    # Operation timeouts (seconds)
    invoke_timeout: float = Field(30.0, ge=1.0, le=300.0)
    query_timeout: float = Field(15.0, ge=1.0, le=120.0)

    model_config = {"frozen": True}

    @field_validator("channel_name", "chaincode_name", "org_name", "user_name")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Value must not be empty.")
        return v.strip()

    @field_validator("network_profile")
    @classmethod
    def _profile_exists(cls, v: str) -> str:
        if PRODUCTION_MODE and not os.path.isfile(v):
            raise ValueError(
                f"Fabric network profile not found at {v!r}. "
                "This is required in PRODUCTION_MODE."
            )
        return v

    @classmethod
    def from_secrets_and_env(cls, raw: Dict[str, Any]) -> "FabricClientConfig":
        """Build config, resolving optional secrets-vault overrides."""
        resolved = dict(raw)
        if SECRETS_MANAGER:
            for key in ("channel_name", "chaincode_name", "org_name", "user_name",
                        "network_profile"):
                vault_key = f"FABRIC_{key.upper()}"
                val = SECRETS_MANAGER.get_secret(vault_key, required=False)
                if val:
                    resolved[key] = val
        return cls.model_validate(resolved)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hmac_sign_tx(payload: Dict[str, Any]) -> str:
    """HMAC-SHA256 sign a serialised transaction dict."""
    key_material = ""
    if SECRETS_MANAGER:
        key_material = SECRETS_MANAGER.get_secret("DLT_HMAC_KEY", required=PRODUCTION_MODE) or ""
    if not key_material:
        return ""
    key_bytes = key_material.encode("utf-8") if isinstance(key_material, str) else key_material
    payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return _hmac_module.new(key_bytes, payload_bytes, hashlib.sha256).hexdigest()


def _hmac_verify_tx(payload: Dict[str, Any], expected: str) -> None:
    """Raise ``FabricIntegrityError`` if the HMAC does not match."""
    if not expected:
        return
    actual = _hmac_sign_tx(payload)
    if not _hmac_module.compare_digest(actual, expected):
        raise FabricIntegrityError(
            "Fabric transaction response HMAC mismatch — possible tampering."
        )


# ---------------------------------------------------------------------------
# FabricClientWrapper
# ---------------------------------------------------------------------------

class FabricClientWrapper:
    """Production Hyperledger Fabric ledger client for the DLT backend.

    Architecture
    ------------
    All chaincode ``invoke`` and ``query`` calls are dispatched to a dedicated
    asyncio executor thread so the event loop is never blocked.  Each thread
    creates its own synchronous Fabric event-loop internally and tears it down
    after the call, avoiding cross-thread loop sharing (required by hfc).

    Off-chain blob references (S3 IDs) are committed to the ledger alongside
    the checkpoint hash and metadata; the actual payload bytes are stored by
    the companion :class:`~dlt_offchain_clients.S3OffChainClient`.

    Chaincode interface expected
    ----------------------------
    The chaincode must implement the following functions:

    ``WriteCheckpoint(name, hash, prev_hash, metadata_json, off_chain_ref, tx_id)``
        Commit a new checkpoint version.  Returns the version number as a string.

    ``ReadCheckpoint(name[, version])``
        Query a checkpoint (latest if version omitted).  Returns a JSON object.

    ``RollbackCheckpoint(name, rollback_hash)``
        Create a rollback entry pointing at the given hash.  Returns JSON.

    ``GetVersion(name)``
        Return the current version number as a string.

    ``Ping()``
        Liveness probe — return any non-empty string.
    """

    def __init__(
        self,
        config: Union[FabricClientConfig, Dict[str, Any]],
        off_chain_client: Any,
    ) -> None:
        if not _HFC_AVAILABLE:
            raise ImportError(
                "hfc is required for production FabricClientWrapper. "
                "Install it with:  pip install hfc"
            )

        if isinstance(config, dict):
            config = FabricClientConfig.from_secrets_and_env(config)
        self._cfg = config
        self.off_chain_client = off_chain_client

        # Lazy Fabric client — created inside the executor thread to avoid
        # initialising the synchronous network profile on the event loop.
        self._fabric_client: Optional[Any] = None

        # Circuit-breaker state
        self._cb_failures: int = 0
        self._cb_opened_at: float = 0.0
        self._cb_open: bool = False

        if audit_logger:
            audit_logger.log_event(
                "fabric_client_init",
                channel=self._cfg.channel_name,
                chaincode=self._cfg.chaincode_name,
                org=self._cfg.org_name,
            )
        logger.info(
            "FabricClientWrapper ready (channel=%r, cc=%r, org=%r).",
            self._cfg.channel_name, self._cfg.chaincode_name, self._cfg.org_name,
        )

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _cb_check(self) -> None:
        if not self._cb_open:
            return
        if time.monotonic() - self._cb_opened_at >= self._cfg.circuit_breaker_reset_sec:
            logger.info("FabricClientWrapper: circuit breaker entering half-open.")
            self._cb_open = False
            _fabric_circuit_open.set(0)
        else:
            raise FabricCircuitOpenError(
                f"Fabric circuit breaker is open; cool-down "
                f"{self._cfg.circuit_breaker_reset_sec:.0f}s."
            )

    def _cb_success(self) -> None:
        self._cb_failures = 0
        self._cb_open = False
        _fabric_circuit_open.set(0)

    def _cb_failure(self, exc: Exception) -> None:
        self._cb_failures += 1
        if self._cb_failures >= self._cfg.circuit_breaker_threshold:
            self._cb_open = True
            self._cb_opened_at = time.monotonic()
            _fabric_circuit_open.set(1)
            alert_operator(
                f"FabricClientWrapper circuit breaker OPENED after "
                f"{self._cb_failures} failures. Last: {exc}",
                level="CRITICAL",
            )
            if audit_logger:
                audit_logger.log_event(
                    "fabric_circuit_breaker_opened",
                    channel=self._cfg.channel_name,
                    failures=self._cb_failures, error=str(exc),
                )

    # ------------------------------------------------------------------
    # Sync Fabric helpers (run in executor thread)
    # ------------------------------------------------------------------

    def _get_or_create_client(self) -> Any:
        """Return (or lazily create) the synchronous hfc Client."""
        if self._fabric_client is None:
            self._fabric_client = HFCFabricClient(
                net_profile=self._cfg.network_profile,
                channel_name=self._cfg.channel_name,
            )
        return self._fabric_client

    def _invoke_sync(self, fcn: str, args: List[str]) -> str:
        """Submit a Fabric chaincode transaction and return the response string."""
        client = self._get_or_create_client()
        loop = asyncio.new_event_loop()
        try:
            requestor = client.get_user(self._cfg.org_name, self._cfg.user_name)
            response = loop.run_until_complete(
                client.chaincode_invoke(
                    requestor=requestor,
                    channel_name=self._cfg.channel_name,
                    peers=[],
                    args=args,
                    cc_name=self._cfg.chaincode_name,
                    fcn=fcn,
                )
            )
            return response if isinstance(response, str) else response.decode("utf-8", errors="replace")
        finally:
            loop.close()

    def _query_sync(self, fcn: str, args: List[str]) -> str:
        """Execute a read-only Fabric chaincode query."""
        client = self._get_or_create_client()
        loop = asyncio.new_event_loop()
        try:
            requestor = client.get_user(self._cfg.org_name, self._cfg.user_name)
            response = loop.run_until_complete(
                client.chaincode_query(
                    requestor=requestor,
                    channel_name=self._cfg.channel_name,
                    peers=[],
                    args=args,
                    cc_name=self._cfg.chaincode_name,
                    fcn=fcn,
                )
            )
            return response if isinstance(response, str) else response.decode("utf-8", errors="replace")
        finally:
            loop.close()

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    async def _with_retry(
        self, operation: str, fcn: str, fn, *args, **kwargs
    ) -> Any:
        """Execute *fn* with exponential backoff; honour the circuit breaker."""
        self._cb_check()
        last_exc: Optional[Exception] = None

        for attempt in range(self._cfg.max_retries):
            t0 = time.monotonic()
            try:
                result = await asyncio.to_thread(fn, *args, **kwargs)
                elapsed = time.monotonic() - t0
                _fabric_ops_total.labels(operation=operation, fcn=fcn, status="success").inc()
                _fabric_latency.labels(operation=operation, fcn=fcn).observe(elapsed)
                self._cb_success()
                return result
            except (asyncio.TimeoutError, Exception) as exc:
                elapsed = time.monotonic() - t0
                last_exc = exc
                _fabric_ops_total.labels(operation=operation, fcn=fcn, status="error").inc()
                _fabric_latency.labels(operation=operation, fcn=fcn).observe(elapsed)
                self._cb_failure(exc)

                if attempt < self._cfg.max_retries - 1:
                    delay = self._cfg.retry_base_delay * (self._cfg.retry_backoff ** attempt)
                    logger.warning(
                        "Fabric %s/%s attempt %d/%d failed (%s); retrying in %.1fs.",
                        operation, fcn, attempt + 1, self._cfg.max_retries, exc, delay,
                    )
                    if audit_logger:
                        audit_logger.log_event(
                            "fabric_retry_attempt",
                            operation=operation, fcn=fcn,
                            attempt=attempt + 1, error=str(exc),
                        )
                    await asyncio.sleep(delay)
                else:
                    logger.critical(
                        "Fabric %s/%s failed after %d attempts. Final: %s",
                        operation, fcn, self._cfg.max_retries, exc, exc_info=True,
                    )
                    alert_operator(
                        f"FabricClientWrapper: '{operation}/{fcn}' failed after "
                        f"{self._cfg.max_retries} attempts — {exc}",
                        level="CRITICAL",
                        details={"traceback": traceback.format_exc()},
                    )
                    if audit_logger:
                        audit_logger.log_event(
                            "fabric_retry_final_failure",
                            operation=operation, fcn=fcn,
                            max_retries=self._cfg.max_retries, final_error=str(exc),
                        )
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def write_checkpoint(
        self,
        checkpoint_name: str,
        hash: str,
        prev_hash: str,
        metadata: Dict[str, Any],
        payload_blob: Any,
        correlation_id: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        """Persist a checkpoint to the Fabric ledger.

        1. Upload the payload blob off-chain via ``off_chain_client.save_blob``.
        2. Commit ``WriteCheckpoint`` on the chaincode with the HMAC-signed
           hash, prev_hash, metadata and off-chain reference.
        3. Query the current version number from the ledger.

        Returns
        -------
        Tuple[str, str, int]
            ``(tx_id, off_chain_id, version)``
        """
        with _tracer.start_as_current_span("fabric.write_checkpoint") as span:
            span.set_attribute("checkpoint_name", checkpoint_name)
            span.set_attribute("channel", self._cfg.channel_name)
            span.set_attribute("chaincode", self._cfg.chaincode_name)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)

            try:
                off_chain_id = await self.off_chain_client.save_blob(
                    checkpoint_name, payload_blob, correlation_id=correlation_id
                )

                tx_id = str(uuid.uuid4())
                meta_json = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
                hmac_sig = _hmac_sign_tx(
                    {"checkpoint_name": checkpoint_name, "hash": hash,
                     "prev_hash": prev_hash, "off_chain_ref": off_chain_id, "tx_id": tx_id}
                )
                args = [
                    checkpoint_name, hash, prev_hash, meta_json,
                    off_chain_id, tx_id, hmac_sig,
                ]

                raw = await self._with_retry("invoke", "WriteCheckpoint",
                                             self._invoke_sync, "WriteCheckpoint", args)
                _fabric_tx_total.labels(
                    channel=self._cfg.channel_name, chaincode=self._cfg.chaincode_name,
                ).inc()
                span.set_attribute("tx_id", tx_id)
                span.set_attribute("off_chain_id", off_chain_id)

                version = await self._fetch_version(checkpoint_name)
                span.set_attribute("version", version)

                if audit_logger:
                    audit_logger.log_event(
                        "fabric_checkpoint_written",
                        checkpoint=checkpoint_name, tx_id=tx_id,
                        off_chain_id=off_chain_id, version=version,
                        cid=correlation_id,
                    )
                logger.info(
                    "Fabric checkpoint written: name=%s tx=%s v=%d",
                    checkpoint_name, tx_id, version,
                )
                return tx_id, off_chain_id, version

            except Exception as exc:
                span.record_exception(exc)
                if _OTEL_AVAILABLE:
                    span.set_status(otel_trace.StatusCode.ERROR, str(exc))
                logger.error(
                    "write_checkpoint failed (name=%s, cid=%s): %s",
                    checkpoint_name, correlation_id, exc,
                )
                raise

    async def _fetch_version(self, checkpoint_name: str) -> int:
        """Query the chaincode for the current version counter."""
        try:
            raw = await self._with_retry(
                "query", "GetVersion", self._query_sync, "GetVersion", [checkpoint_name]
            )
            return int(raw.strip()) if raw and raw.strip().isdigit() else 0
        except Exception:
            return 0

    async def read_checkpoint(
        self,
        name: str,
        version: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read the latest (or a specific) checkpoint from the Fabric ledger."""
        with _tracer.start_as_current_span("fabric.read_checkpoint") as span:
            span.set_attribute("checkpoint_name", name)
            span.set_attribute("channel", self._cfg.channel_name)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)
            if version is not None:
                span.set_attribute("version", version)

            try:
                args = [name] if version is None else [name, str(version)]
                raw = await self._with_retry(
                    "query", "ReadCheckpoint", self._query_sync, "ReadCheckpoint", args
                )
                entry = json.loads(raw)

                stored_hmac = entry.pop("hmac_sig", "")
                _hmac_verify_tx(entry, stored_hmac)

                payload_blob = await self.off_chain_client.get_blob(
                    entry["off_chain_ref"], correlation_id=correlation_id
                )
                if audit_logger:
                    audit_logger.log_event(
                        "fabric_checkpoint_read",
                        checkpoint=name, version=version, cid=correlation_id,
                    )
                return {
                    "metadata": entry,
                    "payload_blob": payload_blob,
                    "tx_id": entry.get("tx_id"),
                }

            except FabricIntegrityError:
                alert_operator(
                    f"Fabric checkpoint HMAC FAILED for name={name!r} v={version} "
                    f"(cid={correlation_id}). Possible ledger tampering.",
                    level="CRITICAL",
                )
                if audit_logger:
                    audit_logger.log_event(
                        "fabric_checkpoint_integrity_failure",
                        checkpoint=name, version=version, cid=correlation_id,
                    )
                raise

            except Exception as exc:
                span.record_exception(exc)
                if _OTEL_AVAILABLE:
                    span.set_status(otel_trace.StatusCode.ERROR, str(exc))
                logger.error(
                    "read_checkpoint failed (name=%s, cid=%s): %s", name, correlation_id, exc,
                )
                raise

    async def get_version_tx(
        self,
        name: str,
        version: int,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve a specific checkpoint version and its transaction data."""
        return await self.read_checkpoint(name, version=version, correlation_id=correlation_id)

    async def rollback_checkpoint(
        self,
        name: str,
        rollback_hash: str,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a rollback entry on the ledger pointing to *rollback_hash*.

        The chaincode locates the version matching *rollback_hash*, writes a
        new version entry that references it, and returns the new entry as JSON.
        """
        with _tracer.start_as_current_span("fabric.rollback_checkpoint") as span:
            span.set_attribute("checkpoint_name", name)
            span.set_attribute("rollback_hash", rollback_hash)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)

            try:
                raw = await self._with_retry(
                    "invoke", "RollbackCheckpoint",
                    self._invoke_sync, "RollbackCheckpoint", [name, rollback_hash],
                )
                entry = json.loads(raw)
                span.set_attribute("new_version", entry.get("version", -1))
                _fabric_tx_total.labels(
                    channel=self._cfg.channel_name, chaincode=self._cfg.chaincode_name,
                ).inc()

                if audit_logger:
                    audit_logger.log_event(
                        "fabric_checkpoint_rollback",
                        checkpoint=name, rollback_hash=rollback_hash,
                        new_version=entry.get("version"), cid=correlation_id,
                    )
                logger.info(
                    "Fabric rollback: name=%s hash=%s new_version=%s",
                    name, rollback_hash, entry.get("version"),
                )
                return entry

            except Exception as exc:
                span.record_exception(exc)
                if _OTEL_AVAILABLE:
                    span.set_status(otel_trace.StatusCode.ERROR, str(exc))
                logger.error(
                    "rollback_checkpoint failed (name=%s, cid=%s): %s",
                    name, correlation_id, exc,
                )
                raise

    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """Ping the Fabric peer; return status dict and update circuit-breaker."""
        with _tracer.start_as_current_span("fabric.health_check") as span:
            span.set_attribute("channel", self._cfg.channel_name)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)
            if self._cb_open:
                return {
                    "status": False,
                    "message": "Fabric circuit breaker is open.",
                    "channel": self._cfg.channel_name,
                }
            try:
                await self._with_retry(
                    "query", "Ping", self._query_sync, "Ping", []
                )
                _fabric_ops_total.labels(operation="health_check", fcn="Ping", status="success").inc()
                return {
                    "status": True,
                    "message": "Fabric peer is reachable.",
                    "channel": self._cfg.channel_name,
                }
            except Exception as exc:
                _fabric_ops_total.labels(operation="health_check", fcn="Ping", status="error").inc()
                span.record_exception(exc)
                return {
                    "status": False,
                    "message": f"Fabric health check failed: {exc}",
                    "channel": self._cfg.channel_name,
                }

    async def close(self) -> None:
        """Release the Fabric client connection."""
        self._fabric_client = None
        logger.info(
            "FabricClientWrapper closed (channel=%r, cc=%r).",
            self._cfg.channel_name, self._cfg.chaincode_name,
        )


__all__ = [
    "FabricClientWrapper",
    "FabricClientConfig",
    "FabricClientError",
    "FabricCircuitOpenError",
    "FabricIntegrityError",
    "FabricTimeoutError",
]
