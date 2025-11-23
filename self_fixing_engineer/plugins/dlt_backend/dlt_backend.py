"""
DLT (Blockchain) Backend Plugin for CheckpointManager
- Async, robust, tamper-evident checkpoint history on DLT (Hyperledger Fabric or similar).
- Pluggable: register as "dlt" backend for CheckpointManager.
- Supports: save, load, rollback, diff, audit, and off-chain payloads.
- Enterprise-grade: uses DLT for hash/metadata/lineage, off-chain for large state blobs.
- Audit/ops ready: emits audit hooks, works with SIEM, OpenTelemetry tracing.
- Extensible: supports extra metadata, multi-crew, node/host, and event streaming.
- Resilient: designed for production distributed ledgers (Fabric, Besu, Corda, etc).
"""

import asyncio
import datetime
import getpass
import gzip
import hashlib  # For hash chain integrity
import hmac  # For hash chain integrity
import io
import json
import logging
import os
import re
import sys  # For sys.exit
import time
import traceback
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Tuple

import redis.asyncio as redis

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    pass

try:
    from plugins.core_audit import audit_logger
    from plugins.core_secrets import SECRETS_MANAGER
    from plugins.core_utils import alert_operator
    from plugins.core_utils import scrub_secrets as scrub_sensitive_data
except ImportError:
    # Handle missing plugins gracefully
    alert_operator = None
    audit_logger = None
    SECRETS_MANAGER = None

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
    trace = None
    TracerProvider = None
    Resource = None
    OTLPSpanExporter = None
    BatchSpanProcessor = None

HAVE_AESGCM = "AESGCM" in globals()


# --- Global Production Mode Flag (from main orchestrator) ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"

logger = logging.getLogger("dlt_backend")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s - [%(levelname)s] - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# --- Custom Exceptions ---
class AnalyzerCriticalError(Exception):
    """
    Custom exception for critical errors that should halt execution and alert ops.
    Alerting is handled by the caller to avoid double-firing.
    """

    pass


class NonCriticalError(Exception):
    """
    Custom exception for recoverable issues that should be logged but not halt execution.
    """

    pass


class HashChainError(Exception):
    """
    Custom exception for hash chain integrity failures.
    """

    pass


# --- Caching: Redis Client Initialization ---
REDIS_CLIENT = None
try:
    REDIS_CLIENT = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=0,
        decode_responses=True,
    )
except Exception as e:
    logger.warning(
        f"Failed to connect to Redis for caching: {e}. Caching will be disabled."
    )
    REDIS_CLIENT = None


async def _should_alert(key: str, ttl=60) -> bool:
    """Checks Redis to see if an alert for this key should be sent, with a TTL."""
    if not REDIS_CLIENT:
        return True
    try:
        # Use SETNX for an atomic "set if not exists" operation
        # Returns 1 if key was set, 0 if it already existed
        if await REDIS_CLIENT.setnx(f"alert:{key}", "1"):
            await REDIS_CLIENT.expire(f"alert:{key}", ttl)
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to check Redis for alert rate limiting: {e}")
        return True


# --- OpenTelemetry Tracing (MANDATORY) ---
# OpenTelemetry tracing must be enforced in prod (fail if not present).
if OPENTELEMETRY_AVAILABLE:
    tracer = trace.get_tracer(__name__)
    logger.info("OpenTelemetry tracer available for dlt_backend.")
else:
    if PRODUCTION_MODE:
        logger.critical(
            "CRITICAL: OpenTelemetry not found. Tracing is mandatory in PRODUCTION_MODE. Aborting startup."
        )
        if alert_operator:
            alert_operator(
                "CRITICAL: OpenTelemetry missing. DLT backend aborted.",
                level="CRITICAL",
            )
        sys.exit(1)
    else:
        logger.warning("OpenTelemetry not found. Tracing will be disabled.")

        class MockTracer:
            def start_as_current_span(self, *args, **kwargs):
                class MockSpan:
                    def __enter__(self):
                        pass

                    def __exit__(self, *args):
                        pass

                    def set_attribute(self, *args):
                        pass

                    def record_exception(self, *args):
                        pass

                    def set_status(self, *args):
                        pass

                return MockSpan()

        tracer = MockTracer()


