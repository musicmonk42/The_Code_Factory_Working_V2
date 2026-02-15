# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Worker utilities for multi-process deployments.

This module provides utilities for identifying and managing worker processes
in multi-process deployments (Gunicorn, Uvicorn workers, Kubernetes pods, etc.).
It enables proper resource allocation (ports, IDs) across workers to avoid conflicts.

Architecture:
    - Provides worker identification for port allocation
    - Supports multiple deployment scenarios (local, containerized, Railway, K8s)
    - Falls back gracefully when worker identification is unavailable
    - Thread-safe and process-safe

Integration Points:
    - Used by: omnicore_engine/metrics.py, generator/runner/runner_metrics.py, 
               generator/clarifier/clarifier.py
    - Enables: Dynamic Prometheus port allocation per worker
    - Prevents: Port conflicts in multi-worker deployments

Compliance:
    - Thread-safe: All functions are stateless and thread-safe
    - Process-safe: Designed for multi-process environments
    - Environment-aware: Detects Railway, K8s, and other platforms
    - Logging: Uses standard logging for diagnostics

Version: 1.0.0
Created: 2025-02-15
Last Modified: 2025-02-15
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def get_worker_offset() -> int:
    """
    Derive a unique worker offset for resource allocation in multi-process deployments.
    
    This function identifies the current worker process and returns an integer offset
    that can be used to allocate unique resources (e.g., ports) per worker. This prevents
    conflicts when multiple workers try to bind to the same port.
    
    Worker identification priority:
    1. WORKER_ID environment variable (explicit worker numbering)
    2. PROMETHEUS_MULTIPROC_DIR environment variable (parsed for worker ID)
    3. multiprocessing.current_process()._identity (for multiprocessing.Pool workers)
    4. Default to 0 (single worker or identification failed)
    
    Returns:
        int: Worker offset (0-based). Returns 0 for single-worker deployments or
             when worker identification is unavailable.
    
    Examples:
        >>> # In a Gunicorn worker with WORKER_ID=2
        >>> get_worker_offset()
        2
        
        >>> # In a multiprocessing.Pool worker (second worker)
        >>> get_worker_offset()
        1
        
        >>> # In a single-worker deployment
        >>> get_worker_offset()
        0
    
    Note:
        - The function is designed to fail gracefully and return 0 when worker
          identification is not possible
        - Accessing multiprocessing.current_process()._identity is necessary as
          there's no public API for worker identification in multiprocessing.Pool
        - This is a known pattern used in production multi-worker deployments
    """
    # Priority 1: Explicit WORKER_ID environment variable
    worker_id_env = os.getenv("WORKER_ID")
    if worker_id_env:
        try:
            offset = int(worker_id_env)
            logger.debug(f"Worker offset derived from WORKER_ID: {offset}")
            return offset
        except (ValueError, TypeError) as e:
            logger.warning(
                f"WORKER_ID environment variable '{worker_id_env}' is not a valid integer: {e}. "
                "Falling back to alternative worker identification."
            )
    
    # Priority 2: PROMETHEUS_MULTIPROC_DIR (may contain worker identifier)
    # Note: This is primarily used in Prometheus multiprocess mode but can be repurposed
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if multiproc_dir:
        try:
            # Try to extract a numeric component from the directory path
            # Common patterns: /tmp/prometheus_multiproc_0, /prometheus/worker_1
            import re
            match = re.search(r'[\D_](\d+)$', multiproc_dir)
            if match:
                offset = int(match.group(1))
                logger.debug(f"Worker offset derived from PROMETHEUS_MULTIPROC_DIR: {offset}")
                return offset
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(
                f"Could not extract worker ID from PROMETHEUS_MULTIPROC_DIR '{multiproc_dir}': {e}"
            )
    
    # Priority 3: multiprocessing.current_process()._identity
    # This is used when the application is started with multiprocessing.Pool
    # Note: _identity is a private attribute, but it's the only way to get worker ID
    # in multiprocessing.Pool. This is a known limitation of the multiprocessing module.
    try:
        import multiprocessing
        current_process = multiprocessing.current_process()
        
        # Check if _identity exists and is not empty
        if hasattr(current_process, '_identity') and current_process._identity:
            # _identity is a tuple like (1,) for the first worker
            # We subtract 1 to make it 0-based
            offset = current_process._identity[0] - 1
            logger.debug(f"Worker offset derived from multiprocessing._identity: {offset}")
            return offset
    except (IndexError, AttributeError, TypeError) as e:
        # This is expected in single-worker deployments or when not using multiprocessing.Pool
        logger.debug(f"Could not derive worker offset from multiprocessing: {e}")
    
    # Default: Single worker or identification unavailable
    logger.debug("Worker offset defaulting to 0 (single worker or identification unavailable)")
    return 0


def calculate_worker_port(base_port: int, max_workers: Optional[int] = None) -> int:
    """
    Calculate a unique port for the current worker based on the base port and worker offset.
    
    This is a convenience function that combines get_worker_offset() with port calculation.
    It ensures that each worker gets a unique port to avoid binding conflicts.
    
    Args:
        base_port: The base port number (e.g., 9090 for Prometheus metrics)
        max_workers: Optional maximum number of workers. If provided, validates that
                    the calculated port doesn't exceed base_port + max_workers
    
    Returns:
        int: The calculated port number for this worker
    
    Raises:
        ValueError: If max_workers is provided and the worker offset exceeds it
    
    Examples:
        >>> # Worker 0 with base port 9090
        >>> calculate_worker_port(9090)
        9090
        
        >>> # Worker 2 with base port 9090
        >>> # (assuming WORKER_ID=2 or similar)
        >>> calculate_worker_port(9090)
        9092
        
        >>> # With max workers validation
        >>> calculate_worker_port(9090, max_workers=10)
        9090  # Returns base_port + offset, max 9099
    """
    offset = get_worker_offset()
    
    if max_workers is not None and offset >= max_workers:
        raise ValueError(
            f"Worker offset {offset} exceeds maximum workers {max_workers}. "
            f"This indicates a configuration issue or too many workers spawned."
        )
    
    port = base_port + offset
    logger.debug(f"Calculated worker port: {port} (base={base_port}, offset={offset})")
    return port


# Module metadata for introspection
__all__ = ["get_worker_offset", "calculate_worker_port"]
__version__ = "1.0.0"
__author__ = "Novatrax Labs LLC"
__status__ = "Production"
