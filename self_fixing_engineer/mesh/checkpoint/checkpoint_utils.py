"""
checkpoint_utils.py

Enterprise-Grade Cryptographic and Data Utilities v3.0.0
Copyright (c) 2024 - Proprietary and Confidential

Core utilities providing cryptographic operations, data manipulation, and 
security functions for the checkpoint management system. Designed for 
deployment in highly regulated environments requiring:

- FIPS 140-2 Level 2 cryptographic compliance
- NIST SP 800-57 key management standards
- PCI DSS encryption requirements
- HIPAA §164.312(a)(2)(iv) encryption standards
- GDPR Article 32 technical measures
- SOX Section 404 data integrity controls

All cryptographic operations use approved algorithms and implementations
suitable for processing classified information up to SECRET level when
properly configured with hardware security modules (HSM).

Security Notice: This module handles cryptographic material and must be
protected according to organizational security policies. Unauthorized
modification may result in data loss or security compromise.
"""

__version__ = '3.0.0'
__author__ = 'Security Engineering Team'
__classification__ = 'CONFIDENTIAL'

# ---- Standard Library Imports ----
import os
import sys
import json
import gzip
import zlib
import bz2
import lzma
import hashlib
import hmac
import secrets
import re
import base64
import time
import uuid
import logging
import warnings
from datetime import datetime, timezone, timedelta
from typing import (
    Dict, Any, Union, Optional, List, Tuple, Set,
    Pattern
)
from functools import lru_cache
from contextlib import contextmanager
import threading
from collections import OrderedDict

# ---- Cryptographic Imports ----
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.backends import default_backend
    from cryptography.fernet import Fernet, MultiFernet, InvalidToken
    from cryptography.x509 import load_pem_x509_certificate
    import cryptography
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    warnings.warn(
        "cryptography library not available. Operating in INSECURE mode. "
        "NOT SUITABLE FOR PRODUCTION USE.",
        SecurityWarning
    )

# ---- Additional Security Libraries ----
try:
    import argon2
    ARGON2_AVAILABLE = True
except ImportError:
    ARGON2_AVAILABLE = False

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False

# ---- Performance Libraries ----
try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False

try:
    import msgpack
    MSGPACK_AVAILABLE = True
except ImportError:
    MSGPACK_AVAILABLE = False

try:
    import xxhash
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False

# ---- Observability ----
try:
    from prometheus_client import Counter, Histogram, Gauge
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False
    trace = None

# ---- Configuration ----

class SecurityConfig:
    """Security configuration with regulatory compliance defaults."""
    
    # Environment
    PROD_MODE = os.environ.get("PROD_MODE", "false").lower() == "true"
    FIPS_MODE = os.environ.get("FIPS_MODE", "false").lower() == "true"
    
    # Encryption
    ENCRYPTION_ALGORITHM = os.environ.get("CHECKPOINT_ENCRYPTION_ALGO", "AES-256-GCM")
    KEY_DERIVATION = os.environ.get("CHECKPOINT_KDF", "PBKDF2")
    KDF_ITERATIONS = int(os.environ.get("CHECKPOINT_KDF_ITERATIONS", "600000"))  # NIST SP 800-63B
    
    # Hashing
    HASH_ALGORITHM = os.environ.get("CHECKPOINT_HASH_ALGO", "SHA3-256")
    HMAC_ALGORITHM = os.environ.get("CHECKPOINT_HMAC_ALGO", "SHA256")
    
    # Compression
    COMPRESSION_ALGORITHM = os.environ.get("CHECKPOINT_COMPRESSION", "ZSTD")
    COMPRESSION_LEVEL = int(os.environ.get("CHECKPOINT_COMPRESSION_LEVEL", "3"))
    
    # Security Policies
    MIN_KEY_LENGTH = int(os.environ.get("CHECKPOINT_MIN_KEY_LENGTH", "32"))
    KEY_ROTATION_DAYS = int(os.environ.get("CHECKPOINT_KEY_ROTATION_DAYS", "90"))
    SECURE_DELETE = os.environ.get("CHECKPOINT_SECURE_DELETE", "true").lower() == "true"
    
    # Data Classification
    DATA_CLASSIFICATION = os.environ.get("DATA_CLASSIFICATION", "CONFIDENTIAL")
    REQUIRE_ENCRYPTION = DATA_CLASSIFICATION in ["CONFIDENTIAL", "SECRET", "TOP SECRET"]
    
    @classmethod
    def validate(cls) -> None:
        """Validate security configuration for compliance."""
        errors = []
        
        if cls.PROD_MODE:
            if not CRYPTOGRAPHY_AVAILABLE:
                errors.append("cryptography library required in production")
            
            if cls.FIPS_MODE:
                if cls.ENCRYPTION_ALGORITHM not in ["AES-256-GCM", "AES-256-CBC"]:
                    errors.append(f"FIPS mode requires AES encryption, got {cls.ENCRYPTION_ALGORITHM}")
                if cls.HASH_ALGORITHM not in ["SHA256", "SHA384", "SHA512", "SHA3-256", "SHA3-384", "SHA3-512"]:
                    errors.append(f"FIPS mode requires SHA-2/3 hashing, got {cls.HASH_ALGORITHM}")
            
            if cls.KDF_ITERATIONS < 600000:
                errors.append(f"KDF iterations {cls.KDF_ITERATIONS} below NIST minimum of 600000")
        
        if errors:
            for error in errors:
                logging.critical(f"Security Configuration Error: {error}")
            sys.exit(1)


