# runner/security_utils.py
import re
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key, PrivateFormat, NoEncryption, Encoding
from cryptography.hazmat.backends import default_backend
# FIX: Added Tuple to the typing import list
from typing import Any, Dict, List, Callable, Optional, Union, Iterable, Pattern, Tuple
import os
import asyncio
import aiohttp
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path # Added for Path objects
import shutil # Added for the teardown in the TestSecurity class
import getpass # <-- ADDED
import json
import hashlib
from collections import deque
import sys # For checking module status for conditional imports
from functools import wraps # [NEW] Added for no-op decorator

# Conditional import for xattr based on OS
try:
    import xattr  # For metadata (compliance expiration)
except ImportError:
    xattr = None
    logging.warning("Warning: 'xattr' library not found. Extended attributes for compliance will not be set.")

# --- REFACTOR FIX: Corrected imports ---
# FIX: Define logger at the top, using __name__
logger = logging.getLogger(__name__)

# FIX: Defer imports that cause circular dependency
# Import registries from the new 'runner' package's __init__.py
# (We assume these registries are defined in runner/__init__.py or a similar central location)
# For this file, we will define them locally if they can't be imported, to ensure startup.
try:
    from runner import REDACTORS, ENCRYPTORS, DECRYPTORS, register_redactor, register_encryptor, register_decryptor
except ImportError:
    logger.warning("Could not import registries from 'runner'. Defining local registries.")
    REDACTORS: Dict[str, Callable[..., Any]] = {}
    ENCRYPTORS: Dict[str, Callable[..., Any]] = {}
    DECRYPTORS: Dict[str, Callable[..., Any]] = {}
    def register_redactor(name: str, func: Callable):
        REDACTORS[name] = func
    def register_encryptor(name: str, func: Callable):
        ENCRYPTORS[name] = func
    def register_decryptor(name: str, func: Callable):
        DECRYPTORS[name] = func

from runner.feedback_handlers import collect_feedback
# --- END REFACTOR FIX ---

# [NEW] State for lazy Presidio loading
# FIX: Removed extra ']' brackets
_PRESIDIO_ANALYZER_ENGINE: Optional[Any] = None
_PRESIDIO_ANONYMIZER_ENGINE: Optional[Any] = None
_PRESIDIO_AVAILABLE: bool = False

# FIX: Define a safe metric getter for the fail-safe metrics
def _get_metric(metric_name: str, default_metric: Callable):
    """Dynamically imports a metric or returns a no-op substitute."""
    try:
        # FIX: The local import is here, which allows Python to load the module first.
        import runner.runner_metrics as metrics
        return getattr(metrics, metric_name, default_metric)
    except ImportError:
        return default_metric
    except Exception:
        return default_metric

class NoOpCounter:
    """A placeholder counter for when metrics cannot be imported due to circular dependencies."""
    def labels(self, *args, **kwargs): return self
    def inc(self, n: float = 1.0): pass

# FIX: Now defines a safe metric accessor at module level, breaking the import error
UTIL_ERRORS = _get_metric('UTIL_ERRORS', NoOpCounter())


# [NEW] Function to lazily load Presidio/spaCy dependencies
def _load_presidio_engine() -> bool:
    """Loads Presidio/spaCy only when first called."""
    global _PRESIDIO_ANALYZER_ENGINE, _PRESIDIO_ANONYMIZER_ENGINE, _PRESIDIO_AVAILABLE
    if _PRESIDIO_AVAILABLE:
        return True
    
    # FIX: CRITICAL CHECK: Use a more robust check for pytest
    # This checks for the module, env vars, etc., to avoid loading
    # heavy NLP dependencies during Pytest collection on Windows.
    TESTING = (
        os.getenv("TESTING") == "1"
        or "pytest" in sys.modules
        or os.getenv("PYTEST_CURRENT_TEST") is not None
        or os.getenv("PYTEST_ADDOPTS") is not None
    )
    if TESTING:
        logger.warning("Skipping heavy NLP/ML dependency load (Presidio/SpaCy) during Pytest session to prevent Windows DLL crash.")
        return False
    
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        
        # NOTE: This still requires torch/spacy libraries to be loadable.
        _nlp_provider_config = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]
        }
        _nlp_provider = NlpEngineProvider(nlp_configuration=_nlp_provider_config)
        _PRESIDIO_ANALYZER_ENGINE = AnalyzerEngine(nlp_engine=_nlp_provider.create_engine())
        _PRESIDIO_ANONYMIZER_ENGINE = AnonymizerEngine()
        _PRESIDIO_AVAILABLE = True
        logger.info("Presidio AnalyzerEngine (NLP) loaded successfully for advanced redaction.")
        return True
    except ImportError:
        _PRESIDIO_AVAILABLE = False
        logger.warning("Presidio or spaCy not found. ML-based redaction unavailable.")
    except Exception as e:
        _PRESIDIO_AVAILABLE = False
        logger.error(f"Error loading Presidio/spaCy model: {e}. ML-based redaction unavailable.", exc_info=True)
    return False

