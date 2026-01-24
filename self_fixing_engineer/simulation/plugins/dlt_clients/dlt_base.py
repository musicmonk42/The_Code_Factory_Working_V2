import asyncio
import atexit
import functools
import hashlib
import hmac
import inspect
import json
import logging
import os
import random
import re
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

# --- Logging Setup (Local to this module) ---
_base_logger = logging.getLogger("simulation.dlt.client")
_base_logger.setLevel(logging.INFO)
if not _base_logger.hasHandlers():
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s - [%(levelname)s] - [%(client_type)s] - %(message)s"
    )
    handler.setFormatter(formatter)
    _base_logger.addHandler(handler)


class DLTClientLoggerAdapter(logging.LoggerAdapter):
    """Adds a client_type context to log messages."""

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        extra.update({"client_type": self.extra.get("client_type")})
        kwargs["extra"] = extra
        return msg, kwargs


# --- Global Production Mode Flag ---
PRODUCTION_MODE = os.getenv("PRODUCTION_MODE", "false").lower() == "true"
_base_logger.info(f"DLT_BASE: PRODUCTION_MODE is set to: {PRODUCTION_MODE}")

# --- Testing Mode Flag ---
# This flag is used to prevent sys.exit() calls during test collection,
# which would cause pytest to fail with CPU timeout (exit code 152).
# When TESTING_MODE is True, missing dependencies will be stubbed instead
# of causing the process to exit.
TESTING_MODE = (
    os.getenv("TESTING", "0") == "1" 
    or os.getenv("PYTEST_CURRENT_TEST") is not None
    or os.getenv("PYTEST_COLLECTING", "0") == "1"
)


# --- Placeholder for Operator Alerting (Centralized) ---
async def alert_operator(message: str, level: str = "CRITICAL"):
    """
    Placeholder function to alert operations team.
    In a real system, this would integrate with PagerDuty, Slack, Email, etc.
    """
    _base_logger.critical(f"[OPS ALERT - {level}] {message}")


