import logging
import os
from typing import Optional, Tuple, Dict, Any

from prometheus_client import Counter, Gauge, Histogram, REGISTRY
from prometheus_client import multiprocess

logger = logging.getLogger(__name__)

# Initialize multiprocess support if the environment variable is set
multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
if multiproc_dir:
    if os.path.exists(multiproc_dir):
        for f in os.listdir(multiproc_dir):
            os.remove(os.path.join(multiproc_dir, f))
    else:
        os.makedirs(multiproc_dir, exist_ok=True)
    multiprocess.MultiProcessCollector(REGISTRY)
    logger.info(
        f"Initialized Prometheus REGISTRY with multiprocess support using dir: {multiproc_dir}"
    )
else:
    logger.warning(
        "PROMETHEUS_MULTIPROC_DIR not set. Using single-process mode—metrics may corrupt in multi-worker apps."
    )

# Global labels for environment and cluster, loaded from environment variables
GLOBAL_LABELS = {
    "environment": os.getenv("ENVIRONMENT", "development"),
    "cluster": os.getenv("CLUSTER_NAME", "default-cluster"),
}


class LabeledMetricWrapper:
    """
    Wrapper that automatically applies global labels to metrics.
    This allows metrics to be used without explicitly specifying global labels each time.
    """

    def __init__(
        self,
        metric,
        global_labels: Dict[str, str],
        extra_labelnames: Tuple[str, ...] = (),
    ):
        self._metric = metric
        self._global_labels = global_labels
        self._extra_labelnames = extra_labelnames
        self._name = metric._name
        self._documentation = metric._documentation
        self._labelnames = metric._labelnames

    def labels(self, **kwargs):
        """Apply labels, automatically including global labels."""
        # Merge global labels with provided labels
        all_labels = {**self._global_labels, **kwargs}
        return self._metric.labels(**all_labels)

    def inc(self, amount=1):
        """Increment counter/gauge with global labels applied."""
        if self._extra_labelnames:
            raise ValueError(
                f"{self._name} has additional labels {self._extra_labelnames} that must be specified"
            )
        return self.labels().inc(amount)

    def dec(self, amount=1):
        """Decrement gauge with global labels applied."""
        if self._extra_labelnames:
            raise ValueError(
                f"{self._name} has additional labels {self._extra_labelnames} that must be specified"
            )
        return self.labels().dec(amount)

    def set(self, value):
        """Set gauge value with global labels applied."""
        if self._extra_labelnames:
            raise ValueError(
                f"{self._name} has additional labels {self._extra_labelnames} that must be specified"
            )
        return self.labels().set(value)

    def observe(self, amount):
        """Observe histogram value with global labels applied."""
        if self._extra_labelnames:
            raise ValueError(
                f"{self._name} has additional labels {self._extra_labelnames} that must be specified"
            )
        return self.labels().observe(amount)

    def time(self):
        """Time a code block (for histograms) with global labels applied."""
        if self._extra_labelnames:
            raise ValueError(
                f"{self._name} has additional labels {self._extra_labelnames} that must be specified"
            )
        return self.labels().time()

    def __getattr__(self, name):
        """Delegate other attributes to the underlying metric."""
        return getattr(self._metric, name)


def _get_or_create_metric_internal(metric_class, name, documentation, labelnames=(), buckets=None):
    """
    Internal helper to get or create a Prometheus metric.
    Handles unregistering existing metrics if there's a type mismatch.
    """
    try:
        existing_metric = REGISTRY._names_to_collectors.get(name)
        if existing_metric and isinstance(existing_metric, metric_class):
            # Return wrapped metric with existing metric
            extra_labelnames = tuple(l for l in labelnames if l not in GLOBAL_LABELS)
            return LabeledMetricWrapper(existing_metric, GLOBAL_LABELS, extra_labelnames)
        if existing_metric:
            REGISTRY.unregister(existing_metric)
            logger.warning(
                f"Unregistered existing metric '{name}' due to type mismatch or re-creation attempt."
            )
    except KeyError:
        pass
    except Exception as e:
        logger.error(f"Error checking/unregistering metric {name}: {e}")

    all_labelnames = tuple(sorted(set(labelnames + tuple(GLOBAL_LABELS.keys()))))

    # Validate labels
    for label in all_labelnames:
        if not isinstance(label, str) or not label:
            raise ValueError(f"Invalid label name: '{label}'. Must be a non-empty string.")

    # Create the metric
    if buckets:
        metric = metric_class(
            name,
            documentation,
            labelnames=all_labelnames,
            buckets=buckets,
            registry=REGISTRY,
        )
    else:
        metric = metric_class(name, documentation, labelnames=all_labelnames, registry=REGISTRY)

    # Return wrapped metric
    extra_labelnames = tuple(l for l in labelnames if l not in GLOBAL_LABELS)
    return LabeledMetricWrapper(metric, GLOBAL_LABELS, extra_labelnames)


class MetricRegistry:
    """
    A registry for managing Prometheus metrics, ensuring metrics are created
    once and can be retrieved, with automatic application of global labels.
    """

    def __init__(self):
        self.metrics: Dict[str, Any] = {}

    def get_or_create(
        self,
        metric_class,
        name: str,
        documentation: str,
        labelnames: Tuple[str, ...] = (),
        buckets: Optional[Tuple[float, ...]] = None,
    ):
        """
        Gets an existing metric or creates a new one with global labels.
        Returns a LabeledMetricWrapper that automatically applies global labels.
        """
        if name in self.metrics:
            return self.metrics[name]

        metric = _get_or_create_metric_internal(
            metric_class, name, documentation, labelnames, buckets
        )
        self.metrics[name] = metric
        return metric


