# self_healing_import_fixer/analyzer/core_utils.py

"""
Enterprise-grade utility module for production analyzer system.
Provides robust alerting, monitoring, security, and operational utilities.
"""

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import secrets
import socket
import threading
import time
import traceback
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

# Third-party imports with graceful fallbacks
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    import boto3
    from botocore.exceptions import ClientError

    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False
    ClientError = Exception

try:
    from prometheus_client import Counter, Gauge, Histogram, Summary

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False

# Configure module logger
logger = logging.getLogger(__name__)

# Global constants
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
SERVICE_NAME = os.getenv("SERVICE_NAME", "analyzer")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
REGION = os.getenv("AWS_REGION", "us-east-1")
HOSTNAME = socket.gethostname()
INSTANCE_ID = os.getenv("INSTANCE_ID", str(uuid.uuid4()))

# Rate limiting configuration
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_CALLS = 100

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_MULTIPLIER = 2.0

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RECOVERY_TIMEOUT = 60
CIRCUIT_BREAKER_EXPECTED_EXCEPTION = Exception


# Alert levels
class AlertLevel(Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


# Alert channels
class AlertChannel(Enum):
    LOG = "log"
    SLACK = "slack"
    PAGERDUTY = "pagerduty"
    EMAIL = "email"
    SNS = "sns"
    DATADOG = "datadog"
    OPSGENIE = "opsgenie"
    WEBHOOK = "webhook"


@dataclass
class AlertConfig:
    """Configuration for alerting system."""

    slack_webhook_url: Optional[str] = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL"))
    pagerduty_routing_key: Optional[str] = field(
        default_factory=lambda: os.getenv("PAGERDUTY_ROUTING_KEY")
    )
    email_smtp_host: Optional[str] = field(default_factory=lambda: os.getenv("SMTP_HOST"))
    email_smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    email_from: Optional[str] = field(default_factory=lambda: os.getenv("ALERT_EMAIL_FROM"))
    email_to: List[str] = field(default_factory=lambda: os.getenv("ALERT_EMAIL_TO", "").split(","))
    sns_topic_arn: Optional[str] = field(default_factory=lambda: os.getenv("SNS_TOPIC_ARN"))
    datadog_api_key: Optional[str] = field(default_factory=lambda: os.getenv("DATADOG_API_KEY"))
    opsgenie_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPSGENIE_API_KEY"))
    webhook_urls: List[str] = field(
        default_factory=lambda: os.getenv("WEBHOOK_URLS", "").split(",")
    )
    enabled_channels: List[AlertChannel] = field(
        default_factory=lambda: [
            AlertChannel(ch) for ch in os.getenv("ALERT_CHANNELS", "log,slack").split(",")
        ]
    )


# Metrics collectors (if Prometheus is available)
if PROMETHEUS_AVAILABLE:
    alert_counter = Counter("analyzer_alerts_total", "Total number of alerts", ["level", "channel"])
    operation_histogram = Histogram(
        "analyzer_operation_duration_seconds", "Operation duration", ["operation"]
    )
    error_counter = Counter("analyzer_errors_total", "Total number of errors", ["error_type"])
    active_operations = Gauge("analyzer_active_operations", "Number of active operations")
    cache_hits = Counter("analyzer_cache_hits_total", "Cache hit count")
    cache_misses = Counter("analyzer_cache_misses_total", "Cache miss count")
else:
    # Dummy metrics for when Prometheus is not available
    class DummyMetric:
        # Add DEFAULT_BUCKETS to match Histogram.DEFAULT_BUCKETS
        DEFAULT_BUCKETS = (
            0.005,
            0.01,
            0.025,
            0.05,
            0.075,
            0.1,
            0.25,
            0.5,
            0.75,
            1.0,
            2.5,
            5.0,
            7.5,
            10.0,
            float("inf"),
        )

        def labels(self, **kwargs):
            return self

        def inc(self, amount=1):
            pass

        def dec(self, amount=1):
            pass

        def observe(self, amount):
            pass

        def set(self, value):
            pass

    alert_counter = DummyMetric()
    operation_histogram = DummyMetric()
    error_counter = DummyMetric()
    active_operations = DummyMetric()
    cache_hits = DummyMetric()
    cache_misses = DummyMetric()

# Global alert configuration
_alert_config = AlertConfig()


class CircuitBreaker:
    """Circuit breaker pattern implementation for fault tolerance."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        recovery_timeout: int = CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
        expected_exception: type = CIRCUIT_BREAKER_EXPECTED_EXCEPTION,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time = None
        self.state = "closed"  # closed, open, half-open
        self._lock = threading.Lock()

    def call(self, func: Callable, *args, **kwargs) -> Any:
        with self._lock:
            if self.state == "open":
                if self._should_attempt_reset():
                    self.state = "half-open"
                else:
                    raise Exception(f"Circuit breaker {self.name} is open")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e

    def _should_attempt_reset(self) -> bool:
        return (
            self.last_failure_time and time.time() - self.last_failure_time >= self.recovery_timeout
        )

    def _on_success(self):
        with self._lock:
            self.failure_count = 0
            self.state = "closed"

    def _on_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "open"
                logger.error(
                    f"Circuit breaker {self.name} opened after {self.failure_count} failures"
                )


class RateLimiter:
    """Token bucket rate limiter for API calls."""

    def __init__(
        self,
        max_calls: int = RATE_LIMIT_MAX_CALLS,
        window_seconds: int = RATE_LIMIT_WINDOW,
    ):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.calls = deque()
        self._lock = threading.Lock()

    def is_allowed(self) -> bool:
        with self._lock:
            now = time.time()
            # Remove old calls outside the window
            while self.calls and self.calls[0] < now - self.window_seconds:
                self.calls.popleft()

            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            return False

    def wait_if_needed(self):
        """Block until rate limit allows the call."""
        while not self.is_allowed():
            time.sleep(0.1)


# Global instances
_circuit_breakers: Dict[str, CircuitBreaker] = {}
_rate_limiters: Dict[str, RateLimiter] = {}
_cache: Dict[str, Tuple[Any, float]] = {}
_cache_lock = threading.Lock()


def get_circuit_breaker(name: str) -> CircuitBreaker:
    """Get or create a circuit breaker for a service."""
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(name)
    return _circuit_breakers[name]


def get_rate_limiter(name: str) -> RateLimiter:
    """Get or create a rate limiter for a service."""
    if name not in _rate_limiters:
        _rate_limiters[name] = RateLimiter()
    return _rate_limiters[name]


@contextmanager
def timing_context(operation_name: str):
    """Context manager for timing operations."""
    start_time = time.time()
    active_operations.inc()
    try:
        yield
    finally:
        duration = time.time() - start_time
        operation_histogram.labels(operation=operation_name).observe(duration)
        active_operations.dec()
        logger.debug(f"Operation {operation_name} took {duration:.3f} seconds")


def retry_with_backoff(
    max_retries: int = MAX_RETRIES,
    initial_backoff: float = INITIAL_BACKOFF,
    max_backoff: float = MAX_BACKOFF,
    backoff_multiplier: float = BACKOFF_MULTIPLIER,
    exceptions: Tuple[type, ...] = (Exception,),
):
    """Decorator for retrying functions with exponential backoff."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            backoff = initial_backoff
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        sleep_time = min(backoff, max_backoff)
                        logger.warning(
                            f"Attempt {attempt + 1} failed for {func.__name__}: {e}. "
                            f"Retrying in {sleep_time:.1f} seconds..."
                        )
                        time.sleep(sleep_time)
                        backoff *= backoff_multiplier
                    else:
                        logger.error(f"All {max_retries} attempts failed for {func.__name__}")

            raise last_exception

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            backoff = initial_backoff
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        sleep_time = min(backoff, max_backoff)
                        logger.warning(
                            f"Attempt {attempt + 1} failed for {func.__name__}: {e}. "
                            f"Retrying in {sleep_time:.1f} seconds..."
                        )
                        await asyncio.sleep(sleep_time)
                        backoff *= backoff_multiplier
                    else:
                        logger.error(f"All {max_retries} attempts failed for {func.__name__}")

            raise last_exception

        return async_wrapper if asyncio.iscoroutinefunction(func) else wrapper

    return decorator


def cached(ttl_seconds: int = 300):
    """Decorator for caching function results."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"

            with _cache_lock:
                if cache_key in _cache:
                    value, timestamp = _cache[cache_key]
                    if time.time() - timestamp < ttl_seconds:
                        cache_hits.inc()
                        return value
                    else:
                        del _cache[cache_key]

            cache_misses.inc()
            result = func(*args, **kwargs)

            with _cache_lock:
                _cache[cache_key] = (result, time.time())

            return result

        return wrapper

    return decorator


def alert_operator(
    message: str,
    level: Union[str, AlertLevel] = AlertLevel.ERROR,
    details: Optional[Dict[str, Any]] = None,
    channels: Optional[List[AlertChannel]] = None,
    dedupe_key: Optional[str] = None,
):
    """
    Send alerts to configured channels with deduplication and rate limiting.

    Args:
        message: Alert message
        level: Alert severity level
        details: Additional context
        channels: Specific channels to use (defaults to configured channels)
        dedupe_key: Key for deduplication (prevents duplicate alerts)
    """
    if isinstance(level, str):
        level = AlertLevel(level.upper())

    channels = channels or _alert_config.enabled_channels

    # Rate limiting for non-critical alerts
    if level not in [AlertLevel.CRITICAL, AlertLevel.EMERGENCY]:
        limiter = get_rate_limiter("alerts")
        if not limiter.is_allowed():
            logger.warning(f"Alert rate limited: {message}")
            return

    # Deduplication
    if dedupe_key:
        cache_key = f"alert_dedupe:{dedupe_key}"
        with _cache_lock:
            if cache_key in _cache:
                _, timestamp = _cache[cache_key]
                if time.time() - timestamp < 300:  # 5 minute deduplication window
                    return
            _cache[cache_key] = (True, time.time())

    # Prepare alert data
    alert_data = {
        "timestamp": datetime.utcnow().isoformat(),
        "level": level.value,
        "message": message,
        "service": SERVICE_NAME,
        "environment": ENVIRONMENT,
        "hostname": HOSTNAME,
        "instance_id": INSTANCE_ID,
        "details": details or {},
    }

    # Add stack trace for errors
    if level in [AlertLevel.ERROR, AlertLevel.CRITICAL, AlertLevel.EMERGENCY]:
        alert_data["stack_trace"] = traceback.format_exc()

    # Send to each channel
    for channel in channels:
        try:
            _send_alert_to_channel(channel, alert_data)
            alert_counter.labels(level=level.value, channel=channel.value).inc()
        except Exception as e:
            logger.error(f"Failed to send alert to {channel.value}: {e}")
            error_counter.labels(error_type="alert_failure").inc()


def _send_alert_to_channel(channel: AlertChannel, alert_data: Dict[str, Any]):
    """Send alert to specific channel."""

    if channel == AlertChannel.LOG:
        level = alert_data["level"]
        log_level = getattr(logging, level, logging.ERROR)

        # Create a copy of alert_data without 'message' to avoid LogRecord conflict
        extra_data = {k: v for k, v in alert_data.items() if k != "message"}

        # Log with the message as the main argument and other data as extra
        logger.log(log_level, f"ALERT: {alert_data['message']}", extra=extra_data)

    elif channel == AlertChannel.SLACK and _alert_config.slack_webhook_url:
        _send_slack_alert(alert_data)

    elif channel == AlertChannel.PAGERDUTY and _alert_config.pagerduty_routing_key:
        _send_pagerduty_alert(alert_data)

    elif channel == AlertChannel.EMAIL and _alert_config.email_smtp_host:
        _send_email_alert(alert_data)

    elif channel == AlertChannel.SNS and _alert_config.sns_topic_arn and AWS_AVAILABLE:
        _send_sns_alert(alert_data)

    elif channel == AlertChannel.DATADOG and _alert_config.datadog_api_key:
        _send_datadog_alert(alert_data)

    elif channel == AlertChannel.OPSGENIE and _alert_config.opsgenie_api_key:
        _send_opsgenie_alert(alert_data)

    elif channel == AlertChannel.WEBHOOK and _alert_config.webhook_urls:
        _send_webhook_alerts(alert_data)


@retry_with_backoff(exceptions=(Exception,))
def _send_slack_alert(alert_data: Dict[str, Any]):
    """Send alert to Slack."""
    if not AIOHTTP_AVAILABLE:
        return

    color_map = {
        "DEBUG": "#808080",
        "INFO": "#36a64f",
        "WARNING": "#ff9900",
        "ERROR": "#ff0000",
        "CRITICAL": "#8b0000",
        "EMERGENCY": "#000000",
    }

    payload = {
        "attachments": [
            {
                "color": color_map.get(alert_data["level"], "#ff0000"),
                "title": f"{alert_data['level']}: {alert_data['message']}",
                "fields": [
                    {"title": "Service", "value": alert_data["service"], "short": True},
                    {
                        "title": "Environment",
                        "value": alert_data["environment"],
                        "short": True,
                    },
                    {
                        "title": "Hostname",
                        "value": alert_data["hostname"],
                        "short": True,
                    },
                    {
                        "title": "Timestamp",
                        "value": alert_data["timestamp"],
                        "short": True,
                    },
                ],
                "footer": f"Instance: {alert_data['instance_id']}",
            }
        ]
    }

    if alert_data.get("stack_trace"):
        payload["attachments"][0]["text"] = f"```{alert_data['stack_trace'][:1000]}```"

    # Synchronous HTTP request (use aiohttp in async context)
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        _alert_config.slack_webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    urllib.request.urlopen(req, timeout=5)


def _send_pagerduty_alert(alert_data: Dict[str, Any]):
    """Send alert to PagerDuty."""
    severity_map = {
        "DEBUG": "info",
        "INFO": "info",
        "WARNING": "warning",
        "ERROR": "error",
        "CRITICAL": "critical",
        "EMERGENCY": "critical",
    }

    payload = {
        "routing_key": _alert_config.pagerduty_routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": alert_data["message"],
            "severity": severity_map.get(alert_data["level"], "error"),
            "source": alert_data["hostname"],
            "component": alert_data["service"],
            "group": alert_data["environment"],
            "custom_details": alert_data["details"],
        },
    }

    # Implementation would send to PagerDuty Events API v2
    logger.info(f"Would send PagerDuty alert: {payload}")


def _send_email_alert(alert_data: Dict[str, Any]):
    """Send alert via email."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart()
    msg["From"] = _alert_config.email_from
    msg["To"] = ", ".join(_alert_config.email_to)
    msg["Subject"] = f"[{alert_data['level']}] {alert_data['message']}"

    body = json.dumps(alert_data, indent=2)
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(_alert_config.email_smtp_host, _alert_config.email_smtp_port) as server:
        server.starttls()
        # Add authentication if needed
        server.send_message(msg)


def _send_sns_alert(alert_data: Dict[str, Any]):
    """Send alert to AWS SNS."""
    if not AWS_AVAILABLE:
        return

    sns = boto3.client("sns", region_name=REGION)

    sns.publish(
        TopicArn=_alert_config.sns_topic_arn,
        Subject=f"[{alert_data['level']}] {alert_data['service']} Alert",
        Message=json.dumps(alert_data, indent=2),
    )


def _send_datadog_alert(alert_data: Dict[str, Any]):
    """Send alert to Datadog."""
    # Implementation would use Datadog API
    logger.info(f"Would send Datadog alert: {alert_data}")


def _send_opsgenie_alert(alert_data: Dict[str, Any]):
    """Send alert to OpsGenie."""
    # Implementation would use OpsGenie API
    logger.info(f"Would send OpsGenie alert: {alert_data}")


def _send_webhook_alerts(alert_data: Dict[str, Any]):
    """Send alerts to configured webhooks."""
    for url in _alert_config.webhook_urls:
        if url:
            try:
                import urllib.request

                req = urllib.request.Request(
                    url,
                    data=json.dumps(alert_data).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                logger.error(f"Failed to send webhook to {url}: {e}")


def scrub_secrets(data: Any, max_depth: int = 10) -> Any:
    """
    Recursively scrub sensitive information from data structures.

    Args:
        data: Data to scrub (dict, list, or primitive)
        max_depth: Maximum recursion depth to prevent infinite loops

    Returns:
        Scrubbed copy of the data
    """
    if max_depth <= 0:
        return "***MAX_DEPTH_EXCEEDED***"

    # Patterns for sensitive data
    sensitive_patterns = [
        r"(?i)(password|passwd|pwd|pass)[\s]*[=:]\s*[\S]+",
        r"(?i)(token|api[_-]?key|secret|auth)[\s]*[=:]\s*[\S]+",
        r"(?i)(aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key)[\s]*[=:]\s*[\S]+",
        r"(?i)bearer\s+[\S]+",
        r"(?i)basic\s+[\S]+",
        r"[a-zA-Z0-9+/]{40,}={0,2}",  # Base64 encoded strings
        r"(?:\d{4}[-\s]?){3}\d{4}",  # Credit card numbers
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
    ]

    sensitive_keys = {
        "password",
        "passwd",
        "pwd",
        "pass",
        "secret",
        "token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "credential",
        "private_key",
        "privatekey",
        "client_secret",
        "access_token",
        "refresh_token",
        "bearer",
        "session",
        "cookie",
        "jwt",
        "aws_access_key_id",
        "aws_secret_access_key",
        "database_url",
        "connection_string",
        "dsn",
        "encryption_key",
        "salt",
        "hash",
    }

    if isinstance(data, dict):
        scrubbed = {}
        for key, value in data.items():
            # Check if key contains sensitive terms
            if any(term in key.lower() for term in sensitive_keys):
                scrubbed[key] = "***REDACTED***"
            else:
                scrubbed[key] = scrub_secrets(value, max_depth - 1)
        return scrubbed

    elif isinstance(data, list):
        return [scrub_secrets(item, max_depth - 1) for item in data]

    elif isinstance(data, str):
        # Check for sensitive patterns in strings
        scrubbed_str = data
        for pattern in sensitive_patterns:
            if re.search(pattern, scrubbed_str):
                scrubbed_str = re.sub(pattern, "***REDACTED***", scrubbed_str)
        return scrubbed_str

    else:
        return data


def validate_input(data: Any, schema: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Validate input data against a schema.

    Args:
        data: Data to validate
        schema: Validation schema

    Returns:
        Tuple of (is_valid, error_message)
    """
    # Simple schema validation - extend as needed
    if not isinstance(data, dict):
        return False, "Data must be a dictionary"

    for field_name, requirements in schema.items():
        if "required" in requirements and requirements["required"]:
            if field_name not in data:
                return False, f"Required field '{field_name}' is missing"

        if field_name in data:
            value = data[field_name]

            if "type" in requirements:
                expected_type = requirements["type"]
                if not isinstance(value, expected_type):
                    return (
                        False,
                        f"Field '{field_name}' must be of type {expected_type.__name__}",
                    )

            if "min_length" in requirements and isinstance(value, str):
                if len(value) < requirements["min_length"]:
                    return (
                        False,
                        f"Field '{field_name}' must be at least {requirements['min_length']} characters",
                    )

            if "max_length" in requirements and isinstance(value, str):
                if len(value) > requirements["max_length"]:
                    return (
                        False,
                        f"Field '{field_name}' must be at most {requirements['max_length']} characters",
                    )

            if "pattern" in requirements and isinstance(value, str):
                if not re.match(requirements["pattern"], value):
                    return (
                        False,
                        f"Field '{field_name}' does not match required pattern",
                    )

            if "enum" in requirements:
                if value not in requirements["enum"]:
                    return (
                        False,
                        f"Field '{field_name}' must be one of {requirements['enum']}",
                    )

    return True, None


def generate_correlation_id() -> str:
    """Generate a unique correlation ID for request tracing."""
    return f"{SERVICE_NAME}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def get_system_health() -> Dict[str, Any]:
    """Get current system health metrics."""
    import psutil

    health = {
        "timestamp": datetime.utcnow().isoformat(),
        "service": SERVICE_NAME,
        "environment": ENVIRONMENT,
        "hostname": HOSTNAME,
        "instance_id": INSTANCE_ID,
        "status": "healthy",
        "checks": {},
    }

    # CPU usage
    health["checks"]["cpu"] = {
        "usage_percent": psutil.cpu_percent(interval=1),
        "status": "healthy" if psutil.cpu_percent() < 80 else "degraded",
    }

    # Memory usage
    memory = psutil.virtual_memory()
    health["checks"]["memory"] = {
        "usage_percent": memory.percent,
        "available_gb": memory.available / (1024**3),
        "status": "healthy" if memory.percent < 85 else "degraded",
    }

    # Disk usage
    disk = psutil.disk_usage("/")
    health["checks"]["disk"] = {
        "usage_percent": disk.percent,
        "free_gb": disk.free / (1024**3),
        "status": "healthy" if disk.percent < 90 else "degraded",
    }

    # Circuit breakers
    open_breakers = [name for name, cb in _circuit_breakers.items() if cb.state == "open"]
    health["checks"]["circuit_breakers"] = {
        "open_count": len(open_breakers),
        "open_breakers": open_breakers,
        "status": "healthy" if len(open_breakers) == 0 else "degraded",
    }

    # Overall status
    if any(check["status"] != "healthy" for check in health["checks"].values()):
        health["status"] = "degraded"

    return health


def secure_hash(data: str, salt: Optional[str] = None) -> str:
    """Generate a secure hash of data."""
    if salt is None:
        salt = secrets.token_hex(16)

    hash_obj = hashlib.pbkdf2_hmac("sha256", data.encode(), salt.encode(), 100000)
    return f"{salt}${hash_obj.hex()}"


def verify_hash(data: str, hashed: str) -> bool:
    """Verify data against a secure hash."""
    try:
        salt, hash_value = hashed.split("$")
        return secure_hash(data, salt) == hashed
    except ValueError:
        return False


def sanitize_path(path: str) -> str:
    """Sanitize file paths to prevent directory traversal attacks."""

    # Check if path contains directory traversal attempts
    has_traversal = ".." in path

    # Remove dangerous patterns
    path = path.replace("..", "")
    path = path.replace("~", "")

    # Strip leading/trailing slashes
    path = path.strip("/\\")

    if has_traversal:
        # If there was a traversal attempt, flatten the entire path
        path = path.replace("/", "").replace("\\", "")
    else:
        # Otherwise, just get the basename (last component)
        path = os.path.normpath(path)
        path = os.path.basename(path)

    return path


@contextmanager
def distributed_lock(lock_name: str, timeout: int = 30):
    """
    Distributed lock using Redis if available, otherwise local lock.

    Args:
        lock_name: Name of the lock
        timeout: Lock timeout in seconds
    """
    if REDIS_AVAILABLE:
        try:
            r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
            lock = r.lock(f"lock:{lock_name}", timeout=timeout)
            lock.acquire()
            try:
                yield
            finally:
                lock.release()
        except Exception as e:
            logger.warning(f"Redis lock failed, falling back to local lock: {e}")
            with threading.Lock():
                yield
    else:
        with threading.Lock():
            yield


def encode_for_logging(obj: Any) -> str:
    """Safely encode objects for logging."""
    try:
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        elif isinstance(obj, (dict, list)):
            return json.dumps(obj, default=str, ensure_ascii=False)
        else:
            return str(obj)
    except Exception:
        return repr(obj)


def _initialize():
    """Initialize the utils module."""
    logger.info(f"Initializing {SERVICE_NAME} utils module")
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info(f"Hostname: {HOSTNAME}")
    logger.info(f"Instance ID: {INSTANCE_ID}")
    logger.info(f"Production Mode: {PRODUCTION_MODE}")

    # Verify critical configurations in production
    if PRODUCTION_MODE:
        if not _alert_config.enabled_channels:
            logger.warning("No alert channels configured in production mode!")

        # Test connectivity to critical services
        if AWS_AVAILABLE:
            try:
                sts = boto3.client("sts")
                identity = sts.get_caller_identity()
                logger.info(f"AWS identity confirmed: {identity['Arn']}")
            except Exception as e:
                logger.error(f"Failed to verify AWS credentials: {e}")

        if REDIS_AVAILABLE and os.getenv("REDIS_URL"):
            try:
                r = redis.Redis.from_url(os.getenv("REDIS_URL"))
                r.ping()
                logger.info("Redis connection verified")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")


# Run initialization
_initialize()

# Export public interface
__all__ = [
    "alert_operator",
    "scrub_secrets",
    "AlertLevel",
    "AlertChannel",
    "AlertConfig",
    "CircuitBreaker",
    "RateLimiter",
    "get_circuit_breaker",
    "get_rate_limiter",
    "timing_context",
    "retry_with_backoff",
    "cached",
    "validate_input",
    "generate_correlation_id",
    "get_system_health",
    "secure_hash",
    "verify_hash",
    "sanitize_path",
    "distributed_lock",
    "encode_for_logging",
]


def alert_operator(msg: str, level: str = "INFO") -> None:
    print(f"[OPS ALERT - {level}] {msg}")


def scrub_secrets(obj):
    return obj
