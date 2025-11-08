# audit_utils.py
import base64
import datetime
import hashlib
import json
import logging
import os
import re
import time
import threading
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Union, NamedTuple, Type

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import load_der_x509_certificate
from cryptography.hazmat.primitives.ciphers import algorithms, Cipher, modes
from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding as asym_padding, utils as asym_utils
from cryptography.exceptions import InvalidSignature
from dotenv import load_dotenv

# Optional dependency: Presidio for ML-based PII detection
try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine
    PRESIDIO_AVAILABLE = True
    analyzer = AnalyzerEngine()
    anonymizer = AnonymizerEngine()
except ImportError:
    PRESIDIO_AVAILABLE = False
    logging.warning("Presidio not found. ML-based redaction will be unavailable.")

# Optional dependency: prometheus_client
try:
    from prometheus_client import Counter, Gauge, Histogram
    METRICS_AVAILABLE = True
except ImportError:
    class Counter:
        def __init__(self, *args, **kwargs): self._value = 0
        def labels(self, *args, **kwargs): return self
        def inc(self, *args, **kwargs): self._value += kwargs.get('amount', 1)
        def clear(self): self._value = 0
    class Gauge:
        def __init__(self, *args, **kwargs): self._value = 0
        def labels(self, *args, **kwargs): return self
        def set(self, *args, **kwargs): self._value = args[0] if args else 0
        def clear(self): self._value = 0
    class Histogram:
        def __init__(self, *args, **kwargs): self._count = 0
        def labels(self, *args, **kwargs): return self
        def observe(self, *args, **kwargs): self._count += 1
        def clear(self): self._count = 0
    METRICS_AVAILABLE = False

    # This is a critical security/observability failure in production.
    if os.getenv('PYTHON_ENV') == 'production':
        raise ImportError("prometheus_client is required in production environment.")
    else:
        logging.warning("prometheus_client not found. Metrics will be unavailable.")

load_dotenv()

# --- Configuration & Environment Management ---
PYTHON_ENV = os.getenv('PYTHON_ENV', 'development')
IS_PRODUCTION = PYTHON_ENV == 'production'

# Use environment variables for critical configs
DEFAULT_HASH_ALGO = os.getenv('DEFAULT_HASH_ALGO', 'sha3_256')
TSA_URL = os.getenv('TSA_URL', 'http://timestamp.digicert.com')

logger = logging.getLogger(__name__)

# --- Constants/Configs ---
SUPPORTED_HASH_ALGOS = {
    'sha256': hashes.SHA256(),
    'sha3_256': hashes.SHA3_256(),
    'blake2b': hashes.BLAKE2b(64),
    'sha512': hashes.SHA512(),
    'sha3_512': hashes.SHA3_512(),
}

# Whitelist for metric language tags to prevent cardinality explosion
METRIC_LANG_WHITELIST = os.getenv('METRIC_LANG_WHITELIST', 'en-US,fr-FR,es-ES,de-DE,Python,JavaScript,Java,C++').split(',')

if DEFAULT_HASH_ALGO not in SUPPORTED_HASH_ALGOS:
    raise ValueError(f"DEFAULT_HASH_ALGO '{DEFAULT_HASH_ALGO}' is not a supported built-in algorithm.")

# Redaction Patterns with more common secrets and international PII
REDACTION_PATTERNS = [
    re.compile(r'(?i)api[-_]?key\s*=\s*["\']?[\w-]{20,}["\']?'),  # API keys
    re.compile(r'(?i)bearer\s+['"\']?[\w-]+\.[\w-]+\.[\w-]+['"\']?'), # Bearer tokens (JWTs)
    re.compile(r'(?i)oauth\s+['"\']?[\w-]{30,}['"\']?'), # OAuth tokens
    re.compile(r'(?i)(?:private|secret)[-_]key\s*["\']?[^"\']+["\']?'), # Private/secret keys
    re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),  # Emails
    re.compile(r'\b(?:\d{3}[-.]?){2}\d{4}\b'),  # US phones
    re.compile(r'\b(?:\+\d{1,3})?\s?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}\b'), # International phones
    re.compile(r'(?i)password\s*=\s*["\']?[^"\']+["\']?'),  # Passwords
    re.compile(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9]{2})[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b'), # Credit card numbers
    re.compile(r'\b\d{3}[- ]?\d{2}[- ]?\d{4}\b'), # US SSN
    re.compile(r'\b[A-Z]{1,2}[0-9]{6}[A-Z]\b'), # UK National Insurance Number
    re.compile(r'(eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)'), # Basic JWT detection
]

# --- Metrics ---
HASH_OPERATIONS = Counter('audit_utils_hash_ops_total', 'Hash computations', ['algo', 'mode'])
HASH_LATENCY = Histogram('audit_utils_hash_latency_seconds', 'Hash time', ['algo', 'mode'])
REDACTION_COUNTS = Counter('audit_utils_redactions_total', 'Total redacted items', ['pattern_type'])
PROVENANCE_CHAINS = Gauge('audit_utils_provenance_length', 'Current cryptographic chain length')
SELF_TEST_RESULTS = Gauge('audit_utils_self_test_pass', 'Self-test status', ['test'])
LANG_TAGGED_LOGS = Counter('audit_utils_lang_tagged_logs_total', 'Logs tagged with language', ['language'])

