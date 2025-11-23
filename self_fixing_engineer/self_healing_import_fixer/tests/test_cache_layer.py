"""
test_cache_layer.py

Unit tests for the cache abstraction in
`self_healing_import_fixer.import_fixer.cache_layer`.

Covers:
- Selection logic: Redis → File → In-Memory
- Basic operations: get / setex / incr
- Expiration behaviour for file and memory caches
- Fallback warning throttling helper
- Redis path success and failure (fully mocked; no real Redis required)

These tests intentionally import using fully-qualified package paths so the
module is tested in-place inside the package layout.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the cache layer via the package-qualified path
from self_healing_import_fixer.import_fixer.cache_layer import (
    _connect_redis,
    _FileCache,
    _InMemoryCache,
    get_cache,
)

PKG_PATH = "self_healing_import_fixer.import_fixer.cache_layer"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeRedisClient:
    """Minimal async-friendly Redis-like client for tests."""

    def __init__(self):
        self._store: dict[str, Any] = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        # ignore TTL semantics here; selection logic is what we care about
        self._store[key] = value
        return True

    async def incr(self, key: str) -> int:
        self._store[key] = int(self._store.get(key, 0) or 0) + 1
        return self._store[key]


class FakeRedisModule:
    """Provides a .Redis() returning our FakeRedisClient"""

    def __init__(self):
        self._client = FakeRedisClient()

    def Redis(self, *a, **k):
        return self._client


# ---------------------------------------------------------------------------
# Fixtures to properly mock dependencies
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_metrics():
    """Mock the metrics to avoid AttributeError"""
    mock_counter = MagicMock()
    mock_counter.labels.return_value = mock_counter
    mock_counter.inc.return_value = None

    mock_histogram = MagicMock()
    mock_histogram.labels.return_value = mock_histogram
    mock_histogram.time.return_value.__enter__ = MagicMock()
    mock_histogram.time.return_value.__exit__ = MagicMock()
    mock_histogram.observe.return_value = None

    with (
        patch(f"{PKG_PATH}.cache_hits", mock_counter),
        patch(f"{PKG_PATH}.cache_misses", mock_counter),
        patch(f"{PKG_PATH}.cache_op_latency", mock_histogram),
        patch(f"{PKG_PATH}.redis_connection_failures", mock_counter),
        patch(f"{PKG_PATH}.file_hmac_failures", mock_counter),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_loggers():
    """Mock the json_logger to avoid kwargs issues"""
    mock_json_logger = MagicMock()
    mock_json_logger.info = MagicMock()
    mock_json_logger.warning = MagicMock()
    mock_json_logger.error = MagicMock()
    mock_json_logger.critical = MagicMock()

    with patch(f"{PKG_PATH}.json_logger", mock_json_logger):
        yield mock_json_logger


@pytest.fixture(autouse=True)
def mock_audit_logger():
    """Mock the audit logger"""
    mock_audit = MagicMock()
    mock_audit.info = MagicMock()
    mock_audit.error = MagicMock()

    with patch(f"{PKG_PATH}.audit_logger", mock_audit):
        yield mock_audit


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cache_uses_in_memory_when_no_redis_and_no_project_root(monkeypatch):
    # Pretend redis cannot import / isn't present
    with patch(f"{PKG_PATH}._HAS_REDIS", False):
        cache = await get_cache(project_root=None)
        assert isinstance(cache, _InMemoryCache)

        # Basic roundtrip
        await cache.setex("k1", 1, "v1")
        assert await cache.get("k1") == "v1"
        assert await cache.get("missing") is None
        assert await cache.incr("ctr") == 1
        assert await cache.incr("ctr") == 2


@pytest.mark.asyncio
async def test_get_cache_uses_file_cache_when_project_root_provided(
    tmp_path, monkeypatch
):
    with patch(f"{PKG_PATH}._HAS_REDIS", False):
        root = tmp_path / "proj"
        root.mkdir(parents=True)

        cache = await get_cache(project_root=str(root))
        assert isinstance(cache, _FileCache)

        # set/get with TTL, ensure the cache file is created
        await cache.setex("k2", 60, "v2")
        assert await cache.get("k2") == "v2"

        # ensure something was written under .healer_cache (or whatever the file impl uses)
        files = list(root.rglob("*.json"))
        assert files, "expected file cache to create a json file on disk"


@pytest.mark.asyncio
async def test_get_cache_prefers_redis_when_available(monkeypatch):
    fake_redis_mod = FakeRedisModule()

    with (
        patch(f"{PKG_PATH}._HAS_REDIS", True),
        patch(f"{PKG_PATH}._redis", fake_redis_mod),
        patch(f"{PKG_PATH}.json_logger.info"),
    ):
        cache = await get_cache(project_root=None)
        # The redis path returns the underlying client (FakeRedisClient)
        assert hasattr(cache, "ping")
        await cache.setex("k3", 60, "v3")
        assert await cache.get("k3") == "v3"
        assert await cache.incr("ctr") == 1


# ---------------------------------------------------------------------------
# InMemory & File cache behaviours
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_cache_expiration():
    c = _InMemoryCache()
    await c.setex("exp", 1, "soon-gone")
    assert await c.get("exp") == "soon-gone"
    # advance time by sleeping; a short sleep keeps tests snappy on CI
    await asyncio.sleep(1.1)
    assert await c.get("exp") is None


@pytest.mark.asyncio
async def test_file_cache_roundtrip_and_expiration(tmp_path):
    class _Secrets:
        def get_secret(self, key: str):
            return "dev-hmac-key"

    cache = _FileCache(
        tmp_path, secrets_manager=_Secrets()
    )  # secrets required by implementation
    await cache.setex("fk", 1, json.dumps({"a": 1}))
    assert json.loads(await cache.get("fk")) == {"a": 1}
    await asyncio.sleep(1.1)
    assert await cache.get("fk") is None


# ---------------------------------------------------------------------------
# _connect_redis success and failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test__connect_redis_success(monkeypatch):
    """Ensure _connect_redis returns a client and pings it."""
    fake = FakeRedisModule()
    with (
        patch(f"{PKG_PATH}._HAS_REDIS", True),
        patch(f"{PKG_PATH}._redis", fake),
        patch(f"{PKG_PATH}.json_logger.info"),
    ):
        client = await _connect_redis()
        assert hasattr(client, "ping")
        assert await client.ping() is True


@pytest.mark.asyncio
async def test__connect_redis_failure(monkeypatch):
    """Simulate a ping failure → _connect_redis should raise or bubble cleanly."""

    class BadClient:
        async def ping(self):
            raise RuntimeError("boom")

    class BadRedis:
        def Redis(self, *a, **k):
            return BadClient()

    with patch(f"{PKG_PATH}._HAS_REDIS", True), patch(f"{PKG_PATH}._redis", BadRedis()):
        with pytest.raises(Exception):
            await _connect_redis(timeout_s=1)


# ---------------------------------------------------------------------------
# Fallback warning throttling (helper is internal; we assert it's being called,
# not its internal rate limiter implementation).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_warning_helper_is_called(monkeypatch):
    # Force the "no redis" path and ensure the fallback helper is invoked at least once
    with (
        patch(f"{PKG_PATH}._HAS_REDIS", False),
        patch(f"{PKG_PATH}._check_fallback_usage", new=AsyncMock()) as mock_fallback,
    ):
        await get_cache(project_root=None)
        mock_fallback.assert_awaited()
        # call many times to ensure it doesn't explode; we don't assert call count
        for _ in range(10):
            await get_cache(project_root=None)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--asyncio-mode=auto"]))
