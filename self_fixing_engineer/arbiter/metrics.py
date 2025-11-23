import logging
import os
import secrets
import sys
import threading
from time import time
from typing import Any, Dict, Optional, Tuple, Type

from fastapi import Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
)

# Mock/Placeholder imports for a self-contained fix
try:
    from arbiter.logging_utils import PIIRedactorFilter
    from arbiter_plugin_registry import PlugInKind, registry
except ImportError:

    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

    class PlugInKind:
        CORE_SERVICE = "core_service"

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True


# Use centralized OpenTelemetry configuration
from arbiter.otel_config import get_tracer

tracer = get_tracer(__name__)

# --- Logging Setup ---
_metrics_logger = logging.getLogger("arbiter.metrics")
if not _metrics_logger.hasHandlers():
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(PIIRedactorFilter())
    _metrics_logger.addHandler(handler)
_metrics_logger.setLevel(logging.INFO)

# --- Multi-process Setup ---
if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
    _metrics_logger.info(
        f"Prometheus multiprocess mode enabled. Metrics will be stored in: {os.environ['PROMETHEUS_MULTIPROC_DIR']}"
    )

# Lock to prevent race conditions during metric registration
_METRICS_LOCK = threading.Lock()

# --- Standard Metrics (defined early to avoid circular dependencies) ---
METRIC_REGISTRATIONS_TOTAL = Counter(
    "arbiter_metric_registrations_total",
    "Total number of metric registrations",
    labelnames=("metric_type",),
)

METRIC_REGISTRATION_ERRORS = Counter(
    "arbiter_metric_registration_errors_total",
    "Total errors during metric registration",
    labelnames=("metric_type", "error_type"),
)

# Add the missing METRIC_REGISTRATION_TIME histogram
METRIC_REGISTRATION_TIME = Histogram(
    "arbiter_metric_registration_time_seconds",
    "Time taken to register a metric",
    labelnames=("metric_name", "metric_type"),
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)


def get_or_create_metric(
    metric_type: Type,
    name: str,
    documentation: str,
    labelnames: Optional[Tuple[str, ...]] = None,
    buckets: Optional[Tuple[float, ...]] = None,
    initial_value: Optional[float] = None,
) -> Any:
    """
    Utility to get an existing Prometheus metric or create a new one in a thread-safe manner.

    This function prevents re-registration errors by using a lock to ensure that only
    one thread at a time can check and register a new metric.

    Args:
        metric_type (Type): The Prometheus metric class (Counter, Gauge, Histogram, Summary).
        name (str): The name of the metric.
        documentation (str): The metric's documentation string.
        labelnames (Optional[Tuple[str, ...]]): A tuple of label names.
        buckets (Optional[Tuple[float, ...]]): A tuple of bucket values for Histograms.
        initial_value (Optional[float]): Initial value for Gauge metrics.

    Returns:
        Any: The existing or newly created metric instance.

    Raises:
        ValueError: If an unsupported metric type is provided.
    """
    if labelnames is None:
        labelnames = ()

    full_name = f"arbiter_{name}"

    # Track registration time
    start_time = time()

    # Use the safe tracer (will be no-op if OTEL is disabled)
    with tracer.start_as_current_span(f"get_or_create_metric_{full_name}"):
        with _METRICS_LOCK:
            try:
                if full_name in REGISTRY._names_to_collectors:
                    existing_metric = REGISTRY._names_to_collectors[full_name]
                    # Fix: Check the actual type of the existing metric
                    # Use type() or __class__ instead of isinstance with a Type parameter
                    if type(existing_metric).__name__ == metric_type.__name__:
                        _metrics_logger.debug(
                            f"Reusing existing metric '{full_name}' of type {metric_type.__name__}."
                        )
                        return existing_metric
                    else:
                        _metrics_logger.critical(
                            f"Metric '{full_name}' already registered with a different type ({type(existing_metric).__name__}). "
                            f"This indicates a serious logical error in the application. Reusing existing metric."
                        )
                        return existing_metric
                else:
                    _metrics_logger.info(
                        f"Registering new metric: '{full_name}' as {metric_type.__name__}."
                    )
                    METRIC_REGISTRATIONS_TOTAL.labels(
                        metric_type=metric_type.__name__
                    ).inc()
                    try:
                        new_metric = None
                        if metric_type == Histogram:
                            new_metric = metric_type(
                                full_name,
                                documentation,
                                labelnames=labelnames,
                                buckets=buckets or Histogram.DEFAULT_BUCKETS,
                            )
                        elif metric_type == Counter:
                            new_metric = metric_type(
                                full_name, documentation, labelnames=labelnames
                            )
                        elif metric_type == Gauge:
                            new_metric = metric_type(
                                full_name, documentation, labelnames=labelnames
                            )
                            # Set initial value if provided and no labels
                            if initial_value is not None and not labelnames:
                                new_metric.set(initial_value)
                        elif metric_type == Summary:
                            new_metric = metric_type(
                                full_name, documentation, labelnames=labelnames
                            )
                        else:
                            raise ValueError(f"Unsupported metric type: {metric_type}")

                        # Record registration time
                        elapsed_time = time() - start_time
                        METRIC_REGISTRATION_TIME.labels(
                            metric_name=full_name, metric_type=metric_type.__name__
                        ).observe(elapsed_time)

                        return new_metric
                    except Exception as e:
                        METRIC_REGISTRATION_ERRORS.labels(
                            metric_type=metric_type.__name__,
                            error_type=type(e).__name__,
                        ).inc()
                        raise
            except Exception:
                # Record failed registration time as well
                elapsed_time = time() - start_time
                METRIC_REGISTRATION_TIME.labels(
                    metric_name=full_name, metric_type=metric_type.__name__
                ).observe(elapsed_time)
                raise