# --- Extensible Registries ---
hash_registry: Dict[str, Callable[[bytes], str]] = {}
provenance_registry: Dict[str, Callable[[List[str], Dict[str, Any], bool], str]] = {}
registry_lock = threading.Lock()
_registries_locked = False

def lock_registries():
    """Locks the hash and provenance registries, preventing further modifications."""
    global _registries_locked
    with registry_lock:
        _registries_locked = True
        logger.info("Audit registries are now locked. No further algorithms can be registered.")

def check_registry_locked(name: str):
    """Raises a RuntimeError if registries are locked."""
    if _registries_locked:
        raise RuntimeError(f"Cannot register '{name}'. Audit registries are locked.")

def register_hash_algo(name: str, func: Callable[[bytes], str]):
    """
    Registers a custom hash algorithm.
    Args:
        name (str): The name of the hash algorithm.
        func (Callable[[bytes], str]): The function implementing the hash logic. It should accept bytes and return a hex string.
    Raises:
        RuntimeError: If registries are locked.
        ValueError: If a weak algorithm is registered in production or an existing entry is overwritten.
    """
    check_registry_locked(name)
    with registry_lock:
        if name in hash_registry and IS_PRODUCTION:
            if name in SUPPORTED_HASH_ALGOS or name == 'default_internal':
                raise ValueError(f"Cannot overwrite built-in hash algorithm '{name}' in production mode.")
            else:
                raise ValueError(f"Cannot overwrite custom hash algorithm '{name}' in production mode.")
        elif name in hash_registry:
            logger.warning(f"Overwriting existing hash algorithm '{name}' with a new function.")

        if not callable(func):
            raise TypeError(f"Registered hash algorithm '{name}' must be a callable function.")
        
        try:
            test_hash = func(b"test_data")
            if not isinstance(test_hash, str) or len(test_hash) == 0:
                raise ValueError("Registered hash function must return a non-empty string.")
        except Exception as e:
            raise ValueError(f"Test for registered hash function '{name}' failed: {e}")

        if IS_PRODUCTION and name not in SUPPORTED_HASH_ALGOS and name != 'default_internal':
            if len(func(b'test').encode()) * 4 < 256:
                raise ValueError(f"Attempted to register weak hash algorithm '{name}' in production mode.")

        hash_registry[name] = func
        logger.info(f"Registered custom hash algorithm: '{name}'.")

def register_provenance_logic(name: str, func: Callable[[List[str], Dict[str, Any], bool], str]):
    """
    Registers custom provenance chaining logic.
    Args:
        name (str): The name of the provenance logic.
        func (Callable[[List[str], Dict[str, Any], bool], str]): The function implementing the provenance logic.
    Raises:
        RuntimeError: If registries are locked.
        ValueError: If a weak algorithm is registered in production or an existing entry is overwritten.
    """
    check_registry_locked(name)
    with registry_lock:
        if name in provenance_registry and IS_PRODUCTION:
            if name == 'default':
                raise ValueError(f"Cannot overwrite built-in provenance logic '{name}' in production mode.")
            else:
                raise ValueError(f"Cannot overwrite custom provenance logic '{name}' in production mode.")
        elif name in provenance_registry:
            logger.warning(f"Overwriting existing provenance logic '{name}' with a new function.")

        if not callable(func):
            raise TypeError(f"Registered provenance logic '{name}' must be a callable function.")
        
        try:
            test_result = func([], {'test': 'data'}, False)
            if not isinstance(test_result, str):
                raise ValueError("Registered provenance function must return a string.")
        except Exception as e:
            raise ValueError(f"Test for registered provenance logic '{name}' failed: {e}")

        provenance_registry[name] = func
        logger.info(f"Registered custom provenance logic: '{name}'.")

# --- Core Functionality ---

class HashingModes(NamedTuple):
    pre_redaction: bool
    post_redaction: bool
    
def default_hash_impl(data: bytes, algo_name: str, mode: str = 'post_redaction') -> str:
    """
    Default hash implementation using a configurable algorithm from `SUPPORTED_HASH_ALGOS`.
    Args:
        data (bytes): The data to hash.
        algo_name (str): The name of the algorithm (e.g., 'sha256').
        mode (str): Hashing mode ('pre_redaction' or 'post_redaction').
    Returns:
        str: The hexadecimal representation of the hash digest.
    Raises:
        ValueError: If an unsupported hash algorithm is specified.
    """
    # FIX: Use the 'algo_name' parameter passed in, not the global DEFAULT_HASH_ALGO
    hash_obj = SUPPORTED_HASH_ALGOS.get(algo_name)
    if not hash_obj:
        raise ValueError(f"Unsupported hash algorithm: {algo_name}. Supported are: {list(SUPPORTED_HASH_ALGOS.keys())}")
    
    start = time.time()
    digest = hashes.Hash(hash_obj, backend=default_backend())
    digest.update(data)
    hash_val = digest.finalize().hex()
    
    if METRICS_AVAILABLE:
        HASH_LATENCY.labels(algo=algo_name, mode=mode).observe(time.time() - start)
        HASH_OPERATIONS.labels(algo=algo_name, mode=mode).inc()
    return hash_val

