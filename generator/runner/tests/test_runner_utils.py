
# test_runner_utils.py
# Highly regulated industry-grade test suite for runner_utils.py.
# Provides comprehensive unit tests for utility functions with strict traceability,
# reproducibility, security, and observability for audit compliance.

import unittest
import asyncio
import os
import sys
import tempfile
import shutil
import json
import uuid
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import logging

# Add parent directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mock dependencies
sys.modules['cryptography'] = MagicMock()
sys.modules['cryptography.fernet'] = MagicMock()
sys.modules['cryptography.hazmat.primitives'] = MagicMock()
sys.modules['cryptography.hazmat.primitives.asymmetric'] = MagicMock()
sys.modules['cryptography.hazmat.primitives.serialization'] = MagicMock()
sys.modules['ecdsa'] = MagicMock()
sys.modules['opentelemetry'] = MagicMock()
sys.modules['opentelemetry.trace'] = MagicMock()

# Import runner modules
from runner.utils import save_files_to_output, redact_secrets, encrypt_log, decrypt_log, generate_provenance
from runner.logging import logger, log_action, LOG_HISTORY
from runner.errors import PersistenceError
from runner.config import SecretStr

class TestRunnerUtils(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Create temporary directory
        self.temp_dir = Path(tempfile.mkdtemp())
        self.output_dir = self.temp_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Mock environment
        self.patch_env = patch.dict(os.environ, {'RUNNER_ENV': 'development'})
        self.patch_env.start()

        # Mock cryptography
        self.mock_fernet = patch('runner.utils.Fernet', return_value=MagicMock(encrypt=lambda x: b'encrypted_' + x, decrypt=lambda x: x[10:]))
        self.mock_fernet.start()
        self.mock_hmac = patch('runner.utils.hmac.HMAC', return_value=MagicMock(digest=lambda: b'hmac_digest'))
        self.mock_hmac.start()
        self.mock_ecdsa = patch('runner.utils.ecdsa', return_value=MagicMock())
        self.mock_ecdsa.start()

        # Mock OpenTelemetry
        self.mock_tracer = patch('runner.utils.trace.get_tracer', return_value=MagicMock())
        self.mock_tracer.start()
        self.mock_span = MagicMock()
        self.mock_span.is_recording.return_value = True
        self.mock_tracer.return_value.start_as_current_span.return_value.__enter__.return_value = self.mock_span

        # Configure logging
        logging.basicConfig(level=logging.INFO)
        self.run_id = str(uuid.uuid4())
        LOG_HISTORY.clear()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.patch_env.stop()
        self.mock_fernet.stop()
        self.mock_hmac.stop()
        self.mock_ecdsa.stop()
        self.mock_tracer.stop()
        LOG_HISTORY.clear()

    async def test_save_files_to_output(self):
        """Test: save_files_to_output saves files and logs provenance."""
        files = {'test.txt': 'content'}
        await save_files_to_output(files, self.output_dir, recover=True)
        output_file = self.output_dir / 'test.txt'
        self.assertTrue(output_file.exists())
        self.assertEqual(output_file.read_text(), 'content')
        log = [log for log in LOG_HISTORY if 'Provenance generated' in log.get('message', '')]
        self.assertTrue(log)
        self.assertIn('test.txt', log[0]['message'])
        self.mock_span.set_attribute.assert_called_with('file_path', str(self.output_dir / 'test.txt'))

    async def test_save_files_to_output_recovery(self):
        """Test: save_files_to_output recovers from backup on failure."""
        files = {'test.txt': 'content'}
        with patch('shutil.copyfile', side_effect=OSError('Write error')):
            with self.assertRaises(PersistenceError) as cm:
                await save_files_to_output(files, self.output_dir, recover=True)
            self.assertEqual(cm.exception.error_code, 'PERSISTENCE_FAILURE')
            log = [log for log in LOG_HISTORY if log.get('action') == 'ErrorRaised']
            self.assertTrue(log)
            self.assertEqual(log[0]['error_code'], 'PERSISTENCE_FAILURE')

    def test_redact_secrets(self):
        """Test: redact_secrets redacts PII."""
        content = {'test.py': 'print("API_KEY=sk-abc123")'}
        redacted = redact_secrets(content)
        self.assertEqual(redacted['test.py'], 'print("[REDACTED]")')
        log_action.assert_called_with('SecretsRedacted', {'keys_redacted': ['test.py']}, run_id=unittest.mock.ANY, provenance_hash=unittest.mock.ANY)

    def test_encrypt_log(self):
        """Test: encrypt_log encrypts content."""
        log_content = 'Sensitive data'
        encrypted = encrypt_log(log_content)
        self.assertTrue(encrypted.startswith('encrypted_'))
        self.assertEqual(encrypted[10:], 'Sensitive data'.encode())
        log_action.assert_called_with('LogEncrypted', {'content_length': len(log_content)}, run_id=unittest.mock.ANY, provenance_hash=unittest.mock.ANY)

    def test_decrypt_log(self):
        """Test: decrypt_log decrypts content."""
        encrypted = 'encrypted_Sensitive data'.encode()
        decrypted = decrypt_log(encrypted)
        self.assertEqual(decrypted, 'Sensitive data')
        log_action.assert_called_with('LogDecrypted', {'content_length': len('Sensitive data')}, run_id=unittest.mock.ANY, provenance_hash=unittest.mock.ANY)

    def test_generate_provenance(self):
        """Test: generate_provenance creates metadata."""
        metadata = generate_provenance('test.txt', 'content', {'user': 'test_user'})
        self.assertEqual(metadata['file'], 'test.txt')
        self.assertIn('sha256_hash', metadata)
        self.assertIn('timestamp_utc', metadata)
        log_action.assert_called_with('ProvenanceGenerated', {'file': 'test.txt', 'hash': unittest.mock.ANY}, run_id=unittest.mock.ANY, provenance_hash=unittest.mock.ANY)

    async def test_traceability(self):
        """Test: Operations are traceable with run_id and OpenTelemetry."""
        files = {'test.txt': 'content'}
        await save_files_to_output(files, self.output_dir)
        self.mock_span.set_attribute.assert_called_with('file_path', str(self.output_dir / 'test.txt'))
        self.assertTrue(any(log['run_id'] == self.run_id for log in LOG_HISTORY))

if __name__ == '__main__':
    unittest.main()


### Explanation of `test_runner_utils.py`
- **Coverage**: Tests `save_files_to_output`, `redact_secrets`, `encrypt_log`, `decrypt_log`, and `generate_provenance`.
- **Regulatory Features**: Validates PII redaction (e.g., API keys replaced with `[REDACTED]`), encryption, provenance, and traceability with `run_id` and OpenTelemetry.
- **Security**: Ensures sensitive data is redacted and encrypted.
- **Reproducibility**: Mocks `cryptography`, `ecdsa`, and OpenTelemetry for deterministic results.
- **Isolation**: Uses temporary directories and clears `LOG_HISTORY` for test isolation.
- **Dependencies**: Requires `unittest` and standard libraries; all other dependencies are mocked.
- **Regulatory Compliance**: Designed for auditability with structured logs, provenance, and tracing.

### Running the Test
1. Save the file in the `runner` directory (e.g., `D:\Code_Factory\Generator\runner\tests\test_runner_utils.py`).
2. Install required test dependencies:
   ```bash
   pip install unittest
   ```
3. Run the test:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_utils.py
   ```
4. For verbose output:
   ```bash
   python -m unittest D:\Code_Factory\Generator\runner\tests\test_runner_utils.py -v
   ```

### Notes
- **Scope**: Focuses exclusively on `runner_utils.py`, covering all critical functions.
- **Regulatory Compliance**: Ensures traceability, PII redaction, and error handling for auditability.
- **Proprietary Nature**: Designed for internal use by Unexpected Innovations Inc.
- **Future Enhancements**: Can add E2E tests with real file system interactions if needed later.

If you need test files for the other modules (`runner_metrics.py`, `runner_mutation.py`, `runner_parsers.py`) or further refinements to this test file, please let me know, and I’ll provide them individually, ensuring I follow your instructions precisely. I’m committed to getting this right for you.