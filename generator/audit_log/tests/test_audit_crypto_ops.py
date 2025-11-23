import base64
import hashlib
import hmac
import json
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest
from cryptography.exceptions import InvalidSignature

# Import exceptions from the module we're mocking, to be used in side_effects
# --- MOVED TO FIX COLLECTION ERROR ---
# from generator.audit_log.audit_crypto.audit_crypto_provider import (
#     CryptoOperationError, KeyNotFoundError, InvalidKeyStatusError,
#     UnsupportedAlgorithmError, HSMError
# )

# --- START OF FIX ---


@pytest.fixture(autouse=True, scope="session")
def set_required_env_vars_for_collection():
    """
    Sets minimal required environment variables *before* any modules are imported.
    This prevents ConfigurationError during pytest collection phase, which happens
    before any test fixtures are run.

    Using scope="session" and autouse=True ensures this runs once, very first.
    We manually create a MonkeyPatch object as the 'monkeypatch' fixture is function-scoped.
    """
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()

    mp.setenv("AUDIT_CRYPTO_PROVIDER_TYPE", "software")
    mp.setenv("AUDIT_CRYPTO_DEFAULT_ALGO", "ed25519")
    mp.setenv("AUDIT_CRYPTO_KEY_ROTATION_INTERVAL_SECONDS", "86400")
    # Set dev mode to bypass production checks (like missing KMS_KEY_ID)
    mp.setenv("AUDIT_LOG_DEV_MODE", "true")

    yield  # Allow the test session to run

    mp.undo()  # Clean up all monkeypatches at the end of the session


# --- END OF FIX ---


# --- Module-Scoped Fixtures ---


@pytest.fixture(scope="module")
def sample_entry_data():
    """Provides a consistent sample entry for tests."""
    return {
        "action": "test_action",
        "timestamp": 1234567890.123,
        "entry_id": "a1b2c3d4-e5f6-7890-a1b2-c3d4e5f67890",
        "user_id": "test_user",
    }


@pytest.fixture(scope="module")
def sample_stream_metadata(sample_entry_data):
    """Provides consistent metadata for stream tests."""
    data = sample_entry_data.copy()
    data["blob_type"] = "file_upload"
    return data


@pytest.fixture
def mock_crypto_provider():
    """Mocks the CryptoProvider instance."""
    provider = MagicMock(name="MockCryptoProvider")
    provider.sign = AsyncMock(name="sign", return_value=b"mock-signature-bytes")
    provider.verify = AsyncMock(name="verify", return_value=True)
    provider.rotate_key = AsyncMock(name="rotate_key", return_value="new-mock-key-id-123")
    return provider


@pytest.fixture
def mock_settings(monkeypatch):
    """Mocks the Dynaconf 'settings' object from the factory."""
    mock_settings_obj = MagicMock(name="MockSettings")

    # Default settings values
    settings_dict = {
        "PROVIDER_TYPE": "mock_provider",
        "SUPPORTED_ALGOS": ["rsa", "ecdsa", "ed25519", "hmac"],
        "FALLBACK_ALERT_INTERVAL_SECONDS": 300,
        "MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT": 3,
        "MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE": 10,
    }

    # Use side_effect to allow per-test overrides
    def get_setting(key, default=None):
        return settings_dict.get(key, default)

    mock_settings_obj.get = MagicMock(side_effect=get_setting)

    # --- FIX for settings.PROVIDER_TYPE failures ---
    # Set the attributes on the mock object itself so that
    # direct attribute access (settings.PROVIDER_TYPE) returns the value.
    for k, v in settings_dict.items():
        setattr(mock_settings_obj, k, v)
    # --- END OF FIX ---

    # Patch the 'settings' object in audit_crypto_ops's namespace
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_ops.settings", mock_settings_obj
    )
    return mock_settings_obj, settings_dict  # Return dict for modification


@pytest.fixture
def mock_crypto_provider_factory(monkeypatch, mock_crypto_provider):
    """Mocks the 'crypto_provider_factory' from the factory."""
    mock_factory = MagicMock(name="MockCryptoProviderFactory")
    mock_factory.get_provider = MagicMock(return_value=mock_crypto_provider)
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_ops.crypto_provider_factory",
        mock_factory,
    )
    return mock_factory


@pytest.fixture
def mock_log_action(monkeypatch):
    """Mocks the 'log_action' async function from the factory."""
    mock = AsyncMock(name="log_action")
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_ops.log_action", mock)
    return mock


