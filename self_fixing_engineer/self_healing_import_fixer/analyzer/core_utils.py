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
    from prometheus_client import Counter, Gauge, Histogram, Summary, REGISTRY

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

    slack_webhook_url: Optional[str] = field(
        default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL")
    )
    pagerduty_routing_key: Optional[str] = field(
        default_factory=lambda: os.getenv("PAGERDUTY_ROUTING_KEY")
    )
    email_smtp_host: Optional[str] = field(
        default_factory=lambda: os.getenv("SMTP_HOST")
    )
    email_smtp_port: int = field(
        default_factory=lambda: int(os.getenv("SMTP_PORT", "587"))
    )
    email_from: Optional[str] = field(
        default_factory=lambda: os.getenv("ALERT_EMAIL_FROM")
    )
    email_to: List[str] = field(
        default_factory=lambda: os.getenv("ALERT_EMAIL_TO", "").split(",")
    )
    sns_topic_arn: Optional[str] = field(
        default_factory=lambda: os.getenv("SNS_TOPIC_ARN")
    )
    datadog_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("DATADOG_API_KEY")
    )
    opsgenie_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("OPSGENIE_API_KEY")
    )
    webhook_urls: List[str] = field(
        default_factory=lambda: os.getenv("WEBHOOK_URLS", "").split(",")
    )
    enabled_channels: List[AlertChannel] = field(
        default_factory=lambda: [
            AlertChannel(ch)
            for ch in os.getenv("ALERT_CHANNELS", "log,slack").split(",")
        ]
    )


def _get_or_create_metric(metric_class, name, description, labelnames=None, **kwargs):
    """
    Safely get or create a Prometheus metric, handling duplicate registration.

    Args:
        metric_class: The metric class (Counter, Gauge, Histogram, Summary)
        name: Metric name
        description: Metric description
        labelnames: List of label names
        **kwargs: Additional metric-specific kwargs

    Returns:
        The metric instance (new or existing)
    """
    try:
        # Try to create the metric
        if labelnames:
            return metric_class(name, description, labelnames, **kwargs)
        else:
            return metric_class(name, description, **kwargs)
    except ValueError as e:
        if "Duplicated timeseries" in str(e):
            # Metric already exists, retrieve it from the registry
            for collector in list(REGISTRY._collector_to_names.keys()):
                if hasattr(collector, "_name") and collector._name == name:
                    logger.debug(
                        f"Metric '{name}' already registered, reusing existing instance"
                    )
                    return collector
            # If we can't find it, create a dummy
            logger.warning(
                f"Metric '{name}' registered but couldn't retrieve, using dummy"
            )
            return _create_dummy_metric()
        else:
            raise


def _create_dummy_metric():
    """Create a dummy metric that does nothing."""

    class DummyMetric:
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

    return DummyMetric()


# Metrics collectors (if Prometheus is available)
if PROMETHEUS_AVAILABLE:
    alert_counter = _get_or_create_metric(
        Counter, "analyzer_alerts_total", "Total number of alerts", ["level", "channel"]
    )
    operation_histogram = _get_or_create_metric(
        Histogram,
        "analyzer_operation_duration_seconds",
        "Operation duration",
        ["operation"],
    )
    error_counter = _get_or_create_metric(
        Counter, "analyzer_errors_total", "Total number of errors", ["error_type"]
    )
    active_operations = _get_or_create_metric(
        Gauge, "analyzer_active_operations", "Number of active operations"
    )
    cache_hits = _get_or_create_metric(
        Counter, "analyzer_cache_hits_total", "Cache hit count"
    )
    cache_misses = _get_or_create_metric(
        Counter, "analyzer_cache_misses_total", "Cache miss count"
    )
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
            self.last_failure_time
            and time.time() - self.last_failure_time >= self.recovery_timeout
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
    """Token bucket rate limiter implementation."""

    def __init__(
        self, max_calls: int = RATE_LIMIT_MAX_CALLS, window: int = RATE_LIMIT_WINDOW
    ):
        self.max_calls = max_calls
        self.window = window
        self.calls = deque()
        self._lock = threading.Lock()

    def is_allowed(self) -> bool:
        with self._lock:
            now = time.time()
            # Remove old calls outside the window
            while self.calls and self.calls[0] <= now - self.window:
                self.calls.popleft()

            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            return False

    def wait_if_needed(self):
        """Block until rate limit allows the call."""
        while not self.is_allowed():
            time.sleep(0.1)