registry = MetricRegistry()

# --- Metrics for Meta-Learning Orchestrator ---
METRIC_CONFLICTS = registry.get_or_create(
    Counter,
    "ml_metric_conflicts_total",
    "Total metric type mismatches requiring unregistration",
)
ML_INGESTION_COUNT = registry.get_or_create(
    Counter, "ml_ingestion_total", "Total learning records ingested"
)
ML_TRAINING_TRIGGER_COUNT = registry.get_or_create(
    Counter, "ml_training_trigger_total", "Total ML training jobs triggered"
)
ML_TRAINING_SUCCESS_COUNT = registry.get_or_create(
    Counter, "ml_training_success_total", "Successful ML training jobs"
)
ML_TRAINING_FAILURE_COUNT = registry.get_or_create(
    Counter, "ml_training_failure_total", "Failed ML training jobs"
)
ML_EVALUATION_COUNT = registry.get_or_create(
    Counter, "ml_evaluation_total", "Total ML model evaluations"
)
ML_DEPLOYMENT_TRIGGER_COUNT = registry.get_or_create(
    Counter, "ml_deployment_trigger_total", "Total ML model deployments triggered"
)
ML_DEPLOYMENT_SUCCESS_COUNT = registry.get_or_create(
    Counter, "ml_deployment_success_total", "Successful ML model deployments"
)
ML_DEPLOYMENT_FAILURE_COUNT = registry.get_or_create(
    Counter, "ml_deployment_failure_total", "Failed ML model deployments"
)
ML_ORCHESTRATOR_ERRORS = registry.get_or_create(
    Counter, "ml_orchestrator_errors_total", "Errors within MetaLearningOrchestrator"
)
# Tuned histogram buckets for ML-specific latencies
ML_TRAINING_LATENCY = registry.get_or_create(
    Histogram,
    "ml_training_latency_seconds",
    "Latency of ML training jobs",
    buckets=(0.1, 1, 5, 10, 30, 60, 300, 600, 1800),
)
ML_EVALUATION_LATENCY = registry.get_or_create(
    Histogram,
    "ml_evaluation_latency_seconds",
    "Latency of ML model evaluations",
    buckets=(0.1, 1, 5, 10, 30, 60, 300, 600),
)
ML_DEPLOYMENT_LATENCY = registry.get_or_create(
    Histogram,
    "ml_deployment_latency_seconds",
    "Latency of ML model deployments",
    buckets=(0.1, 1, 5, 10, 30, 60, 300, 600),
)
ML_CURRENT_MODEL_VERSION = registry.get_or_create(
    Gauge, "ml_current_model_version", "Current deployed ML model version"
)
ML_DATA_QUEUE_SIZE = registry.get_or_create(
    Gauge, "ml_data_queue_size", "Number of new data records awaiting training"
)
ML_DEPLOYMENT_RETRIES_EXHAUSTED = registry.get_or_create(
    Counter,
    "ml_deployment_retries_exhausted_total",
    "Deployments that exhausted all retries",
)
ML_LEADER_STATUS = registry.get_or_create(
    Gauge, "ml_leader_status", "Is this orchestrator instance the leader (1) or not (0)"
)

# Metrics with additional labels beyond global ones
ML_AUDIT_EVENTS_TOTAL = registry.get_or_create(
    Counter,
    "ml_audit_events_total",
    "Total audit events logged",
    labelnames=("event_type",),
)
ML_AUDIT_HASH_MISMATCH = registry.get_or_create(
    Counter, "ml_audit_hash_mismatch_total", "Total audit hash mismatches detected"
)
ML_AUDIT_SIGNATURE_MISMATCH = registry.get_or_create(
    Counter,
    "ml_audit_signature_mismatch_total",
    "Total audit signature mismatches detected",
)


# Keep the standalone function for backward compatibility
def get_or_create_metric(metric_type, name, documentation, labelnames=None):
    """Get or create a metric (for backward compatibility)."""
    if metric_type == "counter":
        return Counter(name, documentation, labelnames=labelnames or [])
    elif metric_type == "histogram":
        return Histogram(name, documentation, labelnames=labelnames or [])
    return None


# Example usage
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from prometheus_client import generate_latest

    # Set environment variables
    os.environ["ENVIRONMENT"] = "production"
    os.environ["CLUSTER_NAME"] = "us-east-1-prod"

    # Recreate global labels with new env values
    GLOBAL_LABELS = {
        "environment": os.getenv("ENVIRONMENT", "development"),
        "cluster": os.getenv("CLUSTER_NAME", "default-cluster"),
    }

    # Create new registry with updated labels
    registry = MetricRegistry()

    # Get metrics
    ingestion_metric = registry.get_or_create(
        Counter, "ml_ingestion_total", "Total learning records ingested"
    )
    audit_metric = registry.get_or_create(
        Counter,
        "ml_audit_events_total",
        "Total audit events",
        labelnames=("event_type",),
    )

    # Use metrics without specifying global labels
    ingestion_metric.inc()
    ingestion_metric.inc(5)

    # For metrics with extra labels, you must specify them
    audit_metric.labels(event_type="login").inc()
    audit_metric.labels(event_type="logout").inc(2)

    logger.info("Metrics output:")
    print(generate_latest().decode("utf-8"))