def _schedule_alert(message: str, level: str = "CRITICAL"):
    """
    Schedule an async alert; if no loop is running, log synchronously.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(alert_operator(message, level))
    except RuntimeError:
        _base_logger.critical(f"[OPS ALERT - {level}] {message}")


# --- Secrets Management (Placeholder for Production Integration) ---
class SecretsManager:
    def __init__(self):
        self.cache = {}

    def get_secret(
        self, key: str, default: Optional[str] = None, required: bool = True
    ) -> Optional[str]:
        secret_value = os.getenv(key)
        if not secret_value and required:
            msg = f"Missing required secret: {key}. Please configure your secret manager or environment variable."
            _base_logger.critical(msg)
            _schedule_alert(
                f"Critical: Missing required secret '{key}' for DLT client.",
                level="CRITICAL",
            )
            raise RuntimeError(msg)
        if not secret_value:
            return default
        self.cache[key] = secret_value
        return secret_value


SECRETS_MANAGER = SecretsManager()


# --- Strict Dependency Checks ---
# These dependencies are required for production operation but can be stubbed
# during test collection to allow pytest to discover and collect tests without
# requiring all production dependencies to be installed.

AIOHTTP_AVAILABLE = False
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    _base_logger.critical(
        "CRITICAL: Required dependency 'aiohttp' not found. Aborting startup."
    )
    _schedule_alert(
        "CRITICAL: Missing required dependency 'aiohttp'. DLT client cannot start.",
        level="CRITICAL",
    )
    if not TESTING_MODE:
        sys.exit(1)
    else:
        # Provide comprehensive stub for test collection only.
        # This stub is NOT suitable for production use.
        import types as _types
        
        class _StubClientSession:
            """
            Minimal aiohttp.ClientSession stub for test collection.
            
            Supports async context manager protocol and returns no-op
            responses for all method calls. This allows test files to
            import modules that use aiohttp without requiring the actual
            dependency during collection phase.
            """
            
            def __init__(self, *args, **kwargs):
                pass
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
            
            async def close(self):
                pass
            
            def __getattr__(self, name):
                """Return async no-op for any method call."""
                async def _async_noop(*args, **kwargs):
                    return None
                return _async_noop
        
        aiohttp = _types.ModuleType("aiohttp")
        aiohttp.ClientSession = _StubClientSession
        aiohttp.ClientError = Exception
        aiohttp.ClientResponseError = Exception
        sys.modules["aiohttp"] = aiohttp

TENACITY_AVAILABLE = False
try:
    import tenacity
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
        wait_random_exponential,
    )

    TENACITY_AVAILABLE = True
except ImportError:
    _base_logger.critical(
        "CRITICAL: Required dependency 'tenacity' not found. Aborting startup."
    )
    _schedule_alert(
        "CRITICAL: Missing required dependency 'tenacity'. DLT client cannot start.",
        level="CRITICAL",
    )
    if not TESTING_MODE:
        sys.exit(1)
    else:
        # Provide minimal tenacity stubs for test collection only.
        # The retry decorator is a passthrough that returns the original function.
        def retry(*args, **kwargs):
            """
            Stub retry decorator that passes through the decorated function unchanged.
            Handles both @retry and @retry(...) usage patterns.
            """
            def decorator(func):
                return func
            # Support @retry without parentheses (direct function decoration)
            if args and callable(args[0]) and not kwargs:
                return args[0]
            return decorator
        
        # Retry condition stubs - return None (no-op conditions)
        def retry_if_exception_type(*args, **kwargs):
            return None
        
        # Stop condition stubs
        def stop_after_attempt(*args, **kwargs):
            return None
        
        # Wait strategy stubs
        def wait_exponential(*args, **kwargs):
            return None
        
        def wait_random_exponential(*args, **kwargs):
            return None

PYDANTIC_AVAILABLE = False
try:
    from pydantic import BaseModel, Field, ValidationError, validator

    PYDANTIC_AVAILABLE = True
except ImportError:
    _base_logger.critical(
        "CRITICAL: pydantic not found. Configuration validation is critical. Aborting startup."
    )
    _schedule_alert(
        "CRITICAL: pydantic not found. DLT client cannot start without configuration validation.",
        level="CRITICAL",
    )
    if not TESTING_MODE:
        sys.exit(1)
    else:
        # Provide minimal pydantic stubs for test collection only.
        # These stubs allow class definitions to be parsed but do NOT
        # provide actual validation functionality.
        
        class BaseModel:
            """
            Minimal BaseModel stub for test collection.
            
            Accepts any keyword arguments and sets them as instance attributes.
            Does not perform validation - for test collection only.
            """
            
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
            
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
            
            def dict(self, **kwargs):
                return self.__dict__.copy()
            
            def model_dump(self, **kwargs):
                return self.__dict__.copy()
        
        class _FieldInfo:
            """Stub field descriptor for pydantic Field()."""
            
            def __init__(self, default=None, **kwargs):
                self.default = default
            
            def __repr__(self):
                return f"FieldInfo(default={self.default!r})"
        
        def Field(default=None, *args, **kwargs):
            """
            Stub Field function for test collection.
            Returns the default value to allow class attribute assignment.
            """
            if default is ...:
                return None  # Required field, return None for stub
            return default
        
        class ValidationError(Exception):
            """Stub ValidationError exception."""
            pass
        
        def validator(*fields, **kwargs):
            """
            Stub validator decorator that passes through the decorated function.
            """
            def decorator(func):
                return func
            return decorator

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    PROMETHEUS_AVAILABLE = True
    _metrics_registry = CollectorRegistry(auto_describe=True)
    _metrics_lock = threading.Lock()

    def get_or_create_metric(
        metric_type, name, documentation, labelnames=None, buckets=None
    ):
        if labelnames is None:
            labelnames = ()
        with _metrics_lock:
            try:
                existing_metric = _metrics_registry._names_to_collectors[name]
                # Check if the existing metric is of the same type
                # Use direct type comparison for better reliability
                if type(existing_metric) == metric_type:
                    return existing_metric
                else:
                    _base_logger.warning(
                        f"Metric '{name}' already registered with a different type. Reusing existing."
                    )
                    return existing_metric
            except KeyError:
                if metric_type == Histogram:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        buckets=buckets or Histogram.DEFAULT_BUCKETS,
                        registry=_metrics_registry,
                    )
                elif metric_type == Counter:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        registry=_metrics_registry,
                    )
                else:
                    metric = metric_type(
                        name,
                        documentation,
                        labelnames=labelnames,
                        registry=_metrics_registry,
                    )
                return metric

    operation_counter = get_or_create_metric(
        Counter,
        "dlt_client_operations_total",
        "Total number of DLT client operations",
        labelnames=["client_type", "operation", "status"],
    )
    operation_latency = get_or_create_metric(
        Histogram,
        "dlt_client_operation_latency_seconds",
        "Latency of DLT client operations in seconds",
        labelnames=["client_type", "operation"],
    )
    circuit_breaker_status = get_or_create_metric(
        Gauge,
        "dlt_client_circuit_breaker_status",
        "Status of DLT client circuit breaker (1=OPEN, 0=CLOSED)",
        labelnames=["client_type"],
    )
    circuit_breaker_failures = get_or_create_metric(
        Gauge,
        "dlt_client_circuit_breaker_failures",
        "Current consecutive failures for DLT client circuit breaker",
        labelnames=["client_type"],
    )
    audit_log_integrity_status = get_or_create_metric(
        Gauge,
        "dlt_audit_log_integrity_status",
        "Status of DLT audit log integrity (1=OK, 0=COMPROMISED)",
    )

except ImportError:
    PROMETHEUS_AVAILABLE = False
    _base_logger.critical(
        "CRITICAL: prometheus-client not found. Metrics collection is critical. Aborting startup."
    )
    _schedule_alert(
        "CRITICAL: prometheus-client not found. DLT client cannot start without metrics.",
        level="CRITICAL",
    )
    if not TESTING_MODE:
        sys.exit(1)
    else:
        # Provide minimal prometheus stubs for test collection only.
        # These stubs allow metric definitions to be parsed but do NOT
        # collect actual metrics.
        
        class _StubMetric:
            """Base stub metric that accepts any method calls."""
            
            def __init__(self, *args, **kwargs):
                pass
            
            def labels(self, *args, **kwargs):
                return self
            
            def inc(self, *args, **kwargs):
                pass
            
            def dec(self, *args, **kwargs):
                pass
            
            def set(self, *args, **kwargs):
                pass
            
            def observe(self, *args, **kwargs):
                pass
        
        Counter = Gauge = Histogram = _StubMetric
        CollectorRegistry = lambda *a, **kw: None
        generate_latest = lambda *a, **kw: b""
        _metrics_registry = None
        _metrics_lock = threading.Lock()
        
        def get_or_create_metric(*args, **kwargs):
            return _StubMetric()
        
        # Stub metric instances
        dlt_requests_total = _StubMetric()
        dlt_request_errors_total = _StubMetric()
        dlt_request_latency_seconds = _StubMetric()
        dlt_backoff_total = _StubMetric()
        circuit_breaker_status = _StubMetric()
        circuit_breaker_failures = _StubMetric()
        audit_log_integrity_status = _StubMetric()


# --- Conditional Imports (still checked, but won't abort unless backend is chosen) ---
FABRIC_AVAILABLE = False
WEB3_AVAILABLE = False
S3_AVAILABLE = False
GCS_AVAILABLE = False
AZURE_BLOB_AVAILABLE = False
IPFS_AVAILABLE = False
OTEL_AVAILABLE = False

try:
    import aioboto3

    S3_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "aioboto3 not found. AWS S3 off-chain storage might be limited."
    )

try:
    import boto3
    from botocore.exceptions import ClientError as BotoClientError
except ImportError:
    _base_logger.warning("boto3 not found. AWS S3 off-chain storage might be limited.")

    class BotoClientError(Exception):
        pass


try:
    from google.cloud import storage as gcs_sdk

    GCS_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "google-cloud-storage not found. GCS off-chain storage will be disabled."
    )

try:
    from azure.core.exceptions import (
        ResourceNotFoundError as AzureResourceNotFoundError,
    )
    from azure.storage.blob.aio import BlobServiceClient as AzureBlobServiceClient

    AZURE_BLOB_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "azure-storage-blob not found. Azure Blob off-chain storage will be disabled."
    )

    class AzureResourceNotFoundError(Exception):
        pass


try:
    from hfc.fabric import Client as FabricSDKClient
    from hfc.fabric.certificate import User as FabricUser
    from hfc.util.keyvaluestore import FileKeyValueStore

    FABRIC_AVAILABLE = True
except ImportError:
    _base_logger.warning(
        "hfc.fabric not found. Hyperledger Fabric DLT client will be disabled."
    )

try:
    from eth_account import Account
    from web3 import AsyncHTTPProvider, Web3
    from web3.exceptions import (
        ContractCustomError,
        ContractLogicError,
        TransactionNotFound,
    )
    from web3.middleware import geth_poa_middleware

    WEB3_AVAILABLE = True
except ImportError:
    _base_logger.warning("web3.py not found. Ethereum/EVM DLT client will be disabled.")

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode

    # Use OTLP exporter instead of deprecated Jaeger exporter
    # (Since v1.35, Jaeger supports OTLP natively. JaegerExporter support ended July 2023.)
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
    except ImportError:
        # OTLP exporter not available, try Console exporter as fallback
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter

        exporter = ConsoleSpanExporter()
        _base_logger.warning(
            "OTLP exporter not available. Using ConsoleSpanExporter for tracing."
        )

    _otel_resource = Resource.create({"service.name": "dlt-client-plugin"})
    _otel_tracer_provider = TracerProvider(resource=_otel_resource)
    _otel_tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_otel_tracer_provider)
    TRACER = trace.get_tracer(__name__)
    OTEL_AVAILABLE = True
    _base_logger.info("OpenTelemetry tracer initialized for DLT clients.")
except ImportError:
    _base_logger.warning("OpenTelemetry not found. Tracing will be disabled.")

    class Status:
        def __init__(self, code, description=None):
            self.code = code
            self.description = description

    class StatusCode:
        OK = "OK"
        ERROR = "ERROR"
        UNSET = "UNSET"

    class DummySpan:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def set_attribute(self, key, value):
            pass

        def record_exception(self, exception):
            pass

        def set_status(self, status):
            pass

    class DummyTracer:
        def start_as_current_span(self, name, **kwargs):
            return DummySpan()

        def get_current_span(self):
            return DummySpan()

    TRACER = DummyTracer()


# --- Configuration Schema ---
class BaseDLTConfig(BaseModel):
    """Base configuration schema for DLT clients."""

    default_timeout_seconds: int = Field(30, ge=1)
    retry_policy: Dict[str, Any] = Field(
        default_factory=lambda: {"retries": 5, "delay": 1, "backoff": 2, "jitter": True}
    )
    secret_scrub_patterns: Optional[List[str]] = None
    circuit_breaker_threshold: int = Field(5, ge=1)
    circuit_breaker_reset_timeout: float = Field(60.0, ge=1.0)


class BaseOffChainConfig(BaseModel):
    """Base configuration schema for off-chain clients."""

    default_timeout_seconds: int = Field(30, ge=1)
    retry_policy: Dict[str, Any] = Field(
        default_factory=lambda: {"retries": 5, "delay": 1, "backoff": 2, "jitter": True}
    )
    circuit_breaker_threshold: int = Field(5, ge=1)
    circuit_breaker_reset_timeout: float = Field(60.0, ge=1.0)


# --- Circuit Breaker (with Operator Alerting) ---
class CircuitBreaker:
    """Simple circuit breaker to prevent repeated failures."""

    def __init__(
        self, client_type: str, failure_threshold: int = 5, reset_timeout: float = 60
    ):
        self.client_type = client_type
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time = None
        self.state = "CLOSED"
        if PROMETHEUS_AVAILABLE:
            circuit_breaker_status.labels(client_type=self.client_type).set(0)
            circuit_breaker_failures.labels(client_type=self.client_type).set(0)

    async def execute(self, operation: Callable, *args, **kwargs) -> Any:
        if self.state == "OPEN":
            if (
                self.last_failure_time
                and (time.time() - self.last_failure_time) > self.reset_timeout
            ):
                self.state = "HALF_OPEN"
                _base_logger.warning(
                    f"Circuit breaker for {self.client_type} is HALF_OPEN. Attempting one request.",
                    extra={"client_type": self.client_type},
                )
            else:
                raise DLTClientCircuitBreakerError(
                    f"Circuit breaker is OPEN for {self.client_type}", self.client_type
                )

        try:
            # Support both sync and async operations
            result = operation(*args, **kwargs)
            if asyncio.iscoroutine(result) or inspect.isawaitable(result):
                result = await result

            if self.failures > 0:
                self.failures = 0
                self.state = "CLOSED"
                _base_logger.info(
                    f"Circuit breaker for {self.client_type} is CLOSED. Failures reset.",
                    extra={"client_type": self.client_type},
                )
                if PROMETHEUS_AVAILABLE:
                    circuit_breaker_status.labels(client_type=self.client_type).set(0)
                    circuit_breaker_failures.labels(client_type=self.client_type).set(0)
            return result
        except Exception:
            self.failures += 1
            self.last_failure_time = time.time()
            if PROMETHEUS_AVAILABLE:
                circuit_breaker_failures.labels(client_type=self.client_type).set(
                    self.failures
                )

            if self.failures >= self.failure_threshold:
                self.state = "OPEN"
                _base_logger.critical(
                    f"Circuit breaker for {self.client_type} TRIPPED to OPEN after {self.failures} failures. Operations will be blocked.",
                    extra={"client_type": self.client_type},
                )
                _schedule_alert(
                    f"CRITICAL: Circuit breaker for DLT client '{self.client_type}' tripped to OPEN. Operations blocked.",
                    level="CRITICAL",
                )
                if PROMETHEUS_AVAILABLE:
                    circuit_breaker_status.labels(client_type=self.client_type).set(1)
            raise


# --- Custom Exception Hierarchy (with Operator Escalation) ---
class DLTClientError(Exception):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.client_type = client_type
        self.original_exception = original_exception
        self.details = details or {}
        self.correlation_id = correlation_id

        scrubbed_details = scrub_secrets(details)

        _base_logger.error(
            f"DLTClientError in {client_type}: {message}",
            exc_info=original_exception,
            extra={
                "client_type": client_type,
                "details": scrubbed_details,
                "correlation_id": correlation_id,
            },
        )
        if PROMETHEUS_AVAILABLE:
            operation_counter.labels(
                client_type=client_type, operation="general", status="error"
            ).inc()

        _schedule_alert(
            f"DLT Client Error ({self.client_type}): {message}. Details: {scrubbed_details}",
            level="ERROR",
        )


class DLTClientConfigurationError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"CRITICAL: DLT Client Configuration Error ({self.client_type}): {message}",
            level="CRITICAL",
        )


class DLTClientConnectivityError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"ERROR: DLT Client Connectivity Error ({self.client_type}): {message}",
            level="ERROR",
        )


class DLTClientAuthError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"CRITICAL: DLT Client Authentication Error ({self.client_type}): {message}",
            level="CRITICAL",
        )


class DLTClientTransactionError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"ERROR: DLT Client Transaction Error ({self.client_type}): {message}",
            level="ERROR",
        )


class DLTClientQueryError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"WARNING: DLT Client Query Error ({self.client_type}): {message}",
            level="WARNING",
        )


class DLTClientResourceError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"ERROR: DLT Client Resource Error ({self.client_type}): {message}",
            level="ERROR",
        )


class DLTClientTimeoutError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"WARNING: DLT Client Timeout Error ({self.client_type}): {message}",
            level="WARNING",
        )


class DLTClientValidationError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"CRITICAL: DLT Client Validation Error ({self.client_type}): {message}",
            level="CRITICAL",
        )


class DLTClientCircuitBreakerError(DLTClientError):
    def __init__(
        self,
        message: str,
        client_type: str,
        original_exception: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        _schedule_alert(
            f"WARNING: DLT Client Circuit Breaker Open ({self.client_type}): {message}",
            level="WARNING",
        )


# --- Async Retry Decorator ---
def async_retry(
    catch_exceptions: Optional[
        Union[Type[Exception], Tuple[Type[Exception], ...]]
    ] = None,
):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(self, *args, **kwargs):
            client_type = getattr(self, "client_type", "N/A")
            config = getattr(self, "config", {})
            retry_policy = config.get(
                "retry_policy", {"retries": 5, "delay": 1, "backoff": 2, "jitter": True}
            )
            retries = retry_policy.get("retries", 5)
            delay = retry_policy.get("delay", 1)
            backoff = retry_policy.get("backoff", 2)
            jitter = retry_policy.get("jitter", True)

            last_exc = None
            correlation_id = kwargs.get("correlation_id", None)

            for i in range(retries):
                try:
                    start_time = time.time()
                    result = await fn(self, *args, **kwargs)
                    if PROMETHEUS_AVAILABLE:
                        operation_latency.labels(
                            client_type=client_type, operation=fn.__name__
                        ).observe(time.time() - start_time)
                        operation_counter.labels(
                            client_type=client_type,
                            operation=fn.__name__,
                            status="success",
                        ).inc()
                    return result
                except Exception as exc:
                    if catch_exceptions is None or isinstance(exc, catch_exceptions):
                        last_exc = exc
                        _base_logger.warning(
                            f"[DLT Retry] Attempt {i+1}/{retries} for {fn.__name__} failed: {type(exc).__name__}: {exc}",
                            extra={
                                "client_type": client_type,
                                "correlation_id": correlation_id,
                            },
                        )
                        if i < retries - 1:
                            wait_time = delay * (backoff**i)
                            if jitter:
                                wait_time += random.uniform(0, 0.1 * wait_time)
                            await asyncio.sleep(wait_time)
                        else:
                            if PROMETHEUS_AVAILABLE:
                                operation_counter.labels(
                                    client_type=client_type,
                                    operation=fn.__name__,
                                    status="error",
                                ).inc()
                            _base_logger.error(
                                f"[DLT Retry] Operation {fn.__name__} failed after {retries} attempts: {last_exc}",
                                extra={
                                    "client_type": client_type,
                                    "correlation_id": correlation_id,
                                },
                            )
                            raise last_exc
                    else:
                        if PROMETHEUS_AVAILABLE:
                            operation_counter.labels(
                                client_type=client_type,
                                operation=fn.__name__,
                                status="error",
                            ).inc()
                        raise
            if last_exc:
                raise last_exc
            else:
                raise RuntimeError(
                    f"Operation {fn.__name__} failed without capturing an exception."
                )

        return wrapper

    return decorator


# --- Secret Scrubbing Utility ---
_global_secret_patterns = [
    r'(?:[Aa]pi)?[_]?([Kk]ey|[Ss]ecret|[Tt]oken|[Pp]ass(?:word)?)[:=]?\s*[\'"]?(\S{8,128})[\'"]?',
    r'([Ss]hared[Kk]ey)[:=][\'"]?([a-zA-Z0-9\/+=]{40,})[\'"]?',
    r"AKIA[0-9A-Z]{16}",
    r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?([A-Za-z0-9-_.+/=])*",
    r"(pk|sk)_[a-zA-Z0-9_]{16,64}",
    r"Bearer\s+[A-Za-z0-9-._~+/]{30,}",
    r"\b(?:[0-9]{4}[ -]?){3}[0-9]{4}\b",
    r"(\d{3}[-\s]?\d{2}[-\s]?\d{4})",
    r"\bemail=([^&\s]+)\b",
    r"user=([^&\s]+)\b",
    r"client_id=\S+",
    r"client_secret=\S+",
    r'private_key=[\'"]?(0x)?[a-fA-F0-9]{64}[\'"]?',
    r'public_key=[\'"]?[A-Za-z0-9+/=]{44}[\'"]?',
]


def scrub_secrets(data: Any, patterns: Optional[List[str]] = None) -> Any:
    """
    Recursively scrubs sensitive data from dictionaries, lists, tuples, and sets.
    Handles cyclical data structures and redacts secrets based on key names and value patterns.

    Note: @lru_cache decorator removed to support unhashable types (dict, list, set).
    """
    all_patterns = [
        re.compile(p, re.IGNORECASE) for p in (patterns or _global_secret_patterns)
    ]
    seen = set()

    def _scrub(item: Any) -> Any:
        # 1. Cycle detection using object IDs to prevent infinite recursion and TypeErrors
        obj_id = id(item)
        if obj_id in seen:
            return "... [cycle detected] ..."

        # Only add mutable types that can cause cycles to the 'seen' set
        if isinstance(item, (dict, list, set)):
            seen.add(obj_id)

        # 2. Key-based and recursive scrubbing for collections
        if isinstance(item, dict):
            scrubbed = {}
            for k, v in item.items():
                # Redact value if the key suggests it is sensitive
                if any(
                    term in str(k).lower()
                    for term in ["key", "secret", "password", "token", "private_key"]
                ):
                    scrubbed[k] = "***REDACTED***"
                else:
                    scrubbed[k] = _scrub(v)
            return scrubbed
        elif isinstance(item, list):
            return [_scrub(elem) for elem in item]
        elif isinstance(item, tuple):
            return tuple(_scrub(elem) for elem in item)
        elif isinstance(item, set):
            return {_scrub(elem) for elem in item}
        elif isinstance(item, str):
            # 3. Value-based scrubbing for strings using regex patterns
            scrubbed_item = item
            for pattern in all_patterns:
                scrubbed_item = pattern.sub("***REDACTED***", scrubbed_item)
            return scrubbed_item

        # 4. Return other data types unchanged
        return item

    return _scrub(data)


# --- Audit Manager (Centralized and Signed) ---
AUDIT_LOG_FILE = os.getenv("DLT_AUDIT_LOG_FILE", "dlt_audit.jsonl")
AUDIT_INTEGRITY_FILE = os.getenv("DLT_AUDIT_INTEGRITY_FILE", "dlt_audit_integrity.json")
AUDIT_HMAC_KEY_ENV = "DLT_AUDIT_HMAC_KEY"
_dlt_audit_hmac_key: Optional[bytes] = None


def _get_dlt_audit_hmac_key() -> bytes:
    global _dlt_audit_hmac_key
    if _dlt_audit_hmac_key is None:
        key_str = SECRETS_MANAGER.get_secret(
            AUDIT_HMAC_KEY_ENV, required=PRODUCTION_MODE
        )
        if not key_str and PRODUCTION_MODE:
            msg = "CRITICAL: DLT_AUDIT_HMAC_KEY is required in PRODUCTION_MODE but not found."
            _base_logger.critical(msg)
            _schedule_alert(msg, level="CRITICAL")
            if not TESTING_MODE:
                sys.exit(1)
        if key_str:
            _dlt_audit_hmac_key = key_str.encode("utf-8")
        else:
            _dlt_audit_hmac_key = os.urandom(32)
            _base_logger.warning(
                "DLT_AUDIT_HMAC_KEY_ENV not set. Generated a random key for audit log signing. THIS IS INSECURE FOR PRODUCTION."
            )
    return _dlt_audit_hmac_key


class AuditManager:
    _instance = None
    _is_initialized = False
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                # Double-checked locking pattern for thread safety
                if cls._instance is None:
                    cls._instance = super(AuditManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if AuditManager._is_initialized:
            return
        self.log_file_path = AUDIT_LOG_FILE
        self.integrity_file_path = AUDIT_INTEGRITY_FILE
        self._log_lock = asyncio.Lock()
        self._integrity_lock = asyncio.Lock()
        self._bg_tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._under_pytest = os.getenv("PYTEST_CURRENT_TEST") is not None
        self._disable_integrity = os.getenv("DLT_DISABLE_INTEGRITY") == "1"

        os.makedirs(os.path.dirname(self.log_file_path) or ".", exist_ok=True)

        try:
            with open(self.log_file_path, "a") as f:
                f.write("")
            _base_logger.info(f"Audit log file '{self.log_file_path}' is writable.")
        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Audit log file '{self.log_file_path}' is not writable or accessible: {e}. Aborting startup."
            )
            _schedule_alert(
                f"CRITICAL: DLT Audit log file '{self.log_file_path}' is not writable. Aborting.",
                level="CRITICAL",
            )
            if not TESTING_MODE:
                sys.exit(1)

        if not os.path.exists(self.integrity_file_path):
            with open(self.integrity_file_path, "w") as f:
                json.dump(
                    {
                        "last_verified_entry_count": 0,
                        "last_verification_time": datetime.utcnow().isoformat(),
                    },
                    f,
                )

        if not self._under_pytest and not self._disable_integrity:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._loop = loop
                    self._bg_tasks.append(
                        loop.create_task(self._periodic_integrity_check())
                    )
            except RuntimeError:
                self._loop = None

        atexit.register(self._sync_shutdown)

        AuditManager._is_initialized = True

    async def log_event(self, event_type: str, **kwargs):
        event_data = {
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "id": str(uuid.uuid4()),
            "payload": kwargs,
        }
        event_json_str = json.dumps(event_data, sort_keys=True, ensure_ascii=False)

        h = hmac.new(
            _get_dlt_audit_hmac_key(), event_json_str.encode("utf-8"), hashlib.sha256
        )
        signed_event = {"event": event_data, "signature": h.hexdigest()}

        async with self._log_lock:
            try:
                with open(self.log_file_path, "a") as f:
                    f.write(json.dumps(signed_event) + "\n")
                _base_logger.debug(f"Audit event '{event_type}' logged to file.")
            except Exception as e:
                _base_logger.critical(
                    f"CRITICAL: Failed to write audit event to file: {e}", exc_info=True
                )
                _schedule_alert(
                    f"CRITICAL: Failed to write DLT audit event to file: {e}",
                    level="CRITICAL",
                )

    async def shutdown(self):
        if not self._bg_tasks:
            return
        for t in self._bg_tasks:
            if not t.done():
                t.cancel()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop and self._loop.is_running() and running is self._loop:
            for t in list(self._bg_tasks):
                with suppress(asyncio.CancelledError):
                    await t
        elif self._loop and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(
                self._await_tasks(self._bg_tasks), self._loop
            )
            with suppress(Exception):
                fut.result(timeout=0.75)
        self._bg_tasks.clear()

    async def _await_tasks(self, tasks):
        for t in tasks:
            with suppress(asyncio.CancelledError):
                await t

    def _sync_shutdown(self):
        if not self._bg_tasks or not self._loop or not self._loop.is_running():
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self.shutdown(), self._loop)
            with suppress(Exception):
                fut.result(timeout=0.75)
        except Exception:
            pass

    async def verify_integrity(self, max_age_hours: int = 24) -> bool:
        try:
            async with self._integrity_lock:
                with open(self.integrity_file_path, "r") as f:
                    integrity_meta = json.load(f)

            last_verified_time_str = integrity_meta.get("last_verification_time")
            if last_verified_time_str:
                last_verified_time = datetime.fromisoformat(last_verified_time_str)
                if datetime.utcnow() - last_verified_time < timedelta(
                    hours=max_age_hours
                ):
                    _base_logger.info(
                        "Audit log integrity recently verified. Skipping full check."
                    )
                    if PROMETHEUS_AVAILABLE:
                        audit_log_integrity_status.set(1)
                    return True

            current_entry_count = 0
            mismatched_signatures = 0
            async with self._log_lock:
                with open(self.log_file_path, "r") as f:
                    for line_num, line in enumerate(f, 1):
                        try:
                            signed_event = json.loads(line)
                            event_data = signed_event.get("event")
                            signature = signed_event.get("signature")

                            if not event_data or not signature:
                                _base_logger.error(
                                    f"Audit log line {line_num} is malformed (missing event/signature)."
                                )
                                mismatched_signatures += 1
                                continue

                            event_json_recreated = json.dumps(
                                event_data, sort_keys=True, ensure_ascii=False
                            )
                            expected_signature = hmac.new(
                                _get_dlt_audit_hmac_key(),
                                event_json_recreated.encode("utf-8"),
                                hashlib.sha256,
                            ).hexdigest()

                            if signature != expected_signature:
                                _base_logger.critical(
                                    f"CRITICAL: Audit log integrity compromised: Signature mismatch on line {line_num}. Event: {event_data}"
                                )
                                _schedule_alert(
                                    f"CRITICAL: DLT Audit log integrity compromised: Signature mismatch on line {line_num}.",
                                    level="CRITICAL",
                                )
                                mismatched_signatures += 1
                            current_entry_count += 1
                        except json.JSONDecodeError as e:
                            _base_logger.error(
                                f"Audit log line {line_num} is not valid JSON: {e}. Line: {line.strip()}"
                            )
                            mismatched_signatures += 1
                        except Exception as e:
                            _base_logger.error(
                                f"Unexpected error during audit log verification on line {line_num}: {e}",
                                exc_info=True,
                            )
                            mismatched_signatures += 1

            if mismatched_signatures > 0:
                _base_logger.critical(
                    f"DLT Audit log integrity check FAILED. {mismatched_signatures} signature mismatches found."
                )
                if PROMETHEUS_AVAILABLE:
                    audit_log_integrity_status.set(0)
                return False
            else:
                _base_logger.info(
                    f"DLT Audit log integrity check PASSED. {current_entry_count} entries verified."
                )
                async with self._integrity_lock:
                    with open(self.integrity_file_path, "w") as f:
                        json.dump(
                            {
                                "last_verified_entry_count": current_entry_count,
                                "last_verification_time": datetime.utcnow().isoformat(),
                            },
                            f,
                        )
                if PROMETHEUS_AVAILABLE:
                    audit_log_integrity_status.set(1)
                return True

        except FileNotFoundError:
            _base_logger.warning(
                "Audit log file or integrity meta file not found. Cannot verify integrity."
            )
            if PROMETHEUS_AVAILABLE:
                audit_log_integrity_status.set(0)
            return False
        except Exception as e:
            _base_logger.critical(
                f"CRITICAL: Error during audit log integrity verification: {e}",
                exc_info=True,
            )
            _schedule_alert(
                f"CRITICAL: Error during DLT audit log integrity verification: {e}",
                level="CRITICAL",
            )
            if PROMETHEUS_AVAILABLE:
                audit_log_integrity_status.set(0)
            return False

    async def _periodic_integrity_check(self, interval_seconds: int = 3600):
        while True:
            await asyncio.sleep(interval_seconds)
            await self.verify_integrity()


AUDIT = AuditManager()


# --- Base Off-Chain Client ---
class BaseOffChainClient(ABC):
    """Abstract base class for all off-chain storage clients."""

    def __init__(self, config: Dict[str, Any]):
        try:
            validated_config = BaseOffChainConfig(**config).dict(exclude_unset=True)
        except ValidationError as e:
            raise DLTClientValidationError(
                f"Invalid off-chain client configuration: {e}", self.__class__.__name__
            )
        self.config = validated_config
        self.logger = DLTClientLoggerAdapter(
            _base_logger, {"client_type": self.__class__.__name__}
        )
        self._circuit_breaker = CircuitBreaker(
            client_type=self.__class__.__name__,
            failure_threshold=self.config.get("circuit_breaker_threshold", 5),
            reset_timeout=self.config.get("circuit_breaker_reset_timeout", 60),
        )
        self.client_type = self.__class__.__name__

    async def _run_blocking_in_executor(
        self, func: Callable, *args: Any, **kwargs: Any
    ) -> Any:
        global_executor = getattr(_base_logger, "_global_executor", None)
        if global_executor is None:
            global_executor = ThreadPoolExecutor(
                max_workers=self.config.get("executor_max_workers", 4)
            )
            setattr(_base_logger, "_global_executor", global_executor)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            global_executor, lambda: func(*args, **kwargs)
        )

    @abstractmethod
    async def save_blob(
        self, key_prefix: str, payload_blob: bytes, correlation_id: Optional[str] = None
    ) -> str:
        pass

    @abstractmethod
    async def get_blob(
        self, off_chain_id: str, correlation_id: Optional[str] = None
    ) -> bytes:
        pass

    async def close(self) -> None:
        if not hasattr(self, "_closed") or not self._closed:
            self.logger.info(
                f"Closing {self.__class__.__name__} client.",
                extra={"client_type": self.__class__.__name__},
            )
            self._closed = True


# --- Base DLT Client ---
class BaseDLTClient(ABC):
    """Abstract base class for all DLT clients."""

    def __init__(self, config: Dict[str, Any], off_chain_client: BaseOffChainClient):
        try:
            validated_config = BaseDLTConfig(**config).dict(exclude_unset=True)
        except ValidationError as e:
            raise DLTClientValidationError(
                f"Invalid DLT client configuration: {e}", self.__class__.__name__
            )
        self.config = validated_config
        self.off_chain_client = off_chain_client
        self.logger = DLTClientLoggerAdapter(
            _base_logger, {"client_type": self.__class__.__name__}
        )
        self._circuit_breaker = CircuitBreaker(
            client_type=self.__class__.__name__,
            failure_threshold=self.config.get("circuit_breaker_threshold", 5),
            reset_timeout=self.config.get("circuit_breaker_reset_timeout", 60),
        )
        self._closed = False
        self.client_type = self.__class__.__name__

    async def __aenter__(self) -> "BaseDLTClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _run_blocking_in_executor(
        self, func: Callable, *args: Any, **kwargs: Any
    ) -> Any:
        global_executor = getattr(_base_logger, "_global_executor", None)
        if global_executor is None:
            global_executor = ThreadPoolExecutor(
                max_workers=self.config.get("executor_max_workers", 4)
            )
            setattr(_base_logger, "_global_executor", global_executor)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            global_executor, lambda: func(*args, **kwargs)
        )

    @abstractmethod
    async def health_check(
        self, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def write_checkpoint(
        self,
        checkpoint_name: str,
        hash: str,
        prev_hash: str,
        metadata: Dict[str, Any],
        payload_blob: bytes,
        correlation_id: Optional[str] = None,
    ) -> Tuple[str, str, int]:
        pass

    @abstractmethod
    async def read_checkpoint(
        self,
        name: str,
        version: Optional[Union[int, str]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_version_tx(
        self, name: str, version: int, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def rollback_checkpoint(
        self, name: str, rollback_hash: str, correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        pass

    async def close(self) -> None:
        if not hasattr(self, "_closed") or not self._closed:
            self.logger.info(
                f"Closing {self.__class__.__name__} client.",
                extra={"client_type": self.__class__.__name__},
            )
            await self.off_chain_client.close()
            self._closed = True


# --- Plugin System Integration ---
PLUGIN_MANIFEST = {
    "name": "dlt_base",
    "version": "1.0.0",
    "description": "Base DLT client framework with off-chain storage support",
    "type": "framework",
    "capabilities": ["dlt_operations", "off_chain_storage"],
    "entry_points": ["register_plugin_entrypoints"],
}


def register_plugin_entrypoints(register_func: Callable):
    """Register plugin entry points with the plugin manager."""
    register_func(
        name="dlt_write_checkpoint",
        executor_func=lambda client, *args, **kwargs: client.write_checkpoint(
            *args, **kwargs
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="dlt_read_checkpoint",
        executor_func=lambda client, *args, **kwargs: client.read_checkpoint(
            *args, **kwargs
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="dlt_get_version_tx",
        executor_func=lambda client, *args, **kwargs: client.get_version_tx(
            *args, **kwargs
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="dlt_rollback_checkpoint",
        executor_func=lambda client, *args, **kwargs: client.rollback_checkpoint(
            *args, **kwargs
        ),
        capabilities=["dlt_operations"],
    )
    register_func(
        name="off_chain_save_blob",
        executor_func=lambda client, *args, **kwargs: client.off_chain_client.save_blob(
            *args, **kwargs
        ),
        capabilities=["off_chain_storage"],
    )
    register_func(
        name="off_chain_get_blob",
        executor_func=lambda client, *args, **kwargs: client.off_chain_client.get_blob(
            *args, **kwargs
        ),
        capabilities=["off_chain_storage"],
    )


def create_dlt_client(
    client_type: str, dlt_config: Dict[str, Any], off_chain_config: Dict[str, Any]
) -> BaseDLTClient:
    """
    Factory function to create a DLT client of the specified type with appropriate off-chain storage.

    Args:
        client_type: The type of DLT client to create (e.g., 'fabric', 'ethereum', 'corda')
        dlt_config: Configuration for the DLT client
        off_chain_config: Configuration for the off-chain storage client

    Returns:
        An initialized BaseDLTClient instance
    """
    # Import client implementations only when needed
    if client_type.lower() == "fabric" and FABRIC_AVAILABLE:
        from .fabric_client import FabricClient, FabricOffChainClient

        off_chain = FabricOffChainClient(off_chain_config)
        return FabricClient(dlt_config, off_chain)
    elif client_type.lower() in ("ethereum", "evm") and WEB3_AVAILABLE:
        from .ethereum_client import EthereumClient, EthereumOffChainClient

        off_chain = EthereumOffChainClient(off_chain_config)
        return EthereumClient(dlt_config, off_chain)
    elif client_type.lower() == "s3" and S3_AVAILABLE:
        from .s3_client import S3Client, S3OffChainClient

        off_chain = S3OffChainClient(off_chain_config)
        return S3Client(dlt_config, off_chain)
    elif client_type.lower() == "gcs" and GCS_AVAILABLE:
        from .gcs_client import GCSClient, GCSOffChainClient

        off_chain = GCSOffChainClient(off_chain_config)
        return GCSClient(dlt_config, off_chain)
    elif client_type.lower() == "azure" and AZURE_BLOB_AVAILABLE:
        from .azure_client import AzureBlobClient, AzureBlobOffChainClient

        off_chain = AzureBlobOffChainClient(off_chain_config)
        return AzureBlobClient(dlt_config, off_chain)
    else:
        supported_backends = []
        if FABRIC_AVAILABLE:
            supported_backends.append("fabric")
        if WEB3_AVAILABLE:
            supported_backends.append("ethereum/evm")
        if S3_AVAILABLE:
            supported_backends.append("s3")
        if GCS_AVAILABLE:
            supported_backends.append("gcs")
        if AZURE_BLOB_AVAILABLE:
            supported_backends.append("azure")

        raise DLTClientConfigurationError(
            f"Unsupported DLT client type '{client_type}' or missing dependencies. Supported: {supported_backends}",
            "DLTClientFactory",
            details={"client_type": client_type, "supported": supported_backends},
        )


# --- Plugin Manager Registration ---
try:
    from ..plugin_manager import PluginManager

    # Auto-register with plugin manager if available
    def _register_with_plugin_manager():
        try:
            plugin_manager = PluginManager.get_instance()
            plugin_manager.register_plugin(
                name="dlt_base", module=sys.modules[__name__], manifest=PLUGIN_MANIFEST
            )
            _base_logger.info("DLT base framework registered with plugin manager.")
        except Exception as e:
            _base_logger.warning(
                f"Could not auto-register DLT base framework with plugin manager: {e}"
            )

    # Only register in production mode
    if PRODUCTION_MODE:
        _register_with_plugin_manager()
except ImportError:
    _base_logger.debug("Plugin manager not available, skipping auto-registration.")


# --- Exports for backwards compatibility with guardrails.audit_log ---
# These aliases and utility functions provide compatibility with existing code
# that expects specific names from this module.

# Type aliases for production use
ProductionDLTClient = BaseDLTClient
ProductionOffChainClient = BaseOffChainClient

# Global DLT client instance placeholder for singleton pattern
_dlt_client_instance: Optional[BaseDLTClient] = None


async def initialize_dlt_backend_clients(config: Dict[str, Any]) -> None:
    """
    Initialize the DLT backend clients based on the provided configuration.

    This function sets up the global _dlt_client_instance with the appropriate
    DLT client (Simple, EVM, Fabric, etc.) based on configuration.

    Args:
        config: Configuration dictionary containing DLT backend settings.
            Expected keys:
            - dlt_type: Type of DLT client ('simple', 'evm', 'fabric', 'corda')
            - off_chain_storage_type: Type of off-chain storage ('in_memory', 's3', 'gcs', 'azure_blob')
            - Additional client-specific configuration
    """
    global _dlt_client_instance

    dlt_type = config.get("dlt_type", "simple").lower()
    off_chain_type = config.get("off_chain_storage_type", "in_memory").lower()

    _base_logger.info(
        f"Initializing DLT backend: dlt_type={dlt_type}, off_chain_type={off_chain_type}"
    )

    # Use a registry pattern to track available off-chain client implementations
    off_chain_registry: Dict[str, Optional[Type[BaseOffChainClient]]] = {
        "in_memory": None,
        "s3": None,
        "gcs": None,
        "azure_blob": None,
    }

    # Import off-chain client implementations
    try:
        from .dlt_offchain_clients import (
            AzureBlobOffChainClient,
            GcsOffChainClient,
            InMemoryOffChainClient,
            S3OffChainClient,
        )

        off_chain_registry["in_memory"] = InMemoryOffChainClient
        off_chain_registry["s3"] = S3OffChainClient
        off_chain_registry["gcs"] = GcsOffChainClient
        off_chain_registry["azure_blob"] = AzureBlobOffChainClient
    except ImportError as e:
        _base_logger.warning(f"Some off-chain clients unavailable: {e}")

    # Create off-chain client based on configuration
    off_chain_client: Optional[BaseOffChainClient] = None

    OffChainClientClass = off_chain_registry.get(off_chain_type)
    if OffChainClientClass:
        off_chain_client = OffChainClientClass(config)
        if hasattr(off_chain_client, "initialize") and off_chain_type != "in_memory":
            await off_chain_client.initialize()
    else:
        # Fallback to in-memory if available
        InMemoryClass = off_chain_registry.get("in_memory")
        if InMemoryClass:
            _base_logger.warning(
                f"Off-chain type '{off_chain_type}' not available, falling back to in_memory"
            )
            off_chain_client = InMemoryClass(config)
        else:
            _base_logger.error("No off-chain client available")
            return

    # Create DLT client based on configuration
    if dlt_type == "simple":
        try:
            from .dlt_simple_clients import SimpleDLTClient as SimpleDLTClientImpl

            _dlt_client_instance = SimpleDLTClientImpl(config, off_chain_client)
            await _dlt_client_instance.initialize()
        except ImportError as e:
            _base_logger.error(f"SimpleDLTClient unavailable: {e}")
    elif dlt_type == "evm":
        try:
            from .dlt_evm_clients import EthereumClientWrapper

            _dlt_client_instance = EthereumClientWrapper(config, off_chain_client)
        except ImportError as e:
            _base_logger.error(f"EVMDLTClient unavailable: {e}")
    elif dlt_type == "fabric":
        try:
            from .dlt_fabric_clients import FabricDLTClient

            _dlt_client_instance = FabricDLTClient(config, off_chain_client)
            await _dlt_client_instance.initialize()
        except ImportError as e:
            _base_logger.error(f"FabricDLTClient unavailable: {e}")
    elif dlt_type == "corda":
        try:
            from .dlt_corda_clients import CordaDLTClient

            _dlt_client_instance = CordaDLTClient(config, off_chain_client)
            await _dlt_client_instance.initialize()
        except ImportError as e:
            _base_logger.error(f"CordaDLTClient unavailable: {e}")
    else:
        _base_logger.warning(f"Unknown DLT type: {dlt_type}, using SimpleDLTClient")
        try:
            from .dlt_simple_clients import SimpleDLTClient as SimpleDLTClientFallback

            _dlt_client_instance = SimpleDLTClientFallback(config, off_chain_client)
            await _dlt_client_instance.initialize()
        except ImportError as e:
            _base_logger.error(f"SimpleDLTClient unavailable: {e}")

    if _dlt_client_instance:
        _base_logger.info(
            f"DLT backend initialized: {_dlt_client_instance.__class__.__name__}"
        )
    else:
        _base_logger.warning("DLT backend initialization failed - no client available")


# Lazy imports to avoid circular import issues
# EVMDLTClient and SimpleDLTClient are loaded on-demand via __getattr__
_lazy_imports = {
    "EVMDLTClient": (".dlt_evm_clients", "EthereumClientWrapper"),
    "SimpleDLTClient": (".dlt_simple_clients", "SimpleDLTClient"),
}


def __getattr__(name: str):
    """
    Lazy module attribute loader to avoid circular imports.

    This allows EVMDLTClient and SimpleDLTClient to be imported from this module
    without causing circular import issues, as the actual import only happens
    when the attribute is accessed, not at module load time.
    """
    if name in _lazy_imports:
        module_path, class_name = _lazy_imports[name]
        try:
            from importlib import import_module

            module = import_module(module_path, package=__package__)
            value = getattr(module, class_name)
            # Cache the value for future access
            globals()[name] = value
            return value
        except ImportError:
            _base_logger.debug(
                f"{name} not available - dependencies may not be installed"
            )
            # Return None for missing optional dependencies
            return None
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
