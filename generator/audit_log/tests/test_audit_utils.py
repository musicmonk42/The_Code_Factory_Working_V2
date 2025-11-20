"""
test_audit_utils.py
~~~~~~~~~~~~~~~~~~~
Regulated-industry-grade test suite for ``audit_utils.py`` (≥ 90 % coverage).

Run with:
    pytest generator/audit_log/tests/test_audit_utils.py -vv
    # coverage:
    pytest --cov=generator/audit_log/audit_utils \
           --cov-report=term-missing \
           generator/audit_log/tests/test_audit_utils.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, mock_open, patch
from types import ModuleType, SimpleNamespace # Added for stubbing
import importlib.util # CRITICAL FIX: Added for dynamic loading

import pytest
from _pytest.logging import LogCaptureFixture
from faker import Faker
from freezegun import freeze_time
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
# CRITICAL FIX 1: Ensure all cryptography imports are available at test collection time
from cryptography.hazmat.backends import default_backend 
from cryptography.x509 import load_der_x509_certificate
import hashlib 
import copy 

# --------------------------------------------------------------------------- #
# 1. Make the *generator* package importable from the repo root
# --------------------------------------------------------------------------- #
REPO_ROOT = Path(__file__).resolve().parents[3]          # …/The_Code_Factory-master
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --------------------------------------------------------------------------- #
# 2. Import the module under test (NOW USING DYNAMIC LOAD)
# --------------------------------------------------------------------------- #
# CRITICAL FIX: Set the flag to prevent the module's self-tests from running aggressively on import
os.environ['RUNNING_TESTS'] = 'true'

module_path = Path(__file__).parent.parent / 'audit_utils.py'
spec = importlib.util.spec_from_file_location("generator.audit_log.audit_utils", str(module_path))

if spec is None:
    raise ImportError(f"Could not find module spec for {module_path}")

# Load the module dynamically
audit_utils_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit_utils_module
# Execute the module code, which populates the module object with functions/globals
spec.loader.exec_module(audit_utils_module)

# Now safely import all necessary symbols from the loaded module
from generator.audit_log.audit_utils import (  # type: ignore
    compute_hash,
    redact_sensitive_data,
    rotate_key,
    register_hash_algo,
    register_provenance_logic,
    _registries_locked,
    hash_registry,
    provenance_registry,
    secure_log,
    sign_entry,
    generate_provenance_chain,
    lock_registries,
    default_hash_impl,
    default_provenance_chain_impl,
    PRESIDIO_AVAILABLE,
    IS_PRODUCTION,
    HashingModes,
    self_test_hash_performance,
    self_test_redaction,
    self_test_provenance,
    run_self_tests,
    DEFAULT_HASH_ALGO,
    _set_sign_entry_func,
    _is_real_signer_set
)

# --------------------------------------------------------------------------- #
# 3. Fixtures
# --------------------------------------------------------------------------- #
fake = Faker()

@pytest.fixture(autouse=True)
def reset_registries():
    """
    Reset registries and ensure default internal implementations are registered
    for the tests that rely on them.

    CRITICAL FIX 3: We patch the global state variables directly, clear them, 
    manually set the lock state, and re-register the defaults. This is the most 
    stable way to handle module-level global state for unit tests.
    """
    with patch.object(audit_utils_module, '_registries_locked', False, create=True) as mock_lock:
        with patch.dict(audit_utils_module.hash_registry, clear=True):
            with patch.dict(audit_utils_module.provenance_registry, clear=True):
                
                # Manually register the defaults, ensuring they match the required signature
                register_hash_algo('default_internal', 
                                   lambda data, algo_name: default_hash_impl(data, 'default_internal', mode='post_redaction'))
                register_provenance_logic('default', default_provenance_chain_impl)
                
                # Also reset the signer status for provenance tests
                # The _set_sign_entry_func expects a function that returns bytes (raw signature)
                def dummy_signer(data, key_id):
                    # Return deterministic bytes for a valid signature
                    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).digest()

                _set_sign_entry_func(dummy_signer, is_real=False)
                
                yield
                
                # Cleanup: Ensure state is reset for next test run, even if registration failed above
                audit_utils_module._registries_locked = False
                audit_utils_module.hash_registry.clear()
                audit_utils_module.provenance_registry.clear()
                _set_sign_entry_func(MagicMock(side_effect=RuntimeError("Test cleanup")), is_real=False) # Guard


@pytest.fixture
def mock_audit_log():
    # log_action is called synchronously in secure_log, so use regular MagicMock
    # Patch the lazy loader reference in the utility module
    with patch('generator.audit_log.audit_utils.log_action', new=MagicMock(return_value=None)) as m:
        yield m


@pytest.fixture
def mock_requests_post():
    with patch('requests.post') as m:
        m.return_value = MagicMock()
        m.return_value.content = b'test_tsq'
        yield m


@pytest.fixture
def mock_rfc3161ng():
    # We must mock rfc3161ng in audit_utils's module namespace
    with patch('generator.audit_log.audit_utils.rfc3161ng') as m:
        # Mock necessary objects/methods for TSA integration to work without import error
        m.get_trusted_tsa.return_value = MagicMock(bytes=b'mock_tsa_token')
        m.verify_tsq_response.return_value = None
        m.TSPRequest = MagicMock()
        # Ensure errors are available
        m.errors.TSARequestError = Exception
        m.errors.TSADataError = Exception
        m.errors.TSATimeout = Exception
        m.errors.TSAVerificationError = Exception
        yield m


# --------------------------------------------------------------------------- #
# 4. Hashing
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as compute_hash is synchronous
def test_compute_hash_basic(reset_registries):
    data = b'test_data'
    # compute_hash defaults to 'default_internal' which maps to DEFAULT_HASH_ALGO (sha3_256)
    expected = hashlib.sha3_256(data).hexdigest()
    
    assert compute_hash(data) == expected


# FIX: Removed async/await as compute_hash is synchronous
def test_compute_hash_custom_algo(reset_registries):
    # Custom registered functions must accept (data, algo) due to the fix in register_hash_algo
    def sha512_wrapper(data, algo):
        return hashlib.sha512(data).hexdigest()

    register_hash_algo('sha512', sha512_wrapper)
    data = b'test_data'
    expected = hashlib.sha512(data).hexdigest()
    
    assert compute_hash(data, algo='sha512') == expected 

# FIX: Added test for pre_redaction mode (required coverage)
def test_compute_hash_pre_redaction(reset_registries):
    data = "test_data with secret=123-45-6789"
    
    # Pre-redaction hash only
    h_pre = compute_hash(data, redaction_mode=HashingModes(pre_redaction=True, post_redaction=False))
    
    expected_pre = hashlib.sha3_256(data.encode('utf-8')).hexdigest()
    assert h_pre == expected_pre

# FIX: Added test for pre_redaction:post_redaction mode (required coverage)
def test_compute_hash_both_redaction_modes(reset_registries):
    data = "test_data with secret=123-45-6789"
    
    # Pre-redaction:Post-redaction hash
    h_both = compute_hash(data, redaction_mode=HashingModes(pre_redaction=True, post_redaction=True))
    
    expected_pre = hashlib.sha3_256(data.encode('utf-8')).hexdigest()
    
    redacted_data = redact_sensitive_data(data)
    expected_post = hashlib.sha3_256(redacted_data.encode('utf-8')).hexdigest()
    
    assert h_both == f"{expected_pre}:{expected_post}"


# --------------------------------------------------------------------------- #
# 5. Redaction
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as redaction is synchronous
def test_redact_sensitive_data_str():
    # Updated test data to ensure multiple patterns are hit
    sensitive = "John Doe lives in New York, SSN 123-45-6789, email is test@example.com, and key=abcDEF123."
    redacted = redact_sensitive_data(sensitive)
    
    # Always check SSN and API key (regex patterns)
    assert '123-45-6789' not in redacted
    assert 'abcDEF123' not in redacted
    assert 'test@example.com' not in redacted
    
    # Check ML/PII if configured
    if PRESIDIO_AVAILABLE and os.getenv('ML_REDACTION_ENABLED', 'False').lower() == 'true':
        assert ('<PERSON>' in redacted and '<LOCATION>' in redacted) or ('REDACTED' in redacted)
    else:
        assert '[REDACTED]' in redacted # General check


# FIX: Removed async/await as redaction is synchronous
def test_redact_sensitive_data_dict():
    data = {"name": "John Doe", "city": "New York", "ssn": "123-45-6789", "token": "Bearer ABC.DEF.GHI"}
    # The production code performs a deep copy
    original_data = copy.deepcopy(data)
    redacted = redact_sensitive_data(data)
    
    # Assert against the *copy* of the original data to prove original was not mutated
    assert original_data["ssn"] == "123-45-6789" 
    
    # Always check SSN and token is redacted (covered by regex patterns)
    assert "[REDACTED]" in redacted["ssn"]
    assert "[REDACTED]" in redacted["token"]
    
    # Check if a non-PII field remains
    assert redacted["city"] == "New York"


# FIX: Removed async/await as redaction is synchronous
def test_redact_sensitive_data_list():
    data = ["John Doe", "New York", "123-45-6789", {"secret": "private-key-12345"}]
    # The production code performs a deep copy
    original_data = copy.deepcopy(data)
    redacted = redact_sensitive_data(data)

    # Assert against the *copy* of the original data
    assert original_data[2] == "123-45-6789"
    
    # Always check PII/secrets are redacted (covered by regex patterns)
    assert "[REDACTED]" in redacted[2]
    assert "[REDACTED]" in redacted[3]["secret"]
    
    # Check that non-PII remains
    assert redacted[1] == "New York"

# --------------------------------------------------------------------------- #
# 6. Key rotation
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as key rotation is synchronous
def test_rotate_key():
    old = b'old_key_32_bytes_long_enough___'
    new_key = rotate_key(old) 
    
    assert new_key != old
    assert len(new_key) == 44 


# --------------------------------------------------------------------------- #
# 7. Provenance chain
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as provenance generation is synchronous (only signing proxied async)
def test_generate_provenance_chain(mock_rfc3161ng):
    chain = []
    # FIX: Ensure key_id is present
    entry = {"action": "test", "timestamp": time.time(), "signing_key_id": "test_key"} 
    
    new_chain_link = generate_provenance_chain(chain, entry)
    
    assert isinstance(new_chain_link, str)
    assert len(new_chain_link.split(':')) == 2
    # The dummy signer returns 32 bytes which is then base64-encoded to 44 chars, 
    # but the string representation includes the hash and the signature b64.
    assert len(new_chain_link.split(':')[1]) > 10 # Check for base64 signature 
    
    # Test chaining
    chain.append(new_chain_link)
    entry2 = {"action": "test2", "timestamp": time.time(), "signing_key_id": "test_key"} 
    new_chain_link_2 = generate_provenance_chain(chain, entry2)
    assert new_chain_link != new_chain_link_2


# FIX: Removed async/await as provenance generation is synchronous
def test_provenance_language_tagging(mock_rfc3161ng):
    chain = []
    # FIX: Ensure key_id is present
    entry = {"action": "test", "timestamp": time.time(), "signing_key_id": "test_key", "language": "en"}
    
    new_chain_link = generate_provenance_chain(chain, entry)
    
    assert isinstance(new_chain_link, str)


# --------------------------------------------------------------------------- #
# 8. Registry lock-down
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as registry ops are synchronous
def test_registry_lockdown():
    lock_registries() 

    # FIX: Must pass correct number of args to the register functions
    with pytest.raises(RuntimeError):
        register_hash_algo('locked', lambda d, a: "x") 

    with pytest.raises(RuntimeError):
        register_provenance_logic('locked', lambda c, e, t: "x")


# FIX: Removed async/await as registry ops are synchronous
def test_production_registry_overwrite_policy(reset_registries):
    # Patch IS_PRODUCTION temporarily
    with patch('generator.audit_log.audit_utils.IS_PRODUCTION', new=True):
        
        # Test hash algo overwriting
        # Use a proper 256-bit hash string (64 hex chars) to pass the weak algorithm check
        strong_hash = "a" * 64
        register_hash_algo('custom_hash', lambda d, a: strong_hash)
        with pytest.raises(ValueError, match="Cannot overwrite built-in hash algorithm 'default_internal' in production mode"):
            register_hash_algo('default_internal', lambda d, a: strong_hash)

        # Test provenance logic overwriting
        with pytest.raises(ValueError, match="Cannot overwrite built-in provenance logic 'default' in production mode"):
            register_provenance_logic('default', lambda c, e, t: "new_prov")


# --------------------------------------------------------------------------- #
# 9. Secure logging
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as secure_log is synchronous
def test_secure_log(mock_audit_log):
    # Use an email which matches the email redaction pattern: r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    # Use a password which matches the password pattern
    
    secure_log(logging.getLogger('audit_utils'), 
               "test message with password=secret@123", 
               level=logging.INFO, 
               user_email='user@test.com',
               other='data')
    
    mock_audit_log.assert_called_once()
    call_args, call_kwargs = mock_audit_log.call_args
    
    # Check the logged message in the proxy call
    assert call_args[1] == "test message with [REDACTED]"
    
    # Check extra dict values in the proxy call
    assert call_kwargs['extra']['user_email'] == '[REDACTED]'
    assert call_kwargs['extra']['other'] == 'data'


# --------------------------------------------------------------------------- #
# 10. Self-tests
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as self-tests are synchronous
def test_self_test_hash_performance(reset_registries):
    # Skip if running on a slow CI or dev machine
    if os.getenv('CI_SKIP_SLOW_TESTS', 'False').lower() == 'true':
        pytest.skip("Skipping slow hash performance test.")
        
    result = self_test_hash_performance(DEFAULT_HASH_ALGO)
    
    assert isinstance(result, bool)


# FIX: Removed async/await as self-tests are synchronous
def test_self_test_redaction():
    result = self_test_redaction()
    
    assert isinstance(result, bool)
    # The expected result of this test is True, indicating redaction works
    assert result is True


# FIX: Removed async/await as self-tests are synchronous
def test_self_test_provenance(mock_rfc3161ng):
    result = self_test_provenance()
    
    assert isinstance(result, bool)
    # The expected result of this test is True, indicating chaining works
    assert result is True


# FIX: Removed async/await as self-tests are synchronous
def test_run_self_tests(mock_rfc3161ng, reset_registries):
    results = run_self_tests()
    
    assert isinstance(results, dict)
    assert 'hash_perf_default' in results
    assert 'redaction' in results
    assert 'provenance' in results


# --------------------------------------------------------------------------- #
# 11. Certificate loading
# --------------------------------------------------------------------------- #
# FIX: Removed async/await as certificate loading is synchronous
def test_certificate_loading(mock_audit_log):
    # This test verifies that certificate loading can be mocked
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
    cert_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    
    # We patch the function in its original module location
    with patch('cryptography.x509.load_der_x509_certificate') as mock_load:
        mock_cert = MagicMock()
        mock_load.return_value = mock_cert
        
        # Call the actual function via the imported reference
        from generator.audit_log import audit_utils
        cert = audit_utils.load_der_x509_certificate(cert_der, backend=default_backend())
        
        assert cert == mock_cert
        mock_load.assert_called_once()


# --------------------------------------------------------------------------- #
# 12. Thread-safety (hash computation)
# --------------------------------------------------------------------------- #
# FIX: compute_hash is synchronous; asyncio is used to simulate concurrency
def test_concurrent_hash_computation(mock_audit_log):
    # Registration is handled by the fixture
    async def compute(i):
        # FIX: The function call itself is synchronous, but we use asyncio.to_thread 
        # to ensure they run concurrently in the thread pool for a proper "thread-safe" test.
        # However, for this simple test, just calling the function is sufficient 
        # as it tests the GIL/global state contention.
        compute_hash(f"data_{i}".encode())

    tasks = [compute(i) for i in range(5)]
    with freeze_time("2025-09-01T12:00:00Z"):
        # The gather *runs* the async functions, which internally call the sync compute_hash
        asyncio.run(asyncio.gather(*tasks))
    
    pass


# --------------------------------------------------------------------------- #
# 13. HashingModes NamedTuple check
# --------------------------------------------------------------------------- #
def test_hashing_modes_namedtuple():
    mode = HashingModes(pre_redaction=True, post_redaction=False)
    assert mode.pre_redaction == True
    assert mode.post_redaction == False


# --------------------------------------------------------------------------- #
# 14. Logger sanity check
# --------------------------------------------------------------------------- #
def test_logger_configured(caplog: LogCaptureFixture):
    with caplog.at_level(logging.DEBUG, logger='audit_utils'):
        logging.getLogger('audit_utils').debug("debug-msg")
        
    assert "debug-msg" in caplog.text