def get_or_create_counter(
    name: str, documentation: str, labelnames: Optional[Tuple[str, ...]] = None
) -> Counter:
    """
    Creates or retrieves a Prometheus Counter metric.

    Args:
        name: The metric name.
        documentation: The metric description.
        labelnames: Optional tuple of label names.

    Returns:
        Counter: The created or existing Counter metric.

    Raises:
        ValueError: If metric registration fails due to invalid parameters.
    """
    return get_or_create_metric(Counter, name, documentation, labelnames)


def get_or_create_gauge(
    name: str,
    documentation: str,
    labelnames: Optional[Tuple[str, ...]] = None,
    initial_value: Optional[float] = None,
) -> Gauge:
    """
    Creates or retrieves a Prometheus Gauge metric.

    Args:
        name: The metric name.
        documentation: The metric description.
        labelnames: Optional tuple of label names.
        initial_value: Optional initial value for the gauge.

    Returns:
        Gauge: The created or existing Gauge metric.

    Raises:
        ValueError: If metric registration fails due to invalid parameters.
    """
    return get_or_create_metric(
        Gauge, name, documentation, labelnames, initial_value=initial_value
    )


def get_or_create_histogram(
    name: str,
    documentation: str,
    labelnames: Optional[Tuple[str, ...]] = None,
    buckets: Optional[Tuple[float, ...]] = None,
) -> Histogram:
    """
    Creates or retrieves a Prometheus Histogram metric.

    Args:
        name: The metric name.
        documentation: The metric description.
        labelnames: Optional tuple of label names.
        buckets: Optional tuple of histogram buckets.

    Returns:
        Histogram: The created or existing Histogram metric.

    Raises:
        ValueError: If metric registration fails due to invalid parameters.
    """
    return get_or_create_metric(
        Histogram, name, documentation, labelnames=labelnames, buckets=buckets
    )


def get_or_create_summary(
    name: str, documentation: str, labelnames: Optional[Tuple[str, ...]] = None
) -> Summary:
    """
    Creates or retrieves a Prometheus Summary metric.

    Args:
        name: The metric name.
        documentation: The metric description.
        labelnames: Optional tuple of label names.

    Returns:
        Summary: The created or existing Summary metric.

    Raises:
        ValueError: If metric registration fails due to invalid parameters.
    """
    return get_or_create_metric(Summary, name, documentation, labelnames=labelnames)


# --- Additional Standard Metrics ---
HTTP_REQUESTS_TOTAL = Counter(
    "arbiter_http_requests_total",
    "Total HTTP requests handled by Arbiter API.",
    labelnames=("method", "endpoint"),
)

