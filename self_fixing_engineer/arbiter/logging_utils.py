"""
Production-grade logging utilities for the Arbiter platform.
Provides PII redaction, structured logging, audit trails, and security features.
"""

import logging
import re
import json
import hashlib
import sys
import os
from typing import Any, Dict, List, Optional, Pattern, Tuple, Union
from datetime import datetime, timezone
from functools import lru_cache
from contextlib import contextmanager
import threading
from pathlib import Path
from enum import Enum
import traceback

# Try to import cryptography for advanced redaction
try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# Thread-local storage for context
_context = threading.local()


class LogLevel(Enum):
    """Enhanced log levels for security and audit events."""
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL
    AUDIT = 25  # Between INFO and WARNING
    SECURITY = 35  # Between WARNING and ERROR


class PIIRedactorFilter(logging.Filter):
    """
    Advanced PII redaction filter with configurable patterns and performance optimization.
    Supports multiple redaction strategies and maintains audit trail of redactions.
    """
    
    # Comprehensive PII patterns with named groups for better tracking
    DEFAULT_PATTERNS: List[Tuple[Pattern, str, str]] = [
        # Email addresses
        (re.compile(r'\b(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})\b'), '[EMAIL_REDACTED]', 'email'),
        
        # Social Security Numbers (various formats)
        (re.compile(r'\b(?P<ssn>\d{3}-\d{2}-\d{4}|\d{9})\b'), '[SSN_REDACTED]', 'ssn'),
        
        # Credit card numbers (with optional spaces/dashes)
        (re.compile(r'\b(?P<cc>(?:\d[ -]?){13,19})\b'), '[CC_REDACTED]', 'credit_card'),
        
        # Phone numbers (international and US formats)
        (re.compile(r'\b(?P<phone>(?:\+?1[ .-]?)?\(?[0-9]{3}\)?[ .-]?[0-9]{3}[ .-]?[0-9]{4})\b'), '[PHONE_REDACTED]', 'phone'),
        
        # IP addresses (IPv4 and IPv6)
        (re.compile(r'\b(?P<ipv4>(?:[0-9]{1,3}\.){3}[0-9]{1,3})\b'), '[IP_REDACTED]', 'ipv4'),
        (re.compile(r'\b(?P<ipv6>(?:[0-9a-fA-F]{0,4}:){7}[0-9a-fA-F]{0,4})\b'), '[IPV6_REDACTED]', 'ipv6'),
        
        # API keys, tokens, and secrets (various formats)
        (re.compile(r'(?P<api_key>(?:api[_-]?key|apikey|api_secret)["\']?\s*[:=]\s*["\']?[\w\-]+)', re.IGNORECASE), '[API_KEY_REDACTED]', 'api_key'),
        (re.compile(r'(?P<token>(?:auth[_-]?token|access[_-]?token|bearer|token)["\']?\s*[:=]\s*["\']?[\w\-\.]+)', re.IGNORECASE), '[TOKEN_REDACTED]', 'token'),
        (re.compile(r'(?P<password>(?:password|passwd|pwd)["\']?\s*[:=]\s*["\']?[^\s"\']+)', re.IGNORECASE), '[PASSWORD_REDACTED]', 'password'),
        (re.compile(r'(?P<secret>(?:secret|private[_-]?key)["\']?\s*[:=]\s*["\']?[\w\-]+)', re.IGNORECASE), '[SECRET_REDACTED]', 'secret'),
        
        # AWS specific patterns
        (re.compile(r'(?P<aws_key>AKIA[0-9A-Z]{16})'), '[AWS_KEY_REDACTED]', 'aws_key'),
        (re.compile(r'(?P<aws_secret>[0-9a-zA-Z/+=]{40})'), '[AWS_SECRET_REDACTED]', 'aws_secret'),
        
        # JWT tokens
        (re.compile(r'(?P<jwt>eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)'), '[JWT_REDACTED]', 'jwt'),
        
        # Database connection strings
        (re.compile(r'(?P<db_conn>(?:mongodb|mysql|postgresql|redis)://[^\s]+)', re.IGNORECASE), '[DB_CONN_REDACTED]', 'db_connection'),
        
        # File paths with user directories
        (re.compile(r'(?P<user_path>/(?:home|users)/[\w\-]+/[^\s]*)'), '[USER_PATH_REDACTED]', 'user_path'),
        (re.compile(r'(?P<win_path>C:\\Users\\[\w\-]+\\[^\s]*)'), '[WIN_PATH_REDACTED]', 'windows_path'),
    ]
    
    def __init__(
        self,
        patterns: Optional[List[Tuple[Pattern, str, str]]] = None,
        redaction_callback: Optional[callable] = None,
        enable_metrics: bool = True,
        enable_audit: bool = True,
        hash_pii: bool = True,
        custom_redactor: Optional[callable] = None
    ):
        """
        Initialize the PII redactor with configurable options.
        
        Args:
            patterns: Custom patterns to use instead of defaults
            redaction_callback: Callback function called when PII is redacted
            enable_metrics: Track redaction metrics
            enable_audit: Create audit trail of redactions
            hash_pii: Store hashed version of PII for correlation
            custom_redactor: Custom redaction function for complex logic
        """
        super().__init__()
        self.patterns = patterns or self.DEFAULT_PATTERNS
        self.redaction_callback = redaction_callback
        self.enable_metrics = enable_metrics
        self.enable_audit = enable_audit
        self.hash_pii = hash_pii
        self.custom_redactor = custom_redactor
        
        # Metrics tracking
        self.redaction_stats = {
            'total_redactions': 0,
            'redactions_by_type': {},
            'last_redaction_time': None
        }
        
        # Cache for performance
        self._cache = {}
        self._cache_size = 1000
        self._lock = threading.Lock()
        
    def filter(self, record: logging.LogRecord) -> bool:
        """
        Filter and redact PII from log records.
        
        Args:
            record: The log record to filter
            
        Returns:
            True (always allows record through after redaction)
        """
        try:
            # Redact message
            if hasattr(record, 'msg'):
                original = str(record.msg)
                record.msg = self._redact_text(original)
                if self.enable_audit and original != record.msg:
                    self._audit_redaction(record, original, record.msg)
            
            # Redact arguments
            if hasattr(record, 'args') and record.args:
                record.args = self._redact_args(record.args)
            
            # Redact exception info
            if record.exc_info and record.exc_info[1]:
                record.exc_text = self._redact_text(str(record.exc_info[1]))
            
            # Add security context if available
            if hasattr(_context, 'security_context'):
                record.security_context = _context.security_context
            
            return True
            
        except Exception as e:
            # Never fail logging due to redaction errors
            logging.getLogger(__name__).error(f"Redaction filter error: {e}", exc_info=False)
            return True
    
    def _redact_text(self, text: str) -> str:
        """
        Efficiently redact PII from text with caching.
        
        Args:
            text: Text to redact
            
        Returns:
            Redacted text
        """
        if not text:
            return text
        
        # Check cache
        text_hash = hashlib.md5(text.encode()).hexdigest()
        with self._lock:
            if text_hash in self._cache:
                return self._cache[text_hash]
        
        redacted = text
        redacted_items = []
        
        # Apply custom redactor first if available
        if self.custom_redactor:
            redacted = self.custom_redactor(redacted)
        
        # Apply pattern-based redaction
        for pattern, replacement, pii_type in self.patterns:
            matches = pattern.finditer(redacted)
            for match in matches:
                if self.hash_pii:
                    # Store hash for correlation
                    pii_hash = hashlib.sha256(match.group().encode()).hexdigest()[:8]
                    replacement_with_hash = f"{replacement}:{pii_hash}"
                    redacted = redacted[:match.start()] + replacement_with_hash + redacted[match.end():]
                else:
                    redacted = redacted[:match.start()] + replacement + redacted[match.end():]
                
                redacted_items.append(pii_type)
                
                # Update metrics
                if self.enable_metrics:
                    self._update_metrics(pii_type)
        
        # Cache result
        with self._lock:
            if len(self._cache) >= self._cache_size:
                # Simple LRU: remove oldest
                self._cache.pop(next(iter(self._cache)))
            self._cache[text_hash] = redacted
        
        # Callback notification
        if self.redaction_callback and redacted_items:
            try:
                self.redaction_callback(redacted_items)
            except Exception:
                pass  # Don't let callback errors affect logging
        
        return redacted
    
    def _redact_args(self, args: Union[tuple, dict]) -> Union[tuple, dict]:
        """Recursively redact PII from log arguments."""
        if isinstance(args, dict):
            return {k: self._redact_value(v) for k, v in args.items()}
        elif isinstance(args, (list, tuple)):
            return type(args)(self._redact_value(v) for v in args)
        else:
            return self._redact_value(args)
    
    def _redact_value(self, value: Any) -> Any:
        """Redact a single value if it's a string."""
        if isinstance(value, str):
            return self._redact_text(value)
        elif isinstance(value, (dict, list, tuple)):
            return self._redact_args(value)
        return value
    
    def _update_metrics(self, pii_type: str):
        """Update redaction metrics."""
        with self._lock:
            self.redaction_stats['total_redactions'] += 1
            self.redaction_stats['redactions_by_type'][pii_type] = \
                self.redaction_stats['redactions_by_type'].get(pii_type, 0) + 1
            self.redaction_stats['last_redaction_time'] = datetime.now(timezone.utc).isoformat()
    
    def _audit_redaction(self, record: logging.LogRecord, original: str, redacted: str):
        """Create audit trail of redaction."""
        audit_logger = logging.getLogger('arbiter.audit.redaction')
        if audit_logger.handlers:  # Only log if audit logger is configured
            audit_logger.info(
                "PII redacted",
                extra={
                    'original_length': len(original),
                    'redacted_length': len(redacted),
                    'source_logger': record.name,
                    'source_level': record.levelname,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
            )
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get redaction metrics."""
        with self._lock:
            return self.redaction_stats.copy()
    
    def clear_cache(self):
        """Clear the redaction cache."""
        with self._lock:
            self._cache.clear()


class StructuredFormatter(logging.Formatter):
    """
    Structured JSON formatter for machine-readable logs.
    Includes additional context and metadata.
    """
    
    def __init__(self, include_traceback: bool = True):
        super().__init__()
        self.include_traceback = include_traceback
        self.hostname = os.uname().nodename if hasattr(os, 'uname') else os.environ.get('COMPUTERNAME', 'unknown')
        self.process_id = os.getpid()
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'thread': record.thread,
            'thread_name': record.threadName,
            'process': self.process_id,
            'hostname': self.hostname,
        }
        
        # Add exception info if present
        if record.exc_info and self.include_traceback:
            log_obj['exception'] = {
                'type': record.exc_info[0].__name__,
                'message': str(record.exc_info[1]),
                'traceback': traceback.format_exception(*record.exc_info)
            }
        
        # Add custom fields
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'message', 'pathname', 'process', 'processName', 'relativeCreated',
                          'thread', 'threadName', 'exc_info', 'exc_text', 'stack_info']:
                log_obj[key] = value
        
        return json.dumps(log_obj, default=str)


class AuditLogger:
    """
    Specialized logger for audit trails with guaranteed delivery.
    """
    
    def __init__(self, name: str = 'arbiter.audit', log_file: Optional[str] = None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(LogLevel.AUDIT.value)
        self.logger.propagate = False
        
        if log_file:
            handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=100 * 1024 * 1024,  # 100MB
                backupCount=10
            )
            handler.setFormatter(StructuredFormatter())
            self.logger.addHandler(handler)
    
    def log_event(self, event_type: str, **kwargs):
        """Log an audit event."""
        self.logger.log(
            LogLevel.AUDIT.value,
            f"AUDIT: {event_type}",
            extra={
                'event_type': event_type,
                'event_data': kwargs,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
        )


def get_logger(
    name: str,
    level: int = logging.INFO,
    enable_pii_filter: bool = True,
    structured: bool = False
) -> logging.Logger:
    """
    Get a configured logger instance.
    
    Args:
        name: Logger name
        level: Logging level
        enable_pii_filter: Enable PII redaction
        structured: Use structured JSON logging
        
    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        
        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
            )
        
        if enable_pii_filter:
            handler.addFilter(PIIRedactorFilter())
        
        logger.addHandler(handler)
    
    return logger


