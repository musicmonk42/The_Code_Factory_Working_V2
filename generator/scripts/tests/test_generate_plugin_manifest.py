
# test_generate_plugin_manifest.py
# Industry-grade test suite for generate_plugin_manifest.py, ensuring compliance with regulated standards.
# Covers unit and integration tests for manifest generation, signing, and verification, with traceability and security.

import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import hashlib
import base64
import logging
import uuid
import sys

# Import functions from generate_plugin_manifest
from generate_plugin_manifest import (
    compute_hash_and_size, load_private_key, load_public_key, sign_manifest,
    verify_manifest, main, GENERATOR_VERSION
)

# Configure logging for traceability and auditability
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Mock cryptography for signing/verification
class MockEd25519PrivateKey:
    def sign(self, data):
        return b'mock_signature'

class MockEd25519PublicKey:
    def verify(self, signature, data):
        if signature != b'mock_signature' or data != b'mock_data':
            raise ValueError("Verification failed")

# Fixture for temporary directory
@pytest.fixture
def tmp_path(tmp_path_factory):
    """Create a temporary directory for test files."""
    return tmp_path_factory.mktemp("manifest_test")

# Fixture for audit log
@pytest.fixture
def audit_log(tmp_path):
    """Set up an audit log file for traceability."""
    log_file = tmp_path / "audit.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]'
    ))
    logger.addHandler(handler)
    yield log_file
    logger.removeHandler(handler)

# Fixture for mock cryptography
@pytest.fixture
def mock_crypto(tmp_path):
    """Mock cryptography module for signing and verification."""
    with patch('generate_plugin_manifest.HAS_CRYPTO', True), \
         patch('generate_plugin_manifest.Ed25519PrivateKey', MockEd25519PrivateKey), \
         patch('generate_plugin_manifest.Ed25519PublicKey', MockEd25519PublicKey), \
         patch('generate_plugin_manifest.serialization.load_pem_private_key', MagicMock(return_value=MockEd25519PrivateKey())), \
         patch('generate_plugin_manifest.serialization.load_pem_public_key', MagicMock(return_value=MockEd25519PublicKey())):
        yield

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# Test class for utility functions
class TestManifestUtilities:
    """Tests for utility functions in generate_plugin_manifest.py."""

    def test_compute_hash_and_size(self, tmp_path, audit_log):
        """Test compute_hash_and_size function."""
        trace_id = str(uuid.uuid4())
        test_file = tmp_path / "test.py"
        test_file.write_bytes(b"test content")
        expected_hash = hashlib.sha256(b"test content").hexdigest()
        hashval, size = compute_hash_and_size(test_file)
        assert hashval == expected_hash
        assert size == len(b"test content")
        log_test_execution("test_compute_hash_and_size", "Passed", trace_id)

    def test_load_private_key(self, tmp_path, mock_crypto, audit_log):
        """Test load_private_key function."""
        trace_id = str(uuid.uuid4())
        key_file = tmp_path / "private_key.pem"
        key_file.write_bytes(b"mock private key")
        private_key = load_private_key(key_file)
        assert isinstance(private_key, MockEd25519PrivateKey)
        log_test_execution("test_load_private_key", "Passed", trace_id)

    def test_load_private_key_missing(self, tmp_path, audit_log):
        """Test load_private_key with missing file."""
        trace_id = str(uuid.uuid4())
        key_file = tmp_path / "nonexistent.pem"
        with pytest.raises(SystemExit) as exc_info:
            load_private_key(key_file)
        assert exc_info.value.code == 1
        log_test_execution("test_load_private_key_missing", "Passed", trace_id)

    def test_load_public_key(self, tmp_path, mock_crypto, audit_log):
        """Test load_public_key function."""
        trace_id = str(uuid.uuid4())
        key_file = tmp_path / "public_key.pem"
        key_file.write_bytes(b"mock public key")
        public_key = load_public_key(key_file)
        assert isinstance(public_key, MockEd25519PublicKey)
        log_test_execution("test_load_public_key", "Passed", trace_id)

    def test_sign_manifest(self, tmp_path, mock_crypto, audit_log):
        """Test sign_manifest function."""
        trace_id = str(uuid.uuid4())
        key_file = tmp_path / "private_key.pem"
        key_file.write_bytes(b"mock private key")
        data = b"mock_data"
        signature = sign_manifest(data, key_file)
        assert signature == base64.b64encode(b"mock_signature").decode("utf-8")
        log_test_execution("test_sign_manifest", "Passed", trace_id)

    def test_verify_manifest_valid(self, tmp_path, mock_crypto, audit_log):
        """Test verify_manifest with valid signature."""
        trace_id = str(uuid.uuid4())
        key_file = tmp_path / "public_key.pem"
        key_file.write_bytes(b"mock public key")
        data = b"mock_data"
        signature = base64.b64encode(b"mock_signature").decode("utf-8")
        verify_manifest(data, signature, key_file)  # Should not raise
        log_test_execution("test_verify_manifest_valid", "Passed", trace_id)

    def test_verify_manifest_invalid(self, tmp_path, mock_crypto, audit_log):
        """Test verify_manifest with invalid signature."""
        trace_id = str(uuid.uuid4())
        key_file = tmp_path / "public_key.pem"
        key_file.write_bytes(b"mock public key")
        data = b"mock_data"
        signature = base64.b64encode(b"invalid_signature").decode("utf-8")
        with pytest.raises(ValueError, match="Verification failed"):
            verify_manifest(data, signature, key_file)
        log_test_execution("test_verify_manifest_invalid", "Passed", trace_id)