# --- Exception Handling & Retries (MANDATORY) ---
def async_retry(retries=5, delay=1, backoff=2):
    """
    An asynchronous retry decorator with exponential backoff.
    Retries should have a max cap (no infinite/flooding loops).
    """

    def decorator(fn):
        async def wrapper(*args, **kwargs):
            last_exc = None
            for i in range(retries):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        f"[DLT Retry] Attempt {i+1}/{retries} failed: {type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
                    audit_logger.log_event(
                        "dlt_retry_attempt",
                        operation=fn.__name__,
                        attempt=i + 1,
                        max_retries=retries,
                        error=str(exc),
                    )
                    if i < retries - 1:
                        await asyncio.sleep(delay * (backoff**i))

            logger.critical(
                f"[DLT Retry] Operation '{fn.__name__}' failed after {retries} attempts: {last_exc}",
                exc_info=True,
            )
            audit_logger.log_event(
                "dlt_retry_final_failure",
                operation=fn.__name__,
                max_retries=retries,
                final_error=str(last_exc),
            )
            if await _should_alert(f"{fn.__name__}:{type(last_exc).__name__}"):
                alert_operator(
                    f"CRITICAL: DLT operation '{fn.__name__}' failed after {retries} attempts. Error: {last_exc}",
                    level="CRITICAL",
                    details={"traceback": traceback.format_exc()},
                )
            raise last_exc

        return wrapper

    return decorator


# --- Data Integrity & Hash Chain ---
def _calculate_hash(data: Any) -> str:
    """Calculates SHA-256 hash of JSON-serialized data for integrity."""
    data_json = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data_json).hexdigest()


def _verify_hash_chain(
    expected_prev_hash: str, actual_prev_hash: str, checkpoint_name: str, version: int
):
    """Verifies the hash chain integrity."""
    if expected_prev_hash != actual_prev_hash:
        audit_logger.log_event(
            "dlt_hash_chain_failure",
            checkpoint=checkpoint_name,
            version=version,
            expected=expected_prev_hash,
            actual=actual_prev_hash,
        )
        raise HashChainError(
            f"Hash chain broken for '{checkpoint_name}' v{version}: "
            f"expected prev {expected_prev_hash}, got {actual_prev_hash}"
        )