# [NEW] No-op fallbacks for metrics/decorators if not found
def util_decorator(func: Callable):
    """No-op decorator fallback."""
    @wraps(func)
    async def _aw(*a, **k): return await func(*a, **k)
    @wraps(func)
    def _sw(*a, **k): return func(*a, **k)
    return _aw if asyncio.iscoroutinefunction(func) else _sw

def detect_anomaly(*a, **k):
    """No-op anomaly detection fallback."""
    logger.debug("detect_anomaly called, but no-op implementation is in use.")
    return False


# External secret managers
try:
    import hvac # Hashicorp Vault (add to reqs: hvac)
    HAS_VAULT = True
except ImportError:
    hvac = None
    HAS_VAULT = False
    logger.warning("hvac not found. Hashicorp Vault integration will be unavailable.")

try:
    import boto3 # AWS (add to reqs: boto3)
    from botocore.exceptions import ClientError as BotoClientError
    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    HAS_BOTO3 = False
    logger.warning("boto3 not found. AWS Secrets Manager/KMS integration will be unavailable.")

try:
    import pkcs11 # For HSM (add to reqs: python-pkcs11)
    from pkcs11.constants import ObjectClass, KeyType, Mechanism
    from pkcs11.exceptions import PKCS11Error
    HAS_PKCS11 = True
except ImportError:
    pkcs11 = None
    HAS_PKCS11 = False
    logger.warning("python-pkcs11 not found. HSM integration will be unavailable.")


# --- Regex Redactors (Default fallback and custom patterns) ---
# Default patterns, always available
def regex_basic_redactor(data: Any, patterns: Optional[List[Pattern]] = None) -> Any:
    """Recursively redacts data using basic regex patterns."""
    if patterns is None:
        patterns = [
            re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), # Email
            re.compile(r'\b(?:\d{3}[-.]?){2}\d{4}\b'), # Phone
        ]

    if isinstance(data, str):
        for pattern in patterns:
            data = pattern.sub('[REDACTED]', data)
        return data
    elif isinstance(data, dict):
        return {k: regex_basic_redactor(v, patterns) for k, v in data.items()}
    elif isinstance(data, list):
        return [regex_basic_redactor(item, patterns) for item in data]
    return data

# NLP-based redactor (if Presidio is available)
def nlp_presidio_redactor(data: Any, patterns: Optional[List[Pattern]] = None) -> Any:
    """Recursively redacts data using Presidio NLP, falling back to regex for non-strings."""
    # FIX: Ensure Presidio is loaded only when this function is called
    if not _PRESIDIO_AVAILABLE:
        _load_presidio_engine()
        
    if not _PRESIDIO_AVAILABLE or not _PRESIDIO_ANALYZER_ENGINE or not _PRESIDIO_ANONYMIZER_ENGINE:
        logger.warning("Presidio/NLP redactor called but not available. Falling back to basic regex.")
        return regex_basic_redactor(data, patterns) # Fallback to regex if Presidio failed

    if isinstance(data, str):
        try:
            results = _PRESIDIO_ANALYZER_ENGINE.analyze(text=data, language='en')
            anonymized_result = _PRESIDIO_ANONYMIZER_ENGINE.anonymize(text=data, analyzer_results=results)
            return anonymized_result.text
        except Exception as e:
            logger.error(f"Presidio redaction failed: {e}. Falling back to basic regex for this item.", exc_info=True)
            # FIX: Get metric safely
            UTIL_ERRORS.labels(func='nlp_presidio_redactor', type=type(e).__name__).inc()
            return regex_basic_redactor(data, patterns) # Fallback on error
    elif isinstance(data, dict):
        return {k: nlp_presidio_redactor(v, patterns) for k, v in data.items()}
    elif isinstance(data, list):
        return [nlp_presidio_redactor(item, patterns) for item in data]
    return data

