# omnicore_engine/array_backend.py
"""
Unified array backend for the OmniCore engine.

Automatically selects the fastest available backend (CuPy → NumPy) and
exposes a **single, stable API** (`xp`) that works everywhere in the code-base.

Upgrades from the original file:
* **Lazy import** – CuPy is only imported when a GPU is actually present.
* **Thread-safe singleton** – one backend instance per process.
* **Prometheus metrics** for backend selection & fallback.
* **Health endpoint** (`await backend.health()`).
* **Explicit `cp` alias** (the test is trying to patch `cp`).
* **Graceful degradation** to NumPy with clear logging.
* **Fixed `cp` always defined** (as None if unavailable) for test patching.
* **Fixed `NameError: name 'threading' is not defined`** by adding `import threading`.
* **Added benchmarking support** with `BackendBenchmarker`.
* **Added validation and sanitization** functions.
* **Extended operations** to include all methods from the original (e.g., `astype`, `reshape`, `sum`, etc.).
* **Quantum and neuromorphic backends** with fallbacks.
* **Dask and Torch support** for distributed and ML workloads.
* **Async health check** for integration with message bus.
* **Structured logging** with structlog.
"""

from __future__ import annotations

import logging
import time
import json
import re
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
    Tuple,
)
from collections import defaultdict
import types
import numpy as np
import os
import threading  # FIXED: Added import threading to resolve NameError

# ---- App/Internal Imports ----
# Replace top-level config instantiation with a defensive lazy/fallback approach

try:
    from arbiter.config import ArbiterConfig  # type: ignore
except Exception:
    ArbiterConfig = None  # tests or minimal installs may not have arbiter available

# Create a safe settings object without importing or running Arbiter application init
if ArbiterConfig is not None:
    try:
        settings = ArbiterConfig()
    except Exception as e:
        # If ArbiterConfig raises during instantiation (missing globals like config_instance),
        # fall back to a minimal settings object to allow safe imports in tests.
        import types

        settings = types.SimpleNamespace(
            log_level="INFO",
            enable_array_backend_benchmarking=False,
        )
        # optional: log/debug the fallback if you have logger available later
else:
    import types

    settings = types.SimpleNamespace(
        log_level="INFO",
        enable_array_backend_benchmarking=False,
    )

try:
    from omnicore_engine.message_bus import ShardedMessageBus, MessageFilter, Message
except ImportError:
    # Allow tests to import without message_bus dependency
    ShardedMessageBus = None  # type: ignore
    MessageFilter = None  # type: ignore
    Message = None  # type: ignore

# Define these flags locally or import from a central constants file
CUPY_AVAILABLE = False
cp = None  # Explicitly define cp as None initially for test patching
try:
    import cupy as cp  # type: ignore

    CUPY_AVAILABLE = True
except ImportError:
    pass

DASK_AVAILABLE = False
try:
    import dask.array as da
    from dask.distributed import Client as DaskClient, LocalCluster

    DASK_AVAILABLE = True
except ImportError:
    pass

TORCH_AVAILABLE = False
try:
    import torch

    TORCH_AVAILABLE = True
except ImportError:
    pass

HAS_QISKIT = False
Aer = None
try:
    from qiskit import QuantumCircuit, transpile

    try:
        from qiskit_aer import Aer

        HAS_QISKIT = True
    except ImportError:
        logging.warning("Modern qiskit_aer not found. Quantum backend will be limited.")
except ImportError:
    logging.warning("Qiskit not found. Quantum backend will be unavailable.")

HAS_NENGO_LOIHI = False
try:
    import nengo_loihi

    HAS_NENGO_LOIHI = True
except ImportError:
    logging.warning("NengoLoihi not found. Neuromorphic backend will be unavailable.")


# FIXED: Centralized logging configuration to use the core logger.
# This ensures consistent logging throughout the application.
_using_structlog = False
try:
    from omnicore_engine.core import logger as core_logger

    logger = core_logger.bind(module="ArrayBackend")
    _using_structlog = True
except ImportError:
    logger = logging.getLogger(__name__)
    # Ensure logger.setLevel receives an integer level, not a string
    log_level = getattr(settings, "log_level", "INFO")
    if isinstance(log_level, str):
        log_level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(log_level)

# Structured logging with structlog if available
try:
    import structlog

    logger = structlog.get_logger(__name__).bind(module="ArrayBackend")
    _using_structlog = True
except ImportError:
    pass  # Fallback to standard logging


# Helper function to log with or without structlog
def _log_info(msg: str, **kwargs) -> None:
    """Helper to log info messages compatible with both logging and structlog"""
    if _using_structlog:
        logger.info(msg, **kwargs)
    else:
        logger.info(f"{msg} {' '.join(f'{k}={v}' for k, v in kwargs.items())}")


def _log_debug(msg: str, **kwargs) -> None:
    """Helper to log debug messages compatible with both logging and structlog"""
    if _using_structlog:
        logger.debug(msg, **kwargs)
    else:
        logger.debug(f"{msg} {' '.join(f'{k}={v}' for k, v in kwargs.items())}")

# --------------------------------------------------------------------------- #
#  Optional Prometheus
# --------------------------------------------------------------------------- #
try:
    from prometheus_client import Counter, Gauge

    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    Counter = Gauge = None

if _PROMETHEUS_AVAILABLE:
    METRIC_BACKEND_SELECTED = Counter(
        "omnicore_array_backend_selected_total",
        "Which backend was selected at runtime",
        ["backend"],
    )
    METRIC_BACKEND_FALLBACK = Counter(
        "omnicore_array_backend_fallback_total",
        "Number of times we fell back to NumPy",
        ["reason"],
    )
else:
    METRIC_BACKEND_SELECTED = None
    METRIC_BACKEND_FALLBACK = None


def _inc_selected(backend: str) -> None:
    if METRIC_BACKEND_SELECTED:
        try:
            METRIC_BACKEND_SELECTED.labels(backend=backend).inc()
        except Exception:
            pass


def _inc_fallback(reason: str) -> None:
    if METRIC_BACKEND_FALLBACK:
        try:
            METRIC_BACKEND_FALLBACK.labels(reason=reason).inc()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Backend singleton