HTTP_REQUESTS_LATENCY_SECONDS = Histogram(
    "arbiter_http_requests_latency_seconds",
    "HTTP request latency (seconds) for Arbiter API.",
    labelnames=("endpoint",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, float("inf")),
)

ERRORS_TOTAL = Counter(
    "arbiter_errors_total",
    "Total errors encountered by Arbiter system.",
    labelnames=("module", "error_type"),
)

# --- Metrics Endpoint ---
security = HTTPBearer()


def metrics_handler(auth: HTTPAuthorizationCredentials = Depends(security)) -> Response:
    """
    Exposes metrics in Prometheus format with authentication.

    The metrics endpoint is protected by a bearer token, which is retrieved from
    the `METRICS_AUTH_TOKEN` environment variable.
    """
    expected_token = os.environ.get("METRICS_AUTH_TOKEN")
    if not expected_token or not secrets.compare_digest(
        auth.credentials, expected_token
    ):
        raise HTTPException(status_code=401, detail="Unauthorized access to metrics")

    if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
        try:
            from prometheus_client.multiprocess import MultiProcessCollector

            return Response(
                content=MultiProcessCollector(REGISTRY).collect(),
                media_type="text/plain",
            )
        except ImportError:
            _metrics_logger.warning(
                "prometheus_client.multiprocess not found. Falling back to single process metrics."
            )
        except Exception as e:
            _metrics_logger.error(
                f"Failed to collect multiprocess metrics: {e}", exc_info=True
            )

    return Response(content=generate_latest(REGISTRY), media_type="text/plain")


def register_dynamic_metric(
    metric_type: Type,
    name: str,
    documentation: str,
    labelnames: Optional[Tuple[str, ...]] = None,
    **kwargs,
) -> Any:
    """
    Dynamically registers a custom metric for use by plugins or other modules.

    Args:
        metric_type (Type): The Prometheus metric class (Counter, Gauge, Histogram, Summary).
        name (str): The name of the metric.
        documentation (str): The metric's documentation string.
        labelnames (Optional[Tuple[str, ...]]): A tuple of label names.
        **kwargs: Additional keyword arguments, such as `buckets` for Histograms.

    Returns:
        Any: The created or existing metric instance.

    Raises:
        ValueError: If an unsupported metric type is provided.
    """
    try:
        if metric_type not in (Counter, Gauge, Histogram, Summary):
            raise ValueError(f"Unsupported metric type: {metric_type.__name__}")

        metric_creator = getattr(
            sys.modules[__name__], f"get_or_create_{metric_type.__name__.lower()}"
        )

        if metric_type is Histogram:
            return metric_creator(
                name,
                documentation,
                labelnames=labelnames,
                buckets=kwargs.get("buckets"),
            )
        elif metric_type is Gauge:
            return metric_creator(
                name,
                documentation,
                labelnames=labelnames,
                initial_value=kwargs.get("initial_value"),
            )
        else:
            return metric_creator(name, documentation, labelnames=labelnames)

    except Exception as e:
        error_type = type(e).__name__
        metric_name = (
            metric_type.__name__.lower() if "metric_type" in locals() else "unknown"
        )
        METRIC_REGISTRATION_ERRORS.labels(
            metric_type=metric_name, error_type=error_type
        ).inc()
        _metrics_logger.error(
            f"Failed to register dynamic metric '{name}': {e}", exc_info=True
        )
        raise


def health_check() -> Dict[str, Any]:
    """
    Checks the health of the metrics system, ensuring the multi-process directory is accessible.

    Returns:
        Dict with health status and details.

    Raises:
        IOError: If PROMETHEUS_MULTIPROC_DIR is inaccessible.
    """
    try:
        if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
            multiproc_dir = os.environ["PROMETHEUS_MULTIPROC_DIR"]
            if not os.path.isdir(multiproc_dir):
                return {
                    "status": "unhealthy",
                    "error": f"Directory {multiproc_dir} does not exist",
                }
            if not os.access(multiproc_dir, os.W_OK):
                return {
                    "status": "unhealthy",
                    "error": f"Directory {multiproc_dir} is not writable",
                }

        registered_metrics = len(REGISTRY._names_to_collectors)
        return {"status": "healthy", "registered_metrics": registered_metrics}
    except Exception as e:
        _metrics_logger.error(f"Health check failed: {e}", exc_info=True)
        METRIC_REGISTRATION_ERRORS.labels(
            metric_type="health_check", error_type=type(e).__name__
        ).inc()
        return {"status": "unhealthy", "error": str(e)}


