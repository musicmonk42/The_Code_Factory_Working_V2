# omnicore_engine/message_bus/rate_limit.py
"""
Rate Limiting components for the Sharded Message Bus.

Provides a **concurrent**, **per-client**, **sliding-window** rate limiter with:
* Thread-safe, async-safe in-memory storage (Redis optional)
* Configurable per-client limits (global + overrides)
* Prometheus metrics (publish, block, error)
* Retry-after headers & structured exceptions
* Graceful fallback when Redis is unavailable
* Integration-ready with ShardedMessageBus (via pre-publish hook)

Upgrades from original:
- Redis-backed distributed rate limiting (optional)
- Per-client configuration (max_requests, window)
- Prometheus metrics
- Structured `RateLimitExceeded` with `retry_after`
- Async-safe per-client locks (no global bottleneck)
- Configurable cleanup interval
- Health endpoint
- **Fixed `RateLimitError` → `RateLimitExceeded` inheritance order**
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

import structlog

# Optional Redis (for distributed rate limiting)
try:
    import redis.asyncio as redis
    from redis.exceptions import ConnectionError, TimeoutError

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _REDIS_AVAILABLE = False
    redis = None
    ConnectionError = TimeoutError = None

# Optional Prometheus
try:
    from prometheus_client import Counter, Gauge

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = Gauge = None

# --- Structured logging ---
logger = structlog.get_logger(__name__)
logger = logger.bind(component="RateLimiter")


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
@dataclass
class RateLimitConfig:
    """Per-client rate limit configuration."""

    max_requests: int = 1000
    window_seconds: int = 60
    redis_ttl_seconds: int = 120  # Keep keys a bit longer than window


class RateLimiterConfig:
    """
    Global rate limiter configuration.
    """

    def __init__(
        self,
        default_max_requests: int = 1000,
        default_window_seconds: int = 60,
        redis_url: Optional[str] = None,
        redis_pool_size: int = 10,
        redis_timeout: float = 2.0,
        cleanup_interval: float = 300.0,
        enable_metrics: bool = True,
    ):
        self.default = RateLimitConfig(
            max_requests=default_max_requests,
            window_seconds=default_window_seconds,
            redis_ttl_seconds=default_window_seconds * 2,
        )
        self.redis_url = redis_url
        self.redis_pool_size = redis_pool_size
        self.redis_timeout = redis_timeout
        self.cleanup_interval = cleanup_interval
        self.enable_metrics = enable_metrics

        # Per-client overrides
        self.client_overrides: Dict[str, RateLimitConfig] = {}

    def set_client_limit(self, client_id: str, max_requests: int, window_seconds: int) -> None:
        """Override limits for a specific client."""
        self.client_overrides[client_id] = RateLimitConfig(
            max_requests=max_requests,
            window_seconds=window_seconds,
            redis_ttl_seconds=window_seconds * 2,
        )

    def get_config(self, client_id: str) -> RateLimitConfig:
        """Return effective config for a client."""
        return self.client_overrides.get(client_id, self.default)


# --------------------------------------------------------------------------- #
#  Metrics
# --------------------------------------------------------------------------- #
if _PROMETHEUS_AVAILABLE and Counter is not None:  # pragma: no cover
    METRIC_RATE_LIMIT_TOTAL = Counter(
        "message_bus_rate_limit_total",
        "Total rate limit checks",
        ["client_id", "result"],  # allowed, blocked, error
    )
    METRIC_RATE_LIMIT_ACTIVE_CLIENTS = Gauge(
        "message_bus_rate_limit_active_clients",
        "Number of clients with active request windows",
    )
else:  # pragma: no cover
    METRIC_RATE_LIMIT_TOTAL = None
    METRIC_RATE_LIMIT_ACTIVE_CLIENTS = None


def _inc_metric(result: str, client_id: str = "unknown") -> None:
    if METRIC_RATE_LIMIT_TOTAL:
        try:
            METRIC_RATE_LIMIT_TOTAL.labels(client_id=client_id, result=result).inc()
        except Exception:
            pass


def _set_active_clients(count: int) -> None:
    if METRIC_RATE_LIMIT_ACTIVE_CLIENTS:
        try:
            METRIC_RATE_LIMIT_ACTIVE_CLIENTS.set(count)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Base Exception (must be defined before RateLimitExceeded)
# --------------------------------------------------------------------------- #
class RateLimitError(Exception):
    """Base exception for rate limiting errors."""

    pass


# --------------------------------------------------------------------------- #
#  Specific Exception
# --------------------------------------------------------------------------- #
class RateLimitExceeded(RateLimitError):
    """Raised when rate limit is exceeded. Includes retry-after."""

    def __init__(self, client_id: str, retry_after: float):
        self.client_id = client_id
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded for {client_id}. Retry after {retry_after:.2f}s.")


# --------------------------------------------------------------------------- #
#  Core RateLimiter
# --------------------------------------------------------------------------- #
class RateLimiter:
    """
    Async, distributed sliding-window rate limiter.
    """

    def __init__(self, config: RateLimiterConfig):
        self.config = config
        self._in_memory: Dict[str, List[float]] = defaultdict(list)
        self._client_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._redis: Optional[redis.Redis] = None
        self._redis_enabled = bool(config.redis_url and _REDIS_AVAILABLE)
        self._cleanup_task: Optional[asyncio.Task] = None
        self._running = False

        if self._redis_enabled:
            self._redis = redis.from_url(
                config.redis_url,
                max_connections=config.redis_pool_size,
                socket_timeout=config.redis_timeout,
                socket_connect_timeout=config.redis_timeout,
            )

        logger.info(
            "RateLimiter initialized.",
            redis_enabled=self._redis_enabled,
            default_max=self.config.default.max_requests,
            default_window=self.config.default.window_seconds,
        )

    # ------------------------------------------------------------------- #
    #  Lifecycle
    # ------------------------------------------------------------------- #
    async def start(self) -> None:
        """Start background cleanup task."""
        if self._running:
            return
        self._running = True
        if self.config.cleanup_interval > 0:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info("RateLimiter started.")

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop cleanup task and release resources."""
        if not self._running:
            return
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=timeout)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if self._redis:
            await self._redis.close()
        logger.info("RateLimiter stopped.")

    async def _periodic_cleanup(self) -> None:
        """Background task to prune old in-memory entries."""
        while self._running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                now = time.time()
                expired = [
                    cid
                    for cid, times in self._in_memory.items()
                    if times and now - times[0] >= self.config.default.window_seconds
                ]
                for cid in expired:
                    del self._in_memory[cid]
                    self._client_locks.pop(cid, None)
                if expired and self.config.enable_metrics:
                    _set_active_clients(len(self._in_memory))
                logger.debug("Cleaned up expired rate limit entries.", count=len(expired))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in rate limiter cleanup.", exc_info=e)

    # ------------------------------------------------------------------- #
    #  Core check
    # ------------------------------------------------------------------- #
    async def check_rate_limit(self, client_id: str) -> bool:
        """
        Check if client is within rate limit.

        Returns:
            True if allowed.

        Raises:
            RateLimitExceeded: with `retry_after` if blocked.
        """
        cfg = self.config.get_config(client_id)
        now = time.time()

        # Use Redis if available, otherwise in-memory
        if self._redis_enabled:
            return await self._check_redis(client_id, cfg, now)
        else:
            return await self._check_in_memory(client_id, cfg, now)

    async def _check_in_memory(self, client_id: str, cfg: RateLimitConfig, now: float) -> bool:
        """In-memory sliding window check."""
        lock = self._client_locks[client_id]
        async with lock:
            # Prune expired
            self._in_memory[client_id] = [
                t for t in self._in_memory[client_id] if now - t < cfg.window_seconds
            ]
            count = len(self._in_memory[client_id])

            if count >= cfg.max_requests:
                retry_after = cfg.window_seconds - (now - self._in_memory[client_id][0])
                _inc_metric("blocked", client_id)
                logger.warning(
                    "Rate limit exceeded (in-memory).",
                    client_id=client_id,
                    count=count,
                    limit=cfg.max_requests,
                    retry_after=retry_after,
                )
                raise RateLimitExceeded(client_id, retry_after)

            self._in_memory[client_id].append(now)
            _inc_metric("allowed", client_id)
            if self.config.enable_metrics:
                _set_active_clients(len(self._in_memory))
            return True

    async def _check_redis(self, client_id: str, cfg: RateLimitConfig, now: float) -> bool:
        """Redis-backed distributed check using Lua script for atomicity."""
        script = """
        local key = KEYS[1]
        local window = tonumber(ARGV[1])
        local max_req = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local ttl = tonumber(ARGV[4])

        -- Remove expired
        redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)

        local count = redis.call('ZCARD', key)
        if count >= max_req then
            local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2]
            local retry_after = window - (now - tonumber(oldest))
            return {0, retry_after}
        else
            redis.call('ZADD', key, now, now)
            redis.call('EXPIRE', key, ttl)
            return {1, 0}
        end
        """
        key = f"ratelimit:{client_id}"
        try:
            result = await self._redis.eval(
                script,
                1,
                key,
                cfg.window_seconds,
                cfg.max_requests,
                now,
                cfg.redis_ttl_seconds,
            )
            allowed, retry_after = result[0], result[1]
            if not allowed:
                _inc_metric("blocked", client_id)
                logger.warning(
                    "Rate limit exceeded (Redis).",
                    client_id=client_id,
                    retry_after=retry_after,
                )
                raise RateLimitExceeded(client_id, retry_after)
            _inc_metric("allowed", client_id)
            return True
        except (ConnectionError, TimeoutError) as e:
            logger.error("Redis rate limit check failed, falling back to in-memory.", exc_info=e)
            _inc_metric("error", client_id)
            return await self._check_in_memory(client_id, cfg, now)
        except Exception as e:
            logger.error("Unexpected Redis rate limit error.", exc_info=e)
            _inc_metric("error", client_id)
            return await self._check_in_memory(client_id, cfg, now)

    # ------------------------------------------------------------------- #
    #  Health
    # ------------------------------------------------------------------- #
    async def health(self) -> Dict[str, Any]:
        """Return health status."""
        redis_status = "disabled"
        if self._redis_enabled and self._redis:
            try:
                await self._redis.ping()
                redis_status = "connected"
            except Exception:
                redis_status = "error"
        return {
            "running": self._running,
            "redis": redis_status,
            "active_clients": len(self._in_memory),
            "client_overrides": list(self.config.client_overrides.keys()),
        }


# --------------------------------------------------------------------------- #
#  Integration Hook for ShardedMessageBus
# --------------------------------------------------------------------------- #
async def rate_limit_middleware(
    limiter: RateLimiter,
    message: "Message",
    client_id: Optional[str] = None,
) -> "Message":
    """
    Pre-publish hook to enforce rate limiting.

    Usage in ShardedMessageBus:
        bus.add_pre_publish_hook(partial(rate_limit_middleware, limiter))
    """

    cid = client_id or getattr(message, "client_id", None) or "anonymous"
    try:
        await limiter.check_rate_limit(cid)
    except RateLimitExceeded as exc:
        # Attach retry info to message for downstream handling
        message.context["rate_limit_retry_after"] = exc.retry_after
        message.context["rate_limit_exceeded"] = True
        raise
    return message


# --- End of File ---
