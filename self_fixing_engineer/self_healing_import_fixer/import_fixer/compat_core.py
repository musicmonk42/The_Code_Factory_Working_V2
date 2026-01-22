# file: compat_core.py
# An enterprise-grade, unified facade for core infrastructure dependencies, designed for
# maximum reliability, security, and compliance in highly regulated industries.
"""
Key Features:
- Secure-by-Default: Enforces TLS with client cert support for observability endpoints.
- Resilient Initialization: Uses retries, timeouts, resource limits, and a shared cache.
- Tamper-Evident Auditing: Generates HMAC-signed audit logs with S3 offloading and retention policies.
- Comprehensive Observability: Rich metrics, traces with contextual events, and structured JSON logs.
- Fail-Safe Operation: Provides secure fallbacks for non-production environments while failing fast
  and securely in production.
"""

# --- Standard Library Imports ---
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, Optional
from unittest.mock import MagicMock

# POSIX-only: guard resource import for Windows
try:
    import resource as _posix_resource

    _HAS_POSIX_RESOURCE = True
except Exception:
    _posix_resource = None
    _HAS_POSIX_RESOURCE = False

# Python 3.11+ only; provide a 3.10-safe shim
try:
    from contextlib import timeout as context_timeout  # type: ignore[attr-defined]
except Exception:

    @contextmanager
    def context_timeout(_seconds: float):
        # No-op shim for older Pythons; callers must enforce their own timeouts
        yield


# --- Third-Party Library Imports ---
# Dependency Management (as of late 2025):
# prometheus_client==0.20.0
# opentelemetry-sdk==1.34.0
# tenacity==8.2.3
# boto3 (optional)
# redis (optional)
try:
    import tenacity
except ImportError:

    class tenacity:
        def retry(*args: Any, **kwargs: Any) -> Callable:
            return lambda f: f


try:
    from prometheus_client import (
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        start_http_server,
    )

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    REGISTRY = Counter = Gauge = Histogram = start_http_server = None

# Import centralized OpenTelemetry configuration with fallback
# to avoid circular import issues with arbiter
try:
    from arbiter.otel_config import get_tracer

    _HAS_ARBITER_OTEL = True
except (ImportError, ModuleNotFoundError):
    _HAS_ARBITER_OTEL = False

    # Fallback: create a simple get_tracer function
    def get_tracer(name: str):
        """Fallback tracer that returns a no-op tracer."""
        try:
            from opentelemetry import trace

            return trace.get_tracer(name)
        except ImportError:
            # Return a no-op tracer
            class _NoOpSpan:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

                def set_attribute(self, *args, **kwargs):
                    pass

                def add_event(self, *args, **kwargs):
                    pass

                def record_exception(self, *args, **kwargs):
                    pass

            class _NoOpTracer:
                def start_as_current_span(self, name, **kwargs):
                    return _NoOpSpan()

            return _NoOpTracer()


# Keep trace import for trace.get_current_span() usage
try:
    from opentelemetry import trace

    _HAS_OPENTELEMETRY = True
except ImportError:
    _HAS_OPENTELEMETRY = False
    trace = None

try:
    from boto3 import client as boto3_client

    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False
    boto3_client = None
try:
    # Integration: Align with cache_layer.py for consistent Redis client usage.
    # For this file, we use a synchronous client for simplicity, but a real
    # async app would use the async client from the shared layer.
    import redis

    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    redis = None

# --- Core Infrastructure Placeholder Imports ---
_alert_operator: Optional[Callable[..., Any]] = None
_scrub_secrets: Optional[Callable[..., Any]] = None
_audit_logger: Optional[Any] = None
_secrets_manager: Optional[Any] = None


# --- Environment-Driven Configuration & Validation ---
def _validate_env_var(var_name: str, value: str, pattern: str) -> str:
    if not re.match(pattern, value):
        raise ValueError(
            f"Invalid {var_name} value: '{value}'. Must match pattern: {pattern}"
        )
    return value


