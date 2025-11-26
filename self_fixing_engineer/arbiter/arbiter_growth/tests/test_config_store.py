import asyncio
import hashlib
import json
import logging
from unittest.mock import MagicMock, patch

import aiofiles
import pytest
import pytest_asyncio

# Assuming all modules are in a discoverable path
from arbiter.arbiter_growth.config_store import ConfigStore, TokenBucketRateLimiter

# --- Fixtures ---


@pytest_asyncio.fixture
async def mock_etcd_client():
    """A fixture that patches the etcd3.client and returns the mock instance."""
    with patch("etcd3.client") as mock_constructor:
        mock_instance = MagicMock()
        # Default successful return value
        mock_instance.get.return_value = (b"etcd_value", None)

        # Make the watch method an async generator
        async def mock_watch_prefix(*args, **kwargs):
            yield

        mock_instance.watch_prefix = mock_watch_prefix
        mock_constructor.return_value = mock_instance
        yield mock_instance


@pytest_asyncio.fixture
async def config_store_defaults():
    """Provides a ConfigStore that will only use hardcoded defaults."""
    with patch("etcd3.client", side_effect=Exception("etcd init failed")):
        yield ConfigStore(fallback_path="/non/existent/path.json")


@pytest_asyncio.fixture
async def config_store_with_fallback(tmp_path, mocker):
    """Provides a ConfigStore that fails etcd and uses a temporary fallback file."""
    fallback_file = tmp_path / "fallback.json"
    checksum_file = tmp_path / "fallback.json.sha256"
    fallback_data = {"fallback_key": "fallback_value"}

    # Write fallback file and its checksum for integrity checks
    content_str = json.dumps(fallback_data)
    content_bytes = content_str.encode("utf-8")
    fallback_file.write_bytes(content_bytes)

    computed_hash = hashlib.sha256(content_bytes).hexdigest()
    checksum_file.write_text(computed_hash)

    # 1. Create a mock file for text reads ('r')
    mock_text_file = mocker.AsyncMock()  # noqa: F821 - pytest fixture
    mock_text_file.read = mocker.AsyncMock(  # noqa: F821 - pytest fixture
        return_value=content_str
    )
    mock_text_file.__aenter__.return_value = mock_text_file
    mock_text_file.__aexit__ = mocker.AsyncMock()  # noqa: F821 - pytest fixture

    # 2. Create a mock file for binary reads ('rb')
    mock_bin_file = mocker.AsyncMock()  # noqa: F821 - pytest fixture
    mock_bin_file.read = mocker.AsyncMock(  # noqa: F821 - pytest fixture
        return_value=content_bytes
    )
    mock_bin_file.__aenter__.return_value = mock_bin_file
    mock_bin_file.__aexit__ = mocker.AsyncMock()  # noqa: F821 - pytest fixture

    # 3. Create a mock file for the checksum read
    mock_checksum_file = mocker.AsyncMock()  # noqa: F821 - pytest fixture
    mock_checksum_file.read = mocker.AsyncMock(  # noqa: F821 - pytest fixture
        return_value=computed_hash
    )
    mock_checksum_file.__aenter__.return_value = mock_checksum_file
    mock_checksum_file.__aexit__ = mocker.AsyncMock()  # noqa: F821 - pytest fixture

    # 4. Create a side_effect function for aiofiles.open
    # FIX: This MUST be a 'def', not 'async def'
    def open_side_effect(path, mode="r", **kwargs):
        path_str = str(path)
        if path_str == str(fallback_file):
            # Return text mock for 'r', binary mock for 'rb'
            return mock_text_file if mode == "r" else mock_bin_file
        if path_str == str(checksum_file):
            return mock_checksum_file
        # Fallback for any unexpected open() calls
        return mocker.AsyncMock()  # noqa: F821 - pytest fixture

    # We patch aiofiles.open where it is *used*
    patch_target = "arbiter.arbiter_growth.config_store.aiofiles.open"
    with patch(patch_target, side_effect=open_side_effect):
        with patch("etcd3.client", side_effect=Exception("etcd fail")):
            # Also mock os.path.exists
            with patch("os.path.exists", return_value=True):
                yield ConfigStore(fallback_path=str(fallback_file))