# --------------------------------------------------------------------------- #
class _ArrayBackend:
    """Internal singleton – do NOT import directly."""

    _instance: Optional["_ArrayBackend"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "_ArrayBackend":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    # ------------------------------------------------------------------- #
    #  Initialization – lazy, safe, and observable
    # ------------------------------------------------------------------- #
    def _initialize(self) -> None:
        self.xp = None  # will be numpy or cupy
        self.is_gpu = False
        self.name = "unknown"

        # 1. Try CuPy
        try:
            global cp  # Use the module-level cp
            import cupy as _cp  # type: ignore

            # Quick sanity-check – create a tiny array
            test = _cp.zeros((2, 2), dtype=_cp.float32)
            del test
            self.xp = _cp
            cp = _cp  # Update module-level cp for patching
            self.is_gpu = True
            self.name = "cupy"
            _inc_selected("cupy")
            _log_info("Array backend selected.", backend="cupy")
        except Exception as e:  # pragma: no cover
            _inc_fallback("cupy_import_or_test_failed")
            _log_debug("CuPy unavailable, falling back to NumPy.", exc_info=e)

        # 2. Fallback to NumPy
        if self.xp is None:
            import numpy as _np  # type: ignore

            self.xp = _np
            cp = None  # Ensure cp is None if not CuPy
            self.is_gpu = False
            self.name = "numpy"
            _inc_selected("numpy")
            _log_info("Array backend selected.", backend="numpy")

    # ------------------------------------------------------------------- #
    #  Public health endpoint
    # ------------------------------------------------------------------- #
    async def health(self) -> Dict[str, Any]:
        """Return a snapshot of the backend state."""
        return {
            "backend": self.name,
            "is_gpu": self.is_gpu,
            "xp_module": self.xp.__name__,
            "xp_version": getattr(self.xp, "__version__", "unknown"),
        }


# --------------------------------------------------------------------------- #
#  Public API – import these from the package
# --------------------------------------------------------------------------- #
backend = _ArrayBackend()  # singleton instance
xp = backend.xp  # numpy or cupy module
is_gpu = backend.is_gpu


# --------------------------------------------------------------------------- #
#  Async health helper (mirrors other components)
# ------------------------------------------------------------------- #
async def health() -> Dict[str, Any]:
    """Convenient top-level health call."""
    return await backend.health()


# ---- Original methods (upgraded) ----
MAX_ARRAY_SIZE = 1_000_000_000  # 1 billion elements


def validate_array_size(shape):
    """
    Validates that the total number of elements in an array does not exceed a predefined limit.
    """
    total_elements = np.prod(shape)
    if total_elements > MAX_ARRAY_SIZE:
        raise ValueError(f"Array too large: {total_elements} elements")


def sanitize_array_input(data: Any, backend_module=None) -> Any:
    """
    Sanitizes and validates array input data to prevent security vulnerabilities.

    Args:
        data: Input data to sanitize (list, tuple, array, or number)
        backend_module: Optional backend module to use (defaults to np)

    Returns:
        Sanitized numpy array
    """
    if backend_module is None:
        backend_module = np

    if not isinstance(data, (list, tuple, np.ndarray, int, float)):
        raise TypeError(
            "Invalid array input type. Must be a list, tuple, number or numpy array."
        )

    # Convert and validate
    arr = backend_module.asarray(data)
    validate_array_size(arr.shape)

    # Check for suspicious values that could indicate an injection or unexpected data
    if arr.dtype == object:
        raise ValueError("Object arrays are not supported for security reasons.")

    return arr


class Benchmarker:
    """Benchmarking class for array operations."""

    def __init__(self):
        self.results: Dict[str, List[float]] = defaultdict(list)

    def run_benchmark(self, xp: Any, operation: str, func: Callable) -> None:
        start = time.time()
        func()
        duration = time.time() - start
        self.results[operation].append(duration)
        logger.debug(
            f"Benchmark for {operation}: {duration:.6f} seconds using {xp.__name__}"
        )

    def get_results(self) -> Dict[str, List[float]]:
        return dict(self.results)


class BackendBenchmarker:
    """
    A utility class to perform simple benchmarks on different array computation backends.
    It measures execution times for basic array operations.
    """

    def __init__(self):
        self.results: Dict[str, List[float]] = defaultdict(list)
        self.logger = logger.bind(sub_module="BackendBenchmarker")

    def run_benchmark(
        self,
        xp_module: Any,
        operation_name: str,
        test_func: Callable,
        iterations: int = 10,
    ) -> Optional[float]:
        """
        Runs a benchmark for a specific operation on a given backend.

        Args:
            xp_module (Any): The backend module (e.g., numpy, cupy, torch).
            operation_name (str): A descriptive name for the operation being benchmarked.
            test_func (Callable): A callable (function) that performs the operation.
                                  It should accept no arguments and return a result.
            iterations (int): Number of times to run the test_func for averaging.

        Returns:
            Optional[float]: The average execution time in seconds, or None if an error occurred.
        """
        times = []
        try:
            for _ in range(iterations):
                start_time = time.perf_counter()
                test_func()
                end_time = time.perf_counter()
                times.append(end_time - start_time)

            avg_time = sum(times) / len(times)
            self.results[f"{xp_module.__name__}_{operation_name}"].append(avg_time)
            self.logger.info(
                f"Benchmark '{operation_name}' on {xp_module.__name__}: Average time = {avg_time:.6f} seconds."
            )
            return avg_time
        except Exception as e:
            self.logger.error(
                f"Benchmark '{operation_name}' on {xp_module.__name__} failed: {e}",
                exc_info=True,
            )
            return None

    def get_results(self) -> Dict[str, List[float]]:
        """Returns all stored benchmark results."""
        return dict(self.results)


class ArrayBackend:
    """
    Unified array backend class with support for multiple backends.
    """

    def __init__(self, mode: str = "auto", enable_benchmarking: bool = False):
        self.mode = mode
        self.enable_benchmarking = enable_benchmarking
        self.benchmarker = Benchmarker() if enable_benchmarking else None
        self.xp = xp  # Use the global xp

        if self.mode == "auto":
            if CUPY_AVAILABLE:
                self.mode = "cupy"
            elif DASK_AVAILABLE:
                self.mode = "dask"
            elif TORCH_AVAILABLE:
                self.mode = "torch"
            else:
                self.mode = "numpy"

        logger.info(f"ArrayBackend initialized in {self.mode} mode.")

    def array(self, data: Any, dtype: Optional[Any] = None) -> Any:
        """
        Creates an array from input data using the current backend.
        Args:
            data (Any): Input data.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object.
        """
        sanitized = sanitize_array_input(data)
        if dtype:
            return self.xp.array(sanitized, dtype=dtype)
        return self.xp.array(sanitized)

    def zeros(
        self, shape: Union[int, Tuple[int, ...]], dtype: Optional[Any] = None
    ) -> Any:
        """
        Creates an array of zeros with the given shape and data type.
        Args:
            shape (Union[int, Tuple[int, ...]]): Shape of the array.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object filled with zeros.
        """
        validate_array_size(shape)
        return self.xp.zeros(shape, dtype=dtype)

    def ones(
        self, shape: Union[int, Tuple[int, ...]], dtype: Optional[Any] = None
    ) -> Any:
        """
        Creates an array of ones with the given shape and data type.
        Args:
            shape (Union[int, Tuple[int, ...]]): Shape of the array.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object filled with ones.
        """
        validate_array_size(shape)
        return self.xp.ones(shape, dtype=dtype)

    def full(
        self,
        shape: Union[int, Tuple[int, ...]],
        fill_value: Any,
        dtype: Optional[Any] = None,
    ) -> Any:
        """
        Creates an array filled with a specified value.
        Args:
            shape (Union[int, Tuple[int, ...]]): Shape of the array.
            fill_value (Any): Value to fill the array with.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object filled with the specified value.
        """
        validate_array_size(shape)
        return self.xp.full(shape, fill_value, dtype=dtype)

    def empty(
        self, shape: Union[int, Tuple[int, ...]], dtype: Optional[Any] = None
    ) -> Any:
        """
        Creates an empty array with the given shape and data type.
        Args:
            shape (Union[int, Tuple[int, ...]]): Shape of the array.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Empty array-like object.
        """
        validate_array_size(shape)
        return self.xp.empty(shape, dtype=dtype)

    def arange(
        self,
        start: int,
        stop: Optional[int] = None,
        step: int = 1,
        dtype: Optional[Any] = None,
    ) -> Any:
        """
        Returns evenly spaced values within a given interval.
        Args:
            start (int): Start of interval.
            stop (Optional[int]): End of interval.
            step (int): Spacing between values.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object of evenly spaced values.
        """
        return self.xp.arange(start, stop, step, dtype=dtype)

    def linspace(
        self,
        start: float,
        stop: float,
        num: int = 50,
        endpoint: bool = True,
        dtype: Optional[Any] = None,
    ) -> Any:
        """
        Returns evenly spaced numbers over a specified interval.
        Args:
            start (float): Starting value.
            stop (float): Ending value.
            num (int): Number of samples.
            endpoint (bool): Whether to include the endpoint.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object of evenly spaced numbers.
        """
        return self.xp.linspace(start, stop, num, endpoint=endpoint, dtype=dtype)

    def logspace(
        self,
        start: float,
        stop: float,
        num: int = 50,
        base: float = 10.0,
        dtype: Optional[Any] = None,
    ) -> Any:
        """
        Returns numbers spaced evenly on a log scale.
        Args:
            start (float): Starting value (base ** start).
            stop (float): Ending value (base ** stop).
            num (int): Number of samples.
            base (float): Base of the log space.
            dtype (Optional[Any]): Desired data type.
        Returns:
            Any: Array-like object of log-spaced numbers.
        """
        return self.xp.logspace(start, stop, num, base=base, dtype=dtype)

    def meshgrid(self, *xi: Any, indexing: str = "xy") -> List[Any]:
        """
        Returns coordinate matrices from coordinate vectors.
        Args:
            *xi (Any): Input coordinate vectors.
            indexing (str): Cartesian ('xy') or matrix ('ij') indexing.
        Returns:
            List[Any]: List of coordinate matrices.
        """
        return self.xp.meshgrid(*xi, indexing=indexing)

    def random_rand(self, *shape: int) -> Any:
        """
        Generates random numbers from a uniform distribution over [0, 1).
        Args:
            *shape (int): Shape of the output array.
        Returns:
            Any: Array-like object of random numbers.
        """
        validate_array_size(shape)
        return self.xp.random.rand(*shape)

    def random_randn(self, *shape: int) -> Any:
        """
        Generates random numbers from a standard normal distribution.
        Args:
            *shape (int): Shape of the output array.
        Returns:
            Any: Array-like object of random numbers.
        """
        validate_array_size(shape)
        return self.xp.random.randn(*shape)

    def random_randint(
        self,
        low: int,
        high: Optional[int] = None,
        size: Optional[Union[int, Tuple[int, ...]]] = None,
    ) -> Any:
        """
        Generates random integers from low (inclusive) to high (exclusive).
        Args:
            low (int): Lowest integer to be drawn.
            high (Optional[int]): One above the highest integer to be drawn.
            size (Optional[Union[int, Tuple[int, ...]]]): Output shape.
        Returns:
            Any: Array-like object of random integers.
        """
        if size:
            validate_array_size(size)
        return self.xp.random.randint(low, high, size)

    def dot(self, a: Any, b: Any) -> Any:
        """
        Computes the dot product of two arrays.
        Args:
            a (Any): First input array.
            b (Any): Second input array.
        Returns:
            Any: Dot product of the inputs.
        """
        return self.xp.dot(a, b)

    def matmul(self, a: Any, b: Any) -> Any:
        """
        Performs matrix multiplication.
        Args:
            a (Any): First input array.
            b (Any): Second input array.
        Returns:
            Any: Matrix product of the inputs.
        """
        return self.xp.matmul(a, b)

    def transpose(self, a: Any, axes: Optional[Tuple[int, ...]] = None) -> Any:
        """
        Transposes the array.
        Args:
            a (Any): Input array.
            axes (Optional[Tuple[int, ...]]): Axes to transpose.
        Returns:
            Any: Transposed array-like object.
        """
        return self.xp.transpose(a, axes)

    def concatenate(self, arrays: Tuple[Any, ...], axis: int = 0) -> Any:
        """
        Joins a sequence of arrays along an existing axis.
        Args:
            arrays (Tuple[Any, ...]): Arrays to concatenate.
            axis (int): Axis along which to concatenate.
        Returns:
            Any: Concatenated array-like object.
        """
        return self.xp.concatenate(arrays, axis=axis)

    def stack(self, arrays: Tuple[Any, ...], axis: int = 0) -> Any:
        """
        Joins a sequence of arrays along a new axis.
        Args:
            arrays (Tuple[Any, ...]): Arrays to stack.
            axis (int): Axis along which to stack.
        Returns:
            Any: Stacked array-like object.
        """
        return self.xp.stack(arrays, axis=axis)

    def vstack(self, arrays: Tuple[Any, ...]) -> Any:
        """
        Stacks arrays vertically (row-wise).
        Args:
            arrays (Tuple[Any, ...]): Arrays to stack.
        Returns:
            Any: Vertically stacked array-like object.
        """
        return self.xp.vstack(arrays)

    def hstack(self, arrays: Tuple[Any, ...]) -> Any:
        """
        Stacks arrays horizontally (column-wise).
        Args:
            arrays (Tuple[Any, ...]): Arrays to stack.
        Returns:
            Any: Horizontally stacked array-like object.
        """
        return self.xp.hstack(arrays)

    def mean(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Computes the arithmetic mean along the specified axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to compute the mean.
        Returns:
            Any: Mean of the array elements.
        """
        return self.xp.mean(a, axis=axis)

    def std(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Computes the standard deviation along the specified axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to compute the standard deviation.
        Returns:
            Any: Standard deviation of the array elements.
        """
        return self.xp.std(a, axis=axis)

    def var(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Computes the variance along the specified axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to compute the variance.
        Returns:
            Any: Variance of the array elements.
        """
        return self.xp.var(a, axis=axis)

    def max(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Returns the maximum of an array or maximum along an axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to find the maximum.
        Returns:
            Any: Maximum of the array elements.
        """
        return self.xp.max(a, axis=axis)

    def min(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Returns the minimum of an array or minimum along an axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to find the minimum.
        Returns:
            Any: Minimum of the array elements.
        """
        return self.xp.min(a, axis=axis)

    def argmax(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Returns the indices of the maximum values along an axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to find the indices.
        Returns:
            Any: Indices of the maximum values.
        """
        return self.xp.argmax(a, axis=axis)

    def argmin(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Returns the indices of the minimum values along an axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to find the indices.
        Returns:
            Any: Indices of the minimum values.
        """
        return self.xp.argmin(a, axis=axis)

    def sort(self, a: Any, axis: Optional[int] = -1) -> Any:
        """
        Returns a sorted copy of an array.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to sort.
        Returns:
            Any: Sorted array-like object.
        """
        return self.xp.sort(a, axis=axis)

    def argsort(self, a: Any, axis: Optional[int] = -1) -> Any:
        """
        Returns the indices that would sort an array.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to sort.
        Returns:
            Any: Indices that would sort the array.
        """
        return self.xp.argsort(a, axis=axis)

    def sin(self, x: Any) -> Any:
        """
        Computes the element-wise sine.
        Args:
            x (Any): Input array.
        Returns:
            Any: Sine of the input.
        """
        return self.xp.sin(x)

    def cos(self, x: Any) -> Any:
        """
        Computes the element-wise cosine.
        Args:
            x (Any): Input array.
        Returns:
            Any: Cosine of the input.
        """
        return self.xp.cos(x)

    def tan(self, x: Any) -> Any:
        """
        Computes the element-wise tangent.
        Args:
            x (Any): Input array.
        Returns:
            Any: Tangent of the input.
        """
        return self.xp.tan(x)

    def exp(self, x: Any) -> Any:
        """
        Computes the element-wise exponential.
        Args:
            x (Any): Input array.
        Returns:
            Any: Exponential of the input.
        """
        return self.xp.exp(x)

    def log(self, x: Any) -> Any:
        """
        Computes the element-wise natural logarithm.
        Args:
            x (Any): Input array.
        Returns:
            Any: Natural logarithm of the input.
        """
        return self.xp.log(x)

    def sqrt(self, x: Any) -> Any:
        """
        Computes the element-wise square root.
        Args:
            x (Any): Input array.
        Returns:
            Any: Square root of the input.
        """
        return self.xp.sqrt(x)

    def abs(self, x: Any) -> Any:
        """
        Computes the element-wise absolute value.
        Args:
            x (Any): Input array.
        Returns:
            Any: Absolute value of the input.
        """
        return self.xp.abs(x)

    def power(self, x: Any, p: Any) -> Any:
        """
        Raises elements in x to the power p.
        Args:
            x (Any): Input array.
            p (Any): Power to raise each element to.
        Returns:
            Any: Array with elements raised to the power p.
        """
        return self.xp.power(x, p)

    def clip(self, a: Any, a_min: Any, a_max: Any) -> Any:
        """
        Clips the values in an array to a given interval.
        Args:
            a (Any): Input array.
            a_min (Any): Minimum value.
            a_max (Any): Maximum value.
        Returns:
            Any: Clipped array-like object.
        """
        return self.xp.clip(a, a_min, a_max)

    def unique(self, a: Any) -> Any:
        """
        Finds the unique elements of an array.
        Args:
            a (Any): Input array.
        Returns:
            Any: Unique elements.
        """
        return self.xp.unique(a)

    def where(self, condition: Any, x: Any, y: Any) -> Any:
        """
        Returns elements chosen from x or y depending on condition.
        Args:
            condition (Any): Condition array.
            x (Any): Values from which to choose if condition is true.
            y (Any): Values from which to choose if condition is false.
        Returns:
            Any: Array with elements from x or y.
        """
        return self.xp.where(condition, x, y)

    def cumsum(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Returns the cumulative sum of the elements along a given axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to compute the cumulative sum.
        Returns:
            Any: Cumulative sum of the array elements.
        """
        return self.xp.cumsum(a, axis=axis)

    def cumprod(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Returns the cumulative product of the elements along a given axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which to compute the cumulative product.
        Returns:
            Any: Cumulative product of the array elements.
        """
        return self.xp.cumprod(a, axis=axis)

    def diff(self, a: Any, n: int = 1, axis: int = -1) -> Any:
        """
        Calculates the n-th discrete difference along the given axis.
        Args:
            a (Any): Input array.
            n (int): The number of times values are differenced.
            axis (int): The axis along which the difference is computed.
        Returns:
            Any: The n-th differences.
        """
        return self.xp.diff(a, n=n, axis=axis)

    def trapz(
        self, y: Any, x: Optional[Any] = None, dx: float = 1.0, axis: int = -1
    ) -> Any:
        """
        Integrates y along the given axis using the composite trapezoidal rule.
        Args:
            y (Any): Input array to integrate.
            x (Optional[Any]): The sample points corresponding to y.
            dx (float): The spacing between sample points.
            axis (int): The axis along which to integrate.
        Returns:
            Any: Definite integral as approximated by trapezoidal rule.
        """
        return self.xp.trapz(y, x=x, dx=dx, axis=axis)

    def fft(self, a: Any, n: Optional[int] = None, axis: int = -1) -> Any:
        """
        Computes the one-dimensional discrete Fourier Transform.
        Args:
            a (Any): Input array.
            n (Optional[int]): Length of the transformed axis.
            axis (int): Axis over which to compute the FFT.
        Returns:
            Any: The transformed array.
        """
        return self.xp.fft.fft(a, n=n, axis=axis)

    def ifft(self, a: Any, n: Optional[int] = None, axis: int = -1) -> Any:
        """
        Computes the one-dimensional inverse discrete Fourier Transform.
        Args:
            a (Any): Input array.
            n (Optional[int]): Length of the transformed axis.
            axis (int): Axis over which to compute the IFFT.
        Returns:
            Any: The inverse transformed array.
        """
        return self.xp.fft.ifft(a, n=n, axis=axis)

    def fftfreq(self, n: int, d: float = 1.0) -> Any:
        """
        Returns the Discrete Fourier Transform sample frequencies.
        Args:
            n (int): Window length.
            d (float): Sample spacing.
        Returns:
            Any: Array of sample frequencies.
        """
        return self.xp.fft.fftfreq(n, d=d)

    def rfftfreq(self, n: int, d: float = 1.0) -> Any:
        """
        Returns the Discrete Fourier Transform sample frequencies for real input.
        Args:
            n (int): Window length.
            d (float): Sample spacing.
        Returns:
            Any: Array of sample frequencies for real input.
        """
        return self.xp.fft.rfftfreq(n, d=d)

    def linalg_svd(self, a: Any, full_matrices: bool = True) -> Tuple[Any, Any, Any]:
        """
        Computes the singular value decomposition of a matrix.
        Args:
            a (Any): Input matrix.
            full_matrices (bool): If True, u and v have the shapes (..., M, M) and (..., N, N).
        Returns:
            Tuple[Any, Any, Any]: U, S, Vh matrices.
        """
        return self.xp.linalg.svd(a, full_matrices=full_matrices)

    def linalg_eig(self, a: Any) -> Tuple[Any, Any]:
        """
        Computes the eigenvalues and right eigenvectors of a square array.
        Args:
            a (Any): Input matrix.
        Returns:
            Tuple[Any, Any]: Eigenvalues and eigenvectors.
        """
        return self.xp.linalg.eig(a)

    def linalg_inv(self, a: Any) -> Any:
        """
        Computes the (multiplicative) inverse of a matrix.
        Args:
            a (Any): Input matrix.
        Returns:
            Any: Inverse matrix.
        """
        return self.xp.linalg.inv(a)

    def linalg_norm(
        self, x: Any, ord: Optional[Any] = None, axis: Optional[int] = None
    ) -> Any:
        """
        Computes the matrix or vector norm.
        Args:
            x (Any): Input array.
            ord (Optional[Any]): Order of the norm.
            axis (Optional[int]): Axis over which to compute the norm.
        Returns:
            Any: Norm of the matrix or vector.
        """
        return self.xp.linalg.norm(x, ord=ord, axis=axis)

    def to_device(self, a: Any, device: str = "cpu") -> Any:
        """
        Moves the array to the specified device (if supported by backend).
        Args:
            a (Any): Input array.
            device (str): Target device ('cpu' or 'gpu').
        Returns:
            Any: Array on the specified device.
        """
        if self.mode == "cupy" and device == "gpu":
            return cp.asarray(a)
        elif self.mode == "torch" and TORCH_AVAILABLE:
            return a.to(device)
        return a  # Fallback for other backends

    def from_device(self, a: Any) -> np.ndarray:
        """
        Moves the array back to CPU and converts to NumPy array.
        Args:
            a (Any): Input array.
        Returns:
            np.ndarray: NumPy array on CPU.
        """
        if hasattr(a, "get"):
            return a.get()  # CuPy
        elif hasattr(a, "cpu"):
            return a.cpu().numpy()  # PyTorch
        return np.asarray(a)

    def asnumpy(self, a: Any) -> np.ndarray:
        """
        Converts the array to a NumPy array.
        Args:
            a (Any): Input array.
        Returns:
            np.ndarray: NumPy array.
        """
        if self.mode == "cupy" and CUPY_AVAILABLE:
            return cp.asnumpy(a)
        elif self.mode == "torch" and TORCH_AVAILABLE:
            return a.cpu().numpy()
        elif self.mode == "dask" and DASK_AVAILABLE:
            return a.compute()
        return np.asarray(a)

    def __init__(
        self,
        mode="numpy",
        use_gpu=False,
        use_dask=False,
        use_quantum=False,
        use_neuromorphic=False,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the ArrayBackend with the specified mode and hardware preferences.

        Args:
            mode (str): Backend mode ("numpy", "cupy", "dask", "torch", "quantum", "neuromorphic").
            use_gpu (bool): Whether to use GPU with CuPy.
            use_dask (bool): Whether to use Dask for distributed computing.
            use_quantum (bool): Whether to use Qiskit for quantum computing.
            use_neuromorphic (bool): Whether to use NengoLoihi for neuromorphic computing.
            logger (Optional[logging.Logger]): Logger instance to use. If None, a new one is created.
        """
        self.logger = logger if logger else logging.getLogger(__name__)
        self.mode = mode
        self.use_gpu = use_gpu and CUPY_AVAILABLE
        self.use_dask = use_dask and DASK_AVAILABLE
        self.use_quantum = use_quantum and HAS_QISKIT and (Aer is not None)
        self.use_neuromorphic = use_neuromorphic and HAS_NENGO_LOIHI
        self.xp = self._init_backend()
        self.benchmarker = BackendBenchmarker()
        # Control benchmarking overhead with a setting
        self.enable_benchmarking = getattr(
            settings, "enable_array_backend_benchmarking", False
        )
        if self.enable_benchmarking:
            self.logger.info("ArrayBackend benchmarking is enabled.")
        else:
            self.logger.info("ArrayBackend benchmarking is disabled.")

        # Initialize message_bus to None, it will be set by OmniCoreEngine
        self.message_bus: Optional[ShardedMessageBus] = None
        self.logger.info(
            f"ArrayBackend initialized with mode: {self.mode}, GPU: {self.use_gpu}, Dask: {self.use_dask}, Quantum: {self.use_quantum}, Neuromorphic: {self.use_neuromorphic}"
        )

    def set_message_bus(self, message_bus: ShardedMessageBus):
        """
        Sets the ShardedMessageBus instance for the ArrayBackend to communicate.
        Subscribes to relevant computation task topics.
        """
        self.message_bus = message_bus
        if self.message_bus:
            computation_filter = MessageFilter(
                lambda payload: isinstance(payload, dict) and "type" in payload
            )
            # FIXED: Decoupled message processing from the core ArrayBackend class.
            # A new dedicated handler function is introduced to maintain separation of concerns.
            self.message_bus.subscribe(
                re.compile(r"computation\.task\..*"),
                self._message_bus_handler,
                filter=computation_filter,
            )
            self.logger.info("ArrayBackend subscribed to computation.task.* topics.")
        else:
            self.logger.warning(
                "Message bus not provided to ArrayBackend. Inter-service computation will not work."
            )

    async def _message_bus_handler(self, message: Message):
        """
        FIXED: A new, dedicated message bus handler to handle incoming computation tasks from the message bus.
        This approach decouples the ArrayBackend's core logic from the message bus transport layer.
        """
        if not self.message_bus:
            self.logger.error(
                "Message bus not set in ArrayBackend. Cannot handle computation tasks."
            )
            return

        self.logger.info(
            f"Received computation task on topic: {message.topic}",
            trace_id=message.trace_id,
        )

        try:
            payload_data = message.payload
            # Decrypt payload if encrypted
            if message.encrypted and self.message_bus.encryption:
                try:
                    decrypted_bytes = self.message_bus.encryption.decrypt(
                        message.payload.encode("utf-8")
                    )
                    payload_data = json.loads(decrypted_bytes.decode("utf-8"))
                    self.logger.debug("Decrypted message payload for computation task.")
                except Exception as e:
                    self.logger.error(
                        f"Failed to decrypt message payload for {message.topic}: {e}",
                        trace_id=message.trace_id,
                    )
                    await self.message_bus.publish(
                        f"computation.error.{message.trace_id}",
                        {
                            "error": f"Decryption failed: {str(e)}",
                            "original_topic": message.topic,
                        },
                        priority=10,
                    )
                    return

            if not isinstance(payload_data, dict):
                raise ValueError(
                    f"Invalid payload format for computation task: expected dict, got {type(payload_data)}"
                )

            result = None
            error_message = None

            # Sanitize data before processing
            if "data" in payload_data:
                payload_data["data"] = sanitize_array_input(payload_data["data"])

            if (
                message.topic == "computation.task.array"
                or payload_data.get("type") == "array_creation"
            ):
                data = payload_data.get("data")
                dtype = payload_data.get("dtype")
                if data is None:
                    raise ValueError("Missing 'data' for array creation task.")
                result = self.array(data, dtype)
                self.logger.info("Performed array creation computation.")
            elif (
                message.topic == "computation.task.normal"
                or payload_data.get("type") == "normal_distribution"
            ):
                loc = payload_data.get("loc", 0.0)
                scale = payload_data.get("scale", 1.0)
                size = payload_data.get("size")
                if size is None:
                    raise ValueError("Missing 'size' for normal distribution task.")
                result = self.normal(loc, scale, size)
                self.logger.info("Performed normal distribution computation.")
            elif (
                message.topic == "computation.task.sum"
                or payload_data.get("type") == "sum_operation"
            ):
                data = payload_data.get("data")
                axis = payload_data.get("axis")
                if data is None:
                    raise ValueError("Missing 'data' for sum operation.")
                arr_data = self.array(data)
                result = self.sum(arr_data, axis)
                self.logger.info("Performed sum operation computation.")
            elif (
                message.topic == "computation.task.reshape"
                or payload_data.get("type") == "reshape_operation"
            ):
                data = payload_data.get("data")
                newshape = payload_data.get("newshape")
                if data is None or newshape is None:
                    raise ValueError(
                        "Missing 'data' or 'newshape' for reshape operation."
                    )
                arr_data = self.array(data)
                result = self.reshape(arr_data, newshape)
                self.logger.info("Performed reshape operation computation.")
            else:
                error_message = f"Unsupported computation task type or topic: {message.topic} / {payload_data.get('type')}"
                self.logger.warning(error_message)

            if error_message:
                await self.message_bus.publish(
                    f"computation.error.{message.trace_id}",
                    {"error": error_message, "original_topic": message.topic},
                    priority=10,
                )
            else:
                await self.message_bus.publish(
                    f"computation.result.{message.trace_id}",
                    (
                        self.asnumpy(result).tolist()
                        if hasattr(result, "tolist")
                        else result
                    ),
                    priority=5,
                    encrypt=True,
                )
                self.logger.info(
                    f"Published computation result for {message.topic}",
                    trace_id=message.trace_id,
                )

        except Exception as e:
            self.logger.error(
                f"ArrayBackend computation failed for {message.topic}: {e}",
                exc_info=True,
                trace_id=message.trace_id,
            )
            await self.message_bus.publish(
                f"computation.error.{message.trace_id}",
                {"error": str(e), "original_topic": message.topic},
                priority=10,
            )

    def _init_backend(self):
        """
        Initializes the appropriate computational backend based on mode and availability.
        Logs backend selection and fallback scenarios.
        """
        if self.mode == "cupy" and self.use_gpu and CUPY_AVAILABLE:
            self.logger.info("ArrayBackend: Initializing CuPy backend.")
            return cp
        if self.mode == "dask" and self.use_dask and DASK_AVAILABLE:
            try:
                if not hasattr(self, "_dask_client") or (
                    self._dask_client and self._dask_client.status == "closed"
                ):
                    self.logger.info(
                        "ArrayBackend: Initializing Dask LocalCluster and Client."
                    )
                    self.cluster = LocalCluster(
                        n_workers=os.cpu_count() or 1, threads_per_worker=1
                    )
                    self._dask_client = DaskClient(self.cluster)
                    self.logger.info(
                        f"ArrayBackend: Dask client initialized: {self._dask_client.dashboard_link}"
                    )
                else:
                    self.logger.info("ArrayBackend: Reusing existing Dask client.")
                return da
            except Exception as e:
                self.logger.warning(
                    f"ArrayBackend: Dask initialization failed: {e}. Falling back to NumPy.",
                    exc_info=True,
                )
        if self.mode == "torch" and TORCH_AVAILABLE:
            self.logger.info("ArrayBackend: Initializing PyTorch backend (CPU).")
            return torch
        if self.mode == "quantum" and self.use_quantum:
            self.logger.info("ArrayBackend: Initializing Quantum (Qiskit) backend.")
            return self._quantum_backend()
        if self.mode == "neuromorphic" and self.use_neuromorphic and HAS_NENGO_LOIHI:
            self.logger.info(
                "ArrayBackend: Initializing Neuromorphic (NengoLoihi) backend."
            )
            return self._neuromorphic_backend()

        self.logger.info(
            "ArrayBackend: Falling back to NumPy backend (default or preferred backend unavailable)."
        )
        return np

    def _quantum_backend(self) -> Type[np.ndarray]:
        """
        Creates a quantum backend using Qiskit. This provides a NumPy-like interface for array operations,
        but internally leverages Qiskit for random number generation and quantum simulations.
        """

        def quantum_normal(loc=0.0, scale=1.0, size=None):
            """Generates normally distributed random numbers using Qiskit for quantum randomness."""
            if not HAS_QISKIT or Aer is None:
                self.logger.warning(
                    "Quantum backend: Qiskit Aer not available, quantum_normal falling back to NumPy."
                )
                return np.random.normal(loc, scale, size)

            size_val = size if size is not None else 1
            results = []
            num_samples = size_val if isinstance(size_val, int) else 1

            for i in range(num_samples):
                try:
                    qc = QuantumCircuit(1, 1)
                    qc.h(0)
                    qc.measure(0, 0)

                    backend = Aer.get_backend("aer_simulator")
                    tqc = transpile(qc, backend)
                    job = backend.run(tqc, shots=1, memory=True)

                    measurement_result = job.result().get_memory(tqc)[0]
                    measurement = int(measurement_result)

                    sample = (measurement * 2 - 1) * scale + loc
                    results.append(sample)
                except Exception as e:
                    self.logger.warning(
                        f"Quantum backend simulation for sample {i+1}/{num_samples} failed: {e}. Falling back to NumPy for this sample.",
                        exc_info=True,
                    )
                    results.append(np.random.normal(loc, scale))

            return (
                np.array(results)
                if isinstance(size_val, int)
                else (results[0] if results else np.random.normal(loc, scale))
            )

        QuantumModule = type(
            "QuantumBackend",
            (object,),
            {
                "normal": quantum_normal,
                "zeros": np.zeros,
                "array": np.array,
                "cumsum": np.cumsum,
                "clip": np.clip,
                "random": types.SimpleNamespace(normal=quantum_normal),
            },
        )
        return QuantumModule()

    def _neuromorphic_backend(self) -> Type[np.ndarray]:
        """
        Creates a neuromorphic backend using NengoLoihi. Provides a NumPy-like interface for array operations,
        internally using Nengo for simulations of neural networks.
        """

        def neuromorphic_normal(loc=0.0, scale=1.0, size=None):
            """Generates normally distributed random numbers using a NengoLoihi simulator."""
            if not HAS_NENGO_LOIHI:
                self.logger.warning(
                    "Neuromorphic backend: NengoLoihi not available, neuromorphic_normal falling back to NumPy."
                )
                return np.random.normal(loc, scale, size)

            size_val = size if size is not None else 1
            num_steps = size_val if isinstance(size_val, int) else 1

            try:
                import nengo
                from nengo_loihi import Simulator

                with nengo.Network(seed=np.random.randint(10000)) as net:
                    noise_node = nengo.Node(
                        nengo.processes.WhiteNoise(
                            dist=nengo.dists.Gaussian(loc, scale)
                        )
                    )
                    p_noise = nengo.Probe(noise_node)

                sim_context = (
                    Simulator(net) if HAS_NENGO_LOIHI else nengo.Simulator(net)
                )

                with sim_context as sim:
                    sim.run(sim.dt * num_steps)

                samples = sim.data[p_noise].flatten()

                if isinstance(size_val, int):
                    return samples[:size_val]
                else:
                    return samples[0] if samples.size > 0 else loc

            except Exception as e:
                self.logger.warning(
                    f"Neuromorphic backend simulation failed: {e}. Falling back to NumPy for random numbers.",
                    exc_info=True,
                )
                return np.random.normal(loc, scale, size)

        NeuromorphicModule = type(
            "NeuromorphicBackend",
            (object,),
            {
                "normal": neuromorphic_normal,
                "zeros": np.zeros,
                "array": np.array,
                "cumsum": np.cumsum,
                "clip": np.clip,
                "random": types.SimpleNamespace(normal=neuromorphic_normal),
            },
        )
        return NeuromorphicModule()

    def array(self, d: Any, t: Optional[Any] = None) -> Any:
        """
        Creates an array using the current backend.
        Args:
            d (Any): Data to create the array from.
            t (Optional[Any]): Desired data type (e.g., np.float32, torch.float32).
        Returns:
            Any: An array-like object from the current backend.
        """
        validated_data = sanitize_array_input(d)

        if self.enable_benchmarking:
            self.benchmarker.run_benchmark(
                self.xp,
                "array_creation",
                lambda: self.xp.array(validated_data, dtype=t),
            )

        if self.mode == "torch" and TORCH_AVAILABLE:
            return self.xp.tensor(validated_data, dtype=t)
        if hasattr(self.xp, "array"):
            return self.xp.array(validated_data, dtype=t)

        self.logger.warning(
            f"Array creation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        return np.array(validated_data, dtype=t)

    def zeros(self, s: Union[int, Tuple[int, ...]], t: Optional[Any] = None) -> Any:
        """
        Creates a zero-filled array of a given shape using the current backend.
        Args:
            s (Union[int, Tuple[int, ...]]): Shape of the array.
            t (Optional[Any]): Desired data type.
        Returns:
            Any: A zero-filled array-like object from the current backend.
        """
        validate_array_size(s)

        if self.enable_benchmarking:
            self.benchmarker.run_benchmark(
                self.xp, "zeros_creation", lambda: self.xp.zeros(s, dtype=t)
            )

        if self.mode == "dask" and DASK_AVAILABLE:
            return self.xp.zeros(s, dtype=t)
        elif hasattr(self.xp, "zeros"):
            if self.mode == "torch" and TORCH_AVAILABLE:
                return self.xp.zeros(s, dtype=t)
            return self.xp.zeros(s, dtype=t)

        self.logger.warning(
            f"Zeros creation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        return np.zeros(s, dtype=t)

    def normal(
        self,
        l: float = 0.0,
        s: float = 1.0,
        z: Optional[Union[int, Tuple[int, ...]]] = None,
    ) -> Any:
        """
        Generates normally distributed random numbers (mean l, std dev s) of shape z.
        Args:
            l (float): Mean of the distribution.
            s (float): Standard deviation of the distribution.
            z (Optional[Union[int, Tuple[int, ...]]]): Shape of the output array.
        Returns:
            Any: Array-like object with normally distributed random numbers.
        """
        if isinstance(z, (int, tuple)):
            validate_array_size(z if isinstance(z, tuple) else (z,))

        if self.enable_benchmarking:
            self.benchmarker.run_benchmark(
                self.xp, "normal_generation", lambda: self.normal(l, s, z), iterations=5
            )

        if hasattr(self.xp, "random") and hasattr(self.xp.random, "normal"):
            return self.xp.random.normal(l, s, z)
        elif hasattr(self.xp, "normal"):
            return self.xp.normal(l, s, z)

        self.logger.warning(
            f"Normal distribution generation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        return np.random.normal(l, s, z)

    def cumsum(self, a: Any, x: Optional[int] = None) -> Any:
        """
        Computes the cumulative sum of an array `a` along a given axis `x`.
        Args:
            a (Any): Input array.
            x (Optional[int]): Axis along which the cumulative sum is computed.
        Returns:
            Any: An array-like object with the cumulative sum.
        """
        if self.enable_benchmarking:
            self.benchmarker.run_benchmark(
                self.xp,
                "cumsum_operation",
                lambda: self.cumsum(self.xp.array([1.0] * 1000), x),
            )

        if hasattr(self.xp, "cumsum"):
            if self.mode == "torch" and TORCH_AVAILABLE:
                return self.xp.cumsum(a, dim=x) if x is not None else self.xp.cumsum(a)
            return self.xp.cumsum(a, axis=x)

        self.logger.warning(
            f"Cumsum operation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        return np.cumsum(a, axis=x)

    def clip(self, a: Any, m: Union[int, float], M: Union[int, float]) -> Any:
        """
        Clips (limits) the values in an array `a` to be within the range [m, M].
        Args:
            a (Any): Input array.
            m (Union[int, float]): Minimum value.
            M (Union[int, float]): Maximum value.
        Returns:
            Any: Clipped array-like object.
        """
        if self.enable_benchmarking:
            self.benchmarker.run_benchmark(
                self.xp,
                "clip_operation",
                lambda: self.clip(self.xp.array([-1.0, 0.5, 2.0]), m, M),
            )

        if hasattr(self.xp, "clip"):
            return self.xp.clip(a, m, M)

        self.logger.warning(
            f"Clip operation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        return np.clip(a, m, M)

    def asnumpy(self, a: Any) -> np.ndarray:
        """
        Converts an array from the current backend to a NumPy array.
        Handles different backend types (CuPy, Dask, PyTorch).
        Args:
            a (Any): An array-like object from the current backend.
        Returns:
            np.ndarray: A NumPy array representation of the input.
        """
        if self.enable_benchmarking:
            self.benchmarker.run_benchmark(
                self.xp,
                "asnumpy_conversion",
                lambda: self.asnumpy(self.array([1.0] * 1000)),
            )

        if self.mode == "cupy" and CUPY_AVAILABLE and isinstance(a, cp.ndarray):
            return cp.asnumpy(a)
        if self.mode == "dask" and DASK_AVAILABLE and isinstance(a, da.Array):
            return a.compute()
        if self.mode == "torch" and TORCH_AVAILABLE and isinstance(a, torch.Tensor):
            return a.detach().cpu().numpy()

        if not isinstance(a, np.ndarray):
            self.logger.debug(
                f"Converting non-NumPy array from {type(a)} to NumPy (mode: {self.mode})."
            )
        return np.array(a)

    def astype(self, a: Any, dtype: Any) -> Any:
        """
        Converts an array to a specified data type using the current backend.
        Args:
            a (Any): Input array.
            dtype (Any): Desired data type.
        Returns:
            Any: Array-like object with the new data type.
        """
        if self.enable_benchmarking:
            # FIX: Call a non-recursive operation for benchmarking
            self.benchmarker.run_benchmark(
                self.xp,
                "astype_operation",
                lambda: self.xp.array([1, 2, 3]).astype("float32"),
            )

        if hasattr(a, "astype"):
            return a.astype(dtype)
        self.logger.warning(
            f"Astype operation not directly supported by array type {type(a)} or current backend ({self.mode}), falling back to NumPy."
        )
        return self.asnumpy(a).astype(dtype)

    def reshape(self, a: Any, newshape: Union[int, Tuple[int, ...]]) -> Any:
        """
        Gives a new shape to an array without changing its data.
        Args:
            a (Any): Input array.
            newshape (Union[int, Tuple[int, ...]]): The new shape.
        Returns:
            Any: Reshaped array-like object.
        """
        if self.enable_benchmarking:
            # FIX: Call a non-recursive operation for benchmarking
            self.benchmarker.run_benchmark(
                self.xp,
                "reshape_operation",
                lambda: self.xp.arange(100).reshape((10, 10)),
            )

        if hasattr(a, "reshape"):
            return a.reshape(newshape)
        self.logger.warning(
            f"Reshape operation not directly supported by array type {type(a)} or current backend ({self.mode}), falling back to NumPy."
        )
        return self.asnumpy(a).reshape(newshape)

    def sum(self, a: Any, axis: Optional[int] = None) -> Any:
        """
        Calculates the sum of array elements over a given axis.
        Args:
            a (Any): Input array.
            axis (Optional[int]): Axis along which the sum is computed.
        Returns:
            Any: The sum of array elements.
        """
        if self.enable_benchmarking:
            # FIX: Call a non-recursive operation for benchmarking
            self.benchmarker.run_benchmark(
                self.xp,
                "sum_operation",
                lambda: self.xp.sum(self.xp.random.rand(100, 100), axis=0),
            )

        if hasattr(self.xp, "sum"):
            if self.mode == "torch" and TORCH_AVAILABLE:
                return self.xp.sum(a, dim=axis) if axis is not None else self.xp.sum(a)
            return self.xp.sum(a, axis=axis)

        logger.warning(
            f"Sum operation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        self.logger.warning(
            f"Sum operation not directly supported by current backend ({self.mode}), falling back to NumPy."
        )
        return self.asnumpy(a).sum(axis=axis)

    def get_benchmarking_results(self) -> Dict[str, List[float]]:
        """
        Retrieves the aggregated benchmarking results for all operations performed
        on the current backend.
        """
        return self.benchmarker.get_results() if self.benchmarker else {}


# --- End of File ---
