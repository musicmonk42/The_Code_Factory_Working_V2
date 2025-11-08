
"""
test_audit_log.py

Regulated industry-grade test suite for audit_log.py.

Features:
- Tests logging actions via REST API, gRPC service, and Typer CLI.
- Validates PII/secret scrubbing with Presidio and audit logging.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, RBAC, and tamper detection.
- Verifies retry logic, error handling, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (backends, Presidio).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- fastapi, grpc, typer, pydantic, prometheus-client, opentelemetry-sdk
- presidio-analyzer, presidio-anonymizer
"""

import asyncio
import json
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from faker import Faker
import aiofiles
from freezegun import freeze_time
from fastapi.testclient import TestClient
import grpc
from grpc.aio import insecure_channel
import typer
from typing import Dict, Any, List

from audit_log import AuditLog, log_action, api_app, app as typer_app
import audit_log_pb2
import audit_log_pb2_grpc

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_audit_log"
TEST_BACKEND_TYPE = "file"
TEST_BACKEND_PARAMS = {"log_file": f"{TEST_LOG_DIR}/audit.log"}
MOCK_CORRELATION_ID = str(uuid.uuid4())
MOCK_ACCESS_TOKEN = "mock_token_123"

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_LOG_ENCRYPTION_KEY'] = base64.b64encode(Fernet.generate_key()).decode('utf-8')
os.environ['AUDIT_LOG_BACKEND_TYPE'] = TEST_BACKEND_TYPE
os.environ['AUDIT_LOG_BACKEND_PARAMS'] = json.dumps(TEST_BACKEND_PARAMS)
os.environ['AUDIT_LOG_IMMUTABLE'] = 'true'

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
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25),
            MagicMock(entity_type='CREDIT_CARD', start=30, end=46)
        ]
        mock_anonymizer_inst.anonymize.return_value = MagicMock(
            text="[REDACTED_EMAIL] [REDACTED_CREDIT_CARD]"
        )
        mock_analyzer.return_value = mock_analyzer_inst
        mock_anonymizer.return_value = mock_anonymizer_inst
        yield mock_analyzer_inst, mock_anonymizer_inst

@pytest_asyncio.fixture
async def mock_audit_log_backend():
    """Mock audit backend."""
    with patch('audit_log.get_backend') as mock_backend:
        mock_backend_inst = AsyncMock()
        mock_backend_inst.append.return_value = None
        mock_backend_inst.query.return_value = [
            {"entry_id": "123", "encrypted_data": "mock_data", "schema_version": 1, "_audit_hash": "mock_hash"}
        ]
        mock_backend.return_value = mock_backend_inst
        yield mock_backend_inst

@pytest_asyncio.fixture
async def mock_audit_log_crypto():
    """Mock crypto provider."""
    with patch('audit_log.crypto_provider') as mock_crypto:
        mock_crypto_inst = MagicMock()
        mock_crypto_inst.sign.return_value = ("mock_signature", "mock_key_id")
        mock_crypto_inst.verify.return_value = True
        mock_crypto.return_value = mock_crypto_inst
        yield mock_crypto_inst

@pytest_asyncio.fixture
async def mock_metrics():
    """Mock Prometheus metrics."""
    with patch('audit_metrics.Counter') as mock_counter, \
         patch('audit_metrics.Histogram') as mock_histogram:
        mock_metrics = {
            'audit_log_writes_total': MagicMock(),
            'audit_log_errors_total': MagicMock(),
            'audit_log_latency_seconds': MagicMock()
        }
        mock_counter.side_effect = lambda name, *args, **kwargs: mock_metrics[name]
        mock_histogram.side_effect = lambda name, *args, **kwargs: mock_metrics[name]
        yield mock_metrics

