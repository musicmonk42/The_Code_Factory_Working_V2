import asyncio
import logging
import os
import random
from typing import Any, Dict, List, Tuple

import aiohttp
import psutil
from aiolimiter import AsyncLimiter

# Import centralized OpenTelemetry configuration
from self_fixing_engineer.arbiter.otel_config import get_tracer
from prometheus_client import REGISTRY, Counter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# Mock/Placeholder imports for a self-contained fix
try:
    # FIXED: Correctly import PluginBase from the appropriate registry module
    from self_fixing_engineer.arbiter.logging_utils import PIIRedactorFilter
    from arbiter_plugin_registry import PluginBase, PlugInKind, registry
except ImportError:

    class registry:
        @staticmethod
        def register(kind, name, version, author):
            def decorator(cls):
                return cls

            return decorator

    class PlugInKind:
        CORE_SERVICE = "core_service"
        FIX = "FIX"

    class PIIRedactorFilter(logging.Filter):
        def filter(self, record):
            return True

    # FIXED: Mock PluginBase if the real one isn't available
    class PluginBase(object):
        pass


# Initialize tracer using centralized config
tracer = get_tracer(__name__)

# Constants
HEALTH_CHECK_TIMEOUT = 5
HEALTH_CHECK_RATE_LIMIT_MAX_RATE = 10
HEALTH_CHECK_RATE_LIMIT_TIME_PERIOD = 60

# Logging Setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    handler.addFilter(PIIRedactorFilter())
    logger.addHandler(handler)


# Helper function for idempotent metric creation
def _get_or_create_metric(metric_class: type, name: str, doc: str, labelnames: list):
    """Idempotently create or retrieve a Prometheus metric."""
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return metric_class(name, doc, labelnames)


# Prometheus Metrics
utils_ops_total = _get_or_create_metric(
    Counter, "utils_ops_total", "Total utils operations", ["operation"]
)
utils_errors_total = _get_or_create_metric(
    Counter, "utils_errors_total", "Total utils errors", ["operation"]
)

# Global state for session pooling and rate limiting
_HEALTH_SESSION = None
_HEALTH_SESSION_LOCK = asyncio.Lock()
_HEALTH_CHECK_LIMITER = AsyncLimiter(
    HEALTH_CHECK_RATE_LIMIT_MAX_RATE, HEALTH_CHECK_RATE_LIMIT_TIME_PERIOD
)


def is_valid_directory_path(path: str) -> bool:
    """
    Check if a path is a valid directory path that can be created.

    Args:
        path: The path to validate.

    Returns:
        True if the path is valid, False otherwise.
    """
    if not path:
        return False

    normalized = os.path.normpath(path)

    # Check for empty result or current directory marker (which happens with empty input)
    if normalized == ".":
        return False

    # Check for paths that are only separators (e.g., '/', '\\')
    if normalized.strip("\\/ ") == "":
        return False

    # Check for Windows root drive paths (e.g., 'D:', 'D:\', 'C:\')
    # These are valid paths but not valid directories to create
    if len(normalized) <= 3:
        # Match patterns like 'D:', 'D:\', 'D:/', etc.
        if len(normalized) >= 2 and normalized[1] == ":":
            return False

    return True


def safe_makedirs(path: str, fallback: str = "./reports") -> Tuple[str, bool]:
    """
    Safely create a directory with path validation and fallback.

    Args:
        path: The path to create.
        fallback: The fallback path if the primary path is invalid or fails.

    Returns:
        A tuple of (actual_path_used, success). The actual_path_used is the
        normalized path that was successfully created (either the original
        path or the fallback).
    """
    normalized_path = os.path.normpath(path) if path else fallback

    if not is_valid_directory_path(path):
        normalized_path = fallback

    try:
        os.makedirs(normalized_path, exist_ok=True)
        return normalized_path, True
    except OSError:
        # Fall back to the fallback path if the primary path fails
        fallback_normalized = os.path.normpath(fallback)
        try:
            os.makedirs(fallback_normalized, exist_ok=True)
            return fallback_normalized, True
        except OSError:
            return fallback_normalized, False


