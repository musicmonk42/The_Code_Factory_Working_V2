# runner/file_utils.py
import os
import shutil
import tempfile
import asyncio
import aiofiles  # For async I/O (add to reqs: aiofiles)
import mimetypes  # For auto-detect
import json
import yaml
import zipfile
import gzip
import base64  # For binary
import hashlib # For integrity checks
import sys # For platform specific logic
import time # For time.time() in backup naming
from typing import Union, Dict, str, AsyncGenerator, Any, Callable, List, Optional
from pathlib import Path
# Conditional import for xattr based on OS
try:
    import xattr  # For metadata (add to reqs: xattr for Linux; win: win32security)
except ImportError:
    xattr = None
    print("Warning: 'xattr' library not found. Extended attributes for GDPR/CCPA compliance will not be set.")

from datetime import datetime, timedelta

# --- REFACTOR FIX: Changed relative 'utils' imports to absolute 'runner' imports ---
# These imports now point to the unified 'runner' foundation.
from runner.security_utils import encrypt_data, decrypt_data, redact_secrets, scan_for_vulnerabilities # Added scan_for_vulnerabilities
from runner.runner_logging import logger, add_provenance
from runner.runner_metrics import util_decorator, UTIL_ERRORS, UTIL_LATENCY # Added UTIL_LATENCY
from runner.feedback_handlers import collect_feedback  # Assume sub or init
from runner import FILE_HANDLERS, register_file_handler # Import from runner/__init__.py
# --- END REFACTOR FIX ---

# Multi-format: Add PDF/OCR handlers, and other formats
try:
    from PIL import Image # Pillow library
    import pytesseract # For OCR (add to reqs: Pillow, pytesseract)
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
    logger.warning("Pillow or pytesseract not found. OCR capabilities will be disabled.")

try:
    import PyPDF2 # For PDF text (add to reqs: PyPDF2)
    HAS_PDF = True
except ImportError:
    HAS_PDF = False
    logger.warning("PyPDF2 not found. PDF text extraction will be disabled.")

try:
    import pandas as pd # For CSV/Excel (add to reqs: pandas, openpyxl)
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    logger.warning("pandas not found. CSV/Excel/Parquet support will be disabled.")

try:
    import pyarrow.parquet as pq # For Parquet (add to reqs: pyarrow)
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False
    logger.warning("pyarrow not found. Parquet support will be disabled.")

try:
    import avro.datafile # For Avro (add to reqs: avro)
    import avro.io
    HAS_AVRO = True
except ImportError:
    HAS_AVRO = False
    logger.warning("avro not found. Avro support will be disabled.")
    
try:
    import docx # For .docx (add to reqs: python-docx)
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False
    logger.warning("python-docx not found. .docx support will be disabled.")

try:
    import magic # For binary (add to reqs: python-magic)
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False
    logger.warning("python-magic not found. Binary file type detection will be limited.")


# --- File Integrity and Provenance Store ---
# In-memory store for file hashes and versions
FILE_INTEGRITY_STORE: Dict[str, Dict[str, str]] = {} # {filepath: {'hash': '...', 'version': '...', 'last_accessed': '...'}}
BACKUP_DIR = Path(os.getenv('FILE_BACKUP_DIR', './file_backups'))
BACKUP_DIR.mkdir(exist_ok=True) # Ensure backup directory exists

async def compute_file_hash(filepath: Path) -> str:
    """Computes the SHA256 hash of a file asynchronously."""
    sha256_hash = hashlib.sha256()
    try:
        async with aiofiles.open(filepath, 'rb') as f:
            while chunk := await f.read(8192):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()
    except FileNotFoundError:
        logger.warning(f"File not found for hashing: {filepath}")
        return ""
    except Exception as e:
        logger.error(f"Error computing hash for {filepath}: {e}", exc_info=True)
        return ""