# Global circuit breakers, rate limiters, and cache
_circuit_breakers: Dict[str, CircuitBreaker] = {}
_rate_limiters: Dict[str, RateLimiter] = {}
_cache: Dict[str, Tuple[Any, float]] = {}


def get_circuit_breaker(name: str, **kwargs) -> CircuitBreaker:
    """Get or create a circuit breaker."""
    if name not in _circuit_breakers:
        _circuit_breakers[name] = CircuitBreaker(name, **kwargs)
    return _circuit_breakers[name]


def get_rate_limiter(name: str, **kwargs) -> RateLimiter:
    """Get or create a rate limiter."""
    if name not in _rate_limiters:
        _rate_limiters[name] = RateLimiter(**kwargs)
    return _rate_limiters[name]


@contextmanager
def timing_context(operation: str):
    """Context manager for timing operations and recording metrics."""
    start_time = time.time()
    active_operations.inc()

    try:
        yield
    finally:
        duration = time.time() - start_time
        operation_histogram.labels(operation=operation).observe(duration)
        active_operations.dec()
        logger.debug(f"Operation '{operation}' took {duration:.3f}s")


def retry_with_backoff(
    max_retries: int = MAX_RETRIES,
    initial_backoff: float = INITIAL_BACKOFF,
    max_backoff: float = MAX_BACKOFF,
    backoff_multiplier: float = BACKOFF_MULTIPLIER,
    exceptions: Tuple[type, ...] = (Exception,),
):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        initial_backoff: Initial backoff delay in seconds
        max_backoff: Maximum backoff delay in seconds
        backoff_multiplier: Multiplier for exponential backoff
        exceptions: Tuple of exceptions to catch and retry
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            backoff = initial_backoff
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries + 1} failed for {func.__name__}: {e}. "
                            f"Retrying in {backoff:.1f}s..."
                        )
                        time.sleep(backoff)
                        backoff = min(backoff * backoff_multiplier, max_backoff)
                    else:
                        logger.error(
                            f"All {max_retries + 1} attempts failed for {func.__name__}"
                        )

            raise last_exception

        return wrapper

    return decorator


def cached(ttl: int = 300):
    """
    Simple in-memory cache decorator with TTL.

    Args:
        ttl: Time-to-live in seconds
    """
    cache = {}
    cache_lock = threading.Lock()

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            key = f"{func.__name__}:{str(args)}:{str(kwargs)}"

            with cache_lock:
                # Check if cached and not expired
                if key in cache:
                    value, timestamp = cache[key]
                    if time.time() - timestamp < ttl:
                        cache_hits.inc()
                        logger.debug(f"Cache hit for {func.__name__}")
                        return value
                    else:
                        del cache[key]

                cache_misses.inc()

            # Call function and cache result
            result = func(*args, **kwargs)

            with cache_lock:
                cache[key] = (result, time.time())

            return result

        return wrapper

    return decorator


