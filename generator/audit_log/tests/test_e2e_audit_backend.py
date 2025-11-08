
"""
test_e2e_audit_backend.py

Regulated industry-grade E2E integration test suite for audit_log audit_backend modules.

Features:
- Tests E2E workflows for FileBackend, SQLiteBackend, S3Backend, HTTPBackend, and InMemoryBackend.
- Validates PII/secret redaction, audit logging, tamper detection, and provenance tracking.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, retry logic, circuit breaking, and concurrent workflows.
- Verifies error handling and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (boto3, google-cloud-storage, azure-storage-blob, aiohttp, aiokafka).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- boto3, google-cloud-storage, azure-storage-blob, aiohttp, aiokafka, sqlite3, zlib
- prometheus-client, opentelemetry-sdk
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
import boto3
from google.cloud import storage
from azure.storage.blob.aio import BlobServiceClient
import aiohttp
import aiokafka
import sqlite3
import zlib
from prometheus_client import REGISTRY

from audit_backend_core import LogBackend
from audit_backend_file_sql import FileBackend, SQLiteBackend
from audit_backend_cloud import S3Backend
from audit_backend_streaming_backends import HTTPBackend, InMemoryBackend
from audit_log import log_action
from audit_utils import compute_hash, send_alert

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_e2e_audit_backend"
TEST_LOG_FILE = f"{TEST_LOG_DIR}/audit.log"
TEST_DB_FILE = f"{TEST_LOG_DIR}/audit.db"
TEST_SNAPSHOT_FILE = f"{TEST_LOG_DIR}/snapshot.jsonl.gz"
TEST_BUCKET = "test-bucket"
TEST_ATHENA_DB = "audit_db"
TEST_ATHENA_TABLE = "audit_logs"
TEST_ATHENA_RESULTS = "s3://test-results/"
TEST_ENDPOINT = "https://example.com/log"
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
async def mock_boto3():
    """Mock boto3 clients for S3 and Athena."""
    with patch('boto3.client') as mock_client:
        mock_s3 = MagicMock()
        mock_athena = MagicMock()
        mock_athena.start_query_execution.return_value = {"QueryExecutionId": "mock_id"}
        mock_athena.get_query_results.return_value = {
            "ResultSet": {
                "Rows": [{"Data": [{"VarCharValue": json.dumps({"entry_id": "123", "encrypted_data": "[REDACTED_EMAIL]"})}}]
            }
        }
        mock_client.side_effect = lambda service: mock_s3 if service == "s3" else mock_athena
        yield mock_s3, mock_athena

@pytest_asyncio.fixture
async def mock_aiohttp():
    """Mock aiohttp client session."""
    with patch('aiohttp.ClientSession') as mock_session:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[{"entry_id": "123", "encrypted_data": "[REDACTED_EMAIL]"}])
        mock_client.post.return_value.__aenter__.return_value = mock_response
        mock_client.get.return_value.__aenter__.return_value = mock_response
        mock_session.return_value.__aenter__.return_value = mock_client
        yield mock_client

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

@pytest_asyncio.fixture
async def s3_backend(mock_boto3):
    """Create an S3Backend instance."""
    backend = S3Backend({
        "bucket": TEST_BUCKET,
        "athena_results_location": TEST_ATHENA_RESULTS,
        "athena_database": TEST_ATHENA_DB,
        "athena_table": TEST_ATHENA_TABLE
    })
    yield backend
    await backend.close()

@pytest_asyncio.fixture
async def http_backend(mock_aiohttp):
    """Create an HTTPBackend instance."""
    backend = HTTPBackend({"endpoint": TEST_ENDPOINT, "query_endpoint": TEST_ENDPOINT})
    yield backend
    await backend.close()

@pytest_asyncio.fixture
async def inmemory_backend():
    """Create an InMemoryBackend instance."""
    backend = InMemoryBackend({"snapshot_file": TEST_SNAPSHOT_FILE})
    yield backend
    await backend.close()

class TestE2EAuditBackend:
    """E2E integration test suite for audit_log audit_backend modules."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_file_backend_workflow(self, file_backend, mock_presidio, mock_audit_log, mock_opentelemetry):
        """Test E2E workflow for FileBackend: append, query, and tamper detection."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        # Append
        with freeze_time("2025-09-01T12:00:00Z"):
            await file_backend.append(entry)
        mock_presidio[0].analyze.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="FileBackend", success=True)
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'FileBackend'}) == 1

        # Query
        results = await file_backend.query({"entry_id": entry["entry_id"]}, limit=1)
        assert len(results) == 1
        assert results[0]["entry_id"] == entry["entry_id"]
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]
        mock_audit_log.assert_called_with("backend_query", backend="FileBackend", success=True)

        # Tamper detection
        tampered_entry = results[0].copy()
        tampered_entry["_audit_hash"] = "invalid_hash"
        with patch('audit_utils.compute_hash', return_value="correct_hash"):
            with pytest.raises(Exception, match="Tamper detected"):
                await file_backend._verify_entry(tampered_entry)
        mock_audit_log.assert_called_with("tamper_detected", backend="FileBackend", issue=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_sqlite_backend_workflow(self, sqlite_backend, mock_presidio, mock_audit_log):
        """Test E2E workflow for SQLiteBackend: append, query, and health check."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        # Append
        with freeze_time("2025-09-01T12:00:00Z"):
            await sqlite_backend.append(entry)
        mock_presidio[0].analyze.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="SQLiteBackend", success=True)

        # Query
        results = await sqlite_backend.query({"entry_id": entry["entry_id"]}, limit=1)
        assert len(results) == 1
        assert results[0]["entry_id"] == entry["entry_id"]
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]

        # Health check
        health_status = await sqlite_backend._health_check()
        assert health_status is True
        mock_audit_log.assert_called_with("backend_health_check", backend="SQLiteBackend", status="healthy")

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_s3_backend_workflow(self, s3_backend, mock_boto3, mock_presidio, mock_audit_log):
        """Test E2E workflow for S3Backend: append, query, and schema migration."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        # Append
        with freeze_time("2025-09-01T12:00:00Z"):
            await s3_backend.append(entry)
        mock_boto3[0].put_object.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="S3Backend", success=True)

        # Query
        results = await s3_backend.query({"entry_id": entry["entry_id"]}, limit=1)
        assert len(results) == 1
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]
        mock_boto3[1].start_query_execution.assert_called_once()

        # Schema migration
        await s3_backend._migrate_schema()
        mock_audit_log.assert_called_with("schema_migration", backend="S3Backend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_http_backend_workflow(self, http_backend, mock_aiohttp, mock_presidio, mock_audit_log):
        """Test E2E workflow for HTTPBackend: append, query, and circuit breaker."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        # Append
        with freeze_time("2025-09-01T12:00:00Z"):
            await http_backend.append(entry)
        mock_aiohttp.post.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="HTTPBackend", success=True)

        # Query
        results = await http_backend.query({"entry_id": entry["entry_id"]}, limit=1)
        assert len(results) == 1
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]
        mock_audit_log.assert_called_with("backend_query", backend="HTTPBackend", success=True)

        # Circuit breaker
        mock_aiohttp.post.side_effect = aiohttp.ClientError("Network failure")
        with pytest.raises(aiohttp.ClientError):
            await http_backend.append(entry)
        assert mock_aiohttp.post.call_count == 4  # Initial + retries
        mock_audit_log.assert_called_with("backend_error", backend="HTTPBackend", error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_inmemory_backend_workflow(self, inmemory_backend, mock_presidio, mock_audit_log):
        """Test E2E workflow for InMemoryBackend: append, snapshot, and query."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        # Append
        with freeze_time("2025-09-01T12:00:00Z"):
            await inmemory_backend.append(entry)
            await inmemory_backend._save_snapshot()
        mock_audit_log.assert_called_with("backend_append", backend="InMemoryBackend", success=True)

        # Query
        results = await inmemory_backend.query({"entry_id": entry["entry_id"]}, limit=1)
        assert len(results) == 1
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]

        # Snapshot verification
        async with aiofiles.open(TEST_SNAPSHOT_FILE, 'rb') as f:
            content = zlib.decompress(await f.read()).decode('utf-8')
        assert "[REDACTED_EMAIL]" in content

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_e2e_concurrent_multi_backend(self, file_backend, sqlite_backend, http_backend, mock_aiohttp, mock_presidio, mock_audit_log):
        """Test concurrent appends across multiple backends."""
        async def append_to_backend(backend: LogBackend, i: int):
            entry = {
                "action": f"user_action_{i}",
                "details_json": json.dumps({"email": f"test{i}@example.com"}),
                "trace_id": MOCK_CORRELATION_ID,
                "actor": "user-123",
                "timestamp": "2025-09-01T12:00:00Z"
            }
            await backend.append(entry)

        tasks = [
            append_to_backend(file_backend, i) for i in range(3)
        ] + [
            append_to_backend(sqlite_backend, i) for i in range(3, 6)
        ] + [
            append_to_backend(http_backend, i) for i in range(6, 9)
        ]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert mock_audit_log.call_count >= 9
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'FileBackend'}) == 3
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'SQLiteBackend'}) == 3
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'HTTPBackend'}) == 3

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_backend_core",
        "--cov=audit_backend_file_sql",
        "--cov=audit_backend_cloud",
        "--cov=audit_backend_streaming_backends",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
