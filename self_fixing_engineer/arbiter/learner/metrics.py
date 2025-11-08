# arbiter/learner/metrics.py

import os
from typing import Tuple, Optional, Dict, Any, Type
from prometheus_client import Counter, Gauge, Histogram, Summary, Info, REGISTRY
import structlog

# Structured logging setup
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level_number,
        structlog.stdlib.add_logger_name,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
logger = structlog.get_logger(__name__)

# Global labels that will be added to all metrics
GLOBAL_LABELS: Dict[str, str] = {
    "environment": os.getenv("ENVIRONMENT", "production"),
    "instance": os.getenv("INSTANCE_NAME", "learner-instance-1")
}

def _get_or_create_metric(
    metric_class: Type,
    name: str,
    documentation: str,
    labelnames: Tuple[str, ...] = (),
    buckets: Optional[Tuple[float, ...]] = None
) -> Any:
    """
    Create or retrieve a Prometheus metric, ensuring thread safety.
    Automatically adds global labels to all metrics except Info metrics.
    
    Args:
        metric_class: Prometheus metric class (e.g., Counter, Gauge).
        name: Metric name.
        documentation: Metric description.
        labelnames: Label names for the metric (global labels will be added automatically).
        buckets: Histogram buckets (if applicable).
    
    Returns:
        Prometheus metric instance.
    """
    # Add global labels to all metrics except Info
    if metric_class != Info:
        # Combine labelnames with global labels, avoiding duplicates
        global_label_names = tuple(GLOBAL_LABELS.keys())
        combined_labels = tuple(set(labelnames + global_label_names))
    else:
        combined_labels = labelnames
    
    try:
        existing_metric = REGISTRY._names_to_collectors.get(name)
        if existing_metric and isinstance(existing_metric, metric_class):
            return existing_metric
        if existing_metric:
            logger.warning("Unregistering existing metric due to type mismatch", metric_name=name)
            REGISTRY.unregister(existing_metric)
    except Exception as e:
        logger.error("Error checking/unregistering metric", metric_name=name, error=str(e))

    # Create metric with combined labels
    if metric_class == Info:
        metric = metric_class(name=name, documentation=documentation)
    else:
        metric_args = {
            "name": name,
            "documentation": documentation,
            "labelnames": combined_labels
        }
        if metric_class == Histogram and buckets:
            metric_args["buckets"] = buckets
        metric = metric_class(**metric_args)
    
    logger.debug("Created metric", metric_name=name, labels=combined_labels)
    return metric

# Module info metric - Info metrics don't support labels in the same way
learner_info = _get_or_create_metric(
    Info,
    "arbiter_learner_build_info",
    "Build information for the arbiter.learner module",
    ()
)
# Set the info values - this is set once at module load
learner_info.info({
    "version": "1.0.0",
    "environment": GLOBAL_LABELS["environment"],
    "instance": GLOBAL_LABELS["instance"]
})

# Helper function to get labels with global defaults
def get_labels(**kwargs) -> Dict[str, str]:
    """
    Helper function to merge provided labels with global labels.
    This makes it easier to use metrics without manually adding global labels each time.
    
    Example:
        learn_counter.labels(**get_labels(domain="test", source="api")).inc()
    """
    return {**GLOBAL_LABELS, **kwargs}

# Learning metrics
learn_counter = _get_or_create_metric(
    Counter,
    "arbiter_learner_learn_total",
    "Total number of learning events",
    ("domain", "source")
)

learn_error_counter = _get_or_create_metric(
    Counter,
    "arbiter_learner_learn_errors_total",
    "Total number of learning errors",
    ("domain", "error_type")
)

learn_duration_seconds = _get_or_create_metric(
    Histogram,
    "arbiter_learner_learn_duration_seconds",
    "Duration of a learning event in seconds",
    ("domain",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
)

learn_duration_summary = _get_or_create_metric(
    Summary,
    "arbiter_learner_learn_duration_summary_seconds",
    "Summary of learning event durations",
    ("domain",)
)

# Forgetting metrics
forget_counter = _get_or_create_metric(
    Counter,
    "arbiter_learner_forget_total",
    "Total number of forgetting events",
    ("domain",)
)

forget_duration_seconds = _get_or_create_metric(
    Histogram,
    "arbiter_learner_forget_duration_seconds",
    "Duration of a forgetting event in seconds",
    ("domain",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0)
)

forget_duration_summary = _get_or_create_metric(
    Summary,
    "arbiter_learner_forget_duration_summary_seconds",
    "Summary of forgetting event durations",
    ("domain",)
)

# Retrieval metrics
retrieve_hit_miss = _get_or_create_metric(
    Counter,
    "arbiter_learner_retrieve_cache_status",
    "Cache hit/miss status for knowledge retrieval",
    ("domain", "cache_status")
)

# Audit metrics
audit_events_total = _get_or_create_metric(
    Counter,
    "arbiter_learner_audit_events_total",
    "Total audit events logged",
    ("action",)
)

circuit_breaker_state = _get_or_create_metric(
    Gauge,
    "arbiter_learner_circuit_breaker_state",
    "Circuit breaker state (1=open, 0=closed)",
    ("name",)
)

audit_failure_total = _get_or_create_metric(
    Counter,
    "arbiter_learner_audit_failure_total",
    "Total audit operation failures",
    ("action", "error_type")
)

# Explanation metrics
explanation_llm_latency_seconds = _get_or_create_metric(
    Histogram,
    "arbiter_learner_explanation_llm_latency_seconds",
    "Latency of LLM calls for explanations",
    ("domain",),
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
)

explanation_llm_failure_total = _get_or_create_metric(
    Counter,
    "arbiter_learner_explanation_llm_failure_total",
    "Total LLM call failures for explanations",
    ("domain", "error_type")
)

# Fuzzy parser metrics
fuzzy_parser_success_total = _get_or_create_metric(
    Counter,
    "arbiter_learner_fuzzy_parser_success_total",
    "Total successful fuzzy parser executions",
    ("parser_name",)
)

fuzzy_parser_failure_total = _get_or_create_metric(
    Counter,
    "arbiter_learner_fuzzy_parser_failure_total",
    "Total failed fuzzy parser executions",
    ("parser_name", "error_type")
)

fuzzy_parser_latency_seconds = _get_or_create_metric(
    Histogram,
    "arbiter_learner_fuzzy_parser_latency_seconds",
    "Latency of fuzzy parser executions",
    ("parser_name",),
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 5.0)
)

# Self-audit metrics
self_audit_duration_seconds = _get_or_create_metric(
    Histogram,
    "arbiter_learner_self_audit_duration_seconds",
    "Duration of self-audit operations",
    (),  # No domain label for self-audit
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
)

self_audit_failure_total = _get_or_create_metric(
    Counter,
    "arbiter_learner_self_audit_failure_total",
    "Total self-audit failures",
    ("error_type",)
)