# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# File: metrics_helpers.py
import logging
from typing import Optional, Sequence

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


def _safe_isinstance(obj, cls) -> bool:
    """Safely check isinstance, handling cases where cls may be mocked.
    
    During testing, prometheus_client classes may be mocked, causing
    isinstance() to fail with TypeError. This function catches that error.
    """
    try:
        return isinstance(obj, cls)
    except TypeError:
        # cls is likely a mock and not a valid type for isinstance()
        return False


def _validate_labelnames(labelnames) -> tuple:
    """Validate and convert labelnames to tuple. Raises Exception for invalid types."""
    if labelnames is None:
        return ()
    if isinstance(labelnames, (list, tuple)):
        return tuple(labelnames)
    raise Exception(f"labelnames must be a list or tuple, got {type(labelnames).__name__}")


def get_or_create_counter_local(
    name: str, documentation: str, labelnames: tuple = ()
) -> Counter:
    """Idempotently creates or retrieves a Prometheus Counter."""
    labelnames = _validate_labelnames(labelnames)
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector and _safe_isinstance(collector, Counter):
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
    labelnames = _validate_labelnames(labelnames)
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector and _safe_isinstance(collector, Gauge):
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
    labelnames = _validate_labelnames(labelnames)
    try:
        collector = REGISTRY._names_to_collectors.get(name)
        if collector and _safe_isinstance(collector, Histogram):
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