@contextmanager
def logging_context(**kwargs):
    """
    Context manager for adding contextual information to logs.
    
    Usage:
        with logging_context(user_id='123', request_id='abc'):
            logger.info('Processing request')  # Will include context
    """
    old_context = getattr(_context, 'security_context', {})
    _context.security_context = {**old_context, **kwargs}
    try:
        yield
    finally:
        _context.security_context = old_context


def configure_logging(
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    structured: bool = False,
    enable_pii_filter: bool = True,
    audit_file: Optional[str] = None
) -> None:
    """
    Configure global logging settings for the application.
    
    Args:
        level: Global logging level
        log_file: Optional file to write logs
        structured: Use structured JSON format
        enable_pii_filter: Enable PII redaction globally
        audit_file: Optional separate audit log file
    """
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if structured:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
        )
    
    if enable_pii_filter:
        console_handler.addFilter(PIIRedactorFilter())
    
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=50 * 1024 * 1024,  # 50MB
            backupCount=5
        )
        if structured:
            file_handler.setFormatter(StructuredFormatter())
        else:
            file_handler.setFormatter(
                logging.Formatter(
                    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S'
                )
            )
        
        if enable_pii_filter:
            file_handler.addFilter(PIIRedactorFilter())
        
        root_logger.addHandler(file_handler)
    
    # Configure audit logger if specified
    if audit_file:
        AuditLogger(log_file=audit_file)
    
    # Add custom log levels
    for level in LogLevel:
        if not hasattr(logging, level.name):
            logging.addLevelName(level.value, level.name)


# Convenience functions
@lru_cache(maxsize=128)
def get_redaction_patterns() -> List[Tuple[Pattern, str, str]]:
    """Get cached redaction patterns for performance."""
    return PIIRedactorFilter.DEFAULT_PATTERNS


def redact_text(text: str) -> str:
    """Convenience function to redact PII from text."""
    filter = PIIRedactorFilter()
    return filter._redact_text(text)


# Export main components
__all__ = [
    'PIIRedactorFilter',
    'StructuredFormatter',
    'AuditLogger',
    'LogLevel',
    'get_logger',
    'configure_logging',
    'logging_context',
    'redact_text',
    'get_redaction_patterns'
]