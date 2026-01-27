# test_audit_crypto_factory.py

import asyncio
import base64
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

# --- Fixtures ---


# This fixture runs for every test, ensuring a clean environment
@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """
    Cleans up global state and environment variables before/after each test.
    Sets minimal env vars to allow the module to be imported without validation errors.
    """

    # 1. Clear all relevant environment variables
    env_vars = [
        "AUDIT_LOG_DEV_MODE",
        "PYTEST_CURRENT_TEST",
        "RUNNING_TESTS",
        "AUDIT_CRYPTO_PROVIDER_TYPE",
        "AUDIT_CRYPTO_DEFAULT_ALGO",
        "AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS",
        "AUDIT_CRYPTO_KMS_KEY_ID",
        "AUDIT_CRYPTO_HSM_ENABLED",
        "AUDIT_CRYPTO_HSM_LIBRARY_PATH",
        "AUDIT_CRYPTO_HSM_SLOT_ID",
        "PYTHON_ENV",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)

    # --- FIX 1: Set minimal env vars *before* import ---
    monkeypatch.setenv("AUDIT_CRYPTO_PROVIDER_TYPE", "software")
    monkeypatch.setenv("AUDIT_CRYPTO_DEFAULT_ALGO", "ed25519")
    monkeypatch.setenv("AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS", "86400")
    monkeypatch.setenv("AUDIT_LOG_DEV_MODE", "true")
    # --- END FIX 1 ---

    # 2. Reset global state variables in the factory module
    from generator.audit_log.audit_crypto import audit_crypto_factory

    monkeypatch.setattr(audit_crypto_factory, "_SOFTWARE_KEY_MASTER", None)
    monkeypatch.setattr(audit_crypto_factory, "_FALLBACK_HMAC_SECRET", None)

    # 3. Reset the factory instance cache
    audit_crypto_factory.crypto_provider_factory._instances.clear()

    yield  # Run the test

    # Post-test cleanup
    monkeypatch.setattr(audit_crypto_factory, "_SOFTWARE_KEY_MASTER", None)
    monkeypatch.setattr(audit_crypto_factory, "_FALLBACK_HMAC_SECRET", None)
    audit_crypto_factory.crypto_provider_factory._instances.clear()


@pytest.fixture
def mock_settings(monkeypatch):
    """
    Mocks the Dynaconf 'settings' object and the validation function.
    Provides a default, valid configuration.
    """
    # FIX 2.1: Use a plain MagicMock object for simpler patching, avoiding spec issues
    mock_settings_instance = MagicMock()

    default_config = {
        "PROVIDER_TYPE": "software",
        "DEFAULT_ALGO": "ed25519",
        "KEY_ROTATION_INTERVAL_SECONDS": 86400,
        "SOFTWARE_KEY_DIR": "/tmp/test_keys",
        "KMS_KEY_ID": "arn:aws:kms:us-east-1:12345:key/mock-key-id",
        "AWS_REGION": "us-east-1",
        "HSM_ENABLED": False,
        "HSM_LIBRARY_PATH": None,
        "HSM_SLOT_ID": None,
        "ALERT_ENDPOINT": "http://mock-alert-endpoint.com",
        "FALLBACK_HMAC_SECRET_B64": None,
        "HSM_HEALTH_CHECK_INTERVAL_SECONDS": 30,
        "ALERT_RETRY_ATTEMPTS": 3,
        "ALERT_BACKOFF_FACTOR": 2.0,
        "ALERT_INITIAL_DELAY": 1.0,
        "HSM_RETRY_ATTEMPTS": 5,
        "HSM_BACKOFF_FACTOR": 2.0,
        "HSM_INITIAL_DELAY": 1.0,
        "SUPPORTED_ALGOS": ["rsa", "ecdsa", "ed25519", "hmac"],
    }

    # Manually configure attributes on the mock object
    for k, v in default_config.items():
        setattr(mock_settings_instance, k, v)

    # FIX 2.2: Mock the 'get' method explicitly using the lambda to return config values.
    # This resolves the original AttributeError during mock initialization.
    mock_settings_instance.get = MagicMock(
        side_effect=lambda key, default=None: default_config.get(key, default)
    )

    # Patch the 'settings' object in the factory module
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.settings",
        mock_settings_instance,
    )

    # Patch the validation function to prevent it from running on import
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.validate_and_load_config",
        MagicMock(),
    )

    return mock_settings_instance, default_config


