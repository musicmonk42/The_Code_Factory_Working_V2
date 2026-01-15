"""Tests for generate_plugin_manifest.py"""
import os
import json
import tempfile
from pathlib import Path

import pytest


def test_load_private_key_from_env():
    """Test that private key can be loaded from environment variable"""
    from generate_plugin_manifest import load_private_key
    
    # Create a temporary key for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        # Generate a test Ed25519 key
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            
            # Generate key
            private_key = Ed25519PrivateKey.generate()
            key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            # Test loading from environment variable
            os.environ['TEST_SIGNING_KEY'] = key_pem.decode('utf-8')
            try:
                loaded_key = load_private_key('env:TEST_SIGNING_KEY')
                assert loaded_key is not None, "Key should be loaded from environment"
                assert isinstance(loaded_key, Ed25519PrivateKey), "Key should be Ed25519PrivateKey"
            finally:
                del os.environ['TEST_SIGNING_KEY']
                
        except ImportError:
            pytest.skip("cryptography package not available")


def test_load_private_key_from_file():
    """Test that private key can be loaded from file"""
    from generate_plugin_manifest import load_private_key
    
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            
            # Generate and save key to file
            private_key = Ed25519PrivateKey.generate()
            key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            key_path = Path(tmpdir) / "test_key.pem"
            key_path.write_bytes(key_pem)
            
            # Load from file
            loaded_key = load_private_key(str(key_path))
            assert loaded_key is not None, "Key should be loaded from file"
            assert isinstance(loaded_key, Ed25519PrivateKey), "Key should be Ed25519PrivateKey"
            
        except ImportError:
            pytest.skip("cryptography package not available")


def test_load_private_key_env_not_found():
    """Test that loading from non-existent env var raises error"""
    from generate_plugin_manifest import load_private_key
    
    with pytest.raises(ValueError, match="Environment variable.*not found"):
        load_private_key('env:NONEXISTENT_KEY')


def test_manifest_generation():
    """Test basic manifest generation"""
    from generate_plugin_manifest import compute_hash_and_size
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test plugin file
        plugin_file = Path(tmpdir) / "test_plugin.py"
        plugin_content = "# Test plugin\ndef test():\n    pass\n"
        plugin_file.write_text(plugin_content)
        
        # Compute hash
        hash_val, size = compute_hash_and_size(str(plugin_file))
        
        assert hash_val is not None, "Hash should be computed"
        assert len(hash_val) == 64, "SHA256 hash should be 64 characters"
        assert size == len(plugin_content.encode('utf-8')), "Size should match file size"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
