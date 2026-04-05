"""API key authentication middleware for the main server.

Provides a FastAPI dependency that validates the ``X-API-Key`` header
against a set of configured keys. When ``REQUIRE_API_KEY`` is not set
or is ``"0"``, authentication is bypassed (development mode).

Usage in routers::

    from server.middleware.api_key_auth import require_api_key

    @router.get("/endpoint")
    async def handler(api_key: str = Depends(require_api_key)):
        ...

Environment variables:
    SERVER_API_KEYS: Comma-separated list of valid API keys.
    REQUIRE_API_KEY: Set to "1" to enforce (default "0" for dev).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

_cached_keys: Optional[set] = None


def _get_valid_keys() -> set:
    """Load valid API keys from environment (cached after first call)."""
    global _cached_keys  # noqa: PLW0603
    if _cached_keys is None:
        raw = os.environ.get("SERVER_API_KEYS", "")
        _cached_keys = {k.strip() for k in raw.split(",") if k.strip()}
    return _cached_keys


def _is_auth_required() -> bool:
    """Check if API key authentication is enforced."""
    return os.environ.get("REQUIRE_API_KEY", "0") == "1"


async def require_api_key(
    api_key: Optional[str] = Security(_API_KEY_HEADER),
) -> Optional[str]:
    """FastAPI dependency — validates X-API-Key header.

    Returns the validated key on success. Raises HTTP 401/403 on failure.
    When REQUIRE_API_KEY != "1", returns None (auth bypassed).
    """
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