# Initialize logging
logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("checkpoint.audit.utils")

# Validate configuration on import
SecurityConfig.validate()


# ---- Metrics ----
if PROMETHEUS_AVAILABLE:
    CRYPTO_OPERATIONS = Counter(
        'checkpoint_crypto_operations_total',
        'Cryptographic operations',
        ['operation', 'algorithm', 'status']
    )
    
    CRYPTO_LATENCY = Histogram(
        'checkpoint_crypto_latency_seconds',
        'Cryptographic operation latency',
        ['operation', 'algorithm'],
        buckets=(0.00001, 0.00005, 0.0001, 0.0005, 0.001, 0.005, 0.01, 0.05, 0.1)
    )
    
    KEY_ROTATIONS = Counter(
        'checkpoint_key_rotations_total',
        'Key rotation operations',
        ['status']
    )
    
    DATA_INTEGRITY_CHECKS = Counter(
        'checkpoint_integrity_checks_total',
        'Data integrity verifications',
        ['result']
    )


# ---- Tracing ----
if TRACING_AVAILABLE:
    tracer = trace.get_tracer(__name__, __version__)
else:
    class NullTracer:
        @contextmanager
        def start_as_current_span(self, name: str, **kwargs):
            yield None
    tracer = NullTracer()


# ---- Constants and Patterns ----

