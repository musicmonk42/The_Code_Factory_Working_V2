```python
"""
test_audit_log_proto.py

Regulated industry-grade test suite for audit_log.proto.

Features:
- Tests gRPC service (LogAction, LogStream) and message serialization/deserialization.
- Validates PII/secret redaction, audit logging, and provenance tracking.
- Ensures Prometheus metrics and OpenTelemetry tracing (via audit_log.py).
- Tests async-safe gRPC calls, streaming, and thread-safety.
- Verifies error handling, tamper detection, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real gRPC implementation with mocked external dependencies (backend, Presidio).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- grpcio, grpcio-tools, presidio-analyzer, presidio-anonymizer
- prometheus-client, opentelemetry-sdk, audit_log
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
import grpc
from grpc.aio import insecure_channel
from prometheus_client import REGISTRY

import audit_log_pb2
import audit_log_pb2_grpc
from audit_log import AuditLog, log_action

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_audit_log_proto"
TEST_BACKEND_TYPE = "file"
TEST_BACKEND_PARAMS = {"log_file": f"{TEST_LOG_DIR}/audit.log"}
MOCK_CORRELATION_ID = str(uuid.uuid4())
MOCK_ACCESS_TOKEN = "mock_token_123"
GRPC_PORT = 50051

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_LOG_ENCRYPTION_KEY'] = base64.b64encode(b"mock_key_32_bytes_1234567890abcd").decode('utf-8')
os.environ['AUDIT_LOG_BACKEND_TYPE'] = TEST_BACKEND_TYPE
os.environ['AUDIT_LOG_BACKEND_PARAMS'] = json.dumps(TEST_BACKEND_PARAMS)
os.environ['AUDIT_LOG_GRPC_PORT'] = str(GRPC_PORT)

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
    if Path(path).exists():
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
            {"entry_id": "123", "encrypted_data": "[REDACTED_EMAIL]", "schema_version": 1, "_audit_hash": "mock_hash"}
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
    with patch('audit_log.Counter') as mock_counter, \
         patch('audit_log.Histogram') as mock_histogram:
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
async def grpc_server(audit_log_instance, mock_audit_log_backend, mock_audit_log_crypto):
    """Start a gRPC server for testing."""
    from audit_log import serve_grpc_server
    server_task = asyncio.create_task(serve_grpc_server())
    yield
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass

@pytest_asyncio.fixture
async def audit_log_instance(mock_audit_log_backend, mock_audit_log_crypto):
    """Create an AuditLog instance."""
    audit_log = AuditLog()
    yield audit_log
    audit_log.close()

@pytest_asyncio.fixture
async def grpc_channel():
    """Create a gRPC test channel."""
    async with insecure_channel(f'localhost:{GRPC_PORT}') as channel:
        yield channel

class TestAuditLogProto:
    """Test suite for audit_log.proto."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_log_action_unary(self, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, mock_opentelemetry, grpc_server):
        """Test unary LogAction RPC."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com", "credit_card": "1234-5678-9012-3456"}),
            trace_id="123e4567-e89b-12d3-a456-426614174000",
            span_id="00f067aa0ba902b7",
            actor="user-123",
            ip_address="192.168.1.1",
            geolocation=json.dumps({"country": "US", "city": "New York"}),
            compliance_tags=["HIPAA"],
            access_token=MOCK_ACCESS_TOKEN
        )
        with freeze_time("2025-09-01T12:00:00Z"):
            response = await stub.LogAction(request)
        assert response.status == "success"
        mock_audit_log_backend.append.assert_called_once()
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert "[REDACTED_CREDIT_CARD]" in call_args["encrypted_data"]
        assert call_args["timestamp"] == "2025-09-01T12:00:00Z"
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")
        mock_opentelemetry[1].set_attribute.assert_any_call("action", "user_login")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_log_stream(self, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, mock_opentelemetry, grpc_server):
        """Test streaming LogStream RPC."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        async def send_requests():
            requests = [
                audit_log_pb2.LogActionRequest(
                    action=f"user_action_{i}",
                    details_json=json.dumps({"email": f"test{i}@example.com"}),
                    actor="user-123",
                    access_token=MOCK_ACCESS_TOKEN
                ) for i in range(3)
            ]
            for req in requests:
                yield req
                await asyncio.sleep(0.01)

        with freeze_time("2025-09-01T12:00:00Z"):
            response = await stub.LogStream(send_requests())
        assert response.status == "success"
        assert mock_audit_log_backend.append.call_count == 3
        for i, call in enumerate(mock_audit_log_backend.append.call_args_list):
            assert f"[REDACTED_EMAIL]" in call[0][0]["encrypted_data"]
        mock_metrics['audit_log_writes_total'].labels.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_tamper_detection(self, grpc_channel, mock_presidio, mock_audit_log_backend, mock_audit_log_crypto, mock_metrics, grpc_server):
        """Test tamper detection in LogAction RPC."""
        mock_audit_log_crypto.verify.return_value = False
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com"}),
            actor="user-123",
            access_token=MOCK_ACCESS_TOKEN
        )
        with pytest.raises(grpc.RpcError, match="Tamper detected"):
            await stub.LogAction(request)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="TamperDetectionError")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_unauthorized_access(self, grpc_channel, mock_audit_log_backend, mock_metrics, grpc_server):
        """Test unauthorized access in LogAction RPC."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com"}),
            actor="unauthorized_user",
            access_token="invalid_token"
        )
        with pytest.raises(grpc.RpcError, match="Unauthorized"):
            await stub.LogAction(request)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="UnauthorizedError")
        mock_audit_log_backend.append.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_message_serialization(self, grpc_channel, mock_audit_log_backend, mock_metrics, grpc_server):
        """Test handling of invalid message serialization."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json="invalid_json",  # Invalid JSON
            actor="user-123",
            access_token=MOCK_ACCESS_TOKEN
        )
        with pytest.raises(grpc.RpcError, match="Invalid JSON"):
            await stub.LogAction(request)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="SerializationError")
        mock_audit_log_backend.append.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_concurrent_log_actions(self, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, grpc_server):
        """Test concurrent LogAction RPCs."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        async def send_request(i):
            request = audit_log_pb2.LogActionRequest(
                action=f"user_action_{i}",
                details_json=json.dumps({"email": f"test{i}@example.com"}),
                actor="user-123",
                access_token=MOCK_ACCESS_TOKEN
            )
            return await stub.LogAction(request)

        tasks = [send_request(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            responses = await asyncio.gather(*tasks)
        assert all(resp.status == "success" for resp in responses)
        assert mock_audit_log_backend.append.call_count == 5
        mock_metrics['audit_log_writes_total'].labels.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_message_provenance(self, grpc_channel, mock_presidio, mock_audit_log_backend, grpc_server):
        """Test provenance tracking in LogActionRequest."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com"}),
            trace_id="123e4567-e89b-12d3-a456-426614174000",
            span_id="00f067aa0ba902b7",
            requirement_id="REQ-456",
            session_id="session-7890abc",
            actor="user-123",
            ip_address="192.168.1.1",
            geolocation=json.dumps({"country": "US", "city": "New York"}),
            compliance_tags=["HIPAA", "GDPR"],
            access_token=MOCK_ACCESS_TOKEN
        )
        with freeze_time("2025-09-01T12:00:00Z"):
            response = await stub.LogAction(request)
        assert response.status == "success"
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert call_args["trace_id"] == "123e4567-e89b-12d3-a456-426614174000"
        assert call_args["span_id"] == "00f067aa0ba902b7"
        assert call_args["requirement_id"] == "REQ-456"
        assert call_args["session_id"] == "session-7890abc"
        assert set(call_args["compliance_tags"]) == {"HIPAA", "GDPR"}

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
```