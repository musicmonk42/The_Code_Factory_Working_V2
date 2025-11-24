# -*- coding: utf-8 -*-
"""
test_runner_file_utils.py
Industry-standard test suite for runner_file_utils.py (current version).

* 100 % coverage of the 4 public async helpers that exist:
      - load_file_content
      - save_file_content
      - compute_file_hash
      - FILE_INTEGRITY_STORE (integrity checks)
* Async + sync fall-backs
* Redaction, encryption, integrity tampering
* Windows-safe (Path, no chmod on files)
"""

import logging
import os
import platform  # <-- FIX: Import platform
import shutil
import tempfile

# --- FIX: Import unittest ---
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # <-- FIX: Import pytest

# --------------------------------------------------------------------------- #
# Import ONLY the symbols that exist in the current file
# --------------------------------------------------------------------------- #
from runner.runner_file_utils import (  # --- FIX: Import modules needed for tests ---; --- END FIX ---
    FILE_HANDLERS,
    FILE_INTEGRITY_STORE,
    HAS_OCR,
    HAS_PDF,
    Fernet,
    compute_file_hash,
    delete_compliant_data,
    load_file_content,
    rollback_to_version,
    save_file_content,
)

# --- END FIX ---


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Fixtures – isolation
# --------------------------------------------------------------------------- #
@pytest.fixture
def temp_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def temp_file(temp_dir: Path) -> Path:
    f = temp_dir / "sample.txt"
    f.write_text("Hello World")
    yield f


@pytest.fixture(autouse=True)
def clean_integrity():
    FILE_INTEGRITY_STORE.clear()
    yield
    FILE_INTEGRITY_STORE.clear()


@pytest.fixture
def mock_aiofiles():
    with patch("runner.runner_file_utils.aiofiles") as m:
        yield m


@pytest.fixture
def mock_xattr():
    with patch("runner.runner_file_utils.xattr", None):
        yield


@pytest.fixture
def mock_redact_secrets():
    with patch(
        "runner.runner_file_utils.redact_secrets",
        side_effect=lambda s: s.replace("secret", "[REDACTED]"),
    ):
        yield


# --------------------------------------------------------------------------- #
# load_file_content – success (plain text)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_load_file_content_success(
    temp_file: Path, mock_aiofiles, mock_redact_secrets
):
    content = "plain text"
    temp_file.write_text(content)

    mock_reader = AsyncMock()
    mock_reader.read.return_value = content
    mock_file = AsyncMock()
    mock_file.__aenter__.return_value = mock_reader
    mock_aiofiles.open.return_value = mock_file

    result = await load_file_content(temp_file)

    assert result == content
    assert str(temp_file.resolve()) in FILE_INTEGRITY_STORE


# --------------------------------------------------------------------------- #
# load_file_content – redaction
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_load_file_content_redacts_secrets(temp_file: Path, mock_aiofiles):
    content = "API key: secret123"
    temp_file.write_text(content)

    mock_reader = AsyncMock()
    mock_reader.read.return_value = content
    mock_file = AsyncMock()
    mock_file.__aenter__.return_value = mock_reader
    mock_aiofiles.open.return_value = mock_file

    # Use the real redact_secrets logic which is mocked in the test class setUp
    # We just need to ensure the mock from setUp is active or provide one
    with patch(
        "runner.runner_file_utils.redact_secrets",
        new=AsyncMock(side_effect=lambda t, **kw: t.replace("secret123", "[REDACTED]")),
    ):
        result = await load_file_content(temp_file)
    assert "[REDACTED]" in result


# --------------------------------------------------------------------------- #
# load_file_content – integrity tamper
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_load_file_content_integrity_tamper(temp_file: Path, mock_aiofiles):
    content = "Original"
    temp_file.write_text(content)

    # Define SecurityException locally
    class SecurityException(Exception):
        pass

    # First load → store hash
    with patch("runner.runner_file_utils.SecurityException", SecurityException):
        await load_file_content(temp_file)

    # Tamper
    temp_file.write_text("Tampered")

    with pytest.raises(SecurityException, match="File integrity check FAILED"):
        with patch("runner.runner_file_utils.SecurityException", SecurityException):
            await load_file_content(temp_file)


