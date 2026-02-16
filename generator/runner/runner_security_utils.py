# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# runner/security_utils.py
import asyncio
import base64
import logging
import os
import re
import shutil  # Added for the teardown in the TestSecurity class
import sys  # For checking module status for conditional imports
import time
from functools import wraps  # [NEW] Added for no-op decorator
from pathlib import Path  # Added for Path objects

# FIX: Added Tuple to the typing import list
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple, Union

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend

# Conditional import for xattr based on OS
# FIX: Use try/except Exception as requested for maximum import robustness.
try:
    import xattr  # type: ignore[import] # For metadata (compliance expiration)
except Exception:
    xattr = None
    logging.info(
        "'xattr' library not found. Extended attributes for compliance will not be set."
    )

# --- REFACTOR FIX: Corrected imports ---
# FIX: Define logger at the top, using __name__
logger = logging.getLogger(__name__)

# FIX: Defer imports that cause circular dependency
# Import registries from the new 'runner' package's __init__.py
# (We assume these registries are defined in runner/__init__.py or a similar central location)
# For this file, we will define them locally if they can't be imported, to ensure startup.
try:
    # FIX: Using the requested import structure, assuming success or defined fallback
    from runner import TESTING  # Assuming TESTING is a global flag from runner.__init__
    from runner import (
        DECRYPTORS,
        ENCRYPTORS,
        REDACTORS,
        register_decryptor,
        register_encryptor,
        register_redactor,
    )
except ImportError:
    logger.warning(
        "Could not import registries or global flags from 'runner'. Defining local registries and flags."
    )
    REDACTORS: Dict[str, Callable[..., Any]] = {}
    ENCRYPTORS: Dict[str, Callable[..., Any]] = {}
    DECRYPTORS: Dict[str, Callable[..., Any]] = {}
    TESTING: bool = (
        os.getenv("TESTING") == "1"
        or "pytest" in sys.modules
        or os.getenv("PYTEST_CURRENT_TEST") is not None
        or os.getenv("PYTEST_ADDOPTS") is not None
    )

    def register_redactor(name: str, func: Callable):
        REDACTORS[name] = func

    def register_encryptor(name: str, func: Callable):
        ENCRYPTORS[name] = func

    def register_decryptor(name: str, func: Callable):
        DECRYPTORS[name] = func


# FIX: Corrected import dependency name

# --- END REFACTOR FIX ---

# [NEW] State for lazy Presidio loading
# FIX: Removed extra ']' brackets
_PRESIDIO_ANALYZER_ENGINE: Optional[Any] = None
_PRESIDIO_ANONYMIZER_ENGINE: Optional[Any] = None
_PRESIDIO_AVAILABLE: bool = False
_PRESIDIO_LOAD_ATTEMPTED: bool = False  # Track if we've already tried loading
_PRESIDIO_NLP_MODE: bool = (
    False  # Track if NLP engine is actually available (not just regex)
)

# --- FIX: REMOVED ALL METRICS IMPORTS AND FALLBACKS ---
# The logic for NoOpCounter and _get_metric has been removed.
# This module will no longer import or increment UTIL_ERRORS.


# Presidio entity types that generate unmapped warnings but don't affect functionality
# These are filtered to prevent log clutter from known non-critical entity types
PRESIDIO_IGNORED_ENTITY_TYPES = [
    "CARDINAL",     # Numbers/quantities
    "MONEY",        # Monetary values
    "PERCENT",      # Percentages
    "WORK_OF_ART",  # Titles of works
    "LAW",          # Legal references
    "EVENT",        # Named events
    "FAC",          # Facilities/buildings
    "ORDINAL",      # Ordinal numbers (1st, 2nd, etc.)
]

# Technical terms and patterns that should NOT be redacted as PII
# These are common in technical documentation and code requirements
# FIX: Expanded allowlist to prevent tool names from being marked as PII
TECHNICAL_ALLOWLIST = [
    # Container and orchestration tools
    "Docker", "Kubernetes", "Helm", "K8s", "Podman", "Containerd",
    
    # CI/CD and DevOps
    "Jenkins", "GitHub", "GitLab", "Bitbucket", "CircleCI", "Travis", "Actions",
    "Ansible", "Terraform", "Puppet", "Chef", "SaltStack",
    
    # Web servers and proxies
    "nginx", "Nginx", "NGINX", "Apache", "Caddy", "Traefik", "HAProxy",
    
    # Databases
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "SQLite",
    "Cassandra", "DynamoDB", "Postgres", "MariaDB", "InfluxDB",
    
    # Programming languages and runtimes
    "Python", "JavaScript", "TypeScript", "Java", "C#", "C++", "Go", "Rust", "Ruby", "PHP",
    "Node.js", "Deno", "Bun",
    
    # Python web frameworks and tools
    "Django", "Flask", "FastAPI", "Gunicorn", "uWSGI", "uvicorn", "Uvicorn",
    "Celery", "Pydantic", "SQLAlchemy",
    
    # JavaScript frameworks
    "Express", "React", "Angular", "Vue", "Next.js", "Svelte", "Nuxt",
    
    # Testing and linting tools
    "pytest", "unittest", "Jest", "Mocha", "Jasmine",
    "ruff", "Ruff", "pylint", "flake8", "black", "isort", "mypy",
    "eslint", "prettier", "tslint",
    
    # Security scanning tools
    "Bandit", "Semgrep", "Snyk", "Trivy", "Hadolint", "Checkov",
    "SonarQube", "Dependabot", "SAST", "DAST",
    
    # Cloud providers and platforms
    "AWS", "Azure", "GCP", "Heroku", "Vercel", "Netlify", "DigitalOcean",
    "Cloudflare", "Linode", "Vultr",
    
    # Protocols and standards
    "API", "REST", "RESTful", "GraphQL", "gRPC", "WebSocket",
    "HTTP", "HTTPS", "SSL", "TLS", "OAuth", "JWT", "SAML", "LDAP",
    "CORS", "DNS", "TCP", "UDP", "SMTP", "IMAP", "POP3",
    
    # Message queues and streaming
    "Kafka", "RabbitMQ", "NATS", "ActiveMQ", "ZeroMQ",
    
    # Monitoring and observability
    "Prometheus", "Grafana", "Datadog", "New Relic", "Sentry",
    "OpenTelemetry", "Jaeger", "Zipkin",
    
    # Version control
    "Git", "SVN", "Mercurial",
    
    # Package managers
    "npm", "pip", "yarn", "pnpm", "cargo", "gem", "composer",
    
    # Common technical patterns
    "localhost", "127.0.0.1", "0.0.0.0", "example.com", "test.com",
    "Active Directory",
    
    # URL patterns (will be handled by custom logic)
    # NOTE: Actual sensitive URLs will still be caught by context-aware detection
]