# Register the redactors
register_redactor('regex_basic', regex_basic_redactor)
# FIX: Register the NLP redactor. The function itself will handle lazy-loading
# and skip if Presidio is not available. This prevents _load_presidio_engine()
# from being called at import time, which fixes the torch/pytest DLL error.
register_redactor('nlp_presidio', nlp_presidio_redactor)


# --- Encryption Providers ---
def fernet_encrypt_decrypt(data: Union[str, bytes], key: bytes, mode: str) -> Union[bytes, str]:
    """Symmetric encryption/decryption using Fernet."""
    f = Fernet(key)
    if mode == 'encrypt':
        data_bytes = data.encode('utf-8') if isinstance(data, str) else data
        return f.encrypt(data_bytes)
    elif mode == 'decrypt':
        if not isinstance(data, bytes):
            raise TypeError("Fernet decryption requires bytes input.")
        decrypted_bytes = f.decrypt(data)
        return decrypted_bytes.decode('utf-8') # Assume decrypted data is utf-8 string
    raise ValueError("Invalid mode for Fernet: must be 'encrypt' or 'decrypt'.")

def aes_cbc_encrypt_decrypt(data: Union[str, bytes], key: bytes, mode: str) -> Union[bytes, str]:
    """Symmetric encryption/decryption using AES-CBC with PKCS7 padding."""
    if len(key) not in [16, 24, 32]:
        raise ValueError("AES key must be 16, 24, or 32 bytes.")
    
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_padding

    if mode == 'encrypt':
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        padder = sym_padding.PKCS7(algorithms.AES.block_size).padder()
        
        data_bytes = data.encode('utf-8') if isinstance(data, str) else data
        padded_data = padder.update(data_bytes) + padder.finalize()
        return iv + encryptor.update(padded_data) + encryptor.finalize() # Prepend IV to ciphertext
    elif mode == 'decrypt':
        if not isinstance(data, bytes) or len(data) <= 16:
            raise TypeError("AES decryption requires bytes input with IV (must be > 16 bytes).")
        
        iv = data[:16]
        ciphertext = data[16:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        unpadder = sym_padding.PKCS7(algorithms.AES.block_size).unpadder()

        decrypted_padded_bytes = decryptor.update(ciphertext) + decryptor.finalize()
        decrypted_bytes = unpadder.update(decrypted_padded_bytes) + unpadder.finalize()
        return decrypted_bytes.decode('utf-8')
    raise ValueError("Invalid mode for AES: must be 'encrypt' or 'decrypt'.")

# Register encryption providers
register_encryptor('fernet', fernet_encrypt_decrypt)
register_encryptor('aes_cbc', aes_cbc_encrypt_decrypt)
# FIX: Corrected typo in function name registration
register_decryptor('fernet', fernet_encrypt_decrypt)
register_decryptor('aes_cbc', aes_cbc_encrypt_decrypt)


# --- Public-facing Security Functions ---
@util_decorator
async def redact_secrets(data: Any, method: Optional[str] = None, patterns: Optional[List[Pattern]] = None) -> Any:
    """
    Redacts sensitive information from data using the specified method.
    Defaults to 'nlp_presidio' if available, otherwise 'regex_basic'.
    """
    # FIX: Lazy import to break circular dependency
    from runner.runner_logging import log_audit_event
    from runner.runner_metrics import UTIL_ERRORS as MetricsUtilErrors # FIX: Deferred import
    
    if method:
        redactor = REDACTORS.get(method)
        if not redactor:
            logger.warning(f"Redactor '{method}' not found. Defaulting to 'nlp_presidio' (if avail) or 'regex_basic'.")
            MetricsUtilErrors.labels(func='redact_secrets', type='invalid_method').inc()
            method = None # Fallback

    if not method:
        method = 'nlp_presidio' if _PRESIDIO_AVAILABLE or _load_presidio_engine() else 'regex_basic'
    
    redactor = REDACTORS.get(method, regex_basic_redactor) # Get the redactor function

    logger.debug(f"Redacting secrets using method: {method}")
    
    # Redactors are sync, run in thread pool to avoid blocking
    result = await asyncio.to_thread(redactor, data, patterns)
    
    # [FIX] Replaced add_provenance with log_audit_event
    await log_audit_event(action="security_redact", data={'method': method, 'data_type': str(type(data))})
    return result

@util_decorator
async def encrypt_data(data: Union[str, bytes], key: bytes, algorithm: str = 'fernet') -> bytes:
    """
    Encrypts data using the specified symmetric algorithm.
    Returns encrypted bytes.
    """
    # FIX: Lazy import to break circular dependency
    from runner.runner_logging import log_audit_event
    from runner.runner_metrics import UTIL_ERRORS as MetricsUtilErrors # FIX: Deferred import
    
    encryptor = ENCRYPTORS.get(algorithm)
    if not encryptor:
        logger.error(f"Encryption algorithm '{algorithm}' not registered.")
        MetricsUtilErrors.labels(func='encrypt_data', type='invalid_algorithm').inc()
        raise ValueError(f"Encryption algorithm '{algorithm}' not registered.")
    
    # Encryption is CPU-bound, run in thread pool
    encrypted_bytes = await asyncio.to_thread(encryptor, data, key, 'encrypt')
    
    # [FIX] Replaced add_provenance with log_audit_event
    await log_audit_event(action="security_encrypt", data={'algorithm': algorithm, 'output_bytes': len(encrypted_bytes)})
    return encrypted_bytes # type: ignore

@util_decorator
async def decrypt_data(data: bytes, key: bytes, algorithm: str = 'fernet') -> str:
    """
    Decrypts data using the specified symmetric algorithm.
    Returns decrypted string (assumes utf-8).
    """
    # FIX: Lazy import to break circular dependency
    from runner.runner_logging import log_audit_event
    from runner.runner_metrics import UTIL_ERRORS as MetricsUtilErrors # FIX: Deferred import
    
    decryptor = DECRYPTORS.get(algorithm)
    if not decryptor:
        logger.error(f"Decryption algorithm '{algorithm}' not registered.")
        MetricsUtilErrors.labels(func='decrypt_data', type='invalid_algorithm').inc()
        raise ValueError(f"Decryption algorithm '{algorithm}' not registered.")
    
    # Decryption is CPU-bound, run in thread pool
    decrypted_string = await asyncio.to_thread(decryptor, data, key, 'decrypt')
    
    # [FIX] Replaced add_provenance with log_audit_event
    await log_audit_event(action="security_decrypt", data={'algorithm': algorithm, 'input_bytes': len(data)})
    return decrypted_string # type: ignore


# --- Secret Management ---
# Global in-memory cache for secrets (simple TTL)
_secret_cache: Dict[str, Tuple[float, Any]] = {}
SECRET_CACHE_TTL = 300 # 5 minutes

def _get_from_cache(key: str) -> Optional[Any]:
    if key in _secret_cache:
        timestamp, value = _secret_cache[key]
        if (time.time() - timestamp) < SECRET_CACHE_TTL:
            logger.debug(f"Secret cache HIT for key: {key}")
            return value
        else:
            logger.debug(f"Secret cache EXPIRED for key: {key}")
            del _secret_cache[key]
    logger.debug(f"Secret cache MISS for key: {key}")
    return None

def _set_to_cache(key: str, value: Any):
    _secret_cache[key] = (time.time(), value)

@util_decorator
async def fetch_secret(secret_name: str, source: str = 'env', **kwargs) -> Optional[str]:
    """
    Fetches a secret from a configured source (env, vault, aws_sm, hsm_pin).
    Caches secrets in memory with a TTL.
    """
    # FIX: Lazy import to break circular dependency
    from runner.runner_logging import log_audit_event, send_alert
    from runner.runner_metrics import UTIL_ERRORS as MetricsUtilErrors # FIX: Deferred import
    
    cached_secret = _get_from_cache(secret_name)
    if cached_secret:
        return cached_secret

    secret_value: Optional[str] = None
    
    try:
        if source == 'env':
            secret_value = os.getenv(secret_name)
            if secret_value:
                logger.info(f"Fetched secret '{secret_name}' from environment variable.")
        
        elif source == 'vault' and HAS_VAULT:
            vault_url = kwargs.get('vault_url', os.getenv('VAULT_ADDR'))
            vault_token = kwargs.get('vault_token', os.getenv('VAULT_TOKEN'))
            mount_point = kwargs.get('mount_point', 'secret')
            
            if not vault_url or not vault_token:
                raise ValueError("Vault URL and token are required for 'vault' source.")
            
            client = hvac.Client(url=vault_url, token=vault_token)
            if not client.is_authenticated():
                raise ConnectionError("Failed to authenticate with Hashicorp Vault.")
            
            # Read secret from KV v2
            response = await asyncio.to_thread(client.secrets.kv.v2.read_secret_version, path=secret_name, mount_point=mount_point)
            secret_value = response['data']['data'].get(secret_name.split('/')[-1]) # Get key from path
            if secret_value:
                logger.info(f"Fetched secret '{secret_name}' from Hashicorp Vault.")
        
        elif source == 'aws_sm' and HAS_BOTO3:
            region_name = kwargs.get('region_name', os.getenv('AWS_REGION', 'us-east-1'))
            
            session = boto3.session.Session()
            client = session.client(service_name='secretsmanager', region_name=region_name)
            
            response = await asyncio.to_thread(client.get_secret_value, SecretId=secret_name)
            if 'SecretString' in response:
                secret_value = response['SecretString']
            else:
                secret_value = base64.b64decode(response['SecretBinary']).decode('utf-8')
            if secret_value:
                logger.info(f"Fetched secret '{secret_name}' from AWS Secrets Manager.")
        
        elif source == 'hsm_pin' and HAS_PKCS11:
            # Placeholder for retrieving HSM PIN.
            # This is highly specific and often passed via environment or secure input.
            secret_value = os.getenv('HSM_PIN') # Default to environment variable for PIN
            if secret_value:
                logger.info("Fetched HSM PIN from environment variable.")
            else:
                logger.warning("HSM PIN requested but 'HSM_PIN' environment variable not set.")
                
        else:
            if not HAS_VAULT and source == 'vault':
                logger.error("Cannot fetch secret from Vault: 'hvac' library not installed.")
            elif not HAS_BOTO3 and source == 'aws_sm':
                logger.error("Cannot fetch secret from AWS Secrets Manager: 'boto3' library not installed.")
            elif not HAS_PKCS11 and source == 'hsm_pin':
                logger.error("Cannot fetch HSM PIN: 'python-pkcs11' library not installed.")
            else:
                logger.error(f"Unknown secret source: {source}")
            MetricsUtilErrors.labels(func='fetch_secret', type='invalid_source').inc()

    except Exception as e:
        logger.error(f"Failed to fetch secret '{secret_name}' from source '{source}': {e}", exc_info=True)
        MetricsUtilErrors.labels(func='fetch_secret', type=type(e).__name__).inc()
        await send_alert(f"Failed to fetch secret '{secret_name}'", f"Source: {source}\nError: {e}", severity="critical")

    if secret_value:
        _set_to_cache(secret_name, secret_value)
    else:
        logger.warning(f"Secret '{secret_name}' not found from source '{source}'.")
        
    return secret_value

@util_decorator
async def monitor_for_leaks(text: str) -> List[Dict[str, Any]]:
    """
    Monitors text for potential leaks using Presidio (if available) and regex.
    """
    # FIX: Lazy import to break circular dependency
    from runner.runner_logging import log_audit_event, send_alert
    from runner.runner_metrics import UTIL_ERRORS as MetricsUtilErrors # FIX: Deferred import
    
    leaks_found: List[Dict[str, Any]] = []
    
    # 1. Presidio (if available)
    if _PRESIDIO_AVAILABLE or _load_presidio_engine():
        if _PRESIDIO_ANALYZER_ENGINE and _PRESIDIO_ANONYMIZER_ENGINE:
            try:
                results = _PRESIDIO_ANALYZER_ENGINE.analyze(text=text, language='en')
                for res in results:
                    leaks_found.append({
                        'type': 'PII_Presidio',
                        'entity': res.entity_type,
                        'location_start': res.start,
                        'location_end': res.end,
                        'score': res.score
                    })
            except Exception as e:
                logger.error(f"Presidio leak monitoring failed: {e}", exc_info=True)
                MetricsUtilErrors.labels(func='monitor_for_leaks', type='presidio_error').inc()
    
    # 2. Regex (always run as fallback or for non-PII secrets)
    patterns = [
        re.compile(r'\b[A-Za-z0_9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'), # Email
        re.compile(r'\b(?:\d{3}[-.]?){2}\d{4}\b'), # Phone
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            leaks_found.append({
                'type': 'PII_Regex',
                'entity': 'Pattern',
                'location_start': match.start(),
                'location_end': match.end(),
                'score': 0.8 # Assign arbitrary score for regex
            })

    if leaks_found:
        logger.warning(f"Potential data leaks detected: {len(leaks_found)} findings.")
        # [FIX] Replaced add_provenance with log_audit_event
        await log_audit_event(action="security_leak_monitor", data={'findings_count': len(leaks_found), 'first_finding_type': leaks_found[0]['type'] if leaks_found else 'N/A'})
        await send_alert("Data Leak Detected", f"Found {len(leaks_found)} potential leaks in processed data.", severity="high")
    
    return leaks_found

@util_decorator
async def scan_for_vulnerabilities(target: Union[str, Path], scan_type: str = 'code') -> Dict[str, Any]:
    """
    Scans code or data for vulnerabilities using external tools (e.g., Bandit, Trivy).
    This is a simplified example; a real implementation would use process_utils.
    """
    # FIX: Lazy import to break circular dependency
    from runner.runner_logging import log_audit_event
    
    scan_results = {
        'status': 'skipped',
        'scanned_target': str(target),
        'scan_type': scan_type,
        'vulnerabilities_found': 0,
        'details': 'No scanners available or scan_type unknown.'
    }
    
    if scan_type == 'code':
        logger.info(f"Simulating vulnerability scan (e.g., Bandit, Semgrep) on code target: {target}")
        scan_results['status'] = 'completed'
        scan_results['vulnerabilities_found'] = 1
        scan_results['details'] = "[Mocked] Found 1 vulnerability: B101 - assert_used (Severity: Low)"
        
    elif scan_type == 'data':
        logger.info(f"Simulating vulnerability scan (e.g., Trivy config scan) on data target: {target}")
        scan_results['status'] = 'completed'
        scan_results['vulnerabilities_found'] = 0
        scan_results['details'] = "[Mocked] No vulnerabilities found in data."
    
    # [FIX] Replaced add_provenance with log_audit_event
    await log_audit_event(action="security_vulnerability_scan", data={'target': str(target), 'type': scan_type, 'findings': scan_results['vulnerabilities_found']})
    return scan_results


# --- Test Suite ---
import unittest
from hypothesis import given, strategies as st
import shutil

# Ensure we're in an async context for tests
class TestSecurityUtils(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path("./test_security_utils_temp")
        self.test_dir.mkdir(exist_ok=True)
        os.environ["TEST_SECRET_KEY"] = "env_secret_value_12345"
        self.fernet_key = Fernet.generate_key()
        
    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir)
        del os.environ["TEST_SECRET_KEY"]

    def test_redact_secrets_nlp_sync(self):
        # Need to run async test in a loop
        async def run_test():
            if not _PRESIDIO_AVAILABLE and not _load_presidio_engine():
                self.skipTest("Presidio not available, skipping NLP redaction test.")
            
            text = "My name is John Doe and my email is test@example.com."
            redacted = await redact_secrets(text, method='nlp_presidio')
            self.assertIn("[REDACTED]", redacted)
            self.assertNotIn("John Doe", redacted)
            self.assertNotIn("test@example.com", redacted)
        asyncio.run(run_test())

    def test_redact_secrets_regex_sync(self):
        async def run_test():
            text = "This is safe. My email is test@example.com."
            redacted = await redact_secrets(text, method='regex_basic')
            self.assertIn("[REDACTED]", redacted)
            self.assertNotIn("test@example.com", redacted)
            self.assertIn("This is safe.", redacted)
        asyncio.run(run_test())

    def test_encrypt_decrypt_fernet_sync(self):
        async def run_test():
            data = "This is a secret message."
            encrypted = await encrypt_data(data, self.fernet_key, algorithm='fernet')
            self.assertIsInstance(encrypted, bytes)
            self.assertNotEqual(data.encode(), encrypted)
            
            decrypted = await decrypt_data(encrypted, self.fernet_key, algorithm='fernet')
            self.assertEqual(data, decrypted)
        asyncio.run(run_test())

    def test_encrypt_decrypt_aes_sync(self):
        async def run_test():
            aes_key = os.urandom(32) # 256-bit key
            data = "AES secret message."
            
            encrypted = await encrypt_data(data, aes_key, algorithm='aes_cbc')
            self.assertIsInstance(encrypted, bytes)
            self.assertNotEqual(data.encode(), encrypted)
            
            decrypted = await decrypt_data(encrypted, aes_key, algorithm='aes_cbc')
            self.assertEqual(data, decrypted)
        asyncio.run(run_test())

    def test_fetch_secret_env_sync(self):
        async def run_test():
            secret = await fetch_secret("TEST_SECRET_KEY", source='env')
            self.assertEqual(secret, "env_secret_value_12345")
            
            with patch.dict(os.environ, {"TEST_SECRET_KEY": "new_value"}):
                cached_secret = await fetch_secret("TEST_SECRET_KEY", source='env')
                self.assertEqual(cached_secret, "env_secret_value_12345")
            
            _secret_cache.clear()
        asyncio.run(run_test())

    def test_fetch_secret_vault_sync(self):
        async def run_test():
            if not HAS_VAULT:
                self.skipTest("hvac (Vault client) not installed. Skipping Vault test.")
            
            with self.assertLogs(logger.name, level='ERROR') as cm:
                secret = await fetch_secret("nonexistent_secret", source='vault')
                self.assertIsNone(secret)
                self.assertIn("Failed to fetch secret", cm.output[0])
        asyncio.run(run_test())

    def test_fetch_secret_aws_sm_sync(self):
        async def run_test():
            if not HAS_BOTO3:
                self.skipTest("boto3 (AWS client) not installed. Skipping AWS SM test.")
            
            with self.assertLogs(logger.name, level='ERROR') as cm:
                secret = await fetch_secret("nonexistent_secret", source='aws_sm')
                self.assertIsNone(secret)
                self.assertIn("Failed to fetch secret", cm.output[0])
        asyncio.run(run_test())
            
    @given(st.text(min_size=1, max_size=200))
    def test_monitor_for_leaks_hypothesis_sync(self, text_segment):
        async def run_test(text_segment):
            leaky_text = f"This is a test with SSN: 999-88-7777 and email: leak_{text_segment}@domain.org"
            leaks = await monitor_for_leaks(leaky_text)
            self.assertTrue(len(leaks) > 0)
            
            clean_text = f"This is a safe sentence without any PII or secrets. {text_segment} is a placeholder."
            no_leaks = await monitor_for_leaks(clean_text)
            self.assertEqual(len(no_leaks), 0)
        asyncio.run(run_test(text_segment))

    def test_scan_for_vulnerabilities_simulated_sync(self):
        async def run_test():
            dummy_code_path = self.test_dir / "dummy_code.py"
            dummy_code_path.write_text("import os; os.system('rm -rf /')")
            code_scan_results = await scan_for_vulnerabilities(dummy_code_path, scan_type='code')
            self.assertEqual(code_scan_results['status'], 'completed')
            self.assertGreater(code_scan_results['vulnerabilities_found'], 0)
            self.assertIn(str(dummy_code_path), code_scan_results['scanned_target'])

            sensitive_data_string = "malicious_injection_string"
            data_scan_results = await scan_for_vulnerabilities(sensitive_data_string, scan_type='data')
            self.assertEqual(data_scan_results['status'], 'completed')
            self.assertEqual(data_scan_results['vulnerabilities_found'], 0)
        asyncio.run(run_test())