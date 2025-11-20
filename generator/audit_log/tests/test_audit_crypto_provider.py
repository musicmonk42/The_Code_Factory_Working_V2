import asyncio
import os
import time
import logging # Import logging for the fixture fix
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch, call, ANY

import pytest
import pytest_asyncio
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import rsa, ec, ed25519
from cryptography.hazmat.primitives import serialization

# --- Mocks for modules imported by the provider ---

# Mock cryptography objects
mock_rsa_priv_key = MagicMock(spec=rsa.RSAPrivateKey)
mock_rsa_pub_key = MagicMock(spec=rsa.RSAPublicKey)
mock_rsa_priv_key.public_key.return_value = mock_rsa_pub_key
mock_rsa_priv_key.private_bytes.return_value = b"mock-rsa-pem-bytes"
mock_rsa_priv_key.sign = MagicMock(return_value=b"mock-rsa-signature")

mock_ec_priv_key = MagicMock(spec=ec.EllipticCurvePrivateKey)
mock_ec_pub_key = MagicMock(spec=ec.EllipticCurvePublicKey)
mock_ec_priv_key.public_key.return_value = mock_ec_pub_key
mock_ec_priv_key.private_bytes.return_value = b"mock-ec-pem-bytes"
mock_ec_priv_key.sign = MagicMock(return_value=b"mock-ec-signature")

mock_ed_priv_key = MagicMock(spec=ed25519.Ed25519PrivateKey)
mock_ed_pub_key = MagicMock(spec=ed25519.Ed25519PublicKey)
mock_ed_priv_key.public_key.return_value = mock_ed_pub_key
mock_ed_priv_key.private_bytes.return_value = b"mock-ed-raw-bytes"
mock_ed_priv_key.sign = MagicMock(return_value=b"mock-ed-signature")

# Mock pkcs11 library
mock_pkcs11 = MagicMock(name="pkcs11")
mock_pkcs11_lib = MagicMock(name="pkcs11.lib")
mock_pkcs11_token = MagicMock(name="pkcs11.Token")
mock_pkcs11_session = MagicMock(name="pkcs11.Session")
mock_pkcs11.lib.return_value = mock_pkcs11_lib
mock_pkcs11_lib.get_token.return_value = mock_pkcs11_token
mock_pkcs11_token.open.return_value = mock_pkcs11_session
mock_pkcs11.exceptions = SimpleNamespace(PKCS11Error=type('PKCS11Error', (Exception,), {}))
mock_pkcs11.constants = SimpleNamespace(
    CKM_EC_EDWARDS_KEY_PAIR_GEN=1,
    CKM_RSA_PKCS_KEY_PAIR_GEN=2,
    CKM_EC_KEY_PAIR_GEN=3,
    CKM_EDDSA=4,
    CKM_RSA_PKCS_PSS=5,
    CKM_ECDSA=6,
    CKM_SHA256=7,
    CKG_MGF1_SHA256=8,
    CKS_RW_USER_FUNCTIONS=9
)
mock_pkcs11.Attribute = SimpleNamespace(
    TOKEN=10, PRIVATE=11, SIGN=12, ID=13, LABEL=14, EXTRACTABLE=15, SENSITIVE=16,
    VERIFY=17, MODULUS_BITS=18, PUBLIC_EXPONENT=19, EC_PARAMS=20, CLASS=21, KEY_TYPE=22
)
mock_pkcs11.ObjectClass = SimpleNamespace(PRIVATE_KEY=23, PUBLIC_KEY=24)
mock_pkcs11.KeyType = SimpleNamespace(EC_EDWARDS=25, RSA=26, EC=27)
mock_pkcs11.ffi = SimpleNamespace(new=MagicMock())


# --- Pytest Fixtures ---

@pytest.fixture(autouse=True, scope="session")
def prometheus_registry_mock():
    """
    Prevent "Duplicated timeseries" errors by patching the global
    Prometheus registry before any modules are imported.
    """
    with patch("prometheus_client.CollectorRegistry.register", MagicMock()):
        yield