def clear_stale_metrics() -> None:
    """
    Clears stale metrics from the PROMETHEUS_MULTIPROC_DIR.

    Raises:
        IOError: If directory cleanup fails.
    """
    try:
        if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
            multiproc_dir = os.environ["PROMETHEUS_MULTIPROC_DIR"]
            for file in os.listdir(multiproc_dir):
                file_path = os.path.join(multiproc_dir, file)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    _metrics_logger.info(f"Removed stale metric file: {file_path}")
    except Exception as e:
        _metrics_logger.error(f"Failed to clear stale metrics: {e}", exc_info=True)
        METRIC_REGISTRATION_ERRORS.labels(
            metric_type="cleanup", error_type=type(e).__name__
        ).inc()
        raise IOError(f"Metrics cleanup failed: {e}") from e


def rotate_metrics_auth_token() -> str:
    """
    Rotates the METRICS_AUTH_TOKEN.

    Returns:
        The new token.

    Raises:
        ValueError: If token rotation fails.
    """
    try:
        new_token = secrets.token_urlsafe(32)
        os.environ["METRICS_AUTH_TOKEN"] = new_token
        _metrics_logger.info("METRICS_AUTH_TOKEN rotated successfully")
        return new_token
    except Exception as e:
        _metrics_logger.error(f"METRICS_AUTH_TOKEN rotation failed: {e}", exc_info=True)
        METRIC_REGISTRATION_ERRORS.labels(
            metric_type="token_rotation", error_type=type(e).__name__
        ).inc()
        raise ValueError(f"Token rotation failed: {e}") from e


# Register as a plugin for dynamic management
class MetricsService:
    @staticmethod
    async def initialize():
        _metrics_logger.info("Initializing MetricsService plugin.")

    @staticmethod
    async def start():
        # Start a server or similar, if necessary. For now, this is a no-op as metrics are passive.
        _metrics_logger.info("Starting MetricsService plugin.")

    @staticmethod
    async def stop():
        # Stop any background tasks. For now, this is a no-op as metrics are passive.
        _metrics_logger.info("Stopping MetricsService plugin.")

    @staticmethod
    async def health_check():
        return health_check()

    @staticmethod
    async def get_capabilities():
        return [
            "prometheus_metrics_response",
            "register_dynamic_metric",
            "health_check",
            "clear_stale_metrics",
        ]


registry.register(
    kind=PlugInKind.CORE_SERVICE,
    name="MetricsService",
    version="1.0.0",
    author="Arbiter Team",
)(MetricsService)

# Additional metrics for arbiter_growth
try:
    CONFIG_FALLBACK_USED = Counter(
        "arbiter_config_fallback_used_total", "Config fallback usage counter"
    )
except ValueError:
    # Metric already registered, get existing one
    CONFIG_FALLBACK_USED = REGISTRY._names_to_collectors.get(
        "arbiter_config_fallback_used_total"
    )

# Export all public metrics and functions
__all__ = [
    # Functions
    "get_or_create_metric",
    "get_or_create_counter",
    "get_or_create_gauge",
    "get_or_create_histogram",
    "get_or_create_summary",
    "register_dynamic_metric",
    "health_check",
    "clear_stale_metrics",
    "rotate_metrics_auth_token",
    "metrics_handler",
    # Metrics
    "METRIC_REGISTRATIONS_TOTAL",
    "METRIC_REGISTRATION_ERRORS",
    "METRIC_REGISTRATION_TIME",  # Added to exports
    "HTTP_REQUESTS_TOTAL",
    "HTTP_REQUESTS_LATENCY_SECONDS",
    "ERRORS_TOTAL",
    "CONFIG_FALLBACK_USED",
    # Classes
    "MetricsService",
    # Prometheus types (re-exported for convenience)
    "Counter",
    "Gauge",
    "Histogram",
    "Summary",
]
