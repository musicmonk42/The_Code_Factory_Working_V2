# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Type

# --- Structured Logging Setup ---
import structlog

# The import statement has been corrected to properly group all imported components.
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    Summary,
    generate_latest,
    multiprocess,
)

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(indent=2),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

_metrics_logger = structlog.get_logger(__name__)
_metrics_logger = _metrics_logger.bind(module="metrics")

# --- Namespace and Registry Setup ---
METRICS_NAMESPACE = os.getenv("REASONER_METRICS_NAMESPACE", "reasoner")
PROMETHEUS_MULTIPROC_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR")
METRICS_REGISTRY: CollectorRegistry = CollectorRegistry()

# --- Global Metrics Dictionary ---
# This dictionary serves as a cache for created metric objects.
METRICS: Dict[str, Any] = {}


def get_or_create_metric(
    metric_type: Type,
    name: str,
    description: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None,
):
    """
    Get or create a metric, handling duplicates gracefully.

    Args:
        metric_type: Type of metric (Counter, Gauge, Histogram)
        name: Metric name
        description: Metric description
        labelnames: Tuple of label names
        buckets: Tuple of histogram buckets

    Returns:
        Metric instance
    """
    # Use name directly as key to avoid issues with finding existing metrics
    cache_key = name

    # Check if metric already exists in our cache
    if cache_key in METRICS:
        return METRICS[cache_key]

    # Try to create the metric
    try:
        if metric_type is Counter:
            metric = Counter(
                name,
                description,
                labelnames=labelnames or (),
                registry=METRICS_REGISTRY,
            )
        elif metric_type is Gauge:
            metric = Gauge(
                name,
                description,
                labelnames=labelnames or (),
                registry=METRICS_REGISTRY,
            )
        elif metric_type is Histogram:
            metric = Histogram(
                name,
                description,
                labelnames=labelnames or (),
                buckets=buckets or Histogram.DEFAULT_BUCKETS,
                registry=METRICS_REGISTRY,
            )
        elif metric_type is Summary:
            metric = Summary(
                name,
                description,
                labelnames=labelnames or (),
                registry=METRICS_REGISTRY,
            )
        else:
            raise ValueError(f"Unsupported metric type: {metric_type}")

        METRICS[cache_key] = metric
        return metric
    except ValueError as e:
        # Metric already registered, try to find it in the registry
        if "Duplicated timeseries" in str(e) or "already registered" in str(e):
            # Try to find the existing metric
            for collector in list(METRICS_REGISTRY._collector_to_names.keys()):
                if hasattr(collector, "_name") and collector._name == name:
                    METRICS[cache_key] = collector
                    return collector
        raise e


def initialize_metrics():
    """Initialize metrics, setting up multiprocess mode if configured."""
    global METRICS_REGISTRY, PROMETHEUS_MULTIPROC_DIR

    if PROMETHEUS_MULTIPROC_DIR:
        try:
            metrics_dir = Path(PROMETHEUS_MULTIPROC_DIR)
            metrics_dir.mkdir(parents=True, exist_ok=True)
            if not os.access(metrics_dir, os.W_OK):
                raise PermissionError(
                    f"Metrics directory {metrics_dir} is not writable."
                )
            multiprocess.MultiProcessCollector(METRICS_REGISTRY)
            _metrics_logger.info(
                "prometheus_multiprocess_enabled",
                directory=str(metrics_dir),
                namespace=METRICS_NAMESPACE,
            )
        except Exception as e:
            _metrics_logger.error(
                "multiprocess_setup_failed_fallback",
                directory=PROMETHEUS_MULTIPROC_DIR,
                error=str(e),
                exc_info=True,
            )
            METRICS_REGISTRY = CollectorRegistry()
            PROMETHEUS_MULTIPROC_DIR = None
            _metrics_logger.warning(
                "fallback_to_in_memory_registry", reason="Multiprocess setup failed."
            )
    else:
        _metrics_logger.info(
            "prometheus_single_process_mode", namespace=METRICS_NAMESPACE
        )
        try:
            ProcessCollector(registry=METRICS_REGISTRY, namespace=METRICS_NAMESPACE)
        except ValueError:
            # Process collector might already be registered
            pass

    # Initialize commonly used metrics
    # Using get_or_create_metric to handle metric registration gracefully
    METRICS["requests_total"] = get_or_create_metric(
        Counter,
        "reasoner_requests_total",
        "Total requests processed",
        labelnames=("user_id", "task_type"),
    )

    METRICS["prompt_size_bytes"] = get_or_create_metric(
        Histogram,
        "reasoner_prompt_size_bytes",
        "Size of generated prompts in bytes",
        labelnames=("type",),
    )

    METRICS["inference_duration_seconds"] = get_or_create_metric(
        Histogram,
        "reasoner_inference_duration_seconds",
        "Duration of inference operations",
        labelnames=("type", "strategy"),
    )

    # Add additional metrics that are referenced in the code
    METRICS["context_validation_errors"] = get_or_create_metric(
        Counter,
        "reasoner_context_validation_errors",
        "Context validation errors",
        labelnames=("error_code",),
    )

    METRICS["sensitive_data_redaction_total"] = get_or_create_metric(
        Counter,
        "reasoner_sensitive_data_redaction_total",
        "Total redactions of sensitive data",
        labelnames=("redaction_type",),
    )

    METRICS["reasoner_sanitization_latency_seconds"] = get_or_create_metric(
        Histogram,
        "reasoner_sanitization_latency_seconds",
        "Latency of context sanitization operations",
    )


# --- Utility Function for Metrics Exposition ---
def get_metrics_content() -> bytes:
    """
    Generates the latest metrics content for exposition via an HTTP endpoint.

    This function collects metrics from the central registry, including any
    multiprocess metrics if configured, and serializes them into the
    Prometheus text format.

    Returns:
        bytes: The serialized metrics content.
    """
    try:
        metrics_data = generate_latest(METRICS_REGISTRY)
        _metrics_logger.debug("metrics_exposed", size_bytes=len(metrics_data))
        return metrics_data
    except Exception as e:
        _metrics_logger.error("metrics_exposition_failed", error=str(e), exc_info=True)
        # Return empty bytes or a minimal error metric on failure
        error_metric = "reasoner_metrics_exposition_errors"
        return f"# HELP {error_metric} Errors in generating metrics\n# TYPE {error_metric} counter\n{error_metric} 1\n".encode(
            "utf-8"
        )


# Initialize metrics when the module is imported
initialize_metrics()