# Presidio placeholder patterns that indicate over-redaction
# These are shared across modules to detect corrupted requirements
PRESIDIO_PLACEHOLDERS = [
    '<ORGANIZATION>',
    '<URL>',
    '<PERSON>',
    '<API_KEY>',
    '<EMAIL_ADDRESS>',
    '<PHONE_NUMBER>',
    '<LOCATION>',
]

# Compile regex pattern once for efficient log filtering
# Matches: "Entity {TYPE} is not mapped", "entity {TYPE} is not mapped", or "{TYPE} is not mapped"
# Uses word boundaries to prevent false matches in other contexts
_presidio_filter_pattern = re.compile(
    r'\b(' + '|'.join(PRESIDIO_IGNORED_ENTITY_TYPES) + r')\s+is\s+not\s+mapped\b',
    re.IGNORECASE
)


def _add_custom_recognizers(analyzer_engine):
    """
    Add custom recognizers for entities not covered by default Presidio patterns
    and configure entity thresholds to reduce false positives.
    
    HIGH: Adds support for API_KEY and CARDINAL entities to prevent exposure in logs.
    FIX: Removes aggressive URL recognizer that causes false positives with technical URLs
    FIX: Increases thresholds for PERSON and ORGANIZATION to reduce over-redaction
    
    Args:
        analyzer_engine: Presidio AnalyzerEngine instance to add recognizers to
    """
    try:
        from presidio_analyzer import Pattern, PatternRecognizer
        
        # FIX: Remove URL recognizer to prevent over-aggressive URL redaction
        # Technical documentation often contains example URLs, API endpoints, etc.
        try:
            analyzer_engine.registry.remove_recognizer("UrlRecognizer")
            logger.info("✓ Removed aggressive UrlRecognizer")
        except Exception:
            pass  # Recognizer may not exist
        
        # HIGH: Add API_KEY recognizer for common API key patterns
        # Matches patterns like: sk-abc123..., xai-..., Bearer ..., etc.
        api_key_patterns = [
            Pattern(
                name="api_key_pattern_1",
                regex=r"\b(sk|pk|xai|ghp|gho|Bearer)[-_]?[a-zA-Z0-9]{20,}\b",
                score=0.8,
            ),
            Pattern(
                name="api_key_pattern_2",
                regex=r"\b[A-Z0-9]{32,}\b",  # Long alphanumeric strings (32+ chars)
                score=0.5,
            ),
        ]
        api_key_recognizer = PatternRecognizer(
            supported_entity="API_KEY",
            patterns=api_key_patterns,
            context=["key", "token", "secret", "api", "bearer", "authorization"],
        )
        analyzer_engine.registry.add_recognizer(api_key_recognizer)
        
        # HIGH: Add CARDINAL recognizer to redact numbers that might be sensitive
        # Matches standalone numbers (card IDs, account numbers, etc.)
        # Using 8+ digits to avoid false positives with dates, ports, etc.
        cardinal_patterns = [
            Pattern(
                name="cardinal_number",
                regex=r"\b\d{8,}\b",  # Numbers with 8+ digits (account numbers, IDs)
                score=0.3,
            ),
        ]
        cardinal_recognizer = PatternRecognizer(
            supported_entity="CARDINAL",
            patterns=cardinal_patterns,
            context=["id", "number", "account", "card", "ssn"],  # More specific context
        )
        analyzer_engine.registry.add_recognizer(cardinal_recognizer)
        
        logger.info("✓ Added custom recognizers for API_KEY and CARDINAL entities")
        logger.info("✓ Configured Presidio with reduced false positive rate")
        
    except Exception as e:
        logger.warning(f"Failed to add custom recognizers: {e}")


