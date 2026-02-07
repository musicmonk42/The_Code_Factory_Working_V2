# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Onboarding module for the test_generation package.

This module provides a bridge to the main onboarding functionality located in
simulation.plugins.onboard, ensuring backward compatibility and proper module
resolution for the test_generation package.

Features:
- Re-exports all onboarding components from simulation.plugins.onboard
- Provides graceful fallbacks when the main module is not available
- Full observability with OpenTelemetry tracing and Prometheus metrics
- Thread-safe lazy initialization

Supported Environment Variables:
- **LOG_LEVEL**: (default `INFO`) Logging verbosity level.
- **ONBOARD_FALLBACK_MODE**: (default `0`) Enable fallback stubs when main module unavailable.

Author: Test Generation Platform Team
Version: 2.0.0
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union

# Pydantic for configuration validation
try:
    from pydantic import BaseModel, Field, ValidationError

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False

    class BaseModel:
        """Stub BaseModel when pydantic is not available."""

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def Field(*args, **kwargs):
        return kwargs.get("default")

    class ValidationError(Exception):
        pass


# OpenTelemetry tracing
try:
    from self_fixing_engineer.arbiter.otel_config import get_tracer

    tracer = get_tracer(__name__)
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

    class _NoOpSpan:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def set_attribute(self, key, value):
            pass

        def set_status(self, status):
            pass

        def record_exception(self, exc):
            pass

    class _NoOpTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, name, **kwargs):
            yield _NoOpSpan()

    tracer = _NoOpTracer()


# Prometheus metrics
try:
    from prometheus_client import Counter, Gauge, Histogram

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    class Counter:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

    class Gauge:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def set(self, *args, **kwargs):
            pass

    class Histogram:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass


# Logger initialization
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

# ============================================================================
# Metrics (idempotent registration)
# ============================================================================

_METRIC_CACHE: Dict[str, Any] = {}


def _get_or_create_metric(
    metric_class: Union[Type[Counter], Type[Gauge], Type[Histogram]],
    name: str,
    documentation: str,
    labelnames: tuple = (),
) -> Union[Counter, Gauge, Histogram]:
    """Idempotently get or create a Prometheus metric."""
    if name in _METRIC_CACHE:
        return _METRIC_CACHE[name]

    try:
        m = metric_class(name, documentation, labelnames=labelnames)
    except ValueError:
        # Already registered
        if PROMETHEUS_AVAILABLE:
            from prometheus_client import REGISTRY

            existing = REGISTRY._names_to_collectors.get(name)
            if existing:
                m = existing
            else:
                m = metric_class(name, documentation, labelnames=labelnames)
        else:
            m = metric_class(name, documentation, labelnames=labelnames)

    _METRIC_CACHE[name] = m
    return m


# Metrics for onboarding operations
ONBOARD_IMPORT_TOTAL = _get_or_create_metric(
    Counter,
    "test_gen_onboard_import_total",
    "Total onboard module import attempts",
    ("status",),
)
ONBOARD_OPS_TOTAL = _get_or_create_metric(
    Counter,
    "test_gen_onboard_ops_total",
    "Total onboard operations",
    ("operation", "status"),
)
ONBOARD_OPS_LATENCY = _get_or_create_metric(
    Histogram,
    "test_gen_onboard_ops_latency_seconds",
    "Onboard operation latency in seconds",
    ("operation",),
)


# ============================================================================
# Configuration Classes (Fallback implementations)
# ============================================================================


class OnboardConfigFallback(BaseModel if PYDANTIC_AVAILABLE else object):
    """
    Fallback configuration class for onboarding when main module is unavailable.

    Mirrors the structure of simulation.plugins.onboard.OnboardConfig to ensure
    API compatibility.
    """

    project_type: str = "agentic_swarm"
    plugins_dir: str = "./plugins"
    results_dir: str = "./simulation_results"
    notification_backend: Dict[str, Any] = {}
    checkpoint_backend: Dict[str, Any] = {}
    environment_variables: Dict[str, Any] = {}
    generated_with: Dict[str, Any] = {}

    if not PYDANTIC_AVAILABLE:

        def __init__(self, **kwargs):
            self.project_type = kwargs.get("project_type", "agentic_swarm")
            self.plugins_dir = kwargs.get("plugins_dir", "./plugins")
            self.results_dir = kwargs.get("results_dir", "./simulation_results")
            self.notification_backend = kwargs.get("notification_backend", {})
            self.checkpoint_backend = kwargs.get("checkpoint_backend", {})
            self.environment_variables = kwargs.get("environment_variables", {})
            self.generated_with = kwargs.get("generated_with", {})


# ============================================================================
# Main Module Import Logic
# ============================================================================

# Track module state
_MODULE_STATE = {
    "initialized": False,
    "import_attempted": False,
    "import_success": False,
    "error": None,
}

# Exported symbols - will be populated by import or fallback
OnboardConfig = None
ONBOARD_DEFAULTS = None
CORE_VERSION = None
onboard = None


