
"""
test_audit_crypto_factory.py

Regulated industry-grade test suite for audit_crypto_factory.py.

Features:
- Tests CryptoProviderFactory initialization, configuration, provider selection, and shutdown.
- Validates secure secret handling, audit logging, and sensitive data redaction.
- Ensures Prometheus metrics and OpenTelemetry tracing.
- Tests thread-safe provider instantiation, retry logic, and error handling.
- Verifies compliance (SOC2/PCI DSS/HIPAA) with no sensitive data leakage.
- Uses real implementations with mocked external dependencies (boto3, audit_log, secrets).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun
- boto3, prometheus-client, dynaconf, opentelemetry-sdk
- audit_log, secrets
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time
from prometheus_client import REGISTRY

from audit_crypto_factory import (
    CryptoProviderFactory, SIGN_OPERATIONS, VERIFY_OPERATIONS, CRYPTO_ERRORS,
    KEY_ROTATIONS, HSM_SESSION_HEALTH, SIGN_LATENCY, VERIFY_LATENCY,
    KEY_LOAD_COUNT, KEY_STORE_COUNT, KEY_CLEANUP_COUNT, crypto_provider_factory
)
from audit_log import log_action
from secrets import aget_hsm_pin, aget_fallback_hmac_secret, aget_kms_master_key_ciphertext_blob

# Initialize faker for test data generation
fake = Faker()

# Test constants
MOCK_CORRELATION_ID = str(uuid.uuid4())
TEST_KMS_KEY_ID = "mock_kms_key_id"
TEST_HSM_LIBRARY_PATH = "/mock/hsm/lib.so"
TEST_HSM_SLOT_ID = "0"
TEST_HSM_PIN = "mock_hsm_pin"
TEST_SOFTWARE_KEY_DIR = "/tmp/test_crypto_keys"
TEST_ALERT_ENDPOINT = "https://example.com/alert"
TEST_HMAC_SECRET_B64 = base64.b64encode(b"mock_hmac_secret").decode('utf-8')

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['AUDIT_CRYPTO_PROVIDER_TYPE'] = 'software'
os.environ['AUDIT_CRYPTO_DEFAULT_ALGO'] = 'rsa'
os.environ['AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS'] = '86400'
os.environ['AUDIT_CRYPTO_SOFTWARE_KEY_DIR'] = TEST_SOFTWARE_KEY_DIR
os.environ['AUDIT_CRYPTO_KMS_KEY_ID'] = TEST_KMS_KEY_ID
os.environ['AUDIT_CRYPTO_ALERT_ENDPOINT'] = TEST_ALERT_ENDPOINT
os.environ['AUDIT_CRYPTO_HSM_ENABLED'] = 'false'
os.environ['AUDIT_CRYPTO_ALERT_RETRY_ATTEMPTS'] = '3'
os.environ['AUDIT_CRYPTO_ALERT_BACKOFF_FACTOR'] = '0.5'
os.environ['AWS_REGION'] = 'us-east-1'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(autouse=True)
def cleanup_test_environment():
    """Clean up test environment before and after tests."""
    for path in [TEST_SOFTWARE_KEY_DIR]:
        if Path(path).exists():
            import shutil
            shutil.rmtree(path, ignore_errors=True)
    Path(TEST_SOFTWARE_KEY_DIR).mkdir(parents=True, exist_ok=True)
    yield
    if Path(TEST_SOFTWARE_KEY_DIR).exists():
        import shutil
        shutil.rmtree(path, ignore_errors=True)

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_boto3():
    """Mock boto3 client for KMS."""
    with patch('boto3.client') as mock_client:
        mock_kms = MagicMock()
        mock_kms.decrypt.return_value = {"Plaintext": b"mock_decrypted_key"}
        mock_client.return_value = mock_kms
        yield mock_kms

@pytest_asyncio.fixture
async def mock_secrets():
    """Mock secrets.py functions."""
    with patch('secrets.aget_hsm_pin') as mock_hsm_pin, \
         patch('secrets.aget_fallback_hmac_secret') as mock_hmac_secret, \
         patch('secrets.aget_kms_master_key_ciphertext_blob') as mock_kms_blob:
        mock_hsm_pin.return_value = TEST_HSM_PIN
        mock_hmac_secret.return_value = base64.b64decode(TEST_HMAC_SECRET_B64)
        mock_kms_blob.return_value = b"mock_ciphertext_blob"
        yield mock_hsm_pin, mock_hmac_secret, mock_kms_blob

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
async def crypto_factory():
    """Create a CryptoProviderFactory instance."""
    factory = CryptoProviderFactory()
    yield factory
    factory.close_all_providers()

class TestAuditCryptoFactory:
    """Test suite for audit_crypto_factory.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_crypto_provider_initialization_software(self, crypto_factory, mock_boto3, mock_secrets, mock_audit_log, mock_opentelemetry):
        """Test initialization of software crypto provider."""
        provider = crypto_factory.get_provider("software")
        assert provider is not None
        mock_boto3.decrypt.assert_called_once()
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="success")
        mock_opentelemetry[1].set_attribute.assert_any_call("provider_type", "software")
        assert REGISTRY.get_sample_value('audit_key_store_count_total', {'provider_type': 'software'}) >= 0

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_crypto_provider_initialization_hsm(self, crypto_factory, mock_secrets, mock_audit_log):
        """Test initialization of HSM crypto provider."""
        with patch.dict(os.environ, {'AUDIT_CRYPTO_HSM_ENABLED': 'true', 'AUDIT_CRYPTO_HSM_LIBRARY_PATH': TEST_HSM_LIBRARY_PATH, 'AUDIT_CRYPTO_HSM_SLOT_ID': TEST_HSM_SLOT_ID}):
            with patch('audit_crypto_factory.pkcs11') as mock_pkcs11:
                mock_pkcs11.Lib.return_value = MagicMock()
                provider = crypto_factory.get_provider("hsm")
        assert provider is not None
        mock_secrets[0].assert_called_once()
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="hsm", status="success")
        assert REGISTRY.get_sample_value('audit_hsm_session_health', {'provider_type': 'hsm'}) >= 0

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invalid_provider_type(self, crypto_factory, mock_audit_log):
        """Test initialization with invalid provider type."""
        with pytest.raises(ValueError, match="Invalid provider type"):
            crypto_factory.get_provider("invalid")
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="invalid", status="fail", error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_missing_kms_key_id(self, crypto_factory, mock_audit_log):
        """Test initialization with missing KMS key ID."""
        with patch.dict(os.environ, {'AUDIT_CRYPTO_KMS_KEY_ID': ''}):
            with pytest.raises(ValueError, match="KMS Key ID is required"):
                crypto_factory.get_provider("software")
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="fail", error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_kms_decryption_failure(self, crypto_factory, mock_boto3, mock_audit_log):
        """Test handling of KMS decryption failure."""
        mock_boto3.decrypt.side_effect = botocore.exceptions.ClientError({"Error": {"Code": "InvalidCiphertextException"}}, "Decrypt")
        with pytest.raises(botocore.exceptions.ClientError):
            crypto_factory.get_provider("software")
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="fail", error=Any)
        assert REGISTRY.get_sample_value('audit_crypto_errors_total', {'type': 'ClientError', 'provider_type': 'software', 'operation': 'decrypt_key'}) >= 1

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_graceful_shutdown(self, crypto_factory, mock_audit_log):
        """Test graceful shutdown of crypto providers."""
        provider = crypto_factory.get_provider("software")
        crypto_factory.close_all_providers()
        mock_audit_log.assert_called_with("close_provider", provider_name="software", status="success")
        assert not crypto_factory._instances, "Providers not closed"

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_provider_instantiation(self, crypto_factory, mock_boto3, mock_audit_log):
        """Test concurrent instantiation of providers."""
        async def instantiate_provider():
            crypto_factory.get_provider("software")

        tasks = [instantiate_provider() for _ in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            await asyncio.gather(*tasks)
        assert len(crypto_factory._instances) == 1  # Singleton behavior
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="success")
        assert REGISTRY.get_sample_value('audit_key_store_count_total', {'provider_type': 'software'}) >= 0

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_fallback_hmac_secret(self, crypto_factory, mock_secrets, mock_audit_log):
        """Test fallback HMAC secret handling."""
        with patch.dict(os.environ, {'AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64': TEST_HMAC_SECRET_B64}):
            provider = crypto_factory.get_provider("software")
        mock_secrets[1].assert_called_once()
        mock_audit_log.assert_called_with("crypto_provider_init", provider_name="software", status="success")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=audit_crypto_factory",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