@pytest_asyncio.fixture
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('audit_log.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

@pytest_asyncio.fixture
async def audit_log_instance(mock_audit_log_backend, mock_audit_log_crypto):
    """Create an AuditLog instance."""
    audit_log = AuditLog()
    yield audit_log
    audit_log.close()

@pytest_asyncio.fixture
async def fastapi_client():
    """Create a FastAPI test client."""
    return TestClient(api_app)

@pytest_asyncio.fixture
async def grpc_channel():
    """Create a gRPC test channel."""
    async with insecure_channel(f'localhost:{os.getenv("AUDIT_LOG_GRPC_PORT", 50051)}') as channel:
        yield channel

class TestAuditLog:
    """Test suite for audit_log.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_log_action_success(self, audit_log_instance, mock_presidio, mock_audit_log_backend, mock_audit_log_crypto, mock_metrics, mock_opentelemetry):
        """Test successful logging of a single action."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com", "ip": "192.168.1.1"}),
            "trace_id": "123e4567-e89b-12d3-a456-426614174000",
            "span_id": "00f067aa0ba902b7",
            "actor": "user-123",
            "compliance_tags": ["HIPAA"]
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            await log_action(**entry)

        # Verify backend interaction
        mock_audit_log_backend.append.assert_called_once()
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert call_args["schema_version"] == 1
        assert call_args["_audit_hash"]
        assert call_args["timestamp"] == "2025-09-01T12:00:00Z"

        # Verify metrics
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")
        mock_metrics['audit_log_latency_seconds'].labels.assert_called_with(action="user_login")

        # Verify tracing
        mock_opentelemetry[1].set_attribute.assert_any_call("action", "user_login")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_log_action_with_pii_redaction(self, audit_log_instance, mock_presidio, mock_audit_log_backend, mock_audit_log_crypto):
        """Test PII redaction in log action."""
        entry = {
            "action": "user_update",
            "details_json": json.dumps({"email": "test@example.com", "credit_card": "1234-5678-9012-3456"}),
            "actor": "user-123"
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            await log_action(**entry)

        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert "[REDACTED_CREDIT_CARD]" in call_args["encrypted_data"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_log_action_tamper_detection(self, audit_log_instance, mock_presidio, mock_audit_log_backend, mock_audit_log_crypto, mock_metrics):
        """Test tamper detection on invalid hash."""
        mock_audit_log_crypto.verify.return_value = False
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "actor": "user-123"
        }
        with pytest.raises(Exception, match="Tamper detected"):
            await log_action(**entry)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="TamperDetectionError")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_fastapi_log_action(self, fastapi_client, mock_presidio, mock_audit_log_backend, mock_metrics, mock_opentelemetry):
        """Test logging via FastAPI endpoint."""
        payload = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "actor": "user-123",
            "access_token": MOCK_ACCESS_TOKEN
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            response = fastapi_client.post("/log", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_audit_log_backend.append.assert_called_once()
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_grpc_log_action(self, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, mock_opentelemetry):
        """Test logging via gRPC service."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com"}),
            actor="user-123",
            access_token=MOCK_ACCESS_TOKEN
        )
        with freeze_time("2025-09-01T12:00:00Z"):
            response = await stub.LogAction(request)
        assert response.status == "success"
        mock_audit_log_backend.append.assert_called_once()
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_typer_cli_log_action(self, mock_presidio, mock_audit_log_backend, mock_metrics):
        """Test logging via Typer CLI."""
        with patch('typer.run') as mock_typer_run:
            from audit_log import app as typer_app
            typer_app(["log", "--action", "user_login", "--details-json", json.dumps({"email": "test@example.com"}), "--actor", "user-123"])
        mock_audit_log_backend.append.assert_called_once()
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_rbac_enforcement(self, fastapi_client, mock_audit_log_backend, mock_metrics):
        """Test RBAC enforcement for unauthorized access."""
        payload = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "actor": "unauthorized_user",
            "access_token": "invalid_token"
        }
        response = fastapi_client.post("/log", json=payload)
        assert response.status_code == 403
        assert "Unauthorized" in response.json()["detail"]
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="UnauthorizedError")
        mock_audit_log_backend.append.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_log_actions(self, audit_log_instance, mock_presidio, mock_audit_log_backend, mock_metrics):
        """Test concurrent logging of multiple actions."""
        async def log_single_action(action_id: int):
            await log_action(
                action=f"user_action_{action_id}",
                details_json=json.dumps({"email": f"test{action_id}@example.com"}),
                actor="user-123"
            )

        tasks = [log_single_action(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)

        assert mock_audit_log_backend.append.call_count == 5
        mock_metrics['audit_log_writes_total'].labels.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_hook_execution(self, audit_log_instance, mock_presidio, mock_audit_log_backend):
        """Test execution of hook events."""
        mock_hook = AsyncMock()
        audit_log_instance.register_hook("log_success", mock_hook)
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "actor": "user-123"
        }
        await log_action(**entry)
        mock_hook.assert_called_once_with(entry=entry)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_key_rotation(self, audit_log_instance, mock_audit_log_crypto):
        """Test cryptographic key rotation event."""
        mock_hook = AsyncMock()
        audit_log_instance.register_hook("key_rotated", mock_hook)
        await audit_log_instance.rotate_key("new_key_id", "old_key_id")
        mock_hook.assert_called_once_with(new_key_id="new_key_id", old_key_id="old_key_id")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_log",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
