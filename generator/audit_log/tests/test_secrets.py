# test_secrets.py
"""
Comprehensive test suite for generator.audit_log.audit_crypto.secrets.py
This suite mocks all external SDKs (boto3, hvac, gcp) and tests all
secret manager implementations, retry logic, rate limiting, and
production guardrails.
"""

import asyncio
import base64
import importlib
import os
import sys  # <-- ADDED for sys.modules pop
from collections import defaultdict  # <-- ADDED
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# --- Test Bootstrap ---
# Set default environment variables *before* any project imports
# We set a DEV mode so the production guardrail doesn't fire on import
os.environ["AUDIT_LOG_DEV_MODE"] = "true"
os.environ["PYTHON_ENV"] = "development"
# Note: Prometheus mocking is handled by conftest.py, no need to patch here


# --- Module Imports ---
# We can now safely import the modules to be tested and mocked
from generator.audit_log.audit_crypto import secrets
from generator.audit_log.audit_crypto.secrets import (
    AWSSecretsManager,
    DummySecretManager,
    GCPSecretManager,
    SecretAccessRateLimitExceeded,
    SecretDecodingError,
    SecretError,
    SecretManager,
    SecretManagerConfigurationError,
    SecretNotFoundError,
    VaultSecretManager,
    _get_secret_with_retries_and_rate_limit,
    aget_fallback_hmac_secret,
    aget_hsm_pin,
    aget_kms_master_key_ciphertext_blob,
    get_fallback_hmac_secret,
    get_hsm_pin,
    get_kms_master_key_ciphertext_blob,
)

# --- Pytest Fixtures ---


@pytest.fixture(autouse=True)
def mock_log_action(mocker):
    """Auto-mock the log_action import in the secrets module."""
    mocker.patch(
        "generator.audit_log.audit_crypto.secrets.log_action", new_callable=AsyncMock
    )


@pytest.fixture
def mock_boto3(mocker):
    """Mocks boto3 client and exceptions."""
    mocker.patch.object(secrets, "HAS_BOTO3", True)
    mock_client = MagicMock()
    mocker.patch("boto3.client", return_value=mock_client)

    # Mock botocore exceptions
    class MockClientError(Exception):
        def __init__(self, response, operation_name):
            self.response = response
            super().__init__(
                f"An error occurred ({response.get('Error', {}).get('Code')})"
            )

    mocker.patch.object(secrets, "ClientError", MockClientError)
    mocker.patch.object(secrets, "BotoCoreError", Exception)
    return mock_client


@pytest.fixture
def mock_gcp(mocker):
    """Mocks GCP SecretManagerServiceClient."""
    mocker.patch.object(secrets, "HAS_GCP_SECRET_MANAGER", True)
    mock_client = MagicMock()
    mocker.patch(
        "google.cloud.secretmanager.SecretManagerServiceClient",
        return_value=mock_client,
    )

    # Mock GCP exceptions
    class MockNotFound(Exception):
        pass

    class MockGoogleAPIError(Exception):
        pass

    mocker.patch.object(secrets, "NotFound", MockNotFound)
    mocker.patch.object(secrets, "GoogleAPIError", MockGoogleAPIError)
    return mock_client


@pytest.fixture
def mock_hvac(mocker):
    """Mocks hvac Client."""
    mocker.patch.object(secrets, "HAS_HVAC", True)
    mock_client = MagicMock()
    mocker.patch("hvac.Client", return_value=mock_client)

    # Mock hvac exceptions
    class MockInvalidRequest(Exception):
        pass

    class MockForbidden(Exception):
        pass

    mocker.patch.object(secrets, "InvalidRequest", MockInvalidRequest)
    mocker.patch.object(secrets, "Forbidden", MockForbidden)
    return mock_client


@pytest.fixture
def reload_secrets_module(mocker):
    """Fixture to force a reload of the secrets module for config testing."""
    module_name = "generator.audit_log.audit_crypto.secrets"

    # --- FIX: Store original env to restore later ---
    original_env = os.environ.copy()

    def _reload():
        # --- FIX: Force-remove the module from cache so it *must* be re-imported ---
        if module_name in sys.modules:
            sys.modules.pop(module_name)

        # --- FIX: DO NOT call patch.stopall() here ---
        # It would undo the mocker.patch.dict(os.environ, ...) from the test

        # Re-import the module. This will re-run all import-time logic.
        reloaded_secrets = importlib.import_module(module_name)
        return reloaded_secrets

    yield _reload

    # --- FIX: Add a cleanup step to restore the original module ---
    # Cleanup: restore original env and reload one last time
    os.environ.clear()
    os.environ.update(original_env)

    if module_name in sys.modules:
        sys.modules.pop(module_name)

    # Stop any patches that might be lingering from the test
    patch.stopall()
    # Re-apply the global patches needed for a clean import
    patch("prometheus_client.Counter", MagicMock()).start()
    patch("prometheus_client.Gauge", MagicMock()).start()
    patch("prometheus_client.Histogram", MagicMock()).start()

    importlib.import_module(module_name)