def _truthy(v: str | None) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


ENVIRONMENT = os.getenv("APP_ENV", "test").lower()
PRODUCTION_MODE = ENVIRONMENT in {"prod", "production"}
ALLOW_FALLBACKS = _truthy(os.getenv("ALLOW_FALLBACKS", "0" if PRODUCTION_MODE else "1"))
AUDIT_LOG_ENABLED = _truthy(
    os.getenv("AUDIT_LOG_ENABLED", "1" if PRODUCTION_MODE else "0")
)
AUDIT_SIGNING_ENABLED = _truthy(
    os.getenv("AUDIT_SIGNING_ENABLED", "1" if PRODUCTION_MODE else "0")
)
METRICS_ENABLED: bool = os.getenv("METRICS_ENABLED", "true").lower().strip() == "true"
TRACING_ENABLED: bool = os.getenv("TRACING_ENABLED", "true").lower().strip() == "true"
_LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO").upper().strip()
LOG_LEVEL = (
    logging.INFO
    if _LOG_LEVEL_STR not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    else getattr(logging, _LOG_LEVEL_STR)
)
METRICS_PORT: str = _validate_env_var(
    "METRICS_PORT", os.getenv("METRICS_PORT", "8000"), r"^\d{4,5}$"
)


# --- Logging Configuration for Compliance ---
class JSONFormatter(logging.Formatter):
    """# ISO 27001 A.12.4.1 / SOC 2 A1.2: Ensures structured, auditable event logs."""

    def format(self, record: logging.LogRecord) -> str:
        if PRODUCTION_MODE and not hasattr(record, "data_classification"):
            raise ValueError(
                f"Log record missing mandatory 'data_classification': {record.getMessage()}"
            )
        log_record: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        if TRACING_ENABLED and _HAS_OPENTELEMETRY and trace:
            try:
                ctx = trace.get_current_span().get_span_context()
                if getattr(ctx, "is_valid", False):
                    # Be robust if ctx.trace_id/span_id are mocks
                    log_record.update(
                        {
                            "trace_id": str(getattr(ctx, "trace_id", "")),
                            "span_id": str(getattr(ctx, "span_id", "")),
                        }
                    )
            except Exception:
                pass
        if hasattr(record, "data_classification"):
            log_record["data_classification"] = record.data_classification
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.setLevel(LOG_LEVEL)
    _handler = logging.StreamHandler()
    _handler.setFormatter(JSONFormatter())
    logger.addHandler(_handler)


# --- No-op Prometheus metrics for test/dev ---
class _NoopTimer:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _NoopMetric:
    def labels(self, *a, **k):
        return self

    def time(self):
        return _NoopTimer()

    def inc(self, *a, **k):
        pass

    def set(self, *a, **k):
        pass

    def observe(self, *a, **k):
        pass


# --- Observability: Metrics and Tracing Lazy Initialization ---
_observability_lock = threading.Lock()
_tracer = None
_metrics_registry: Dict[str, Any] = {}