def _maybe_sign_checkpoint(checkpoint_data: Dict[str, Any]) -> Optional[str]:
    """Generates an HMAC signature for a checkpoint payload if a key is available."""
    key = SECRETS_MANAGER.get_secret("DLT_HMAC_KEY", required=PRODUCTION_MODE)
    if not key:
        return None
    payload = json.dumps(checkpoint_data, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


# --- Cryptography and Compression Helpers ---
def compress_json(data: dict) -> bytes:
    """Compresses a JSON dictionary using gzip."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return buf.getvalue()


def decompress_json(data: bytes) -> dict:
    """Decompresses gzipped data to a JSON dictionary."""
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as f:
        return json.loads(f.read().decode("utf-8"))


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypts plaintext using AES-256-GCM."""
    if not HAVE_AESGCM:
        raise AnalyzerCriticalError(
            "Encryption requested but 'cryptography' (AESGCM) is not installed."
        )
    if PRODUCTION_MODE and (not isinstance(key, bytes) or len(key) != 32):
        raise AnalyzerCriticalError(
            "Invalid encryption key. Must be 32 bytes for AES-256-GCM."
        )
    aes = AESGCM(key)
    nonce = os.urandom(12)
    return nonce + aes.encrypt(nonce, plaintext, associated_data=None)


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypts ciphertext using AES-256-GCM."""
    if not HAVE_AESGCM:
        raise AnalyzerCriticalError(
            "Decryption requested but 'cryptography' (AESGCM) is not installed."
        )
    if PRODUCTION_MODE and (not isinstance(key, bytes) or len(key) != 32):
        raise AnalyzerCriticalError(
            "Invalid decryption key. Must be 32 bytes for AES-256-GCM."
        )
    nonce, ct = ciphertext[:12], ciphertext[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ct, associated_data=None)


############################################
# Production Off-Chain Storage: S3 Example #
############################################

# S3 Client (from dlt_offchain_clients.py)
try:
    from .dlt_offchain_clients import S3OffChainClient
except ImportError as e:
    if PRODUCTION_MODE:
        logger.critical(
            f"CRITICAL: S3OffChainClient not found. Off-chain storage is critical. Aborting startup: {e}."
        )
        alert_operator(
            "CRITICAL: S3OffChainClient missing. DLT backend aborted.",
            level="CRITICAL",
        )
        sys.exit(1)
    else:
        logger.warning("S3OffChainClient not found. Using dummy for off-chain storage.")

        class S3OffChainClient:  # Dummy for non-prod
            def __init__(self, config):
                self.store = {}
                logger.warning("Using dummy S3OffChainClient.")

            async def save_blob(self, checkpoint_name, blob, correlation_id=None):
                key = f"dummy_s3/{checkpoint_name}-{int(time.time())}.gz"
                self.store[key] = blob
                return key

            async def get_blob(self, off_chain_id, correlation_id=None):
                if off_chain_id not in self.store:
                    raise FileNotFoundError(f"Dummy blob {off_chain_id} not found.")
                return self.store[off_chain_id]

            async def health_check(self, correlation_id=None):
                return {"status": True, "message": "Dummy S3 is healthy."}

            async def close(self):
                pass


##############################################
# Production DLT Client: Fabric SDK Example  #
##############################################

# Fabric Client (from dlt_fabric_clients.py)
try:
    from .dlt_fabric_clients import FabricClientWrapper
except ImportError as e:
    if PRODUCTION_MODE:
        logger.critical(
            f"CRITICAL: FabricClientWrapper not found. DLT client is critical. Aborting startup: {e}."
        )
        alert_operator(
            "CRITICAL: FabricClientWrapper missing. DLT backend aborted.",
            level="CRITICAL",
        )
        sys.exit(1)
    else:
        logger.warning("FabricClientWrapper not found. Using dummy for DLT client.")

        class FabricClientWrapper:  # Dummy for non-prod
            def __init__(self, config, off_chain_client):
                self.chain = {}
                self.off_chain_client = off_chain_client
                logger.warning("Using dummy FabricClientWrapper.")

            async def write_checkpoint(
                self,
                checkpoint_name,
                hash,
                prev_hash,
                metadata,
                payload_blob,
                correlation_id=None,
            ):
                version = len(self.chain.get(checkpoint_name, [])) + 1
                tx_id = f"{checkpoint_name}-tx{version}-{int(time.time())}"
                off_chain_id = await self.off_chain_client.save_blob(
                    checkpoint_name, payload_blob
                )
                entry = {
                    "hash": hash,
                    "prev_hash": prev_hash,
                    "metadata": metadata,
                    "off_chain_ref": off_chain_id,
                    "tx_id": tx_id,
                    "version": version,
                }
                self.chain.setdefault(checkpoint_name, []).append(entry)
                return tx_id, off_chain_id, version

            async def read_checkpoint(self, name, version=None, correlation_id=None):
                chain = self.chain.get(name, [])
                entry = (
                    chain[-1]
                    if version is None or version == "latest"
                    else next((e for e in chain if e["version"] == version), None)
                )
                if not entry:
                    raise FileNotFoundError(
                        f"Dummy checkpoint {name} v{version} not found."
                    )
                payload_blob = await self.off_chain_client.get_blob(
                    entry["off_chain_ref"]
                )
                return {
                    "metadata": entry,
                    "payload_blob": payload_blob,
                    "tx_id": entry["tx_id"],
                }

            async def get_version_tx(self, name, version, correlation_id=None):
                return await self.read_checkpoint(name, version)

            async def rollback_checkpoint(
                self, name, rollback_hash, correlation_id=None
            ):
                chain = self.chain.get(name, [])
                entry = next((e for e in chain if e["hash"] == rollback_hash), None)
                if not entry:
                    raise FileNotFoundError(
                        f"Dummy checkpoint {name} hash {rollback_hash} not found."
                    )
                new_version = len(chain) + 1
                tx_id = f"{name}-rollback-tx{new_version}-{int(time.time())}"
                new_entry = {
                    "hash": entry["hash"],
                    "prev_hash": entry["prev_hash"],
                    "metadata": entry["metadata"],
                    "off_chain_ref": entry["off_chain_ref"],
                    "tx_id": tx_id,
                    "version": new_version,
                    "rollback_of_version": entry["version"],
                }
                chain.append(new_entry)
                return new_entry

            async def health_check(self, correlation_id=None):
                return {"status": True, "message": "Dummy Fabric is healthy."}

            async def close(self):
                pass


# Configuration for DLT backend (from main application config)
DLT_BACKEND_CONFIG: Dict[str, Any] = {}  # Populated by external config loader

off_chain_client: Optional[S3OffChainClient] = None
fabric_client: Optional[FabricClientWrapper] = None


async def initialize_dlt_backend(config: Dict[str, Any]) -> None:
    """
    Initializes the DLT backend clients based on provided configuration.
    """
    global off_chain_client, fabric_client, DLT_BACKEND_CONFIG
    DLT_BACKEND_CONFIG = config

    off_chain_type = DLT_BACKEND_CONFIG.get("off_chain_storage_type", "s3")
    dlt_type = DLT_BACKEND_CONFIG.get("dlt_client_type", "fabric")

    if PRODUCTION_MODE:
        if off_chain_type == "in_memory":
            raise AnalyzerCriticalError(
                "'in_memory' off-chain storage is forbidden in PRODUCTION_MODE."
            )
        if dlt_type == "simple":
            raise AnalyzerCriticalError(
                "'simple' DLT client is forbidden in PRODUCTION_MODE."
            )

    try:
        if off_chain_type == "s3":
            off_chain_config = DLT_BACKEND_CONFIG.get("s3", {})
            off_chain_client = S3OffChainClient(off_chain_config)
        else:
            raise AnalyzerCriticalError(
                f"Unsupported off-chain storage type '{off_chain_type}'. Aborting startup."
            )

        health_result = await off_chain_client.health_check()
        if not health_result["status"]:
            raise AnalyzerCriticalError(
                f"Off-chain client '{off_chain_type}' failed initial health check: {health_result['message']}."
            )
        logger.info(f"Off-chain client '{off_chain_type}' initialized and healthy.")
    except AnalyzerCriticalError as e:
        logger.critical(f"CRITICAL: {e}.")
        alert_operator(
            f"CRITICAL: Off-chain client '{off_chain_type}' failed health check. Aborting.",
            level="CRITICAL",
        )
        raise e
    except Exception as e:
        logger.critical(
            f"CRITICAL: Failed to initialize off-chain client '{off_chain_type}': {e}.",
            exc_info=True,
        )
        alert_operator(
            f"CRITICAL: Failed to initialize off-chain client '{off_chain_type}': {e}. Aborting.",
            level="CRITICAL",
        )
        raise AnalyzerCriticalError(
            f"Failed to initialize off-chain client '{off_chain_type}': {e}."
        )

    try:
        if dlt_type == "fabric":
            dlt_client_config = DLT_BACKEND_CONFIG.get("fabric", {})
            fabric_client = FabricClientWrapper(dlt_client_config, off_chain_client)
        else:
            raise AnalyzerCriticalError(
                f"Unsupported DLT client type '{dlt_type}'. Aborting startup."
            )

        health_result = await fabric_client.health_check()
        if not health_result["status"]:
            raise AnalyzerCriticalError(
                f"DLT client '{dlt_type}' failed initial health check: {health_result['message']}."
            )
        logger.info(f"DLT client '{dlt_type}' initialized and healthy.")
    except AnalyzerCriticalError as e:
        logger.critical(f"CRITICAL: {e}.")
        alert_operator(
            f"CRITICAL: DLT client '{dlt_type}' failed health check. Aborting.",
            level="CRITICAL",
        )
        raise e
    except Exception as e:
        logger.critical(
            f"CRITICAL: Failed to initialize DLT client '{dlt_type}': {e}.",
            exc_info=True,
        )
        alert_operator(
            f"CRITICAL: Failed to initialize DLT client '{dlt_type}': {e}. Aborting.",
            level="CRITICAL",
        )
        raise AnalyzerCriticalError(
            f"Failed to initialize DLT client '{dlt_type}': {e}."
        )

    logger.info("DLT Backend initialized successfully.")
    audit_logger.log_event(
        "dlt_backend_initialized", off_chain_type=off_chain_type, dlt_type=dlt_type
    )


NAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


def _lock_factory():
    return asyncio.Lock()


_save_locks = defaultdict(_lock_factory)
DIST_TTL = int(os.getenv("DLT_DIST_LOCK_TTL", "15"))
CACHE_TTL = int(os.getenv("DLT_CACHE_TTL", "3600"))


@asynccontextmanager
async def _maybe_dist_lock(name: str):
    """Acquires a best-effort distributed lock if Redis is available."""
    lock_key = f"dlt:lock:{name}"
    lock_acquired = False
    if REDIS_CLIENT:
        token = os.urandom(16).hex()
        try:
            # Atomic set with NX (not exists) and EX (expire)
            ok = await REDIS_CLIENT.set(lock_key, token, nx=True, ex=DIST_TTL)
            if ok:
                lock_acquired = True
                yield
            else:
                # Lock not acquired, likely another process has it. Wait a bit and continue.
                logger.warning(
                    f"Failed to acquire distributed lock for '{name}'. Another process may be writing."
                )
                await asyncio.sleep(0.1)
                yield
        except Exception as e:
            logger.warning(
                f"Distributed lock failed due to Redis error: {e}", exc_info=True
            )
            yield
        finally:
            if lock_acquired:
                # Best-effort release, only if we hold the lock (token matches)
                try:
                    curr_token = await REDIS_CLIENT.get(lock_key)
                    if curr_token == token:
                        await REDIS_CLIENT.delete(lock_key)
                except Exception:
                    logger.warning("Failed to release distributed lock", exc_info=True)
    else:
        yield


async def dlt_backend(self: "CheckpointManager", op: str, name: str, *args, **kwargs):
    """
    DLT Backend plugin for CheckpointManager.
    """
    if not NAME_RE.match(name):
        raise ValueError(f"Invalid checkpoint name: {name}")

    if tracer:
        with tracer.start_as_current_span(f"dlt_{op}") as span:
            try:
                span.set_attribute("dlt.name", name)
                span.set_attribute("dlt.op", op)
            except Exception:
                pass
            return await _dlt_backend_impl(self, op, name, *args, **kwargs)
    else:
        return await _dlt_backend_impl(self, op, name, *args, **kwargs)


async def _dlt_backend_impl(
    self: "CheckpointManager", op: str, name: str, *args, **kwargs
):

    def get_blob(state: Any) -> bytes:
        if self.state_schema:
            self.state_schema(**state)  # Validate before compressing
        blob = compress_json({"state": state})
        if getattr(self, "encrypt_key", None):
            blob = encrypt(blob, self.encrypt_key)

        MAX_BLOB_MB = int(os.getenv("DLT_MAX_BLOB_MB", "16"))
        if len(blob) > MAX_BLOB_MB * 1024 * 1024:
            raise AnalyzerCriticalError(f"State blob exceeds {MAX_BLOB_MB}MB limit.")

        return blob

    async def from_blob(blob: bytes) -> Any:
        try:
            if getattr(self, "encrypt_key", None):
                blob = decrypt(blob, self.encrypt_key)
            state = decompress_json(blob)["state"]
            if self.state_schema:
                self.state_schema(**state)  # Validate after decompressing
            return state
        except Exception as exc:
            audit_logger.log_event("dlt_payload_corrupt", name=name, error=str(exc))
            if await _should_alert(f"payload_corrupt:{name}"):
                alert_operator(
                    f"CRITICAL: Off-chain payload corrupt for '{name}'.",
                    level="CRITICAL",
                )
            raise AnalyzerCriticalError(f"Corrupt payload for '{name}': {exc}")

    if op == "save":
        async with _save_locks[name]:
            async with _maybe_dist_lock(name):
                state, metadata = args[0], args[1] if len(args) > 1 else {}
                if not isinstance(metadata, dict):
                    metadata = {}

                # Fetch latest once for both idempotency and prev_hash resync
                latest_tx = None
                try:
                    latest_tx = await async_retry()(fabric_client.read_checkpoint)(
                        name, version="latest"
                    )
                except (FileNotFoundError, IndexError):
                    latest_tx = None

                state_hash = _calculate_hash({"state": state})

                if (
                    latest_tx
                    and latest_tx["metadata"].get("payload_hash") == state_hash
                ):
                    logger.info(
                        f"DLT checkpoint save skipped: {name} is identical to latest version."
                    )
                    if REDIS_CLIENT:
                        try:
                            # Prime cache with the latest state if it was a cache miss
                            await REDIS_CLIENT.setex(
                                f"dlt_checkpoint:{name}:latest",
                                CACHE_TTL,
                                json.dumps(
                                    {
                                        "state": state,
                                        "hash": latest_tx["metadata"]["hash"],
                                    }
                                ),
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to prime cache after save skip: {e}"
                            )
                    return latest_tx["tx_id"]

                prev_hash = latest_tx["metadata"]["hash"] if latest_tx else None

                # Calculate the new version hash for the DLT record
                version_hash_data = {"payload_hash": state_hash, "prev_hash": prev_hash}
                version_hash = _calculate_hash(version_hash_data)

                blob = get_blob(state)

                base = {
                    "name": name,
                    "payload_hash": state_hash,
                    "prev_hash": prev_hash,
                }
                sig = _maybe_sign_checkpoint(base)

                metadata.update(
                    {
                        "compression_algo": "gzip",
                        "encryption_algo": (
                            "AES-256-GCM"
                            if getattr(self, "encrypt_key", None)
                            else "none"
                        ),
                        "prev_hash": prev_hash,
                        "payload_hash": state_hash,
                    }
                )
                if sig:
                    metadata["signature"] = sig

                if tracer and "trace" in globals():
                    try:
                        span = trace.get_current_span()
                        if hasattr(span, "set_attribute"):
                            span.set_attribute("dlt.payload_hash", state_hash)
                            span.set_attribute("dlt.prev_hash", prev_hash or "")
                    except Exception:
                        pass

                tx_id, off_chain_id, version = await async_retry()(
                    fabric_client.write_checkpoint
                )(
                    checkpoint_name=name,
                    hash=version_hash,
                    prev_hash=prev_hash,
                    metadata=metadata,
                    payload_blob=blob,
                    correlation_id=kwargs.get("correlation_id"),
                )

                audit_logger.log_event(
                    "dlt_checkpoint_saved",
                    name=name,
                    hash=version_hash,
                    prev_hash=prev_hash,
                    tx_id=tx_id,
                    version=version,
                    ts=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    metadata=scrub_sensitive_data(metadata),
                    off_chain_id=off_chain_id,
                    user=(
                        getpass.getuser()
                        if "getpass" in sys.modules
                        else "unknown_user"
                    ),
                    correlation_id=kwargs.get("correlation_id"),
                )

                if REDIS_CLIENT:
                    try:
                        await REDIS_CLIENT.setex(
                            f"dlt_checkpoint:{name}:latest",
                            CACHE_TTL,
                            json.dumps({"state": state, "hash": version_hash}),
                        )
                    except Exception as e:
                        logger.warning(f"Failed to prime cache after save: {e}")

                logger.info(
                    f"DLT checkpoint saved: {name} [hash={version_hash} tx_id={tx_id}]"
                )
                return tx_id

    elif op == "load":
        version = args[0] if args else None
        is_latest = version in (None, "latest")

        cache_key = f"dlt_checkpoint:{name}:{version or 'latest'}"
        if is_latest and REDIS_CLIENT:
            try:
                cached_data = await REDIS_CLIENT.get(cache_key)
                if cached_data:
                    cached_obj = json.loads(cached_data)
                    latest_tx_on_chain = await async_retry()(
                        fabric_client.read_checkpoint
                    )(name, version="latest")
                    if latest_tx_on_chain["metadata"]["hash"] == cached_obj.get("hash"):
                        logger.info(
                            f"DLT checkpoint loaded from cache and verified: {name} v{version}"
                        )
                        return cached_obj["state"]
                    else:
                        logger.warning(
                            "Cached data hash mismatch with latest ledger entry. Bypassing cache."
                        )
            except Exception as e:
                logger.warning(f"Cache fast-path failed: {e}")

        tx = await async_retry()(fabric_client.read_checkpoint)(
            name, version=version, correlation_id=kwargs.get("correlation_id")
        )

        hash_chain_ok = True
        expected_prev = tx["metadata"].get("prev_hash")
        version_num = tx["metadata"].get("version")

        # Verify prev link when version > 1
        if self.enable_hash_chain and version_num and version_num > 1:
            prev_tx = await async_retry()(fabric_client.get_version_tx)(
                name, version_num - 1, correlation_id=kwargs.get("correlation_id")
            )
            actual_prev_hash = prev_tx["metadata"]["hash"]
            try:
                _verify_hash_chain(expected_prev, actual_prev_hash, name, version_num)
            except HashChainError as e:
                hash_chain_ok = False
                logger.critical(
                    f"CRITICAL: DLT checkpoint load failed due to broken chain: {e}"
                )
                if await _should_alert(f"load:{name}:hash_chain_broken"):
                    alert_operator(
                        f"CRITICAL: DLT checkpoint load failed for '{name}' v{version}. Hash chain broken.",
                        level="CRITICAL",
                    )
                raise

        # Recompute current *content* hash and compare to stored version hash.
        state = await from_blob(tx["payload_blob"])
        if self.enable_hash_chain:
            payload_hash = _calculate_hash({"state": state})
            expected_current_hash = _calculate_hash(
                {"payload_hash": payload_hash, "prev_hash": expected_prev}
            )
            if expected_current_hash != tx["metadata"]["hash"]:
                hash_chain_ok = False
                msg = (
                    f"Tamper detected for '{name}' v{version_num}: "
                    f"stored hash {tx['metadata']['hash']} != recomputed {expected_current_hash}"
                )
                logger.critical("CRITICAL: " + msg)
                audit_logger.log_event(
                    "dlt_hash_current_mismatch",
                    checkpoint=name,
                    version=version_num,
                    stored_hash=tx["metadata"]["hash"],
                    recomputed_hash=expected_current_hash,
                )
                if await _should_alert(f"load:{name}:tamper_detected"):
                    alert_operator(f"CRITICAL: {msg}", level="CRITICAL")
                raise HashChainError(msg)

            # Optional: verify HMAC signature
            sig = tx["metadata"].get("signature")
            if sig:
                key = SECRETS_MANAGER.get_secret(
                    "DLT_HMAC_KEY", required=PRODUCTION_MODE
                )
                if key:
                    base = {
                        "name": name,
                        "payload_hash": payload_hash,
                        "prev_hash": expected_prev,
                    }
                    computed_sig = hmac.new(
                        key.encode("utf-8"),
                        json.dumps(base, sort_keys=True, ensure_ascii=False).encode(
                            "utf-8"
                        ),
                        hashlib.sha256,
                    ).hexdigest()
                    if computed_sig != sig:
                        raise HashChainError(
                            "HMAC signature mismatch for checkpoint metadata."
                        )
                elif PRODUCTION_MODE:
                    raise AnalyzerCriticalError(
                        "DLT_HMAC_KEY missing in PRODUCTION_MODE."
                    )

        if REDIS_CLIENT and is_latest:
            try:
                await REDIS_CLIENT.setex(
                    cache_key,
                    CACHE_TTL,
                    json.dumps({"state": state, "hash": tx["metadata"]["hash"]}),
                )
            except Exception as e:
                logger.warning(f"Failed to cache checkpoint: {e}")

        audit_logger.log_event(
            "dlt_checkpoint_loaded",
            name=name,
            version=version,
            hash=tx["metadata"]["hash"],
            tx_id=tx.get("tx_id"),
            ts=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            metadata=scrub_sensitive_data(tx.get("metadata")),
            hash_chain_ok=hash_chain_ok,
            user=getpass.getuser() if "getpass" in sys.modules else "unknown_user",
            correlation_id=kwargs.get("correlation_id"),
        )
        logger.info(
            f"DLT checkpoint loaded: {name} v{version} [tx_id={tx.get('tx_id')}]"
        )
        return state

    elif op == "rollback":
        version = args[0]
        # Get the transaction to roll back to
        prev_tx = await async_retry()(fabric_client.get_version_tx)(
            name, version, correlation_id=kwargs.get("correlation_id")
        )

        # Create a new DLT entry that points back to the previous hash
        rollback_tx = await async_retry()(fabric_client.rollback_checkpoint)(
            name,
            prev_tx["metadata"]["hash"],
            correlation_id=kwargs.get("correlation_id"),
        )

        audit_logger.log_event(
            "dlt_checkpoint_rollback",
            name=name,
            to_version=version,
            rollback_tx_id=rollback_tx["tx_id"],
            ts=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            metadata=scrub_sensitive_data(rollback_tx.get("metadata")),
            user=getpass.getuser() if "getpass" in sys.modules else "unknown_user",
            correlation_id=kwargs.get("correlation_id"),
        )

        if REDIS_CLIENT:
            try:
                latest = await async_retry()(fabric_client.read_checkpoint)(
                    name, version="latest"
                )
                state_latest = await from_blob(latest["payload_blob"])
                await REDIS_CLIENT.setex(
                    f"dlt_checkpoint:{name}:latest",
                    CACHE_TTL,
                    json.dumps(
                        {"state": state_latest, "hash": latest["metadata"]["hash"]}
                    ),
                )
            except Exception as e:
                logger.warning(f"Failed to refresh cache after rollback: {e}")

        logger.info(
            f"DLT checkpoint rolled back: {name} -> v{version} [rollback_tx={rollback_tx['tx_id']}]"
        )
        return rollback_tx

    elif op == "diff":
        v1, v2 = args
        tx1 = await async_retry()(fabric_client.get_version_tx)(
            name, v1, correlation_id=kwargs.get("correlation_id")
        )
        tx2 = await async_retry()(fabric_client.get_version_tx)(
            name, v2, correlation_id=kwargs.get("correlation_id")
        )
        s1 = await from_blob(tx1["payload_blob"])
        s2 = await from_blob(tx2["payload_blob"])
        diff = _deep_diff(s1, s2)

        audit_logger.log_event(
            "dlt_checkpoint_diff",
            name=name,
            v1=v1,
            v2=v2,
            ts=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            diff_summary=f"{len(diff)} keys changed" if diff else "no changes",
            user=getpass.getuser() if "getpass" in sys.modules else "unknown_user",
            correlation_id=kwargs.get("correlation_id"),
        )
        logger.info(
            f"DLT checkpoint diff: {name} v{v1} vs v{v2} ({len(diff)} keys changed)"
        )
        return diff

    else:
        logger.critical(
            f"CRITICAL: Unsupported DLT backend operation: '{op}'. Aborting."
        )
        audit_logger.log_event("dlt_operation_unsupported", operation=op)
        alert_operator(
            f"CRITICAL: Unsupported DLT operation '{op}'. Aborting.", level="CRITICAL"
        )
        raise NotImplementedError(f"DLT backend op '{op}'")


class CheckpointManager:
    _backends = {}
    enable_hash_chain = True
    state_schema = None

    @classmethod
    def register_backend(cls, name):
        def decorator(func):
            cls._backends[name] = func
            return func

        return decorator

    def __init__(
        self, backend="dlt", enable_hash_chain=True, state_schema=None, encrypt_key=None
    ):
        self.backend = backend
        self.enable_hash_chain = enable_hash_chain
        self.state_schema = state_schema
        self.encrypt_key = encrypt_key

    async def save(self, name, state, metadata=None):
        return await self._backends[self.backend](self, "save", name, state, metadata)

    async def load(self, name, version=None):
        return await self._backends[self.backend](self, "load", name, version)

    async def rollback(self, name, version):
        return await self._backends[self.backend](self, "rollback", name, version)

    async def diff(self, name, v1, v2):
        return await self._backends[self.backend](self, "diff", name, v1, v2)


def _deep_diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Tuple[Any, Any]]:
    """Compares two dictionaries and returns keys with differing values."""
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}


# Register the dlt_backend after the class definition
CheckpointManager.register_backend("dlt")(dlt_backend)


try:
    from pydantic import BaseModel as PydanticBaseModel

    class ExampleStateSchema(PydanticBaseModel):
        value: int
        timestamp: str
        metadata: Dict[str, Any]

except ImportError:
    ExampleStateSchema = None
    logger.warning("Pydantic not available for ExampleStateSchema validation.")

if PRODUCTION_MODE:
    logger.info("Running in PRODUCTION_MODE. Strict checks are enabled.")
else:
    logger.warning("Running in Development Mode. Production checks are relaxed.")


async def _run_initialization_and_test() -> None:
    dlt_config = {
        "off_chain_storage_type": "s3",
        "dlt_client_type": "fabric",
        "s3": {
            "bucket_name": os.getenv("S3_BUCKET_NAME", "your-s3-bucket"),
            "region_name": os.getenv("S3_REGION", "us-east-1"),
            "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", "dummy_access_key"),
            "aws_secret_access_key": os.getenv(
                "AWS_SECRET_ACCESS_KEY", "dummy_secret_key"
            ),
        },
        "fabric": {
            "channel_name": os.getenv("FABRIC_CHANNEL", "mychannel"),
            "chaincode_name": os.getenv("FABRIC_CHAINCODE", "mychaincode"),
            "org_name": os.getenv("FABRIC_ORG", "Org1"),
            "user_name": os.getenv("FABRIC_USER", "User1"),
            "network_profile": os.getenv(
                "FABRIC_NETWORK_PROFILE", "path/to/connection.json"
            ),
        },
    }

    try:
        await initialize_dlt_backend(dlt_config)
        logger.info("DLT Backend initialized successfully for testing.")

        encrypt_key = os.urandom(32) if HAVE_AESGCM else None
        chk_manager = CheckpointManager(
            backend="dlt",
            enable_hash_chain=True,
            state_schema=ExampleStateSchema,
            encrypt_key=encrypt_key,
        )

        state1 = {
            "value": 10,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "metadata": {"source": "test"},
        }
        tx_id1 = await chk_manager.save(
            "my_checkpoint", state1, metadata={"user": "test_user"}
        )
        logger.info(f"Saved checkpoint 1: {tx_id1}")

        loaded_state1 = await chk_manager.load("my_checkpoint")
        logger.info(f"Loaded checkpoint 1: {loaded_state1}")

        state2 = {
            "value": 20,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "metadata": {"source": "test"},
        }
        tx_id2 = await chk_manager.save(
            "my_checkpoint", state2, metadata={"user": "test_user_2"}
        )
        logger.info(f"Saved checkpoint 2: {tx_id2}")

        loaded_state2 = await chk_manager.load("my_checkpoint")
        logger.info(f"Loaded checkpoint 2 (latest): {loaded_state2}")

        loaded_state1_v1 = await chk_manager.load("my_checkpoint", version=1)
        logger.info(f"Loaded checkpoint 1 (by version): {loaded_state1_v1}")

        diff_result = await chk_manager.diff("my_checkpoint", 1, 2)
        logger.info(f"Diff between v1 and v2: {diff_result}")

        rollback_info = await chk_manager.rollback("my_checkpoint", version=1)
        logger.info(f"Rolled back to checkpoint 1: {rollback_info}")
        rolled_back_state = await chk_manager.load("my_checkpoint")
        logger.info(f"State after rollback: {rolled_back_state}")

    except SystemExit:
        logger.error(
            "Initialization or test run aborted due to critical error (SystemExit)."
        )
    except Exception as e:
        logger.critical(
            f"An unexpected error occurred during DLT backend test: {e}", exc_info=True
        )
        alert_operator(f"CRITICAL: DLT Backend test failed: {e}.", level="CRITICAL")
    finally:
        if os.getenv("FABRIC_NETWORK_PROFILE") and os.path.exists(
            os.getenv("FABRIC_NETWORK_PROFILE")
        ):
            os.remove(os.getenv("FABRIC_NETWORK_PROFILE"))


if __name__ == "__main__":
    try:
        asyncio.run(_run_initialization_and_test())
    except AnalyzerCriticalError as e:
        logger.critical(f"Application startup failed: {e}", exc_info=True)
        sys.exit(1)
    except Exception as e:
        logger.critical(
            f"An unexpected error occurred during application runtime: {e}",
            exc_info=True,
        )
        sys.exit(1)
