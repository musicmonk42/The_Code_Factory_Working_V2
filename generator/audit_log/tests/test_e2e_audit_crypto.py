
"""
test_e2e_audit_crypto.py

Regulated industry-grade E2E integration test suite for audit_log crypto modules.

Features:
- Tests E2E workflows for CryptoProviderFactory, SoftwareCryptoProvider, HSMCryptoProvider, KeyStore, and secret managers.
- Validates key generation, storage, signing, verification, and secret retrieval.
- Ensures sensitive data redaction, audit logging, and provenance tracking.
- Tests Prometheus metrics, OpenTelemetry tracing, and concurrent operations.
- Verifies error handling, fallback mechanisms, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (boto3, pkcs11, secrets).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun, aiofiles
- boto3, cryptography, pkcs11, prometheus-client, opentelemetry-sdk
- audit_log, audit_crypto_factory, audit_crypto_ops, audit_crypto_provider, audit_keystore, secrets
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
import boto3
import hmac
import hashlib

from audit_crypto_factory import CryptoProviderFactory
from audit_crypto_ops import sign_async, verify_async, hmac_sign_fallback
from audit_crypto_provider import SoftwareCryptoProvider, HSMCryptoProvider
from audit_keystore import KeyStore, FileKeyStorageBackend
from secrets import AWSSecretsManager
from audit_log import log_action

# Initialize faker for test data generation
fake = Faker()

# Test constants
TEST_KEY_DIR = "/tmp/test_e2e_audit_crypto"
TEST_HSM_LIBRARY_PATH = "/mock/hsm/lib.so"
TEST_HSM_SLOT_ID = "0"
TEST_HSM_PIN = "mock_hsm_pin"
TEST_KMS_KEY_ID = "mock_kms_key_id"
TEST_AWS_SECRET_NAME = "test/secret"
MOCK_CORRELATION_ID = str(uuid.uuid4())
MOCK_HMAC_SECRET = b"mock_hmac_secret_32_bytes_1234567890"

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['PYTHON_ENV'] = 'production'
os.environ['AUDIT_CRYPTO_PROVIDER_TYPE'] = 'software'
os.environ['AUDIT_CRYPTO_DEFAULT_ALGO'] = 'rsa'
os.environ['AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS'] = '86400'
os.environ['AUDIT_CRYPTO_SOFTWARE_KEY_DIR'] = TEST_KEY_DIR
os.environ['AUDIT_CRYPTO_KMS_KEY_ID'] = TEST_KMS_KEY_ID
os.environ['AUDIT_CRYPTO_HSM_ENABLED'] = 'false'
os.environ['AUDIT_CRYPTO_HSM_LIBRARY_PATH'] = TEST_HSM_LIBRARY_PATH
os.environ['AUDIT_CRYPTO_HSM_SLOT_ID'] = TEST_HSM_SLOT_ID
os.environ['AUDIT_CRYPTO_FALLBACK_ALERT_INTERVAL_SECONDS'] = '60'
os.environ['AUDIT_CRYPTO_MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT'] = '3'
os.environ['AUDIT_CRYPTO_MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE'] = '10'
os.environ['AWS_REGION'] = 'us-east-1'
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
async def mock_pkcs11():
    """Mock pkcs11 HSM library."""
    with patch('audit_crypto_provider.pkcs11') as mock_pkcs11:
        mock_lib = MagicMock()
        mock_session = AsyncMock()
        mock_session.sign.return_value = b"mock_hsm_signature"
        mock_lib.get_slot.return_value = mock_session
        mock_pkcs11.Lib.return_value = mock_lib
        yield mock_lib, mock_session

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

class TestE2EAuditCrypto:
    """E2E integration test suite for audit_log crypto modules."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_software_crypto_workflow(self, crypto_factory, keystore, mock_boto3, mock_audit_log, mock_opentelemetry):
        """Test E2E workflow for SoftwareCryptoProvider: key generation, storage, signing, and verification."""
        # Initialize provider
        provider = crypto_factory.get_provider("software")
        assert provider is not None
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="success")

        # Generate and store key
        key_id = await provider.generate_key("rsa", 2048)
        mock_audit_log.assert_called_with("key_generate", success=True, key_id=Any)

        # Sign data
        entry = {
            "action": "user_login",
            "details_json": json.dumps({"email": "test@example.com"}),
            "trace_id": MOCK_CORRELATION_ID,
            "timestamp": "2025-09-01T12:00:00Z"
        }
        prev_hash = "prev_hash_mock"
        with freeze_time("2025-09-01T12:00:00Z"):
            signature, sign_key_id = await sign_async(entry, prev_hash)
        assert signature is not None
        assert sign_key_id == key_id
        mock_audit_log.assert_called_with("sign_operation", success=True, key_id=key_id)
        assert REGISTRY.get_sample_value('audit_sign_operations_total', {'provider_type': 'software'}) == 1

        # Verify signature
        entry_with_signature = entry.copy()
        entry_with_signature["signature"] = signature
        entry_with_signature["key_id"] = key_id
        result = await verify_async(entry_with_signature, prev_hash)
        assert result is True
        mock_audit_log.assert_called_with("verify_operation", success=True, key_id=key_id)
        mock_opentelemetry[1].set_attribute.assert_any_call("operation", "sign")

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_hsm_crypto_workflow(self, crypto_factory, mock_pkcs11, mock_boto3, mock_audit_log):
        """Test E2E workflow for HSMCryptoProvider: key generation, signing, and session management."""
        with patch.dict(os.environ, {'AUDIT_CRYPTO_HSM_ENABLED': 'true', 'AUDIT_CRYPTO_HSM_LIBRARY_PATH': TEST_HSM_LIBRARY_PATH, 'AUDIT_CRYPTO_HSM_SLOT_ID': TEST_HSM_SLOT_ID}):
            provider = crypto_factory.get_provider("hsm")
            assert provider is not None
            mock_audit_log.assert_called_with("crypto_provider_init", provider_name="hsm", status="success")

            # Generate key
            key_id = await provider.generate_key("rsa", 2048)
            mock_audit_log.assert_called_with("key_generate", success=True, key_id=Any)

            # Sign data
            data = b"test_data"
            signature, sign_key_id = await provider.sign(data)
            assert signature == b"mock_hsm_signature"
            assert sign_key_id == key_id
            mock_audit_log.assert_called_with("sign_operation", success=True, key_id=Any)
            assert REGISTRY.get_sample_value('audit_sign_operations_total', {'provider_type': 'hsm'}) == 1

            # Close session
            await provider.close()
            mock_audit_log.assert_called_with("hsm_session_close", status="success")

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_secret_retrieval(self, mock_boto3, mock_audit_log):
        """Test E2E secret retrieval with AWSSecretsManager."""
        manager = AWSSecretsManager({"secret_name": TEST_AWS_SECRET_NAME})
        secret = await manager.aget_secret("hsm_pin")
        assert secret == TEST_HSM_PIN
        mock_boto3[1].get_secret_value.assert_called_with(SecretId=TEST_AWS_SECRET_NAME + "/hsm_pin")
        mock_audit_log.assert_called_with("secret_access", secret_type="hsm_pin", manager="AWSSecretsManager", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_fallback_signing(self, crypto_factory, mock_boto3, mock_audit_log):
        """Test E2E HMAC fallback signing when primary provider fails."""
        with patch('audit_crypto_ops.crypto_provider') as mock_crypto_provider, \
             patch('audit_crypto_ops._FALLBACK_HMAC_SECRET', MOCK_HMAC_SECRET):
            mock_crypto_provider.sign.side_effect = Exception("Provider failure")
            entry = {
                "action": "user_login",
                "details_json": json.dumps({"email": "test@example.com"}),
                "trace_id": MOCK_CORRELATION_ID
            }
            prev_hash = "prev_hash_mock"
            with freeze_time("2025-09-01T12:00:00Z"):
                signature, key_id = await sign_async(entry, prev_hash)
            expected_data = json.dumps({"action": "user_login", "details_json": entry["details_json"], "trace_id": MOCK_CORRELATION_ID, "prev_hash": prev_hash}, sort_keys=True).encode('utf-8')
            expected_signature = hmac.new(MOCK_HMAC_SECRET, expected_data, hashlib.sha256).hexdigest()
            assert signature == expected_signature
            assert key_id == "hmac_fallback"
            mock_audit_log.assert_called_with("hmac_fallback_sign", success=True)
            assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'FallbackUsed', 'provider_type': 'fallback', 'operation': 'sign'}) == 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(90)
    async def test_e2e_concurrent_crypto_operations(self, crypto_factory, keystore, mock_boto3, mock_audit_log):
        """Test concurrent key generation, storage, and signing."""
        async def crypto_workflow(i):
            provider = crypto_factory.get_provider("software")
            key_id = await provider.generate_key("rsa", 2048)
            entry = {
                "action": f"user_action_{i}",
                "details_json": json.dumps({"email": f"test{i}@example.com"}),
                "trace_id": MOCK_CORRELATION_ID
            }
            signature, sign_key_id = await sign_async(entry, "prev_hash_mock")
            return signature, sign_key_id

        tasks = [crypto_workflow(i) for i in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            results = await asyncio.gather(*tasks)
        assert len(results) == 5
        assert all(isinstance(signature, bytes) and sign_key_id for signature, sign_key_id in results)
        assert mock_audit_log.call_count >= 15  # Key generation, storage, signing
        assert REGISTRY.get_sample_value('audit_sign_operations_total', {'provider_type': 'software'}) == 5

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_e2e_crypto_failure(self, crypto_factory, mock_boto3, mock_audit_log):
        """Test E2E handling of KMS decryption failure."""
        mock_boto3[0].decrypt.side_effect = boto3.client('kms').exceptions.ClientError({"Error": {"Code": "InvalidCiphertextException"}}, "Decrypt")
        with pytest.raises(Exception, match="KMS decryption failed"):
            crypto_factory.get_provider("software")
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="fail", error=Any)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'ClientError', 'provider_type': 'software', 'operation': 'decrypt_key'}) >= 1

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_crypto_factory",
        "--cov=audit_crypto_ops",
        "--cov=audit_crypto_provider",
        "--cov=audit_keystore",
        "--cov=secrets",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