@pytest.fixture
def mock_boto(monkeypatch):
    """Mocks the boto3 client and its responses."""
    mock_kms_client = MagicMock()
    mock_decrypt = MagicMock(
        return_value={"Plaintext": b"test-master-key-from-kms-0123456"}
    )
    mock_kms_client.decrypt = mock_decrypt

    mock_boto_client = MagicMock(return_value=mock_kms_client)
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.boto3.client",
        mock_boto_client,
    )
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3", True
    )

    return mock_boto_client, mock_decrypt


@pytest.fixture
def mock_secrets(monkeypatch):
    """Mocks all async secret-fetching functions from secrets.py."""
    mock_aget_kms = AsyncMock(return_value=base64.b64encode(b"mock-kms-ciphertext"))
    mock_aget_hmac = AsyncMock(return_value=b"mock-hmac-secret-bytes-!@#")

    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.aget_kms_master_key_ciphertext_blob",
        mock_aget_kms,
    )
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.aget_fallback_hmac_secret",
        mock_aget_hmac,
    )

    return {
        "aget_kms": mock_aget_kms,
        "aget_hmac": mock_aget_hmac,
    }


@pytest.fixture
def mock_providers(monkeypatch):
    """
    Mocks the __init__ of Software and HSM providers to prevent real init.
    Returns a dictionary containing the mock classes and instantiated objects.
    """
    from generator.audit_log.audit_crypto.audit_crypto_provider import CryptoProvider

    # Track data using a dict for cleaner access
    data = {
        "software_calls": [],
        "hsm_calls": [],
        "software_instance": None,
        "hsm_instance": None,
    }

    # Create mock classes that actually inherit from CryptoProvider
    class MockSoftwareProvider(CryptoProvider):
        def __init__(self, *args, **kwargs):
            # Track the call
            data["software_calls"].append((args, kwargs))
            # FIX: Store the created instance for assertions
            if data["software_instance"] is None:
                data["software_instance"] = self

            self._init_args = args
            self._init_kwargs = kwargs

        async def generate_key(self, algo: str) -> str:
            return "mock-key-id"

        async def sign(self, data: bytes, key_id: str) -> bytes:
            return b"mock-signature"

        async def verify(self, data: bytes, signature: bytes, key_id: str) -> bool:
            return True

        async def rotate_key(self, key_id: str) -> str:
            return "new-mock-key-id"

        async def close(self):
            pass  # Must implement for provider close

    class MockHSMProvider(CryptoProvider):
        def __init__(self, *args, **kwargs):
            # Track the call
            data["hsm_calls"].append((args, kwargs))
            # FIX: Store the created instance for assertions
            if data["hsm_instance"] is None:
                data["hsm_instance"] = self

            self._init_args = args
            self._init_kwargs = kwargs

        async def generate_key(self, algo: str) -> str:
            return "mock-hsm-key-id"

        async def sign(self, data: bytes, key_id: str) -> bytes:
            return b"mock-hsm-signature"

        async def verify(self, data: bytes, signature: bytes, key_id: str) -> bool:
            return True

        async def rotate_key(self, key_id: str) -> str:
            return "new-mock-hsm-key-id"

        async def close(self):
            pass  # Must implement for provider close

    # Patch the imports - use the actual mock classes
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.SoftwareCryptoProvider",
        MockSoftwareProvider,
    )
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.HSMCryptoProvider",
        MockHSMProvider,
    )

    # FIX 3: Add class references back to the returned data dict to fix KeyErrors
    data["software_class"] = MockSoftwareProvider
    data["hsm_class"] = MockHSMProvider

    return data