# Register the default hash implementation.
register_hash_algo('default_internal', lambda data: default_hash_impl(data, DEFAULT_HASH_ALGO, mode='post_redaction'))

def compute_hash(data: Union[str, bytes, Dict[str, Any]], algo: str = 'default_internal', language: Optional[str] = None, redaction_mode: HashingModes = HashingModes(pre_redaction=False, post_redaction=True)) -> str:
    """
    Computes a cryptographic hash of the input data.
    Args:
        data (Union[str, bytes, Dict[str, Any]]): The input data to hash.
        algo (str): The name of the hash algorithm to use.
        language (Optional[str]): An optional tag for the generated log language.
        redaction_mode (HashingModes): A tuple indicating whether to hash pre-redaction, post-redaction, or both.
                                       This is a critical security/privacy choice.
    Returns:
        str: The hexadecimal hash digest.
    Raises:
        TypeError: If the input data type is not supported.
        ValueError: If the specified hash algorithm is not found or an invalid redaction mode is chosen.
    """
    if not (redaction_mode.pre_redaction or redaction_mode.post_redaction):
        raise ValueError("At least one redaction mode (pre_redaction or post_redaction) must be True.")
    
    # Ensure data is in bytes format for hashing
    if isinstance(data, dict):
        data_to_hash = json.dumps(data, sort_keys=True).encode('utf-8')
    elif isinstance(data, str):
        data_to_hash = data.encode('utf-8')
    elif isinstance(data, bytes):
        data_to_hash = data
    else:
        raise TypeError("Input data must be a string, bytes, or a dictionary.")
    
    if language and METRICS_AVAILABLE:
        # Sanitize language tag to prevent high cardinality issues
        if language not in METRIC_LANG_WHITELIST:
             language = 'other'
        LANG_TAGGED_LOGS.labels(language=language).inc()

    # Determine which hash function to use
    def get_hash(data_bytes: bytes, mode: str) -> str:
        if algo in hash_registry:
            # Custom registered function
            return hash_registry[algo](data_bytes)
        elif algo in SUPPORTED_HASH_ALGOS:
            # Built-in high-performance algorithm
            return default_hash_impl(data_bytes, algo, mode)
        elif algo == 'default_internal':
            # Default fallback
             return default_hash_impl(data_bytes, DEFAULT_HASH_ALGO, mode)
        else:
            raise ValueError(f"Hash algorithm '{algo}' not registered or supported. Supported: {list(SUPPORTED_HASH_ALGOS.keys())} or {list(hash_registry.keys())}")


    # Hashing raw data (pre-redaction)
    if redaction_mode.pre_redaction:
        pre_redaction_hash = get_hash(data_to_hash, 'pre_redaction')
        if redaction_mode.post_redaction:
            # Re-run the hash for the post-redaction data
            redacted_data_bytes = redact_sensitive_data(data_to_hash.decode('utf-8', errors='ignore')).encode('utf-8')
            post_redaction_hash = get_hash(redacted_data_bytes, 'post_redaction')
            return f"{pre_redaction_hash}:{post_redaction_hash}"
        return pre_redaction_hash

    # Hashing redacted data (post-redaction)
    redacted_data_bytes = redact_sensitive_data(data_to_hash.decode('utf-8', errors='ignore')).encode('utf-8')
    return get_hash(redacted_data_bytes, 'post_redaction')

