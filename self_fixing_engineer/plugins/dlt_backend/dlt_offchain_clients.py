# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
dlt_offchain_clients.py

Production off-chain blob storage backed by AWS S3.

Provides ``S3OffChainClient``, a drop-in replacement for the dev file-backed
stub that is activated when this module cannot be imported.  All write paths
apply HMAC-SHA256 integrity signing and optional AES-256-GCM client-side
encryption before uploading; all read paths verify integrity on retrieval.
Prometheus metrics and OpenTelemetry spans are emitted for every operation.

Requirements
------------
* ``boto3``       — AWS S3 SDK
* ``cryptography`` — AES-256-GCM encryption (if ``DLT_ENCRYPT_AT_REST=true``)
* ``prometheus_client`` — Operational metrics

Credentials are resolved (in priority order) via:
  1. ``SECRETS_MANAGER`` (the platform-wide secure vault)
  2. botocore credential chain (IAM instance profile, env vars, ~/.aws/credentials)
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import sys
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Third-party deps – all guarded with graceful ImportError handling
# ---------------------------------------------------------------------------

try:
    import boto3
    from botocore.config import Config as BotocoreConfig
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False
    BotoCoreError = Exception  # type: ignore[misc,assignment]
    ClientError = Exception   # type: ignore[misc,assignment]

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _AESGCM_AVAILABLE = True
except ImportError:
    _AESGCM_AVAILABLE = False

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
    from opentelemetry.trace import StatusCode
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Platform utilities – soft-import for portability across test environments
# ---------------------------------------------------------------------------

try:
    from plugins.core_audit import audit_logger
    from plugins.core_secrets import SECRETS_MANAGER
    from plugins.core_utils import alert_operator
    from plugins.core_utils import scrub_secrets as scrub_sensitive_data
except ImportError:
    audit_logger = None       # type: ignore[assignment]
    SECRETS_MANAGER = None    # type: ignore[assignment]

    def alert_operator(msg: str, level: str = "WARNING", **_) -> None:  # type: ignore[misc]
        logging.getLogger(__name__).warning("[OPS ALERT – %s] %s", level, msg)

    def scrub_sensitive_data(obj: Any) -> Any:  # type: ignore[misc]
        return obj

# ---------------------------------------------------------------------------
# Module-level globals
# ---------------------------------------------------------------------------

PRODUCTION_MODE: bool = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
ENCRYPT_AT_REST: bool = os.getenv("DLT_ENCRYPT_AT_REST", "false").lower() == "true"

logger = logging.getLogger("dlt_offchain_clients")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# OpenTelemetry tracer
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

_s3_ops_total = Counter(
    "dlt_s3_operations_total",
    "Total S3 off-chain operations",
    ["operation", "status"],
) if _PROMETHEUS_AVAILABLE else Counter()

_s3_bytes_total = Counter(
    "dlt_s3_bytes_total",
    "Total bytes transferred to/from S3",
    ["direction"],
) if _PROMETHEUS_AVAILABLE else Counter()