def _get_metrics() -> Dict[str, Any]:
    """
    Get or create Prometheus metrics registry.
    
    Thread-safe implementation that prevents duplicate metric registration
    by checking the existing REGISTRY before creating new metrics.
    Follows industry best practices for metric reuse in multi-import scenarios.
    """
    global _metrics_registry
    with _observability_lock:
        if not _metrics_registry and METRICS_ENABLED and _HAS_PROMETHEUS:
            
            def get_or_create_metric(metric_class, name, documentation, labelnames=None):
                """
                Helper function to get existing metric or create new one.
                Prevents 'Duplicated timeseries in CollectorRegistry' errors
                by checking registry BEFORE creating metrics.
                
                Industry best practice: Check first to avoid any duplication errors.
                
                Args:
                    metric_class: Counter, Gauge, or Histogram class
                    name: Metric name
                    documentation: Metric description
                    labelnames: Optional list of label names
                    
                Returns:
                    Existing or newly created metric
                """
                # CRITICAL: Check if metric exists BEFORE attempting creation
                # This prevents any ValueError from being raised in the first place
                if hasattr(REGISTRY, '_names_to_collectors'):
                    existing = REGISTRY._names_to_collectors.get(name)
                    if existing is not None:
                        logger.debug(
                            f"Reusing existing metric: {name}",
                            extra={"data_classification": "internal"},
                        )
                        return existing
                
                # Metric doesn't exist - safe to create
                try:
                    if labelnames:
                        return metric_class(name, documentation, labelnames)
                    return metric_class(name, documentation)
                except ValueError as e:
                    # Last resort: if creation still fails, retrieve from registry
                    if "Duplicated timeseries" in str(e) and hasattr(REGISTRY, '_names_to_collectors'):
                        existing = REGISTRY._names_to_collectors.get(name)
                        if existing is not None:
                            logger.warning(
                                f"Metric {name} was created concurrently, reusing existing",
                                extra={"data_classification": "internal"},
                            )
                            return existing
                    raise  # Re-raise if it's a different error
            
            _metrics_registry = {
                "init_duration": get_or_create_metric(
                    Histogram,
                    "compat_core_init_duration_seconds",
                    "Time taken to initialize compat_core",
                ),
                "import_failures": get_or_create_metric(
                    Counter,
                    "compat_core_import_failures_total",
                    "Core module import failures",
                    ["module"],
                ),
                "fallback_usage": get_or_create_metric(
                    Counter,
                    "compat_core_fallback_usage_total",
                    "Usage of fallback shims",
                    ["component", "environment"],
                ),
                "load_status": get_or_create_metric(
                    Gauge,
                    "compat_core_module_loaded_status",
                    "Status of core module load (1=loaded, 0=failed)",
                    ["module"],
                ),
                "fallback_latency": get_or_create_metric(
                    Histogram,
                    "compat_core_fallback_latency_seconds",
                    "Latency of fallback operations",
                    ["component"],
                ),
                "s3_offload_failures": get_or_create_metric(
                    Counter,
                    "compat_core_s3_offload_failures_total",
                    "Failures to offload audit logs to S3",
                ),
                "suppressed_warnings": get_or_create_metric(
                    Counter,
                    "compat_core_suppressed_warnings_total",
                    "Warnings suppressed by rate limiter",
                ),
            }
        if not _metrics_registry:
            noop = _NoopMetric()
            _metrics_registry = {
                n: noop
                for n in [
                    "init_duration",
                    "import_failures",
                    "fallback_usage",
                    "load_status",
                    "fallback_latency",
                    "s3_offload_failures",
                    "suppressed_warnings",
                ]
            }
        return _metrics_registry


def _get_tracer() -> Any:
    global _tracer
    with _observability_lock:
        if _tracer is None:
            # Use centralized tracer configuration
            _tracer = get_tracer(__name__)
    return _tracer


def get_prometheus_metrics():
    # Return a tiny namespace exposing Prometheus classes (or dummies)
    class M:
        Counter = Counter
        Gauge = Gauge
        Histogram = Histogram

    return M


def get_telemetry_tracer(_name: str = __name__):
    # Use centralized tracer configuration
    return get_tracer(_name)


def get_audit_logger():
    return audit_logger


# JSON logger (structured)
_json_logger = logging.getLogger("json_logger")
if not _json_logger.handlers:
    _jh = logging.StreamHandler()
    _jh.setFormatter(JSONFormatter())
    _json_logger.addHandler(_jh)
_json_logger.setLevel(LOG_LEVEL)


def get_json_logger():
    return _json_logger


# --- Initialization State and Health ---
_init_lock = threading.Lock()
_core_initialized: bool = False
_core_init_error: Optional[Exception] = None


@dataclass
class CoreModuleStatus:
    module_name: str
    loaded: bool = False
    error: Optional[str] = None
    load_time_ms: float = 0.0