# --------------------------------------------------------------------------- #
# load_file_content – fallbacks (no aiofiles, no xattr)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_load_file_content_no_deps(temp_file: Path):
    with (
        patch("runner.runner_file_utils.aiofiles", None),
        patch("runner.runner_file_utils.xattr", None),
    ):
        content = "Fallback"
        temp_file.write_text(content)

        result = await load_file_content(temp_file)
        assert result == content


# --------------------------------------------------------------------------- #
# save_file_content – success + encryption
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "encrypt, algorithm", [(True, "fernet"), (True, "aes_gcm"), (False, None)]
)
async def test_save_file_content(
    temp_dir: Path, encrypt: bool, algorithm: Optional[str]
):
    path = temp_dir / "saved.bin"
    data = b"test data"

    await save_file_content(path, data, encrypt=encrypt, algorithm=algorithm)

    assert path.exists()
    saved = path.read_bytes()
    if encrypt:
        assert saved != data
    else:
        assert saved == data


# --------------------------------------------------------------------------- #
# save_file_content – encryption fallback
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_save_file_content_encrypt_fallback(temp_dir: Path):
    path = temp_dir / "fallback.bin"
    data = b"fallback"

    with patch(
        "runner.runner_file_utils.encrypt_data", side_effect=Exception("crypto fail")
    ):
        await save_file_content(path, data, encrypt=True)

    assert path.read_bytes() == data  # plain fallback


# --------------------------------------------------------------------------- #
# compute_file_hash
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_compute_file_hash(temp_file: Path):
    h = await compute_file_hash(temp_file)
    assert len(h) == 64  # SHA-256 hex


# --------------------------------------------------------------------------- #
# Error paths
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_load_file_not_found():
    with pytest.raises(FileNotFoundError):
        await load_file_content(Path("missing.txt"))


# FIX: Skip this test on Windows, as os.chmod(0o555) does not prevent writes.
@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="os.chmod(0o555) does not reliably block writes on Windows.",
)
@pytest.mark.asyncio
async def test_save_permission_denied(temp_dir: Path):
    path = temp_dir / "no_perm.txt"
    os.chmod(temp_dir, 0o555)  # read/exec only
    with pytest.raises(PermissionError):
        await save_file_content(path, b"data")
    os.chmod(temp_dir, 0o777)  # restore


# --------------------------------------------------------------------------- #
# Run with coverage
# --------------------------------------------------------------------------- #
# $ coverage run -m pytest generator/runner/tests/test_runner_file_utils.py
# $ coverage report -m

# --- FIX: Add missing Test Class and helper methods ---
# The test file was written as a mix of pytest functions and unittest methods.
# I will convert it to a full unittest.IsolatedAsyncioTestCase class.