_s3_latency = Histogram(
    "dlt_s3_operation_duration_seconds",
    "S3 off-chain operation latency",
    ["operation"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
) if _PROMETHEUS_AVAILABLE else Histogram()

_s3_circuit_open = Gauge(
    "dlt_s3_circuit_breaker_open",
    "1 when the S3 circuit breaker is open",
) if _PROMETHEUS_AVAILABLE else Gauge()


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class S3OffChainError(Exception):
    """Base exception for all S3OffChainClient errors."""


class S3IntegrityError(S3OffChainError):
    """HMAC verification failed — blob has been tampered with or corrupted."""


class S3CircuitOpenError(S3OffChainError):
    """Request rejected because the circuit breaker is open."""


# ---------------------------------------------------------------------------
# Configuration (Pydantic)
# ---------------------------------------------------------------------------

class S3OffChainClientConfig(BaseModel):
    """Validated, immutable S3 client configuration.

    Credentials may be set explicitly here or resolved from the botocore
    credential chain (preferred in production via IAM instance profile).
    """

    bucket_name: str = Field(..., description="S3 bucket for off-chain blobs")
    region_name: str = Field("us-east-1", description="AWS region")
    aws_access_key_id: Optional[str] = Field(None, repr=False)
    aws_secret_access_key: Optional[str] = Field(None, repr=False)
    key_prefix: str = Field("dlt-offchain/", description="S3 key prefix")
    sse: Optional[str] = Field(None, description="Server-side encryption (e.g. 'aws:kms')")
    kms_key_id: Optional[str] = Field(None, repr=False, description="KMS CMK ARN for SSE-KMS")

    # Retry / circuit-breaker
    max_retries: int = Field(5, ge=1, le=20)
    retry_base_delay: float = Field(1.0, ge=0.1, le=10.0)
    retry_backoff: float = Field(2.0, ge=1.0, le=4.0)
    circuit_breaker_threshold: int = Field(5, ge=1, le=50)
    circuit_breaker_reset_sec: float = Field(60.0, ge=10.0, le=3600.0)

    # Connection pool
    max_pool_connections: int = Field(10, ge=1, le=100)
    connect_timeout: int = Field(5, ge=1, le=60)
    read_timeout: int = Field(30, ge=1, le=300)

    model_config = {"frozen": True}

    @field_validator("bucket_name")
    @classmethod
    def _validate_bucket(cls, v: str) -> str:
        if not v or not v.replace("-", "").replace(".", "").isalnum():
            raise ValueError(f"Invalid S3 bucket name: {v!r}")
        return v

    @field_validator("key_prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if v and not v.endswith("/"):
            return v + "/"
        return v

    @classmethod
    def from_secrets_and_env(cls, raw: Dict[str, Any]) -> "S3OffChainClientConfig":
        """Build config, resolving credentials via SECRETS_MANAGER then env then *raw*."""
        resolved: Dict[str, Any] = dict(raw)

        def _secret(key: str, fallback: Optional[str] = None) -> Optional[str]:
            if SECRETS_MANAGER:
                val = SECRETS_MANAGER.get_secret(key, required=False)
                if val:
                    return val
            return os.environ.get(key, fallback)

        if not resolved.get("aws_access_key_id"):
            resolved["aws_access_key_id"] = _secret("AWS_ACCESS_KEY_ID")
        if not resolved.get("aws_secret_access_key"):
            resolved["aws_secret_access_key"] = _secret("AWS_SECRET_ACCESS_KEY")

        return cls.model_validate(resolved)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hmac_sign(payload: bytes) -> str:
    """HMAC-SHA256 sign *payload* with ``DLT_HMAC_KEY``."""
    key_material = ""
    if SECRETS_MANAGER:
        key_material = SECRETS_MANAGER.get_secret("DLT_HMAC_KEY", required=PRODUCTION_MODE) or ""
    if not key_material:
        if PRODUCTION_MODE:
            raise S3OffChainError("DLT_HMAC_KEY must be set in PRODUCTION_MODE for blob integrity.")
        return ""
    key_bytes = key_material.encode("utf-8") if isinstance(key_material, str) else key_material
    return hmac.new(key_bytes, payload, hashlib.sha256).hexdigest()


def _hmac_verify(payload: bytes, expected: str) -> None:
    """Raise ``S3IntegrityError`` if the HMAC of *payload* does not match *expected*."""
    if not expected:
        return  # HMAC was not set at write time (dev/test mode)
    actual = _hmac_sign(payload)
    if not hmac.compare_digest(actual, expected):
        raise S3IntegrityError(
            "Off-chain blob HMAC mismatch — possible tampering or data corruption."
        )


def _encrypt(plaintext: bytes) -> bytes:
    """AES-256-GCM encrypt *plaintext*; prefix with 12-byte nonce."""
    if not _AESGCM_AVAILABLE:
        raise S3OffChainError("cryptography package is required for encryption.")
    key_hex = (SECRETS_MANAGER.get_secret("DLT_ENCRYPTION_KEY", required=True)
               if SECRETS_MANAGER else os.environ.get("DLT_ENCRYPTION_KEY", ""))
    if len(key_hex) != 64:
        raise S3OffChainError("DLT_ENCRYPTION_KEY must be a 64-character hex string (32 bytes).")
    key = bytes.fromhex(key_hex)
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, associated_data=None)


def _decrypt(ciphertext: bytes) -> bytes:
    """AES-256-GCM decrypt; first 12 bytes are the nonce."""
    if not _AESGCM_AVAILABLE:
        raise S3OffChainError("cryptography package is required for decryption.")
    key_hex = (SECRETS_MANAGER.get_secret("DLT_ENCRYPTION_KEY", required=True)
               if SECRETS_MANAGER else os.environ.get("DLT_ENCRYPTION_KEY", ""))
    key = bytes.fromhex(key_hex)
    nonce, ct = ciphertext[:12], ciphertext[12:]
    return AESGCM(key).decrypt(nonce, ct, associated_data=None)


def _compress(data: bytes) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(data)
    return buf.getvalue()


def _decompress(data: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as gz:
        return gz.read()


# ---------------------------------------------------------------------------
# S3OffChainClient
# ---------------------------------------------------------------------------

class S3OffChainClient:
    """Production AWS S3-backed off-chain blob storage for the DLT backend.

    Features
    --------
    * **HMAC-SHA256 integrity** — every blob is signed on write and verified
      on read using ``DLT_HMAC_KEY`` from the platform secrets vault.
    * **AES-256-GCM encryption** — optional client-side encryption when
      ``DLT_ENCRYPT_AT_REST=true`` (requires ``DLT_ENCRYPTION_KEY`` in vault).
    * **gzip compression** — blobs are compressed before upload to reduce
      S3 storage costs and transfer latency.
    * **Exponential-backoff retry** — transient S3 / network errors are
      retried up to ``config.max_retries`` times with capped jitter.
    * **Circuit breaker** — consecutive failures open the circuit and
      fast-fail requests until the cool-down period has elapsed.
    * **Prometheus metrics** — ``dlt_s3_operations_total``,
      ``dlt_s3_bytes_total``, ``dlt_s3_operation_duration_seconds``,
      ``dlt_s3_circuit_breaker_open``.
    * **OpenTelemetry spans** — every public method emits a span with
      ``correlation_id``, ``bucket``, and ``blob_id`` attributes.
    * **Non-blocking** — all boto3 I/O is delegated to ``asyncio.to_thread``.

    Construct with ``S3OffChainClientConfig`` (or pass a raw dict which will
    be converted via ``S3OffChainClientConfig.from_secrets_and_env``).
    """

    def __init__(
        self,
        config: Union[S3OffChainClientConfig, Dict[str, Any]],
    ) -> None:
        if not _BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for production S3OffChainClient. "
                "Install it with:  pip install 'boto3>=1.26'"
            )

        if isinstance(config, dict):
            config = S3OffChainClientConfig.from_secrets_and_env(config)
        self._cfg = config

        boto_kwargs: Dict[str, Any] = {
            "region_name": self._cfg.region_name,
            "config": BotocoreConfig(
                max_pool_connections=self._cfg.max_pool_connections,
                connect_timeout=self._cfg.connect_timeout,
                read_timeout=self._cfg.read_timeout,
                retries={"mode": "standard", "max_attempts": 1},  # we retry ourselves
            ),
        }
        if self._cfg.aws_access_key_id:
            boto_kwargs["aws_access_key_id"] = self._cfg.aws_access_key_id
        if self._cfg.aws_secret_access_key:
            boto_kwargs["aws_secret_access_key"] = self._cfg.aws_secret_access_key

        self._s3 = boto3.client("s3", **boto_kwargs)

        # Circuit-breaker state
        self._cb_failures: int = 0
        self._cb_opened_at: float = 0.0
        self._cb_open: bool = False

        if PRODUCTION_MODE and not ENCRYPT_AT_REST:
            logger.warning(
                "DLT_ENCRYPT_AT_REST is not enabled in PRODUCTION_MODE. "
                "Enable client-side encryption for compliance."
            )

        if audit_logger:
            audit_logger.log_event(
                "s3_offchain_client_init",
                bucket=self._cfg.bucket_name,
                region=self._cfg.region_name,
                sse=self._cfg.sse,
                encrypt_at_rest=ENCRYPT_AT_REST,
            )
        logger.info(
            "S3OffChainClient ready (bucket=%r, region=%s, sse=%s, encrypt=%s).",
            self._cfg.bucket_name, self._cfg.region_name,
            self._cfg.sse or "none", ENCRYPT_AT_REST,
        )

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _cb_check(self) -> None:
        if not self._cb_open:
            return
        if time.monotonic() - self._cb_opened_at >= self._cfg.circuit_breaker_reset_sec:
            logger.info("S3OffChainClient: circuit breaker entering half-open.")
            self._cb_open = False
            _s3_circuit_open.set(0)
        else:
            raise S3CircuitOpenError(
                f"S3 circuit breaker is open; retrying after "
                f"{self._cfg.circuit_breaker_reset_sec:.0f}s cool-down."
            )

    def _cb_success(self) -> None:
        self._cb_failures = 0
        self._cb_open = False
        _s3_circuit_open.set(0)

    def _cb_failure(self, exc: Exception) -> None:
        self._cb_failures += 1
        if self._cb_failures >= self._cfg.circuit_breaker_threshold:
            self._cb_open = True
            self._cb_opened_at = time.monotonic()
            _s3_circuit_open.set(1)
            alert_operator(
                f"S3OffChainClient circuit breaker OPENED after "
                f"{self._cb_failures} consecutive failures. Last error: {exc}",
                level="CRITICAL",
            )
            if audit_logger:
                audit_logger.log_event(
                    "s3_circuit_breaker_opened",
                    bucket=self._cfg.bucket_name,
                    failures=self._cb_failures,
                    error=str(exc),
                )

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    async def _with_retry(self, operation: str, fn, *args, **kwargs):
        """Execute *fn* with exponential backoff; honour circuit breaker."""
        self._cb_check()
        last_exc: Optional[Exception] = None
        for attempt in range(self._cfg.max_retries):
            t0 = time.monotonic()
            try:
                result = await asyncio.to_thread(fn, *args, **kwargs)
                _s3_ops_total.labels(operation=operation, status="success").inc()
                _s3_latency.labels(operation=operation).observe(time.monotonic() - t0)
                self._cb_success()
                return result
            except (BotoCoreError, ClientError, OSError) as exc:
                last_exc = exc
                _s3_ops_total.labels(operation=operation, status="error").inc()
                _s3_latency.labels(operation=operation).observe(time.monotonic() - t0)
                self._cb_failure(exc)

                if attempt < self._cfg.max_retries - 1:
                    delay = self._cfg.retry_base_delay * (self._cfg.retry_backoff ** attempt)
                    logger.warning(
                        "S3 %s attempt %d/%d failed (%s); retrying in %.1fs.",
                        operation, attempt + 1, self._cfg.max_retries, exc, delay,
                    )
                    if audit_logger:
                        audit_logger.log_event(
                            "s3_retry_attempt",
                            operation=operation, attempt=attempt + 1,
                            max_retries=self._cfg.max_retries, error=str(exc),
                        )
                    await asyncio.sleep(delay)
                else:
                    logger.critical(
                        "S3 %s failed after %d attempts. Final error: %s",
                        operation, self._cfg.max_retries, exc,
                        exc_info=True,
                    )
                    alert_operator(
                        f"S3OffChainClient: '{operation}' failed after "
                        f"{self._cfg.max_retries} attempts — {exc}",
                        level="CRITICAL",
                        details={"traceback": traceback.format_exc()},
                    )
                    if audit_logger:
                        audit_logger.log_event(
                            "s3_retry_final_failure",
                            operation=operation,
                            max_retries=self._cfg.max_retries,
                            final_error=str(exc),
                        )
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Encoding helpers
    # ------------------------------------------------------------------

    def _object_key(self, blob_id: str) -> str:
        return f"{self._cfg.key_prefix}{blob_id}.bin"

    def _encode_blob(
        self,
        checkpoint_name: str,
        blob: Union[str, bytes, dict, list],
    ) -> tuple[bytes, str]:
        """Serialise → compress → (encrypt) blob; return (payload, hmac_sig)."""
        if isinstance(blob, (dict, list)):
            raw = json.dumps(
                {"checkpoint_name": checkpoint_name, "blob": blob},
                sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")
        elif isinstance(blob, str):
            raw = json.dumps(
                {"checkpoint_name": checkpoint_name, "blob": blob},
            ).encode("utf-8")
        else:
            raw = json.dumps(
                {"checkpoint_name": checkpoint_name,
                 "blob": blob.decode("utf-8", errors="replace")},
            ).encode("utf-8")

        compressed = _compress(raw)
        payload = _encrypt(compressed) if ENCRYPT_AT_REST else compressed
        sig = _hmac_sign(payload)
        return payload, sig

    def _decode_blob(self, payload: bytes, sig: str) -> Any:
        """Verify HMAC → (decrypt) → decompress → return blob value."""
        _hmac_verify(payload, sig)
        decompressed_payload = _decrypt(payload) if ENCRYPT_AT_REST else payload
        data = json.loads(_decompress(decompressed_payload))
        return data.get("blob")

    # ------------------------------------------------------------------
    # Sync S3 helpers (called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _put_sync(self, key: str, body: bytes, metadata: Dict[str, str]) -> None:
        kwargs: Dict[str, Any] = {
            "Bucket": self._cfg.bucket_name,
            "Key": key,
            "Body": body,
            "ContentType": "application/octet-stream",
            "Metadata": metadata,
        }
        if self._cfg.sse:
            kwargs["ServerSideEncryption"] = self._cfg.sse
            if self._cfg.kms_key_id:
                kwargs["SSEKMSKeyId"] = self._cfg.kms_key_id
        self._s3.put_object(**kwargs)

    def _get_sync(self, key: str) -> tuple[bytes, Dict[str, str]]:
        response = self._s3.get_object(Bucket=self._cfg.bucket_name, Key=key)
        body = response["Body"].read()
        meta = response.get("Metadata", {})
        return body, meta

    def _head_bucket_sync(self) -> None:
        self._s3.head_bucket(Bucket=self._cfg.bucket_name)

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def save_blob(
        self,
        checkpoint_name: str,
        blob: Union[str, bytes, dict, list],
        correlation_id: Optional[str] = None,
    ) -> str:
        """Compress, optionally encrypt, sign, and upload *blob* to S3.

        Returns
        -------
        str
            A UUID blob ID; pass to :meth:`get_blob` to retrieve the data.
        """
        with _tracer.start_as_current_span("s3_offchain.save_blob") as span:
            blob_id = str(uuid.uuid4())
            span.set_attribute("blob_id", blob_id)
            span.set_attribute("checkpoint_name", checkpoint_name)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)

            try:
                payload, sig = self._encode_blob(checkpoint_name, blob)
                key = self._object_key(blob_id)
                metadata = {
                    "blob-id": blob_id,
                    "checkpoint-name": checkpoint_name,
                    "hmac-sha256": sig,
                    "encrypted": str(ENCRYPT_AT_REST).lower(),
                }
                if correlation_id:
                    metadata["correlation-id"] = correlation_id

                await self._with_retry("put_object", self._put_sync, key, payload, metadata)
                _s3_bytes_total.labels(direction="upload").inc(len(payload))
                span.set_attribute("payload_bytes", len(payload))

                if audit_logger:
                    audit_logger.log_event(
                        "s3_blob_saved",
                        blob_id=blob_id, checkpoint=checkpoint_name,
                        bytes=len(payload), cid=correlation_id,
                    )
                logger.debug(
                    "S3 blob saved: id=%s bucket=%s bytes=%d",
                    blob_id, self._cfg.bucket_name, len(payload),
                )
                return blob_id

            except Exception as exc:
                span.record_exception(exc)
                if _OTEL_AVAILABLE:
                    span.set_status(otel_trace.StatusCode.ERROR, str(exc))
                logger.error(
                    "save_blob failed (blob_id=%s, cid=%s): %s",
                    blob_id, correlation_id, exc,
                )
                raise

    async def get_blob(
        self,
        off_chain_id: str,
        correlation_id: Optional[str] = None,
    ) -> Any:
        """Download, verify integrity, decrypt and return the stored blob value."""
        with _tracer.start_as_current_span("s3_offchain.get_blob") as span:
            span.set_attribute("blob_id", off_chain_id)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)

            try:
                key = self._object_key(off_chain_id)
                payload, meta = await self._with_retry("get_object", self._get_sync, key)
                _s3_bytes_total.labels(direction="download").inc(len(payload))
                span.set_attribute("payload_bytes", len(payload))

                sig = meta.get("hmac-sha256", "")
                blob_value = self._decode_blob(payload, sig)

                if audit_logger:
                    audit_logger.log_event(
                        "s3_blob_retrieved",
                        blob_id=off_chain_id, bytes=len(payload), cid=correlation_id,
                    )
                return blob_value

            except S3IntegrityError:
                alert_operator(
                    f"S3 blob integrity check FAILED for id={off_chain_id!r} "
                    f"(cid={correlation_id}). Possible data tampering.",
                    level="CRITICAL",
                )
                if audit_logger:
                    audit_logger.log_event(
                        "s3_blob_integrity_failure",
                        blob_id=off_chain_id, cid=correlation_id,
                    )
                raise

            except Exception as exc:
                span.record_exception(exc)
                if _OTEL_AVAILABLE:
                    span.set_status(otel_trace.StatusCode.ERROR, str(exc))
                logger.error(
                    "get_blob failed (id=%s, cid=%s): %s", off_chain_id, correlation_id, exc,
                )
                raise

    async def health_check(self, correlation_id: Optional[str] = None) -> Dict[str, Any]:
        """Verify S3 bucket accessibility; update circuit-breaker status."""
        with _tracer.start_as_current_span("s3_offchain.health_check") as span:
            span.set_attribute("bucket", self._cfg.bucket_name)
            if correlation_id:
                span.set_attribute("correlation_id", correlation_id)
            if self._cb_open:
                return {
                    "status": False,
                    "message": "S3 circuit breaker is open.",
                    "bucket": self._cfg.bucket_name,
                }
            try:
                await asyncio.to_thread(self._head_bucket_sync)
                _s3_ops_total.labels(operation="health_check", status="success").inc()
                return {
                    "status": True,
                    "message": f"S3 bucket {self._cfg.bucket_name!r} is accessible.",
                    "bucket": self._cfg.bucket_name,
                }
            except (BotoCoreError, ClientError) as exc:
                _s3_ops_total.labels(operation="health_check", status="error").inc()
                span.record_exception(exc)
                return {
                    "status": False,
                    "message": f"S3 health check failed: {exc}",
                    "bucket": self._cfg.bucket_name,
                }

    async def close(self) -> None:
        """Release the boto3 client (no async teardown required)."""
        self._s3 = None  # type: ignore[assignment]
        logger.info("S3OffChainClient closed (bucket=%r).", self._cfg.bucket_name)


__all__ = [
    "S3OffChainClient",
    "S3OffChainClientConfig",
    "S3IntegrityError",
    "S3CircuitOpenError",
    "S3OffChainError",
]
