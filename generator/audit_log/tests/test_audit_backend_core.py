
"""
test_audit_backend_core.py

Regulated industry-grade test suite for audit_backend_core.py.

Features:
- Tests LogBackend core functionality (batch writes, queries, schema migration, health checks).
- Validates PII/secret redaction, audit logging, and tamper detection.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe batch operations, retry logic, and thread-safety.
- Verifies error handling, invalid configurations, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (audit_utils, audit_log, KMS).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- cryptography, zstandard, dynaconf, prometheus-client, opentelemetry-sdk
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
from cryptography.fernet import Fernet
from prometheus_client import REGISTRY
import zstandard as zstd
import zlib

from audit_backend_core import (
    LogBackend, AuditBackendError, MigrationError, TamperDetectionError,
    BACKEND_ERRORS, BACKEND_WRITES, BACKEND_HEALTH, ENCRYPTER, COMPRESSION_ALGO, COMPRESSION_LEVEL
)
from audit_log import log_action
from audit_utils import compute_hash, send_alert

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_audit_backend_core"
MOCK_CORRELATION_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_ENCRYPTION_KEYS'] = json.dumps([base64.b64encode(Fernet.generate_key()).decode('utf-8')])
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
async def mock_boto3():
    """Mock boto3 client for KMS."""
    with patch('boto3.client') as mock_client:
        mock_kms = MagicMock()
        mock_client.return_value = mock_kms
        yield mock_kms

@pytest_asyncio.fixture
async def test_backend():
    """Create a concrete LogBackend implementation for testing."""
    class TestBackend(LogBackend):
        async def _append_single(self, prepared_entry: Dict[str, Any]) -> None:
            pass

        async def _query_single(self, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
            return [{"entry_id": "123", "encrypted_data": "mock_data", "schema_version": 1, "_audit_hash": "mock_hash"}]

        async def _migrate_schema(self) -> None:
            pass

        async def _health_check(self) -> bool:
            return True

        async def _get_current_schema_version(self) -> int:
            return 1

    backend = TestBackend({"test_param": "value"})
    yield backend
    await backend.close()

class TestAuditBackendCore:
    """Test suite for audit_backend_core.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_backend_append(self, test_backend, mock_presidio, mock_audit_log, mock_opentelemetry):
        """Test LogBackend append operation with encryption and compression."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "timestamp": "2025-09-01T12:00:00Z"
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            await test_backend.append(entry)
        mock_presidio[0].analyze.assert_called_once()
        mock_audit_log.assert_called_with("backend_append", backend="TestBackend", success=True)
        mock_opentelemetry[1].set_attribute.assert_any_call("backend", "TestBackend")
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'TestBackend'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_backend_query(self, test_backend, mock_audit_log):
        """Test LogBackend query operation."""
        results = await test_backend.query({"entry_id": "123"}, limit=1)
        assert len(results) == 1
        assert results[0]["entry_id"] == "123"
        mock_audit_log.assert_called_with("backend_query", backend="TestBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_tamper_detection(self, test_backend, mock_audit_log, mock_send_alert):
        """Test tamper detection during entry verification."""
        entry = {
            "entry_id": "123",
            "encrypted_data": "mock_data",
            "schema_version": 1,
            "_audit_hash": "invalid_hash"
        }
        with patch('audit_utils.compute_hash', return_value="correct_hash"):
            with pytest.raises(TamperDetectionError, match="Tamper detected"):
                await test_backend._verify_entry(entry)
        mock_audit_log.assert_called_with("tamper_detected", backend="TestBackend", issue=Any)
        mock_send_alert.assert_called_with(Any, severity="critical")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_health_check(self, test_backend, mock_audit_log):
        """Test LogBackend health check."""
        health_status = await test_backend._health_check()
        assert health_status is True
        mock_audit_log.assert_called_with("backend_health_check", backend="TestBackend", status="healthy")
        assert REGISTRY.get_sample_value('audit_backend_health', {'backend': 'TestBackend'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_schema_migration(self, test_backend, mock_audit_log):
        """Test LogBackend schema migration."""
        await test_backend._migrate_schema()
        mock_audit_log.assert_called_with("schema_migration", backend="TestBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_config(self, mock_audit_log):
        """Test LogBackend with invalid configuration."""
        with pytest.raises(ValueError, match="Invalid configuration"):
            LogBackend({})  # Missing required params
        mock_audit_log.assert_called_with("backend_init_error", backend="LogBackend", error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_appends(self, test_backend, mock_presidio, mock_audit_log):
        """Test concurrent append operations."""
        async def append_entry(i):
            entry = {
                "action": f"user_action_{i}",
                "details_json": json.dumps({"email": f"test{i}@example.com"}),
                "trace_id": MOCK_CORRELATION_ID,
                "actor": "user-123",
                "timestamp": "2025-09-01T12:00:00Z"
            }
            await test_backend.append(entry)

        tasks = [append_entry(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert mock_audit_log.call_count >= 5
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'TestBackend'}) == 5

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_retry_operation_failure(self, test_backend, mock_audit_log, mock_send_alert):
        """Test retry logic for failed operations."""
        async def failing_op():
            raise ValueError("Operation failed")

        with pytest.raises(ValueError):
            await test_backend.retry_operation(failing_op, backend_name="TestBackend", op_name="test_op")
        assert test_backend.retry_operation.call_count == 3  # Matches AUDIT_RETRY_MAX_ATTEMPTS
        mock_audit_log.assert_called_with("backend_error", backend="TestBackend", error=Any)
        mock_send_alert.assert_called_with(Any, severity="critical")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_compression_zlib(self, test_backend, mock_presidio, mock_audit_log):
        """Test zlib compression."""
        with patch('audit_backend_core.COMPRESSION_ALGO', 'zlib'):
            entry = {
                "action": "user_login",
                "details_json": json.dumps({"email": "test@example.com"}),
                "trace_id": MOCK_CORRELATION_ID,
                "actor": "user-123"
            }
            await test_backend.append(entry)
        mock_audit_log.assert_called_with("backend_append", backend="TestBackend", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_compression_zstd(self, test_backend, mock_presidio, mock_audit_log):
        """Test zstd compression."""
        with patch('audit_backend_core.COMPRESSION_ALGO', 'zstd'):
            entry = {
                "action": "user_login",
                "details_json": json.dumps({"email": "test@example.com"}),
                "trace_id": MOCK_CORRELATION_ID,
                "actor": "user-123"
            }
            await test_backend.append(entry)
        mock_audit_log.assert_called_with("backend_append", backend="TestBackend", success=True)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_backend_core",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])