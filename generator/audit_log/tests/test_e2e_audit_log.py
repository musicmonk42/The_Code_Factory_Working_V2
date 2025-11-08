
"""
test_e2e_audit_log.py

Regulated industry-grade E2E integration test suite for audit_log core modules.

Features:
- Tests E2E workflows for audit_log.py (REST API, gRPC, CLI), audit_metrics.py, audit_plugins.py, and audit_utils.py.
- Validates PII/secret redaction, audit logging, tamper detection, and provenance tracking.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, concurrent workflows, and error handling.
- Verifies compliance (SOC2/PCI DSS/HIPAA) with real implementations and mocked external dependencies.

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- fastapi, grpcio, grpcio-tools, presidio-analyzer, prometheus-client, opentelemetry-sdk, typer
- audit_log, audit_metrics, audit_plugins, audit_utils
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
from fastapi.testclient import TestClient
import grpc
from grpc.aio import insecure_channel
from prometheus_client import REGISTRY
import typer
import aiofiles

from audit_log import AuditLog, log_action, api_app, serve_grpc_server, app as typer_app
from audit_metrics import LOG_WRITES, LOG_ERRORS
from audit_plugins import AuditPlugin, register_plugin
from audit_utils import compute_hash
import audit_log_pb2
import audit_log_pb2_grpc

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_e2e_audit_log"
TEST_LOG_FILE = f"{TEST_LOG_DIR}/audit.log"
MOCK_CORRELATION_ID = str(uuid.uuid4())
MOCK_ACCESS_TOKEN = "mock_token_123"
GRPC_PORT = 50052  # Use a unique port to avoid conflicts

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_LOG_ENCRYPTION_KEY'] = base64.b64encode(b"mock_key_32_bytes_1234567890abcd").decode('utf-8')
os.environ['AUDIT_LOG_BACKEND_TYPE'] = 'file'
os.environ['AUDIT_LOG_BACKEND_PARAMS'] = json.dumps({"log_file": TEST_LOG_FILE})
os.environ['AUDIT_LOG_GRPC_PORT'] = str(GRPC_PORT)
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
            MagicMock(entity_type='EMAIL_ADDRESS', start=10, end=25)
        ]
        mock_anonymizer_inst.anonymize.return_value = MagicMock(
            text="[REDACTED_EMAIL]"
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
async def mock_crypto():
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
            'audit_log_latency_seconds': MagicMock(),
            'audit_plugin_invocations_total': MagicMock()
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
async def audit_log_instance(mock_audit_log_backend, mock_crypto):
    """Create an AuditLog instance."""
    audit_log = AuditLog()
    yield audit_log
    audit_log.close()

@pytest_asyncio.fixture
async def fastapi_client():
    """Create a FastAPI test client."""
    return TestClient(api_app)

@pytest_asyncio.fixture
async def grpc_server(mock_audit_log_backend, mock_crypto):
    """Start a gRPC server for testing."""
    server_task = asyncio.create_task(serve_grpc_server())
    yield
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass

@pytest_asyncio.fixture
async def grpc_channel():
    """Create a gRPC test channel."""
    async with insecure_channel(f'localhost:{GRPC_PORT}') as channel:
        yield channel

@pytest_asyncio.fixture
async def test_plugin(audit_log_instance):
    """Create a test plugin."""
    class TestPlugin(AuditPlugin):
        def __init__(self):
            super().__init__("TestPlugin")
            self.processed_entries = 0

        async def process(self, event: str, data: Dict[str, Any]) -> Dict[str, Any]:
            self.processed_entries += 1
            data["processed"] = True
            return data

    plugin = TestPlugin()
    audit_log_instance.register_plugin("test_plugin", plugin, {"redact": True, "augment": True})
    yield plugin

class TestE2EAuditLog:
    """E2E integration test suite for audit_log core modules."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_rest_api_workflow(self, audit_log_instance, fastapi_client, mock_presidio, mock_audit_log_backend, mock_metrics, mock_opentelemetry, test_plugin):
        """Test E2E workflow for REST API: log action, query, and plugin processing."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "access_token": MOCK_ACCESS_TOKEN
        }
        # Log via REST API
        with freeze_time("2025-09-01T12:00:00Z"):
            response = fastapi_client.post("/log", json=entry)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_audit_log_backend.append.assert_called_once()
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert call_args["processed"] is True  # Plugin processed
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")
        mock_opentelemetry[1].set_attribute.assert_any_call("action", "user_login")

        # Query via REST API
        response = fastapi_client.get("/recent_history?limit=1", headers={"access_token": MOCK_ACCESS_TOKEN})
        assert response.status_code == 200
        results = response.json()["entries_json"]
        assert len(results) == 1
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]
        mock_audit_log_backend.query.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_grpc_workflow(self, audit_log_instance, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, test_plugin, grpc_server):
        """Test E2E workflow for gRPC: log action, query, and plugin processing."""
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com"}),
            trace_id=MOCK_CORRELATION_ID,
            actor="user-123",
            access_token=MOCK_ACCESS_TOKEN
        )
        # Log via gRPC
        with freeze_time("2025-09-01T12:00:00Z"):
            response = await stub.LogAction(request)
        assert response.status == "success"
        mock_audit_log_backend.append.assert_called_once()
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert call_args["processed"] is True
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")

        # Query via gRPC
        response = await stub.GetRecentHistory(audit_log_pb2.GetRecentHistoryRequest(limit=1, access_token=MOCK_ACCESS_TOKEN))
        assert len(response.entries_json) == 1
        assert "[REDACTED_EMAIL]" in response.entries_json[0]["encrypted_data"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_cli_workflow(self, audit_log_instance, mock_presidio, mock_audit_log_backend, mock_metrics, test_plugin):
        """Test E2E workflow for CLI: log action and plugin processing."""
        with patch('typer.run') as mock_typer_run:
            with freeze_time("2025-09-01T12:00:00Z"):
                typer_app(["log", "--action", "user_login", "--details-json", json.dumps({"email": "test@example.com"}), "--actor", "user-123"])
        mock_audit_log_backend.append.assert_called_once()
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert call_args["processed"] is True
        mock_metrics['audit_plugin_invocations_total'].labels.assert_called_with(event="pre_append", plugin="TestPlugin")

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_tamper_detection(self, audit_log_instance, fastapi_client, mock_crypto, mock_audit_log_backend, mock_metrics):
        """Test E2E tamper detection via REST API."""
        mock_crypto.verify.return_value = False
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "actor": "user-123",
            "access_token": MOCK_ACCESS_TOKEN
        }
        with pytest.raises(Exception, match="Tamper detected"):
            with freeze_time("2025-09-01T12:00:00Z"):
                fastapi_client.post("/log", json=entry)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="TamperDetectionError")

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_e2e_concurrent_workflow(self, audit_log_instance, fastapi_client, mock_presidio, mock_audit_log_backend, mock_metrics):
        """Test concurrent logging across REST API and gRPC."""
        async def log_via_rest(i):
            entry = {
                "action": f"user_action_{i}",
                "details_json": json.dumps({"email": f"test{i}@example.com"}),
                "actor": "user-123",
                "access_token": MOCK_ACCESS_TOKEN
            }
            with freeze_time("2025-09-01T12:00:00Z"):
                response = fastapi_client.post("/log", json=entry)
            assert response.status_code == 200

        async def log_via_grpc(i, channel):
            stub = audit_log_pb2_grpc.AuditLogServiceStub(channel)
            request = audit_log_pb2.LogActionRequest(
                action=f"user_action_{i}",
                details_json=json.dumps({"email": f"test{i}@example.com"}),
                actor="user-123",
                access_token=MOCK_ACCESS_TOKEN
            )
            with freeze_time("2025-09-01T12:00:00Z"):
                response = await stub.LogAction(request)
            assert response.status == "success"

        tasks = [
            log_via_rest(i) for i in range(3)
        ] + [
            log_via_grpc(i, await insecure_channel(f'localhost:{GRPC_PORT}')) for i in range(3, 6)
        ]
        await asyncio.gather(*tasks)
        assert mock_audit_log_backend.append.call_count == 6
        assert REGISTRY.get_sample_value('audit_log_writes_total', {'backend': 'FileBackend'}) == 6

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_metrics_emission(self, audit_log_instance, fastapi_client, mock_metrics, mock_audit_log_backend):
        """Test E2E metrics emission for logging and errors."""
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "actor": "user-123",
            "access_token": MOCK_ACCESS_TOKEN
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            response = fastapi_client.post("/log", json=entry)
        assert response.status_code == 200
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")

        # Simulate error
        with patch('audit_log.crypto_provider.verify', return_value=False):
            with pytest.raises(Exception, match="Tamper detected"):
                fastapi_client.post("/log", json=entry)
        mock_metrics['audit_log_errors_total'].labels.assert_called_with(error_type="TamperDetectionError")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_log",
        "--cov=audit_metrics",
        "--cov=audit_plugins",
        "--cov=audit_utils",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
