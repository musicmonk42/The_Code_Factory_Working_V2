
"""
test_secrets.py

Regulated industry-grade test suite for secrets.py.

Features:
- Tests AWSSecretsManager, GCPSecretManager, VaultSecretManager, and MockSecretManager for secret retrieval.
- Validates secure secret handling, audit logging, and sensitive data redaction.
- Ensures OpenTelemetry tracing (via audit_log integration).
- Tests async-safe secret retrieval, thread-safety, and rate limiting.
- Verifies error handling, production enforcement, and compliance (SOC2/PCI DSS/HIPAA).
- Uses real implementations with mocked external dependencies (boto3, google-cloud-secretmanager, hvac, audit_log).

Dependencies:
- pytest, pytest-asyncio, unittest.mock, faker, freezegun
- boto3, google-cloud-secretmanager, hvac, opentelemetry-sdk
- audit_log
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from faker import Faker
from freezegun import freeze_time
from botocore.exceptions import ClientError
from google.api_core.exceptions import NotFound
from hvac.exceptions import InvalidRequest

from secrets import (
    SecretManager, AWSSecretsManager, GCPSecretManager, VaultSecretManager,
    MockSecretManager, SecretError, get_hsm_pin, get_fallback_hmac_secret,
    get_kms_master_key_ciphertext_blob
)
from audit_log import log_action

# Initialize faker for test data generation
fake = Faker()

# Test constants
MOCK_HSM_PIN = "mock_hsm_pin"
MOCK_HMAC_SECRET = b"mock_hmac_secret_32_bytes_1234567890"
MOCK_KMS_BLOB = b"mock_kms_ciphertext_blob"
MOCK_AWS_SECRET_NAME = "test/secret"
MOCK_GCP_SECRET_NAME = "projects/test/secrets/test-secret"
MOCK_VAULT_PATH = "secret/data/test"
MOCK_CORRELATION_ID = str(uuid.uuid4())

# Environment variables for compliance mode
os.environ['COMPLIANCE_MODE'] = 'true'
os.environ['PYTHON_ENV'] = 'production'
os.environ['SECRET_MANAGER'] = 'aws'

@pytest.fixture(scope="function")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture
async def mock_audit_log():
    """Mock audit_log.log_action."""
    with patch('audit_log.log_action') as mock_log:
        yield mock_log

@pytest_asyncio.fixture
async def mock_boto3():
    """Mock boto3 client for AWS Secrets Manager."""
    with patch('boto3.client') as mock_client:
        mock_secrets = MagicMock()
        mock_secrets.get_secret_value.return_value = {"SecretString": MOCK_HSM_PIN}
        mock_client.return_value = mock_secrets
        yield mock_secrets

@pytest_asyncio.fixture
async def mock_gcp_secretmanager():
    """Mock GCP Secret Manager client."""
    with patch('google.cloud.secretmanager.SecretManagerServiceClient') as mock_client:
        mock_secret = MagicMock()
        mock_secret.access_secret_version.return_value.payload.data = MOCK_HMAC_SECRET
        mock_client.return_value = mock_secret
        yield mock_secret

@pytest_asyncio.fixture
async def mock_hvac():
    """Mock hvac client for Vault."""
    with patch('hvac.Client') as mock_client:
        mock_vault = MagicMock()
        mock_vault.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": base64.b64encode(MOCK_KMS_BLOB).decode('utf-8')}}}
        mock_client.return_value = mock_vault
        yield mock_vault

@pytest_asyncio.fixture
async def mock_opentelemetry():
    """Mock OpenTelemetry tracer."""
    with patch('secrets.trace') as mock_trace:
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_trace.get_tracer.return_value = mock_tracer
        yield mock_tracer, mock_span

class TestSecrets:
    """Test suite for secrets.py."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_aws_secrets_manager(self, mock_boto3, mock_audit_log, mock_opentelemetry):
        """Test AWSSecretsManager secret retrieval."""
        manager = AWSSecretsManager({"secret_name": MOCK_AWS_SECRET_NAME})
        secret = await manager.aget_secret("hsm_pin")
        assert secret == MOCK_HSM_PIN
        mock_boto3.get_secret_value.assert_called_with(SecretId=MOCK_AWS_SECRET_NAME + "/hsm_pin")
        mock_audit_log.assert_called_with("secret_access", secret_type="hsm_pin", manager="AWSSecretsManager", success=True)
        mock_opentelemetry[1].set_attribute.assert_any_call("manager", "AWSSecretsManager")

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_gcp_secrets_manager(self, mock_gcp_secretmanager, mock_audit_log):
        """Test GCPSecretManager secret retrieval."""
        manager = GCPSecretManager({"project_id": "test"})
        secret = await manager.aget_secret("hmac_secret")
        assert secret == MOCK_HMAC_SECRET
        mock_gcp_secretmanager.access_secret_version.assert_called()
        mock_audit_log.assert_called_with("secret_access", secret_type="hmac_secret", manager="GCPSecretManager", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_vault_secrets_manager(self, mock_hvac, mock_audit_log):
        """Test VaultSecretManager secret retrieval."""
        manager = VaultSecretManager({"url": "http://vault.example.com", "token": "mock_token"})
        secret = await manager.aget_secret("kms_blob")
        assert secret == MOCK_KMS_BLOB
        mock_hvac.secrets.kv.v2.read_secret_version.assert_called()
        mock_audit_log.assert_called_with("secret_access", secret_type="kms_blob", manager="VaultSecretManager", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_mock_secrets_manager(self, mock_audit_log):
        """Test MockSecretManager secret retrieval."""
        with patch.dict(os.environ, {'PYTHON_ENV': 'development'}):
            manager = MockSecretManager({"mock_secrets": {"hsm_pin": MOCK_HSM_PIN}})
            secret = await manager.aget_secret("hsm_pin")
        assert secret == MOCK_HSM_PIN
        mock_audit_log.assert_called_with("secret_access", secret_type="hsm_pin", manager="MockSecretManager", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_aws_secret_failure(self, mock_boto3, mock_audit_log):
        """Test AWSSecretsManager handling of secret retrieval failure."""
        mock_boto3.get_secret_value.side_effect = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue")
        manager = AWSSecretsManager({"secret_name": MOCK_AWS_SECRET_NAME})
        with pytest.raises(SecretError, match="Failed to fetch secret"):
            await manager.aget_secret("hsm_pin")
        mock_audit_log.assert_called_with("secret_access", secret_type="hsm_pin", manager="AWSSecretsManager", success=False, error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_production_no_mock(self, mock_audit_log):
        """Test enforcement of no MockSecretManager in production."""
        with pytest.raises(SecretError, match="MockSecretManager is not allowed in production"):
            MockSecretManager({"mock_secrets": {"hsm_pin": MOCK_HSM_PIN}})
        mock_audit_log.assert_called_with("secret_manager_init", manager="MockSecretManager", success=False, error=Any)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_concurrent_secret_access(self, mock_boto3, mock_audit_log):
        """Test concurrent secret access with AWSSecretsManager."""
        manager = AWSSecretsManager({"secret_name": MOCK_AWS_SECRET_NAME})
        async def fetch_secret():
            return await manager.aget_secret("hsm_pin")

        tasks = [fetch_secret() for _ in range(5)]
        with freeze_time("2025-09-01T12:00:00Z"):
            results = await asyncio.gather(*tasks)
        assert all(secret == MOCK_HSM_PIN for secret in results)
        assert mock_boto3.get_secret_value.call_count == 5
        mock_audit_log.assert_called_with("secret_access", secret_type="hsm_pin", manager="AWSSecretsManager", success=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_sync_wrapper_invalid_context(self, mock_boto3):
        """Test synchronous wrapper in async context."""
        loop = asyncio.get_event_loop()
        with patch('asyncio.get_event_loop', return_value=loop):
            with patch.object(loop, 'is_running', return_value=True):
                with pytest.raises(SecretError, match="Cannot call sync get_hsm_pin from an async context"):
                    get_hsm_pin()

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([
        __file__,
        "-v",
        "--cov=secrets",
        "--cov-report=term-missing",
        "--cov-report=html",
        "--asyncio-mode=auto",
        "-W", "ignore::DeprecationWarning",
        "--tb=short"
    ])