@pytest.fixture(autouse=True, scope="session")
def set_required_env_vars_for_collection():
    """
    Sets minimal required environment variables *before* any modules are imported.
    This prevents ConfigurationError during pytest collection, which happens
    before any test fixtures are run.
    """
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    mp.setenv("AUDIT_CRYPTO_PROVIDER_TYPE", "software")
    mp.setenv("AUDIT_CRYPTO_DEFAULT_ALGO", "ed25519")
    mp.setenv("AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS", "86400")
    mp.setenv("AUDIT_LOG_DEV_MODE", "true")
    mp.setenv("AUDIT_CRYPTO_HSM_ENABLED", "false") # Default to false
    yield
    mp.undo()

@pytest.fixture
def mock_settings(monkeypatch):
    """Mocks the Dynaconf 'settings' object."""
    mock_settings_obj = MagicMock(name="MockSettings")
    settings_dict = {
        "PROVIDER_TYPE": "software",
        "DEFAULT_ALGO": "ed25519",
        "SUPPORTED_ALGOS": ["rsa", "ecdsa", "ed25519", "hmac"],
        "SOFTWARE_KEY_DIR": "/tmp/test-keys",
        "HSM_ENABLED": False,
        "HSM_LIBRARY_PATH": "/mock/lib.so",
        "HSM_SLOT_ID": 1,
        "KEY_ROTATION_INTERVAL_SECONDS": 3600, # 1 hour for testing
        "HSM_HEALTH_CHECK_INTERVAL_SECONDS": 30,
        "HSM_RETRY_ATTEMPTS": 3,
        "HSM_BACKOFF_FACTOR": 1,
        "HSM_INITIAL_DELAY": 0.1,
    }
    
    def get_setting(key, default=None):
        return settings_dict.get(key, default)

    mock_settings_obj.get = MagicMock(side_effect=get_setting)
    for k, v in settings_dict.items():
        setattr(mock_settings_obj, k, v)
        
    # Patch the 'settings' object in the provider's namespace
    # This is complex because of the lazy loading. We patch the one from the factory.
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_factory.settings", mock_settings_obj)
    
    # --- FIX: Remove incorrect patch target ---
    # We also patch the `default_settings` import alias in the provider module
    # monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_provider.default_settings", mock_settings_obj)
    # --- END OF FIX ---

    return mock_settings_obj, settings_dict