# --- Test Classes ---


# --- FIX: Removed @pytest.mark.asyncio from sync test class ---
class TestSecretManagerConfiguration:
    """Tests the import-time configuration and production guardrails."""

    def test_selects_dummy_by_default(self, reload_secrets_module):
        # Force reload with default env
        reloaded_secrets = reload_secrets_module()
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.DummySecretManager
        )

    def test_selects_aws(self, mocker, reload_secrets_module, mock_boto3):
        mocker.patch.dict(
            os.environ, {"USE_AWS_SECRETS": "true", "AWS_REGION": "us-test-1"}
        )
        reloaded_secrets = reload_secrets_module()
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.AWSSecretsManager
        )

    def test_selects_gcp(self, mocker, reload_secrets_module, mock_gcp):
        mocker.patch.dict(
            os.environ, {"USE_GCP_SECRETS": "true", "GCP_PROJECT_ID": "test-project"}
        )
        reloaded_secrets = reload_secrets_module()
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.GCPSecretManager
        )

    def test_selects_vault(self, mocker, reload_secrets_module, mock_hvac):
        mocker.patch.dict(
            os.environ,
            {
                "USE_HASHICORP_VAULT": "true",
                "VAULT_ADDR": "http://test",
                "VAULT_TOKEN": "tok",
            },
        )
        reloaded_secrets = reload_secrets_module()
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.VaultSecretManager
        )

    def test_production_guardrail_fails(self, mocker, reload_secrets_module):
        mocker.patch.dict(
            os.environ, {"PYTHON_ENV": "production", "AUDIT_LOG_DEV_MODE": "false"}
        )

        # --- FIX: Must catch by string match. ---
        # The reloaded module raises a *new* InsecureSecretManagerError class,
        # which won't match the one imported by the test.
        with pytest.raises(
            Exception,
            match="CRITICAL: No production-ready secret manager configured for production environment",
        ):
            reload_secrets_module()  # <-- Call *inside* the raises block

    def test_production_guardrail_bypassed(self, mocker, reload_secrets_module):
        mocker.patch.dict(
            os.environ,
            {
                "PYTHON_ENV": "production",
                "AUDIT_DEV_MODE_ALLOW_INSECURE_SECRETS": "true",
            },
        )
        reloaded_secrets = reload_secrets_module()
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.DummySecretManager
        )

    def test_production_guardrail_passes_with_aws(
        self, mocker, reload_secrets_module, mock_boto3
    ):
        mocker.patch.dict(
            os.environ,
            {
                "PYTHON_ENV": "production",
                "AUDIT_LOG_DEV_MODE": "false",
                "USE_AWS_SECRETS": "true",
                "AWS_REGION": "us-test-1",
            },
        )
        reloaded_secrets = reload_secrets_module()
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.AWSSecretsManager
        )

    def test_aws_init_fails_gracefully(self, mocker, reload_secrets_module):
        # --- FIX: Patch sys.modules to simulate boto3 NOT being installed ---
        # This forces the `except ImportError` block in secrets.py to run
        mocker.patch.dict(sys.modules, {"boto3": None})
        mocker.patch.dict(os.environ, {"USE_AWS_SECRETS": "true"})
        reloaded_secrets = reload_secrets_module()

        # Falls back to Dummy
        # --- FIX: Assert against the reloaded module's class ---
        assert isinstance(
            reloaded_secrets._secret_manager, reloaded_secrets.DummySecretManager
        )