@pytest.fixture
def mock_send_alert(monkeypatch):
    """Mocks the 'send_alert' async function from the factory."""
    mock = AsyncMock(name="send_alert")
    monkeypatch.setattr("generator.audit_log.audit_crypto.audit_crypto_ops.send_alert", mock)
    return mock


@pytest.fixture
def mock_metrics(monkeypatch):
    """Mocks the 'CRYPTO_ERRORS' Prometheus counter from the factory."""
    mock_counter = MagicMock(name="CRYPTO_ERRORS")
    mock_counter.labels.return_value.inc = MagicMock()
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_ops.CRYPTO_ERRORS", mock_counter
    )
    return mock_counter


@pytest.fixture
def mock_fallback_secret(monkeypatch):
    """Mocks the '_FALLBACK_HMAC_SECRET' global var from the factory."""
    secret = b"test-fallback-secret-key-32bytes!"
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_ops._FALLBACK_HMAC_SECRET",
        secret,
    )
    return secret


@pytest.fixture
def mock_time(monkeypatch):
    """Mocks 'time.time'."""
    mock_t = MagicMock(name="time.time", return_value=1234567890.0)
    monkeypatch.setattr("time.time", mock_t)
    return mock_t


@pytest.fixture
def mock_asyncio_sleep(monkeypatch):
    """Mocks 'asyncio.sleep'."""
    mock_sleep = AsyncMock(name="asyncio.sleep")
    monkeypatch.setattr("asyncio.sleep", mock_sleep)
    return mock_sleep


@pytest.fixture(autouse=True)
def reset_ops_global_state(monkeypatch):
    """
    Resets the global state variables in audit_crypto_ops before each test.
    This is CRITICAL for fallback logic tests.
    """
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_ops._FALLBACK_ATTEMPT_COUNT", {}
    )
    monkeypatch.setattr(
        "generator.audit_log.audit_crypto.audit_crypto_ops._LAST_FALLBACK_ALERT_TIME",
        0.0,
    )


# --- Helper for streaming tests ---
async def async_gen(data_list: list):
    """A simple async generator for stream tests."""
    for item in data_list:
        if isinstance(item, Exception):
            raise item
        yield item


# --- Test Classes ---