async def alert_operator_async(
    message: str,
    level: AlertLevel = AlertLevel.INFO,
    channels: Optional[List[AlertChannel]] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Send alerts to operators through configured channels (async version).

    Args:
        message: Alert message
        level: Alert severity level
        channels: List of channels to use (defaults to configured channels)
        metadata: Additional metadata to include with the alert
    """
    if channels is None:
        channels = _alert_config.enabled_channels

    metadata = metadata or {}
    metadata.update(
        {
            "timestamp": datetime.utcnow().isoformat(),
            "service": SERVICE_NAME,
            "environment": ENVIRONMENT,
            "hostname": HOSTNAME,
            "instance_id": INSTANCE_ID,
        }
    )

    alert_payload = {
        "message": message,
        "level": level.value,
        "metadata": metadata,
    }

    tasks = []
    for channel in channels:
        alert_counter.labels(level=level.value, channel=channel.value).inc()

        if channel == AlertChannel.LOG:
            log_level = getattr(logging, level.value)
            logger.log(log_level, f"ALERT: {message}", extra=metadata)

        elif channel == AlertChannel.SLACK and _alert_config.slack_webhook_url:
            if AIOHTTP_AVAILABLE:
                tasks.append(_send_slack_alert_async(message, level, metadata))

        elif channel == AlertChannel.PAGERDUTY and _alert_config.pagerduty_routing_key:
            if AIOHTTP_AVAILABLE:
                tasks.append(_send_pagerduty_alert_async(message, level, metadata))

        elif channel == AlertChannel.EMAIL and _alert_config.email_smtp_host:
            # Email sending is typically synchronous, skip for async
            logger.info("Email alerts not supported in async mode")

        elif (
            channel == AlertChannel.SNS
            and _alert_config.sns_topic_arn
            and AWS_AVAILABLE
        ):
            tasks.append(_send_sns_alert_async(message, level, metadata))

        elif channel == AlertChannel.DATADOG and _alert_config.datadog_api_key:
            if AIOHTTP_AVAILABLE:
                tasks.append(_send_datadog_alert_async(message, level, metadata))

        elif channel == AlertChannel.OPSGENIE and _alert_config.opsgenie_api_key:
            if AIOHTTP_AVAILABLE:
                tasks.append(_send_opsgenie_alert_async(message, level, metadata))

        elif channel == AlertChannel.WEBHOOK and _alert_config.webhook_urls:
            if AIOHTTP_AVAILABLE:
                for url in _alert_config.webhook_urls:
                    if url:
                        tasks.append(_send_webhook_alert_async(url, alert_payload))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _send_slack_alert_async(
    message: str, level: AlertLevel, metadata: Dict[str, Any]
):
    """Send alert to Slack."""
    if not AIOHTTP_AVAILABLE:
        return

    color_map = {
        AlertLevel.DEBUG: "#808080",
        AlertLevel.INFO: "#36a64f",
        AlertLevel.WARNING: "#ff9900",
        AlertLevel.ERROR: "#ff0000",
        AlertLevel.CRITICAL: "#8B0000",
        AlertLevel.EMERGENCY: "#000000",
    }

    payload = {
        "attachments": [
            {
                "color": color_map.get(level, "#808080"),
                "title": f"{level.value} Alert",
                "text": message,
                "fields": [
                    {"title": k, "value": str(v), "short": True}
                    for k, v in metadata.items()
                ],
                "footer": SERVICE_NAME,
                "ts": int(time.time()),
            }
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _alert_config.slack_webhook_url, json=payload
            ) as response:
                if response.status != 200:
                    logger.error(
                        f"Failed to send Slack alert: {response.status} {await response.text()}"
                    )
    except Exception as e:
        logger.error(f"Error sending Slack alert: {e}")


async def _send_pagerduty_alert_async(
    message: str, level: AlertLevel, metadata: Dict[str, Any]
):
    """Send alert to PagerDuty."""
    if not AIOHTTP_AVAILABLE:
        return

    severity_map = {
        AlertLevel.DEBUG: "info",
        AlertLevel.INFO: "info",
        AlertLevel.WARNING: "warning",
        AlertLevel.ERROR: "error",
        AlertLevel.CRITICAL: "critical",
        AlertLevel.EMERGENCY: "critical",
    }

    payload = {
        "routing_key": _alert_config.pagerduty_routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": message,
            "severity": severity_map.get(level, "info"),
            "source": HOSTNAME,
            "custom_details": metadata,
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://events.pagerduty.com/v2/enqueue", json=payload
            ) as response:
                if response.status != 202:
                    logger.error(
                        f"Failed to send PagerDuty alert: {response.status} {await response.text()}"
                    )
    except Exception as e:
        logger.error(f"Error sending PagerDuty alert: {e}")


async def _send_sns_alert_async(
    message: str, level: AlertLevel, metadata: Dict[str, Any]
):
    """Send alert via AWS SNS."""
    if not AWS_AVAILABLE:
        return

    try:
        # SNS client operations are synchronous, run in executor
        loop = asyncio.get_event_loop()
        sns = boto3.client("sns")

        subject = f"{level.value} Alert from {SERVICE_NAME}"
        full_message = f"{message}\n\nMetadata:\n{json.dumps(metadata, indent=2)}"

        await loop.run_in_executor(
            None,
            lambda: sns.publish(
                TopicArn=_alert_config.sns_topic_arn,
                Subject=subject,
                Message=full_message,
            ),
        )
    except Exception as e:
        logger.error(f"Error sending SNS alert: {e}")


async def _send_datadog_alert_async(
    message: str, level: AlertLevel, metadata: Dict[str, Any]
):
    """Send alert to Datadog."""
    if not AIOHTTP_AVAILABLE:
        return

    alert_type_map = {
        AlertLevel.DEBUG: "info",
        AlertLevel.INFO: "info",
        AlertLevel.WARNING: "warning",
        AlertLevel.ERROR: "error",
        AlertLevel.CRITICAL: "error",
        AlertLevel.EMERGENCY: "error",
    }

    payload = {
        "title": f"{level.value} Alert",
        "text": message,
        "alert_type": alert_type_map.get(level, "info"),
        "tags": [f"{k}:{v}" for k, v in metadata.items()],
    }

    headers = {"DD-API-KEY": _alert_config.datadog_api_key}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.datadoghq.com/api/v1/events",
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 202:
                    logger.error(
                        f"Failed to send Datadog alert: {response.status} {await response.text()}"
                    )
    except Exception as e:
        logger.error(f"Error sending Datadog alert: {e}")


async def _send_opsgenie_alert_async(
    message: str, level: AlertLevel, metadata: Dict[str, Any]
):
    """Send alert to Opsgenie."""
    if not AIOHTTP_AVAILABLE:
        return

    priority_map = {
        AlertLevel.DEBUG: "P5",
        AlertLevel.INFO: "P4",
        AlertLevel.WARNING: "P3",
        AlertLevel.ERROR: "P2",
        AlertLevel.CRITICAL: "P1",
        AlertLevel.EMERGENCY: "P1",
    }

    payload = {
        "message": message,
        "priority": priority_map.get(level, "P3"),
        "details": metadata,
    }

    headers = {"Authorization": f"GenieKey {_alert_config.opsgenie_api_key}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.opsgenie.com/v2/alerts",
                json=payload,
                headers=headers,
            ) as response:
                if response.status != 202:
                    logger.error(
                        f"Failed to send Opsgenie alert: {response.status} {await response.text()}"
                    )
    except Exception as e:
        logger.error(f"Error sending Opsgenie alert: {e}")


async def _send_webhook_alert_async(url: str, payload: Dict[str, Any]):
    """Send alert to a webhook."""
    if not AIOHTTP_AVAILABLE:
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as response:
                if response.status not in (200, 201, 202, 204):
                    logger.error(
                        f"Failed to send webhook alert to {url}: {response.status} {await response.text()}"
                    )
    except Exception as e:
        logger.error(f"Error sending webhook alert to {url}: {e}")


def scrub_secrets(data: Any) -> Any:
    """
    Scrub sensitive information from data before logging or transmission.

    Args:
        data: Data to scrub (can be string, dict, list, etc.)

    Returns:
        Scrubbed data with sensitive information masked
    """
    # Patterns for common secrets
    secret_patterns = [
        (r"password['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", "password"),
        (r"api[_-]?key['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", "api_key"),
        (r"secret['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", "secret"),
        (r"token['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", "token"),
        (r"authorization['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", "authorization"),
        (r"bearer\s+([a-zA-Z0-9\-._~+/]+=*)", "bearer_token"),
        (r"['\"]?private[_-]?key['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", "private_key"),
        (
            r"aws[_-]?secret[_-]?access[_-]?key['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)",
            "aws_secret",
        ),
    ]

    if isinstance(data, dict):
        scrubbed = {}
        for key, value in data.items():
            # Check if key itself indicates sensitive data
            if any(
                sensitive in key.lower()
                for sensitive in ["password", "secret", "token", "key", "auth"]
            ):
                scrubbed[key] = "***REDACTED***"
            else:
                scrubbed[key] = scrub_secrets(value)
        return scrubbed

    elif isinstance(data, list):
        return [scrub_secrets(item) for item in data]

    elif isinstance(data, str):
        scrubbed_str = data
        for pattern, name in secret_patterns:
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
    open_breakers = [
        name for name, cb in _circuit_breakers.items() if cb.state == "open"
    ]
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