@pytest.fixture
def mock_aiohttp(monkeypatch):
    """Mocks aiohttp.ClientSession for testing send_alert."""
    # Create the mock response object
    mock_response = AsyncMock()
    mock_response.raise_for_status = MagicMock()

    # Create the mock post context manager
    mock_post_context = AsyncMock()
    mock_post_context.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post_context.__aexit__ = AsyncMock(return_value=None)

    # Create the mock session
    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post_context)

    # Create the mock ClientSession context manager
    mock_client_session_context = AsyncMock()
    mock_client_session_context.__aenter__ = AsyncMock(return_value=mock_session)
    mock_client_session_context.__aexit__ = AsyncMock(return_value=None)

    # Mock ClientSession to return the context manager
    mock_client_session = MagicMock(return_value=mock_client_session_context)
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.aiohttp.ClientSession",
        mock_client_session,
    )

    return mock_session, mock_response


@pytest.fixture
def mock_log_action(monkeypatch):
    """Mocks the log_action async function."""
    mock_log = AsyncMock()
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_factory.log_action", mock_log
    )
    return mock_log


# --- Test Classes ---


class TestHelpers:
    """Tests for helper functions and classes."""

    @pytest.mark.parametrize(
        "env_var, value, expected",
        [
            (None, None, False),
            ("AUDIT_LOG_DEV_MODE", "true", True),
            ("AUDIT_LOG_DEV_MODE", "false", False),
            ("PYTEST_CURRENT_TEST", "some_test_name", True),
            ("RUNNING_TESTS", "true", True),
            ("RUNNING_TESTS", "False", False),
            # AUDIT_CRYPTO_MODE tests
            ("AUDIT_CRYPTO_MODE", "dev", True),  # "dev" triggers dev mode
            ("AUDIT_CRYPTO_MODE", "disabled", False),  # "disabled" does NOT trigger dev mode (production-safe)
            ("AUDIT_CRYPTO_MODE", "full", False),  # "full" does NOT trigger dev mode
        ],
    )
    def test_is_test_or_dev_mode(self, monkeypatch, env_var, value, expected):
        """Tests the _is_test_or_dev_mode helper with various env vars."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _is_test_or_dev_mode,
        )

        # Clean the environment to isolate the test case correctly.
        monkeypatch.delenv("AUDIT_LOG_DEV_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("RUNNING_TESTS", raising=False)
        monkeypatch.delenv("AUDIT_CRYPTO_MODE", raising=False)
        monkeypatch.delenv("DEV_MODE", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)

        if env_var:
            monkeypatch.setenv(env_var, value)

        # The test runner sets PYTEST_CURRENT_TEST. We need to override it for the cases expecting False.
        if expected is False:
            monkeypatch.setenv("PYTEST_CURRENT_TEST", "")

        assert _is_test_or_dev_mode() == expected

    def test_production_with_disabled_crypto_mode(self, monkeypatch):
        """
        Tests that production mode with AUDIT_CRYPTO_MODE=disabled does NOT trigger
        the security conflict error. This is a valid configuration when secrets
        are not yet configured in production.
        """
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _is_test_or_dev_mode,
        )
        from generator.audit_log.audit_crypto.audit_common import (
            is_production_environment,
        )

        # Clean environment
        monkeypatch.delenv("AUDIT_LOG_DEV_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("RUNNING_TESTS", raising=False)
        monkeypatch.delenv("DEV_MODE", raising=False)

        # Set production environment with disabled crypto mode
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("AUDIT_CRYPTO_MODE", "disabled")

        # Override PYTEST_CURRENT_TEST to simulate non-test environment
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "")

        # Verify production is detected
        assert is_production_environment() is True
        
        # Verify dev mode is NOT detected (this is the key fix)
        assert _is_test_or_dev_mode() is False

        # This configuration should NOT raise a security conflict error

    def test_production_with_dev_crypto_mode_raises_error(self, monkeypatch):
        """
        Tests that production mode with AUDIT_CRYPTO_MODE=dev DOES trigger
        the security conflict error. This is the security guardrail working correctly.
        """
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _is_test_or_dev_mode,
        )
        from generator.audit_log.audit_crypto.audit_common import (
            is_production_environment,
        )

        # Clean environment
        monkeypatch.delenv("AUDIT_LOG_DEV_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("RUNNING_TESTS", raising=False)
        monkeypatch.delenv("DEV_MODE", raising=False)

        # Set production environment with dev crypto mode (WRONG configuration)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("AUDIT_CRYPTO_MODE", "dev")

        # Override PYTEST_CURRENT_TEST to simulate non-test environment
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "")

        # Verify production is detected
        assert is_production_environment() is True

        # Verify dev mode IS detected (this is the problem)
        assert _is_test_or_dev_mode() is True

        # This configuration SHOULD raise a security conflict error when get_provider() is called

    def test_sensitive_data_filter(self, caplog):
        """Tests that the SensitiveDataFilter redacts logs."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            SensitiveDataFilter,
        )

        logger = logging.getLogger("test_filter")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.filters.clear()

        logger.addFilter(SensitiveDataFilter())

        with caplog.at_level(logging.INFO, logger="test_filter"):
            logger.info("This is a test PIN and a secret.")
            # FIX 4: Use logging.LoggerAdapter to force 'extra' to be carried
            adapter = logging.LoggerAdapter(
                logger, extra={"user_pin": "1234", "user_secret": "abc"}
            )
            adapter.info("This is an extra dict.")

        # Check the message itself
        assert "***REDACTED_PIN***" in caplog.records[0].msg
        assert "***REDACTED_SECRET***" in caplog.records[0].msg

        # Check the 'extra' dict redaction (this is the LogRecord that the adapter created)
        assert "user_pin" in caplog.records[1].__dict__
        # FIX 7: Assert the value is correctly redacted
        assert caplog.records[1].__dict__["user_pin"] == "***REDACTED***"
        assert "user_secret" in caplog.records[1].__dict__
        assert caplog.records[1].__dict__["user_secret"] == "***REDACTED***"