@pytest_asyncio.fixture
async def config_store_with_etcd(mock_etcd_client):
    """Provides a ConfigStore connected to a mock etcd client."""
    # Create the store without starting the watcher
    store = ConfigStore()
    # Manually start the watcher in the async context
    await store.start_watcher()
    yield store
    await store.stop_watcher()


@pytest_asyncio.fixture
async def rate_limiter(config_store_defaults):
    """Provides a TokenBucketRateLimiter with a default config."""
    # Ensure default values are "loaded" into the cache for the test
    store = config_store_defaults
    store.defaults["rate_limit_tokens"] = 5.0
    store.defaults["rate_limit_refill_rate"] = 1.0
    store.defaults["rate_limit_timeout"] = 2.0

    limiter = TokenBucketRateLimiter(store)
    # Initialize with a known good state
    limiter.last_refill = asyncio.get_event_loop().time()
    limiter.tokens = 5.0
    return limiter


# --- Unit Tests for ConfigStore ---


@pytest.mark.asyncio
async def test_init_no_etcd(caplog):
    """Tests that ConfigStore handles etcd initialization failure gracefully."""
    with patch("etcd3.client", side_effect=Exception("etcd init failed")):
        with caplog.at_level(logging.ERROR):
            cs = ConfigStore()
            assert cs.client is None
            assert "Failed to initialize etcd client" in caplog.text


@pytest.mark.asyncio
async def test_get_config_uses_default_when_all_fails(config_store_defaults, caplog):
    """Tests that a hardcoded default is used when etcd and fallback fail."""
    with caplog.at_level(logging.WARNING):
        store = config_store_defaults
        value = await store.get_config("flush_interval_min")
        assert value == 2.0
        assert "Using hardcoded default for config 'flush_interval_min'" in caplog.text


@pytest.mark.asyncio
async def test_get_config_from_etcd_successfully(
    config_store_with_etcd, mock_etcd_client
):
    """Tests successful retrieval of a config value from etcd."""
    mock_etcd_client.get.return_value = (b"42.0", None)

    value = await config_store_with_etcd.get_config("test_key")

    assert value == 42.0
    mock_etcd_client.get.assert_called_with("test_key")
    assert config_store_with_etcd._cache["test_key"][0] == 42.0


@pytest.mark.asyncio
async def test_get_config_etcd_fails_then_uses_fallback(
    config_store_with_fallback, caplog
):
    """Tests that the fallback file is used when etcd is unavailable."""
    with caplog.at_level(logging.WARNING):
        store = config_store_with_fallback
        value = await store.get_config("fallback_key")
        assert value == "fallback_value"
        assert "Using fallback config for 'fallback_key'" in caplog.text


@pytest.mark.asyncio
async def test_get_config_uses_cache_on_second_call(
    config_store_with_etcd, mock_etcd_client
):
    """Tests that a cached value is returned without calling etcd again."""
    mock_etcd_client.get.return_value = (b"cached_value", None)

    await config_store_with_etcd.get_config("cached_key")
    mock_etcd_client.get.assert_called_once()

    value = await config_store_with_etcd.get_config("cached_key")
    assert value == "cached_value"
    mock_etcd_client.get.assert_called_once()


@pytest.mark.asyncio
async def test_get_config_refetches_after_cache_expires(
    config_store_with_etcd, mock_etcd_client
):
    """Tests that an expired cache entry triggers a new etcd fetch."""
    config_store_with_etcd.cache_ttl = -1

    mock_etcd_client.get.return_value = (b"value1", None)
    await config_store_with_etcd.get_config("expire_key")

    mock_etcd_client.get.return_value = (b"value2", None)
    await config_store_with_etcd.get_config("expire_key")

    assert mock_etcd_client.get.call_count == 2


@pytest.mark.asyncio
async def test_get_config_raises_key_error_if_not_found(config_store_defaults):
    """Tests that a KeyError is raised for a key that doesn't exist anywhere."""
    store = config_store_defaults
    with pytest.raises(KeyError):
        await store.get_config("this_key_does_not_exist")


@pytest.mark.asyncio
async def test_etcd_retry_logic_succeeds(config_store_with_etcd, mock_etcd_client):
    """Tests that the internal retry mechanism for fetching from etcd works."""
    mock_etcd_client.get.side_effect = [
        Exception("Connection failed"),
        Exception("Still failing"),
        (b"final_value", None),
    ]

    value = await config_store_with_etcd.get_config("retry_key")

    assert value == "final_value"
    assert mock_etcd_client.get.call_count == 3