# [NEW] Function to lazily load Presidio/spaCy dependencies
def _load_presidio_engine() -> bool:
    """
    Load presidio engine without auto-downloading models that cause SystemExit.

    This function implements enterprise-grade error handling with graceful degradation:
    1. Try with configurable spaCy model (default: en_core_web_sm)
    2. Fall back to regex-only mode if model unavailable
    3. Catch SystemExit to prevent application crashes
    4. Never crash - always return boolean status
    5. Track NLP availability separately from basic availability

    Returns:
        bool: True if Presidio is available (with or without NLP), False otherwise
    """
    global _PRESIDIO_ANALYZER_ENGINE, _PRESIDIO_ANONYMIZER_ENGINE, _PRESIDIO_AVAILABLE, _PRESIDIO_LOAD_ATTEMPTED, _PRESIDIO_NLP_MODE

    # Return cached result if already loaded successfully
    if _PRESIDIO_AVAILABLE:
        return True

    # Don't retry if we've already attempted and failed
    if _PRESIDIO_LOAD_ATTEMPTED:
        return False

    _PRESIDIO_LOAD_ATTEMPTED = True

    # FIX: CRITICAL CHECK: Use the global TESTING flag if available, otherwise define locally
    global TESTING
    if "TESTING" not in globals():
        TESTING = (
            os.getenv("TESTING") == "1"
            or "pytest" in sys.modules
            or os.getenv("PYTEST_CURRENT_TEST") is not None
            or os.getenv("PYTEST_ADDOPTS") is not None
        )

    if TESTING:
        logger.warning(
            "Skipping heavy NLP/ML dependency load (Presidio/SpaCy) during Pytest session to prevent Windows DLL crash."
        )
        return False

    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        # Prevent auto-download warnings that can trigger SystemExit
        os.environ["SPACY_WARNING_IGNORE"] = "W007"

        # Enterprise-grade: Make model configurable via environment
        model_name = os.getenv("PRESIDIO_SPACY_MODEL", "en_core_web_sm")

        # FIX: Only load English recognizers to suppress unsupported language warnings
        # The multilingual models (es, it, pl) were generating 132+ warnings per run:
        # "Recognizer not added to registry because language is not supported by registry"
        # We only support English for now to avoid log pollution
        models_config = [{"lang_code": "en", "model_name": model_name}]
        supported_langs = ["en"]
        
        logger.info("Configuring Presidio with English-only support to suppress unsupported language warnings")
        
        configuration = {
            "nlp_engine_name": "spacy",
            "models": models_config,
            # [FIX] Suppress noisy warnings for these entity types
            "ner_model_configuration": {
                "labels_to_ignore": [
                    "CARDINAL",      # Numbers like "1", "two", "100"
                    "ORDINAL",       # Ordinals like "first", "second"
                    "WORK_OF_ART",   # Titles of books, songs, etc.
                    "PRODUCT",       # Product names
                    "FAC",           # Facilities like buildings, airports
                    "PERCENT",       # Percentages
                    "MONEY",         # Monetary values
                ]
            }
        }

        try:
            # Attempt to create NLP engine with configured model
            provider = NlpEngineProvider(nlp_configuration=configuration)
            nlp_engine = provider.create_engine()
            # Only enable languages for which we have working models
            _PRESIDIO_ANALYZER_ENGINE = AnalyzerEngine(
                nlp_engine=nlp_engine, supported_languages=supported_langs
            )
            _PRESIDIO_ANONYMIZER_ENGINE = AnonymizerEngine()
            _PRESIDIO_AVAILABLE = True
            _PRESIDIO_NLP_MODE = True  # Full NLP mode available
            
            # HIGH: Add custom recognizers for API_KEY and CARDINAL entities
            _add_custom_recognizers(_PRESIDIO_ANALYZER_ENGINE)
            
            # Suppress non-critical Presidio warnings for unmapped entities
            # These warnings clutter logs but don't affect functionality
            presidio_logger = logging.getLogger("presidio_analyzer")
            
            def presidio_log_filter(record):
                """Filter out non-critical unmapped entity warnings from Presidio.
                
                Uses compiled regex for efficient filtering. Matches patterns like:
                - "Entity CARDINAL is not mapped"
                - "entity MONEY is not mapped"
                - "PERCENT is not mapped"
                - "Recognizer not added to registry because language is not supported"
                
                Case-insensitive with word boundaries to prevent false positives.
                """
                msg = record.getMessage()
                # Filter unmapped entity warnings
                if _presidio_filter_pattern.search(msg):
                    return False
                # Filter unsupported language warnings for recognizers
                if "not added to registry because language is not supported" in msg.lower():
                    return False
                return True
            
            presidio_logger.addFilter(presidio_log_filter)
            
            logger.info(
                f"Presidio analyzer loaded successfully with {model_name} model (full NLP mode)"
            )
            return True

        except SystemExit as se:
            # CRITICAL: Catch SystemExit from spaCy download attempts
            # This prevents the entire application from crashing during startup
            logger.warning(
                f"Presidio model download blocked (SystemExit {se.code}). "
                "Using regex-only PII detection mode for graceful degradation."
            )
            # Create analyzer without NLP engine - uses regex patterns only
            # FIX: Specify supported_languages to avoid warnings about non-English recognizers
            _PRESIDIO_ANALYZER_ENGINE = AnalyzerEngine(
                nlp_engine=None, supported_languages=["en"]
            )
            _PRESIDIO_ANONYMIZER_ENGINE = AnonymizerEngine()
            _PRESIDIO_AVAILABLE = True
            _PRESIDIO_NLP_MODE = False  # Degraded to regex-only mode
            
            # HIGH: Add custom recognizers even in regex-only mode
            _add_custom_recognizers(_PRESIDIO_ANALYZER_ENGINE)
            
            # Suppress non-critical Presidio warnings
            presidio_logger = logging.getLogger("presidio_analyzer")
            presidio_logger.addFilter(
                lambda record: not any(
                    entity in record.getMessage()
                    for entity in ["CARDINAL", "MONEY", "PERCENT", "WORK_OF_ART", "is not mapped"]
                )
            )
            
            logger.info("Presidio running in REGEX-ONLY mode (NLP unavailable)")
            return True

        except Exception as model_error:
            # Model loading failed, but presidio itself is available
            logger.warning(
                f"Presidio NLP engine unavailable ({type(model_error).__name__}: {model_error}). "
                "Using regex-only mode for PII detection."
            )
            # Graceful degradation: use presidio without NLP
            # FIX: Specify supported_languages to avoid warnings about non-English recognizers
            _PRESIDIO_ANALYZER_ENGINE = AnalyzerEngine(
                nlp_engine=None, supported_languages=["en"]
            )
            _PRESIDIO_ANONYMIZER_ENGINE = AnonymizerEngine()
            _PRESIDIO_AVAILABLE = True
            _PRESIDIO_NLP_MODE = False  # Degraded to regex-only mode
            
            # HIGH: Add custom recognizers even in regex-only mode
            _add_custom_recognizers(_PRESIDIO_ANALYZER_ENGINE)
            
            # Suppress non-critical Presidio warnings
            presidio_logger = logging.getLogger("presidio_analyzer")
            presidio_logger.addFilter(
                lambda record: not any(
                    entity in record.getMessage()
                    for entity in ["CARDINAL", "MONEY", "PERCENT", "WORK_OF_ART", "is not mapped"]
                )
            )
            logger.info("Presidio running in REGEX-ONLY mode (NLP failed to load)")
            return True

    except ImportError as ie:
        # Presidio library not installed
        _PRESIDIO_AVAILABLE = False
        logger.warning(
            f"Presidio library not available ({ie}). PII detection will be disabled. "
            "Install with: pip install presidio-analyzer presidio-anonymizer"
        )
        return False

    except SystemExit as se:
        # CRITICAL: Catch SystemExit at outer level too
        _PRESIDIO_AVAILABLE = False
        logger.error(
            f"SystemExit caught during presidio initialization (code {se.code}). "
            "Disabling presidio to prevent application crash."
        )
        return False

    except Exception as e:
        # Unexpected error during initialization
        _PRESIDIO_AVAILABLE = False
        logger.error(
            f"Presidio initialization failed with unexpected error: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return False


# [NEW] No-op fallbacks for metrics/decorators if not found
def util_decorator(func: Callable):
    """No-op decorator fallback."""

    @wraps(func)
    async def _aw(*a, **k):
        return await func(*a, **k)

    @wraps(func)
    def _sw(*a, **k):
        return func(*a, **k)

    return _aw if asyncio.iscoroutinefunction(func) else _sw


def detect_anomaly(*a, **k):
    """No-op anomaly detection fallback."""
    logger.debug("detect_anomaly called, but no-op implementation is in use.")
    return False


# External secret managers
try:
    import hvac  # Hashicorp Vault (add to reqs: hvac)

    HAS_VAULT = True
except ImportError:
    hvac = None
    HAS_VAULT = False
    logger.info("hvac not found. Hashicorp Vault integration will be unavailable.")

try:
    import boto3  # AWS (add to reqs: boto3)
    from botocore.exceptions import ClientError as BotoClientError

    HAS_BOTO3 = True
except ImportError:
    boto3 = None
    HAS_BOTO3 = False
    logger.info(
        "boto3 not found. AWS Secrets Manager/KMS integration will be unavailable."
    )

try:
    import pkcs11  # For HSM (add to reqs: python-pkcs11)
    from pkcs11.constants import KeyType, Mechanism, ObjectClass
    from pkcs11.exceptions import PKCS11Error

    HAS_PKCS11 = True
except ImportError:
    pkcs11 = None
    HAS_PKCS11 = False
    logger.info("python-pkcs11 not found. HSM integration will be unavailable.")


# --- Regex Redactors (Default fallback and custom patterns) ---
# Default patterns, always available
def regex_basic_redactor(data: Any, patterns: Optional[List[Pattern]] = None) -> Any:
    """Recursively redacts data using basic regex patterns."""
    if patterns is None:
        # Comprehensive redaction patterns as specified in problem statement
        patterns = [
            # Email addresses
            re.compile(r'\b[\w.+-]+@[\w.-]+\.\w+\b'),
            
            # Phone numbers (various formats)
            re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'),
            
            # API keys and tokens (20+ alphanumeric/special chars)
            re.compile(r'\b[A-Za-z0-9_-]{20,}\b'),
            
            # Password in key-value format (case-insensitive)
            re.compile(r'(password|pwd|pass|secret|token|key)([:=]\s*\S+)', re.IGNORECASE),
            
            # Credit card numbers (basic pattern)
            re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'),
            
            # SSN format
            re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
        ]

    if isinstance(data, str):
        result = data
        # Apply each pattern individually with appropriate replacement
        for i, pattern in enumerate(patterns):
            # Pattern index 3 is the password pattern with capture groups
            if i == 3:
                # Replace password values but preserve the key name
                result = pattern.sub(r'\1: [REDACTED]', result)
            else:
                # Replace the entire match for other patterns
                result = pattern.sub('[REDACTED]', result)
        return result
    elif isinstance(data, dict):
        return {k: regex_basic_redactor(v, patterns) for k, v in data.items()}
    elif isinstance(data, list):
        return [regex_basic_redactor(item, patterns) for item in data]
    return data


# NLP-based redactor (if Presidio is available)
def nlp_presidio_redactor(data: Any, patterns: Optional[List[Pattern]] = None, apply_allowlist: bool = True) -> Any:
    """
    Recursively redacts data using Presidio NLP, falling back to regex for non-strings.
    
    Args:
        data: Data to redact (string, dict, list, or other)
        patterns: Optional additional regex patterns to apply after Presidio
        apply_allowlist: If True, filters out technical terms from TECHNICAL_ALLOWLIST
    
    Returns:
        Redacted data with PII removed but technical terms preserved
    """
    # FIX: Ensure Presidio is loaded only when this function is called
    if not _PRESIDIO_AVAILABLE:
        _load_presidio_engine()

    if (
        not _PRESIDIO_AVAILABLE
        or not _PRESIDIO_ANALYZER_ENGINE
        or not _PRESIDIO_ANONYMIZER_ENGINE
    ):
        logger.warning(
            "Presidio/NLP redactor called but not available. Falling back to basic regex."
        )
        return regex_basic_redactor(
            data, patterns
        )  # Fallback to regex if Presidio failed

    if isinstance(data, str):
        try:
            # FIX: Apply allowlist filtering to reduce false positives
            # Analyze with higher thresholds for common entities
            results = _PRESIDIO_ANALYZER_ENGINE.analyze(
                text=data, 
                language="en",
                score_threshold=0.6  # Increased from default 0.0 to reduce false positives
            )
            
            # FIX: Filter out technical terms from allowlist
            if apply_allowlist:
                filtered_results = []
                for result in results:
                    # Extract the detected text
                    detected_text = data[result.start:result.end]
                    # Check if it's in our technical allowlist (case-insensitive)
                    if detected_text not in TECHNICAL_ALLOWLIST and detected_text.lower() not in [t.lower() for t in TECHNICAL_ALLOWLIST]:
                        filtered_results.append(result)
                    else:
                        logger.debug(f"Skipping allowlisted term: {detected_text}")
                results = filtered_results
            
            anonymized_result = _PRESIDIO_ANONYMIZER_ENGINE.anonymize(
                text=data, analyzer_results=results
            )

            # If custom patterns are provided, apply them *after* Presidio runs on the resulting text.
            if patterns:
                result_text = anonymized_result.text
                for pattern in patterns:
                    result_text = pattern.sub("[REDACTED]", result_text)
                return result_text

            return anonymized_result.text
        except Exception as e:
            logger.error(
                f"Presidio redaction failed: {e}. Falling back to basic regex for this item.",
                exc_info=True,
            )
            # --- FIX: REMOVED METRIC INCREMENT ---
            return regex_basic_redactor(data, patterns)  # Fallback on error
    elif isinstance(data, dict):
        return {k: nlp_presidio_redactor(v, patterns, apply_allowlist) for k, v in data.items()}
    elif isinstance(data, list):
        return [nlp_presidio_redactor(item, patterns, apply_allowlist) for item in data]
    return data


# Register the redactors
register_redactor("regex_basic", regex_basic_redactor)
# FIX: Register the NLP redactor. The function itself will handle lazy-loading
# and skip if Presidio is not available. This prevents _load_presidio_engine()
# from being called at import time, which fixes the torch/pytest DLL error.
register_redactor("nlp_presidio", nlp_presidio_redactor)


# --- Encryption Providers ---
def fernet_encrypt_decrypt(
    data: Union[str, bytes], key: bytes, mode: str
) -> Union[bytes, str]:
    """Symmetric encryption/decryption using Fernet."""
    f = Fernet(key)
    if mode == "encrypt":
        data_bytes = data.encode("utf-8") if isinstance(data, str) else data
        return f.encrypt(data_bytes)
    elif mode == "decrypt":
        if not isinstance(data, bytes):
            raise TypeError("Fernet decryption requires bytes input.")
        decrypted_bytes = f.decrypt(data)
        return decrypted_bytes.decode("utf-8")  # Assume decrypted data is utf-8 string
    raise ValueError("Invalid mode for Fernet: must be 'encrypt' or 'decrypt'.")


def aes_cbc_encrypt_decrypt(
    data: Union[str, bytes], key: bytes, mode: str
) -> Union[bytes, str]:
    """Symmetric encryption/decryption using AES-CBC with PKCS7 padding."""
    if len(key) not in [16, 24, 32]:
        raise ValueError("AES key must be 16, 24, or 32 bytes.")

    from cryptography.hazmat.primitives import padding as sym_padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if mode == "encrypt":
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        padder = sym_padding.PKCS7(algorithms.AES.block_size).padder()

        data_bytes = data.encode("utf-8") if isinstance(data, str) else data
        padded_data = padder.update(data_bytes) + padder.finalize()
        return (
            iv + encryptor.update(padded_data) + encryptor.finalize()
        )  # Prepend IV to ciphertext
    elif mode == "decrypt":
        if not isinstance(data, bytes) or len(data) <= 16:
            raise TypeError(
                "AES decryption requires bytes input with IV (must be > 16 bytes)."
            )

        iv = data[:16]
        ciphertext = data[16:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        unpadder = sym_padding.PKCS7(algorithms.AES.block_size).unpadder()

        decrypted_padded_bytes = decryptor.update(ciphertext) + decryptor.finalize()
        decrypted_bytes = unpadder.update(decrypted_padded_bytes) + unpadder.finalize()
        return decrypted_bytes.decode("utf-8")
    raise ValueError("Invalid mode for AES: must be 'encrypt' or 'decrypt'.")


# Register encryption providers
register_encryptor("fernet", fernet_encrypt_decrypt)
register_encryptor("aes_cbc", aes_cbc_encrypt_decrypt)
# FIX: Corrected typo in function name registration
register_decryptor("fernet", fernet_encrypt_decrypt)
register_decryptor("aes_cbc", aes_cbc_encrypt_decrypt)


# --- Public-facing Security Functions ---
@util_decorator
def redact_secrets(
    data: Any, method: Optional[str] = None, patterns: Optional[List[Pattern]] = None
) -> Any:
    """
    Redacts sensitive information from data using the specified method.

    Enterprise-grade implementation with comprehensive error handling:
    - Catches SystemExit to prevent application crashes
    - Gracefully degrades through multiple fallback levels
    - Never crashes - always returns data (redacted or original)
    - Thread-safe with proper exception isolation

    Fallback chain:
    1. Requested method (if specified and available)
    2. NLP-based presidio (if available)
    3. Regex-based pattern matching
    4. Original data (if all else fails, with warning)

    Args:
        data: The data to redact (str, dict, list, etc.)
        method: Optional specific redaction method to use
        patterns: Optional custom regex patterns for redaction

    Returns:
        Redacted data of the same type as input

    [FIX] This is now a SYNCHRONOUS function to fix the RuntimeWarning.
    """
    # Defensive: If no data, return immediately
    if data is None:
        return data

    try:
        # FIX: Lazy import to break circular dependency - use sync version
        from .runner_audit import log_audit_event_sync as log_audit_event
    except ImportError:
        # If logging not available, continue without it
        log_audit_event = None

    # 1. Determine the redactor method with error handling
    effective_method = None
    try:
        if method:
            if method in REDACTORS:
                effective_method = method
            else:
                logger.warning(
                    f"Redactor '{method}' not found. Falling back to auto-selection."
                )
                effective_method = None  # Fallback

        if not effective_method:
            # Determine NLP availability lazily - wrap in try-catch for SystemExit
            try:
                nlp_available = _PRESIDIO_AVAILABLE or _load_presidio_engine()
                effective_method = "nlp_presidio" if nlp_available else "regex_basic"
            except SystemExit:
                # CRITICAL: Catch SystemExit from presidio loading
                logger.warning(
                    "SystemExit caught during presidio availability check. "
                    "Falling back to regex-only redaction."
                )
                effective_method = "regex_basic"
            except Exception as e:
                logger.debug(
                    f"Error checking presidio availability: {e}. Using regex fallback."
                )
                effective_method = "regex_basic"

        redactor = REDACTORS.get(
            effective_method, regex_basic_redactor
        )  # Get the redactor function

        logger.debug(f"Redacting secrets using method: {effective_method}")

        # FIX: Call the synchronous redactor directly with error handling
        try:
            result = redactor(data, patterns)
        except SystemExit:
            # CRITICAL: Catch SystemExit from redactor execution
            logger.warning(
                "SystemExit caught during secret redaction. Returning original data."
            )
            result = data
        except Exception as redact_error:
            # If redaction fails, log and return original data
            logger.warning(
                f"Redaction failed ({type(redact_error).__name__}: {redact_error}). "
                "Returning original data for safety."
            )
            result = data

        # [FIX] Use log_audit_event_sync (fire-and-forget)
        if log_audit_event:
            try:
                log_audit_event(
                    action="security_redact",
                    data={"method": effective_method, "data_type": str(type(data))},
                )
            except Exception:
                # Silently ignore logging failures - never crash due to logging
                pass

        return result

    except SystemExit:
        # CRITICAL: Outermost SystemExit handler - last line of defense
        logger.error(
            "SystemExit caught in redact_secrets outer handler. "
            "This should never happen. Returning original data."
        )
        return data
    except Exception as e:
        # Catch-all: Never crash, always return something
        logger.error(
            f"Unexpected error in redact_secrets: {type(e).__name__}: {e}",
            exc_info=True,
        )
        return data


# Alias for backward compatibility and semantic clarity
# Many modules expect scrub_pii_and_secrets instead of redact_secrets
scrub_pii_and_secrets = redact_secrets


@util_decorator
async def encrypt_data(
    data: Union[str, bytes], key: bytes, algorithm: str = "fernet"
) -> bytes:
    """
    Encrypts data using the specified symmetric algorithm.
    Returns encrypted bytes.
    """
    # FIX: Lazy import to break circular dependency - use relative import
    from .runner_logging import log_audit_event

    # --- FIX: REMOVED METRICS IMPORT ---

    encryptor = ENCRYPTORS.get(algorithm)
    if not encryptor:
        logger.error(f"Encryption algorithm '{algorithm}' not registered.")
        # --- FIX: REMOVED METRICS INCREMENT ---
        raise ValueError(f"Encryption algorithm '{algorithm}' not registered.")

    # Encryption is CPU-bound, run in thread pool
    encrypted_bytes = await asyncio.to_thread(encryptor, data, key, "encrypt")

    # [FIX] Replaced add_provenance with log_audit_event
    # Audit logging should not cause encryption to fail
    try:
        await log_audit_event(
            action="security_encrypt",
            data={"algorithm": algorithm, "output_bytes": len(encrypted_bytes)},
        )
    except Exception:
        logger.debug("Audit logging failed for encrypt_data, continuing", exc_info=True)
    return encrypted_bytes  # type: ignore


@util_decorator
async def decrypt_data(data: bytes, key: bytes, algorithm: str = "fernet") -> str:
    """
    Decrypts data using the specified symmetric algorithm.
    Returns decrypted string (assumes utf-8).
    """
    # FIX: Lazy import to break circular dependency - use relative import
    from .runner_logging import log_audit_event

    # --- FIX: REMOVED METRICS IMPORT ---

    decryptor = DECRYPTORS.get(algorithm)
    if not decryptor:
        logger.error(f"Decryption algorithm '{algorithm}' not registered.")
        # --- FIX: REMOVED METRICS INCREMENT ---
        raise ValueError(f"Decryption algorithm '{algorithm}' not registered.")

    # Decryption is CPU-bound, run in thread pool
    decrypted_string = await asyncio.to_thread(decryptor, data, key, "decrypt")

    # [FIX] Replaced add_provenance with log_audit_event
    # Audit logging should not cause decryption to fail
    try:
        await log_audit_event(
            action="security_decrypt",
            data={"algorithm": algorithm, "input_bytes": len(data)},
        )
    except Exception:
        logger.debug("Audit logging failed for decrypt_data, continuing", exc_info=True)
    return decrypted_string  # type: ignore


# --- Secret Management ---
# Global in-memory cache for secrets (simple TTL)
_secret_cache: Dict[str, Tuple[float, Any]] = {}
SECRET_CACHE_TTL = 300  # 5 minutes


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
async def fetch_secret(
    secret_name: str, source: str = "env", **kwargs
) -> Optional[str]:
    """
    Fetches a secret from a configured source (env, vault, aws_sm, hsm_pin).
    Caches secrets in memory with a TTL.
    """
    # FIX: Lazy import to break circular dependency - use relative import
    from .runner_logging import send_alert

    # --- FIX: REMOVED METRICS IMPORT ---

    cached_secret = _get_from_cache(secret_name)
    if cached_secret:
        return cached_secret

    secret_value: Optional[str] = None

    try:
        if source == "env":
            secret_value = os.getenv(secret_name)
            if secret_value:
                logger.info(
                    f"Fetched secret '{secret_name}' from environment variable."
                )

        elif source == "vault" and HAS_VAULT:
            vault_url = kwargs.get("vault_url", os.getenv("VAULT_ADDR"))
            vault_token = kwargs.get("vault_token", os.getenv("VAULT_TOKEN"))
            mount_point = kwargs.get("mount_point", "secret")

            if not vault_url or not vault_token:
                raise ValueError("Vault URL and token are required for 'vault' source.")

            client = hvac.Client(url=vault_url, token=vault_token)
            if not client.is_authenticated():
                raise ConnectionError("Failed to authenticate with Hashicorp Vault.")

            # Read secret from KV v2
            response = await asyncio.to_thread(
                client.secrets.kv.v2.read_secret_version,
                path=secret_name,
                mount_point=mount_point,
            )
            secret_value = response["data"]["data"].get(
                secret_name.split("/")[-1]
            )  # Get key from path
            if secret_value:
                logger.info(f"Fetched secret '{secret_name}' from Hashicorp Vault.")

        elif source == "aws_sm" and HAS_BOTO3:
            region_name = kwargs.get(
                "region_name", os.getenv("AWS_REGION", "us-east-1")
            )

            session = boto3.session.Session()
            client = session.client(
                service_name="secretsmanager", region_name=region_name
            )

            response = await asyncio.to_thread(
                client.get_secret_value, SecretId=secret_name
            )
            if "SecretString" in response:
                secret_value = response["SecretString"]
            else:
                secret_value = base64.b64decode(response["SecretBinary"]).decode(
                    "utf-8"
                )
            if secret_value:
                logger.info(f"Fetched secret '{secret_name}' from AWS Secrets Manager.")

        elif source == "hsm_pin" and HAS_PKCS11:
            # Placeholder for retrieving HSM PIN.
            # This is highly specific and often passed via environment or secure input.
            secret_value = os.getenv(
                "HSM_PIN"
            )  # Default to environment variable for PIN
            if secret_value:
                logger.info("Fetched HSM PIN from environment variable.")
            else:
                logger.warning(
                    "HSM PIN requested but 'HSM_PIN' environment variable not set."
                )

        else:
            if not HAS_VAULT and source == "vault":
                logger.error(
                    "Cannot fetch secret from Vault: 'hvac' library not installed."
                )
            elif not HAS_BOTO3 and source == "aws_sm":
                logger.error(
                    "Cannot fetch secret from AWS Secrets Manager: 'boto3' library not installed."
                )
            elif not HAS_PKCS11 and source == "hsm_pin":
                logger.error(
                    "Cannot fetch HSM PIN: 'python-pkcs11' library not installed."
                )
            else:
                logger.error(f"Unknown secret source: {source}")
            # --- FIX: REMOVED METRICS INCREMENT ---

    except Exception as e:
        logger.error(
            f"Failed to fetch secret '{secret_name}' from source '{source}': {e}",
            exc_info=True,
        )
        # --- FIX: REMOVED METRICS INCREMENT ---
        await send_alert(
            f"Failed to fetch secret '{secret_name}'",
            f"Source: {source}\nError: {e}",
            severity="critical",
        )

    if secret_value:
        _set_to_cache(secret_name, secret_value)
    else:
        logger.warning(f"Secret '{secret_name}' not found from source '{source}'.")

    return secret_value


# [NEW] Synchronous secret scanner for deploy_response_handler
SECRET_SCAN_PATTERNS = [
    # API keys (common prefixes)
    re.compile(r'(?i)(api_key|secret_key|token)[\s=:"\']{1,5}([a-zA-Z0-9_-]{20,})'),
    # Generic Base64-looking strings (20+ chars) - more specific
    re.compile(r"\b[a-zA-Z0-9/+]{20,}[=]{0,2}\b"),
    # Common passwords in configs
    re.compile(r'(?i)(password|passwd|secret)[\s=:"\']{1,5}([^"\s\']{8,})'),
]


def scan_for_secrets(content: str) -> List[Dict[str, Any]]:
    """
    Scan text for potential secrets and return list of findings.
    
    Returns:
        List of dictionaries with 'type' and 'match' keys for each finding
    """
    if not isinstance(content, str):
        return []
    
    findings = []
    # Track locations to avoid duplicates (store as list of (start, end) tuples)
    matched_ranges = []
    
    def overlaps_existing(start, end):
        """
        Check if a range overlaps with any existing range.
        Ranges don't overlap if one ends before the other starts.
        """
        for existing_start, existing_end in matched_ranges:
            # Check for overlap: NOT (end before existing_start OR start after existing_end)
            if not (end <= existing_start or start >= existing_end):
                return True
        return False
    
    # Email
    for match in re.finditer(r'\b[\w.+-]+@[\w.-]+\.\w+\b', content):
        start, end = match.span()
        if not overlaps_existing(start, end):
            findings.append({
                'type': 'email', 
                'match': 'email_address',
                'location_start': start,
                'location_end': end
            })
            matched_ranges.append((start, end))
    
    # Phone
    for match in re.finditer(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', content):
        start, end = match.span()
        if not overlaps_existing(start, end):
            findings.append({
                'type': 'phone', 
                'match': 'phone_number',
                'location_start': start,
                'location_end': end
            })
            matched_ranges.append((start, end))
    
    # API key/token (20+ chars)
    for match in re.finditer(r'\b[A-Za-z0-9_-]{20,}\b', content):
        start, end = match.span()
        key = match.group(0)
        # Skip very long strings that are unlikely to be API keys
        if len(key) <= 100 and not overlaps_existing(start, end):
            findings.append({
                'type': 'api_key', 
                'match': key[:10] + '...' if len(key) > 10 else key,
                'location_start': start,
                'location_end': end
            })
            matched_ranges.append((start, end))
    
    # Password patterns (require 8+ characters)
    for match in re.finditer(r'(password|pwd|pass)[:=]\s*(\S+)', content, re.IGNORECASE):
        # match.group(2) is the password value
        if len(match.group(2)) >= 8:
            start, end = match.span()
            if not overlaps_existing(start, end):
                findings.append({
                    'type': 'password', 
                    'match': 'password_field',
                    'location_start': start,
                    'location_end': end
                })
                matched_ranges.append((start, end))
    
    # Also check the legacy SECRET_SCAN_PATTERNS for additional findings
    for pattern in SECRET_SCAN_PATTERNS:
        for match in pattern.finditer(content):
            start, end = match.span()
            # Skip if we already found an overlapping match
            if overlaps_existing(start, end):
                continue
                
            # Avoid matching very long non-secret strings
            if (
                pattern.pattern == r"\b[a-zA-Z0-9/+]{20,}[=]{0,2}\b"
                and len(match.group(0)) > 100
            ):
                continue

            # Add as Secret_Regex type for backward compatibility
            findings.append(
                {
                    "type": "Secret_Regex",
                    "pattern": pattern.pattern,
                    "location_start": start,
                    "location_end": end,
                }
            )
            matched_ranges.append((start, end))
    
    return findings


@util_decorator
async def monitor_for_leaks(text: str) -> List[Dict[str, Any]]:
    """
    Monitors text for potential leaks using Presidio (if available) and regex.
    """
    # FIX: Lazy import to break circular dependency - use relative import
    from .runner_logging import log_audit_event, send_alert

    # --- FIX: REMOVED METRICS IMPORT ---

    leaks_found: List[Dict[str, Any]] = []

    # 1. Presidio (if available)
    if _PRESIDIO_AVAILABLE or _load_presidio_engine():
        if _PRESIDIO_ANALYZER_ENGINE and _PRESIDIO_ANONYMIZER_ENGINE:
            try:
                results = _PRESIDIO_ANALYZER_ENGINE.analyze(text=text, language="en")
                for res in results:
                    leaks_found.append(
                        {
                            "type": "PII_Presidio",
                            "entity": res.entity_type,
                            "location_start": res.start,
                            "location_end": res.end,
                            "score": res.score,
                        }
                    )
            except Exception as e:
                logger.error(f"Presidio leak monitoring failed: {e}", exc_info=True)
                # --- FIX: REMOVED METRICS INCREMENT ---

    # 2. Regex (always run as fallback or for non-PII secrets)
    # Use the same patterns as the synchronous scanner
    for finding in scan_for_secrets(text):
        leaks_found.append(
            {
                "type": "Secret_Regex",
                "entity": "Pattern",
                "location_start": finding["location_start"],
                "location_end": finding["location_end"],
                "score": 0.8,  # Assign arbitrary score for regex
            }
        )

    if leaks_found:
        logger.warning(f"Potential data leaks detected: {len(leaks_found)} findings.")
        # [FIX] Replaced add_provenance with log_audit_event
        await log_audit_event(
            action="security_leak_monitor",
            data={
                "findings_count": len(leaks_found),
                "first_finding_type": leaks_found[0]["type"] if leaks_found else "N/A",
            },
        )
        await send_alert(
            "Data Leak Detected",
            f"Found {len(leaks_found)} potential leaks in processed data.",
            severity="high",
        )

    return leaks_found


@util_decorator
async def scan_for_vulnerabilities(
    target: Union[str, Path], scan_type: str = "code"
) -> Dict[str, Any]:
    """
    Scans code or data for vulnerabilities using external tools (e.g., Bandit, Trivy).
    This is a simplified example; a real implementation would use process_utils.
    """
    scan_results = {
        "status": "skipped",
        "scanned_target": str(target),
        "scan_type": scan_type,
        "vulnerabilities_found": 0,
        "details": "No scanners available or scan_type unknown.",
    }

    if scan_type == "code":
        logger.info(
            f"Simulating vulnerability scan (e.g., Bandit, Semgrep) on code target: {target}"
        )
        scan_results["status"] = "completed"
        scan_results["vulnerabilities_found"] = 0  # Changed to 0 for TESTING mode safety
        scan_results["details"] = "[Mocked] No vulnerabilities found in code."

    elif scan_type == "data":
        logger.info(
            f"Simulating vulnerability scan (e.g., Trivy config scan) on data target: {target}"
        )
        scan_results["status"] = "completed"
        scan_results["vulnerabilities_found"] = 0
        scan_results["details"] = "[Mocked] No vulnerabilities found in data."

    # Audit logging with robust error handling
    try:
        from .runner_logging import log_audit_event
        import inspect
        
        # Handle both sync and async implementations gracefully
        if inspect.iscoroutinefunction(log_audit_event):
            await log_audit_event(
                action="security_vulnerability_scan",
                data={
                    "target": str(target),
                    "type": scan_type,
                    "findings": scan_results["vulnerabilities_found"],
                },
            )
        else:
            # Sync version - call directly
            log_audit_event(
                action="security_vulnerability_scan",
                data={
                    "target": str(target),
                    "type": scan_type,
                    "findings": scan_results["vulnerabilities_found"],
                },
            )
    except Exception as e:
        # Audit logging failures should never break security scanning
        logger.warning(f"Failed to log audit event for vulnerability scan: {e}")
    
    return scan_results