# Test class for main function
class TestManifestMain:
    """Tests for the main function in generate_plugin_manifest.py."""

    @pytest.mark.asyncio
    async def test_main_generate_signed_manifest(self, tmp_path, mock_crypto, audit_log):
        """Test main function generating a signed manifest."""
        trace_id = str(uuid.uuid4())
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1.py").write_bytes(b"content1")
        (plugin_dir / "plugin2.py").write_bytes(b"content2")
        output_file = tmp_path / "manifest.json"
        key_file = tmp_path / "private_key.pem"
        key_file.write_bytes(b"mock private key")

        with patch('sys.argv', ['generate_plugin_manifest.py', str(plugin_dir), '--sign', str(key_file), '--out', str(output_file)]):
            with patch('sys.stdout', new=MagicMock()):
                main()

        with open(output_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        assert "manifest" in manifest_data
        assert "plugin1" in manifest_data["manifest"]
        assert "plugin2" in manifest_data["manifest"]
        assert manifest_data["manifest"]["plugin1"] == hashlib.sha256(b"content1").hexdigest()
        assert "signature" in manifest_data
        assert "signed_at" in manifest_data
        assert manifest_data["generator_version"] == GENERATOR_VERSION
        log_test_execution("test_main_generate_signed_manifest", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_main_generate_unsigned_manifest(self, tmp_path, audit_log):
        """Test main function generating an unsigned manifest."""
        trace_id = str(uuid.uuid4())
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1.py").write_bytes(b"content1")
        output_file = tmp_path / "manifest.json"

        with patch('sys.argv', ['generate_plugin_manifest.py', str(plugin_dir), '--out', str(output_file)]), \
             patch('sys.stderr', new=MagicMock()) as mock_stderr:
            main()

        with open(output_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        assert "manifest" in manifest_data
        assert "plugin1" in manifest_data["manifest"]
        assert manifest_data["manifest"]["plugin1"] == hashlib.sha256(b"content1").hexdigest()
        assert "signature" not in manifest_data
        assert "WARNING: Manifest is NOT SIGNED" in mock_stderr.getvalue()
        log_test_execution("test_main_generate_unsigned_manifest", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_main_fail_on_unsigned(self, tmp_path, audit_log):
        """Test main function with --fail-on-unsigned flag."""
        trace_id = str(uuid.uuid4())
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1.py").write_bytes(b"content1")

        with patch('sys.argv', ['generate_plugin_manifest.py', str(plugin_dir), '--fail-on-unsigned']), \
             patch('sys.stderr', new=MagicMock()):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        log_test_execution("test_main_fail_on_unsigned", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_main_verify_manifest(self, tmp_path, mock_crypto, audit_log):
        """Test main function in verify mode."""
        trace_id = str(uuid.uuid4())
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1.py").write_bytes(b"content1")
        manifest_file = tmp_path / "manifest.json"
        key_file = tmp_path / "public_key.pem"
        key_file.write_bytes(b"mock public key")
        manifest_data = {
            "manifest": {"plugin1": hashlib.sha256(b"content1").hexdigest()},
            "files": {"plugin1": {"filename": "plugin1.py", "size_bytes": len(b"content1")}},
            "signed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator_version": GENERATOR_VERSION,
            "signature": base64.b64encode(b"mock_signature").decode("utf-8")
        }
        manifest_file.write_text(json.dumps(manifest_data))

        with patch('sys.argv', ['generate_plugin_manifest.py', '--verify', str(manifest_file), '--pubkey', str(key_file)]), \
             patch('sys.stdout', new=MagicMock()) as mock_stdout:
            main()
        assert "Manifest signature is VALID and authentic" in mock_stdout.getvalue()
        log_test_execution("test_main_verify_manifest", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_main_invalid_directory(self, tmp_path, audit_log):
        """Test main function with invalid plugin directory."""
        trace_id = str(uuid.uuid4())
        invalid_dir = tmp_path / "nonexistent"
        with patch('sys.argv', ['generate_plugin_manifest.py', str(invalid_dir)]), \
             patch('sys.stderr', new=MagicMock()):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        log_test_execution("test_main_invalid_directory", "Passed", trace_id)

# Integration test class
class TestManifestIntegration:
    """Integration tests for generate_plugin_manifest.py with related components."""

    @pytest.mark.asyncio
    async def test_manifest_generation_and_verification(self, tmp_path, mock_crypto, audit_log):
        """Test full cycle of manifest generation and verification."""
        trace_id = str(uuid.uuid4())
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "plugin1.py").write_bytes(b"content1")
        output_file = tmp_path / "manifest.json"
        private_key_file = tmp_path / "private_key.pem"
        public_key_file = tmp_path / "public_key.pem"
        private_key_file.write_bytes(b"mock private key")
        public_key_file.write_bytes(b"mock public key")

        # Generate manifest
        with patch('sys.argv', ['generate_plugin_manifest.py', str(plugin_dir), '--sign', str(private_key_file), '--out', str(output_file)]):
            main()

        # Verify manifest
        with patch('sys.argv', ['generate_plugin_manifest.py', '--verify', str(output_file), '--pubkey', str(public_key_file)]), \
             patch('sys.stdout', new=MagicMock()) as mock_stdout:
            main()
        assert "Manifest signature is VALID and authentic" in mock_stdout.getvalue()

        # Validate audit log
        with open(audit_log, "r", encoding="utf-8") as f:
            audit_content = f.read()
        assert trace_id in audit_content
        log_test_execution("test_manifest_generation_and_verification", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
