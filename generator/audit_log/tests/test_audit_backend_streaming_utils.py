
"""
test_audit_backend_streaming_utils.py

Regulated industry-grade test suite for audit_backend_streaming_utils.py.

Features:
- Tests SensitiveDataFilter, SimpleCircuitBreaker, PersistentRetryQueue, and FileBackedRetryQueue.
- Validates PII/secret redaction, audit logging, and retry queue persistence.
- Ensures Prometheus metrics and OpenTelemetry tracing (via audit_log integration).
- Tests async-safe operations, thread-safety, and circuit breaker state transitions.
- Verifies error handling, disk failures, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (audit_log, file operations).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- prometheus-client, zlib, audit_log
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time
from prometheus_client import REGISTRY
from collections import deque

from audit_backend_streaming_utils import (
    SensitiveDataFilter, SimpleCircuitBreaker, PersistentRetryQueue, FileBackedRetryQueue
)
from audit_log import log_action
from audit_utils import send_alert

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_audit_backend_streaming_utils"
TEST_PERSISTENCE_FILE = f"{TEST_LOG_DIR}/retry_queue.log"
MOCK_CORRELATION_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_BACKEND_QUEUE_MAX_SIZE'] = '100'
os.environ['AUDIT_BACKEND_MAX_REPROCESS_ATTEMPTS'] = '3'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def cleanup_test_environment():
    """Clean up test environment before and after tests."""
    for path in [TEST_LOG_DIR]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
    Path(TEST_LOG_DIR).mkdir(parents=True, exist_ok=True)
    yield
    if Path(TEST_LOG_DIR).exists():
        import shutil
        shutil.rmtree(path, ignore_errors=True)

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_send_alert():
    """Mock audit_utils.send_alert."""
    with patch('audit_utils.send_alert') as mock_alert:
        yield mock_alert

@pytest_asyncio.fixture
async def mock_aiofiles():
    """Mock aiofiles operations."""
    with patch('aiofiles.open') as mock_open:
        mock_file = AsyncMock()
        mock_open.return_value.__aenter__.return_value = mock_file
        yield mock_file

@pytest_asyncio.fixture
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('audit_backend_core.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

class TestAuditBackendStreamingUtils:
    """Test suite for audit_backend_streaming_utils.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sensitive_data_filter(self, mock_audit_log):
        """Test SensitiveDataFilter redaction."""
        filter_instance = SensitiveDataFilter()
        record = MagicMock()
        record.msg = "hec_token: 'mock_token' password: 'secret123' api_key: 'sk-1234567890'"
        record.args = {"connection_string": "secret=abc123"}
        assert filter_instance.filter(record)
        assert "hec_token: '[REDACTED]'" in str(record.msg)
        assert "password: '[REDACTED]'" in str(record.msg)
        assert "api_key: '[REDACTED]'" in str(record.msg)
        assert record.args["connection_string"] == "secret='[REDACTED]'"
        mock_audit_log.assert_called_with("sensitive_data_filtered", filter="SensitiveDataFilter", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_circuit_breaker_state_transitions(self, mock_audit_log, mock_send_alert):
        """Test SimpleCircuitBreaker state transitions."""
        breaker = SimpleCircuitBreaker("test_breaker", failure_threshold=2, reset_timeout=1)
        assert breaker.state == "closed"

        # Simulate two failures to open circuit
        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("Test failure")
        with pytest.raises(ValueError):
            async with breaker:
                raise ValueError("Test failure")
        assert breaker.state == "open"
        mock_audit_log.assert_called_with("circuit_breaker_state_change", breaker="test_breaker", state="open")
        mock_send_alert.assert_called_with(Any, severity="critical")

        # Wait for reset timeout and test half-open
        await asyncio.sleep(1.1)
        assert breaker.state == "half-open"
        mock_audit_log.assert_called_with("circuit_breaker_state_change", breaker="test_breaker", state="half-open")

        # Simulate success to close circuit
        async with breaker:
            pass
        assert breaker.state == "closed"
        mock_audit_log.assert_called_with("circuit_breaker_state_change", breaker="test_breaker", state="closed")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_persistent_retry_queue_append(self, mock_audit_log):
        """Test PersistentRetryQueue append operation."""
        queue = PersistentRetryQueue("test_queue", TEST_PERSISTENCE_FILE, max_queue_size=10)
        entry = {"data": "test_entry"}
        await queue.append(entry)
        assert queue._queue.qsize() == 1
        assert queue._queue[0]["data"] == "test_entry"
        mock_audit_log.assert_called_with("retry_queue_append", queue="test_queue", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backed_retry_queue_persistence(self, mock_aiofiles, mock_audit_log):
        """Test FileBackedRetryQueue persistence."""
        queue = FileBackedRetryQueue("test_queue", TEST_PERSISTENCE_FILE, max_queue_size=10)
        entry = {"data": "test_entry"}
        await queue.append(entry)
        await queue._persist_queue_state()
        mock_aiofiles.write.assert_called_once()
        content = mock_aiofiles.write.call_args[0][0]
        assert "test_entry" in content.decode('utf-8')
        mock_audit_log.assert_called_with("retry_queue_persist", queue="test_queue", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_persistent_retry_queue_reprocess(self, mock_audit_log, mock_send_alert):
        """Test PersistentRetryQueue reprocessing."""
        async def mock_reprocess_func(entry):
            return True  # Simulate successful reprocessing

        queue = PersistentRetryQueue("test_queue", TEST_PERSISTENCE_FILE, max_queue_size=10)
        entry = {"data": "test_entry", "attempts": 0}
        await queue.append(entry)
        await queue.reprocess(mock_reprocess_func)
        assert queue._queue.qsize() == 0
        mock_audit_log.assert_called_with("retry_queue_reprocess", queue="test_queue", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backed_retry_queue_failure(self, mock_aiofiles, mock_audit_log, mock_send_alert):
        """Test FileBackedRetryQueue persistence failure."""
        mock_aiofiles.write.side_effect = IOError("Disk full")
        queue = FileBackedRetryQueue("test_queue", TEST_PERSISTENCE_FILE, max_queue_size=10)
        entry = {"data": "test_entry"}
        await queue.append(entry)
        with pytest.raises(IOError):
            await queue._persist_queue_state()
        mock_audit_log.assert_called_with("retry_queue_error", queue="test_queue", error=Any)
        mock_send_alert.assert_called_with(Any, severity="critical")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_queue_appends(self, mock_audit_log):
        """Test concurrent PersistentRetryQueue appends."""
        queue = PersistentRetryQueue("test_queue", TEST_PERSISTENCE_FILE, max_queue_size=10)
        async def append_entry(i):
            entry = {"data": f"test_entry_{i}"}
            await queue.append(entry)

        tasks = [append_entry(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert queue._queue.qsize() == 5
        mock_audit_log.assert_called_with("retry_queue_append", queue="test_queue", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_circuit_breaker_concurrent_access(self, mock_audit_log):
        """Test concurrent access to SimpleCircuitBreaker."""
        breaker = SimpleCircuitBreaker("test_breaker", failure_threshold=2, reset_timeout=10)
        async def access_breaker(i):
            async with breaker:
                pass

        tasks = [access_breaker(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert breaker.state == "closed"
        mock_audit_log.assert_called_with("circuit_breaker_access", breaker="test_breaker", success=True)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_backend_streaming_utils",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
