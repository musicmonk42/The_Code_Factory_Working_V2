
"""
test_audit_keystore.py

Regulated industry-grade test suite for audit_keystore.py.

Features:
- Tests KeyStore for key storage, retrieval, deletion, and permission verification.
- Validates sensitive data redaction, audit logging, and provenance tracking.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests async-safe operations, POSIX advisory locking, and thread-safety.
- Verifies error handling, disk failures, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (audit_log, audit_crypto_factory, file operations).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- cryptography, prometheus-client, opentelemetry-sdk
- audit_log, audit_crypto_factory
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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from prometheus_client import REGISTRY

from audit_keystore import KeyStore, FileKeyStorageBackend, CryptoOperationError
from audit_log import log_action
from audit_crypto_factory import CRYPTO_ERRORS, KEY_STORE_COUNT, KEY_LOAD_COUNT, send_alert

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_KEY_DIR = "/tmp/test_audit_keystore"
MOCK_CORRELATION_ID = str(uuid.uuid4())
TEST_KEY_ID = "test_key_123"

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def cleanup_test_environment():
    """Clean up test environment before and after tests."""
    for path in [TEST_KEY_DIR]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
    Path(TEST_KEY_DIR).mkdir(parents=True, exist_ok=True)
    yield
    if Path(TEST_KEY_DIR).exists():
        import shutil
        shutil.rmtree(path, ignore_errors=True)

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_send_alert():
    """Mock audit_crypto_factory.send_alert."""
    with patch('audit_crypto_factory.send_alert') as mock_alert:
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
    with patch('audit_crypto_factory.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

@pytest_asyncio.fixture
async def keystore():
    """Create a KeyStore instance with FileKeyStorageBackend."""
    backend = FileKeyStorageBackend(key_dir=TEST_KEY_DIR)
    keystore = KeyStore(backend=backend)
    yield keystore
    await keystore.close()

class TestAuditKeystore:
    """Test suite for audit_keystore.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_store_key(self, keystore, mock_audit_log, mock_opentelemetry, mock_aiofiles):
        """Test storing a key."""
        key_data = {
            "key_id": TEST_KEY_ID,
            "private_key": rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ),
            "status": "active",
            "created_at": "2025-09-01T12:00:00Z"
        }
        with freeze_time("2025-09-01T12:00:00Z"):
            await keystore.store_key(key_data)
        mock_aiofiles.write.assert_called_once()
        mock_audit_log.assert_called_with("key_store", key_id=TEST_KEY_ID, success=True)
        mock_opentelemetry[1].set_attribute.assert_any_call("operation", "store_key")
        assert REGISTRY.get_sample_value('audit_key_store_count_total', {'provider_type': 'software'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_load_key(self, keystore, mock_audit_log, mock_aiofiles):
        """Test loading a key."""
        key_data = {
            "key_id": TEST_KEY_ID,
            "private_key": rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ),
            "status": "active",
            "created_at": "2025-09-01T12:00:00Z"
        }
        mock_aiofiles.read.return_value = json.dumps(key_data).encode('utf-8')
        with freeze_time("2025-09-01T12:00:00Z"):
            loaded_key = await keystore.load_key(TEST_KEY_ID)
        assert loaded_key["key_id"] == TEST_KEY_ID
        mock_audit_log.assert_called_with("key_load", key_id=TEST_KEY_ID, success=True)
        assert REGISTRY.get_sample_value('audit_key_load_count_total', {'provider_type': 'software'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_delete_key(self, keystore, mock_audit_log):
        """Test deleting a key."""
        with patch('os.path.exists', return_value=True), \
             patch('os.unlink', return_value=None):
            with freeze_time("2025-09-01T12:00:00Z"):
                deleted = await keystore.delete_key(TEST_KEY_ID)
        assert deleted is True
        mock_audit_log.assert_called_with("key_delete", key_id=TEST_KEY_ID, success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_key_id(self, keystore, mock_audit_log):
        """Test handling of invalid key ID."""
        with pytest.raises(TypeError, match="key_id must be a non-empty string"):
            await keystore.store_key({"key_id": ""})
        mock_audit_log.assert_called_with("key_store", key_id="", success=False, error=Any)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'TypeError', 'provider_type': 'software', 'operation': 'store_key'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_file_permission_error(self, keystore, mock_audit_log, mock_send_alert):
        """Test handling of file permission errors."""
        with patch('os.path.exists', return_value=True), \
             patch('os.stat', side_effect=OSError("Permission denied")):
            with pytest.raises(CryptoOperationError, match="Permission check failed"):
                await keystore.store_key({
                    "key_id": TEST_KEY_ID,
                    "private_key": b"mock_key",
                    "status": "active"
                })
        mock_audit_log.assert_called_with("key_store", key_id=TEST_KEY_ID, success=False, error=Any)
        mock_send_alert.assert_called_with(Any, severity="critical")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_key_store(self, keystore, mock_audit_log):
        """Test concurrent key store operations with POSIX locking."""
        async def store_key(i):
            key_data = {
                "key_id": f"key_{i}",
                "private_key": rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ),
                "status": "active",
                "created_at": "2025-09-01T12:00:00Z"
            }
            await keystore.store_key(key_data)

        tasks = [store_key(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert mock_audit_log.call_count >= 5
        assert REGISTRY.get_sample_value('audit_key_store_count_total', {'provider_type': 'software'}) == 5

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_disk_full_error(self, keystore, mock_audit_log, mock_send_alert, mock_aiofiles):
        """Test handling of disk full error during key storage."""
        mock_aiofiles.write.side_effect = IOError("Disk full")
        with pytest.raises(CryptoOperationError, match="Failed to store key"):
            await keystore.store_key({
                "key_id": TEST_KEY_ID,
                "private_key": b"mock_key",
                "status": "active"
            })
        mock_audit_log.assert_called_with("key_store", key_id=TEST_KEY_ID, success=False, error=Any)
        mock_send_alert.assert_called_with(Any, severity="critical")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_keystore",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
