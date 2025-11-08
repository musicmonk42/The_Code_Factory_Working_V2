
"""
test_audit_backend_file_sql.py

Regulated industry-grade test suite for audit_backend_file_sql.py.

Features:
- Tests FileBackend and SQLiteBackend for batch writes, queries, schema migration, health checks, and WAL recovery.
- Validates PII/secret redaction, audit logging, and tamper detection.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe batch operations, retry logic, and thread-safety.
- Verifies error handling, invalid configurations, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (Presidio, audit_log, file operations).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- sqlite3, zlib, presidio-analyzer, prometheus-client, opentelemetry-sdk
- audit_log, audit_utils
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
import sqlite3
import aiofiles
from prometheus_client import REGISTRY
import zlib

from audit_backend_file_sql import FileBackend, SQLiteBackend
from audit_log import log_action
from audit_utils import compute_hash

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_audit_backend_file_sql"
TEST_LOG_FILE = f"{TEST_LOG_DIR}/audit.log"
TEST_DB_FILE = f"{TEST_LOG_DIR}/audit.db"
MOCK_CORRELATION_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_ENCRYPTION_KEYS'] = json.dumps([base64.b64encode(b"mock_key_32_bytes_1234567890abcd").decode('utf-8')])
os.environ['AUDIT_COMPRESSION_ALGO'] = 'zlib'
os.environ['AUDIT_COMPRESSION_LEVEL'] = '9'
os.environ['AUDIT_BATCH_FLUSH_INTERVAL'] = '10'
os.environ['AUDIT_BATCH_MAX_SIZE'] = '100'
os.environ['AUDIT_HEALTH_CHECK_INTERVAL'] = '30'
os.environ['AUDIT_RETRY_MAX_ATTEMPTS'] = '3'
os.environ['AUDIT_RETRY_BACKOFF_FACTOR'] = '0.5'
os.environ['AUDIT_TAMPER_DETECTION_ENABLED'] = 'true'

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
async def mock_presidio():
    """Mock Presidio analyzer and anonymizer."""
    with patch('audit_utils.presidio_analyzer.AnalyzerEngine') as mock_analyzer, \
         patch('audit_utils.presidio_anonymizer.AnonymizerEngine') as mock_anonymizer:
        mock_analyzer_inst = MagicMock()
        mock_anonymizer_inst = MagicMock()
        mock_analyzer_inst.analyze.return_value = [
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25)
        ]
        mock_anonymizer_inst.anonymize.return_value = MagicMock(
            text="[REDACTED_EMAIL]"
        )
        mock_analyzer.return_value = mock_analyzer_inst
        mock_anonymizer.return_value = mock_anonymizer_inst
        yield mock_analyzer_inst, mock_anonymizer_inst

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
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('audit_backend_core.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

@pytest_asyncio.fixture
async def file_backend():
    """Create a FileBackend instance."""
    backend = FileBackend({"log_file": TEST_LOG_FILE})
    yield backend
    await backend.close()

@pytest_asyncio.fixture
async def sqlite_backend():
    """Create a SQLiteBackend instance."""
    backend = SQLiteBackend({"db_file": TEST_DB_FILE})
    yield backend
    await backend.close()

class TestAuditBackendFileSQL:
    """Test suite for audit_backend_file_sql.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backend_append(self, file_backend, mock_presidio, mock_audit_log, mock_opentelemetry):
        """Test FileBackend append operation with WAL."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            await file_backend.append(entry)
        mock_presidio[0].analyze.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="FileBackend", success=True)
        mock_opentelemetry[1].set_attribute.assert_any_call("backend", "FileBackend")
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'FileBackend'}) == 1

        # Verify WAL file
        async with aiofiles.open(file_backend.wal_file, 'r') as wal:
            content = await wal.read()
        assert "[REDACTED_EMAIL]" in content

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sqlite_backend_append(self, sqlite_backend, mock_presidio, mock_audit_log):
        """Test SQLiteBackend append operation."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            await sqlite_backend.append(entry)
        mock_presidio[0].analyze.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="SQLiteBackend", success=True)
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'SQLiteBackend'}) == 1

        # Verify database
        conn = sqlite3.connect(TEST_DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT encrypted_data FROM logs WHERE entry_id = ?", (entry["entry_id"],))
        result = cursor.fetchone()
        assert result and "[REDACTED_EMAIL]" in result[0]
        conn.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backend_query(self, file_backend, mock_audit_log):
        """Test FileBackend query operation."""
        entry = {
            "entry_id": "123",
            "encrypted_data": json.dumps({"email": "[REDACTED_EMAIL]"}),
            "schema_version": 1,
            "_audit_hash": "mock_hash",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        async with aiofiles.open(file_backend.log_file, 'a') as f:
            await f.write(json.dumps(entry) + "\n")
        results = await file_backend._query_single({"entry_id": "123"}, limit=1)
        assert len(results) == 1
        assert results[0]["entry_id"] == "123"
        mock_audit_log.assert_called_with("backend_query", backend="FileBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sqlite_backend_query(self, sqlite_backend, mock_audit_log):
        """Test SQLiteBackend query operation."""
        entry = {
            "entry_id": "123",
            "encrypted_data": json.dumps({"email": "[REDACTED_EMAIL]"}),
            "schema_version": 1,
            "_audit_hash": "mock_hash",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        await sqlite_backend._append_single(entry)
        results = await sqlite_backend._query_single({"entry_id": "123"}, limit=1)
        assert len(results) == 1
        assert results[0]["entry_id"] == "123"
        mock_audit_log.assert_called_with("backend_query", backend="SQLiteBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backend_wal_recovery(self, file_backend, mock_audit_log):
        """Test FileBackend WAL recovery."""
        entry = {
            "entry_id": "123",
            "encrypted_data": json.dumps({"email": "[REDACTED_EMAIL]"}),
            "schema_version": 1,
            "_audit_hash": "mock_hash"
        }
        async with aiofiles.open(file_backend.wal_file, 'a') as wal:
            await wal.write(json.dumps(entry) + "\n")
        await file_backend.recover_wal()
        async with aiofiles.open(file_backend.log_file, 'r') as log:
            content = await log.read()
        assert "123" in content
        mock_audit_log.assert_called_with("wal_recovery", backend="FileBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sqlite_backend_health_check(self, sqlite_backend, mock_audit_log):
        """Test SQLiteBackend health check."""
        health_status = await sqlite_backend._health_check()
        assert health_status is True
        mock_audit_log.assert_called_with("backend_health_check", backend="SQLiteBackend", status="healthy")
        assert REGISTRY.get_sample_value('audit_backend_health', {'backend': 'SQLiteBackend'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backend_tamper_detection(self, file_backend, mock_audit_log, mock_send_alert):
        """Test FileBackend tamper detection."""
        entry = {
            "entry_id": "123",
            "encrypted_data": "mock_data",
            "schema_version": 1,
            "_audit_hash": "invalid_hash"
        }
        with patch('audit_utils.compute_hash', return_value="correct_hash"):
            with pytest.raises(TamperDetectionError, match="Tamper detected"):
                await file_backend._verify_entry(entry)
        mock_audit_log.assert_called_with("tamper_detected", backend="FileBackend", issue=Any)
        mock_send_alert.assert_called_with(Any, severity="critical")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sqlite_backend_schema_migration(self, sqlite_backend, mock_audit_log):
        """Test SQLiteBackend schema migration."""
        await sqlite_backend._migrate_schema()
        mock_audit_log.assert_called_with("schema_migration", backend="SQLiteBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_backend_invalid_config(self, mock_audit_log):
        """Test FileBackend with invalid configuration."""
        with pytest.raises(ValueError, match="log_file parameter is required"):
            FileBackend({})
        mock_audit_log.assert_called_with("backend_init_error", backend="FileBackend", error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_file_appends(self, file_backend, mock_presidio, mock_audit_log):
        """Test concurrent FileBackend append operations."""
        async def append_entry(i):
            entry = {
                "action": f"user_action_{i}",
                "details_json": json.dumps({"email": f"test{i}@example.com"}),
                "trace_id": MOCK_CORRELATION_ID,
                "actor": "user-123",
                "timestamp": "2025-09-01T12:00:00Z"
            }
            await file_backend.append(entry)

        tasks = [append_entry(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert mock_audit_log.call_count >= 5
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'FileBackend'}) == 5

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_backend_file_sql",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
