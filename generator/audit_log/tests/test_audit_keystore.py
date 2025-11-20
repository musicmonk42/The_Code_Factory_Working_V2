# ---- test bootstrap (must be before imports) ----
import os
os.environ.setdefault("AUDIT_CRYPTO_PROVIDER_TYPE", "software")
os.environ.setdefault("AUDIT_CRYPTO_DEFAULT_ALGO", "ed25519")
os.environ.setdefault("AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS", "86400")
os.environ.setdefault("AUDIT_LOG_DEV_MODE", "true")

# --- FIX: Patch Prometheus *before* any project imports ---
from unittest.mock import patch, MagicMock
# This patch starts NOW and will be active when audit_crypto_factory is imported
prometheus_patcher = patch("prometheus_client.CollectorRegistry.register", MagicMock())
prometheus_patcher.start()
# -------------------------------------------------


# test_audit_keystore.py
# FIX: All import paths and patch paths have been corrected to be absolute from the 'generator' root.

import unittest
import asyncio
import tempfile
import stat
import json
import base64
import time
import sys # <-- ADDED for platform check
from unittest.mock import patch, MagicMock, AsyncMock, call
import pytest # <-- Import pytest

# --- Import the module to be tested ---
try:
    from generator.audit_log.audit_crypto.audit_keystore import KeyStore, FileSystemKeyStorageBackend
    # We also need the real cryptography exceptions for testing
    from cryptography.exceptions import InvalidTag
    # --- FIX: Import the factory to get mock handles ---
    from generator.audit_log.audit_crypto import audit_crypto_factory
    from generator.audit_log.audit_crypto import audit_crypto_provider
except ImportError as e:
    print(f"Error: Could not import audit_keystore. Ensure it's in the same directory or on your PYTHONPATH.")
    print(f"Details: {e}")
    exit(1)

# We need to mock the external dependencies that audit_keystore imports# This is a helper function to apply all mocks to a test class
_PATCHED_KEYSTORE_DEPS = False
def patch_keystore_dependencies(cls):
    """
    Applies all necessary patches for external dependencies
    (from factory and provider) to a test class.

    IMPORTANT: We start the patches at module import time and
    simply return the class unmodified, to avoid injecting
    extra positional arguments into test methods.
    """
    global _PATCHED_KEYSTORE_DEPS
    if _PATCHED_KEYSTORE_DEPS:
        # Patches already active; just return the class unchanged.
        return cls

    # --- Patch the *source* modules so tests see mocks via audit_crypto_factory ---
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_factory.log_action",
        new_callable=AsyncMock,
    ).start()
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_factory.send_alert",
        new_callable=AsyncMock,
    ).start()

    # Metrics counters: provide a .labels(...).inc() chain of MagicMocks
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_factory.KEY_STORE_COUNT",
        MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
    ).start()
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_factory.KEY_LOAD_COUNT",
        MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
    ).start()
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_factory.CRYPTO_ERRORS",
        MagicMock(labels=MagicMock(return_value=MagicMock(inc=MagicMock()))),
    ).start()

    # Mock the SensitiveDataFilter import used in the factory
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_factory.SensitiveDataFilter",
        MagicMock(),
    ).start()

    # Mock CryptoOperationError from audit_crypto_provider so the tests can
    # catch it without pulling in the full provider stack.
    MockCryptoError = type("CryptoOperationError", (Exception,), {})
    patch(
        "generator.audit_log.audit_crypto.audit_crypto_provider.CryptoOperationError",
        MockCryptoError,
    ).start()

    _PATCHED_KEYSTORE_DEPS = True
    return cls