# --- Tests for TokenBucketRateLimiter ---


@pytest.mark.asyncio
async def test_rate_limiter_acquire_immediately(rate_limiter):
    """Tests immediate token acquisition when tokens are available."""
    limiter = rate_limiter
    assert await limiter.acquire() is True
    assert limiter.tokens == 4.0


@pytest.mark.asyncio
async def test_rate_limiter_blocks_then_succeeds(config_store_defaults):
    """Tests that the limiter blocks and waits for a token to be refilled."""
    # Create a fresh limiter for this test
    store = config_store_defaults
    store.defaults["rate_limit_tokens"] = 5.0
    store.defaults["rate_limit_refill_rate"] = 1.0
    store.defaults["rate_limit_timeout"] = 2.0

    limiter = TokenBucketRateLimiter(store)

    # Manually initialize to have control over the state
    limiter.last_refill = asyncio.get_event_loop().time()
    limiter.tokens = 0.5  # Start with 0.5 tokens

    start_time = asyncio.get_event_loop().time()
    assert await limiter.acquire(timeout=2.0) is True
    end_time = asyncio.get_event_loop().time()

    # Should wait approximately 0.5 seconds (0.5 tokens needed at 1 token/sec)
    # Allow some tolerance for timing
    wait_time = end_time - start_time
    assert 0.4 <= wait_time <= 0.6, f"Expected wait time ~0.5s, got {wait_time:.3f}s"


@pytest.mark.asyncio
async def test_rate_limiter_times_out(config_store_defaults):
    """Tests that acquire returns False if a token cannot be acquired within the timeout."""
    # Create a fresh limiter for this test
    store = config_store_defaults
    store.defaults["rate_limit_tokens"] = 5.0
    store.defaults["rate_limit_refill_rate"] = 1.0
    store.defaults["rate_limit_timeout"] = 2.0

    limiter = TokenBucketRateLimiter(store)

    # Manually initialize with no tokens and a recent refill time
    limiter.last_refill = asyncio.get_event_loop().time()
    limiter.tokens = 0.0

    # With 0 tokens and refill rate of 1/sec, it needs 1 second to get a token
    # But we only give it 0.1 seconds timeout, so it should fail
    assert await limiter.acquire(timeout=0.1) is False


@pytest.mark.asyncio
async def test_rate_limiter_concurrent_acquires(rate_limiter):
    """Tests that concurrent requests for tokens are handled correctly."""
    limiter = rate_limiter
    tasks = [limiter.acquire(timeout=3.0) for _ in range(7)]
    results = await asyncio.gather(*tasks)

    assert all(results)


# --- Reconstructed and New Tests ---


@pytest.mark.asyncio
async def test_negative_cache_ttl(config_store_with_etcd):
    """Tests that a negative cache TTL results in no caching."""
    config_store_with_etcd.cache_ttl = -1
    mock_value = (b"test_value", None)

    with patch.object(config_store_with_etcd.client, "get", return_value=mock_value):
        await config_store_with_etcd.get_config("key")
        # With negative TTL, the cache should not be valid
        assert not config_store_with_etcd._is_cache_valid("key")


