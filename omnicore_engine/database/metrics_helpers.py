# File: metrics_helpers.py
import logging
from prometheus_client import Counter, Histogram, Gauge, REGISTRY
from typing import Optional

logger = logging.getLogger(__name__)


def get_or_create_counter_local(
    name: str, documentation: str, labelnames: tuple = ()
) -> Counter:
    """Idempotently creates or retrieves a Prometheus Counter."""
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector and isinstance(collector, Counter):
            return collector
        return Counter(name, documentation, labelnames=labelnames)
    except ValueError:
        return REGISTRY._names_to_collectors[name]
    except Exception as e:
        logger.error(f"Error getting or creating Counter '{name}': {e}")
        raise


def get_or_create_gauge_local(
    name: str, documentation: str, labelnames: tuple = ()
) -> Gauge:
    """Idempotently creates or retrieves a Prometheus Gauge."""
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector and isinstance(collector, Gauge):
            return collector
        return Gauge(name, documentation, labelnames=labelnames)
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered with a different type or incompatible labels. Reusing existing."
        )
        return REGISTRY._names_to_collectors[name]
    except Exception as e:
        logger.error(f"Error getting or creating Gauge '{name}': {e}")
        raise


def get_or_create_histogram_local(
    name: str,
    documentation: str,
    labelnames: tuple = (),
    buckets: Optional[tuple] = Histogram.DEFAULT_BUCKETS,
) -> Histogram:
    """Idempotently creates or retrieves a Prometheus Histogram."""
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector and isinstance(collector, Histogram):
            return collector
        return Histogram(name, documentation, labelnames=labelnames, buckets=buckets)
    except ValueError:
        logger.warning(
            f"Metric '{name}' already registered with a different type or incompatible labels. Reusing existing."
        )
        return REGISTRY._names_to_collectors[name]
    except Exception as e:
        logger.error(f"Error getting or creating Histogram '{name}': {e}")
        raise