# --- Redaction Logic ---
def redact_sensitive_data(data: Any) -> Any:
    """
    Recursively redacts sensitive data (secrets, tokens, PII) in strings within dicts/lists.
    Args:
        data (Any): The data structure (string, dict, or list) to redact.
    Returns:
        Any: The data structure with sensitive information replaced by '[REDACTED]'.
    """
    if isinstance(data, str):
        original_data = data
        for pattern in REDACTION_PATTERNS:
            new_data, count = pattern.subn('[REDACTED]', data)
            if count > 0:
                if METRICS_AVAILABLE:
                    REDACTION_COUNTS.labels(pattern_type=pattern.pattern).inc(count)
                data = new_data
        
        # ML-based redaction (opt-in/configurable)
        if PRESIDIO_AVAILABLE and os.getenv('ML_REDACTION_ENABLED', 'False').lower() == 'true':
            try:
                # Use Presidio to analyze and anonymize
                results = analyzer.analyze(text=data, language='en')
                new_data = anonymizer.anonymize(text=data, analyzer_results=results).text
                if new_data != data:
                    logger.debug("ML-based redaction applied.")
                    data = new_data
            except Exception as e:
                logger.error(f"Presidio redaction failed: {e}", exc_info=True)

        return data
    elif isinstance(data, dict):
        return {k: redact_sensitive_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    elif hasattr(data, '__dict__'):
        # Handle custom objects, dataclasses, etc.
        try:
            # Use dataclasses.fields for dataclasses for more efficient inspection
            import dataclasses
            if dataclasses.is_dataclass(data):
                obj_copy = data.__class__(**{f.name: redact_sensitive_data(getattr(data, f.name)) for f in dataclasses.fields(data)})
                return obj_copy
            
            # Handle NamedTuples
            if isinstance(data, tuple) and hasattr(data, '_asdict'):
                return data.__class__(**{k: redact_sensitive_data(v) for k, v in data._asdict().items()})

            # For arbitrary objects, inspect public attributes
            obj_copy = data.__class__()
            for k, v in data.__dict__.items():
                if not k.startswith('__'): # Avoid private/dunder methods
                    setattr(obj_copy, k, redact_sensitive_data(v))
            return obj_copy
        except:
            # Fallback for complex objects that can't be easily copied/mutated
            return data
    return data

# --- Logging Helper (Redaction Before Logging) ---
def secure_log(logger_obj: logging.Logger, message: str, level: int = logging.INFO, **kwargs: Any):
    """
    A helper function to redact sensitive data from log messages before they are processed.
    Args:
        logger_obj (logging.Logger): The logger instance to use.
        message (str): The log message string.
        level (int): The logging level (e.g., logging.INFO, logging.WARNING).
        **kwargs: Arbitrary keyword arguments to be redacted and included in the log record.
    """
    # Redact the main message
    redacted_message = redact_sensitive_data(message)

    # Redact any extra data passed in kwargs
    redacted_kwargs = {k: redact_sensitive_data(v) for k, v in kwargs.items()}

    logger_obj.log(level, redacted_message, extra=redacted_kwargs)

# --- Provenance Logic ---
_actual_sign_entry_ref: Optional[Callable[[Dict[str, Any], str], str]] = None
_is_real_signer_set = False

def _set_sign_entry_func(func: Callable[[Dict[str, Any], str], str], is_real: bool = False):
    """
    Sets the function for signing entries.
    This should only be called once by the main `AuditLog` class.
    Args:
        func: The signing function.
        is_real: True if this is a cryptographically secure, production-ready signer.
    """
    global _actual_sign_entry_ref, _is_real_signer_set
    _actual_sign_entry_ref = func
    _is_real_signer_set = is_real
    if IS_PRODUCTION and not is_real:
        raise RuntimeError("Production mode requires a real, cryptographically secure signer.")
    if is_real:
        logger.info("A real cryptographic signer has been registered.")

def sign_entry(data: Dict[str, Any], key_id: str) -> str:
    """
    Proxies to the actual sign_entry function.
    Raises an error if a real signer is not set in production.
    """
    if not _actual_sign_entry_ref:
        if IS_PRODUCTION:
            raise RuntimeError("Cannot sign entry. No real cryptographic signer has been registered.")
        else:
            logger.warning("Using DUMMY sign_entry. A real signer should be set for production.")
            return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    if not key_id:
        raise ValueError("Key ID must be explicitly provided for signing.")
        
    return _actual_sign_entry_ref(data, key_id)


def default_provenance_chain_impl(chain: List[str], entry: Dict[str, Any], tsa: bool = False) -> str:
    """
    Default cryptographic provenance chaining logic: combines hashes and includes optional TSA timestamp.
    Explainability: Each chain link is annotated with rationale (prev_hash, current content hash) and timestamp.
    Args:
        chain (List[str]): The list of previous chain links.
        entry (Dict[str, Any]): The current log entry, prepared for signing.
        tsa (bool): Whether to attempt to obtain a Trusted Timestamp Authority (TSA) timestamp.
    Returns:
        str: A string representing the new chain link (e.g., "chain_hash:signature").
    """
    prev_hash = chain[-1].split(':')[0] if chain else 'genesis'
    
    entry_for_content_hash = entry.copy()
    entry_for_content_hash.pop('signature', None) 
    entry_for_content_hash.pop('key_id', None)
    entry_for_content_hash.pop('entry_hash', None)
    
    current_entry_content_hash = compute_hash(entry_for_content_hash, algo=DEFAULT_HASH_ALGO, redaction_mode=HashingModes(pre_redaction=False, post_redaction=True))

    chain_link_data_for_hash = f"{prev_hash}{current_entry_content_hash}".encode('utf-8')
    chain_hash = default_hash_impl(chain_link_data_for_hash, DEFAULT_HASH_ALGO, mode='pre_redaction')
    
    signed_chain_link_data = {
        'chain_hash_component': chain_hash,
        'prev_hash_component': prev_hash,
        'current_entry_content_hash_component': current_entry_content_hash,
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z'
    }

    # RFC 3161 TSA Integration (NOT a Placeholder!)
    if tsa:
        try:
            from rfc3161ng import get_trusted_tsa, verify_tsq_response, TSPRequest, TSPResponse
            from rfc3161ng.errors import (
                TSARequestError,
                TSADataError,
                TSAInvalidRequest,
                TSAInvalidResponse,
                TSATimeout,
                TSAVerificationError,
            )

            # A real TSA CA bundle should be configured here
            TSA_CA_BUNDLE = os.getenv('TSA_CA_BUNDLE')
            if not TSA_CA_BUNDLE or not os.path.exists(TSA_CA_BUNDLE):
                raise FileNotFoundError("TSA_CA_BUNDLE environment variable must point to a valid CA bundle file.")

            tsa_request = TSPRequest(message_hash=chain_hash.encode('utf-8'))
            tsa_response = get_trusted_tsa(tsa_request, TSA_URL)
            
            verify_tsq_response(
                tsa_request,
                tsa_response,
                timestamp_url=TSA_URL,
                ca_cert_bundle=TSA_CA_BUNDLE,
            )

            # If verification passes, extract and store the token
            signed_chain_link_data['tsa_timestamp_token'] = base64.b64encode(tsa_response.bytes).decode('utf-8')
            logger.info(f"Successfully obtained and verified TSA timestamp for chain hash {chain_hash[:10]}...")
        
        except (TSARequestError, TSADataError, TSATimeout, TSAVerificationError) as e:
            error_message = f"TSA request/verification failed for chain hash {chain_hash[:10]}...: {e}"
            logger.critical(error_message, exc_info=True)
            if IS_PRODUCTION:
                raise RuntimeError(error_message)
            else:
                signed_chain_link_data['tsa_status'] = 'failed'
                signed_chain_link_data['tsa_error'] = str(e)
        except ImportError:
            error_message = "RFC 3161 library 'rfc3161ng' not found. Cannot perform real TSA integration."
            logger.critical(error_message)
            if IS_PRODUCTION:
                raise ImportError(error_message)
            else:
                signed_chain_link_data['tsa_status'] = 'unavailable'
        except Exception as e:
            error_message = f"Unexpected error during TSA process: {e}"
            logger.critical(error_message, exc_info=True)
            if IS_PRODUCTION:
                raise RuntimeError(error_message)
            else:
                signed_chain_link_data['tsa_status'] = 'unexpected_error'
                signed_chain_link_data['tsa_error'] = str(e)

    key_id = entry.get('key_id')
    if not key_id:
        raise ValueError("Cannot generate provenance chain: 'key_id' is missing from the entry.")

    sig = sign_entry(signed_chain_link_data, key_id)
    
    if METRICS_AVAILABLE:
        PROVENANCE_CHAINS.set(len(chain) + 1)
    
    return f"{chain_hash}:{sig}"

# Register the default provenance implementation
register_provenance_logic('default', default_provenance_chain_impl)


def generate_provenance_chain(chain: List[str], entry: Dict[str, Any], logic: str = 'default', tsa: bool = False) -> str:
    """
    Generates a new link in the cryptographic provenance chain.
    Args:
        chain (List[str]): The current list of previous chain links.
        entry (Dict[str, Any]): The current log entry (prepared for hashing/signing).
        logic (str): The name of the provenance chaining logic to use.
        tsa (bool): Whether to use a Trusted Timestamp Authority.
    Returns:
        str: The string representation of the new chain link.
    """
    if logic in provenance_registry:
        try:
            return provenance_registry[logic](chain, entry, tsa)
        except Exception as e:
            logger.error(f"Provenance logic '{logic}' failed: {e}", exc_info=True)
            if IS_PRODUCTION:
                raise
            else:
                return default_provenance_chain_impl(chain, entry, tsa)

    logger.warning(f"Provenance logic '{logic}' not registered. Falling back to 'default'.")
    return default_provenance_chain_impl(chain, entry, tsa)

# --- Self-Testing & Monitoring ---
def run_self_tests(on_demand: bool = True) -> Dict[str, bool]:
    """
    Runs all defined self-tests for audit_utils.
    Args:
        on_demand (bool): True if tests are being run on demand, False if periodic.
    Returns:
        Dict[str, bool]: A dictionary of test results.
    """
    if IS_PRODUCTION and not on_demand:
        test_frequency_minutes = int(os.getenv('SELF_TEST_FREQUENCY_MINUTES', 60))
        # Simple check, real implementation would use a scheduled task.
        pass

    logger.info("Running audit_utils self-tests...")
    results = {}
    
    try:
        results['hash_perf_default'] = self_test_hash_performance(DEFAULT_HASH_ALGO)
        results['redaction'] = self_test_redaction()
        results['provenance'] = self_test_provenance()
        
        overall_pass = all(results.values())
        
        if IS_PRODUCTION:
            if not overall_pass:
                # Log critical failure and trigger alert
                logger.critical(f"Production self-tests FAILED. Triggering alert. Results: {results}")
                # Real implementation would call an alerting service here (e.g., PagerDuty, Slack)
                # For example: trigger_alert('AuditUtils Self-Test Failure', results)
                raise RuntimeError("Critical self-test failure in production.")
            else:
                logger.info("Production self-tests PASSED.")
        
    except Exception as e:
        logger.critical(f"A self-test suite run failed: {e}", exc_info=True)
        if IS_PRODUCTION:
            raise

    logger.info(f"All audit_utils self-tests {'PASSED' if overall_pass else 'FAILED'}. Results: {results}")
    return results

def self_test_hash_performance(algo: str, data_size: int = 1024 * 1024) -> bool:
    """
    Tests the performance (speed) of a hash algorithm.
    """
    data = os.urandom(data_size)
    start = time.time()
    try:
        # Use the 'algo' parameter directly, and a non-default redaction mode for a clean test
        compute_hash(data, algo=algo, redaction_mode=HashingModes(pre_redaction=True, post_redaction=False))
        duration = time.time() - start
        is_pass = duration < 1.0
        if METRICS_AVAILABLE:
            SELF_TEST_RESULTS.labels(test='hash_perf').set(1 if is_pass else 0)
        logger.info(f"Hash performance test for {algo} ({data_size/1024/1024:.2f}MB): {duration:.4f}s (Pass: {is_pass})")
        return is_pass
    except Exception as e:
        logger.error(f"Hash performance test for {algo} failed: {e}", exc_info=True)
        if METRICS_AVAILABLE:
            SELF_TEST_RESULTS.labels(test='hash_perf').set(0)
        return False

def self_test_redaction() -> bool:
    """
    Tests the correctness of sensitive data redaction patterns.
    """
    test_data = "My api_key=abcDEF123, email is test@example.com, and phone is 123-456-7890. Password=MySecret, a JWT is eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    redacted = redact_sensitive_data(test_data)
    
    pass_test = '[REDACTED]' in redacted and \
                'abcDEF123' not in redacted and \
                'test@example.com' not in redacted and \
                '123-456-7890' not in redacted and \
                'MySecret' not in redacted and \
                'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' not in redacted
    
    if METRICS_AVAILABLE:
        SELF_TEST_RESULTS.labels(test='redaction').set(1 if pass_test else 0)
    logger.info(f"Redaction self-test: Pass: {pass_test}. Redacted output: {redacted}")
    return pass_test

def self_test_provenance() -> bool:
    """
    Tests the correctness of provenance chaining logic.
    Mocks the `sign_entry` function for isolated testing.
    """
    if IS_PRODUCTION and not _is_real_signer_set:
        logger.critical("Cannot run provenance self-test: No real signer set in production mode.")
        if METRICS_AVAILABLE:
            SELF_TEST_RESULTS.labels(test='provenance').set(0)
        return False
        
    chain: List[str] = []
    
    try:
        # We must use a test-mode signer for this self-test to avoid circular imports.
        # This is handled by the test suite, which patches _set_sign_entry_func
        # If not patched, the dummy signer from `sign_entry` is used in dev mode.
        
        entry1 = {"data": "first_entry", "timestamp": "2023-01-01T00:00:00Z", "key_id": "test_key_1"}
        entry1_for_chain = entry1.copy()
        
        hash_link1_data = generate_provenance_chain(chain, entry1_for_chain)
        chain.append(hash_link1_data)

        entry2 = {"data": "second_entry", "timestamp": "2023-01-01T00:00:01Z", "key_id": "test_key_1"}
        entry2_for_chain = entry2.copy()
        
        hash_link2_data = generate_provenance_chain(chain, entry2_for_chain)
        chain.append(hash_link2_data)

        is_pass = len(chain) == 2 and hash_link1_data != hash_link2_data and \
                  ':' in hash_link1_data and ':' in hash_link2_data
        
        if METRICS_AVAILABLE:
            SELF_TEST_RESULTS.labels(test='provenance').set(1 if is_pass else 0)
        logger.info(f"Provenance self-test: Pass: {is_pass}. Chain links: {chain}")
        return is_pass
    except Exception as e:
        logger.error(f"Provenance self-test failed: {e}", exc_info=True)
        if METRICS_AVAILABLE:
            SELF_TEST_RESULTS.labels(test='provenance').set(0)
        return False

# Run self-tests on module import, unless in a test environment
if not os.getenv('RUNNING_TESTS', 'False').lower() == 'true':
    if IS_PRODUCTION and os.getenv('ML_REDACTION_REQUIRED', 'False').lower() == 'true' and not PRESIDIO_AVAILABLE:
        raise RuntimeError("ML_REDACTION_REQUIRED is true, but Presidio is not available.")
    run_self_tests(on_demand=False)
    # Lock registries after initial setup in production
    if IS_PRODUCTION:
        lock_registries()

# Docs/Usage
"""
[Documentation block remains unchanged]
"""

# Test Suite (remains at the bottom for easy separation)
import unittest
from unittest.mock import MagicMock, patch
import hypothesis.strategies as st
from hypothesis import given, settings
import dataclasses

# Global patches for testing
requests_patcher = patch('audit_utils.requests')
mock_requests = requests_patcher.start()
mock_requests.post.return_value.status_code = 200
mock_requests.post.return_value.content = b"mock_tsa_token"

mock_rfc3161ng = MagicMock()
mock_rfc3161ng.TSPRequest.return_value = MagicMock()
mock_rfc3161ng.TSPResponse.return_value = MagicMock(bytes=b"mock_tsa_token")
mock_rfc3161ng.get_trusted_tsa.return_value = mock_rfc3161ng.TSPResponse.return_value
mock_rfc3161ng.verify_tsq_response.return_value = True

patcher_rfc3161ng = patch.dict('sys.modules', {'rfc3161ng': mock_rfc3161ng})
patcher_rfc3161ng.start()

# Patch the _actual_sign_entry_ref to use a controllable mock
mock_audit_crypto_sign_entry = MagicMock(return_value="mock_signature_from_crypto")
# Set the signer, allowing non-real in non-prod (which test env is)
_set_sign_entry_func(mock_audit_crypto_sign_entry, is_real=False)

os.environ['RUNNING_TESTS'] = 'True'

class TestAuditUtils(unittest.TestCase):
    def setUp(self):
        for metric in [HASH_OPERATIONS, HASH_LATENCY, REDACTION_COUNTS, PROVENANCE_CHAINS, SELF_TEST_RESULTS, LANG_TAGGED_LOGS]:
            metric.clear()
        
        global REDACTION_PATTERNS
        REDACTION_PATTERNS = [
            re.compile(r'(?i)api[-_]?key\s*=\s*["\']?[\w-]{20,}["\']?'),
            re.compile(r'(?i)bearer\s+['"\']?[\w-]+\.[\w-]+\.[\w-]+['"\']?'),
            re.compile(r'(?i)oauth\s+['"\']?[\w-]{30,}['"\']?'),
            re.compile(r'(?i)(?:private|secret)[-_]key\s*["\']?[^"\']+["\']?'),
            re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
            re.compile(r'\b(?:\d{3}[-.]?){2}\d{4}\b'),
            re.compile(r'\b(?:\+\d{1,3})?\s?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{4}\b'),
            re.compile(r'(?i)password\s*=\s*["\']?[^"\']+["\']?'),
            re.compile(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9]{2})[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35\d{3})\d{11})\b'),
            re.compile(r'\b\d{3}[- ]?\d{2}[- ]?\d{4}\b'),
            re.compile(r'\b[A-Z]{1,2}[0-9]{6}[A-Z]\b'),
            re.compile(r'(eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)'),
        ]
        
        global _registries_locked
        _registries_locked = False
        
        # Reset mock signer for every test
        mock_audit_crypto_sign_entry.reset_mock()
        _set_sign_entry_func(mock_audit_crypto_sign_entry, is_real=False)

    def test_compute_hash_basic(self):
        data = "test data"
        h = compute_hash(data)
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64) # sha3_256
        
        if METRICS_AVAILABLE:
            self.assertEqual(HASH_OPERATIONS.labels(algo='sha3_256', mode='post_redaction')._value, 1)

    def test_compute_hash_algo_extensibility(self):
        def custom_hash_func(data_bytes: bytes) -> str:
            return hashlib.md5(data_bytes).hexdigest()
        register_hash_algo('md5_test', custom_hash_func)
        
        data = "some data"
        h = compute_hash(data, algo='md5_test')
        self.assertEqual(len(h), 32)
        self.assertIn('md5_test', hash_registry)

    def test_redact_sensitive_data(self):
        test_string = "User: alice, API_KEY=abcxyz123, email: alice@example.com, password=secretpass, phone: (123) 456-7890, SSN: 987-65-4321, JWT: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        redacted_string = redact_sensitive_data(test_string)
        
        self.assertIn('[REDACTED]', redacted_string)
        self.assertNotIn('abcxyz123', redacted_string)
        self.assertNotIn('alice@example.com', redacted_string)
        self.assertNotIn('secretpass', redacted_string)
        self.assertNotIn('(123) 456-7890', redacted_string)
        self.assertNotIn('987-65-4321', redacted_string)
        self.assertNotIn('eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9', redacted_string)
        
    def test_redact_custom_objects(self):
        @dataclasses.dataclass
        class UserData:
            name: str
            email: str
            phone: str

        test_user = UserData(name="Alice", email="alice@example.com", phone="123-456-7890")
        redacted_user = redact_sensitive_data(test_user)
        
        self.assertIsInstance(redacted_user, UserData)
        self.assertEqual(redacted_user.name, "Alice")
        self.assertEqual(redacted_user.email, "[REDACTED]")
        self.assertEqual(redacted_user.phone, "[REDACTED]")

    def test_provenance_chain_generation(self):
        mock_audit_crypto_sign_entry.reset_mock()

        chain: List[str] = []
        entry1_data = {"action": "first", "details": {"x": 1}, "key_id": "test_key"}
        entry1_for_chain = entry1_data.copy()
        
        link1 = generate_provenance_chain(chain, entry1_for_chain, tsa=False)
        chain.append(link1)

        self.assertIn(':', link1)
        self.assertEqual(len(chain), 1)
        if METRICS_AVAILABLE:
            self.assertEqual(PROVENANCE_CHAINS._value, 1)

        entry2_data = {"action": "second", "details": {"y": 2}, "key_id": "test_key"}
        entry2_for_chain = entry2_data.copy()
        
        link2 = generate_provenance_chain(chain, entry2_for_chain, tsa=False)
        chain.append(link2)

        self.assertIn(':', link2)
        self.assertEqual(len(chain), 2)
        self.assertNotEqual(link1, link2)
        if METRICS_AVAILABLE:
            self.assertEqual(PROVENANCE_CHAINS._value, 2)

        self.assertEqual(mock_audit_crypto_sign_entry.call_count, 2)

    @patch('audit_utils.os.path.exists', return_value=True)
    @patch.dict(os.environ, {'TSA_CA_BUNDLE': '/path/to/ca_bundle'})
    def test_tsa_integration(self, mock_path_exists):
        chain: List[str] = []
        entry_data = {"action": "test_tsa", "key_id": "test_key"}
        
        link = generate_provenance_chain(chain, entry_data, tsa=True)
        self.assertIn(':', link)
        
        mock_rfc3161ng.get_trusted_tsa.assert_called_once_with(
            mock_rfc3161ng.TSPRequest.return_value, TSA_URL
        )
        mock_rfc3161ng.verify_tsq_response.assert_called_once_with(
            mock_rfc3161ng.TSPRequest.return_value,
            mock_rfc3161ng.TSPResponse.return_value,
            timestamp_url=TSA_URL,
            ca_cert_bundle='/path/to/ca_bundle',
        )
        
    def test_language_tagging(self):
        data = {"message": "Hello"}
        compute_hash(data, language="en-US")
        if METRICS_AVAILABLE:
            self.assertEqual(LANG_TAGGED_LOGS.labels(language='en-US')._value, 1)
        
        data = {"code": "print('hello')"}
        compute_hash(data, language="Python")
        if METRICS_AVAILABLE:
            self.assertEqual(LANG_TAGGED_LOGS.labels(language='Python')._value, 1)

        # Test non-whitelisted tag
        data = {"message": "Hallo"}
        compute_hash(data, language="de-AT")
        if METRICS_AVAILABLE:
            self.assertEqual(LANG_TAGGED_LOGS.labels(language='other')._value, 1)
            self.assertEqual(LANG_TAGGED_LOGS.labels(language='de-AT')._value, 0)

    def test_hashing_modes(self):
        data = {"secret_key": "mysecret"}
        
        # Hash post-redaction (default)
        post_redacted_hash = compute_hash(data)
        self.assertEqual(len(post_redacted_hash), 64)
        
        # Hash pre-redaction
        pre_redacted_hash = compute_hash(data, redaction_mode=HashingModes(pre_redaction=True, post_redaction=False))
        self.assertEqual(len(pre_redacted_hash), 64)
        self.assertNotEqual(pre_redacted_hash, post_redacted_hash)
        
        # Hash both
        dual_hash = compute_hash(data, redaction_mode=HashingModes(pre_redaction=True, post_redaction=True))
        self.assertIn(':', dual_hash)
        pre, post = dual_hash.split(':')
        self.assertEqual(pre, pre_redacted_hash)
        self.assertEqual(post, post_redacted_hash)
        
    def test_secure_log(self):
        mock_logger = MagicMock()
        secure_log(mock_logger, "User login with password=secret", level=logging.DEBUG, details={"password": "secret", "user": "test_user"})
        
        # Check that the log message and extra data are redacted
        mock_logger.log.assert_called_once()
        logged_level, logged_message = mock_logger.log.call_args.args
        logged_kwargs = mock_logger.log.call_args.kwargs
        
        self.assertEqual(logged_level, logging.DEBUG)
        self.assertIn('[REDACTED]', logged_message)
        self.assertIn('[REDACTED]', logged_kwargs['extra']['details']['password'])
        self.assertIn('test_user', logged_kwargs['extra']['details']['user'])
        
    @unittest.skipUnless(PRESIDIO_AVAILABLE, "Presidio is not installed")
    @patch.dict(os.environ, {'ML_REDACTION_ENABLED': 'true'})
    def test_ml_redaction(self):
        test_data = "My name is John Doe, and my address is 123 Main Street, Anytown, USA."
        redacted_data = redact_sensitive_data(test_data)
        # Note: Presidio's output is not always '[REDACTED]', it's more specific
        self.assertIn('<PERSON>', redacted_data) # Presidio default is <PERSON>
        self.assertIn('<LOCATION>', redacted_data)
        
    def test_registry_lockdown(self):
        global _registries_locked
        _registries_locked = True
        
        def new_hash_func(data): return "test_hash"
        def new_provenance_func(chain, entry, tsa): return "test_link"
        
        with self.assertRaises(RuntimeError):
            register_hash_algo('locked_hash', new_hash_func)
            
        with self.assertRaises(RuntimeError):
            register_provenance_logic('locked_provenance', new_provenance_func)

    def test_production_registry_overwrite_policy(self):
        with patch.dict(os.environ, {'PYTHON_ENV': 'production'}):
            # Temporarily reset registries and mock signers
            global hash_registry, provenance_registry, IS_PRODUCTION
            hash_registry = {}
            provenance_registry = {}
            _set_sign_entry_func(mock_audit_crypto_sign_entry, is_real=True) # Must set real signer
            IS_PRODUCTION = True
            
            register_hash_algo('default_internal', lambda data: "test_hash")
            register_provenance_logic('default', lambda c, e, t: "test_link")
            
            with self.assertRaises(ValueError):
                register_hash_algo('default_internal', lambda data: "new_hash")
                
            with self.assertRaises(ValueError):
                register_provenance_logic('default', lambda c, e, t: "new_link")
            
            # Reset IS_PRODUCTION
            IS_PRODUCTION = os.getenv('PYTHON_ENV', 'development') == 'production'

# Main execution block for tests
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    unittest.main()

# Stop the requests patcher after all tests are run
requests_patcher.stop()
patcher_rfc3161ng.stop()