def _attempt_import() -> bool:
    """
    Attempt to import the main onboarding module from simulation.plugins.

    Returns:
        True if import succeeded, False otherwise.
    """
    global OnboardConfig, ONBOARD_DEFAULTS, CORE_VERSION, onboard

    if _MODULE_STATE["import_attempted"]:
        return _MODULE_STATE["import_success"]

    _MODULE_STATE["import_attempted"] = True
    start_time = time.monotonic()

    with tracer.start_as_current_span("onboard_module_import") as span:
        try:
            # Attempt import from the canonical location
            from simulation.plugins.onboard import (
                CORE_VERSION as _CORE_VERSION,
                ONBOARD_DEFAULTS as _ONBOARD_DEFAULTS,
                OnboardConfig as _OnboardConfig,
                onboard as _onboard,
            )

            # Successfully imported - assign to module globals
            OnboardConfig = _OnboardConfig
            ONBOARD_DEFAULTS = _ONBOARD_DEFAULTS
            CORE_VERSION = _CORE_VERSION
            onboard = _onboard

            _MODULE_STATE["import_success"] = True
            _MODULE_STATE["initialized"] = True

            ONBOARD_IMPORT_TOTAL.labels(status="success").inc()
            span.set_attribute("import.success", True)
            span.set_attribute("import.source", "simulation.plugins.onboard")

            logger.info(
                "Successfully imported onboarding module from simulation.plugins.onboard"
            )
            return True

        except ImportError as e:
            _MODULE_STATE["error"] = str(e)
            _MODULE_STATE["import_success"] = False

            ONBOARD_IMPORT_TOTAL.labels(status="import_error").inc()
            span.set_attribute("import.success", False)
            span.set_attribute("import.error", str(e))
            span.record_exception(e)

            logger.warning(
                f"Could not import from simulation.plugins.onboard: {e}. "
                "Using fallback implementations."
            )

            # Use fallback implementations
            _initialize_fallbacks()
            return False

        except Exception as e:
            _MODULE_STATE["error"] = str(e)
            _MODULE_STATE["import_success"] = False

            ONBOARD_IMPORT_TOTAL.labels(status="unexpected_error").inc()
            span.set_attribute("import.success", False)
            span.set_attribute("import.error", str(e))
            span.record_exception(e)

            logger.error(
                f"Unexpected error importing onboarding module: {e}",
                exc_info=True,
            )

            # Use fallback implementations
            _initialize_fallbacks()
            return False

        finally:
            ONBOARD_OPS_LATENCY.labels(operation="import").observe(
                time.monotonic() - start_time
            )


def _initialize_fallbacks() -> None:
    """Initialize fallback implementations for all exported symbols."""
    global OnboardConfig, ONBOARD_DEFAULTS, CORE_VERSION, onboard

    OnboardConfig = OnboardConfigFallback
    ONBOARD_DEFAULTS = OnboardConfigFallback()
    CORE_VERSION = "1.0.0-fallback"
    onboard = _onboard_fallback

    _MODULE_STATE["initialized"] = True

    logger.info("Fallback implementations initialized for onboarding module")


async def _onboard_fallback(args: Any) -> Dict[str, Any]:
    """
    Fallback onboard function when the main module is not available.

    This provides a minimal implementation that logs the attempt and returns
    a status indicating that full onboarding functionality is not available.

    Args:
        args: Command-line arguments or configuration object.

    Returns:
        Dictionary with status information.
    """
    op = "onboard_fallback"
    start_time = time.monotonic()
    ONBOARD_OPS_TOTAL.labels(operation=op, status="attempt").inc()

    with tracer.start_as_current_span(f"test_gen_{op}") as span:
        try:
            logger.warning(
                "Onboard function called but simulation.plugins.onboard module "
                "is not available. Full onboarding functionality is disabled."
            )

            result = {
                "status": "fallback",
                "message": (
                    "Onboarding module not fully available. "
                    "Please ensure simulation.plugins.onboard is accessible."
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "module_state": {
                    "initialized": _MODULE_STATE["initialized"],
                    "import_success": _MODULE_STATE["import_success"],
                    "error": _MODULE_STATE["error"],
                },
            }

            ONBOARD_OPS_TOTAL.labels(operation=op, status="success").inc()
            span.set_attribute("onboard.status", "fallback")
            return result

        except Exception as e:
            ONBOARD_OPS_TOTAL.labels(operation=op, status="failure").inc()
            span.record_exception(e)
            logger.error(f"Error in onboard fallback: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        finally:
            ONBOARD_OPS_LATENCY.labels(operation=op).observe(
                time.monotonic() - start_time
            )


# ============================================================================
# Module Initialization
# ============================================================================


def get_module_status() -> Dict[str, Any]:
    """
    Get the current status of the onboarding module.

    Returns:
        Dictionary with module status information.
    """
    return {
        "initialized": _MODULE_STATE["initialized"],
        "import_attempted": _MODULE_STATE["import_attempted"],
        "import_success": _MODULE_STATE["import_success"],
        "error": _MODULE_STATE["error"],
        "using_fallback": not _MODULE_STATE["import_success"],
        "core_version": CORE_VERSION,
        "pydantic_available": PYDANTIC_AVAILABLE,
        "otel_available": OTEL_AVAILABLE,
        "prometheus_available": PROMETHEUS_AVAILABLE,
    }


def ensure_initialized() -> bool:
    """
    Ensure the module is initialized, attempting import if needed.

    Returns:
        True if module is fully functional, False if using fallbacks.
    """
    if not _MODULE_STATE["initialized"]:
        _attempt_import()
    return _MODULE_STATE["import_success"]


# Perform initialization on module load
_attempt_import()


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Primary exports
    "onboard",
    "OnboardConfig",
    "ONBOARD_DEFAULTS",
    "CORE_VERSION",
    # Utility functions
    "get_module_status",
    "ensure_initialized",
]