@patch_keystore_dependencies
class TestFileSystemKeyStorageBackend(unittest.IsolatedAsyncioTestCase):
    """
    Tests the FileSystemKeyStorageBackend (the "physical" layer).
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.key_dir = self.temp_dir.name
        self.backend = FileSystemKeyStorageBackend(self.key_dir)
        
        # We need this for our `except` blocks
        # --- FIX: Get the mock error from the correct (patched) module ---
        self.CryptoOperationError = audit_crypto_provider.CryptoOperationError

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_01_init_creates_dir(self):
        # Test that a new directory is created
        new_dir = os.path.join(self.temp_dir.name, "new_keys")
        self.assertFalse(os.path.exists(new_dir))
        FileSystemKeyStorageBackend(new_dir)
        self.assertTrue(os.path.exists(new_dir))

    async def test_02_store_and_load_key_data(self):
        key_id = "test-key-1"
        payload = "encrypted-payload"
        metadata = {"algo": "rsa", "creation_time": 123, "status": "active"}

        # Store
        await self.backend.store_key_data(key_id, payload, metadata)
        
        # Check file exists
        filepath = os.path.join(self.key_dir, f"{key_id}.json")
        self.assertTrue(os.path.exists(filepath))

        # Load
        loaded_data = await self.backend.load_key_data(key_id)
        
        self.assertIsNotNone(loaded_data)
        self.assertEqual(loaded_data["encrypted_payload_b64"], payload)
        self.assertEqual(loaded_data["algo"], "rsa")
        self.assertEqual(loaded_data["key_id"], key_id)

    async def test_03_load_key_not_found(self):
        loaded_data = await self.backend.load_key_data("non-existent-key")
        self.assertIsNone(loaded_data)

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows does not support POSIX 0o600 file permissions")
    async def test_04_atomic_write_and_permissions(self):
        filepath = os.path.join(self.key_dir, "atomic_test.txt")
        content = b"atomic data"
        
        await self.backend._atomic_write_and_set_permissions(filepath, content)

        # Check content
        with open(filepath, "rb") as f:
            self.assertEqual(f.read(), content)
        
        # Check permissions (must be 0o600)
        mode = stat.S_IMODE(os.stat(filepath).st_mode)
        self.assertEqual(mode, 0o600, f"File permissions are {oct(mode)}, not 0o600")

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows does not support POSIX 0o600 file permissions")
    async def test_05_verify_permissions_good(self):
        filepath = os.path.join(self.key_dir, "good_perms.txt")
        with open(filepath, "w") as f:
            f.write("test")
        os.chmod(filepath, 0o600)

        # Should run without error
        await self.backend._verify_permissions(filepath)
        mode = stat.S_IMODE(os.stat(filepath).st_mode)
        self.assertEqual(mode, 0o600)

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows does not support POSIX 0o600 file permissions")
    async def test_06_verify_permissions_bad_and_fix(self):
        filepath = os.path.join(self.key_dir, "bad_perms.txt")
        with open(filepath, "w") as f:
            f.write("test")
        os.chmod(filepath, 0o777) # Set bad permissions
        
        # Verify it's bad
        self.assertNotEqual(stat.S_IMODE(os.stat(filepath).st_mode), 0o600)
        
        # Should correct them
        await self.backend._verify_permissions(filepath)
        
        # Verify they are fixed
        mode = stat.S_IMODE(os.stat(filepath).st_mode)
        self.assertEqual(mode, 0o600, f"File permissions are {oct(mode)}, not 0o600")

    async def test_07_delete_key_data(self):
        key_id = "to-be-deleted"
        filepath = os.path.join(self.key_dir, f"{key_id}.json")
        
        # Create a file to delete
        await self.backend.store_key_data(key_id, "payload", {"algo": "test", "creation_time": 123, "status": "active"})
        self.assertTrue(os.path.exists(filepath))
        
        # Delete
        result = await self.backend.delete_key_data(key_id)
        self.assertTrue(result)
        self.assertFalse(os.path.exists(filepath))

        # Delete non-existent
        result_false = await self.backend.delete_key_data("non-existent")
        self.assertFalse(result_false)

    async def test_08_list_key_metadata(self):
        # Store 3 keys
        await self.backend.store_key_data("key1", "p1", {"algo": "rsa", "creation_time": 1, "status": "active"})
        await self.backend.store_key_data("key2", "p2", {"algo": "ecdsa", "creation_time": 2, "status": "retired", "retired_at": 3})
        await self.backend.store_key_data("key3", "p3", {"algo": "hmac", "creation_time": 4, "status": "active"})
        
        # Create a non-key file
        with open(os.path.join(self.key_dir, "not_a_key.txt"), "w") as f:
            f.write("test")

        metadata_list = await self.backend.list_key_metadata()
        
        self.assertEqual(len(metadata_list), 3)
        key_ids = {m["key_id"] for m in metadata_list}
        self.assertSetEqual(key_ids, {"key1", "key2", "key3"})
        
        # Check retired_at was included
        key2_meta = next(m for m in metadata_list if m["key_id"] == "key2")
        self.assertEqual(key2_meta["status"], "retired")
        self.assertEqual(key2_meta["retired_at"], 3)

    async def test_09_load_key_corrupted_json(self):
        key_id = "bad-json"
        filepath = os.path.join(self.key_dir, f"{key_id}.json")
        
        # Write bad data (not JSON)
        await self.backend._atomic_write_and_set_permissions(filepath, b"{not json")
        
        with self.assertRaises(self.CryptoOperationError) as cm:
            await self.backend.load_key_data(key_id)
        self.assertIn("Corrupted key file", str(cm.exception))

    async def test_10_load_key_missing_metadata(self):
        key_id = "missing-meta"
        # Missing 'algo'
        await self.backend.store_key_data(key_id, "payload", {"creation_time": 123, "status": "active"})
        
        with self.assertRaises(ValueError) as cm:
            await self.backend.load_key_data(key_id)
        self.assertIn("Missing essential metadata", str(cm.exception))

    async def test_11_load_key_id_mismatch(self):
        key_id = "correct-id"
        filepath = os.path.join(self.key_dir, f"{key_id}.json")
        
        # Manually write file with mismatched key_id in metadata
        bad_meta = {"key_id": "wrong-id", "encrypted_payload_b64": "p", "algo": "a", "creation_time": 1, "status": "s"}
        data = json.dumps(bad_meta).encode('utf-8')
        await self.backend._atomic_write_and_set_permissions(filepath, data)
        
        with self.assertRaises(ValueError) as cm:
            await self.backend.load_key_data(key_id)
        self.assertIn("Key ID mismatch", str(cm.exception))

    async def test_12_list_key_skips_corrupted(self):
        # Store 2 good keys
        await self.backend.store_key_data("good1", "p1", {"algo": "rsa", "creation_time": 1, "status": "active"})
        await self.backend.store_key_data("good2", "p2", {"algo": "ecdsa", "creation_time": 2, "status": "active"})
        
        # Store 1 bad JSON key
        filepath = os.path.join(self.key_dir, "bad.json")
        await self.backend._atomic_write_and_set_permissions(filepath, b"not json")
        
        metadata_list = await self.backend.list_key_metadata()
        
        # Should log an error but continue, returning only the good keys
        self.assertEqual(len(metadata_list), 2)
        key_ids = {m["key_id"] for m in metadata_list}
        self.assertSetEqual(key_ids, {"good1", "good2"})

    async def test_13_locking_mechanism(self):
        filepath = os.path.join(self.key_dir, "lock_test.txt")

        # Test exclusive lock
        self.assertNotIn(filepath, self.backend._lock_files)
        await self.backend._acquire_lock(filepath, shared=False)
        self.assertIn(filepath, self.backend._lock_files)
        self.assertFalse(self.backend._lock_files[filepath].closed)
        
        await self.backend._release_lock(filepath)
        self.assertNotIn(filepath, self.backend._lock_files)

        # Test shared lock
        self.assertNotIn(filepath, self.backend._lock_files)
        await self.backend._acquire_lock(filepath, shared=True)
        self.assertIn(filepath, self.backend._lock_files)
        self.assertFalse(self.backend._lock_files[filepath].closed)
        
        await self.backend._release_lock(filepath)
        self.assertNotIn(filepath, self.backend._lock_files)


@patch_keystore_dependencies
class TestKeyStore(unittest.IsolatedAsyncioTestCase):
    """
    Tests the KeyStore (the "logical" layer) with a real
    FileSystemKeyStorageBackend.
    """
    
    # --- FIX: Get the mock error from the correct (patched) module ---
    @property
    def CryptoOperationError(self):
        return audit_crypto_provider.CryptoOperationError

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.key_dir = self.temp_dir.name
        self.master_key = os.urandom(32) # 256-bit AES key
        
        # Use a real backend for integration testing
        self.backend = FileSystemKeyStorageBackend(self.key_dir)
        self.keystore = KeyStore(self.key_dir, self.master_key, backend=self.backend)

        # Sample data
        self.key_id = "ks-test-key-1"
        self.key_data_bytes = b"my-secret-key-material"
        self.algo = "hmac"
        self.creation_time = time.time()
        self.status = "active"
        
        # --- FIX: Get mock handles directly from the patched factory module ---
        self.mock_log_action = audit_crypto_factory.log_action
        self.mock_key_store_count = audit_crypto_factory.KEY_STORE_COUNT
        self.mock_key_load_count = audit_crypto_factory.KEY_LOAD_COUNT
        self.mock_crypto_errors = audit_crypto_factory.CRYPTO_ERRORS
        self.mock_send_alert = audit_crypto_factory.send_alert
        
        self.mock_log_action.reset_mock()
        self.mock_key_store_count.labels.return_value.inc.reset_mock()
        self.mock_key_load_count.labels.return_value.inc.reset_mock()
        self.mock_crypto_errors.labels.return_value.inc.reset_mock()
        self.mock_send_alert.reset_mock()


    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_01_init_validates_master_key(self):
        with self.assertRaises(ValueError, msg="Master key must be 32 bytes"):
            KeyStore(self.key_dir, master_key=b"short-key")
        
        with self.assertRaises(TypeError, msg="master_key must be bytes"):
            KeyStore(self.key_dir, master_key="not bytes")
            
        with self.assertRaises(TypeError, msg="key_dir must be a non-empty string"):
            KeyStore(None, master_key=self.master_key)

    async def test_02_store_and_load_key_success(self):
        # Store
        await self.keystore.store_key(
            self.key_id, self.key_data_bytes, self.algo, 
            self.creation_time, self.status
        )
        
        # Check that log_action was called with success
        self.mock_log_action.assert_called_with(
            "key_store", key_id=self.key_id, algo=self.algo, 
            status=self.status, success=True
        )
        self.mock_key_store_count.labels.assert_called_with(provider_type="software", status="success")
        
        self.mock_log_action.reset_mock()

        # Load
        loaded_data = await self.keystore.load_key(self.key_id)

        self.assertIsNotNone(loaded_data)
        self.assertEqual(loaded_data["key_data"], self.key_data_bytes)
        self.assertEqual(loaded_data["algo"], self.algo)
        self.assertEqual(loaded_data["status"], self.status)
        self.assertAlmostEqual(loaded_data["creation_time"], self.creation_time)
        
        # Check log_action for load
        self.mock_log_action.assert_called_with(
            "key_load", key_id=self.key_id, algo=self.algo, 
            status=self.status, success=True
        )
        self.mock_key_load_count.labels.assert_called_with(provider_type="software", status="success")

    async def test_03_load_key_not_found(self):
        loaded_data = await self.keystore.load_key("non-existent-key")
        self.assertIsNone(loaded_data)
        self.mock_log_action.assert_called_with(
            "key_load", key_id="non-existent-key", success=False, error="Key not found"
        )

    async def test_04_load_key_wrong_master_key(self):
        # Store with correct key
        await self.keystore.store_key(
            self.key_id, self.key_data_bytes, self.algo, 
            self.creation_time, self.status
        )
        
        # Create new keystore with wrong key
        wrong_master_key = os.urandom(32)
        wrong_keystore = KeyStore(self.key_dir, wrong_master_key, backend=self.backend)

        # Mock its dependencies too (since it's a new instance)
        # --- FIX: No need to patch __globals__ ---
        
        # Attempt to load
        with self.assertRaises(self.CryptoOperationError) as cm:
            await wrong_keystore.load_key(self.key_id)
        
        # This is the key: AES GCM raises InvalidTag on wrong key or tampered data
        self.assertIsInstance(cm.exception.__cause__, InvalidTag)
        self.assertIn("Integrity check failed", str(cm.exception))
        
        # Check that error was logged and alert was sent
        self.mock_log_action.assert_called_with(
            "key_load", key_id=self.key_id, success=False, error="Integrity check failed (InvalidTag)"
        )
        self.mock_crypto_errors.labels.assert_called_with(type="KeyTampering", provider_type="software", operation="load_key")
        self.mock_send_alert.assert_called_once()

    async def test_05_load_key_tampered_payload(self):
        # Store
        await self.keystore.store_key(
            self.key_id, self.key_data_bytes, self.algo, 
            self.creation_time, self.status
        )
        
        # Manually tamper with the file
        filepath = os.path.join(self.key_dir, f"{self.key_id}.json")
        with open(filepath, "r") as f:
            data = json.load(f)
        
        payload_b64 = data["encrypted_payload_b64"]
        payload_bytes = base64.b64decode(payload_b64)
        
        # Flip a bit in the ciphertext (after nonce, before tag)
        tampered_payload = payload_bytes[:15] + b"\x00" + payload_bytes[16:]
        data["encrypted_payload_b64"] = base64.b64encode(tampered_payload).decode('utf-8')
        
        with open(filepath, "w") as f:
            json.dump(data, f)
            
        # Attempt to load
        with self.assertRaises(self.CryptoOperationError) as cm:
            await self.keystore.load_key(self.key_id)
        self.assertIsInstance(cm.exception.__cause__, InvalidTag)
        self.assertIn("Integrity check failed", str(cm.exception))
        self.mock_send_alert.assert_called_once()

    async def test_06_load_key_tampered_aad(self):
        # Store
        await self.keystore.store_key(
            self.key_id, self.key_data_bytes, self.algo, 
            self.creation_time, self.status
        )
        
        # Manually tamper with the AAD (metadata in the file)
        filepath = os.path.join(self.key_dir, f"{self.key_id}.json")
        with open(filepath, "r") as f:
            data = json.load(f)
        
        data["algo"] = "tampered-algo" # This was part of AAD
        
        with open(filepath, "w") as f:
            json.dump(data, f)
            
        # Attempt to load
        with self.assertRaises(self.CryptoOperationError) as cm:
            await self.keystore.load_key(self.key_id)
        self.assertIsInstance(cm.exception.__cause__, InvalidTag)
        self.assertIn("Integrity check failed", str(cm.exception))
        self.mock_send_alert.assert_called_once()

    async def test_07_store_key_with_retired_at(self):
        retired_time = time.time()
        await self.keystore.store_key(
            self.key_id, self.key_data_bytes, self.algo, 
            self.creation_time, "retired", retired_at=retired_time
        )
        
        loaded_data = await self.keystore.load_key(self.key_id)
        
        self.assertEqual(loaded_data["status"], "retired")
        self.assertAlmostEqual(loaded_data["retired_at"], retired_time)

    async def test_08_list_keys(self):
        await self.keystore.store_key("k1", b"d1", "rsa", time.time(), "active")
        await self.keystore.store_key("k2", b"d2", "ecdsa", time.time(), "active")
        
        keys_list = await self.keystore.list_keys()
        
        self.assertEqual(len(keys_list), 2)
        key_ids = {k["key_id"] for k in keys_list}
        self.assertSetEqual(key_ids, {"k1", "k2"})

    async def test_09_delete_key_file(self):
        await self.keystore.store_key(
            self.key_id, self.key_data_bytes, self.algo, 
            self.creation_time, self.status
        )
        
        self.assertTrue(os.path.exists(os.path.join(self.key_dir, f"{self.key_id}.json")))
        
        result = await self.keystore.delete_key_file(self.key_id)
        
        self.assertTrue(result)
        self.assertFalse(os.path.exists(os.path.join(self.key_dir, f"{self.key_id}.json")))
        self.mock_log_action.assert_called_with(
            "key_delete", key_id=self.key_id, success=True
        )

    async def test_10_store_key_handles_backend_error(self):
        # Mock the backend to raise an error
        mock_backend = AsyncMock(spec=FileSystemKeyStorageBackend)
        mock_backend.store_key_data.side_effect = self.CryptoOperationError("Disk full")
        
        keystore = KeyStore(self.key_dir, self.master_key, backend=mock_backend)
        
        # Mock its dependencies
        # --- FIX: No need to patch __globals__ ---
        
        with self.assertRaises(self.CryptoOperationError) as cm:
            await keystore.store_key(
                self.key_id, self.key_data_bytes, self.algo, 
                self.creation_time, self.status
            )
        
        self.assertIn("Disk full", str(cm.exception))
        
        # Verify failure was logged
        self.mock_log_action.assert_called_with(
            "key_store", key_id=self.key_id, algo=self.algo, 
            status=self.status, success=False, error="Disk full"
        )
        self.mock_key_store_count.labels.assert_called_with(provider_type="software", status="fail")


if __name__ == "__main__":
    unittest.main()

# --- FIX: Stop the patcher at the end of the module ---
prometheus_patcher.stop()