@pytest.mark.asyncio
async def test_fallback_corrupted(
    caplog, mocker
):  # FIX: Removed fixture, set up manually
    """
    FIX: Tests that a corrupted fallback file is detected and ignored.
    This test is now isolated and uses a dedicated mock setup.
    """
    # 1. Set up a store instance for this test
    # Mock os.path.exists to return true for these paths
    with patch("os.path.exists", return_value=True):
        store = ConfigStore(fallback_path="/mock/fallback.json")

    checksum_file = "/mock/fallback.json.sha256"
    fallback_file_path = "/mock/fallback.json"

    # 2. Mock for the *corrupted* checksum file (text read)
    mock_corrupt_checksum = mocker.AsyncMock()
    mock_corrupt_checksum.read = mocker.AsyncMock(return_value="invalid_hash")
    mock_corrupt_checksum.__aenter__.return_value = mock_corrupt_checksum
    mock_corrupt_checksum.__aexit__ = mocker.AsyncMock()

    # 3. Mock for the *content* file (binary read for hash check)
    mock_content_file = mocker.AsyncMock()
    content_bytes = b'{"fallback_key": "fallback_value"}'
    mock_content_file.read = mocker.AsyncMock(return_value=content_bytes)
    mock_content_file.__aenter__.return_value = mock_content_file
    mock_content_file.__aexit__ = mocker.AsyncMock()

    # 4. Side_effect function for aiofiles.open
    # FIX: This MUST be a 'def', not 'async def'
    def open_side_effect(path, mode="r", **kwargs):
        path_str = str(path)
        if path_str == checksum_file:
            return mock_corrupt_checksum
        if path_str == fallback_file_path and mode == "rb":
            return mock_content_file
        # Fallback for any other call
        return mocker.AsyncMock()

    # 5. We need to mock os.path.exists as well for this isolated test
    with patch("os.path.exists", return_value=True):
        # Patch where the code *under test* uses it
        patch_target = "arbiter.arbiter_growth.config_store.aiofiles.open"
        with patch(patch_target, side_effect=open_side_effect):
            with caplog.at_level(logging.ERROR):
                await store._load_from_fallback()

                assert "Fallback file integrity check failed" in caplog.text
                assert "fallback_key" not in store._cache


@pytest.mark.asyncio
async def test_fallback_corrupted(tmp_path, mocker, caplog):
    """Tests that a corrupted fallback file is detected and ignored."""
    # Set up file paths
    fallback_file = tmp_path / "fallback.json"
    checksum_file = tmp_path / "fallback.json.sha256"
    fallback_data = {"fallback_key": "fallback_value"}
    
    content_str = json.dumps(fallback_data)
    content_bytes = content_str.encode("utf-8")
    fallback_file.write_bytes(content_bytes)
    
    # Write an INVALID checksum to simulate corruption
    checksum_file.write_text("invalid_hash_value")
    
    # Create mock for text file reading (fallback data)
    mock_text_file = mocker.AsyncMock()
    mock_text_file.read = mocker.AsyncMock(return_value=content_str)
    mock_text_file.__aenter__.return_value = mock_text_file
    mock_text_file.__aexit__ = mocker.AsyncMock()
    
    # Create mock for binary file reading (for hash calculation)
    mock_bin_file = mocker.AsyncMock()
    mock_bin_file.read = mocker.AsyncMock(return_value=content_bytes)
    mock_bin_file.__aenter__.return_value = mock_bin_file
    mock_bin_file.__aexit__ = mocker.AsyncMock()
    
    # Create mock for checksum file reading - returns INVALID hash
    mock_checksum_file = mocker.AsyncMock()
    mock_checksum_file.read = mocker.AsyncMock(return_value="invalid_hash_value")
    mock_checksum_file.__aenter__.return_value = mock_checksum_file
    mock_checksum_file.__aexit__ = mocker.AsyncMock()
    
    def open_side_effect(path, mode="r", **kwargs):
        path_str = str(path)
        if path_str == str(fallback_file):
            return mock_text_file if mode == "r" else mock_bin_file
        if path_str == str(checksum_file):
            return mock_checksum_file
        return mocker.AsyncMock()
    
    patch_target = "arbiter.arbiter_growth.config_store.aiofiles.open"
    with patch(patch_target, side_effect=open_side_effect):
        with patch("etcd3.client", side_effect=Exception("etcd fail")):
            with patch("os.path.exists", return_value=True):
                store = ConfigStore(fallback_path=str(fallback_file))
                
                with caplog.at_level(logging.ERROR):
                    await store._load_from_fallback()
                    assert "Fallback file integrity check failed" in caplog.text
                    # The cache should remain empty after a failed integrity check
                    assert "fallback_key" not in store._cache


@pytest.mark.asyncio
async def test_concurrent_config_fetches(config_store_with_etcd, mock_etcd_client):
    """Tests that multiple concurrent fetches for the same key only trigger one etcd call."""
    mock_etcd_client.get.return_value = (b"etcd_value", None)

    tasks = [config_store_with_etcd.get_config("key") for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Due to the cache lock, only one etcd call should be made
    assert mock_etcd_client.get.call_count == 1  # Should be exactly 1 due to lock
    assert mock_etcd_client.get.call_count <= 2  # Allow for some race conditions
    assert all(result == "etcd_value" for result in results)