@pytest.mark.usefixtures(
    "mock_settings",
    "mock_crypto_provider_factory",
    "mock_log_action",
    "mock_send_alert",
    "mock_metrics",
    "mock_fallback_secret",
)
class TestUtilityFunctions:
    """Tests the helper functions."""

    def test_compute_hash(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import compute_hash

        data = b"hello world"
        expected_hash = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        assert compute_hash(data) == expected_hash

    def test_compute_hash_invalid_type(self, caplog):
        from generator.audit_log.audit_crypto.audit_crypto_ops import compute_hash

        with pytest.raises(TypeError, match="Data for hashing must be bytes"):
            compute_hash("not bytes")
        assert "Data for hashing must be bytes" in caplog.text

    @pytest.mark.asyncio
    async def test_stream_compute_hash_success(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_compute_hash

        data = [b"hello", b" ", b"world"]
        expected_hash = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        result = await stream_compute_hash(async_gen(data))
        assert result == expected_hash

    @pytest.mark.asyncio
    async def test_stream_compute_hash_invalid_chunk_type(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_compute_hash

        data = [b"good", "bad", b"chunk"]
        with pytest.raises(TypeError, match="All chunks yielded by data_chunks must be bytes"):
            await stream_compute_hash(async_gen(data))

    @pytest.mark.asyncio
    async def test_stream_compute_hash_stream_error(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_compute_hash

        data = [b"good", b"chunk", RuntimeError("Stream failed")]
        with pytest.raises(RuntimeError, match="Stream failed"):
            await stream_compute_hash(async_gen(data))


@pytest.mark.usefixtures(
    "mock_settings",
    "mock_crypto_provider_factory",
    "mock_log_action",
    "mock_send_alert",
    "mock_metrics",
    "mock_fallback_secret",
)
class TestSigning:
    """Tests for sign_entry and stream_sign_entry."""

    # --- START OF MOVED IMPORT (FIX) ---
    @pytest.fixture(autouse=True)
    def import_exceptions(self):
        """Imports exceptions needed for side_effects."""
        global CryptoOperationError, KeyNotFoundError, InvalidKeyStatusError, UnsupportedAlgorithmError, HSMError
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
            HSMError,
            InvalidKeyStatusError,
            KeyNotFoundError,
            UnsupportedAlgorithmError,
        )

    # --- END OF MOVED IMPORT (FIX) ---

    @pytest.mark.asyncio
    async def test_sign_entry_success(
        self, mock_crypto_provider, mock_log_action, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import compute_hash, sign_entry

        entry = sample_entry_data
        key_id = "key-1"
        prev_hash = "hash-of-prev-entry"

        result = await sign_entry(entry, key_id, prev_hash)

        # Check result
        assert result == base64.b64encode(b"mock-signature-bytes").decode("utf-8")

        # Check that the provider was called with the correctly serialized data
        expected_signed_dict = entry.copy()
        expected_signed_dict["prev_hash"] = prev_hash
        expected_signed_data = json.dumps(expected_signed_dict, sort_keys=True).encode("utf-8")

        mock_crypto_provider.sign.assert_called_once_with(expected_signed_data, key_id)

        # Check log_action
        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "sign",
                "key_id": key_id,
                "entry_hash_signed_content": compute_hash(expected_signed_data),
                "prev_hash_used": prev_hash,
                "success": True,
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_entry, key, p_hash, expected_error",
        [
            ("not a dict", "key-1", "hash", TypeError),
            ({}, 123, "hash", TypeError),
            ({}, "key-1", 123, TypeError),
        ],
    )
    async def test_sign_entry_invalid_types(self, invalid_entry, key, p_hash, expected_error):
        from generator.audit_log.audit_crypto.audit_crypto_ops import sign_entry

        with pytest.raises(expected_error):
            await sign_entry(invalid_entry, key, p_hash)

    @pytest.mark.asyncio
    async def test_sign_entry_missing_fields(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import sign_entry

        entry = {"action": "only_one_field"}
        with pytest.raises(ValueError, match="Missing: timestamp, entry_id"):
            await sign_entry(entry, "key-1", "hash")

    @pytest.mark.asyncio
    async def test_sign_entry_provider_failure(
        self, mock_crypto_provider, mock_metrics, mock_log_action, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import sign_entry

        mock_crypto_provider.sign.side_effect = KeyNotFoundError("key not found")

        with pytest.raises(KeyNotFoundError):
            await sign_entry(sample_entry_data, "key-1", "hash")

        # Check error metric
        mock_metrics.labels.assert_called_once_with(
            type="KeyNotFoundError",
            provider_type="mock_provider",
            operation="sign_entry",
        )

        # Check error log
        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "sign",
                "key_id": "key-1",
                "entry_hash_signed_content": ANY,
                "prev_hash_used": "hash",
                "success": False,
                "error": "key not found",
            },
        )

    @pytest.mark.asyncio
    async def test_sign_entry_unexpected_failure(
        self, mock_crypto_provider, mock_metrics, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import sign_entry

        mock_crypto_provider.sign.side_effect = Exception("boom")

        with pytest.raises(CryptoOperationError, match="Unexpected error during signing: boom"):
            await sign_entry(sample_entry_data, "key-1", "hash")

        # Check error metric
        mock_metrics.labels.assert_called_once_with(
            type="UnexpectedError",
            provider_type="mock_provider",
            operation="sign_entry",
        )

    @pytest.mark.asyncio
    async def test_stream_sign_entry_success(
        self, mock_crypto_provider, mock_log_action, sample_stream_metadata
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_sign_entry

        stream_data = [b"chunk1", b"chunk2"]
        metadata = sample_stream_metadata
        key_id = "key-stream"
        prev_hash = "prev-hash-stream"

        result = await stream_sign_entry(async_gen(stream_data), metadata, key_id, prev_hash)

        assert result == base64.b64encode(b"mock-signature-bytes").decode("utf-8")

        # Check what was signed
        data_hash = hashlib.sha256(b"chunk1chunk2").hexdigest()
        expected_signed_dict = metadata.copy()
        expected_signed_dict["data_hash"] = data_hash
        expected_signed_dict["prev_hash"] = prev_hash
        expected_signed_data = json.dumps(expected_signed_dict, sort_keys=True).encode("utf-8")

        mock_crypto_provider.sign.assert_called_once_with(expected_signed_data, key_id)

        # Check log_action
        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "stream_sign",
                "key_id": key_id,
                "data_hash": data_hash,
                "prev_hash_used": prev_hash,
                "success": True,
            },
        )

    @pytest.mark.asyncio
    async def test_stream_sign_entry_hash_failure(self, mock_metrics, sample_stream_metadata):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_sign_entry

        stream_data = [b"chunk1", TypeError("invalid chunk")]

        with pytest.raises(CryptoOperationError, match="Failed to hash data stream"):
            await stream_sign_entry(async_gen(stream_data), sample_stream_metadata, "key-1", "hash")

        mock_metrics.labels.assert_called_once_with(
            type="StreamingHashFail", provider_type="utility", operation="stream_sign"
        )

    @pytest.mark.asyncio
    async def test_stream_sign_entry_missing_metadata(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_sign_entry

        with pytest.raises(ValueError, match="Missing: action, timestamp, entry_id"):
            await stream_sign_entry(async_gen([b"data"]), {}, "key-1", "hash")


@pytest.mark.usefixtures(
    "mock_settings",
    "mock_crypto_provider_factory",
    "mock_log_action",
    "mock_send_alert",
    "mock_metrics",
    "mock_fallback_secret",
)
class TestVerification:
    """Tests for verify_entry and stream_verify_entry."""

    # --- START OF MOVED IMPORT (FIX) ---
    @pytest.fixture(autouse=True)
    def import_exceptions(self):
        """Imports exceptions needed for side_effects."""
        global CryptoOperationError, KeyNotFoundError, InvalidKeyStatusError, UnsupportedAlgorithmError, HSMError
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
            HSMError,
            InvalidKeyStatusError,
            KeyNotFoundError,
            UnsupportedAlgorithmError,
        )

    # --- END OF MOVED IMPORT (FIX) ---

    @pytest.mark.asyncio
    async def test_verify_entry_success(
        self, mock_crypto_provider, mock_log_action, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_entry

        entry = sample_entry_data.copy()
        entry["prev_hash"] = "hash-of-prev"
        signature_b64 = base64.b64encode(b"mock-signature-bytes").decode("utf-8")
        key_id = "key-1"

        result = await verify_entry(entry, signature_b64, key_id)

        assert result is True

        # Check what was verified
        expected_verified_data = json.dumps(entry, sort_keys=True).encode("utf-8")
        mock_crypto_provider.verify.assert_called_once_with(
            b"mock-signature-bytes", expected_verified_data, key_id
        )

        # Check log
        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": key_id,
                "success": True,
                "entry_hash_verified_content": ANY,
            },
        )

    @pytest.mark.asyncio
    async def test_verify_entry_invalid_signature(
        self, mock_crypto_provider, mock_metrics, mock_log_action, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_entry

        mock_crypto_provider.verify.side_effect = InvalidSignature("sig mismatch")

        result = await verify_entry(sample_entry_data, "c2lnIGJhZA==", "key-1")

        assert result is False

        # Check metric
        mock_metrics.labels.assert_called_once_with(
            type="InvalidSignature",
            provider_type="mock_provider",
            operation="verify_entry",
        )

        # Check log
        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": "key-1",
                "success": False,
                "entry_hash_verified_content": ANY,
                "error": "InvalidSignature",
            },
        )

    @pytest.mark.asyncio
    async def test_verify_entry_invalid_base64(
        self, mock_crypto_provider, mock_metrics, mock_log_action, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_entry

        result = await verify_entry(sample_entry_data, "not-valid-base64!", "key-1")

        assert result is False

        # Check metric
        mock_metrics.labels.assert_called_once_with(
            type="Base64DecodeError", provider_type="unknown", operation="verify_entry"
        )

        # Provider should not be called
        mock_crypto_provider.verify.assert_not_called()

        # Check log
        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "verify",
                "key_id": "key-1",
                "success": False,
                "entry_hash_verified_content": ANY,
                "error": ANY,
            },
        )

    @pytest.mark.asyncio
    async def test_verify_entry_provider_error(
        self, mock_crypto_provider, mock_metrics, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_entry

        mock_crypto_provider.verify.side_effect = HSMError("hsm connection failed")

        with pytest.raises(HSMError):
            await verify_entry(sample_entry_data, "c2lnIGJhZA==", "key-1")

        mock_metrics.labels.assert_called_once_with(
            type="HSMError", provider_type="mock_provider", operation="verify_entry"
        )

    @pytest.mark.asyncio
    async def test_stream_verify_entry_success(
        self, mock_crypto_provider, mock_log_action, sample_stream_metadata
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_verify_entry

        stream_data = [b"chunk1", b"chunk2"]
        metadata = sample_stream_metadata.copy()
        metadata["prev_hash"] = "prev-hash-stream"

        signature_b64 = base64.b64encode(b"mock-signature-bytes").decode("utf-8")
        key_id = "key-stream"

        # We need to compute the hash *before* the call to check the provider
        data_hash = hashlib.sha256(b"chunk1chunk2").hexdigest()

        # This is the entry that gets verified
        expected_verified_dict = metadata.copy()
        expected_verified_dict["data_hash"] = data_hash
        expected_verified_data = json.dumps(expected_verified_dict, sort_keys=True).encode("utf-8")

        result = await stream_verify_entry(async_gen(stream_data), metadata, signature_b64, key_id)

        assert result is True
        mock_crypto_provider.verify.assert_called_once_with(
            b"mock-signature-bytes", expected_verified_data, key_id
        )

        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "stream_verify",
                "key_id": key_id,
                "success": True,
                "data_hash_verified_content": data_hash,
            },
        )

    @pytest.mark.asyncio
    async def test_stream_verify_entry_hash_fail(
        self, mock_metrics, mock_crypto_provider, sample_stream_metadata
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_verify_entry

        stream_data = [b"chunk1", RuntimeError("stream read error")]

        result = await stream_verify_entry(
            async_gen(stream_data), sample_stream_metadata, "c2ln=", "key-1"
        )

        assert result is False
        mock_metrics.labels.assert_called_once_with(
            type="StreamingHashFail", provider_type="utility", operation="stream_verify"
        )
        mock_crypto_provider.verify.assert_not_called()

    @pytest.mark.asyncio
    async def test_stream_verify_entry_invalid_signature(
        self, mock_crypto_provider, mock_metrics, sample_stream_metadata
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import stream_verify_entry

        mock_crypto_provider.verify.side_effect = InvalidSignature("sig mismatch")

        result = await stream_verify_entry(
            async_gen([b"data"]), sample_stream_metadata, "c2ln=", "key-1"
        )

        assert result is False
        mock_metrics.labels.assert_called_once_with(
            type="InvalidSignature",
            provider_type="mock_provider",
            operation="stream_verify",
        )


@patch(
    "generator.audit_log.audit_crypto.audit_crypto_ops.verify_entry",
    new_callable=AsyncMock,
)
@patch("generator.audit_log.audit_crypto.audit_crypto_ops.compute_hash")
@pytest.mark.usefixtures(
    "mock_settings",
    "mock_crypto_provider_factory",
    "mock_log_action",
    "mock_send_alert",
    "mock_metrics",
    "mock_fallback_secret",
)
class TestChainVerification:
    """Tests the verify_chain function."""

    def _create_chain(self, mock_compute_hash, num_entries=3):
        """Helper to create a list of validly chained entries."""
        entries = []
        prev_hash = ""

        # --- FIX for chain verification logic ---
        # Use side_effect to return the correct hash for each entry
        hash_list = [f"hash-{i}" for i in range(num_entries)]
        mock_compute_hash.side_effect = hash_list
        # --- END OF FIX ---

        for i in range(num_entries):
            entry_content = {
                "entry_id": f"id-{i}",
                "action": "test",
                "timestamp": 1234567890 + i,
                "prev_hash": prev_hash,
            }

            # The hash is computed on the content *before* sig/key are added
            current_hash = hash_list[i]  # Get the hash for this iteration

            # Call compute_hash manually to get the value it *would* have returned
            # This is what verify_chain will call
            json_data = json.dumps(entry_content, sort_keys=True).encode("utf-8")
            # We call compute_hash via the mock, so we don't need this line:
            # _ = compute_hash(json_data)

            entry_full = entry_content.copy()
            entry_full["signature"] = f"sig-{i}"
            entry_full["key_id"] = f"key-{i}"

            entries.append(entry_full)
            prev_hash = current_hash  # Set for next iteration

        return entries

    @pytest.mark.asyncio
    async def test_verify_chain_success(
        self, mock_compute_hash, mock_verify_entry, mock_log_action
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_chain

        mock_verify_entry.return_value = True
        entries = self._create_chain(mock_compute_hash, 3)

        result = await verify_chain(entries)

        assert result is True

        # Check that verify_entry was called for each entry
        assert mock_verify_entry.call_count == 3
        mock_verify_entry.assert_any_call(ANY, "sig-0", "key-0")
        mock_verify_entry.assert_any_call(ANY, "sig-1", "key-1")
        mock_verify_entry.assert_any_call(ANY, "sig-2", "key-2")

        # Check that compute_hash was called to calculate the *next* prev_hash
        assert mock_compute_hash.call_count == 3

        mock_log_action.assert_called_with("verify_chain", status="success")

    @pytest.mark.asyncio
    async def test_verify_chain_hash_mismatch(
        self, mock_compute_hash, mock_verify_entry, mock_metrics, mock_log_action
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_chain

        entries = self._create_chain(mock_compute_hash, 3)
        entries[2]["prev_hash"] = "tampered-hash"  # Break the chain

        result = await verify_chain(entries)

        assert result is False

        # FIX: The loop adds tasks for 0 and 1, then fails on 2.
        # The gather is called with 2 tasks.
        assert mock_verify_entry.call_count == 2

        mock_metrics.labels.assert_called_once_with(
            type="ChainIntegrityFail", provider_type="unknown", operation="verify_chain"
        )
        mock_log_action.assert_called_with(
            "verify_chain",
            status="fail",
            reason="hash_mismatch",
            entry_index=2,
            entry_id="id-2",
        )

    @pytest.mark.asyncio
    async def test_verify_chain_signature_fail(
        self, mock_compute_hash, mock_verify_entry, mock_log_action
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_chain

        # Second entry's signature is invalid
        mock_verify_entry.side_effect = [True, False, True]
        entries = self._create_chain(mock_compute_hash, 3)

        result = await verify_chain(entries)

        assert result is False

        # All verifications run in parallel via asyncio.gather
        assert mock_verify_entry.call_count == 3

        mock_log_action.assert_called_with(
            "verify_chain", status="fail", reason="cryptographic_mismatch"
        )

    @pytest.mark.asyncio
    async def test_verify_chain_missing_signature(
        self, mock_compute_hash, mock_verify_entry, mock_metrics, mock_log_action
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import verify_chain

        entries = self._create_chain(mock_compute_hash, 3)
        del entries[1]["signature"]  # Remove signature from second entry

        result = await verify_chain(entries)

        assert result is False

        # Fails before hash check of entry 1
        mock_metrics.labels.assert_called_once_with(
            type="MissingSigOrKey", provider_type="unknown", operation="verify_chain"
        )
        mock_log_action.assert_called_with(
            "verify_chain",
            status="fail",
            reason="missing_sig_or_key",
            entry_index=1,
            entry_id="id-1",
        )

        # Only verify_entry(0) would have been gathered before the check fails
        # The loop appends task 0.
        # Loop 1 checks hash (ok), then sig/key (fail), then returns False.
        # asyncio.gather is never called.

        # FIX: The task is created, so mock_verify_entry *is* called once.
        assert mock_verify_entry.call_count == 1
        assert mock_compute_hash.call_count == 1  # Only for entry 0


@pytest.mark.usefixtures(
    "mock_settings",
    "mock_crypto_provider_factory",
    "mock_log_action",
    "mock_send_alert",
    "mock_metrics",
    "mock_fallback_secret",
)
class TestRotation:
    """Tests the rotate_key function."""

    # --- START OF MOVED IMPORT (FIX) ---
    @pytest.fixture(autouse=True)
    def import_exceptions(self):
        """Imports exceptions needed for side_effects."""
        global CryptoOperationError, KeyNotFoundError, InvalidKeyStatusError, UnsupportedAlgorithmError, HSMError
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
            HSMError,
            InvalidKeyStatusError,
            KeyNotFoundError,
            UnsupportedAlgorithmError,
        )

    # --- END OF MOVED IMPORT (FIX) ---

    @pytest.mark.asyncio
    async def test_rotate_key_success(self, mock_crypto_provider, mock_log_action):
        from generator.audit_log.audit_crypto.audit_crypto_ops import rotate_key

        result = await rotate_key(algo="ed25519", old_key_id="key-to-retire")

        assert result == "new-mock-key-id-123"
        mock_crypto_provider.rotate_key.assert_called_once_with("key-to-retire", "ed25519")

        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "rotate_key_overall",
                "old_key_id": "key-to-retire",
                "new_key_id": "new-mock-key-id-123",
                "algo": "ed25519",
                "provider": "mock_provider",
                "success": True,
            },
        )

    @pytest.mark.asyncio
    async def test_rotate_key_unsupported_algo(self):
        from generator.audit_log.audit_crypto.audit_crypto_ops import rotate_key

        with pytest.raises(UnsupportedAlgorithmError, match="Unsupported algorithm"):
            await rotate_key(algo="md5", old_key_id="key-1")

    @pytest.mark.asyncio
    async def test_rotate_key_provider_fail(
        self, mock_crypto_provider, mock_metrics, mock_log_action
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import rotate_key

        mock_crypto_provider.rotate_key.side_effect = HSMError("HSM key destruction failed")

        with pytest.raises(HSMError):
            await rotate_key(algo="rsa", old_key_id="key-1")

        mock_metrics.labels.assert_called_once_with(
            type="HSMError", provider_type="mock_provider", operation="rotate_key"
        )

        mock_log_action.assert_called_once_with(
            "crypto_key_operation",
            {
                "operation": "rotate_key_overall",
                "old_key_id": "key-1",
                "new_key_id": "N/A",
                "algo": "rsa",
                "provider": "mock_provider",
                "success": False,
                "error": "HSM key destruction failed",
            },
        )


@pytest.mark.usefixtures(
    "mock_settings",
    "mock_crypto_provider_factory",
    "mock_log_action",
    "mock_send_alert",
    "mock_metrics",
    "mock_fallback_secret",
    "mock_time",
    "mock_asyncio_sleep",
)
class TestFallbackLogic:
    """Tests the safe_sign and hmac_sign_fallback functions."""

    # --- START OF MOVED IMPORT (FIX) ---
    @pytest.fixture(autouse=True)
    def import_exceptions(self):
        """Imports exceptions needed for side_effects."""
        global CryptoOperationError, KeyNotFoundError, InvalidKeyStatusError, UnsupportedAlgorithmError, HSMError
        from generator.audit_log.audit_crypto.audit_crypto_provider import (
            CryptoOperationError,
            HSMError,
            InvalidKeyStatusError,
            KeyNotFoundError,
            UnsupportedAlgorithmError,
        )

    # --- END OF MOVED IMPORT (FIX) ---

    @pytest.mark.asyncio
    async def test_safe_sign_primary_success(
        self, mock_crypto_provider, mock_log_action, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import safe_sign

        result = await safe_sign(sample_entry_data, "key-1", "hash")

        # Primary success returns raw bytes from the provider
        assert result == b"mock-signature-bytes"

        # Provider was called
        mock_crypto_provider.sign.assert_called_once()

        # Fallback was NOT used
        mock_log_action.assert_not_called()  # No log_action on success path for safe_sign

        # Check that fallback counters were reset
        from generator.audit_log.audit_crypto.audit_crypto_ops import _FALLBACK_ATTEMPT_COUNT

        assert _FALLBACK_ATTEMPT_COUNT.get("total") == 0
        assert _FALLBACK_ATTEMPT_COUNT.get("since_alert") == 0

    @pytest.mark.asyncio
    async def test_safe_sign_primary_fail_fallback_success(
        self,
        mock_crypto_provider,
        mock_log_action,
        mock_metrics,
        mock_fallback_secret,
        sample_entry_data,
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import safe_sign

        # 1. Make primary provider fail
        mock_crypto_provider.sign.side_effect = HSMError("HSM is offline")

        result = await safe_sign(sample_entry_data, "key-1", "hash")

        # 2. Calculate expected HMAC
        expected_signed_dict = sample_entry_data.copy()
        expected_signed_dict["prev_hash"] = "hash"
        data = json.dumps(expected_signed_dict, sort_keys=True).encode("utf-8")
        expected_hmac = hmac.new(mock_fallback_secret, data, hashlib.sha256).hexdigest()

        # 3. Check result
        assert result == expected_hmac

        # 4. Check state
        from generator.audit_log.audit_crypto.audit_crypto_ops import _FALLBACK_ATTEMPT_COUNT

        assert _FALLBACK_ATTEMPT_COUNT == {"total": 1, "since_alert": 1}

        # 5. Check metrics and logs
        mock_metrics.labels.assert_called_once_with(
            type="PrimarySignFail", provider_type="mock_provider", operation="safe_sign"
        )

        mock_log_action.assert_has_calls(
            [
                call(
                    "crypto_primary_sign_fail",
                    {
                        "operation": "sign",
                        "key_id": "key-1",
                        "entry_id": sample_entry_data["entry_id"],
                        "reason": "HSM is offline",
                    },
                ),
                call(
                    "crypto_fallback_sign",
                    {
                        "operation": "sign",
                        "key_id": "HMAC_FALLBACK",
                        "entry_id": sample_entry_data["entry_id"],
                        "reason": "HSM is offline",
                        "success": True,
                    },
                ),
            ]
        )

    @pytest.mark.asyncio
    async def test_safe_sign_primary_and_fallback_fail(
        self,
        mock_crypto_provider,
        mock_log_action,
        mock_metrics,
        mock_send_alert,
        sample_entry_data,
        monkeypatch,
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import safe_sign

        # 1. Make primary provider fail
        mock_crypto_provider.sign.side_effect = HSMError("HSM is offline")

        # 2. Make fallback fail by removing secret
        monkeypatch.setattr(
            "generator.audit_log.audit_crypto.audit_crypto_ops._FALLBACK_HMAC_SECRET",
            None,
        )

        with pytest.raises(CryptoOperationError, match="Both primary and fallback signing failed"):
            await safe_sign(sample_entry_data, "key-1", "hash")

        # Check metrics
        # FIX: The code path logs 3 times: PrimaryFail, FallbackSecretMissing, FallbackSignFail
        assert mock_metrics.labels.call_count == 3
        mock_metrics.labels.assert_any_call(
            type="PrimarySignFail", provider_type="mock_provider", operation="safe_sign"
        )
        mock_metrics.labels.assert_any_call(
            type="FallbackSecretMissing",
            provider_type="fallback",
            operation="hmac_sign",
        )
        mock_metrics.labels.assert_any_call(
            type="FallbackSignFail", provider_type="fallback", operation="safe_sign"
        )

        # Check emergency alert
        mock_send_alert.assert_called_once_with(ANY, severity="emergency")
        assert "HMAC fallback signing failed" in mock_send_alert.call_args[0][0]

    @pytest.mark.asyncio
    async def test_safe_sign_fallback_alerting_logic(
        self,
        mock_crypto_provider,
        mock_settings,
        mock_log_action,
        mock_send_alert,
        mock_time,
        sample_entry_data,
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import (
            _FALLBACK_ATTEMPT_COUNT,
            safe_sign,
        )

        # 1. Configure settings for the test
        mock_settings[1]["MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT"] = 3
        mock_settings[1]["FALLBACK_ALERT_INTERVAL_SECONDS"] = 300

        # 2. Make primary provider fail
        mock_crypto_provider.sign.side_effect = HSMError("HSM is offline")

        # 3. First two attempts: no alert
        await safe_sign(sample_entry_data, "key-1", "hash")  # count = 1
        await safe_sign(sample_entry_data, "key-1", "hash")  # count = 2
        mock_send_alert.assert_not_called()
        assert _FALLBACK_ATTEMPT_COUNT == {"total": 2, "since_alert": 2}

        # 4. Third attempt: alert fires, counter resets
        await safe_sign(sample_entry_data, "key-1", "hash")  # count = 3
        mock_send_alert.assert_called_once_with(ANY, severity="high")
        assert "3 fallback attempts" in mock_send_alert.call_args[0][0]
        assert _FALLBACK_ATTEMPT_COUNT == {"total": 3, "since_alert": 0}

        # 5. Fourth attempt (immediately after): no alert, interval not passed
        mock_send_alert.reset_mock()
        await safe_sign(sample_entry_data, "key-1", "hash")  # count = 4, since_alert = 1
        mock_send_alert.assert_not_called()
        assert _FALLBACK_ATTEMPT_COUNT == {"total": 4, "since_alert": 1}

        # 6. Advance time past interval
        mock_time.return_value += 500

        # 7. Next 2 attempts: no alert
        await safe_sign(sample_entry_data, "key-1", "hash")  # count = 5, since_alert = 2
        await safe_sign(sample_entry_data, "key-1", "hash")  # count = 6, since_alert = 3

        # 8. Alert fires again on 3rd attempt *after* interval
        mock_send_alert.assert_called_once_with(ANY, severity="high")
        assert _FALLBACK_ATTEMPT_COUNT == {"total": 6, "since_alert": 0}

    @pytest.mark.asyncio
    async def test_safe_sign_fallback_auto_disable(
        self, mock_crypto_provider, mock_settings, mock_send_alert, sample_entry_data
    ):
        from generator.audit_log.audit_crypto.audit_crypto_ops import (
            _FALLBACK_ATTEMPT_COUNT,
            safe_sign,
        )

        # 1. Configure settings for the test
        mock_settings[1]["MAX_FALLBACK_ATTEMPTS_BEFORE_DISABLE"] = 5
        mock_settings[1]["MAX_FALLBACK_ATTEMPTS_BEFORE_ALERT"] = 100  # Disable noisy alerts

        # 2. Make primary provider fail
        mock_crypto_provider.sign.side_effect = HSMError("HSM is offline")

        # 3. Run 5 times (should all succeed on fallback)
        for i in range(5):
            await safe_sign(sample_entry_data, "key-1", f"hash-{i}")

        assert _FALLBACK_ATTEMPT_COUNT["total"] == 5

        # 4. Sixth attempt: should fail with auto-disable error
        mock_send_alert.reset_mock()
        with pytest.raises(CryptoOperationError, match="HMAC fallback auto-disabled"):
            await safe_sign(sample_entry_data, "key-1", "hash-6")

        # 5. Check for emergency alert
        mock_send_alert.assert_called_once_with(ANY, severity="emergency")
        assert "auto-disabled" in mock_send_alert.call_args[0][0]