async def verify_file_integrity(filepath: Path) -> bool:
    """Verifies the integrity of a file against the stored hash."""
    filepath_str = str(filepath.resolve())
    if filepath_str not in FILE_INTEGRITY_STORE:
        logger.debug(f"No integrity record for {filepath}. Skipping verification.")
        return True # Cannot verify, assume valid for first load

    stored_hash = FILE_INTEGRITY_STORE[filepath_str]['hash']
    current_hash = await compute_file_hash(filepath)

    if stored_hash != current_hash:
        logger.warning(f"File integrity check FAILED for {filepath}. Hash mismatch.")
        add_provenance({'action': 'file_integrity_check', 'file': filepath_str, 'status': 'failed', 'stored_hash': stored_hash, 'current_hash': current_hash}, action="file_integrity_failed")
        return False
    
    logger.debug(f"File integrity check PASSED for {filepath}.")
    FILE_INTEGRITY_STORE[filepath_str]['last_accessed'] = datetime.utcnow().isoformat()
    return True

async def store_file_integrity(filepath: Path, version: str):
    """Stores the current hash and version of a file."""
    filepath_str = str(filepath.resolve())
    current_hash = await compute_file_hash(filepath)
    if current_hash:
        FILE_INTEGRITY_STORE[filepath_str] = {
            'hash': current_hash,
            'version': version,
            'last_accessed': datetime.utcnow().isoformat()
        }
        logger.debug(f"Stored integrity hash for {filepath} (Version: {version})")


# --- File Handlers (Extensible) ---
# Each handler is registered to the global FILE_HANDLERS registry from __init__.py

@register_file_handler('text/plain', ['.txt', '.md', '.py', '.js', '.css', '.html', '.sh', '.go', '.rs', '.java'])
async def load_text_file(filepath: Path) -> str:
    """Loads a plain text file."""
    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
        return await f.read()

@register_file_handler('application/json', ['.json'])
async def load_json_file(filepath: Path) -> Dict[str, Any]:
    """Loads a JSON file."""
    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
        return json.loads(await f.read())

@register_file_handler('application/x-yaml', ['.yaml', '.yml'])
async def load_yaml_file(filepath: Path) -> Dict[str, Any]:
    """Loads a YAML file."""
    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
        return yaml.safe_load(await f.read())

@register_file_handler('application/zip', ['.zip'])
async def load_zip_file(filepath: Path) -> Dict[str, str]:
    """Loads text contents from all files within a ZIP archive."""
    contents = {}
    with zipfile.ZipFile(filepath, 'r') as zf:
        for name in zf.namelist():
            if not name.endswith('/'): # Skip directories
                try:
                    contents[name] = zf.read(name).decode('utf-8')
                except (UnicodeDecodeError, zlib.error) as e:
                    logger.warning(f"Skipping binary or corrupt file in ZIP '{filepath}': {name}. Error: {e}")
    return contents

@register_file_handler('application/gzip', ['.gz'])
async def load_gzip_file(filepath: Path) -> str:
    """Loads a Gzip compressed text file."""
    async with aiofiles.open(filepath, 'rb') as f:
        decompressed_data = gzip.decompress(await f.read())
        return decompressed_data.decode('utf-8')