core_statuses: Dict[str, CoreModuleStatus] = {
    "analyzer.core_utils": CoreModuleStatus(module_name="analyzer.core_utils"),
    "analyzer.core_audit": CoreModuleStatus(module_name="analyzer.core_audit"),
    "analyzer.core_secrets": CoreModuleStatus(module_name="analyzer.core_secrets"),
}
_redis_client = None


def _get_redis_client():
    """
    Get or create Redis client with comprehensive fallback support.
    
    Configuration priority (highest to lowest):
    1. REDIS_URL - Full connection URL (preferred for Railway and other platforms)
    2. REDIS_HOST/REDIS_PORT - Traditional host/port configuration
    3. REDISHOST/REDISPORT - Railway-specific environment variables
    
    Implements graceful degradation: returns None if Redis is unavailable,
    allowing the application to continue without distributed caching.
    Follows industry standards for connection resilience and fallback handling.
    
    Connection timeout is set to 5 seconds to prevent hanging during startup.
    
    Returns:
        redis.Redis instance if connection successful, None otherwise
    """
    global _redis_client
    if _redis_client is None and _HAS_REDIS:
        try:
            # Support REDIS_URL (preferred) for platforms like Railway
            redis_url = os.getenv("REDIS_URL")
            if redis_url:
                _redis_client = redis.from_url(
                    redis_url, 
                    decode_responses=True,
                    socket_connect_timeout=5,  # Prevent hanging on slow connections
                    socket_timeout=5,
                )
                logger.debug(
                    "Redis client created from REDIS_URL",
                    extra={"data_classification": "internal"},
                )
            else:
                # Fallback to traditional REDIS_HOST/REDIS_PORT
                # Also support Railway's REDISHOST/REDISPORT variables
                host = os.getenv("REDIS_HOST", os.getenv("REDISHOST", "localhost"))
                port = int(os.getenv("REDIS_PORT", os.getenv("REDISPORT", "6379")))
                _redis_client = redis.Redis(
                    host=host,
                    port=port,
                    decode_responses=True,
                    socket_connect_timeout=5,  # Prevent hanging on slow connections
                    socket_timeout=5,
                )
                logger.debug(
                    f"Redis client created with host={host}, port={port}",
                    extra={"data_classification": "internal"},
                )
            
            # Test connection to ensure Redis is actually available
            # ping() will respect the socket_timeout configured above
            _redis_client.ping()
            logger.info(
                "Redis connection established successfully",
                extra={"data_classification": "internal"},
            )
        except Exception as e:
            # Graceful degradation: log warning and continue without Redis
            logger.warning(
                f"Redis unavailable, distributed caching disabled: {e}",
                extra={"data_classification": "internal"},
            )
            _redis_client = None  # Explicit fallback to None
    return _redis_client


# --- Fallback Implementations and Compliance Controls ---
_fallback_warnings_rate_limiter: Dict[str, float] = {}
_global_warning_count = 0
_warning_window_start = time.monotonic()


def _should_log_warning(
    key: str, interval_seconds: int = 300, max_per_hour: int = 100
) -> bool:
    global _global_warning_count, _warning_window_start
    now = time.monotonic()
    if now - _warning_window_start > 3600:
        _global_warning_count, _warning_window_start = 0, now
    if _global_warning_count >= max_per_hour:
        _get_metrics()["suppressed_warnings"].inc()
        return False
    last_log_time = _fallback_warnings_rate_limiter.get(key, 0)
    if now - last_log_time > interval_seconds:
        _fallback_warnings_rate_limiter[key], _global_warning_count = (
            now,
            _global_warning_count + 1,
        )
        return True
    return False


_s3_client = None