# --- FIX: Removed @pytest.mark.asyncio from class ---
class TestAWSSecretsManager:

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_init_fails_without_boto3(self, mocker):
        mocker.patch.object(secrets, "HAS_BOTO3", False)
        with pytest.raises(
            SecretManagerConfigurationError, match="boto3 library not found"
        ):
            AWSSecretsManager()

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_string(self, mock_boto3):
        mock_boto3.get_secret_value.return_value = {"SecretString": "test_secret"}
        manager = AWSSecretsManager()
        secret = await manager.get_secret("test_id")
        assert secret == b"test_secret"
        mock_boto3.get_secret_value.assert_called_with(SecretId="test_id")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_binary(self, mock_boto3):
        mock_boto3.get_secret_value.return_value = {"SecretBinary": b"binary_secret"}
        manager = AWSSecretsManager()
        secret = await manager.get_secret("test_id")
        assert secret == b"binary_secret"

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_not_found(self, mock_boto3):
        mock_boto3.get_secret_value.side_effect = secrets.ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
        )
        manager = AWSSecretsManager()
        with pytest.raises(SecretNotFoundError):
            await manager.get_secret("not_found")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_other_client_error(self, mock_boto3):
        mock_boto3.get_secret_value.side_effect = secrets.ClientError(
            {"Error": {"Code": "AccessDenied"}}, "GetSecretValue"
        )
        manager = AWSSecretsManager()
        with pytest.raises(SecretError, match="AWS Secrets Manager client error"):
            await manager.get_secret("denied")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_empty(self, mock_boto3):
        mock_boto3.get_secret_value.return_value = {}
        manager = AWSSecretsManager()
        secret = await manager.get_secret("empty")
        assert secret is None

    # --- FIX: Added mock_boto3 fixture to test ---
    def test_is_production_ready(self, mock_boto3):
        assert AWSSecretsManager().is_production_ready is True


# --- FIX: Removed @pytest.mark.asyncio from class ---
class TestGCPSecretManager:

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_init_fails_without_gcp_sdk(self, mocker):
        mocker.patch.object(secrets, "HAS_GCP_SECRET_MANAGER", False)
        with pytest.raises(
            SecretManagerConfigurationError,
            match="google-cloud-secret-manager library not found",
        ):
            GCPSecretManager(project_id="test")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_init_fails_without_project_id(self, mocker):
        mocker.patch.object(secrets, "HAS_GCP_SECRET_MANAGER", True)
        with pytest.raises(
            SecretManagerConfigurationError, match="project_id must be provided"
        ):
            GCPSecretManager()

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_success(self, mock_gcp):
        mock_response = MagicMock()
        mock_response.payload.data = b"gcp_secret"
        mock_gcp.access_secret_version.return_value = mock_response

        manager = GCPSecretManager(project_id="test-project")
        secret = await manager.get_secret("test_secret")

        assert secret == b"gcp_secret"
        mock_gcp.access_secret_version.assert_called_with(
            request={
                "name": "projects/test-project/secrets/test_secret/versions/latest"
            }
        )

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_not_found(self, mock_gcp):
        mock_gcp.access_secret_version.side_effect = secrets.NotFound("not found")
        manager = GCPSecretManager(project_id="test-project")
        with pytest.raises(SecretNotFoundError):
            await manager.get_secret("not_found")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_api_error(self, mock_gcp):
        mock_gcp.access_secret_version.side_effect = secrets.GoogleAPIError("api error")
        manager = GCPSecretManager(project_id="test-project")
        with pytest.raises(SecretError, match="GCP Secret Manager API error"):
            await manager.get_secret("api_error")

    # --- FIX: Added mock_gcp fixture to test ---
    def test_is_production_ready(self, mock_gcp):
        assert GCPSecretManager(project_id="test").is_production_ready is True


# --- FIX: Removed @pytest.mark.asyncio from class ---
class TestVaultSecretManager:

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_init_fails_without_hvac(self, mocker):
        mocker.patch.object(secrets, "HAS_HVAC", False)
        with pytest.raises(
            SecretManagerConfigurationError, match="hvac library not found"
        ):
            VaultSecretManager(url="http://test", token="tok")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_success(self, mock_hvac):
        mock_hvac.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "vault_secret"}}
        }
        manager = VaultSecretManager(url="http://test", token="tok")
        secret = await manager.get_secret("kv/test_secret")
        assert secret == b"vault_secret"

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_not_found(self, mock_hvac):
        mock_hvac.secrets.kv.v2.read_secret_version.side_effect = (
            secrets.InvalidRequest("not found")
        )
        manager = VaultSecretManager(url="http://test", token="tok")
        with pytest.raises(SecretNotFoundError):
            await manager.get_secret("not_found")

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_forbidden(self, mock_hvac):
        mock_hvac.secrets.kv.v2.read_secret_version.side_effect = secrets.Forbidden(
            "access denied"
        )
        manager = VaultSecretManager(url="http://test", token="tok")
        with pytest.raises(SecretError, match="Vault permission denied"):
            await manager.get_secret("denied")

    # --- FIX: Removed @pytest.mark.asyncio from sync test ---
    def test_is_production_ready(self):
        assert (
            VaultSecretManager(url="http://test", token="tok").is_production_ready
            is True
        )


