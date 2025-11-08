"""
Metrics helper to handle duplicate registrations gracefully
"""
from prometheus_client import Counter, Gauge, Histogram, REGISTRY
import logging

logger = logging.getLogger(__name__)

def get_or_create_metric(metric_type, name, description, labelnames=None):
    """
    Get existing metric or create new one, handling duplicates gracefully.
    
    Args:
        metric_type: 'Counter', 'Gauge', or 'Histogram'
        name: Metric name
        description: Metric description
        labelnames: Optional list of label names
    
    Returns:
        The metric instance
    """
    # Check if metric already exists
    for collector in REGISTRY._collector_to_names:
        collector_names = REGISTRY._collector_to_names[collector]
        if name in collector_names:
            logger.debug(f"Metric {name} already exists, returning existing instance")
            return collector
    
    # Create new metric
    kwargs = {'name': name, 'documentation': description}
    if labelnames:
        kwargs['labelnames'] = labelnames
    
    try:
        if metric_type == 'Counter':
            return Counter(**kwargs)
        elif metric_type == 'Gauge':
            return Gauge(**kwargs)
        elif metric_type == 'Histogram':
            return Histogram(**kwargs)
        else:
            raise ValueError(f"Unknown metric type: {metric_type}")
    except ValueError as e:
        # Handle race condition where metric was created between check and create
        logger.warning(f"Metric {name} was created by another thread: {e}")
        for collector in REGISTRY._collector_to_names:
            if name in REGISTRY._collector_to_names[collector]:
                return collector
        raise  # Re-raise if we still can't find it

def clear_all_metrics():
    """Clear all metrics from the registry (useful for tests)"""
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        try:
            REGISTRY.unregister(collector)
        except Exception:
            pass
