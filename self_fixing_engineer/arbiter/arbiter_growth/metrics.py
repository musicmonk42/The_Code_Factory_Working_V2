import logging
from typing import Optional, Type, Union

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


def get_or_create_metric(
    metric_class: Type[Union[Counter, Gauge, Histogram]],
    name: str,
    documentation: str,
    labelnames: tuple = (),
    config_store: Optional[object] = None,
    registry: CollectorRegistry = REGISTRY,
) -> Union[Counter, Gauge, Histogram]:
    """
    Safely retrieves an existing metric from the registry or creates a new one.

    This function prevents errors that occur when attempting to register a metric
    with a name that's already in use. It checks for existing metrics and handles
    potential type mismatches by unregistering the old one.

    It also allows for sourcing metric configurations (like histogram buckets or
    additional labels) from a central ConfigStore.

    Args:
        metric_class: The Prometheus metric class (e.g., Counter, Gauge, Histogram).
        name (str): The name of the metric.
        documentation (str): The help text for the metric.
        labelnames (tuple, optional): A tuple of label names. Defaults to ().
        config_store (Optional[ConfigStore], optional): A config store instance to
            source custom metric configurations. Defaults to None.
        registry (CollectorRegistry, optional): The registry to use. Defaults to REGISTRY.

    Returns:
        Union[Counter, Gauge, Histogram]: The existing or newly created metric instance.
    """
    from .config_store import ConfigStore

    if config_store is None:
        config_store = ConfigStore()

    # Check for custom configurations in the config store
    custom_labels = config_store.get(f"metrics.{name}.labels", default=labelnames)
    custom_labels = custom_labels if custom_labels is not None else labelnames or ()
    custom_buckets = None
    if metric_class == Histogram:
        custom_buckets = config_store.get(f"metrics.{name}.buckets")

    try:
        # Check if a metric with the same name already exists
        existing_metric = registry._names_to_collectors.get(name)
        if existing_metric:
            # If it exists and is the correct type, return it
            if isinstance(existing_metric, metric_class):
                logger.debug(f"Returning existing metric '{name}'")
                return existing_metric
            # If the type is wrong, unregister the old one before creating a new one
            else:
                logger.warning(
                    f"Metric '{name}' exists with a different type. Unregistering old metric."
                )
                try:
                    registry.unregister(existing_metric)
                    logger.debug(f"Successfully unregistered old metric '{name}'.")
                except Exception as e:
                    logger.error(
                        f"Failed to unregister conflicting metric '{name}': {e}",
                        exc_info=True,
                    )
                    # If unregister fails, return the existing metric to avoid duplicate error
                    return existing_metric
    except KeyError:
        # The metric does not exist in the registry
        pass
    except Exception as e:
        # Catch any other unexpected errors during the check
        logger.error(
            f"Unexpected error while checking for metric '{name}': {e}", exc_info=True
        )

    # Create the new metric
    kwargs = {"labelnames": custom_labels} if custom_labels else {}
    if metric_class == Histogram and custom_buckets:
        kwargs["buckets"] = custom_buckets

    # Get metric class name safely (handles Mocks during test collection)
    class_name = getattr(metric_class, "__name__", str(metric_class))
    logger.debug(f"Creating new metric '{name}' of type {class_name}.")
    return metric_class(name, documentation, **kwargs, registry=registry)


# --- Arbiter Growth Manager Metrics ---

GROWTH_EVENTS = get_or_create_metric(
    Counter,
    "growth_events_total",
    "Total number of growth events recorded.",
    ("arbiter",),
)
GROWTH_SAVE_ERRORS = get_or_create_metric(
    Counter,
    "growth_save_errors_total",
    "Total number of errors encountered while saving arbiter state.",
    ("arbiter",),
)
GROWTH_PENDING_QUEUE = get_or_create_metric(
    Gauge,
    "growth_pending_queue_size",
    "The current size of the pending operations queue.",
    ("arbiter",),
)
GROWTH_SKILL_IMPROVEMENT = get_or_create_metric(
    Histogram,
    "growth_skill_improvement_value",
    "Distribution of skill improvement amounts.",
    ("arbiter", "skill"),
)
GROWTH_SNAPSHOTS = get_or_create_metric(
    Counter,
    "growth_snapshots_total",
    "Total number of state snapshots created.",
    ("arbiter",),
)
GROWTH_CIRCUIT_BREAKER_TRIPS = get_or_create_metric(
    Counter,
    "growth_circuit_breaker_trips_total",
    "Total number of times a circuit breaker has tripped (opened).",
    ("arbiter", "breaker_name"),
)
GROWTH_ANOMALY_SCORE = get_or_create_metric(
    Gauge,
    "growth_anomaly_score",
    "A score indicating the anomalousness of a growth event.",
    ("arbiter", "event_type"),
)

# --- Latency Metrics ---

GROWTH_EVENT_PUSH_LATENCY = get_or_create_metric(
    Histogram,
    "growth_event_push_latency_seconds",
    "Latency of pushing an event to external systems (e.g., Knowledge Graph).",
    ("arbiter",),
)
GROWTH_OPERATION_QUEUE_LATENCY = get_or_create_metric(
    Histogram,
    "growth_operation_queue_latency_seconds",
    "Time an operation spends in the pending queue before execution.",
    ("arbiter",),
)
GROWTH_OPERATION_EXECUTION_LATENCY = get_or_create_metric(
    Histogram,
    "growth_operation_execution_latency_seconds",
    "Time it takes to execute a queued operation.",
    ("arbiter",),
)
STORAGE_LATENCY_SECONDS = get_or_create_metric(
    Histogram,
    "storage_latency_seconds",
    "Latency of storage backend operations.",
    ("backend", "operation"),
)

# --- Security and Auditing Metrics ---

AUDIT_VALIDATION_ERRORS_TOTAL = get_or_create_metric(
    Counter,
    "audit_validation_errors_total",
    "Total number of audit chain validation failures.",
    ("arbiter",),
)
GROWTH_AUDIT_ANCHORS_TOTAL = get_or_create_metric(
    Counter,
    "growth_audit_anchors_total",
    "Total number of audit chain hashes anchored to an external ledger.",
    ("arbiter",),
)

# --- Idempotency and Rate Limiting Metrics ---

IDEMPOTENCY_HITS_TOTAL = get_or_create_metric(
    Counter,
    "idempotency_hits_total",
    "Total number of idempotency check hits and misses.",
    ("arbiter", "hit"),
)
RATE_LIMIT_REJECTIONS_TOTAL = get_or_create_metric(
    Counter,
    "rate_limit_rejections_total",
    "Total number of operations rejected due to rate limiting.",
    ("arbiter",),
)

# --- Configuration Metrics ---

CONFIG_FALLBACK_USED = get_or_create_metric(
    Counter,
    "config_fallback_used_total",
    "Total number of times a fallback default config was used.",
    ("config_key",),
)
