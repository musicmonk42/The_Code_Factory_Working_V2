"""
cache_layer.py

A unified, lazy, resilient cache abstraction.

- Prefers Redis (if available).
- Falls back to a file-based cache under the project root.
- Falls back to in-memory cache as a last resort.

No import-time Redis dependency; all cache clients are acquired at runtime via get_cache(...).
"""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .compat_core import (
    PRODUCTION_MODE,
    SECRETS_MANAGER,
    _validate_env_var,
    alert_operator,
    get_audit_logger,
    get_json_logger,
    get_prometheus_metrics,
    get_telemetry_tracer,
)

# redis (optional)
try:
    import redis.asyncio as _redis
    from redis.exceptions import ConnectionError, RedisError

    _HAS_REDIS = True
except Exception:
    _redis = None
    _HAS_REDIS = False

    class RedisError(Exception): ...

    class ConnectionError(Exception): ...


# tenacity (optional)
try:
    from tenacity import (
        RetryError,
        after_log,
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    _HAS_TENACITY = True
except Exception:
    _HAS_TENACITY = False

    def retry(*a, **k):
        def _wrap(f):
            return f

        return _wrap

    def stop_after_attempt(*a, **k):
        pass

    def wait_exponential(*a, **k):
        pass

    def retry_if_exception_type(*a, **k):
        class _P:
            def __or__(self, other):
                # Return a new instance that combines both predicates
                return _P()

        return _P()

    class RetryError(Exception): ...

    def after_log(*a, **k):
        def _cb(*_a, **_k):
            return None

        return _cb


tracer = get_telemetry_tracer(__name__)
metrics = get_prometheus_metrics()
audit_logger = get_audit_logger()
json_logger = get_json_logger()


# --- Safe metric wrapper (works with real, mocked, or missing prometheus) ---
def _safe_metric(ctor, *args, **kwargs):
    """
    Returns a metric instance that always supports:
      - .labels(...)->self
      - .time() context manager
      - .inc(), .observe(), .set()
    If the ctor or returned object is mocked or lacks these, we wrap it.
    """

    # Local no-op metric
    class _NoopTimer:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class _NoopMetric:
        def labels(self, *a, **k):
            return self

        def time(self):
            return _NoopTimer()

        def inc(self, *a, **k):
            pass

        def observe(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

    try:
        m = ctor(*args, **kwargs)
        # If it's a mock or unexpected object, ensure required attrs exist
        has_labels = hasattr(m, "labels") and callable(getattr(m, "labels"))
        has_time = hasattr(m, "time") and callable(getattr(m, "time"))
        # If ctor returned a module-level function (mocked) or something odd, wrap it
        if not (has_labels and has_time):
            return _NoopMetric()
        return m
    except Exception:
        return _NoopMetric()


cache_hits = _safe_metric(metrics.Counter, "cache_layer_hits_total", "Cache hits", ["backend"])
cache_misses = _safe_metric(
    metrics.Counter, "cache_layer_misses_total", "Cache misses", ["backend"]
)
cache_op_latency = _safe_metric(
    metrics.Histogram,
    "cache_layer_op_latency_seconds",
    "Cache operation latency",
    ["backend", "operation"],
)
redis_connection_failures = _safe_metric(
    metrics.Counter,
    "cache_layer_redis_connection_failures_total",
    "Redis connection failures",
)
file_hmac_failures = _safe_metric(
    metrics.Counter,
    "cache_layer_file_hmac_failures_total",
    "File cache HMAC verification failures",
)

_retry_on_redis = (
    retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=(retry_if_exception_type(RedisError) | retry_if_exception_type(ConnectionError)),
        after=after_log(json_logger, 20),
    )
    if _HAS_TENACITY
    else (lambda f: f)
)


class _BaseCache:
    """Base class for cache implementations with shared logic."""

    def __init__(self, backend_name: str):
        self.backend = backend_name

    @_retry_on_redis
    async def get(self, key: str) -> Optional[Any]:
        """Gets a value from the cache."""
        span_id = str(uuid.uuid4())
        with tracer.start_as_current_span(
            f"{self.backend}_cache_get",
            attributes={"backend": self.backend, "key": key, "span_id": span_id},
        ) as span:
            with cache_op_latency.labels(self.backend, "get").time():
                try:
                    # Python 3.10+ safe way to enforce timeout
                    if sys.version_info >= (3, 11):
                        async with asyncio.timeout(5.0):
                            result = await self._get_impl(key)
                    else:
                        result = await asyncio.wait_for(self._get_impl(key), timeout=5.0)

                    if result is None:
                        cache_misses.labels(self.backend).inc()
                        span.add_event("cache_miss", attributes={"key": key})
                    else:
                        cache_hits.labels(self.backend).inc()
                        span.add_event("cache_hit", attributes={"key": key})
                    audit_logger.info(
                        "Cache operation",
                        operation="get",
                        backend=self.backend,
                        key=key,
                        status="success",
                        span_id=span_id,
                        data_classification="internal",
                    )
                    return result
                except Exception as e:
                    json_logger.error(
                        "Cache GET failed",
                        extra={
                            "data": {
                                "backend": self.backend,
                                "key": key,
                                "span_id": span_id,
                                "error": str(e),
                            }
                        },
                        exc_info=True,
                    )
                    span.record_exception(e)
                    audit_logger.error(
                        "Cache operation failed",
                        operation="get",
                        backend=self.backend,
                        key=key,
                        status="failure",
                        error=str(e),
                        span_id=span_id,
                        data_classification="internal",
                    )
                    return None

    @_retry_on_redis
    async def setex(self, key: str, ttl: int, val: Any) -> bool:
        """Sets a value with an expiration."""
        span_id = str(uuid.uuid4())
        with tracer.start_as_current_span(
            f"{self.backend}_cache_setex",
            attributes={
                "backend": self.backend,
                "key": key,
                "ttl": ttl,
                "span_id": span_id,
            },
        ) as span:
            with cache_op_latency.labels(self.backend, "setex").time():
                try:
                    if sys.version_info >= (3, 11):
                        async with asyncio.timeout(5.0):
                            success = await self._setex_impl(key, ttl, val)
                    else:
                        success = await asyncio.wait_for(
                            self._setex_impl(key, ttl, val), timeout=5.0
                        )

                    audit_logger.info(
                        "Cache operation",
                        operation="setex",
                        backend=self.backend,
                        key=key,
                        status="success" if success else "failure",
                        span_id=span_id,
                        data_classification="internal",
                    )
                    return success
                except Exception as e:
                    json_logger.error(
                        "Cache SETEX failed",
                        extra={
                            "data": {
                                "backend": self.backend,
                                "key": key,
                                "span_id": span_id,
                                "error": str(e),
                            }
                        },
                        exc_info=True,
                    )
                    span.record_exception(e)
                    audit_logger.error(
                        "Cache operation failed",
                        operation="setex",
                        backend=self.backend,
                        key=key,
                        status="failure",
                        error=str(e),
                        span_id=span_id,
                        data_classification="internal",
                    )
                    return False

    @_retry_on_redis
    async def incr(self, key: str) -> int:
        """Increments a numeric value."""
        span_id = str(uuid.uuid4())
        with tracer.start_as_current_span(
            f"{self.backend}_cache_incr",
            attributes={"backend": self.backend, "key": key, "span_id": span_id},
        ) as span:
            with cache_op_latency.labels(self.backend, "incr").time():
                try:
                    if sys.version_info >= (3, 11):
                        async with asyncio.timeout(5.0):
                            new_val = await self._incr_impl(key)
                    else:
                        new_val = await asyncio.wait_for(self._incr_impl(key), timeout=5.0)

                    audit_logger.info(
                        "Cache operation",
                        operation="incr",
                        backend=self.backend,
                        key=key,
                        status="success",
                        new_value=new_val,
                        span_id=span_id,
                        data_classification="internal",
                    )
                    return new_val
                except Exception as e:
                    json_logger.error(
                        "Cache INCR failed",
                        extra={
                            "data": {
                                "backend": self.backend,
                                "key": key,
                                "span_id": span_id,
                                "error": str(e),
                            }
                        },
                        exc_info=True,
                    )
                    span.record_exception(e)
                    audit_logger.error(
                        "Cache operation failed",
                        operation="incr",
                        backend=self.backend,
                        key=key,
                        status="failure",
                        error=str(e),
                        span_id=span_id,
                        data_classification="internal",
                    )
                    return 0

    async def _get_impl(self, key: str) -> Optional[Any]:
        raise NotImplementedError

    async def _setex_impl(self, key: str, ttl: int, val: Any) -> bool:
        raise NotImplementedError

    async def _incr_impl(self, key: str) -> int:
        raise NotImplementedError


class _InMemoryCache(_BaseCache):
    def __init__(self):
        super().__init__("in_memory")
        self._s: Dict[str, Any] = {}

    async def _get_impl(self, key: str) -> Optional[Any]:
        v = self._s.get(key)
        if isinstance(v, dict) and v.get("exp") and time.time() >= v["exp"]:
            self._s.pop(key, None)
            return None
        return v.get("v") if isinstance(v, dict) else v

    async def _setex_impl(self, key: str, ttl: int, val: Any) -> bool:
        self._s[key] = {"v": val, "exp": time.time() + ttl if ttl else None}
        return True

    async def _incr_impl(self, key: str) -> int:
        current_val = int(self._s.get(key, {}).get("v", 0) or 0)
        new_val = current_val + 1
        self._s[key] = {"v": new_val, "exp": self._s.get(key, {}).get("exp")}
        return new_val


class _FileCache(_BaseCache):
    def __init__(self, root: Path, secrets_manager: Any):
        super().__init__("file")
        self.root = root
        self.secrets_manager = secrets_manager
        self.dir = self.root / ".healer_cache"
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.hmac_key = self.secrets_manager.get_secret("FILE_CACHE_HMAC_KEY")
        if not self.hmac_key:
            if PRODUCTION_MODE:
                json_logger.critical(
                    "FILE_CACHE_HMAC_KEY not found in production. This is a security risk."
                )
            else:
                self.hmac_key = os.urandom(32).hex()
                json_logger.warning("Generated temporary FILE_CACHE_HMAC_KEY for development.")

    def _p(self, key: str) -> Path:
        """Generates a secure file path from a key."""
        return self.dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _sign_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Signs the cache payload with HMAC."""
        payload_str = json.dumps(payload, sort_keys=True).encode("utf-8")
        h = hashlib.sha256(self.hmac_key.encode() + payload_str).hexdigest()
        payload["hmac_sig"] = h
        return payload

    def _verify_payload(self, payload: Dict[str, Any]) -> bool:
        """Verifies the HMAC signature of the payload."""
        hmac_sig = payload.pop("hmac_sig", None)
        if not hmac_sig:
            json_logger.warning(
                "File cache entry missing HMAC signature",
                extra={"data": {"path": str(self._p("")), "payload": payload}},
            )
            return False
        payload_str = json.dumps(payload, sort_keys=True).encode("utf-8")
        h = hashlib.sha256(self.hmac_key.encode() + payload_str).hexdigest()
        return hmac_sig == h

    async def _get_impl(self, key: str) -> Optional[Any]:
        p = self._p(key)
        if not p.exists():
            return None
        try:
            payload = json.loads(p.read_text("utf-8"))
            if not self._verify_payload(payload):
                file_hmac_failures.inc()
                json_logger.error(
                    "File cache entry HMAC validation failed. Potential tampering.",
                    extra={"data": {"path": str(p)}},
                )
                with contextlib.suppress(Exception):
                    p.unlink()
                return None
            if payload.get("exp") and time.time() >= payload["exp"]:
                with contextlib.suppress(Exception):
                    p.unlink()
                return None
            return payload.get("v")
        except Exception:
            json_logger.error(
                "Error reading file cache entry",
                extra={"path": str(p), "exc_info": True},
            )
            return None

    async def _setex_impl(self, key: str, ttl: int, val: Any) -> bool:
        p = self._p(key)
        payload = self._sign_payload({"v": val, "exp": time.time() + ttl if ttl else None})
        with contextlib.suppress(Exception):
            p.write_text(json.dumps(payload), "utf-8")
        return True

    async def _incr_impl(self, key: str) -> int:
        json_logger.warning("Inefficient INCR call on file cache", extra={"key": key})
        # This implementation requires a read-modify-write cycle, which is not atomic.
        # For simplicity in this dummy file, we'll implement it. In production,
        # a warning is appropriate, and a more robust solution would be to use a
        # lock file or a more performant backend.
        p = self._p(key)
        current_val = 0
        payload = {}
        if p.exists():
            try:
                payload = json.loads(p.read_text("utf-8"))
                current_val = int(payload.get("v", 0) or 0)
            except (json.JSONDecodeError, ValueError):
                pass

        new_val = current_val + 1
        payload["v"] = new_val
        payload = self._sign_payload(payload)

        with contextlib.suppress(Exception):
            p.write_text(json.dumps(payload), "utf-8")

        return new_val


_CACHED_REDIS_CLIENT: Optional[Any] = None
_last_fallback_alert_time = 0


@_retry_on_redis
async def _connect_redis():
    """Connects to Redis with retry logic, timeouts, and security."""
    if not _HAS_REDIS or _redis is None:
        raise RuntimeError("Redis library not available")

    redis_host = _validate_env_var(
        "REDIS_HOST",
        os.getenv("REDIS_HOST", "localhost"),
        re.compile(r"^[a-zA-Z0-9\.\-_]+$"),
    )
    redis_port = _validate_env_var(
        "REDIS_PORT", os.getenv("REDIS_PORT", "6379"), re.compile(r"^\d{1,5}$")
    )
    redis_ssl = (
        _validate_env_var(
            "REDIS_SSL", os.getenv("REDIS_SSL", "false"), re.compile(r"^(true|false)$")
        )
        == "true"
        and PRODUCTION_MODE
    )

    password = SECRETS_MANAGER.get_secret("REDIS_PASSWORD", required=PRODUCTION_MODE)
    ssl_cert_file = SECRETS_MANAGER.get_secret(
        "REDIS_SSL_CERT", required=redis_ssl and PRODUCTION_MODE
    )
    ssl_key_file = SECRETS_MANAGER.get_secret(
        "REDIS_SSL_KEY", required=redis_ssl and PRODUCTION_MODE
    )

    with contextlib.suppress(Exception):
        json_logger.info(
            "Attempting to connect to Redis",
            extra={
                "host": redis_host,
                "port": redis_port,
                "ssl": redis_ssl,
                "data_classification": "internal",
            },
        )

    try:
        c = _redis.Redis(
            host=redis_host,
            port=int(redis_port),
            db=0,
            decode_responses=True,
            password=password,
            ssl=redis_ssl,
            ssl_cert_reqs="required" if redis_ssl else None,
            ssl_certfile=ssl_cert_file,
            ssl_keyfile=ssl_key_file,
            max_connections=10,
        )
        await asyncio.wait_for(c.ping(), timeout=5.0)

        json_logger.info(
            "Successfully connected to Redis",
            extra={
                "host": redis_host,
                "port": redis_port,
                "data_classification": "internal",
            },
        )
        return c
    except asyncio.TimeoutError:
        redis_connection_failures.inc()
        raise ConnectionError("Redis connection attempt timed out.")


async def _check_fallback_usage(backend_name: str, message: str):
    """Logs and alerts on fallback usage with rate limiting."""
    global _last_fallback_alert_time
    current_time = time.time()
    if current_time - _last_fallback_alert_time > 3600 or not PRODUCTION_MODE:
        alert_operator(message, level="WARNING")
        json_logger.warning(
            "Using fallback cache",
            extra={
                "backend": backend_name,
                "reason": message,
                "data_classification": "internal",
            },
        )
        _last_fallback_alert_time = current_time


async def get_cache(
    project_root: Optional[str] = None,
    *,
    whitelisted_plugin_dirs: Optional[Sequence[str]] = None,
):
    """
    Acquires a cache client, preferring Redis, then file-based, then in-memory.
    """
    global _CACHED_REDIS_CLIENT

    # Prefer Redis if possible (Singleton pattern)
    if _HAS_REDIS:
        if _CACHED_REDIS_CLIENT:
            try:
                await asyncio.wait_for(_CACHED_REDIS_CLIENT.ping(), timeout=5.0)
                return _CACHED_REDIS_CLIENT
            except (RedisError, ConnectionError, asyncio.TimeoutError):
                json_logger.warning(
                    "Stale Redis client detected. Disconnecting and retrying.",
                    extra={"data_classification": "internal"},
                )
                _CACHED_REDIS_CLIENT = None

        try:
            _CACHED_REDIS_CLIENT = await _connect_redis()
            return _CACHED_REDIS_CLIENT
        except RetryError as e:
            redis_connection_failures.inc()
            alert_operator(
                f"CRITICAL: Failed to connect to Redis after multiple retries: {e}. Using fallback cache.",
                level="CRITICAL",
            )
            json_logger.critical(
                "Redis connection failed. Falling back.",
                exc_info=True,
                extra={"data_classification": "internal"},
            )
            _CACHED_REDIS_CLIENT = None
        except Exception as e:
            redis_connection_failures.inc()
            await _check_fallback_usage(
                "file_cache", f"Redis not available: {e}. Using fallback cache."
            )

    # Fallback to file cache
    if project_root:
        try:
            if whitelisted_plugin_dirs:
                p = Path(project_root).resolve()
                roots = [Path(w).resolve() for w in whitelisted_plugin_dirs]
                if not any(str(p).startswith(str(r)) for r in roots):
                    await _check_fallback_usage(
                        "in_memory_cache",
                        f"project_root {project_root!r} not under whitelisted dirs; using in-memory cache.",
                    )
                    return _InMemoryCache()
            return _FileCache(Path(project_root), SECRETS_MANAGER)
        except Exception as e:
            await _check_fallback_usage(
                "in_memory_cache",
                f"File cache unavailable at {project_root}: {e}. Using in-memory cache.",
            )

    # Fallback to in-memory cache (last resort)
    await _check_fallback_usage("in_memory_cache", "Using in-memory cache as last resort.")
    return _InMemoryCache()