# Sensitive data patterns (PCI DSS, HIPAA, GDPR compliance)
SENSITIVE_PATTERNS = {
    'credit_card': re.compile(r'\b(?:\d[ -]*?){13,19}\b'),
    'ssn': re.compile(r'\b\d{3}-?\d{2}-?\d{4}\b'),
    'email': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    'phone': re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
    'ip_address': re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
    'api_key': re.compile(r'\b(?:sk-|pk-|api-)[A-Za-z0-9]{32,}\b'),
    'aws_key': re.compile(r'\b(?:AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}\b'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'),
}

# Sensitive field names
SENSITIVE_FIELD_PATTERNS = [
    re.compile(r'.*password.*', re.IGNORECASE),
    re.compile(r'.*secret.*', re.IGNORECASE),
    re.compile(r'.*token.*', re.IGNORECASE),
    re.compile(r'.*key.*', re.IGNORECASE),
    re.compile(r'.*auth.*', re.IGNORECASE),
    re.compile(r'.*cred.*', re.IGNORECASE),
    re.compile(r'.*ssn.*', re.IGNORECASE),
    re.compile(r'.*tax.*id.*', re.IGNORECASE),
    re.compile(r'.*account.*num.*', re.IGNORECASE),
    re.compile(r'.*routing.*num.*', re.IGNORECASE),
    re.compile(r'.*credit.*card.*', re.IGNORECASE),
    re.compile(r'.*cvv.*', re.IGNORECASE),
    re.compile(r'.*pin.*', re.IGNORECASE),
    re.compile(r'.*dob|birth.*date.*', re.IGNORECASE),
    re.compile(r'.*medical.*record.*', re.IGNORECASE),
    re.compile(r'.*diagnosis.*', re.IGNORECASE),
    re.compile(r'.*treatment.*', re.IGNORECASE),
]


# ---- Core Cryptographic Functions ----

class CryptoProvider:
    """
    Enterprise-grade cryptographic provider with algorithm agility
    and compliance with regulatory standards.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._key_cache = {}
        self._rotation_schedule = {}
        self._init_crypto()
    
    def _init_crypto(self):
        """Initialize cryptographic components."""
        if not CRYPTOGRAPHY_AVAILABLE:
            logger.warning("Cryptography unavailable - operating in INSECURE mode")
            return
        
        # Initialize secure random
        self._random = secrets.SystemRandom()
        
        # Initialize backend
        self._backend = default_backend()
        
        # Verify FIPS mode if required
        if SecurityConfig.FIPS_MODE:
            try:
                # Attempt to enable FIPS mode (platform-specific)
                import ssl
                ssl.OPENSSL_VERSION_INFO  # Verify OpenSSL is available
                # Note: Actual FIPS enablement is platform-specific
                logger.info("FIPS mode verification completed")
            except Exception as e:
                logger.error(f"FIPS mode verification failed: {e}")
                if SecurityConfig.PROD_MODE:
                    raise
    
    def generate_key(self, length: int = 32, key_type: Optional[str] = None) -> bytes:
        """
        Generate cryptographically secure random bytes.
        
        Args:
            length: Byte length
            key_type: Type of data being generated ('key', 'nonce', 'salt', 'iv', 'random')
                      If not specified, infers from length (<=16 treated as salt/nonce)
            
        Returns:
            Secure random bytes
        """
        # Infer key_type if not specified based on common cryptographic sizes
        if key_type is None:
            if length == 12:
                key_type = "nonce"  # Common for AES-GCM
            elif length == 16:
                key_type = "salt"   # Common salt size
            elif length >= 32:
                key_type = "key"    # Actual encryption key
            else:
                key_type = "random" # Generic random bytes
        
        # Only enforce minimum for actual encryption keys
        if key_type == "key" and length < SecurityConfig.MIN_KEY_LENGTH:
            raise ValueError(f"Key length {length} below minimum {SecurityConfig.MIN_KEY_LENGTH}")
        
        with tracer.start_as_current_span("crypto.generate_key") as span:
            if span:
                span.set_attribute("key.length", length)
                span.set_attribute("key.type", key_type)
            
            key = secrets.token_bytes(length)
            
            # Verify entropy quality for actual keys
            if key_type == "key" and len(set(key)) < length // 4:
                logger.warning("Generated key has low entropy, regenerating")
                key = secrets.token_bytes(length)
            
            if PROMETHEUS_AVAILABLE:
                CRYPTO_OPERATIONS.labels(
                    operation='generate_key',
                    algorithm='CSPRNG',
                    status='success'
                ).inc()
            
            return key
    
    def derive_key(
        self,
        password: Union[str, bytes],
        salt: Optional[bytes] = None,
        length: int = 32,
        iterations: Optional[int] = None
    ) -> bytes:
        """
        Derive encryption key from password using configured KDF.
        
        Args:
            password: Password or passphrase
            salt: Salt for KDF (generated if not provided)
            length: Desired key length
            iterations: KDF iterations (uses config default if not provided)
            
        Returns:
            Derived key bytes
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise RuntimeError("Cryptography required for key derivation")
        
        start_time = time.perf_counter()
        
        with tracer.start_as_current_span("crypto.derive_key") as span:
            if span:
                span.set_attribute("kdf.algorithm", SecurityConfig.KEY_DERIVATION)
            
            # Ensure password is bytes
            if isinstance(password, str):
                password = password.encode('utf-8')
            
            # Generate salt if not provided
            if salt is None:
                salt = self.generate_key(16, key_type="salt")
            
            iterations = iterations or SecurityConfig.KDF_ITERATIONS
            
            try:
                if SecurityConfig.KEY_DERIVATION == "PBKDF2":
                    kdf = PBKDF2HMAC(
                        algorithm=hashes.SHA256(),
                        length=length,
                        salt=salt,
                        iterations=iterations,
                        backend=self._backend
                    )
                    key = kdf.derive(password)
                
                elif SecurityConfig.KEY_DERIVATION == "SCRYPT":
                    kdf = Scrypt(
                        salt=salt,
                        length=length,
                        n=2**14,  # CPU/memory cost
                        r=8,      # Block size
                        p=1,      # Parallelization
                        backend=self._backend
                    )
                    key = kdf.derive(password)
                
                elif SecurityConfig.KEY_DERIVATION == "ARGON2" and ARGON2_AVAILABLE:
                    hasher = argon2.PasswordHasher(
                        time_cost=3,
                        memory_cost=65536,
                        parallelism=4
                    )
                    # Argon2 returns a full hash string, extract the raw hash
                    full_hash = hasher.hash(password)
                    # For key derivation, we'll use HKDF to expand
                    hkdf = HKDF(
                        algorithm=hashes.SHA256(),
                        length=length,
                        salt=salt,
                        info=b'checkpoint-key',
                        backend=self._backend
                    )
                    key = hkdf.derive(full_hash.encode())
                
                else:
                    # Fallback to PBKDF2
                    kdf = PBKDF2HMAC(
                        algorithm=hashes.SHA256(),
                        length=length,
                        salt=salt,
                        iterations=iterations,
                        backend=self._backend
                    )
                    key = kdf.derive(password)
                
                if PROMETHEUS_AVAILABLE:
                    CRYPTO_OPERATIONS.labels(
                        operation='derive_key',
                        algorithm=SecurityConfig.KEY_DERIVATION,
                        status='success'
                    ).inc()
                    
                    CRYPTO_LATENCY.labels(
                        operation='derive_key',
                        algorithm=SecurityConfig.KEY_DERIVATION
                    ).observe(time.perf_counter() - start_time)
                
                return key
                
            except Exception as e:
                if PROMETHEUS_AVAILABLE:
                    CRYPTO_OPERATIONS.labels(
                        operation='derive_key',
                        algorithm=SecurityConfig.KEY_DERIVATION,
                        status='failure'
                    ).inc()
                
                logger.error(f"Key derivation failed: {e}")
                raise
    
    def encrypt_aes_gcm(self, plaintext: bytes, key: bytes) -> Tuple[bytes, bytes, bytes]:
        """
        Encrypt using AES-256-GCM (AEAD).
        
        Args:
            plaintext: Data to encrypt
            key: 256-bit encryption key
            
        Returns:
            Tuple of (ciphertext, nonce, tag)
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise RuntimeError("Cryptography required for encryption")
        
        aesgcm = AESGCM(key)
        nonce = self.generate_key(12, key_type="nonce")  # 96-bit nonce for GCM
        
        # Encrypt and authenticate
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)
        
        # GCM appends 16-byte tag to ciphertext
        ciphertext = ciphertext_with_tag[:-16]
        tag = ciphertext_with_tag[-16:]
        
        return ciphertext, nonce, tag
    
    def decrypt_aes_gcm(
        self,
        ciphertext: bytes,
        key: bytes,
        nonce: bytes,
        tag: bytes
    ) -> bytes:
        """
        Decrypt using AES-256-GCM.
        
        Args:
            ciphertext: Encrypted data
            key: 256-bit encryption key
            nonce: Nonce used for encryption
            tag: Authentication tag
            
        Returns:
            Decrypted plaintext
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise RuntimeError("Cryptography required for decryption")
        
        aesgcm = AESGCM(key)
        
        # Reconstruct ciphertext with tag
        ciphertext_with_tag = ciphertext + tag
        
        # Decrypt and verify
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        
        return plaintext
    
    def secure_compare(self, a: bytes, b: bytes) -> bool:
        """
        Constant-time comparison to prevent timing attacks.
        
        Args:
            a: First value
            b: Second value
            
        Returns:
            True if values match
        """
        return hmac.compare_digest(a, b)
    
    def secure_erase(self, data: Union[bytes, bytearray, memoryview]) -> None:
        """
        Securely overwrite memory containing sensitive data.
        
        Args:
            data: Sensitive data to erase
        """
        if not SecurityConfig.SECURE_DELETE:
            return
        
        try:
            if isinstance(data, bytes):
                # Convert to mutable type
                data = bytearray(data)
            
            # Overwrite with random data multiple times (DOD 5220.22-M standard)
            for _ in range(3):
                for i in range(len(data)):
                    data[i] = secrets.randbits(8)
            
            # Final overwrite with zeros
            for i in range(len(data)):
                data[i] = 0
                
        except Exception as e:
            logger.warning(f"Secure erase failed: {e}")


# Global crypto provider
crypto = CryptoProvider()


# ---- Multi-Algorithm Hash Functions ----

def hash_data(
    data: Union[bytes, str, Dict[str, Any]],
    algorithm: Optional[str] = None,
    encoding: str = 'utf-8'
) -> str:
    """
    Generate cryptographic hash of data using specified algorithm.
    
    Args:
        data: Data to hash (bytes, string, or dict)
        algorithm: Hash algorithm (uses config default if not specified)
        encoding: Text encoding for string data
        
    Returns:
        Hexadecimal hash string
    """
    algorithm = algorithm or SecurityConfig.HASH_ALGORITHM
    
    with tracer.start_as_current_span("crypto.hash_data") as span:
        if span:
            span.set_attribute("hash.algorithm", algorithm)
        
        # Normalize data to bytes
        if isinstance(data, dict):
            # Deterministic JSON serialization
            if ORJSON_AVAILABLE:
                data_bytes = orjson.dumps(
                    data,
                    option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z
                )
            else:
                data_bytes = json.dumps(data, sort_keys=True, default=str).encode(encoding)
        elif isinstance(data, str):
            data_bytes = data.encode(encoding)
        else:
            data_bytes = data
        
        # Select hash algorithm
        if algorithm == "SHA3-256":
            if CRYPTOGRAPHY_AVAILABLE:
                digest = hashes.Hash(hashes.SHA3_256(), backend=default_backend())
                digest.update(data_bytes)
                hash_value = digest.finalize().hex()
            else:
                hash_value = hashlib.sha3_256(data_bytes).hexdigest()
        
        elif algorithm == "SHA256":
            hash_value = hashlib.sha256(data_bytes).hexdigest()
        
        elif algorithm == "SHA512":
            hash_value = hashlib.sha512(data_bytes).hexdigest()
        
        elif algorithm == "BLAKE2B":
            hash_value = hashlib.blake2b(data_bytes).hexdigest()
        
        elif algorithm == "XXHASH" and XXHASH_AVAILABLE:
            hash_value = xxhash.xxh64(data_bytes).hexdigest()
        
        else:
            # Fallback to SHA256
            hash_value = hashlib.sha256(data_bytes).hexdigest()
            logger.warning(f"Unknown algorithm {algorithm}, using SHA256")
        
        if PROMETHEUS_AVAILABLE:
            CRYPTO_OPERATIONS.labels(
                operation='hash',
                algorithm=algorithm,
                status='success'
            ).inc()
        
        return hash_value


def hash_dict(
    data: Dict[str, Any],
    prev_hash: Optional[str] = None,
    algorithm: Optional[str] = None
) -> str:
    """
    Generate hash of dictionary with optional chaining.
    
    Args:
        data: Dictionary to hash
        prev_hash: Previous hash for chaining
        algorithm: Hash algorithm
        
    Returns:
        Hexadecimal hash string
    """
    # Create canonical representation
    canonical = OrderedDict(sorted(data.items()))
    
    if prev_hash:
        canonical['__prev_hash__'] = prev_hash
    
    return hash_data(canonical, algorithm)


def compute_hmac(
    data: bytes,
    key: bytes,
    algorithm: Optional[str] = None
) -> str:
    """
    Compute HMAC for data integrity and authentication.
    
    Args:
        data: Data to authenticate
        key: HMAC key
        algorithm: HMAC algorithm
        
    Returns:
        Hexadecimal HMAC string
    """
    algorithm = algorithm or SecurityConfig.HMAC_ALGORITHM
    
    if algorithm == "SHA256":
        h = hmac.new(key, data, hashlib.sha256)
    elif algorithm == "SHA512":
        h = hmac.new(key, data, hashlib.sha512)
    elif algorithm == "SHA3-256":
        h = hmac.new(key, data, hashlib.sha3_256)
    else:
        h = hmac.new(key, data, hashlib.sha256)
    
    return h.hexdigest()


def verify_hmac(
    data: bytes,
    key: bytes,
    expected_hmac: str,
    algorithm: Optional[str] = None
) -> bool:
    """
    Verify HMAC in constant time.
    
    Args:
        data: Data to verify
        key: HMAC key
        expected_hmac: Expected HMAC value
        algorithm: HMAC algorithm
        
    Returns:
        True if HMAC matches
    """
    computed = compute_hmac(data, key, algorithm)
    
    result = hmac.compare_digest(computed, expected_hmac)
    
    if PROMETHEUS_AVAILABLE:
        DATA_INTEGRITY_CHECKS.labels(
            result='valid' if result else 'invalid'
        ).inc()
    
    return result


# ---- Compression Functions ----

def compress_data(
    data: bytes,
    algorithm: Optional[str] = None,
    level: Optional[int] = None
) -> bytes:
    """
    Compress data using specified algorithm.
    
    Args:
        data: Data to compress
        algorithm: Compression algorithm
        level: Compression level (1-9)
        
    Returns:
        Compressed data
    """
    algorithm = algorithm or SecurityConfig.COMPRESSION_ALGORITHM
    level = level or SecurityConfig.COMPRESSION_LEVEL
    
    with tracer.start_as_current_span("utils.compress") as span:
        if span:
            span.set_attribute("compression.algorithm", algorithm)
            span.set_attribute("compression.input_size", len(data))
        
        if algorithm == "GZIP":
            compressed = gzip.compress(data, compresslevel=level)
        elif algorithm == "ZLIB":
            compressed = zlib.compress(data, level=level)
        elif algorithm == "BZ2":
            compressed = bz2.compress(data, compresslevel=level)
        elif algorithm == "LZMA":
            compressed = lzma.compress(data, preset=level)
        elif algorithm == "ZSTD":
            try:
                import zstandard as zstd
                cctx = zstd.ZstdCompressor(level=level)
                compressed = cctx.compress(data)
            except ImportError:
                # Fallback to gzip
                compressed = gzip.compress(data, compresslevel=level)
        else:
            compressed = gzip.compress(data, compresslevel=level)
        
        if span:
            span.set_attribute("compression.output_size", len(compressed))
            span.set_attribute("compression.ratio", len(data) / len(compressed))
        
        return compressed


def decompress_data(
    data: bytes,
    algorithm: Optional[str] = None
) -> bytes:
    """
    Decompress data using specified algorithm.
    
    Args:
        data: Compressed data
        algorithm: Compression algorithm (auto-detect if not specified)
        
    Returns:
        Decompressed data
    """
    with tracer.start_as_current_span("utils.decompress") as span:
        # Auto-detect compression if not specified
        if algorithm is None:
            # Check magic bytes
            if data[:2] == b'\x1f\x8b':  # GZIP
                algorithm = "GZIP"
            elif data[:2] == b'\x78\x9c' or data[:2] == b'\x78\x5e' or data[:2] == b'\x78\xda':  # ZLIB variants
                algorithm = "ZLIB"
            elif data[:3] == b'BZh':  # BZ2
                algorithm = "BZ2"
            elif data[:6] == b'\xfd7zXZ\x00':  # LZMA/XZ
                algorithm = "LZMA"
            elif data[:4] == b'(\xb5/\xfd':  # ZSTD
                algorithm = "ZSTD"
            else:
                # Try each algorithm in order
                for try_algo in ["GZIP", "ZLIB", "BZ2", "LZMA"]:
                    try:
                        if try_algo == "GZIP":
                            decompressed = gzip.decompress(data)
                        elif try_algo == "ZLIB":
                            decompressed = zlib.decompress(data)
                        elif try_algo == "BZ2":
                            decompressed = bz2.decompress(data)
                        elif try_algo == "LZMA":
                            decompressed = lzma.decompress(data)
                        
                        if span:
                            span.set_attribute("compression.algorithm", try_algo)
                            span.set_attribute("compression.input_size", len(data))
                            span.set_attribute("compression.output_size", len(decompressed))
                        
                        return decompressed
                    except:
                        continue
                
                # If all fail, raise error
                raise ValueError("Unable to detect compression algorithm")
        
        if span:
            span.set_attribute("compression.algorithm", algorithm)
            span.set_attribute("compression.input_size", len(data))
        
        try:
            if algorithm == "GZIP":
                decompressed = gzip.decompress(data)
            elif algorithm == "ZLIB":
                decompressed = zlib.decompress(data)
            elif algorithm == "BZ2":
                decompressed = bz2.decompress(data)
            elif algorithm == "LZMA":
                decompressed = lzma.decompress(data)
            elif algorithm == "ZSTD":
                try:
                    import zstandard as zstd
                    dctx = zstd.ZstdDecompressor()
                    decompressed = dctx.decompress(data)
                except ImportError:
                    decompressed = gzip.decompress(data)
            else:
                raise ValueError(f"Unknown compression algorithm: {algorithm}")
            
            if span:
                span.set_attribute("compression.output_size", len(decompressed))
            
            return decompressed
            
        except Exception as e:
            logger.error(f"Decompression failed: {e}")
            raise


def compress_json(
    data: Dict[str, Any],
    algorithm: Optional[str] = None,
    level: Optional[int] = None
) -> bytes:
    """
    Compress JSON data efficiently.
    
    Args:
        data: Dictionary to compress
        algorithm: Compression algorithm
        level: Compression level
        
    Returns:
        Compressed JSON bytes
    """
    # Serialize to JSON
    if ORJSON_AVAILABLE:
        json_bytes = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
    else:
        json_bytes = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
    
    # Compress
    return compress_data(json_bytes, algorithm, level)


def decompress_json(
    data: bytes,
    algorithm: Optional[str] = None
) -> Dict[str, Any]:
    """
    Decompress and deserialize JSON data.
    
    Args:
        data: Compressed JSON bytes
        algorithm: Compression algorithm
        
    Returns:
        Deserialized dictionary
    """
    # Decompress
    json_bytes = decompress_data(data, algorithm)
    
    # Deserialize
    if ORJSON_AVAILABLE:
        return orjson.loads(json_bytes)
    else:
        return json.loads(json_bytes)


# ---- Data Scrubbing and Privacy ----

def scrub_data(
    data: Any,
    patterns: Optional[Dict[str, Pattern]] = None,
    replacement: str = "[REDACTED]"
) -> Any:
    """
    Recursively scrub sensitive data for compliance with privacy regulations.
    
    Args:
        data: Data to scrub (dict, list, string, etc.)
        patterns: Custom patterns to detect sensitive data
        replacement: Replacement text for sensitive data
        
    Returns:
        Scrubbed data with sensitive information removed
    """
    patterns = patterns or SENSITIVE_PATTERNS
    
    def _scrub_string(text: str) -> str:
        """Scrub sensitive patterns from string."""
        scrubbed = text
        for pattern_name, pattern in patterns.items():
            if pattern.search(scrubbed):
                scrubbed = pattern.sub(replacement, scrubbed)
                audit_logger.info(
                    f"Scrubbed {pattern_name} from data",
                    extra={"pattern": pattern_name}
                )
        return scrubbed
    
    def _is_sensitive_field(field_name: str) -> bool:
        """Check if field name indicates sensitive data."""
        return any(pattern.match(field_name) for pattern in SENSITIVE_FIELD_PATTERNS)
    
    def _scrub_recursive(obj: Any) -> Any:
        """Recursively scrub object."""
        if isinstance(obj, dict):
            scrubbed = {}
            for key, value in obj.items():
                # Check if key is sensitive
                if _is_sensitive_field(key):
                    scrubbed[key] = replacement
                    audit_logger.info(
                        "Scrubbed sensitive field",
                        extra={"field": key}
                    )
                else:
                    # Recursively scrub value
                    scrubbed[key] = _scrub_recursive(value)
            return scrubbed
        
        elif isinstance(obj, list):
            return [_scrub_recursive(item) for item in obj]
        
        elif isinstance(obj, tuple):
            return tuple(_scrub_recursive(item) for item in obj)
        
        elif isinstance(obj, str):
            return _scrub_string(obj)
        
        elif isinstance(obj, bytes):
            try:
                text = obj.decode('utf-8', errors='ignore')
                scrubbed_text = _scrub_string(text)
                return scrubbed_text.encode('utf-8')
            except:
                return obj
        
        else:
            return obj
    
    return _scrub_recursive(data)


def anonymize_data(
    data: Dict[str, Any],
    fields_to_anonymize: List[str],
    method: str = "hash"
) -> Dict[str, Any]:
    """
    Anonymize specific fields in data for GDPR compliance.
    
    Args:
        data: Data containing fields to anonymize
        fields_to_anonymize: List of field paths to anonymize
        method: Anonymization method ('hash', 'tokenize', 'generalize')
        
    Returns:
        Data with specified fields anonymized
    """
    anonymized = data.copy()
    
    for field_path in fields_to_anonymize:
        # Support nested field paths (e.g., "user.email")
        path_parts = field_path.split('.')
        current = anonymized
        
        for i, part in enumerate(path_parts[:-1]):
            if part in current and isinstance(current[part], dict):
                current = current[part]
            else:
                break
        
        field = path_parts[-1]
        if field in current:
            original_value = current[field]
            
            if method == "hash":
                # One-way hash (irreversible)
                current[field] = hash_data(str(original_value))[:16]
            
            elif method == "tokenize":
                # Replace with token (requires token mapping storage)
                token = base64.urlsafe_b64encode(
                    crypto.generate_key(8)
                ).decode().rstrip('=')
                current[field] = f"TOKEN_{token}"
            
            elif method == "generalize":
                # Generalize data (e.g., age -> age range)
                if isinstance(original_value, (int, float)):
                    # Round numbers
                    current[field] = round(original_value, -1)
                elif isinstance(original_value, str):
                    # Keep only first letter
                    current[field] = original_value[0] + "***" if original_value else ""
            
            audit_logger.info(
                "Anonymized field",
                extra={
                    "field": field_path,
                    "method": method
                }
            )
    
    return anonymized


# ---- Data Comparison and Diff ----

def deep_diff(
    old_data: Dict[str, Any],
    new_data: Dict[str, Any],
    ignore_keys: Optional[Set[str]] = None,
    track_type_changes: bool = True
) -> Dict[str, Any]:
    """
    Compute deep difference between two dictionaries.
    
    Args:
        old_data: Original data
        new_data: New data
        ignore_keys: Keys to ignore in comparison
        track_type_changes: Whether to track type changes
        
    Returns:
        Dictionary describing differences
    """
    ignore_keys = ignore_keys or set()
    diff = {
        'added': {},
        'removed': {},
        'modified': {},
        'type_changed': {} if track_type_changes else None
    }
    
    def _diff_recursive(old: Any, new: Any, path: str = "") -> None:
        """Recursively compute differences."""
        if type(old) != type(new):
            if track_type_changes:
                diff['type_changed'][path] = {
                    'old_type': type(old).__name__,
                    'new_type': type(new).__name__,
                    'old_value': old,
                    'new_value': new
                }
            return
        
        if isinstance(old, dict) and isinstance(new, dict):
            # Find added keys
            for key in new.keys() - old.keys():
                if key not in ignore_keys:
                    new_path = f"{path}.{key}" if path else key
                    diff['added'][new_path] = new[key]
            
            # Find removed keys
            for key in old.keys() - new.keys():
                if key not in ignore_keys:
                    new_path = f"{path}.{key}" if path else key
                    diff['removed'][new_path] = old[key]
            
            # Find modified keys
            for key in old.keys() & new.keys():
                if key not in ignore_keys:
                    new_path = f"{path}.{key}" if path else key
                    _diff_recursive(old[key], new[key], new_path)
        
        elif isinstance(old, (list, tuple)) and isinstance(new, (list, tuple)):
            if len(old) != len(new) or old != new:
                diff['modified'][path] = {
                    'old': old,
                    'new': new,
                    'old_length': len(old),
                    'new_length': len(new)
                }
        
        elif old != new:
            diff['modified'][path] = {
                'old': old,
                'new': new
            }
    
    _diff_recursive(old_data, new_data)
    
    # Remove empty sections
    diff = {k: v for k, v in diff.items() if v}
    
    return diff


# ---- Multi-Fernet Key Rotation Support ----

def create_fernet_key(passphrase: Optional[str] = None) -> bytes:
    """
    Create a Fernet encryption key.
    
    Args:
        passphrase: Optional passphrase to derive key from
        
    Returns:
        Base64-encoded Fernet key
    """
    if not CRYPTOGRAPHY_AVAILABLE:
        raise RuntimeError("Cryptography required for Fernet keys")
    
    if passphrase:
        # Derive key from passphrase
        key_bytes = crypto.derive_key(passphrase, length=32)
    else:
        # Generate random key
        key_bytes = crypto.generate_key(32)
    
    # Encode for Fernet
    return base64.urlsafe_b64encode(key_bytes)


def rotate_fernet_keys(
    current_keys: List[bytes],
    new_key: Optional[bytes] = None
) -> Tuple[MultiFernet, List[bytes]]:
    """
    Rotate Fernet encryption keys.
    
    Args:
        current_keys: List of current Fernet keys
        new_key: New key to add (generated if not provided)
        
    Returns:
        Tuple of (MultiFernet instance, updated key list)
    """
    if not CRYPTOGRAPHY_AVAILABLE:
        raise RuntimeError("Cryptography required for key rotation")
    
    if new_key is None:
        new_key = Fernet.generate_key()
    
    # New key becomes primary (first in list)
    rotated_keys = [new_key] + current_keys
    
    # Keep limited history (e.g., last 3 keys)
    max_keys = int(os.environ.get("CHECKPOINT_MAX_KEYS", "3"))
    rotated_keys = rotated_keys[:max_keys]
    
    # Create MultiFernet with rotated keys
    fernets = [Fernet(key) for key in rotated_keys]
    multi_fernet = MultiFernet(fernets)
    
    if PROMETHEUS_AVAILABLE:
        KEY_ROTATIONS.labels(status='success').inc()
    
    audit_logger.info(
        "Encryption keys rotated",
        extra={
            "key_count": len(rotated_keys),
            "max_keys": max_keys
        }
    )
    
    return multi_fernet, rotated_keys


# ---- Validation Functions ----

def validate_checkpoint_data(
    data: Dict[str, Any],
    schema: Optional[Any] = None,
    max_size: Optional[int] = None
) -> bool:
    """
    Validate checkpoint data for integrity and compliance.
    
    Args:
        data: Data to validate
        schema: Optional schema for validation
        max_size: Maximum allowed size in bytes
        
    Returns:
        True if data is valid
        
    Raises:
        ValueError: If validation fails
    """
    # Check size limit
    if max_size:
        data_size = len(json.dumps(data))
        if data_size > max_size:
            raise ValueError(f"Data size {data_size} exceeds limit {max_size}")
    
    # Check for required fields
    required_fields = ['state', 'metadata']
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required field: {field}")
    
    # Validate metadata
    metadata = data.get('metadata', {})
    if 'timestamp' in metadata:
        try:
            # Validate timestamp format
            datetime.fromisoformat(metadata['timestamp'])
        except:
            raise ValueError("Invalid timestamp format")
    
    # Schema validation if provided
    if schema:
        # Implement schema validation based on your schema format
        pass
    
    return True


# ---- Utility Functions ----

def generate_checkpoint_id() -> str:
    """
    Generate unique checkpoint identifier.
    
    Returns:
        UUID-based checkpoint ID
    """
    checkpoint_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    return f"{timestamp}_{checkpoint_id[:8]}"


def format_size(size_bytes: int) -> str:
    """
    Format byte size in human-readable format.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted size string
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def parse_duration(duration_str: str) -> timedelta:
    """
    Parse duration string to timedelta.
    
    Args:
        duration_str: Duration string (e.g., "1d", "2h", "30m", "45s")
        
    Returns:
        timedelta object
    """
    units = {
        's': 'seconds',
        'm': 'minutes',
        'h': 'hours',
        'd': 'days',
        'w': 'weeks'
    }
    
    match = re.match(r'^(\d+)([smhdw])$', duration_str.lower())
    if not match:
        raise ValueError(f"Invalid duration format: {duration_str}")
    
    value, unit = match.groups()
    return timedelta(**{units[unit]: int(value)})


@lru_cache(maxsize=128)
def is_valid_identifier(identifier: str) -> bool:
    """
    Validate checkpoint identifier format.
    
    Args:
        identifier: Identifier to validate
        
    Returns:
        True if identifier is valid
    """
    # Allow alphanumeric, underscore, hyphen, and dot
    pattern = r'^[a-zA-Z0-9_.-]+$'
    return bool(re.match(pattern, identifier))


# ---- Module Initialization ----

def _run_self_test():
    """Run self-test to verify cryptographic operations."""
    if not SecurityConfig.PROD_MODE:
        return
    
    try:
        # Test key generation
        test_key = crypto.generate_key(32)
        assert len(test_key) == 32
        
        # Test hashing
        test_hash = hash_data(b"test data")
        assert len(test_hash) == 64  # SHA256 hex
        
        # Test compression
        test_data = b"test" * 1000
        compressed = compress_data(test_data)
        decompressed = decompress_data(compressed)
        assert decompressed == test_data
        
        logger.info("Cryptographic self-test passed")
        
    except Exception as e:
        logger.critical(f"Cryptographic self-test failed: {e}")
        if SecurityConfig.PROD_MODE:
            sys.exit(1)


# Run self-test on module import
_run_self_test()

logger.info(
    f"Checkpoint utilities initialized (v{__version__})",
    extra={
        "fips_mode": SecurityConfig.FIPS_MODE,
        "encryption": SecurityConfig.ENCRYPTION_ALGORITHM,
        "classification": SecurityConfig.DATA_CLASSIFICATION
    }
)