# --- FIX: Removed @pytest.mark.asyncio from class ---
class TestDummySecretManager:

    # --- FIX: Added @pytest.mark.asyncio to async test ---
    @pytest.mark.asyncio
    async def test_get_secret_always_raises(self):
        manager = DummySecretManager()
        with pytest.raises(SecretNotFoundError):
            await manager.get_secret("anything")

    # --- FIX: Removed @pytest.mark.asyncio from sync test ---
    def test_is_not_production_ready(self):
        manager = DummySecretManager()
        assert manager.is_production_ready is False


@pytest.mark.asyncio
class TestGetSecretWithRetries:

    @pytest.fixture(autouse=True)
    def setup_mock_manager(self, mocker):
        self.mock_manager = AsyncMock(spec=SecretManager)
        mocker.patch.object(secrets, "_secret_manager", self.mock_manager)
        # --- FIX: Use defaultdict from import ---
        mocker.patch.object(
            secrets, "_SECRET_ACCESS_ATTEMPTS", defaultdict(list)
        )  # Clear rate limit cache
        return self.mock_manager

    async def test_success_first_try(self):
        self.mock_manager.get_secret.return_value = b"success"
        result = await _get_secret_with_retries_and_rate_limit(
            "my_secret", max_retries=3
        )
        assert result == b"success"
        self.mock_manager.get_secret.assert_called_once_with("my_secret")

    async def test_retry_and_succeed(self, mocker):
        mocker.patch.object(asyncio, "sleep", AsyncMock())  # Speed up retry
        self.mock_manager.get_secret.side_effect = [SecretError("fail 1"), b"success"]

        result = await _get_secret_with_retries_and_rate_limit(
            "my_secret", max_retries=3
        )

        assert result == b"success"
        assert self.mock_manager.get_secret.call_count == 2
        asyncio.sleep.assert_called_once()  # Called once before the successful retry

    async def test_retry_and_fail(self, mocker):
        mocker.patch.object(asyncio, "sleep", AsyncMock())  # Speed up retry
        self.mock_manager.get_secret.side_effect = [
            SecretError("fail 1"),
            SecretError("fail 2"),
            SecretError("fail 3"),
        ]

        with pytest.raises(SecretError, match="Failed to retrieve secret"):
            await _get_secret_with_retries_and_rate_limit("my_secret", max_retries=3)

        assert self.mock_manager.get_secret.call_count == 3
        assert asyncio.sleep.call_count == 2  # Called after fail 1 and fail 2

    async def test_rate_limit_exceeded(self, mocker):
        mocker.patch.object(secrets, "SECRET_MAX_ATTEMPTS_PER_WINDOW", 5)
        mocker.patch.object(secrets, "SECRET_BURST_LIMIT", 0)
        self.mock_manager.get_secret.return_value = b"success"

        # 5 successful calls
        for _ in range(5):
            await _get_secret_with_retries_and_rate_limit("my_secret", max_retries=1)

        assert self.mock_manager.get_secret.call_count == 5

        # 6th call should fail
        with pytest.raises(SecretAccessRateLimitExceeded):
            await _get_secret_with_retries_and_rate_limit("my_secret", max_retries=1)

        assert self.mock_manager.get_secret.call_count == 5  # Not called the 6th time


