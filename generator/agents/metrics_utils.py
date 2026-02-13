# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Shared Metrics Utilities for Generator Agents

Provides idempotent Prometheus metric creation to prevent 'Duplicated timeseries
in CollectorRegistry' errors across agent modules during initialization, testing,
and hot-reload scenarios.
"""

import logging

from prometheus_client import REGISTRY

logger = logging.getLogger(__name__)


def get_or_create_metric(metric_class, name: str, description: str, labelnames=None):
    """
    Idempotent metric factory with deduplication protection.

    Implements check-before-create pattern to prevent 'Duplicated timeseries
    in CollectorRegistry' errors that crash agents during initialization.

    Thread Safety: Uses REGISTRY's internal locking mechanism.

    Args:
        metric_class: prometheus_client metric class (Counter, Gauge, Histogram)
        name: Unique metric name following prometheus naming conventions
        description: Human-readable metric description
        labelnames: Optional list of label names for dimensional metrics

    Returns:
        Existing or newly created metric instance

    Raises:
        ValueError: Only if a non-duplicate registration error occurs
    """
    labelnames = labelnames or []

    # Check if metric already exists in registry (idempotent)
    try:
        existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing
    except (AttributeError, KeyError):
        pass  # Registry structure may vary

    # Create new metric if it doesn't exist
    try:
        if labelnames:
            return metric_class(name, description, labelnames)
        return metric_class(name, description)
    except ValueError as e:
        # Handle race condition: metric was created by another thread/process
        if "Duplicated timeseries" in str(e):
            existing = REGISTRY._names_to_collectors.get(name)
            if existing is not None:
                return existing
        raise  # Re-raise if it's a different error