@pytest.fixture
def mock_factory_imports(monkeypatch):
    """Mocks all imports from audit_crypto_factory."""
    mock_log = AsyncMock(name="log_action")
    mock_send_alert = AsyncMock(name="send_alert")
    mock_retry = AsyncMock(name="retry_operation", side_effect=lambda func, **kwargs: func()) # Just execute the func
    
    mock_metrics = {
        "CRYPTO_ERRORS": MagicMock(name="CRYPTO_ERRORS", labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "KEY_LOAD_COUNT": MagicMock(name="KEY_LOAD_COUNT", labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "KEY_CLEANUP_COUNT": MagicMock(name="KEY_CLEANUP_COUNT", labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "SIGN_OPERATIONS": MagicMock(name="SIGN_OPERATIONS", labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "SIGN_LATENCY": MagicMock(name="SIGN_LATENCY", labels=MagicMock(return_value=MagicMock(observe=MagicMock()))),
        "VERIFY_OPERATIONS": MagicMock(name="VERIFY_OPERATIONS", labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "VERIFY_LATENCY": MagicMock(name="VERIFY_LATENCY", labels=MagicMock(return_value=MagicMock(observe=MagicMock()))),
        "KEY_ROTATIONS": MagicMock(name="KEY_ROTATIONS", labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
        "HSM_SESSION_HEALTH": MagicMock(name="HSM_SESSION_HEALTH", labels=MagicMock(return_value=MagicMock(set=MagicMock()))),
    }

    # --- FIX: Patch the *source* module (audit_crypto_factory) ---
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_factory.log_action", mock_log, raising=False)
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_factory.send_alert", mock_send_alert, raising=False)
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_factory.retry_operation", mock_retry, raising=False)
    
    for name, mock in mock_metrics.items():
        monkeypatch.setattr(f"generator.audit_log.audit_crypto.audit_crypto_factory.{name}", mock, raising=False)
        
    # Mock SensitiveDataFilter to avoid import errors
    mock_filter = MagicMock(name="SensitiveDataFilter")
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_factory.SensitiveDataFilter", mock_filter)
    
    # Mock CryptoInitializationError (it's defined in factory)
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_factory.CryptoInitializationError", Exception)
    # --- END OF FIX ---

    return {
        "log_action": mock_log,
        "send_alert": mock_send_alert,
        "retry_operation": mock_retry,
        **mock_metrics
    }

@pytest.fixture
def mock_keystore(monkeypatch):
    """Mocks the KeyStore class."""
    mock_keystore_instance = MagicMock(name="KeyStore")
    mock_keystore_instance.list_keys = AsyncMock(return_value=[])
    mock_keystore_instance.load_key = AsyncMock(return_value=None)
    mock_keystore_instance.store_key = AsyncMock()
    mock_keystore_instance.delete_key_file = AsyncMock(return_value=True)
    
    mock_keystore_class = MagicMock(name="KeyStoreClass", return_value=mock_keystore_instance)
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_provider.KeyStore", mock_keystore_class)
    return mock_keystore_instance, mock_keystore_class

@pytest.fixture
def mock_crypto_libs(monkeypatch):
    """Mocks cryptography generation functions."""
    # Reset mocks before patching
    mock_rsa_priv_key.reset_mock()
    mock_rsa_pub_key.reset_mock()
    mock_ec_priv_key.reset_mock()
    mock_ec_pub_key.reset_mock()
    mock_ed_priv_key.reset_mock()
    mock_ed_pub_key.reset_mock()

    mocks = {
        "rsa": patch("cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key", return_value=mock_rsa_priv_key),
        "ec": patch("cryptography.hazmat.primitives.asymmetric.ec.generate_private_key", return_value=mock_ec_priv_key),
        "ed25519": patch("cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey.generate", return_value=mock_ed_priv_key),
        "hmac": patch("os.urandom", return_value=b"mock-hmac-key-32-bytes-12345678"),
        "uuid": patch("uuid.uuid4", return_value=MagicMock(hex="mock-uuid-hex", __str__=lambda s: "mock-uuid-str")),
        "time": patch("time.time", return_value=1234567890.0)
    }
    
    # Start all patches
    for m in mocks.values():
        m.start()
        
    yield mocks
    
    # Stop all patches
    for m in mocks.values():
        m.stop()

@pytest.fixture
def mock_hsm_full(monkeypatch):
    """Mocks HSM dependencies (pkcs11 and get_hsm_pin)."""
    # Reset mocks
    mock_pkcs11_lib.reset_mock()
    mock_pkcs11_token.reset_mock()
    mock_pkcs11_session.reset_mock()
    
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_provider.HAS_PKCS11", True)
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_provider.pkcs11", mock_pkcs11)
    mock_get_pin = MagicMock(name="get_hsm_pin", return_value="123456")
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_provider.get_hsm_pin", mock_get_pin)
    
    # Mock os.path.exists for the library path check
    monkeypatch.setattr("os.path.exists", lambda path: True)
    
    return mock_pkcs11_session, mock_get_pin

@pytest_asyncio.fixture
async def mock_accessors():
    """Provides mock async accessor functions for master key and fallback secret."""
    master_key_accessor = AsyncMock(name="master_key_accessor", return_value=b"mock-master-key-for-keystore-32b")
    fallback_secret_accessor = AsyncMock(name="fallback_secret_accessor", return_value=b"mock-fallback-secret-key-32bytes!")
    return master_key_accessor, fallback_secret_accessor

@pytest.fixture
def software_provider(mock_settings, mock_factory_imports, mock_keystore, mock_crypto_libs, mock_accessors):
    """
    Provides an initialized SoftwareCryptoProvider instance.
    
    --- FIX ---
    This fixture is now SYNCHRONOUS and MANUALLY CONSTRUCTS the provider
    instance without calling __init__. This is critical to avoid the
    `asyncio.run()` call in __init__ which poisons the event loop for
    all subsequent `@pytest.mark.asyncio` tests.
    
    The synchronous init tests (`test_init_*`) will test the real __init__ method.
    The async method tests (`test_sign_*`, `test_verify_*`) will use this
    "clean" instance.
    """
    from generator.audit_log.audit_crypto.audit_crypto_provider import SoftwareCryptoProvider
    
    # 1. Create instance without calling __init__
    provider = SoftwareCryptoProvider.__new__(SoftwareCryptoProvider)
    
    # 2. Manually set all the attributes that __init__ would have set
    provider._lazy_settings = mock_settings[0]
    provider._background_tasks = set()
    provider.logger = logging.getLogger(f"{__name__}.{provider.__class__.__name__}")
    provider.settings = mock_settings[0]
    provider.software_key_master_accessor = mock_accessors[0]
    provider.fallback_hmac_secret_accessor = mock_accessors[1]
    
    # 3. Mock the results of __init__
    provider.key_store = mock_keystore[0] # Use the mock keystore instance
    provider.keys = {} # Init to empty dict
    
    # 4. Mock the tasks that __init__ *would* have started (if it were async)
    #    We need these for the .close() call.
    provider._load_keys_task = AsyncMock(name="_load_keys_task")
    provider._rotation_task = AsyncMock(name="_rotation_task")
    provider._background_tasks.add(provider._load_keys_task)
    provider._background_tasks.add(provider._rotation_task)
    
    try:
        yield provider
    finally:
        # Cleanup is async, so we must run it.
        # This is safe *after* the async test has run and its loop is closed.
        asyncio.run(provider.close())

@pytest_asyncio.fixture
async def hsm_provider(mock_settings, mock_factory_imports, mock_hsm_full, mock_accessors):
    """Provides an initialized HSMCryptoProvider instance."""
    from generator.audit_log.audit_crypto.audit_crypto_provider import HSMCryptoProvider
    
    mock_settings[1]["HSM_ENABLED"] = True # Ensure HSM is enabled
    
    # This fixture is async, but the HSM __init__ doesn't call asyncio.run()
    # in the same problematic way, it defers with create_task.
    # So, the original logic is fine.

    # Stop the background tasks from starting automatically
    with patch.object(HSMCryptoProvider, "_initialize_hsm_session", AsyncMock()) as mock_init, \
         patch.object(HSMCryptoProvider, "_monitor_hsm_health", AsyncMock()):
        
        provider = HSMCryptoProvider(
            software_key_master_accessor=mock_accessors[0],
            fallback_hmac_secret_accessor=mock_accessors[1],
            settings=mock_settings[0]
        )
        # Manually set session to valid for most tests
        provider.session = mock_hsm_full[0]
        mock_init.assert_called_once() # Init should be called
        
        yield provider
        await provider.close()


# --- Test Classes ---

@pytest.mark.usefixtures("mock_factory_imports", "mock_accessors", "mock_settings")
class TestCryptoProviderABC:
    
    @pytest.mark.asyncio
    async def test_base_class_close(self, mock_accessors, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import CryptoProvider
        
        class TestProvider(CryptoProvider):
            async def sign(self, data, key_id): pass
            async def verify(self, sig, data, key_id): pass
            async def generate_key(self, algo): pass
            async def rotate_key(self, old_key_id, algo): pass

        provider = TestProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])
        
        # Add a mock task
        mock_task = AsyncMock(name="mock_task")
        mock_task.done.return_value = False
        provider._background_tasks.add(mock_task)
        
        await provider.close()
        
        mock_task.cancel.assert_called_once()
        # FIX: Assert task is *removed* from the set
        assert mock_task not in provider._background_tasks # add_done_callback not called on mock
        provider._background_tasks.clear() # Manual clear for test


@pytest.mark.usefixtures("mock_settings", "mock_factory_imports", "mock_keystore", "mock_crypto_libs")
class TestSoftwareCryptoProvider:

    # FIX: This test must be SYNC because it tests a SYNC __init__ that calls asyncio.run()
    def test_init_success(self, mock_accessors, mock_keystore, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import SoftwareCryptoProvider
        
        # FIX: Removed asyncio.run patch
        with patch.object(SoftwareCryptoProvider, "_load_existing_keys", AsyncMock()) as mock_load, \
             patch.object(SoftwareCryptoProvider, "_rotate_keys_periodically", AsyncMock()) as mock_rotate:
            
            provider = None
            try:
                provider = SoftwareCryptoProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])
                
                mock_accessors[0].assert_called_once()
                mock_keystore[1].assert_called_once_with(
                    "/tmp/test-keys",
                    b"mock-master-key-for-keystore-32b"
                )
                
                # --- FIX ---
                # In a sync context, __init__ *correctly* fails to get a running loop,
                # logs a warning, and *does not* start the tasks.
                # The assertion must be that they were NOT called.
                mock_load.assert_not_called()
                mock_rotate.assert_not_called()
                
            finally:
                if provider:
                    asyncio.run(provider.close()) # FIX: Cleanup

    # FIX: This test must be SYNC
    def test_init_no_master_key(self, mock_accessors, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import SoftwareCryptoProvider
        
        mock_accessors[0].return_value = None # Master key accessor fails
        
        # FIX: Removed asyncio.run patch
        with pytest.raises(Exception, match="Master encryption key is missing"):
            SoftwareCryptoProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])

    # FIX: This test must be SYNC
    def test_init_keystore_fail(self, mock_accessors, mock_keystore, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import SoftwareCryptoProvider
        
        mock_keystore[1].side_effect = Exception("Keystore init failed")
        
        # FIX: Removed asyncio.run patch
        with pytest.raises(Exception, match="Failed to initialize KeyStore"):
            SoftwareCryptoProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])

    @pytest.mark.asyncio
    async def test_sign_success(self, software_provider):
        key_id = "key-1"
        software_provider.keys[key_id] = {"key_obj": mock_ed_priv_key, "algo": "ed25519", "status": "active"}
        
        sig = await software_provider.sign(b"data", key_id)
        
        mock_ed_priv_key.sign.assert_called_once_with(b"data")
        assert sig == mock_ed_priv_key.sign.return_value

    @pytest.mark.asyncio
    async def test_sign_key_not_found(self, software_provider):
        from generator.audit_log.audit_crypto.audit_crypto_provider import KeyNotFoundError
        
        with pytest.raises(KeyNotFoundError, match="Active key 'key-1' not found"):
            await software_provider.sign(b"data", "key-1")

    @pytest.mark.asyncio
    async def test_sign_key_not_active(self, software_provider):
        from generator.audit_log.audit_crypto.audit_crypto_provider import InvalidKeyStatusError
        key_id = "key-1"
        software_provider.keys[key_id] = {"key_obj": mock_ed_priv_key, "algo": "ed25519", "status": "retired"}
        
        with pytest.raises(InvalidKeyStatusError, match="Key 'key-1' is not active"):
            await software_provider.sign(b"data", key_id)

    @pytest.mark.asyncio
    async def test_verify_success(self, software_provider):
        key_id = "key-1"
        software_provider.keys[key_id] = {"key_obj": mock_ed_priv_key, "algo": "ed25519", "status": "active"}
        
        result = await software_provider.verify(b"sig", b"data", key_id)
        
        assert result is True
        mock_ed_pub_key.verify.assert_called_once_with(b"sig", b"data")

    @pytest.mark.asyncio
    async def test_verify_success_retired_key(self, software_provider):
        key_id = "key-1"
        software_provider.keys[key_id] = {"key_obj": mock_ed_priv_key, "algo": "ed25519", "status": "retired"}
        
        result = await software_provider.verify(b"sig", b"data", key_id)
        assert result is True # Verification should still work
        mock_ed_pub_key.verify.assert_called_once_with(b"sig", b"data")

    @pytest.mark.asyncio
    async def test_verify_key_not_found(self, software_provider):
        result = await software_provider.verify(b"sig", b"data", "key-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_fail_invalid_signature(self, software_provider):
        key_id = "key-1"
        software_provider.keys[key_id] = {"key_obj": mock_ed_priv_key, "algo": "ed25519", "status": "active"}
        mock_ed_pub_key.verify.side_effect = InvalidSignature("Verification failed")
        
        result = await software_provider.verify(b"sig", b"data", key_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_generate_key_success(self, software_provider, mock_keystore, mock_crypto_libs):
        new_key_id = await software_provider.generate_key("ed25519")
        
        assert new_key_id == "mock-uuid-str"
        mock_keystore[0].store_key.assert_called_once_with(
            "mock-uuid-str",
            b"mock-ed-raw-bytes",
            "ed25519",
            1234567890.0,
            status="active"
        )
        assert "mock-uuid-str" in software_provider.keys
        assert software_provider.keys["mock-uuid-str"]["algo"] == "ed25519"

    @pytest.mark.asyncio
    async def test_generate_key_unsupported(self, software_provider):
        from generator.audit_log.audit_crypto.audit_crypto_provider import UnsupportedAlgorithmError
        with pytest.raises(UnsupportedAlgorithmError, match="Unsupported algorithm: md5"):
            await software_provider.generate_key("md5")

    @pytest.mark.asyncio
    async def test_rotate_key_success(self, software_provider, mock_keystore):
        old_key_id = "old-key"
        software_provider.keys[old_key_id] = {
            "key_obj": mock_ed_priv_key, 
            "algo": "ed25519", 
            "creation_time": 1000.0,
            "status": "active"
        }
        
        new_key_id = await software_provider.rotate_key(old_key_id, "ed25519")
        
        assert new_key_id == "mock-uuid-str"
        assert software_provider.keys[old_key_id]["status"] == "retired"
        assert software_provider.keys[old_key_id]["retired_at"] == 1234567890.0
        
        # Check that new key was stored
        mock_keystore[0].store_key.assert_any_call(
            "mock-uuid-str", ANY, "ed25519", 1234567890.0, status="active"
        )
        # Check that old key was updated
        mock_keystore[0].store_key.assert_any_call(
            old_key_id, b"mock-ed-raw-bytes", "ed25519", 1000.0, status="retired", retired_at=1234567890.0
        )

    @pytest.mark.asyncio
    async def test_periodic_rotation_and_cleanup(self, software_provider, mock_keystore, mock_factory_imports, mock_crypto_libs):
        
        # This test needs to *undo* the patch on _rotate_keys_periodically
        # that the software_provider fixture applies.
        
        # 1. Setup keys
        active_key = "active-key-1"
        old_active_key = "old-active-key"
        retired_key = "retired-key-1"
        old_retired_key = "old-retired-key"
        
        current_time = 1234567890.0
        mock_crypto_libs["time"].return_value = current_time
        
        software_provider.keys = {
            active_key: {"key_obj": mock_ed_priv_key, "algo": "ed25519", "creation_time": current_time - 100, "status": "active"},
            old_active_key: {"key_obj": mock_ed_priv_key, "algo": "ed25519", "creation_time": current_time - 4000, "status": "active"}, # 4000s > 3600s interval
            retired_key: {"key_obj": mock_ed_priv_key, "algo": "ed25519", "creation_time": 1000.0, "status": "retired", "retired_at": current_time - 100},
            old_retired_key: {"key_obj": mock_ed_priv_key, "algo": "ed25519", "creation_time": 2000.0, "status": "retired", "retired_at": current_time - 8000}, # 8000s > 3600s * 2
        }
        
        # --- FIX: START ---
        # The real method awaits _load_keys_task. The fixture sets this
        # to an AsyncMock, which is not awaitable. We must replace it
        # with an awaitable, completed Future for this test.
        completed_future = asyncio.Future()
        completed_future.set_result(None)
        software_provider._load_keys_task = completed_future
        # --- FIX: END ---
        
        # 2. Patch sleep to run once and *unpatch* the rotate method
        with patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)):
            with patch.object(software_provider, "rotate_key", AsyncMock(return_value="new-key-from-rotate")) as mock_rotate_call:
                
                # 3. Run the task
                # We fetch the original method from the class and bind it to the instance
                from generator.audit_log.audit_crypto.audit_crypto_provider import SoftwareCryptoProvider
                original_rotate_periodic = SoftwareCryptoProvider._rotate_keys_periodically
                
                await original_rotate_periodic(software_provider)
                
                # 4. Check assertions
                # Should try to rotate the old active key
                mock_rotate_call.assert_called_once_with(old_active_key, "ed25519")
                
                # Should try to delete the old retired key
                mock_keystore[0].delete_key_file.assert_called_once_with(old_retired_key)
                assert old_retired_key not in software_provider.keys # Removed from memory
                
                # Should log success
                mock_factory_imports["KEY_CLEANUP_COUNT"].labels.assert_called_with(provider_type="software", status="success")
                mock_factory_imports["log_action"].assert_any_call("key_delete", key_id=old_retired_key, provider="software", status="success")


@pytest.mark.usefixtures("mock_settings", "mock_factory_imports", "mock_hsm_full", "mock_crypto_libs")
class TestHSMCryptoProvider:

    @pytest.mark.asyncio
    async def test_init_success(self, mock_accessors, mock_hsm_full, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import HSMCryptoProvider
        
        mock_session, mock_get_pin = mock_hsm_full
        mock_settings[1]["HSM_ENABLED"] = True
        
        with patch.object(HSMCryptoProvider, "_monitor_hsm_health", AsyncMock()):
            provider = HSMCryptoProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])
            
            # Wait for the _hsm_init_task to complete
            await provider._hsm_init_task
            
            mock_get_pin.assert_called_once()
            mock_pkcs11_lib.get_token.assert_called_once_with(slot=1)
            mock_pkcs11_token.open.assert_called_once_with(rw=True, user_pin="123456")
            assert provider.session is mock_session
            await provider.close()

    @pytest.mark.asyncio
    async def test_init_no_pkcs11(self, monkeypatch, mock_accessors, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import HSMCryptoProvider
        
        monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_provider.HAS_PKCS11", False)
        mock_settings[1]["HSM_ENABLED"] = True
        
        with pytest.raises(Exception, match="PKCS#11 library not found"):
            HSMCryptoProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])

    @pytest.mark.asyncio
    async def test_init_pin_fail(self, mock_accessors, mock_hsm_full, mock_settings):
        from generator.audit_log.audit_crypto.audit_crypto_provider import HSMCryptoProvider
        
        mock_hsm_full[1].side_effect = Exception("Secret not found")
        mock_settings[1]["HSM_ENABLED"] = True
        
        with pytest.raises(Exception, match="HSM PIN not available"):
            HSMCryptoProvider(mock_accessors[0], mock_accessors[1], mock_settings[0])

    @pytest.mark.asyncio
    async def test_generate_key_success(self, hsm_provider):
        new_key_id = await hsm_provider.generate_key("ed25519")
        
        assert new_key_id == "mock-uuid-str"
        mock_pkcs11_session.generate_key_pair.assert_called_once_with(
            mock_pkcs11.constants.CKM_EC_EDWARDS_KEY_PAIR_GEN,
            private_template=ANY,
            public_template=ANY
        )

    @pytest.mark.asyncio
    async def test_sign_success(self, hsm_provider):
        # FIX: Assign mock key first, *then* set its attributes
        mock_priv_key = MagicMock(name="MockPrivKey")
        mock_pkcs11_session.find_objects.return_value.single.return_value = mock_priv_key
        mock_priv_key.get_attribute.return_value = mock_pkcs11.KeyType.EC_EDWARDS
        
        # Setup mock return for the signer context
        mock_signer = mock_priv_key.sign.return_value.__enter__.return_value
        mock_signer.sign.return_value = b"mock-hsm-signature"
        
        sig = await hsm_provider.sign(b"data", "key-label-1")
        
        mock_pkcs11_session.find_objects.assert_called_once_with({
            mock_pkcs11.Attribute.CLASS: mock_pkcs11.ObjectClass.PRIVATE_KEY,
            mock_pkcs11.Attribute.LABEL: "key-label-1"
        })
        # FIX: Assert the call on the signer, not the session
        mock_priv_key.sign.assert_called_once_with(ANY) # Check that .sign() was called to get the context
        mock_signer.sign.assert_called_once_with(b"data")
        # --- FINAL FIX: Add the missing hyphen ---
        assert sig == b"mock-hsm-signature"

    @pytest.mark.asyncio
    async def test_sign_key_not_found(self, hsm_provider):
        from generator.audit_log.audit_crypto.audit_crypto_provider import HSMKeyError
        
        mock_pkcs11_session.find_objects.return_value.single.return_value = None
        
        with pytest.raises(HSMKeyError, match="Private key with label 'key-label-1' not found"):
            await hsm_provider.sign(b"data", "key-label-1")

    @pytest.mark.asyncio
    async def test_verify_success(self, hsm_provider):
        # FIX: Assign mock key first, *then* set its attributes
        mock_pub_key = MagicMock(name="MockPubKey")
        mock_pkcs11_session.find_objects.return_value.single.return_value = mock_pub_key
        mock_pub_key.get_attribute.return_value = mock_pkcs11.KeyType.EC_EDWARDS
        
        result = await hsm_provider.verify(b"sig", b"data", "key-label-1")
        
        assert result is True
        mock_pkcs11_session.find_objects.assert_called_once_with({
            mock_pkcs11.Attribute.CLASS: mock_pkcs11.ObjectClass.PUBLIC_KEY,
            mock_pkcs11.Attribute.LABEL: "key-label-1"
        })
        mock_pkcs11_session.verify.assert_called_once_with(mock_pub_key, b"data", b"sig", mechanism=ANY)

    @pytest.mark.asyncio
    async def test_verify_fail_invalid_signature(self, hsm_provider):
        # FIX: Assign mock key first, *then* set its attributes
        mock_pub_key = MagicMock(name="MockPubKey")
        mock_pkcs11_session.find_objects.return_value.single.return_value = mock_pub_key
        mock_pub_key.get_attribute.return_value = mock_pkcs11.KeyType.EC_EDWARDS
        
        mock_pkcs11_session.verify.side_effect = InvalidSignature("HSM verify failed")
        
        result = await hsm_provider.verify(b"sig", b"data", "key-label-1")
        assert result is False

    @pytest.mark.asyncio
    async def test_rotate_key_success(self, hsm_provider):
        # Mock find_objects to return a valid object for destruction
        mock_priv_key_obj = MagicMock(name="MockPrivKey")
        mock_pub_key_obj = MagicMock(name="MockPubKey")
        
        def find_objects_side_effect(attrs):
            if attrs[mock_pkcs11.Attribute.CLASS] == mock_pkcs11.ObjectClass.PRIVATE_KEY:
                return MagicMock(single=MagicMock(return_value=mock_priv_key_obj))
            if attrs[mock_pkcs11.Attribute.CLASS] == mock_pkcs11.ObjectClass.PUBLIC_KEY:
                return MagicMock(single=MagicMock(return_value=mock_pub_key_obj))
            return MagicMock(single=MagicMock(return_value=None))
            
        mock_pkcs11_session.find_objects.side_effect = find_objects_side_effect
        
        new_key_id = await hsm_provider.rotate_key("old-key-label", "ed25519")
        
        assert new_key_id == "mock-uuid-str"
        # Check that new key was generated
        mock_pkcs11_session.generate_key_pair.assert_called_once()
        # Check that old keys were destroyed
        mock_pkcs11_session.destroy_object.assert_has_calls([
            call(mock_priv_key_obj),
            call(mock_pub_key_obj)
        ])
        assert mock_pkcs11_session.destroy_object.call_count == 2