# tests/test_io_utils.py
import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

# Import the module under test
from intent_capture.io_utils import (
    FileManager,
    ScalableProvenanceLogger,
    download_file_to_temp,
    get_redis_client,
    hash_file_distributed_cache,
    log_audit_event,
    prune_audit_logs,
    startup_validation,
)


# --- Test Fixtures ---
@pytest.fixture
def mock_env(monkeypatch):
    """Mock environment variables."""
    monkeypatch.setenv("PROD_MODE", "false")
    monkeypatch.setenv("PROVENANCE_SALT", "test_salt")
    monkeypatch.setenv("IO_WORKSPACE_DIR", "/tmp/test_io")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("ENABLE_AUDIT", "false")
    monkeypatch.setenv("USE_QUEUE", "false")
    monkeypatch.setenv("USE_SAFETY_CHECK", "false")
    monkeypatch.setenv("CHECK_BIAS", "false")
    yield


@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace


@pytest.fixture
def file_manager(temp_workspace):
    """Create FileManager instance with temp workspace."""
    return FileManager(str(temp_workspace))


@pytest.fixture
def mock_redis():
    """Mock redis client."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=None)
    mock_client.set = AsyncMock()
    mock_client.close = AsyncMock()

    # Create a proper async context manager that returns the mock_client
    async def async_from_url(*args, **kwargs):
        return mock_client

    with patch("intent_capture.io_utils.aredis.from_url", side_effect=async_from_url):
        with patch("intent_capture.io_utils.REDIS_AVAILABLE", True):
            yield mock_client


@pytest.fixture
def mock_aiohttp():
    """Mock aiohttp for downloads."""
    mock_response = AsyncMock()
    mock_response.headers = {"Content-Length": "1000"}
    mock_response.raise_for_status = MagicMock()

    # Create async iterator for content chunks
    async def async_iter():
        yield b"test_data"

    mock_response.content.iter_chunked = lambda size: async_iter()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()

    with patch("intent_capture.io_utils.aiohttp.ClientSession", return_value=mock_session):
        with patch("intent_capture.io_utils.AIOHTTP_AVAILABLE", True):
            yield mock_session


@pytest.fixture
def mock_circuit_breaker():
    """Mock circuit breaker."""
    mock_breaker = MagicMock()

    # Make call_async properly await the coroutine
    async def call_async_impl(f, *args, **kwargs):
        result = f(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    mock_breaker.call_async = call_async_impl

    with patch("intent_capture.io_utils.download_breaker", mock_breaker):
        with patch("intent_capture.io_utils.redis_breaker", mock_breaker):
            with patch("intent_capture.io_utils.AIOBREAKER_AVAILABLE", True):
                yield mock_breaker


@pytest.fixture
def mock_boto3():
    """Mock boto3 for S3."""
    mock_s3 = MagicMock()
    mock_s3.put_object = MagicMock()
    mock_s3.list_objects_v2 = MagicMock(return_value={"Contents": []})
    mock_s3.delete_objects = MagicMock()

    mock_boto3_module = MagicMock()
    mock_boto3_module.client.return_value = mock_s3

    with patch("intent_capture.io_utils.boto3", mock_boto3_module):
        yield mock_s3


@pytest.fixture
def mock_prometheus():
    """Mock Prometheus metrics."""
    mock_counter = MagicMock()
    mock_histogram = MagicMock()
    mock_gauge = MagicMock()

    # Create a mock context manager for time() and track_inprogress()
    mock_timer = MagicMock()
    mock_timer.__enter__ = MagicMock()
    mock_timer.__exit__ = MagicMock()
    mock_histogram.time = MagicMock(return_value=mock_timer)
    mock_histogram.labels = MagicMock(return_value=mock_histogram)

    mock_gauge.track_inprogress = MagicMock(return_value=mock_timer)

    with patch("intent_capture.io_utils.FILE_OPS_TOTAL", mock_counter), patch(
        "intent_capture.io_utils.FILE_OPS_LATENCY_SECONDS", mock_histogram
    ), patch("intent_capture.io_utils.DOWNLOAD_LATENCY_SECONDS", mock_histogram), patch(
        "intent_capture.io_utils.DOWNLOAD_BYTES_TOTAL", mock_counter
    ), patch(
        "intent_capture.io_utils.IN_PROGRESS_DOWNLOADS", mock_gauge
    ), patch(
        "intent_capture.io_utils.SAFETY_VIOLATIONS_TOTAL", mock_counter
    ):
        yield


# --- Tests for FileManager ---
def test_file_manager_init(temp_workspace):
    """Test FileManager initialization."""
    fm = FileManager(str(temp_workspace))
    assert fm.workspace == str(temp_workspace.resolve())


def test_file_manager_validate_path_valid(file_manager, temp_workspace):
    """Test valid path validation."""
    test_file = temp_workspace / "test.txt"
    test_file.write_text("content")
    validated = file_manager.validate_path("test.txt")
    assert validated == str(test_file.resolve())


def test_file_manager_validate_path_traversal(file_manager):
    """Test path traversal detection."""
    with pytest.raises(PermissionError, match="Path traversal attempt"):
        file_manager.validate_path("../outside.txt")


def test_file_manager_safe_open(file_manager, temp_workspace):
    """Test safe file opening."""
    test_file = temp_workspace / "test.txt"
    test_file.write_text("content")

    with file_manager.safe_open("test.txt", "r") as f:
        content = f.read()
    assert content == "content"


# --- Tests for ScalableProvenanceLogger ---
def test_provenance_logger_log_event(mock_env):
    """Test provenance event logging."""
    logger = ScalableProvenanceLogger()
    with patch("intent_capture.io_utils.utils_logger") as mock_logger:
        logger.log_event({"action": "test"})
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        assert "PROVENANCE:" in call_args
        assert "test" in call_args


def test_provenance_logger_hash_chain():
    """Test hash chaining in provenance logger."""
    logger = ScalableProvenanceLogger()
    initial_hash = logger._last_hash

    with patch("intent_capture.io_utils.utils_logger"):
        logger.log_event({"action": "test1"})
        hash1 = logger._last_hash

        logger.log_event({"action": "test2"})
        hash2 = logger._last_hash

    assert initial_hash != hash1
    assert hash1 != hash2


# --- Tests for Redis Client ---
@pytest.mark.asyncio
async def test_get_redis_client_available(mock_redis):
    """Test getting Redis client when available."""
    async with get_redis_client() as client:
        assert client is not None
        # Verify close is called on exit
    mock_redis.close.assert_called_once()


@pytest.mark.asyncio
async def test_get_redis_client_not_available(monkeypatch):
    """Test Redis client when not available."""
    monkeypatch.setenv("REDIS_URL", "")
    async with get_redis_client() as client:
        assert client is None


# --- Tests for Hash File ---
@pytest.mark.asyncio
async def test_hash_file_distributed_cache_success(
    file_manager, temp_workspace, mock_redis, mock_prometheus
):
    """Test successful file hashing with cache."""
    test_file = temp_workspace / "test.txt"
    test_file.write_text("test content")

    # Mock Redis to return no cached value
    mock_redis.get.return_value = None

    # Mock IOInput if it exists
    with patch("intent_capture.io_utils.IOInput") as mock_input:
        mock_input_instance = MagicMock()
        mock_input_instance.path = "test.txt"
        mock_input.return_value = mock_input_instance

        hash_result = await hash_file_distributed_cache("test.txt", file_manager)

    assert len(hash_result) == 64  # SHA256 hex length
    # Verify Redis cache was checked and set
    mock_redis.get.assert_called()
    mock_redis.set.assert_called()


@pytest.mark.asyncio
async def test_hash_file_distributed_cache_cached(
    file_manager, temp_workspace, mock_redis, mock_prometheus
):
    """Test file hashing with cached result."""
    test_file = temp_workspace / "test.txt"
    test_file.write_text("test content")

    # Mock Redis to return cached hash
    mock_redis.get.return_value = "cached_hash_value"

    # Mock IOInput if it exists
    with patch("intent_capture.io_utils.IOInput") as mock_input:
        mock_input_instance = MagicMock()
        mock_input_instance.path = "test.txt"
        mock_input.return_value = mock_input_instance

        hash_result = await hash_file_distributed_cache("test.txt", file_manager)

    assert hash_result == "cached_hash_value"
    # Verify cache was checked but not set
    mock_redis.get.assert_called()
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_hash_file_size_limit(file_manager, temp_workspace):
    """Test file size limit check."""
    test_file = temp_workspace / "large.txt"
    test_file.write_text("x")

    # Mock IOInput if it exists
    with patch("intent_capture.io_utils.IOInput") as mock_input:
        mock_input_instance = MagicMock()
        mock_input_instance.path = "large.txt"
        mock_input.return_value = mock_input_instance

        # Mock file size to exceed limit
        with patch("os.path.getsize", return_value=3 * 1024 * 1024 * 1024):  # 3GB
            with pytest.raises(ValueError, match="exceeds maximum"):
                await hash_file_distributed_cache("large.txt", file_manager)


# --- Tests for Download File ---
@pytest.mark.asyncio
async def test_download_file_to_temp_success(
    file_manager, mock_circuit_breaker, mock_prometheus, monkeypatch
):
    """Test successful file download."""
    monkeypatch.setattr("intent_capture.io_utils.last_download_time", 0)

    # Mock the actual response object properly
    mock_response = AsyncMock()
    mock_response.headers = {"Content-Length": "1000"}
    mock_response.raise_for_status = AsyncMock()

    # Create async iterator for content chunks
    async def async_iter():
        yield b"test_data"

    mock_response.content.iter_chunked = lambda size: async_iter()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock()

    # Mock the session.get to return the response
    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()

    with patch("intent_capture.io_utils.AIOHTTP_AVAILABLE", True):
        with patch("intent_capture.io_utils.aiohttp.ClientSession", return_value=mock_session):
            with patch("tempfile.mkstemp", return_value=(999, "/tmp/test_download")):
                with patch("os.fdopen", mock_open()):
                    with patch("os.path.getsize", return_value=1000):
                        with patch("intent_capture.io_utils.log_audit_event"):  # Mock audit logging
                            result = await download_file_to_temp(
                                "https://example.com/file.txt", file_manager
                            )

    assert result == "/tmp/test_download"


@pytest.mark.asyncio
async def test_download_file_rate_limited(file_manager, monkeypatch):
    """Test download rate limiting."""
    # Set last download time to now
    monkeypatch.setattr("intent_capture.io_utils.last_download_time", time.time())
    monkeypatch.setenv("DOWNLOAD_RATE_SEC", "10")

    result = await download_file_to_temp("https://example.com/file.txt", file_manager)
    assert result is None


@pytest.mark.asyncio
async def test_download_file_content_too_large(file_manager, mock_circuit_breaker, monkeypatch):
    """Test download with content too large."""
    monkeypatch.setattr("intent_capture.io_utils.last_download_time", 0)

    # Create a mock response with large Content-Length
    mock_response = AsyncMock()
    mock_response.headers = {"Content-Length": str(200 * 1024 * 1024)}  # 200MB
    mock_response.raise_for_status = AsyncMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock()

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_response)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock()

    with patch("intent_capture.io_utils.AIOHTTP_AVAILABLE", True):
        with patch("intent_capture.io_utils.aiohttp.ClientSession", return_value=mock_session):
            with patch("intent_capture.io_utils.log_audit_event"):  # Mock audit logging
                result = await download_file_to_temp("https://example.com/file.txt", file_manager)

    assert result is None


# --- Tests for Audit Logging ---
def test_log_audit_event_enabled(mock_boto3, monkeypatch):
    """Test audit logging when enabled."""
    monkeypatch.setenv("ENABLE_AUDIT", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test_key")
    monkeypatch.setenv("AWS_SECRET_KEY", "test_secret")

    with patch("os.getlogin", return_value="testuser"):
        log_audit_event("test_event", {"data": "test"})

    mock_boto3.put_object.assert_called_once()


def test_log_audit_event_disabled(mock_boto3, monkeypatch):
    """Test audit logging when disabled."""
    monkeypatch.setenv("ENABLE_AUDIT", "false")

    log_audit_event("test_event", {"data": "test"})
    mock_boto3.put_object.assert_not_called()


# --- Tests for Audit Pruning ---
def test_prune_audit_logs(mock_boto3, monkeypatch):
    """Test pruning audit logs."""
    monkeypatch.setenv("CONSENT_PRUNE", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test_key")
    monkeypatch.setenv("AWS_SECRET_KEY", "test_secret")

    # Create a real datetime object for old_date

    old_date = datetime.now() - timedelta(days=100)

    # Mock S3 response with old objects
    mock_boto3.list_objects_v2.return_value = {
        "Contents": [{"Key": "old_log.json", "LastModified": old_date}]
    }

    # Need to properly mock datetime module
    # The code uses datetime.now() and datetime.timedelta
    with patch("intent_capture.io_utils.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.now()
        mock_dt.timedelta = timedelta

        prune_audit_logs(retention_days=30)

    mock_boto3.list_objects_v2.assert_called_once()
    mock_boto3.delete_objects.assert_called_once()


# --- Tests for Startup Validation ---
def test_startup_validation_success(mock_env):
    """Test successful startup validation."""
    startup_validation()  # Should not raise


def test_startup_validation_missing_provenance_salt(monkeypatch):
    """Test startup validation with missing PROVENANCE_SALT in prod."""
    monkeypatch.setenv("PROD_MODE", "true")
    monkeypatch.delenv("PROVENANCE_SALT", raising=False)

    # The actual code checks if PROVENANCE_SALT is falsy when PROD_MODE is true
    # This happens during module import, not in startup_validation
    # So we need to test differently
    with patch("intent_capture.io_utils.PROD_MODE", True):
        with patch("intent_capture.io_utils.PROVENANCE_SALT", ""):
            with pytest.raises(RuntimeError, match="PROVENANCE_SALT"):
                startup_validation()


def test_startup_validation_missing_redis_url(monkeypatch):
    """Test startup validation with missing REDIS_URL."""
    with patch("intent_capture.io_utils.REDIS_AVAILABLE", True):
        monkeypatch.delenv("REDIS_URL", raising=False)

        with pytest.raises(RuntimeError, match="REDIS_URL"):
            startup_validation()
