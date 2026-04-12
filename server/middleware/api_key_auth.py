"""API key authentication middleware for the main server.

Provides a FastAPI dependency that validates the ``X-API-Key`` header
against a set of configured keys. When ``REQUIRE_API_KEY`` is not set
or is ``\"0\"``, authentication is bypassed (development mode).

The same dependency also enforces lightweight in-process HTTP protections
for the FastAPI surface:

- per-IP request throttling (default: 100 requests / 60 seconds)
- request body size limits using the declared ``Content-Length`` header
  (default: 50 MiB)

Usage in routers::

    from server.middleware.api_key_auth import require_api_key

    @router.get("/endpoint")
    async def handler(api_key: str = Depends(require_api_key)):
        ...

Environment variables:
    SERVER_API_KEYS: Comma-separated list of valid API keys.
    REQUIRE_API_KEY: Set to "1" to enforce (default "0" for dev).
    HTTP_RATE_LIMIT_ENABLED: Set to "0" to disable HTTP rate limiting.
    HTTP_RATE_LIMIT_REQUESTS: Max requests per client IP inside the window.
    HTTP_RATE_LIMIT_WINDOW_SECONDS: Sliding window size in seconds.
    HTTP_RATE_LIMIT_TRUST_PROXY_HEADERS: Trust X-Forwarded-For / X-Real-IP.
    MAX_REQUEST_BODY_SIZE_BYTES: Max allowed declared HTTP body size.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Optional

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

_DEFAULT_HTTP_RATE_LIMIT_REQUESTS = 100
_DEFAULT_HTTP_RATE_LIMIT_WINDOW_SECONDS = 60
_DEFAULT_MAX_REQUEST_BODY_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB

_RATE_LIMIT_EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/api/health",
        "/api/ready",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
        "/favicon.ico",
    }
)

_cached_keys: Optional[set[str]] = None
_rate_limit_lock = threading.Lock()
_request_windows: dict[str, Deque[float]] = defaultdict(deque)


def _get_valid_keys() -> set[str]:
    """Load valid API keys from environment (cached after first call)."""
    global _cached_keys  # noqa: PLW0603
    if _cached_keys is None:
        raw = os.environ.get("SERVER_API_KEYS", "")
        _cached_keys = {k.strip() for k in raw.split(",") if k.strip()}
    return _cached_keys


def _is_auth_required() -> bool:
    """Check if API key authentication is enforced."""
    return os.environ.get("REQUIRE_API_KEY", "0") == "1"


def _env_flag(name: str, default: str = "0") -> bool:
    """Parse a boolean environment flag using common truthy values."""
    return os.environ.get(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parse_positive_int_env(name: str, default: int) -> int:
    """Return a positive integer env var value, or *default* when invalid."""
    raw = os.environ.get(name)
    if raw is None:
        return default

    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid integer value for %s=%r. Using default=%d.",
            name,
            raw,
            default,
        )
        return default

    if value <= 0:
        logger.warning(
            "Non-positive integer value for %s=%r. Using default=%d.",
            name,
            raw,
            default,
        )
        return default

    return value


def _is_rate_limit_enabled() -> bool:
    """Return True when HTTP throttling is enabled."""
    return _env_flag("HTTP_RATE_LIMIT_ENABLED", "1")


def _should_trust_proxy_headers() -> bool:
    """Return True when proxy-derived client IP headers may be trusted."""
    return _env_flag("HTTP_RATE_LIMIT_TRUST_PROXY_HEADERS", "0")


def _get_rate_limit_max_requests() -> int:
    """Return the configured per-IP request budget for the HTTP window."""
    return _parse_positive_int_env(
        "HTTP_RATE_LIMIT_REQUESTS",
        _DEFAULT_HTTP_RATE_LIMIT_REQUESTS,
    )


def _get_rate_limit_window_seconds() -> int:
    """Return the configured HTTP sliding-window size in seconds."""
    return _parse_positive_int_env(
        "HTTP_RATE_LIMIT_WINDOW_SECONDS",
        _DEFAULT_HTTP_RATE_LIMIT_WINDOW_SECONDS,
    )


def _get_max_request_body_size_bytes() -> int:
    """Return the configured max allowed declared HTTP body size."""
    return _parse_positive_int_env(
        "MAX_REQUEST_BODY_SIZE_BYTES",
        _DEFAULT_MAX_REQUEST_BODY_SIZE_BYTES,
    )


def _is_rate_limit_exempt_path(path: str) -> bool:
    """Return True when a path should bypass HTTP throttling."""
    return path in _RATE_LIMIT_EXEMPT_PATHS or path.startswith("/static/")


def _get_client_ip(request: Request) -> str:
    """Resolve the caller IP from the request, optionally trusting proxy headers."""
    if _should_trust_proxy_headers():
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            first_hop = forwarded_for.split(",", 1)[0].strip()
            if first_hop:
                return first_hop

        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def _enforce_request_body_size_limit(request: Request) -> None:
    """Reject oversized HTTP requests using the declared Content-Length value."""
    content_length = request.headers.get("content-length")
    if content_length is None:
        return

    try:
        declared_size = int(content_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid Content-Length header.",
        ) from exc

    if declared_size < 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid Content-Length header.",
        )

    max_bytes = _get_max_request_body_size_bytes()
    if declared_size <= max_bytes:
        return

    client_ip = _get_client_ip(request)
    logger.warning(
        "Rejected oversized HTTP request from %s to %s: declared_size=%d max=%d",
        client_ip,
        request.url.path,
        declared_size,
        max_bytes,
    )
    raise HTTPException(
        status_code=413,
        detail=f"Request body too large. Limit is {max_bytes} bytes.",
    )


def _enforce_rate_limit(request: Request) -> None:
    """Enforce a per-IP in-process HTTP sliding-window rate limit."""
    if not _is_rate_limit_enabled():
        return

    if _is_rate_limit_exempt_path(request.url.path):
        return

    client_ip = _get_client_ip(request)
    max_requests = _get_rate_limit_max_requests()
    window_seconds = _get_rate_limit_window_seconds()
    now = time.monotonic()

    with _rate_limit_lock:
        request_times = _request_windows[client_ip]

        while request_times and (now - request_times[0]) >= window_seconds:
            request_times.popleft()

        if len(request_times) >= max_requests:
            oldest_request_age = now - request_times[0]
            retry_after_seconds = max(
                1,
                math.ceil(window_seconds - oldest_request_age),
            )
            logger.warning(
                "HTTP rate limit exceeded for %s on %s: count=%d max=%d window=%ds",
                client_ip,
                request.url.path,
                len(request_times),
                max_requests,
                window_seconds,
            )
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded. Max {max_requests} requests per "
                    f"{window_seconds} seconds."
                ),
                headers={"Retry-After": str(retry_after_seconds)},
            )

        request_times.append(now)


async def require_api_key(
    request: Optional[Request] = None,
    api_key: Optional[str] = Security(_API_KEY_HEADER),
) -> Optional[str]:
    """FastAPI dependency — validates X-API-Key header.

    Returns the validated key on success. Raises HTTP 401/403 on failure.
    When REQUIRE_API_KEY != "1", returns None (auth bypassed).

    When *request* is available (normal FastAPI execution), the dependency also
    enforces HTTP throttling and request-size checks before route handlers run.
    """
    if request is not None:
        _enforce_request_body_size_limit(request)
        _enforce_rate_limit(request)

    if not _is_auth_required():
        return None

    valid_keys = _get_valid_keys()
    if not valid_keys:
        logger.error("REQUIRE_API_KEY=1 but SERVER_API_KEYS is empty")
        raise HTTPException(
            status_code=503,
            detail="Authentication not configured. Set SERVER_API_KEYS.",
        )

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header.",
        )

    if api_key not in valid_keys:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key.",
        )

    return api_key


def reset_key_cache() -> None:
    """Clear cached keys (useful for tests)."""
    global _cached_keys  # noqa: PLW0603
    _cached_keys = None


def reset_rate_limit_state() -> None:
    """Clear in-memory rate-limit state (useful for tests)."""
    with _rate_limit_lock:
        _request_windows.clear()
