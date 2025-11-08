
# test_scripts_e2e.py
# Industry-grade end-to-end integration test suite for the scripts module.
# Ensures generate_plugin_manifest.py, migrate_prompts.py, and bootstrap_agent_dev.py
# work together seamlessly, with traceability, reproducibility, and security for regulated environments.

import pytest
import asyncio
import os
import json
import base64
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import logging
import sys
from jinja2 import TemplateSyntaxError

# Import functions from scripts module
from generate_plugin_manifest import compute_hash_and_size, sign_manifest, verify_manifest, main as manifest_main
from migrate_prompts import migrate_file, migrate_dir, generate_summary_report
from bootstrap_agent_dev import create_dummy_files

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
    return tmp_path_factory.mktemp("scripts_e2e_test")

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
def mock_crypto():
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

# E2E integration test class
class TestScriptsE2E:
    """End-to-end integration tests for the scripts module."""

    @pytest.mark.asyncio
    async def test_e2e_bootstrap_and_migrate(self, tmp_path, audit_log):
        """Test E2E workflow: bootstrap dummy files and migrate prompts."""
        trace_id = str(uuid.uuid4())
        # Set up directories
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        # Run bootstrap_agent_dev to create dummy files
        with patch('os.path.exists', MagicMock(return_value=False)), \
             patch('os.makedirs', MagicMock()):
            os.chdir(tmp_path)
            create_dummy_files()

        # Create a mock testgen_agent.py with PROMPT_TEMPLATES
        testgen_agent = src_dir / "testgen_agent.py"
        testgen_agent.write_text("""
from audit_log import log_action
PROMPT_TEMPLATES = {
    'generate_test': '''Generate a test for {{ code_file }}'''
}
async def run_agent():
    log_action("TestEvent", {"data": "test"})
    return PROMPT_TEMPLATES['generate_test']
""")

        # Run migrate_prompts
        with patch('builtins.open', new=MagicMock()) as mock_open:
            mock_open.side_effect = [MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: testgen_agent.read_text(), write=lambda x: None)))]
            report = await migrate_file(testgen_agent, prompts_dir, dry_run=False, verbose=True, backup=True)

        # Validate migration
        assert report["status"] == "success"
        assert (prompts_dir / "generate_test.j2").exists()
        assert (src_dir / "testgen_agent.py.bak").exists()
        with open(prompts_dir / "generate_test.j2", "r", encoding="utf-8") as f:
            assert f.read() == "Generate a test for {{ code_file }}"

        # Validate dummy file functionality
        sys.path.insert(0, str(tmp_path))
        import testgen_agent
        result = await testgen_agent.run_agent()
        assert result == "Generate a test for {{ code_file }}"

        # Validate audit log
        with open(audit_log, "r", encoding="utf-8") as f:
            audit_content = f.read()
        assert trace_id in audit_content
        assert "Created dummy" in audit_content
        log_test_execution("test_e2e_bootstrap_and_migrate", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_e2e_manifest_and_migration(self, tmp_path, mock_crypto, audit_log):
        """Test E2E workflow: migrate prompts and generate/verify plugin manifest."""
        trace_id = str(uuid.uuid4())
        # Set up directories
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        manifest_file = tmp_path / "manifest.json"
        private_key_file = tmp_path / "private_key.pem"
        private_key_file.write_bytes(b"mock private key")
        public_key_file = tmp_path / "public_key.pem"
        public_key_file.write_bytes(b"mock public key")

        # Create a plugin file with PROMPT_TEMPLATES
        plugin_file = plugin_dir / "plugin.py"
        plugin_file.write_text("""
PROMPT_TEMPLATES = {
    'test_prompt': '''Test {{ variable }}'''
}
""")

        # Run migrate_prompts
        with patch('builtins.open', new=MagicMock()) as mock_open:
            mock_open.side_effect = [MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: plugin_file.read_text(), write=lambda x: None)))]
            report = await migrate_file(plugin_file, prompts_dir, dry_run=False, verbose=True, backup=True)

        # Validate migration
        assert report["status"] == "success"
        assert (prompts_dir / "test_prompt.j2").exists()
        assert (plugin_dir / "plugin.py.bak").exists()

        # Generate manifest
        with patch('sys.argv', ['generate_plugin_manifest.py', str(plugin_dir), '--sign', str(private_key_file), '--out', str(manifest_file)]), \
             patch('sys.stdout', new=MagicMock()):
            manifest_main()

        # Validate manifest
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        assert "manifest" in manifest_data
        assert "plugin" in manifest_data["manifest"]
        assert "signature" in manifest_data
        assert manifest_data["generator_version"] == "2025.08.24-enterprise.1"

        # Verify manifest
        with patch('sys.argv', ['generate_plugin_manifest.py', '--verify', str(manifest_file), '--pubkey', str(public_key_file)]), \
             patch('sys.stdout', new=MagicMock()) as mock_stdout:
            manifest_main()
        assert "Manifest signature is VALID and authentic" in mock_stdout.getvalue()

        # Validate audit log
        with open(audit_log, "r", encoding="utf-8") as f:
            audit_content = f.read()
        assert trace_id in audit_content
        log_test_execution("test_e2e_manifest_and_migration", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_e2e_full_pipeline(self, tmp_path, mock_crypto, audit_log):
        """Test E2E workflow: bootstrap, migrate prompts, and generate/verify manifest."""
        trace_id = str(uuid.uuid4())
        # Set up directories
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        manifest_file = tmp_path / "manifest.json"
        private_key_file = tmp_path / "private_key.pem"
        private_key_file.write_bytes(b"mock private key")
        public_key_file = tmp_path / "public_key.pem"
        public_key_file.write_bytes(b"mock public key")

        # Run bootstrap_agent_dev
        with patch('os.path.exists', MagicMock(return_value=False)), \
             patch('os.makedirs', MagicMock()):
            os.chdir(tmp_path)
            create_dummy_files()

        # Create a mock testgen_agent.py with PROMPT_TEMPLATES
        testgen_agent = src_dir / "testgen_agent.py"
        testgen_agent.write_text("""
from audit_log import log_action
PROMPT_TEMPLATES = {
    'generate_test': '''Generate a test for {{ code_file }}'''
}
async def run_agent():
    log_action("TestEvent", {"data": "test"})
    return PROMPT_TEMPLATES['generate_test']
""")

        # Run migrate_prompts
        with patch('builtins.open', new=MagicMock()) as mock_open:
            mock_open.side_effect = [MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: testgen_agent.read_text(), write=lambda x: None)))]
            report = await migrate_file(testgen_agent, prompts_dir, dry_run=False, verbose=True, backup=True)

        # Validate migration
        assert report["status"] == "success"
        assert (prompts_dir / "generate_test.j2").exists()
        assert (src_dir / "testgen_agent.py.bak").exists()

        # Generate manifest for src directory
        with patch('sys.argv', ['generate_plugin_manifest.py', str(src_dir), '--sign', str(private_key_file), '--out', str(manifest_file)]), \
             patch('sys.stdout', new=MagicMock()):
            manifest_main()

        # Validate manifest
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        assert "manifest" in manifest_data
        assert "testgen_agent" in manifest_data["manifest"]
        assert "signature" in manifest_data

        # Verify manifest
        with patch('sys.argv', ['generate_plugin_manifest.py', '--verify', str(manifest_file), '--pubkey', str(public_key_file)]), \
             patch('sys.stdout', new=MagicMock()) as mock_stdout:
            manifest_main()
        assert "Manifest signature is VALID and authentic" in mock_stdout.getvalue()

        # Run the agent to ensure dummy files work
        sys.path.insert(0, str(tmp_path))
        import testgen_agent
        result = await testgen_agent.run_agent()
        assert result == "Generate a test for {{ code_file }}"

        # Validate audit log
        with open(audit_log, "r", encoding="utf-8") as f:
            audit_content = f.read()
        assert trace_id in audit_content
        assert "Created dummy" in audit_content
        assert "testgen_agent.py" in audit_content
        log_test_execution("test_e2e_full_pipeline", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_e2e_error_handling(self, tmp_path, mock_crypto, audit_log):
        """Test E2E error handling with invalid inputs."""
        trace_id = str(uuid.uuid4())
        # Invalid source file for migration
        source_file = tmp_path / "invalid.py"
        source_file.write_text("invalid syntax")
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        # Run migrate_prompts with invalid syntax
        with pytest.raises(SyntaxError):
            await migrate_file(source_file, prompts_dir, dry_run=False, verbose=True, backup=True)

        # Invalid directory for manifest generation
        invalid_dir = tmp_path / "nonexistent"
        with patch('sys.argv', ['generate_plugin_manifest.py', str(invalid_dir)]), \
             patch('sys.stderr', new=MagicMock()):
            with pytest.raises(SystemExit) as exc_info:
                manifest_main()
            assert exc_info.value.code == 1

        # Validate audit log
        with open(audit_log, "r", encoding="utf-8") as f:
            audit_content = f.read()
        assert trace_id in audit_content
        log_test_execution("test_e2e_error_handling", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
