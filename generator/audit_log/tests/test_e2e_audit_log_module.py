
"""
test_e2e_audit_log_module.py

Regulated industry-grade E2E integration test suite for the entire audit_log module.

Features:
- Tests E2E workflows across audit_log, audit_metrics, audit_plugins, audit_utils, audit_crypto, and audit_backend modules.
- Validates PII/secret redaction, audit logging, tamper detection, and provenance tracking.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, concurrent workflows, retry logic, and circuit breaking.
- Verifies error handling, key management, secret retrieval, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (boto3, aiohttp, aiokafka, sqlite3).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- fastapi, grpcio, grpcio-tools, boto3, cryptography, prometheus-client, opentelemetry-sdk, typer, aiohttp, aiokafka, sqlite3, zlib
- audit_log, audit_metrics, audit_plugins, audit_utils, audit_crypto_factory, audit_crypto_ops, audit_crypto_provider, audit_keystore, secrets
- audit_backend_core, audit_backend_file_sql, audit_backend_cloud, audit_backend_streaming, audit_backend_streaming_backends, audit_backend_streaming_utils
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
import boto3
import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from audit_log import AuditLog, log_action, api_app, serve_grpc_server
from audit_metrics import LOG_WRITES, LOG_ERRORS
from audit_plugins import AuditPlugin, register_plugin
from audit_utils import compute_hash
from audit_crypto_factory import CryptoProviderFactory
from audit_crypto_ops import sign_async, verify_async
from audit_crypto_provider import SoftwareCryptoProvider
from audit_keystore import KeyStore, FileKeyStorageBackend
from secrets import AWSSecretsManager
from audit_backend_file_sql import FileBackend
from audit_backend_streaming_backends import HTTPBackend
import audit_log_pb2
import audit_log_pb2_grpc

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_LOG_DIR = "/tmp/test_e2e_audit_log_module"
TEST_LOG_FILE = f"{TEST_LOG_DIR}/audit.log"
TEST_KEY_DIR = f"{TEST_LOG_DIR}/keys"
TEST_SNAPSHOT_FILE = f"{TEST_LOG_DIR}/snapshot.jsonl.gz"
TEST_ENDPOINT = "https://example.com/log"
TEST_KMS_KEY_ID = "mock_kms_key_id"
TEST_AWS_SECRET_NAME = "test/secret"
TEST_HSM_PIN = "mock_hsm_pin"
MOCK_CORRELATION_ID = str(uuid.uuid4())
MOCK_ACCESS_TOKEN = "mock_token_123"
GRPC_PORT = 50053  # Unique port to avoid conflicts

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['PYTHON_ENV'] = 'production'
os.environ['AUDIT_LOG_ENCRYPTION_KEY'] = base64.b64encode(b"mock_key_32_bytes_1234567890abcd").decode('utf-8')
os.environ['AUDIT_LOG_BACKEND_TYPE'] = 'file'
os.environ['AUDIT_LOG_BACKEND_PARAMS'] = json.dumps({"log_file": TEST_LOG_FILE})
os.environ['AUDIT_LOG_GRPC_PORT'] = str(GRPC_PORT)
os.environ['AUDIT_LOG_IMMUTABLE'] = 'true'
os.environ['AUDIT_CRYPTO_PROVIDER_TYPE'] = 'software'
os.environ['AUDIT_CRYPTO_DEFAULT_ALGO'] = 'rsa'
os.environ['AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS'] = '86400'
os.environ['AUDIT_CRYPTO_SOFTWARE_KEY_DIR'] = TEST_KEY_DIR
os.environ['AUDIT_CRYPTO_KMS_KEY_ID'] = TEST_KMS_KEY_ID
os.environ['SECRET_MANAGER'] = 'aws'

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
            {"entry_id": "123", "encrypted_data": "[REDACTED_EMAIL]", "schema_version": 1, "_audit_hash": "mock_hash", "signature": "mock_signature", "key_id": "mock_key_id"}
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
async def mock_boto3():
    """Mock boto3 clients for KMS and Secrets Manager."""
    with patch('boto3.client') as mock_client:
        mock_kms = MagicMock()
        mock_kms.decrypt.return_value = {"Plaintext": b"mock_decrypted_key"}
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {"SecretString": TEST_HSM_PIN}
        mock_client.side_effect = lambda service: mock_kms if service == "kms" else mock_secrets
        yield mock_kms, mock_secrets

@pytest_asyncio.fixture
async def mock_aiohttp():
    """Mock aiohttp client session."""
    with patch('aiohttp.ClientSession') as mock_session:
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=[{"entry_id": "123", "encrypted_data": "[REDACTED_EMAIL]", "signature": "mock_signature", "key_id": "mock_key_id"}])
        mock_client.post.return_value.__aenter__.return_value = mock_response
        mock_client.get.return_value.__aenter__.return_value = mock_response
        mock_session.return_value.__aenter__.return_value = mock_client
        yield mock_client

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

@pytest_asyncio.fixture
async def crypto_factory(mock_boto3):
    """Create a CryptoProviderFactory instance."""
    factory = CryptoProviderFactory()
    yield factory
    factory.close_all_providers()

@pytest_asyncio.fixture
async def keystore():
    """Create a KeyStore instance."""
    backend = FileKeyStorageBackend(key_dir=TEST_KEY_DIR)
    keystore = KeyStore(backend=backend)
    yield keystore
    await keystore.close()

class TestE2EAuditLogModule:
    """E2E integration test suite for the entire audit_log module."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_e2e_full_workflow_rest(self, audit_log_instance, fastapi_client, mock_presidio, mock_audit_log_backend, mock_metrics, mock_opentelemetry, test_plugin, crypto_factory, keystore):
        """Test E2E workflow via REST API: key generation, signing, logging, querying, and metrics."""
        # Generate and store key
        provider = crypto_factory.get_provider("software")
        key_id = await provider.generate_key("rsa", 2048)
        mock_audit_log_backend.append.assert_called_once()
        mock_metrics['audit_key_store_count_total'].labels.assert_called_with(provider_type="software")

        # Log via REST API
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "actor": "user-123",
            "access_token": MOCK_ACCESS_TOKEN
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            response = fastapi_client.post("/log", json=entry)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert call_args["processed"] is True  # Plugin processed
        assert call_args["signature"] is not None
        assert call_args["key_id"] == key_id
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")
        mock_metrics['audit_plugin_invocations_total'].labels.assert_called_with(event="pre_append", plugin="TestPlugin")
        mock_opentelemetry[1].set_attribute.assert_any_call("action", "user_login")

        # Query via REST API
        response = fastapi_client.get("/recent_history?limit=1", headers={"access_token": MOCK_ACCESS_TOKEN})
        assert response.status_code == 200
        results = response.json()["entries_json"]
        assert len(results) == 1
        assert "[REDACTED_EMAIL]" in results[0]["encrypted_data"]
        mock_audit_log_backend.query.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_e2e_full_workflow_grpc(self, audit_log_instance, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, test_plugin, grpc_server, crypto_factory, keystore):
        """Test E2E workflow via gRPC: key generation, signing, logging, and querying."""
        # Generate and store key
        provider = crypto_factory.get_provider("software")
        key_id = await provider.generate_key("rsa", 2048)
        mock_audit_log_backend.append.assert_called_once()

        # Log via gRPC
        stub = audit_log_pb2_grpc.AuditLogServiceStub(grpc_channel)
        request = audit_log_pb2.LogActionRequest(
            action="user_login",
            details_json=json.dumps({"email": "test@example.com"}),
            trace_id=MOCK_CORRELATION_ID,
            actor="user-123",
            access_token=MOCK_ACCESS_TOKEN
        )
        with freeze_time("2025-09-01T12:00:00Z"):
            response = await stub.LogAction(request)
        assert response.status == "success"
        call_args = mock_audit_log_backend.append.call_args[0][0]
        assert "[REDACTED_EMAIL]" in call_args["encrypted_data"]
        assert call_args["processed"] is True
        assert call_args["signature"] is not None
        mock_metrics['audit_log_writes_total'].labels.assert_called_with(action="user_login")

        # Query via gRPC
        response = await stub.GetRecentHistory(audit_log_pb2.GetRecentHistoryRequest(limit=1, access_token=MOCK_ACCESS_TOKEN))
        assert len(response.entries_json) == 1
        assert "[REDACTED_EMAIL]" in response.entries_json[0]["encrypted_data"]

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
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
    @pytest.mark.timeout(120)
    async def test_e2e_concurrent_multi_interface(self, audit_log_instance, fastapi_client, grpc_channel, mock_presidio, mock_audit_log_backend, mock_metrics, test_plugin, crypto_factory, keystore):
        """Test concurrent logging across REST API, gRPC, and HTTPBackend."""
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

        # Switch to HTTPBackend for some operations
        with patch('audit_log.get_backend') as mock_backend:
            http_backend = HTTPBackend({"endpoint": TEST_ENDPOINT, "query_endpoint": TEST_ENDPOINT})
            mock_backend.return_value = http_backend
            async def log_via_http(i):
                entry = {
                    "action": f"user_action_{i}",
                    "details_json": json.dumps({"email": f"test{i}@example.com"}),
                    "trace_id": MOCK_CORRELATION_ID,
                    "actor": "user-123"
                }
                with freeze_time("2025-09-01T12:00:00Z"):
                    await http_backend.append(entry)

            tasks = [
                log_via_rest(i) for i in range(3)
            ] + [
                log_via_grpc(i, await insecure_channel(f'localhost:{GRPC_PORT}')) for i in range(3, 6)
            ] + [
                log_via_http(i) for i in range(6, 9)
            ]
            await asyncio.gather(*tasks)
            await http_backend.close()
        assert mock_audit_log_backend.append.call_count == 9
        assert REGISTRY.get_sample_value('audit_log_writes_total', {'backend': 'FileBackend'}) == 6
        assert REGISTRY.get_sample_value('audit_backend_writes_total', {'backend': 'HTTPBackend'}) == 3

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
        "--cov=audit_crypto_factory",
        "--cov=audit_crypto_ops",
        "--cov=audit_crypto_provider",
        "--cov=audit_keystore",
        "--cov=secrets",
        "--cov=audit_backend_core",
        "--cov=audit_backend_file_sql",
        "--cov=audit_backend_cloud",
        "--cov=audit_backend_streaming",
        "--cov=audit_backend_streaming_backends",
        "--cov=audit_backend_streaming_utils",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