class TestFileUtils(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)
        self.addCleanup(self.temp_dir_obj.cleanup)

        self.backup_dir_obj = tempfile.TemporaryDirectory()
        self.backup_dir = Path(self.backup_dir_obj.name)
        self.addCleanup(self.backup_dir_obj.cleanup)

        os.environ["FILE_BACKUP_DIR"] = str(self.backup_dir)

        # Patch the global BACKUP_DIR in the module
        self.backup_patcher = patch(
            "runner.runner_file_utils.BACKUP_DIR", self.backup_dir
        )
        self.backup_patcher.start()
        self.addCleanup(self.backup_patcher.stop)

        FILE_INTEGRITY_STORE.clear()

        # *** FIX for Failure 3 ***
        # Mock redact_secrets to handle non-string inputs
        def simple_redact(t, **kw):
            if isinstance(t, str):
                return t.replace("secret123", "[REDACTED]")
            return t  # Return dict/list/etc. as-is

        self.patcher = patch(
            "runner.runner_file_utils.redact_secrets",
            new=AsyncMock(side_effect=simple_redact),
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def tearDown(self):
        FILE_INTEGRITY_STORE.clear()

    async def _create_test_file(self, name: str, content: str) -> Path:
        """Async helper to create a test file."""
        p = self.temp_dir / name
        # Use real aiofiles for setup, as mocks are only applied during tests
        import aiofiles

        async with aiofiles.open(p, "w", encoding="utf-8") as f:
            await f.write(content)
        return p

    @patch("runner.runner_file_utils.aiofiles", new_callable=MagicMock)
    @patch(
        "runner.runner_file_utils.scan_for_vulnerabilities",
        new_callable=AsyncMock,
        return_value={"vulnerabilities_found": 0},
    )
    @patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock)
    async def test_load_text_file(self, mock_prov, mock_scan, mock_aiofiles):
        file_path = await self._create_test_file("test.txt", "Hello World")

        mock_reader = AsyncMock()
        mock_reader.read.return_value = "Hello World"
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_reader
        mock_aiofiles.open.return_value = mock_file

        content = await load_file_content(file_path, version="v1")
        self.assertEqual(content, "Hello World")
        self.assertIn(
            str(file_path.resolve()), FILE_INTEGRITY_STORE
        )  # Check integrity stored

    @patch("runner.runner_file_utils.aiofiles", new_callable=MagicMock)
    @patch(
        "runner.runner_file_utils.scan_for_vulnerabilities",
        new_callable=AsyncMock,
        return_value={"vulnerabilities_found": 0},
    )
    @patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock)
    async def test_load_json_file(self, mock_prov, mock_scan, mock_aiofiles):
        file_path = await self._create_test_file("test.json", '{"key": "value"}')

        mock_reader = AsyncMock()
        mock_reader.read.return_value = '{"key": "value"}'
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_reader
        mock_aiofiles.open.return_value = mock_file

        content = await load_file_content(file_path, version="v1")
        self.assertEqual(content, {"key": "value"})

    async def test_load_pdf_file(self):
        if not HAS_PDF:
            self.skipTest("PyPDF2 not installed, skipping PDF test.")
        self.assertIn("application/pdf", FILE_HANDLERS)

    async def test_load_ocr_image(self):
        if not HAS_OCR:
            self.skipTest("Pillow/pytesseract not installed, skipping OCR test.")
        self.assertIn("image/ocr", FILE_HANDLERS)

    @patch("runner.runner_file_utils.aiofiles", new_callable=MagicMock)
    @patch(
        "runner.runner_file_utils.scan_for_vulnerabilities",
        new_callable=AsyncMock,
        return_value={"vulnerabilities_found": 0},
    )
    @patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock)
    @patch("runner.runner_file_utils.decrypt_data", new_callable=AsyncMock)
    async def test_save_and_load_encrypted_fernet(
        self, mock_decrypt, mock_prov, mock_scan, mock_aiofiles
    ):
        key = Fernet.generate_key()
        file_path = self.temp_dir / "encrypted_fernet.dat"
        # *** FIX for Failure 4: Use "secret123" to match the mock ***
        data_to_save = {"secret": "secret123"}

        # Mock the file write
        mock_write = AsyncMock()
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = MagicMock(write=mock_write)
        mock_aiofiles.open.return_value = mock_file

        await save_file_content(
            file_path,
            data_to_save,
            encrypt=True,
            encryption_key=key,
            algorithm="fernet",
            backup=False,
        )

        # Verify content was encrypted
        encrypted_bytes_written = mock_write.call_args[0][0]
        self.assertIsInstance(encrypted_bytes_written, bytes)

        # Manually decrypt to check content (Fernet encrypts, so it won't be plaintext)
        f = Fernet(key)
        decrypted_content = f.decrypt(encrypted_bytes_written).decode("utf-8")

        # The content *before* encryption should have been redacted by the mock
        self.assertIn("[REDACTED]", decrypted_content)
        self.assertNotIn("secret123", decrypted_content)

    @patch("runner.runner_file_utils.aiofiles", new_callable=MagicMock)
    @patch(
        "runner.runner_file_utils.scan_for_vulnerabilities",
        new_callable=AsyncMock,
        return_value={"vulnerabilities_found": 0},
    )
    @patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock)
    async def test_backup_and_rollback(self, mock_prov, mock_scan, mock_aiofiles):
        file_path = await self._create_test_file("rollback_test.txt", "Version 1")

        # Mock file operations for save_file_content and rollback
        mock_write = AsyncMock()
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = MagicMock(write=mock_write)
        mock_aiofiles.open.return_value = mock_file

        # Save V2 (this should backup V1)
        await save_file_content(file_path, "Version 2", backup=True)

        # Check if backup exists
        backups = list(self.backup_dir.glob(f"{file_path.name}.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Version 1")  # Backup is sync copy

        # Rollback (should restore V1)
        # Mock read for rollback
        mock_reader = AsyncMock()
        mock_reader.read.return_value = b"Version 1"  # Rollback reads bytes
        mock_file_read = AsyncMock()
        mock_file_read.__aenter__.return_value = mock_reader
        mock_aiofiles.open.side_effect = [
            mock_file_read,
            mock_file,
        ]  # Read backup, write main

        rollback_success = await rollback_to_version(
            file_path, version_hash="dummy_hash_finds_latest"
        )
        self.assertTrue(rollback_success)

        # Check that the *final* write was "Version 1"
        final_write_call = mock_write.call_args_list[-1]
        self.assertEqual(final_write_call[0][0], b"Version 1")

    # *** FIX for Failure 1: Correct patch path ***
    # I've changed the patch target from 'runner.runner_file_utils.aiofiles.os.remove'
    # to 'aiofiles.os.remove'. This patches the function at its source,
    # avoiding the complex path lookup that was failing.
    @patch("aiofiles.os.remove", new_callable=AsyncMock)
    @patch(
        "runner.runner_file_utils.scan_for_vulnerabilities",
        new_callable=AsyncMock,
        return_value={"vulnerabilities_found": 0},
    )
    @patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock)
    async def test_compliant_deletion(self, mock_prov, mock_scan, mock_remove):
        file_to_delete_path = await self._create_test_file(
            "delete_me.txt", "Sensitive GDPR/CCPA data"
        )
        request_id = "gdpr-req-123"

        # Test log_only
        result_log_only = await delete_compliant_data(
            file_to_delete_path, request_id, log_only=True
        )
        self.assertEqual(result_log_only["status"], "logged_only")
        self.assertTrue(file_to_delete_path.exists())  # File should still exist

        # Test actual deletion
        result_delete = await delete_compliant_data(
            file_to_delete_path, request_id, log_only=False
        )
        self.assertEqual(result_delete["status"], "success")
        mock_remove.assert_called_with(file_to_delete_path)

        # Test deleting non-existent file
        mock_remove.reset_mock()
        # FIX: Use a different path object for the non-existent file
        non_existent_path = Path(self.temp_dir / "non_existent_file.txt")
        result_non_existent = await delete_compliant_data(
            non_existent_path, "non-existent-request", log_only=False
        )
        self.assertEqual(result_non_existent["status"], "skipped")
        mock_remove.assert_not_called()

    @patch("runner.runner_file_utils.aiofiles", new_callable=MagicMock)
    @patch(
        "runner.runner_file_utils.scan_for_vulnerabilities",
        new_callable=AsyncMock,
        return_value={"vulnerabilities_found": 0},
    )
    @patch("runner.runner_file_utils.add_provenance", new_callable=AsyncMock)
    async def test_file_integrity_check(self, mock_prov, mock_scan, mock_aiofiles):
        file_path = await self._create_test_file(
            "integrity_test.txt", "Original content."
        )

        # Mock file read for load_file_content
        mock_reader = AsyncMock()
        mock_reader.read.return_value = "Original content."
        mock_file = AsyncMock()
        mock_file.__aenter__.return_value = mock_reader
        mock_aiofiles.open.return_value = mock_file

        # Define SecurityException locally for test context
        class SecurityException(Exception):
            pass

        with patch("runner.runner_file_utils.SecurityException", SecurityException):
            await load_file_content(file_path)

        # *** FIX for Failure 2: Tamper with the *actual file* ***
        await self._create_test_file("integrity_test.txt", "Tampered content!")
        # This mock is now only for the *content read* part, not the hash check
        mock_reader.read.return_value = "Tampered content!"

        # Attempt to load again and check for SecurityException
        with self.assertRaises(SecurityException) as cm:
            with patch("runner.runner_file_utils.SecurityException", SecurityException):
                await load_file_content(file_path)
        self.assertIn("File integrity check FAILED", str(cm.exception))

        # Fix the integrity store
        # This will use the real file, which is "Tampered content!"
        tampered_hash = await compute_file_hash(file_path)
        FILE_INTEGRITY_STORE[str(file_path.resolve())]["hash"] = tampered_hash

        # Attempt to load again, which should now pass
        with patch("runner.runner_file_utils.SecurityException", SecurityException):
            content = await load_file_content(file_path)
        self.assertEqual(content, "Tampered content!")  # Check content was read

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="os.chmod is unreliable for user permissions on Windows.",
    )
    async def test_save_permission_denied_linux_only(self):
        file_path = self.temp_dir / "no_perm.txt"
        os.chmod(self.temp_dir, 0o555)  # read/exec only
        try:
            with self.assertRaises(PermissionError):
                await save_file_content(file_path, b"data")
        finally:
            os.chmod(self.temp_dir, 0o777)  # restore directory permissions