def random_chance(probability: float) -> bool:
    """
    Returns True with a given probability.

    Args:
        probability (float): The probability of returning True.
                             Must be a value between 0.0 and 1.0.

    Returns:
        bool: True if a random value is less than the specified probability,
              False otherwise.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError("Probability must be between 0.0 and 1.0")
    utils_ops_total.labels(operation="random_chance").inc()
    return random.random() < probability


def get_system_metrics() -> Dict[str, Any]:
    """
    Collects current system performance metrics using psutil.

    Returns:
        Dict[str, Any]: A dictionary containing key system metrics:
                        - "cpu_percent": CPU utilization percentage.
                        - "memory_percent": Virtual memory usage percentage.
                        - "disk_usage_percent": Disk usage percentage for the root partition.
                        Returns {"error": str} if data collection fails.
    """
    try:
        utils_ops_total.labels(operation="get_system_metrics").inc()
        return {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_usage_percent": psutil.disk_usage("/").percent,
        }
    except Exception as e:
        utils_errors_total.labels(operation="get_system_metrics").inc()
        return {"error": f"Failed to collect system metrics: {str(e)}"}


async def get_system_metrics_async() -> Dict[str, Any]:
    """
    Asynchronously collects system performance metrics.
    This is useful for non-blocking I/O operations in an async context.

    Returns:
        Dict[str, Any]: A dictionary with CPU, memory, and disk usage percentages,
                        or an error message if the collection fails.
    """
    try:
        utils_ops_total.labels(operation="get_system_metrics_async").inc()
        # Use asyncio.to_thread for CPU-bound tasks in an async context (Python 3.9+)
        # This prevents blocking the event loop.
        metrics = await asyncio.to_thread(
            lambda: {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_usage_percent": psutil.disk_usage("/").percent,
            }
        )
        return metrics
    except Exception as e:
        utils_errors_total.labels(operation="get_system_metrics_async").inc()
        return {"error": f"Failed to collect metrics asynchronously: {str(e)}"}


async def get_health_session() -> aiohttp.ClientSession:
    """Returns a reusable, global aiohttp client session."""
    global _HEALTH_SESSION
    async with _HEALTH_SESSION_LOCK:
        if _HEALTH_SESSION is None or _HEALTH_SESSION.closed:
            timeout = aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT)
            _HEALTH_SESSION = aiohttp.ClientSession(timeout=timeout)
    return _HEALTH_SESSION


async def close_health_session() -> None:
    """Closes the global aiohttp client session."""
    global _HEALTH_SESSION
    async with _HEALTH_SESSION_LOCK:
        if _HEALTH_SESSION is not None and not _HEALTH_SESSION.closed:
            await _HEALTH_SESSION.close()
            _HEALTH_SESSION = None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(aiohttp.ClientError),
)
async def check_service_health(
    url: str = "http://localhost:8080/health",
) -> Dict[str, Any]:
    """
    Checks the health status of a microservice's health endpoint.

    Args:
        url (str): The URL of the service's health endpoint.

    Returns:
        Dict[str, Any]: The JSON response from the health endpoint if successful,
                        or a dictionary with an error message if the request fails.

    Raises:
        aiohttp.ClientError: If the HTTP request fails after retries (e.g., connection error, timeout).
        aiohttp.ContentTypeError: If the response is not valid JSON.
    """
    with tracer.start_as_current_span("check_service_health"):
        utils_ops_total.labels(operation="check_service_health").inc()

        async with _HEALTH_CHECK_LIMITER:
            session = await get_health_session()
            headers = (
                {"Authorization": f"Bearer {os.getenv('HEALTH_AUTH_TOKEN')}"}
                if os.getenv("HEALTH_AUTH_TOKEN")
                else None
            )

            try:
                async with session.get(url, headers=headers) as response:
                    response.raise_for_status()

                    try:
                        return await response.json()
                    except aiohttp.ContentTypeError:
                        content = await response.text()
                        utils_errors_total.labels(
                            operation="check_service_health"
                        ).inc()
                        logger.error(f"Non-JSON response from {url}: {content}")
                        return {"error": f"Non-JSON response from {url}: {content}"}
            except aiohttp.ClientError as e:
                utils_errors_total.labels(operation="check_service_health").inc()
                logger.error(f"HTTP request failed for {url}: {e}")
                raise
            except Exception as e:
                utils_errors_total.labels(operation="check_service_health").inc()
                logger.error(f"An unexpected error occurred for {url}: {e}")
                raise


# --- New Class Definition and Registration Update ---
# FIXED: UtilsPlugin now correctly inherits from PluginBase and implements required async methods.


class UtilsPlugin(PluginBase):
    """
    Plugin class to expose utility functions as service methods.
    Inherits from PluginBase and implements required lifecycle methods.
    """

    # --- PluginBase Mandatory Methods ---
    async def initialize(self) -> None:
        """Mandatory PluginBase method."""
        logger.info("UtilsPlugin initialized.")

    async def start(self) -> None:
        """Mandatory PluginBase method."""
        pass  # No start logic required

    async def stop(self) -> None:
        """Mandatory PluginBase method."""
        await close_health_session()
        pass  # No stop logic required

    async def health_check(self) -> bool:
        """Mandatory PluginBase method."""
        return True  # Always healthy as it's a utility collection

    async def get_capabilities(self) -> List[str]:
        """Mandatory PluginBase method."""
        return [
            "random_chance",
            "get_system_metrics",
            "get_system_metrics_async",
            "check_service_health",
        ]

    # --- Utility Wrapper Methods (for registry access) ---
    @staticmethod
    def random_chance(*args, **kwargs):
        return random_chance(*args, **kwargs)

    @staticmethod
    def get_system_metrics(*args, **kwargs):
        return get_system_metrics(*args, **kwargs)

    @staticmethod
    async def get_system_metrics_async(*args, **kwargs):
        return await get_system_metrics_async(*args, **kwargs)

    @staticmethod
    async def get_health_session(*args, **kwargs):
        return await get_health_session(*args, **kwargs)

    @staticmethod
    async def close_health_session(*args, **kwargs):
        return await close_health_session(*args, **kwargs)

    @staticmethod
    async def check_service_health(*args, **kwargs):
        return await check_service_health(*args, **kwargs)


# Register as a plugin
# FIXED: Registration now uses the properly defined PluginBase class.
registry.register(
    kind=PlugInKind.CORE_SERVICE, name="Utils", version="1.0.0", author="Arbiter Team"
)(UtilsPlugin)