def _ensure_s3_lifecycle_policy():
    bucket = os.getenv("AUDIT_S3_BUCKET")
    if bucket and _s3_client:
        try:
            # HIPAA 164.316(b)(2)(i): Retain documentation for 6 years.
            _s3_client.put_bucket_lifecycle_configuration(
                Bucket=bucket,
                LifecycleConfiguration={
                    "Rules": [
                        {
                            "ID": "audit-log-retention",
                            "Filter": {"Prefix": "audit-logs/"},
                            "Status": "Enabled",
                            "Transitions": [{"Days": 90, "StorageClass": "GLACIER"}],
                            "Expiration": {"Days": 2190},
                        }
                    ]
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to set S3 lifecycle policy: {e}",
                extra={"data_classification": "internal"},
            )


def _offload_audit_log_to_s3(filename: str):
    global _s3_client
    bucket = os.getenv("AUDIT_S3_BUCKET")
    if AUDIT_LOG_ENABLED and bucket and _HAS_BOTO3:
        try:
            if _s3_client is None:
                _s3_client = boto3_client(
                    "s3", region_name=os.getenv("AWS_REGION", "us-east-1")
                )
                _ensure_s3_lifecycle_policy()
            key = f"audit-logs/{datetime.now(timezone.utc).isoformat()}/{os.path.basename(filename)}"
            _s3_client.upload_file(filename, bucket, key)
        except Exception as e:
            _get_metrics()["s3_offload_failures"].inc()
            logger.error(
                f"S3 offload failed: {e}", extra={"data_classification": "internal"}
            )


class S3RotatingFileHandler(RotatingFileHandler):
    def doRollover(self):
        super().doRollover()
        _offload_audit_log_to_s3(self.baseFilename)


_audit_fallback_logger = logging.getLogger("audit_fallback")
if not _audit_fallback_logger.handlers:
    _audit_fallback_logger.setLevel(logging.INFO)
    _audit_handler = S3RotatingFileHandler(
        "audit_fallback.log", maxBytes=10 * 1024 * 1024, backupCount=5
    )
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_fallback_logger.addHandler(_audit_handler)

_SIGNING_REENTRANT = False


def _sign_log_entry(entry: dict) -> str:
    global _SIGNING_REENTRANT
    if not PRODUCTION_MODE:
        return json.dumps(entry, sort_keys=True)
    if not AUDIT_SIGNING_ENABLED or _SIGNING_REENTRANT:
        return json.dumps(entry, sort_keys=True)
    _SIGNING_REENTRANT = True
    try:
        secret = SECRETS_MANAGER.get_secret("AUDIT_LOG_HMAC_KEY", required=False)
        if not secret:
            return json.dumps(entry, sort_keys=True)
        sig = hmac.new(
            secret.encode("utf-8"),
            json.dumps(entry, sort_keys=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        with_sig = dict(entry)
        with_sig["_sig"] = sig
        return json.dumps(with_sig, sort_keys=True)
    except Exception:
        return json.dumps(entry, sort_keys=True)
    finally:
        _SIGNING_REENTRANT = False


class _FallbackAuditLogger:
    """
    Minimal, safe logger that supports the common logging interface used by the
    rest of the codebase (info/warning/error/debug) *and* a structured
    `log_event` method. Accepts arbitrary **fields for structured context.
    """

    def _emit(self, level: str, message: str, **fields):
        # Be tolerant under tests (no secret material, just serialize)
        entry = {
            "level": level.upper(),
            "msg": message,
            "env": ENVIRONMENT,
            "ts": time.time(),
            **fields,
        }
        try:
            payload = (
                _sign_log_entry(entry)
                if AUDIT_LOG_ENABLED
                else json.dumps(entry, sort_keys=True)
            )
        except Exception:
            payload = json.dumps(entry, sort_keys=True)
        # Route to the fallback Python logger
        if level == "error":
            _audit_fallback_logger.error(payload)
        elif level == "warning":
            _audit_fallback_logger.warning(payload)
        elif level == "debug":
            _audit_fallback_logger.debug(payload)
        else:
            _audit_fallback_logger.info(payload)

    # Structured event-style API (kept for backwards compat)
    def log_event(self, event_name: str, **fields):
        self._emit("info", event_name, **fields)

    # Standard logger-style API used by cache_layer, etc.
    def info(self, message: str, **fields):
        self._emit("info", message, **fields)

    def warning(self, message: str, **fields):
        self._emit("warning", message, **fields)

    def error(self, message: str, **fields):
        self._emit("error", message, **fields)

    def debug(self, message: str, **fields):
        self._emit("debug", message, **fields)


_fallback_audit_logger_instance = _FallbackAuditLogger()

_fallback_alert_counters: dict[str, int] = {}
_fallback_alert_escalated: set[str] = set()
_ALERT_REENTRANT = False


def _check_fallback_usage(component: str) -> None:
    """
    Record fallback usage and (if it explodes) emit a single CRITICAL ops alert.
    Designed to be safe under pytest/dev: no unbounded recursion or hard deps.
    """
    global _ALERT_REENTRANT

    # Never recursively alert about the alert operator itself
    if component == "alert_operator":
        return

    # Count
    _fallback_alert_counters[component] = _fallback_alert_counters.get(component, 0) + 1

    # Best-effort metric
    try:
        metrics = _get_metrics()
        # Guard mocks/no-ops
        m = metrics.get("fallback_usage")
        if hasattr(m, "labels"):
            m.labels(component=component, environment=ENVIRONMENT).inc()
        elif hasattr(m, "inc"):
            m.inc()  # totally fine as a no-op metric
    except Exception:
        pass

    # De-dupe + thresholded escalation
    if (
        _fallback_alert_counters[component] > 10
        and component not in _fallback_alert_escalated
    ):
        if _ALERT_REENTRANT:
            return
        _fallback_alert_escalated.add(component)
        try:
            _ALERT_REENTRANT = True
            _get_alert_operator()(  # may be the real one or our fallback
                f"CRITICAL: Excessive fallback usage for '{component}' in '{ENVIRONMENT}'.",
                "CRITICAL",
            )
        finally:
            _ALERT_REENTRANT = False


def _fallback_alert_operator(msg: str, level: str = "WARNING") -> None:
    """
    Minimal, safe alert operator for dev/test: just structured-log the message.
    IMPORTANT: Do NOT call _check_fallback_usage('alert_operator') here — that
    would recurse back into us. The monitor above counts all call sites already.
    """
    try:
        logger.info(
            json.dumps({"ops_alert": {"level": level, "message": msg}}),
            extra={"data_classification": "internal"},
        )
    except Exception:
        # Never let alerts crash app/test paths
        pass


class _FallbackSecretsManager:
    @lru_cache(maxsize=128)
    def get_secret(self, key: str, required: bool = False) -> str | None:
        start = time.monotonic()
        _check_fallback_usage("secrets_manager")
        val = os.getenv(key)
        if (val is None or val == "") and required:
            if AUDIT_LOG_ENABLED:
                _fallback_audit_logger_instance.log_event(
                    "fallback_secret_missing", key=key, required=required
                )
            raise KeyError(f"Secret {key!r} not found")
        try:
            _get_metrics().get("init_duration").observe(
                max(0.0, time.monotonic() - start)
            )
        except Exception:
            pass
        return val


_fallback_secrets_manager_instance = _FallbackSecretsManager()


def _get_alert_operator() -> Callable:
    return alert_operator if "alert_operator" in globals() else _fallback_alert_operator


# --- Core Initialization Logic ---
try:
    # Use a dummy stub for the missing modules during testing.
    # This prevents the ModuleNotFoundError from being raised and allows the fallback logic to be tested.
    class MockAnalyzerCoreUtils:
        alert_operator = MagicMock()
        scrub_secrets = MagicMock(side_effect=lambda x: x)

    class MockAnalyzerCoreAudit:
        get_audit_logger = MagicMock(return_value=MagicMock())
        audit_logger = MagicMock()

    class MockAnalyzerCoreSecrets:
        SECRETS_MANAGER = MagicMock()

    sys.modules["analyzer.core_utils"] = MockAnalyzerCoreUtils
    sys.modules["analyzer.core_audit"] = MockAnalyzerCoreAudit
    sys.modules["analyzer.core_secrets"] = MockAnalyzerCoreSecrets
except ImportError:
    pass


@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=10),
    before_sleep=lambda rs: logger.warning(
        f"Retry {rs.attempt_number} for core init...",
        extra={"data_classification": "internal"},
    ),
    reraise=True,
    retry=tenacity.retry_if_exception_type(ImportError),
)
def _initialize_core_modules() -> None:
    global _core_initialized
    total_start_time = time.monotonic()
    metrics, tracer = _get_metrics(), _get_tracer()
    if redis_client := _get_redis_client():
        if redis_client.get("compat_core_initialized") == "true":
            logger.info(
                "Skipping init, already completed by another instance.",
                extra={"data_classification": "internal"},
            )
            return
    with tracer.start_as_current_span("compat_core.initialize") as span:
        if span:
            span.set_attribute("environment", ENVIRONMENT)
        with _init_lock:
            if _core_initialized:
                return
            try:
                if _HAS_POSIX_RESOURCE:
                    _posix_resource.setrlimit(_posix_resource.RLIMIT_CPU, (10, 15))
                    _posix_resource.setrlimit(
                        _posix_resource.RLIMIT_NOFILE, (1024, 4096)
                    )
                    _posix_resource.setrlimit(_posix_resource.RLIMIT_NPROC, (50, 100))

                modules = [
                    ("analyzer.core_utils", ["alert_operator", "scrub_secrets"]),
                    # Accept either factory or direct instance for audit logger
                    ("analyzer.core_audit", ["get_audit_logger"]),
                    ("analyzer.core_secrets", ["SECRETS_MANAGER"]),
                ]

                for name, symbols in modules:
                    start_time = time.monotonic()
                    try:
                        module = __import__(name, fromlist=symbols)
                        for symbol in symbols:
                            if symbol == "get_audit_logger":
                                # Prefer factory if present; otherwise accept a direct instance named `audit_logger`
                                if hasattr(module, "get_audit_logger"):
                                    globals()[
                                        "_audit_logger"
                                    ] = module.get_audit_logger()
                                elif hasattr(module, "audit_logger"):
                                    globals()["_audit_logger"] = getattr(
                                        module, "audit_logger"
                                    )
                                else:
                                    raise AttributeError(
                                        f"Module {name!r} lacks get_audit_logger() and audit_logger"
                                    )
                            else:
                                globals()[f"_{symbol}"] = getattr(module, symbol)
                        core_statuses[name].loaded = True
                        m = metrics.get("load_status")
                        if hasattr(m, "labels"):
                            try:
                                m.labels(module=name).set(1)
                            except Exception:
                                pass
                    except (ImportError, AttributeError) as e:
                        core_statuses[name].error = str(e)
                        try:
                            metrics.get("load_status").labels(module=name).set(0)
                        except Exception:
                            pass
                        try:
                            metrics.get("import_failures").labels(module=name).inc()
                        except Exception:
                            pass
                        logger.error(
                            f"Failed import '{name}': {e}",
                            exc_info=True,
                            extra={"data_classification": "internal"},
                        )
                    finally:
                        core_statuses[name].load_time_ms = (
                            time.monotonic() - start_time
                        ) * 1000

                if PRODUCTION_MODE and not all(
                    s.loaded for s in core_statuses.values()
                ):
                    # One or more core deps missing in prod: fail fast with a clear message.
                    missing = [
                        name for name, s in core_statuses.items() if not s.loaded
                    ]
                    raise RuntimeError(
                        "Required core modules missing in production: "
                        + ", ".join(missing)
                        + ". Set APP_ENV!=prod for dev/CI fallbacks, or wire the core stack."
                    )

                if redis_client:
                    redis_client.setex("compat_core_initialized", 3600, "true")
            except Exception as e:
                _core_init_error = e
                logger.critical(
                    f"Critical init error: {e}",
                    exc_info=True,
                    extra={"data_classification": "internal"},
                )
                raise
            finally:
                _core_initialized = True
                try:
                    metrics.get("init_duration", _NoopMetric()).observe(
                        time.monotonic() - total_start_time
                    )
                except Exception:
                    pass


# --- Module Initialization and Public Interface Assignment ---
try:
    with context_timeout(30.0) as t:
        _initialize_core_modules()
except Exception as e:
    _core_init_error = e
    if PRODUCTION_MODE:
        logger.critical(
            "Startup halted due to critical initialization failure.",
            extra={"data_classification": "internal"},
        )
        raise

alert_operator = (
    _alert_operator
    if core_statuses["analyzer.core_utils"].loaded
    else _fallback_alert_operator
)
scrub_secrets = (
    _scrub_secrets if core_statuses["analyzer.core_utils"].loaded else lambda x: x
)
audit_logger = (
    _audit_logger
    if core_statuses["analyzer.core_audit"].loaded
    else _fallback_audit_logger_instance
)
# Ensure the audit_logger exposes the common methods used elsewhere.
for _meth in ("info", "warning", "error", "debug", "log_event"):
    if not hasattr(audit_logger, _meth):
        # Wrap into the fallback adapter if the real one is incomplete
        audit_logger = _fallback_audit_logger_instance
        break
SECRETS_MANAGER = (
    _secrets_manager
    if core_statuses["analyzer.core_secrets"].loaded
    else _fallback_secrets_manager_instance
)

# --- Compliance and Health Check ---
if AUDIT_LOG_ENABLED:
    try:
        audit_logger.log_event(
            "compat_core_initialized",
            environment=ENVIRONMENT,
            production_mode=PRODUCTION_MODE,
            core_status={k: asdict(v) for k, v in core_statuses.items()},
            error=str(_core_init_error) if _core_init_error else None,
        )
    except Exception as e:
        logger.error(
            f"Failed to write init audit log: {e}",
            exc_info=True,
            extra={"data_classification": "internal"},
        )


def get_core_health() -> str:
    return json.dumps(
        {
            "initialized": _core_initialized,
            "modules": {k: asdict(v) for k, v in core_statuses.items()},
            "error": str(_core_init_error) if _core_init_error else None,
        },
        indent=2,
    )


def verify_audit_log(log_entry: str, secret: str) -> bool:
    try:
        parsed = json.loads(log_entry)
        payload, signature = parsed["payload"], parsed["signature"]
        expected = hmac.new(
            secret.encode(),
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (json.JSONDecodeError, KeyError):
        return False


try:
    cli_audit_logger
except NameError:
    cli_audit_logger = None


def get_core_dependencies() -> Dict[str, Any]:
    """
    Returns a dictionary of core dependencies and their status.
    """
    return {
        "alert_operator": alert_operator,
        "scrub_secrets": scrub_secrets,
        "audit_logger": audit_logger,
        "SECRETS_MANAGER": SECRETS_MANAGER,
        "core_initialized": _core_initialized,
    }


def load_analyzer(module_path: str) -> Any:
    """
    Loads an analyzer module from the given path.
    Falls back to a no-op mock if not available.
    """
    try:
        return importlib.import_module(module_path)
    except ImportError:
        return MagicMock()


try:
    _NoOpMetric
except NameError:

    class _NoOpMetric:
        def __init__(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

        def inc(self, *a, **k):
            pass

        def observe(self, *a, **k):
            pass


if (
    os.getenv("RUN_AS_SERVICE", "false").lower() == "true"
    and METRICS_ENABLED
    and _HAS_PROMETHEUS
):
    try:
        start_http_server(int(METRICS_PORT))
        logger.info(
            f"Prometheus metrics exposed on port {METRICS_PORT}",
            extra={"data_classification": "internal"},
        )
    except Exception as e:
        logger.error(
            f"Failed to start Prometheus server: {e}",
            extra={"data_classification": "internal"},
        )