# --- Conditional Handlers (based on optional dependencies) ---
if HAS_PDF:
    @register_file_handler('application/pdf', ['.pdf'])
    async def load_pdf_file(filepath: Path) -> str:
        """Loads text from a PDF file using PyPDF2."""
        text = []
        try:
            async with aiofiles.open(filepath, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text.append(page.extract_text())
            return "\n".join(filter(None, text))
        except Exception as e:
            logger.error(f"Failed to extract text from PDF {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_pdf_file', type(e).__name__).inc()
            return f"[Error: Failed to extract text from PDF: {e}]"

if HAS_OCR:
    @register_file_handler('image/ocr', ['.png', '.jpg', '.jpeg', '.tiff', '.bmp'])
    async def load_image_with_ocr(filepath: Path) -> str:
        """Loads text from an image file using OCR (Tesseract)."""
        try:
            # pytesseract is synchronous, run in thread pool
            text = await asyncio.to_thread(pytesseract.image_to_string, Image.open(filepath))
            return text
        except Exception as e:
            logger.error(f"OCR failed for image {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_image_with_ocr', type(e).__name__).inc()
            return f"[Error: OCR failed for image: {e}]"

if HAS_PANDAS:
    @register_file_handler('text/csv', ['.csv'])
    async def load_csv_file(filepath: Path) -> str:
        """Loads a CSV file into a string representation using pandas."""
        try:
            df = await asyncio.to_thread(pd.read_csv, filepath)
            return df.to_string()
        except Exception as e:
            logger.error(f"Failed to load CSV {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_csv_file', type(e).__name__).inc()
            return f"[Error: Failed to load CSV: {e}]"
            
    @register_file_handler('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', ['.xlsx'])
    async def load_excel_file(filepath: Path) -> str:
        """Loads an Excel file into a string representation using pandas."""
        try:
            df = await asyncio.to_thread(pd.read_excel, filepath)
            return df.to_string()
        except Exception as e:
            logger.error(f"Failed to load Excel {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_excel_file', type(e).__name__).inc()
            return f"[Error: Failed to load Excel: {e}]"

if HAS_PYARROW and HAS_PANDAS:
    @register_file_handler('application/parquet', ['.parquet'])
    async def load_parquet_file(filepath: Path) -> str:
        """Loads a Parquet file into a string representation using pandas/pyarrow."""
        try:
            df = await asyncio.to_thread(pd.read_parquet, filepath)
            return df.to_string()
        except Exception as e:
            logger.error(f"Failed to load Parquet {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_parquet_file', type(e).__name__).inc()
            return f"[Error: Failed to load Parquet: {e}]"

if HAS_AVRO:
    @register_file_handler('application/avro', ['.avro'])
    async def load_avro_file(filepath: Path) -> Dict[str, Any]:
        """Loads records from an Avro file."""
        records = []
        try:
            with avro.datafile.DataFileReader(open(filepath, "rb"), avro.io.DatumReader()) as reader:
                for record in reader:
                    records.append(record)
            return {"schema": reader.schema, "records": records}
        except Exception as e:
            logger.error(f"Failed to load Avro {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_avro_file', type(e).__name__).inc()
            return {"error": f"Failed to load Avro: {e}"}

if HAS_DOCX:
    @register_file_handler('application/vnd.openxmlformats-officedocument.wordprocessingml.document', ['.docx'])
    async def load_docx_file(filepath: Path) -> str:
        """Loads text from a .docx file."""
        try:
            doc = await asyncio.to_thread(docx.Document, filepath)
            return "\n".join([p.text for p in doc.paragraphs])
        except Exception as e:
            logger.error(f"Failed to load .docx {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_docx_file', type(e).__name__).inc()
            return f"[Error: Failed to load .docx: {e}]"

if HAS_MAGIC:
    @register_file_handler('application/octet-stream', []) # Generic binary fallback
    async def load_binary_file_as_base64(filepath: Path) -> Dict[str, str]:
        """Loads a binary file as base64 and includes file type info."""
        try:
            async with aiofiles.open(filepath, 'rb') as f:
                content = await f.read()
            
            file_type = await asyncio.to_thread(magic.from_buffer, content, mime=True)
            file_type_desc = await asyncio.to_thread(magic.from_buffer, content)

            return {
                "mime_type": file_type,
                "description": file_type_desc,
                "content_base64": base64.b64encode(content).decode('utf-8')
            }
        except Exception as e:
            logger.error(f"Failed to load binary file {filepath}: {e}", exc_info=True)
            UTIL_ERRORS.labels('load_binary_file', type(e).__name__).inc()
            return {"error": f"Failed to load binary file: {e}"}

# --- Main File Utility Functions ---
@util_decorator
async def load_file_content(filepath: Union[str, Path], version: str = "latest", encoding: str = "utf-8") -> Any:
    """
    Loads file content using the appropriate handler based on mime type or extension.
    Includes integrity verification, redaction, and vulnerability scanning.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        logger.error(f"File not found: {filepath}")
        UTIL_ERRORS.labels('load_file_content', 'file_not_found').inc()
        raise FileNotFoundError(f"File not found: {filepath}")

    # 1. Integrity Check
    if not await verify_file_integrity(filepath):
        # Integrity check failed
        UTIL_ERRORS.labels('load_file_content', 'integrity_check_failed').inc()
        # In a high-security setting, this should raise an exception.
        # For resilience, we log a critical error but may proceed.
        logger.critical(f"File integrity check FAILED for {filepath}. Loading content, but it may be compromised.")
        # Raise exception to stop loading compromised file
        raise SecurityException(f"File integrity check FAILED for {filepath}. Halting load.")

    # 2. Find Handler
    mime_type, _ = mimetypes.guess_type(filepath)
    handler = FILE_HANDLERS.get(mime_type) if mime_type else None
    
    if not handler:
        # Fallback to extension matching
        ext = filepath.suffix.lower()
        for mime, exts in FILE_HANDLERS.get_extensions().items():
            if ext in exts:
                handler = FILE_HANDLERS.get(mime)
                mime_type = mime
                break

    if not handler:
        # Final fallback to binary handler if magic is available
        if HAS_MAGIC:
            handler = FILE_HANDLERS.get('application/octet-stream')
            mime_type = 'application/octet-stream'
        else:
            logger.error(f"No file handler found for {filepath} (Mime: {mime_type}).")
            UTIL_ERRORS.labels('load_file_content', 'no_handler_found').inc()
            raise TypeError(f"No file handler found for {filepath}")

    logger.debug(f"Loading {filepath} using handler for mime_type: {mime_type}")

    # 3. Load Content
    try:
        content = await handler(filepath)
    except Exception as e:
        logger.error(f"File handler failed to load {filepath}: {e}", exc_info=True)
        UTIL_ERRORS.labels('load_file_content', 'handler_load_failed').inc()
        raise
    
    # 4. Security Processing (Redaction & Scanning)
    # Redact secrets from text-based content
    if isinstance(content, (str, dict, list)):
        redacted_content = await redact_secrets(content, method='nlp_presidio' if PRESIDIO_AVAILABLE else 'regex_basic')
    else:
        redacted_content = content # Cannot redact binary content

    # Scan for vulnerabilities
    scan_results = await scan_for_vulnerabilities(filepath, scan_type='code' if mime_type in ['text/plain', 'application/json', 'application/x-yaml'] else 'data')
    if scan_results['vulnerabilities_found'] > 0:
        logger.warning(f"Vulnerabilities found in {filepath}: {scan_results['details']}")
        # Optionally, raise exception in high-security mode
        # raise SecurityException(f"Vulnerabilities found in {filepath}. Halting.")
    
    # 5. Provenance and Integrity Store
    await store_file_integrity(filepath, version)
    add_provenance({
        'action': 'load_file',
        'file': str(filepath.resolve()),
        'version': version,
        'handler': mime_type,
        'scan_findings': scan_results['vulnerabilities_found']
    }, action="file_load_success")
    
    return redacted_content


@util_decorator
async def create_backup(filepath: Path) -> Path:
    """Creates a versioned backup of a file in the BACKUP_DIR."""
    if not filepath.exists():
        raise FileNotFoundError(f"Cannot create backup. File not found: {filepath}")
    
    timestamp = datetime.now().strftime("%Y%m%d%HM%S%f")
    backup_filename = f"{filepath.name}.{timestamp}.bak"
    backup_path = BACKUP_DIR / backup_filename
    
    await asyncio.to_thread(shutil.copy, filepath, backup_path)
    
    logger.info(f"Created backup for {filepath} at {backup_path}")
    add_provenance({'action': 'create_backup', 'original_file': str(filepath), 'backup_file': str(backup_path)}, action="file_backup_created")
    return backup_path

@util_decorator
async def rollback_to_version(filepath: Path, version_hash: str) -> bool:
    """
    Rolls back a file to a specific version from the backup directory.
    (Note: This implementation is simplified; a real system would use a proper version hash
    to find the correct backup file.)
    """
    # This is a simplified rollback logic. A real implementation would need
    # to find the backup corresponding to the `version_hash`.
    # For now, we find the *most recent* backup.
    
    backups = sorted(BACKUP_DIR.glob(f"{filepath.name}.*.bak"), key=os.path.getmtime, reverse=True)
    if not backups:
        logger.warning(f"No backups found for {filepath}. Cannot rollback.")
        return False
        
    latest_backup = backups[0]
    try:
        # Atomic rollback
        async with aiofiles.open(latest_backup, 'rb') as f_src:
            content = await f_src.read()
        
        # Use tempfile + os.replace for atomic write
        temp_file = None
        temp_fd, temp_path_str = tempfile.mkstemp(dir=filepath.parent)
        temp_path = Path(temp_path_str)
        
        async with aiofiles.open(temp_path, 'wb') as f_dst:
            await f_dst.write(content)
        
        os.replace(temp_path, filepath) # Atomic rename/replace
        
        logger.info(f"Successfully rolled back {filepath} to backup version: {latest_backup.name}")
        await store_file_integrity(filepath, version=latest_backup.name)
        add_provenance({'action': 'rollback_file', 'file': str(filepath), 'rolled_back_to': str(latest_backup)}, action="file_rollback_success")
        return True
    except Exception as e:
        logger.error(f"Failed to rollback {filepath}: {e}", exc_info=True)
        UTIL_ERRORS.labels('rollback_to_version', type(e).__name__).inc()
        if temp_path and temp_path.exists():
            os.remove(temp_path) # Clean up temp file on failure
        return False
    finally:
        if 'temp_file' in locals() and temp_file:
            temp_file.close() # Close file descriptor

@util_decorator
async def save_file_content(filepath: Union[str, Path], content: Union[str, bytes, Dict, List], 
                            encoding: str = "utf-8", 
                            encrypt: bool = False, encryption_key: Optional[bytes] = None,
                            backup: bool = True,
                            compliance_metadata: Optional[Dict[str, Any]] = None) -> Path:
    """
    Saves content to a file with encryption, redaction, and compliance metadata.
    Handles atomic writes and backups.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    # 1. Create backup if file exists
    if backup and filepath.exists():
        await create_backup(filepath)

    # 2. Serialize content (JSON/YAML)
    if isinstance(content, (dict, list)):
        if filepath.suffix.lower() in ['.yaml', '.yml']:
            content_bytes = yaml.dump(content, indent=2).encode(encoding)
        else: # Default to JSON
            content_bytes = json.dumps(content, indent=2).encode(encoding)
    elif isinstance(content, str):
        content_bytes = content.encode(encoding)
    elif isinstance(content, bytes):
        content_bytes = content
    else:
        raise TypeError(f"Unsupported content type for saving: {type(content)}")

    # 3. Redact secrets before saving (if text)
    try:
        content_str_for_redaction = content_bytes.decode(encoding)
        redacted_content_bytes = (await redact_secrets(content_str_for_redaction)).encode(encoding)
    except UnicodeDecodeError:
        # It's binary data, don't redact
        redacted_content_bytes = content_bytes

    # 4. Encrypt if requested
    if encrypt:
        if not encryption_key:
            raise ValueError("encryption_key is required when encrypt=True.")
        final_content_bytes = await encrypt_data(redacted_content_bytes, encryption_key, algorithm='fernet')
    else:
        final_content_bytes = redacted_content_bytes
        
    # 5. Atomic Write
    temp_file = None
    try:
        # Use tempfile in the same directory for atomic os.replace
        temp_fd, temp_path_str = tempfile.mkstemp(dir=filepath.parent, prefix=f"{filepath.name}.")
        temp_path = Path(temp_path_str)
        
        async with aiofiles.open(temp_path, 'wb') as f:
            await f.write(final_content_bytes)
        
        os.replace(temp_path, filepath) # Atomic rename/replace
        
    except Exception as e:
        logger.error(f"Failed to write file atomically to {filepath}: {e}", exc_info=True)
        if 'temp_path' in locals() and temp_path.exists():
            os.remove(temp_path) # Clean up temp file on failure
        UTIL_ERRORS.labels('save_file_content', 'atomic_write_failed').inc()
        raise
    finally:
        if 'temp_file' in locals() and temp_file:
            temp_file.close() # Close file descriptor

    # 6. Apply Compliance Metadata (e.g., xattr for GDPR/CCPA)
    if xattr and compliance_metadata:
        try:
            attrs = xattr.xattr(filepath)
            if 'retention_days' in compliance_metadata:
                expiry = (datetime.now() + timedelta(days=compliance_metadata['retention_days'])).isoformat()
                attrs.set('user.compliance.retention_expiry', expiry.encode('utf-8'))
            if 'data_subject_id' in compliance_metadata:
                attrs.set('user.compliance.data_subject_id', str(compliance_metadata['data_subject_id']).encode('utf-8'))
            logger.debug(f"Applied compliance metadata (xattr) to {filepath}")
        except Exception as e:
            logger.warning(f"Failed to set extended attributes (xattr) on {filepath}: {e}. (This is common on filesystems that don't support it, like FAT32 or some network shares.)")

    # 7. Store integrity
    await store_file_integrity(filepath, version=datetime.now().isoformat())
    
    add_provenance({
        'action': 'save_file',
        'file': str(filepath.resolve()),
        'bytes_written': len(final_content_bytes),
        'encrypted': encrypt,
        'backup_created': backup and filepath.exists()
    }, action="file_save_success")
    
    return filepath

@util_decorator
async def delete_compliant_data(filepath: Union[str, Path], request_id: str, log_only: bool = False) -> Dict[str, Any]:
    """
    Handles compliant deletion of data (e.g., GDPR Right to be Forgotten).
    Logs the deletion request and, if not log_only, securely deletes the file.
    """
    filepath = Path(filepath)
    delete_log = {
        'action': 'delete_data_request',
        'request_id': request_id,
        'file_target': str(filepath.resolve()),
        'timestamp': datetime.utcnow().isoformat(),
        'status': 'pending'
    }
    
    if not filepath.exists():
        delete_log['status'] = 'skipped'
        delete_log['message'] = 'File not found.'
        logger.warning(f"Deletion request {request_id}: File {filepath} not found.", extra=delete_log)
        add_provenance(delete_log, action="data_delete_skip")
        return delete_log

    if log_only:
        delete_log['status'] = 'logged_only'
        delete_log['message'] = 'Deletion logged but not executed (log_only=True).'
        add_provenance(delete_log, action="data_delete_log_only")
        logger.info(f"Deletion request {request_id} for {filepath} logged.", extra=delete_log)
        return delete_log

    try:
        # Secure deletion (shredding) is complex and OS-dependent.
        # For this, we'll perform a simple os.remove and log the action.
        # In a real high-security setup, `srm` or a secure erase utility would be called via process_utils.
        
        # 1. Create a final backup/snapshot before deletion if required by policy
        await create_backup(filepath.parent / f"{filepath.name}.PRE_DELETE.{request_id}")
        
        # 2. Perform deletion
        await aiofiles.os.remove(filepath)
        
        # 3. Clear integrity store
        if str(filepath.resolve()) in FILE_INTEGRITY_STORE:
            del FILE_INTEGRITY_STORE[str(filepath.resolve())]
            
        delete_log['status'] = 'success'
        delete_log['message'] = 'File deleted successfully.'
        add_provenance(delete_log, action="data_delete_success")
        logger.info(f"Compliant deletion {request_id} for {filepath} completed.", extra=delete_log)
    except Exception as e:
        delete_log['status'] = 'failed'
        delete_log['message'] = str(e)
        logger.error(f"Compliant deletion {request_id} for {filepath} FAILED: {e}", exc_info=True, extra=delete_log)
        UTIL_ERRORS.labels('delete_compliant_data', type(e).__name__).inc()
        add_provenance(delete_log, action="data_delete_fail")

    return delete_log


# --- Test Suite ---
import unittest
from hypothesis import given, strategies as st
from unittest.mock import patch, MagicMock

# Define SecurityException for the test
class SecurityException(Exception):
    pass

class TestFileUtils(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_file_utils_temp")
        self.test_dir.mkdir(exist_ok=True)
        os.environ['FILE_BACKUP_DIR'] = str(self.test_dir / "backups")
        global BACKUP_DIR
        BACKUP_DIR = Path(os.getenv('FILE_BACKUP_DIR'))
        BACKUP_DIR.mkdir(exist_ok=True)
        # Clear integrity store for clean tests
        FILE_INTEGRITY_STORE.clear()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        del os.environ['FILE_BACKUP_DIR']

    async def _create_test_file(self, name: str, content: str) -> Path:
        """Async helper to create a test file."""
        p = self.test_dir / name
        async with aiofiles.open(p, 'w', encoding='utf-8') as f:
            await f.write(content)
        return p

    @asyncio.coroutine
    async def test_load_text_file(self):
        file_path = await self._create_test_file("test.txt", "Hello World")
        content = await load_file_content(file_path, version="v1")
        self.assertEqual(content, "Hello World")
        self.assertIn(str(file_path.resolve()), FILE_INTEGRITY_STORE) # Check integrity stored

    @asyncio.coroutine
    async def test_load_json_file(self):
        file_path = await self._create_test_file("test.json", '{"key": "value"}')
        content = await load_file_content(file_path, version="v1")
        self.assertEqual(content, {"key": "value"})

    @asyncio.coroutine
    async def test_load_pdf_file(self):
        if not HAS_PDF:
            self.skipTest("PyPDF2 not installed, skipping PDF test.")
        # This requires a real PDF file. Mocking PyPDF2 is complex.
        # For this test, we check that the handler is registered.
        self.assertIn('application/pdf', FILE_HANDLERS)

    @asyncio.coroutine
    async def test_load_ocr_image(self):
        if not HAS_OCR:
            self.skipTest("Pillow/pytesseract not installed, skipping OCR test.")
        # This requires a real image file and Tesseract installed.
        # For this test, we check that the handler is registered.
        self.assertIn('image/ocr', FILE_HANDLERS)

    @asyncio.coroutine
    async def test_save_and_load_encrypted_fernet(self):
        key = Fernet.generate_key()
        file_path = self.test_dir / "encrypted_fernet.dat"
        data_to_save = {"secret": "my_password_123"}
        
        await save_file_content(file_path, data_to_save, encrypt=True, encryption_key=key, backup=False)
        
        # Verify content is encrypted
        async with aiofiles.open(file_path, 'rb') as f:
            raw_content = await f.read()
        self.assertNotEqual(raw_content, json.dumps(data_to_save).encode())

        # Decrypt using the main load function (which handles decryption via security_utils)
        # Note: load_file_content doesn't decrypt by default. We need to call decrypt_data.
        decrypted_content_str = await decrypt_data(raw_content, key, algorithm='fernet')
        decrypted_data = json.loads(decrypted_content_str)
        
        # We must test redaction on save. The saved data should be redacted BEFORE encryption.
        # The test above tests encryption, but let's test redaction *within* save_file_content.
        # The current save_file_content redacts *then* encrypts.
        self.assertIn("secret", decrypted_data)
        self.assertIn("[REDACTED]", decrypted_data['secret']) # Assuming 'my_password_123' is redacted

    @asyncio.coroutine
    async def test_save_and_load_encrypted_aes(self):
        key = os.urandom(32) # AES 256-bit key
        file_path = self.test_dir / "encrypted_aes.dat"
        data_to_save = "AES test data"
        
        await save_file_content(file_path, data_to_save, encrypt=True, encryption_key=key, algorithm='aes_cbc', backup=False)
        
        async with aiofiles.open(file_path, 'rb') as f:
            raw_content = await f.read()
        self.assertNotEqual(raw_content, data_to_save.encode())
        
        decrypted_content = await decrypt_data(raw_content, key, algorithm='aes_cbc')
        self.assertEqual(data_to_save, decrypted_content)

    @asyncio.coroutine
    async def test_backup_and_rollback(self):
        file_path = await self._create_test_file("rollback_test.txt", "Version 1")
        
        # Save V2 (this should backup V1)
        await save_file_content(file_path, "Version 2", backup=True)
        self.assertEqual(file_path.read_text(), "Version 2")
        
        # Check if backup exists
        backups = list(BACKUP_DIR.glob(f"{file_path.name}.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Version 1")

        # Rollback (should restore V1)
        # Using a simplified hash for this test
        rollback_success = await rollback_to_version(file_path, version_hash="dummy_hash_finds_latest")
        self.assertTrue(rollback_success)
        self.assertEqual(file_path.read_text(), "Version 1")

    @asyncio.coroutine
    async def test_compliant_deletion(self):
        file_to_delete_path = await self._create_test_file("delete_me.txt", "Sensitive GDPR/CCPA data")
        request_id = "gdpr-req-123"
        
        # Test log_only
        result_log_only = await delete_compliant_data(file_to_delete_path, request_id, log_only=True)
        self.assertEqual(result_log_only['status'], 'logged_only')
        self.assertTrue(file_to_delete_path.exists()) # File should still exist

        # Test actual deletion
        result_delete = await delete_compliant_data(file_to_delete_path, request_id, log_only=False)
        self.assertEqual(result_delete['status'], 'success')
        self.assertFalse(file_to_delete_path.exists()) # File should be deleted

        # Test deleting non-existent file
        result_non_existent = await delete_compliant_data(file_to_delete_path, "non-existent-request", log_only=False)
        self.assertEqual(result_non_existent['status'], 'skipped')

    @asyncio.coroutine
    async def test_file_integrity_check(self):
        file_path = await self._create_test_file("integrity_test.txt", "Original content.")
        first_hash = await compute_file_hash(file_path)
        
        # Load the file to store its integrity data
        await load_file_content(file_path)
        
        # Modify the file content directly (simulating tampering)
        async with aiofiles.open(file_path, 'a') as f:
            await f.write("Tampered content!")

        # Attempt to load again and check for warning
        with self.assertLogs(logger.name, level='WARNING') as cm:
            await load_file_content(file_path)
            self.assertIn("File integrity check FAILED", cm.output[0])
            self.assertIn("Loading content, but it may be compromised.", cm.output[1])
            # NOTE: This test assumes SecurityException is defined in this scope
            # In a real run, this would raise SecurityException if defined,
            # or just log the warning if not.
            # For this test, we check the warning.

        # Fix the integrity store and try again
        await store_file_integrity(file_path, version="v2_tampered")
        with self.assertLogs(logger.name, level='DEBUG') as cm_debug:
            await load_file_content(file_path)
            # Check for the *absence* of a warning
            self.assertFalse(any("File integrity check FAILED" in log for log in cm_debug.output))
            # Check for the presence of a PASS
            self.assertTrue(any("File integrity check PASSED" in log for log in cm_debug.output))