@pytest.mark.asyncio
class TestPublicAsyncAPI:

    # --- FIX: Standardize on mocking _secret_manager ---
    @pytest.fixture(autouse=True)
    def setup_mock_manager(self, mocker):
        self.mock_manager = AsyncMock(spec=SecretManager)
        mocker.patch.object(secrets, "_secret_manager", self.mock_manager)
        mocker.patch.object(secrets, "_SECRET_ACCESS_ATTEMPTS", defaultdict(list))
        return self.mock_manager

    # --- aget_hsm_pin ---
    async def test_aget_hsm_pin_success(self):
        self.mock_manager.get_secret.return_value = b"12345"
        pin = await aget_hsm_pin()
        assert pin == "12345"
        self.mock_manager.get_secret.assert_called_with("AUDIT_CRYPTO_HSM_PIN")

    async def test_aget_hsm_pin_not_found(self):
        self.mock_manager.get_secret.return_value = None
        with pytest.raises(ValueError, match="HSM PIN not found or accessible"):
            await aget_hsm_pin()

    async def test_aget_hsm_pin_secret_error(self):
        self.mock_manager.get_secret.side_effect = SecretError("AWS fail")
        with pytest.raises(
            ValueError, match="HSM PIN not found or accessible: .*AWS fail"
        ):
            await aget_hsm_pin()

    # --- aget_fallback_hmac_secret ---
    async def test_aget_fallback_hmac_secret_success(self):
        b64_secret = base64.b64encode(b"a_very_long_secret_key_16_bytes")
        self.mock_manager.get_secret.return_value = b64_secret
        secret = await aget_fallback_hmac_secret()
        assert secret == b"a_very_long_secret_key_16_bytes"
        self.mock_manager.get_secret.assert_called_with(
            "AUDIT_CRYPTO_FALLBACK_HMAC_SECRET_B64"
        )

    async def test_aget_fallback_hmac_secret_not_found(self):
        self.mock_manager.get_secret.return_value = None
        secret = await aget_fallback_hmac_secret()
        assert secret is None

    async def test_aget_fallback_hmac_secret_bad_base64(self):
        self.mock_manager.get_secret.return_value = b"not-base64-at-all"
        with pytest.raises(SecretDecodingError):
            await aget_fallback_hmac_secret()

    async def test_aget_fallback_hmac_secret_too_short(self):
        b64_secret = base64.b64encode(b"short")
        self.mock_manager.get_secret.return_value = b64_secret
        with pytest.raises(SecretDecodingError, match="too short"):
            await aget_fallback_hmac_secret()

    # --- aget_kms_master_key_ciphertext_blob ---
    async def test_aget_kms_master_key_success(self):
        b64_blob = base64.b64encode(b"encrypted-key-blob")
        self.mock_manager.get_secret.return_value = b64_blob
        blob = await aget_kms_master_key_ciphertext_blob()
        assert blob == b"encrypted-key-blob"
        self.mock_manager.get_secret.assert_called_with(
            "AUDIT_CRYPTO_SOFTWARE_KEY_MASTER_ENCRYPTION_KEY_B64"
        )

    async def test_aget_kms_master_key_not_found(self):
        self.mock_manager.get_secret.return_value = None
        with pytest.raises(
            SecretNotFoundError, match="Software key master encryption key not found"
        ):
            await aget_kms_master_key_ciphertext_blob()

    async def test_aget_kms_master_key_bad_base64(self):
        self.mock_manager.get_secret.return_value = b"not-base64-at-all"
        with pytest.raises(SecretDecodingError):
            await aget_kms_master_key_ciphertext_blob()


class TestPublicSyncAPI:

    # --- FIX: Standardize on mocking _secret_manager ---
    @pytest.fixture(autouse=True)
    def setup_mock_manager(self, mocker):
        self.mock_manager = AsyncMock(spec=SecretManager)
        mocker.patch.object(secrets, "_secret_manager", self.mock_manager)
        mocker.patch.object(secrets, "_SECRET_ACCESS_ATTEMPTS", defaultdict(list))
        return self.mock_manager

    def test_get_hsm_pin_sync_success(self):
        self.mock_manager.get_secret.return_value = b"12345"
        pin = get_hsm_pin()
        assert pin == "12345"

    def test_get_fallback_hmac_secret_sync_success(self):
        b64_secret = base64.b64encode(b"a_very_long_secret_key_16_bytes")
        self.mock_manager.get_secret.return_value = b64_secret
        secret = get_fallback_hmac_secret()
        assert secret == b"a_very_long_secret_key_16_bytes"

    def test_get_kms_master_key_sync_success(self):
        b64_blob = base64.b64encode(b"encrypted-key-blob")
        self.mock_manager.get_secret.return_value = b64_blob
        blob = get_kms_master_key_ciphertext_blob()
        assert blob == b"encrypted-key-blob"

    @pytest.mark.asyncio
    async def test_get_hsm_pin_works_in_async_context(self):
        """Test that sync get_hsm_pin now works from async context (fixed deadlock)."""
        self.mock_manager.get_secret.return_value = b"12345"
        # This should now succeed instead of raising an error
        pin = get_hsm_pin()
        assert pin == "12345"

    @pytest.mark.asyncio
    async def test_get_fallback_works_in_async_context(self):
        """Test that sync get_fallback_hmac_secret now works from async context (fixed deadlock)."""
        b64_secret = base64.b64encode(b"a_very_long_secret_key_16_bytes")
        self.mock_manager.get_secret.return_value = b64_secret
        # This should now succeed instead of raising an error
        secret = get_fallback_hmac_secret()
        assert secret == b"a_very_long_secret_key_16_bytes"

    @pytest.mark.asyncio
    async def test_get_kms_works_in_async_context(self):
        """Test that sync get_kms_master_key_ciphertext_blob now works from async context (fixed deadlock)."""
        b64_blob = base64.b64encode(b"encrypted-key-blob")
        self.mock_manager.get_secret.return_value = b64_blob
        # This should now succeed instead of raising an error
        blob = get_kms_master_key_ciphertext_blob()
        assert blob == b"encrypted-key-blob"