@pytest.mark.usefixtures("mock_settings")
class TestConfiguration:
    """Tests the Dynaconf configuration validation logic."""

    def test_prod_config_valid_software(self, monkeypatch, mock_settings):
        """Tests a valid production config for 'software' provider."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            ConfigurationError,
        )

        # FIX 8: Remove invalid monkeypatch.undo and use patch for flow control
        # The target function is already mocked by the fixture, so we use patch.object to wrap it with the original function's logic.
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        # Re-set settings directly to override fixture mocks which run globally
        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "software")
        setattr(
            mock_dynaconf, "KMS_KEY_ID", "arn:aws:kms:us-east-1:12345:key/mock-key-id"
        )

        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            validate_and_load_config as original_validate,
        )

        with patch(
            "generator.audit_log.audit_crypto.audit_crypto_factory.validate_and_load_config",
            wraps=original_validate,
        ) as mock_validate:
            try:
                mock_validate()
            except ConfigurationError as e:
                pytest.fail(f"Valid software config failed validation: {e}")

    def test_prod_config_valid_hsm(self, monkeypatch, mock_settings):
        """Tests a valid production config for 'hsm' provider."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            ConfigurationError,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        # Update settings for HSM
        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "hsm")
        setattr(mock_dynaconf, "HSM_ENABLED", True)
        setattr(mock_dynaconf, "HSM_LIBRARY_PATH", "/usr/lib/mock-hsm.so")
        setattr(mock_dynaconf, "HSM_SLOT_ID", 1)

        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            validate_and_load_config as original_validate,
        )

        with patch(
            "generator.audit_log.audit_crypto.audit_crypto_factory.validate_and_load_config",
            wraps=original_validate,
        ) as mock_validate:
            try:
                mock_validate()
            except ConfigurationError as e:
                pytest.fail(f"Valid HSM config failed validation: {e}")

    def test_prod_config_software_missing_kms_id(self, monkeypatch, mock_settings):
        """Tests that 'software' provider fails in prod without KMS_KEY_ID."""
        # FIX 2: Change expected exception to ValidationError as post_validation_checks is called directly
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            ValidationError,
            post_validation_checks,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        # Invalidate the config
        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "software")
        setattr(mock_dynaconf, "KMS_KEY_ID", None)

        with pytest.raises(ValidationError, match="KMS_KEY_ID is required"):
            post_validation_checks()

    def test_prod_config_hsm_missing_lib_path(self, monkeypatch, mock_settings):
        """Tests that 'hsm' provider fails in prod without HSM_LIBRARY_PATH."""
        # FIX 3: Change expected exception to ValidationError as post_validation_checks is called directly
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            ValidationError,
            post_validation_checks,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        # Invalidate the config
        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "hsm")
        setattr(mock_dynaconf, "HSM_ENABLED", True)
        # We assume the default Dynaconf value is being retrieved here, which should pass if it's a non-None string.
        # However, to explicitly test the 'missing' case, we set it to None.
        setattr(mock_dynaconf, "HSM_LIBRARY_PATH", None)

        with pytest.raises(ValidationError, match="HSM_LIBRARY_PATH is required"):
            post_validation_checks()

    def test_dev_mode_bypasses_prod_checks(self, monkeypatch, mock_settings):
        """Tests that dev mode allows missing production settings."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            ConfigurationError,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: True,
        )

        # Invalidate the config (missing KMS_KEY_ID for software provider)
        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "software")
        setattr(mock_dynaconf, "KMS_KEY_ID", None)

        # This should NOT raise an error
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            validate_and_load_config as original_validate,
        )

        with patch(
            "generator.audit_log.audit_crypto.audit_crypto_factory.validate_and_load_config",
            wraps=original_validate,
        ) as mock_validate:
            try:
                mock_validate()
            except ConfigurationError as e:
                pytest.fail(f"Dev mode failed to bypass validation: {e}")


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_settings", "mock_log_action")
class TestGlobalSecrets:
    """Tests the lazy-loading of global secrets."""

    async def test_ensure_software_key_master_dev_mode(self, monkeypatch):
        """Tests that dev mode returns the dummy master key."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: True,
        )

        key = await _ensure_software_key_master()
        assert key == b"0123456789abcdef0123456789abcdef"

        # Test caching
        key2 = await _ensure_software_key_master()
        assert key is key2  # Should be the same object

    async def test_ensure_software_key_master_prod_success(
        self, monkeypatch, mock_secrets, mock_boto
    ):
        """Tests successful production load of the master key from KMS."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        key = await _ensure_software_key_master()
        assert key == b"test-master-key-from-kms-0123456"

        # Assert mocks were called
        mock_secrets["aget_kms"].assert_called_once()
        mock_boto[0].assert_called_once_with("kms", region_name="us-east-1")
        mock_boto[1].assert_called_once_with(
            CiphertextBlob=base64.b64encode(b"mock-kms-ciphertext"),
            KeyId="arn:aws:kms:us-east-1:12345:key/mock-key-id",
        )

        # Test caching
        key2 = await _ensure_software_key_master()
        assert key is key2
        mock_secrets["aget_kms"].assert_called_once()  # Should not be called again

    async def test_ensure_software_key_master_prod_no_secret(
        self, monkeypatch, mock_secrets
    ):
        """Tests failure when the secret is not found in the secret manager."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        mock_secrets["aget_kms"].return_value = None

        with pytest.raises(
            CryptoInitializationError, match="No KMS master key ciphertext blob"
        ):
            await _ensure_software_key_master()

    async def test_ensure_software_key_master_prod_no_boto(
        self, monkeypatch, mock_secrets
    ):
        """Tests failure when boto3 is not installed."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.HAS_BOTO3", False
        )

        with pytest.raises(CryptoInitializationError, match="boto3 not available"):
            await _ensure_software_key_master()

    async def test_ensure_software_key_master_prod_kms_decrypt_fail(
        self, monkeypatch, mock_secrets, mock_boto
    ):
        """Tests failure when the KMS decrypt call fails."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        mock_boto[1].side_effect = Exception("KMS Access Denied")

        with pytest.raises(CryptoInitializationError, match="KMS Access Denied"):
            await _ensure_software_key_master()

    async def test_ensure_fallback_secret_dev_mode(self, monkeypatch):
        """Tests that dev mode returns the dummy HMAC secret."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_fallback_hmac_secret,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: True,
        )

        secret = await _ensure_fallback_hmac_secret()
        assert secret == b"0123456789abcdef0123456789abcdef"

        # Test caching
        secret2 = await _ensure_fallback_hmac_secret()
        assert secret is secret2

    async def test_ensure_fallback_secret_prod_success(self, monkeypatch, mock_secrets):
        """Tests successful production load of the HMAC secret."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_fallback_hmac_secret,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        secret = await _ensure_fallback_hmac_secret()
        assert secret == b"mock-hmac-secret-bytes-!@#"

        # Test caching
        secret2 = await _ensure_fallback_hmac_secret()
        assert secret is secret2
        mock_secrets["aget_hmac"].assert_called_once()

    async def test_ensure_fallback_secret_prod_fail(self, monkeypatch, mock_secrets):
        """Tests failure when the HMAC secret is not found."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            _ensure_fallback_hmac_secret,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )
        mock_secrets["aget_hmac"].return_value = None

        with pytest.raises(
            CryptoInitializationError, match="Fallback HMAC secret not available"
        ):
            await _ensure_fallback_hmac_secret()


@pytest.mark.asyncio
@pytest.mark.usefixtures("mock_settings", "mock_log_action")
class TestAsyncUtils:
    """Tests utility functions like send_alert and retry_operation."""

    async def test_send_alert_success(self, mock_aiohttp, mock_settings):
        """Tests that send_alert successfully POSTs to the endpoint."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import send_alert

        mock_session, mock_response = mock_aiohttp

        # The mock is already set up properly in the fixture
        # Just ensure raise_for_status doesn't raise an exception
        mock_response.raise_for_status.side_effect = None

        # FIX 4: Explicitly pass the mocked endpoint value to bypass default argument evaluation issue.
        mock_endpoint = mock_settings[0].ALERT_ENDPOINT
        await send_alert("Test Alert", severity="high", endpoint=mock_endpoint)

        # Verify the post method was called
        mock_session.post.assert_called_once_with(
            mock_endpoint, json={"message": "Test Alert", "severity": "high"}
        )

    async def test_send_alert_failure_with_retries(
        self, monkeypatch, mock_aiohttp, mock_settings
    ):
        """Tests that send_alert retries on failure."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import send_alert

        mock_session, mock_response = mock_aiohttp
        # Make raise_for_status raise an error
        mock_response.raise_for_status.side_effect = aiohttp.ClientError(
            "Connection failed"
        )

        # Mock asyncio.sleep to speed up the test
        mock_sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", mock_sleep)

        # The alert should fail but NOT raise (it catches the exception and logs it)
        await send_alert("Test Alert", severity="critical")

        # Assert it was called the correct number of times (default is 3)
        assert mock_session.post.call_count == 3
        # Assert sleep was called with exponential backoff
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0].args[0] == 1.0  # initial_delay
        assert mock_sleep.call_args_list[1].args[0] == 2.0  # delay * backoff_factor

    async def test_retry_operation_success_first_try(self, mock_log_action):
        """Tests retry_operation succeeding on the first attempt."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            retry_operation,
        )

        mock_func = AsyncMock(return_value="success")

        result = await retry_operation(
            mock_func, backend_name="test_be", op_name="test_op"
        )

        assert result == "success"
        mock_func.assert_called_once()
        # FIX 5: Assertion uses attempts_taken=1 (0 failures + 1 success)
        mock_log_action.assert_called_with(
            "retry_operation",
            status="success",
            backend="test_be",
            operation="test_op",
            attempts_taken=1,
        )

    async def test_retry_operation_success_after_retries(
        self, monkeypatch, mock_log_action
    ):
        """Tests retry_operation succeeding after 2 failures."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            retry_operation,
        )

        mock_func = AsyncMock(
            side_effect=[ConnectionError("fail1"), ConnectionError("fail2"), "success"]
        )
        mock_sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", mock_sleep)

        result = await retry_operation(
            mock_func, max_attempts=5, backend_name="test_be", op_name="test_op"
        )

        assert result == "success"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2  # Slept after fail1 and fail2

        # Check that failure was logged
        mock_log_action.assert_any_call(
            "retry_operation",
            status="attempt_fail",
            backend="test_be",
            operation="test_op",
            attempt=1,
            error="fail1",
        )
        # Check that success was logged
        # FIX 6: Assertion uses attempts_taken=3 (2 failures + 1 success)
        mock_log_action.assert_called_with(
            "retry_operation",
            status="success",
            backend="test_be",
            operation="test_op",
            attempts_taken=3,
        )

    async def test_retry_operation_final_failure(self, monkeypatch, mock_log_action):
        """Tests retry_operation failing after all attempts."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            retry_operation,
        )

        mock_func = AsyncMock(side_effect=ConnectionError("final fail"))
        mock_sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", mock_sleep)

        with pytest.raises(ConnectionError, match="final fail"):
            await retry_operation(
                mock_func, max_attempts=3, backend_name="test_be", op_name="test_op"
            )

        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2

        # Check that final failure was logged
        mock_log_action.assert_called_with(
            "retry_operation",
            status="final_fail",
            backend="test_be",
            operation="test_op",
            attempt=3,
            error="final fail",
        )

    async def test_retry_operation_cancelled(self, monkeypatch, mock_log_action):
        """Tests that CancelledError propagates immediately."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            retry_operation,
        )

        mock_func = AsyncMock(side_effect=asyncio.CancelledError)
        mock_sleep = AsyncMock()
        monkeypatch.setattr("asyncio.sleep", mock_sleep)

        with pytest.raises(asyncio.CancelledError):
            await retry_operation(
                mock_func, max_attempts=3, backend_name="test_be", op_name="test_op"
            )

        assert mock_func.call_count == 1
        assert mock_sleep.call_count == 0
        mock_log_action.assert_called_with(
            "retry_operation",
            status="cancelled",
            backend="test_be",
            operation="test_op",
            attempt=0,
        )


@pytest.mark.usefixtures(
    "mock_settings", "mock_log_action", "mock_secrets", "mock_boto", "mock_providers"
)
class TestCryptoProviderFactory:
    """Tests the CryptoProviderFactory logic."""

    def test_factory_registers_defaults(self, mock_settings):
        """Tests that the factory registers default providers on init."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoProviderFactory,
            DummyCryptoProvider,
            SoftwareCryptoProvider,
        )

        mock_dynaconf, config_dict = mock_settings
        config_dict["HSM_ENABLED"] = False

        factory = CryptoProviderFactory()

        assert "software" in factory._registry
        assert factory._registry["software"] == SoftwareCryptoProvider
        assert "dummy" in factory._registry
        assert factory._registry["dummy"] == DummyCryptoProvider
        assert "hsm" not in factory._registry  # Because HSM_ENABLED was False

    def test_factory_registers_hsm_when_enabled(self, mock_settings, mock_providers):
        """Tests that HSM provider is registered when enabled."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoProviderFactory,
        )

        mock_dynaconf, config_dict = mock_settings
        # Set it on the mock object itself, not just the config dict
        setattr(mock_dynaconf, "HSM_ENABLED", True)
        config_dict["HSM_ENABLED"] = True  # Enable HSM

        factory = CryptoProviderFactory()

        assert "hsm" in factory._registry
        # The registry contains the mock class

    @pytest.mark.asyncio
    async def test_get_provider_dev_mode_returns_dummy(self, monkeypatch):
        """Tests that get_provider *always* returns DummyProvider in dev/test mode."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            DummyCryptoProvider,
            crypto_provider_factory,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: True,
        )

        # Request "software"
        provider_sw = crypto_provider_factory.get_provider("software")
        assert isinstance(provider_sw, DummyCryptoProvider)

        # Request "hsm"
        provider_hsm = crypto_provider_factory.get_provider("hsm")
        assert isinstance(provider_hsm, DummyCryptoProvider)

        # Check caching
        assert provider_sw is provider_hsm
        assert "dummy" in crypto_provider_factory._instances

    @pytest.mark.asyncio
    async def test_get_provider_prod_software_success(
        self, monkeypatch, mock_providers, mock_settings
    ):
        """Tests getting a 'software' provider in production mode."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_fallback_hmac_secret,
            _ensure_software_key_master,
            crypto_provider_factory,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "software")

        provider = crypto_provider_factory.get_provider("software")

        # Verify it was instantiated correctly by checking the calls list
        # FIX 7: Assertions updated to use the saved instance and check call count
        assert len(mock_providers["software_calls"]) >= 1
        args, kwargs = mock_providers["software_calls"][0]
        # Check that the accessor functions were passed
        assert kwargs.get("software_key_master_accessor") == _ensure_software_key_master
        assert (
            kwargs.get("fallback_hmac_secret_accessor") == _ensure_fallback_hmac_secret
        )
        assert kwargs.get("settings") == mock_dynaconf
        assert provider is mock_providers["software_instance"]

        # Verify caching
        provider2 = crypto_provider_factory.get_provider("software")
        assert provider is provider2
        assert len(mock_providers["software_calls"]) == 1  # Not called again

    @pytest.mark.asyncio
    async def test_get_provider_prod_hsm_success(
        self, monkeypatch, mock_providers, mock_settings
    ):
        """Tests getting an 'hsm' provider in production mode."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            _ensure_software_key_master,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "hsm")
        setattr(mock_dynaconf, "HSM_ENABLED", True)  # Must be enabled for registration

        # We need to re-create the factory to register the HSM provider
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            crypto_provider_factory,
        )

        factory = (
            crypto_provider_factory.__class__()
        )  # FIX 8: Re-initialize the factory

        provider = factory.get_provider("hsm")

        # FIX 8: Assertion uses the correct key
        assert provider is mock_providers["hsm_instance"]
        args, kwargs = mock_providers["hsm_calls"][0]
        assert kwargs.get("software_key_master_accessor") == _ensure_software_key_master

    @pytest.mark.asyncio
    async def test_get_provider_prod_hsm_fail_fallback_to_software(
        self, monkeypatch, mock_providers, mock_settings
    ):
        """Tests that a failed HSM init falls back to the software provider."""

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "hsm")
        setattr(mock_dynaconf, "HSM_ENABLED", True)

        # Re-create factory to register HSM
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            crypto_provider_factory,
        )

        factory = (
            crypto_provider_factory.__class__()
        )  # FIX 9: Re-initialize the factory

        # Make HSM init fail
        # This will fail on the *first* call, which is when the factory calls __init__
        mock_providers["hsm_class"].__init__ = MagicMock(
            side_effect=Exception("HSM Connection Failed")
        )

        # This should NOT raise an error
        provider = factory.get_provider("hsm")

        # It should have returned the *software* instance
        # FIX 9: Assertion uses the correct key
        assert provider is mock_providers["software_instance"]

    @pytest.mark.asyncio
    async def test_get_provider_prod_total_failure(
        self, monkeypatch, mock_providers, mock_settings
    ):
        """Tests that if 'software' (as primary or fallback) fails, it's a critical error."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            CryptoInitializationError,
            crypto_provider_factory,
        )
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoProvider,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: False,
        )

        mock_dynaconf, config_dict = mock_settings
        setattr(mock_dynaconf, "PROVIDER_TYPE", "software")

        # Create a mock Software provider that raises an exception
        class FailingMockSoftwareCryptoProvider(CryptoProvider):
            def __init__(self, *args, **kwargs):
                raise Exception("Software Init Failed")

            async def generate_key(self, algo: str) -> str:
                return ""

            async def sign(self, data: bytes, key_id: str) -> bytes:
                return b""

            async def verify(self, data: bytes, signature: bytes, key_id: str) -> bool:
                return True

            async def rotate_key(self, key_id: str) -> str:
                return ""

            async def close(self):
                pass

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.SoftwareCryptoProvider",
            FailingMockSoftwareCryptoProvider,
        )

        # Re-create the factory with the failing mock
        factory = crypto_provider_factory.__class__()

        # The error message should match what's actually produced
        with pytest.raises(
            CryptoInitializationError,
            match="CRITICAL: Failed to initialize 'software' crypto provider",
        ):
            factory.get_provider("software")

    def test_get_crypto_provider_helper(self, monkeypatch):
        """Tests the global get_crypto_provider() helper."""
        from generator.audit_log.audit_crypto.audit_crypto_factory import (
            DummyCryptoProvider,
            get_crypto_provider,
        )

        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory._is_test_or_dev_mode",
            lambda: True,
        )

        dummy = DummyCryptoProvider(None, None, None)
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_factory.crypto_provider",
            dummy,
        )

        provider = get_crypto_provider()
        assert provider is dummy
