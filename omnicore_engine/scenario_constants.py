import logging
from collections.abc import Mapping
from multiprocessing import (
    Lock as ProcessSafeLock,
)  # To avoid name collision with standard Lock
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator

if TYPE_CHECKING:
    try:
        from omnicore_engine.metrics import Counter
    except ImportError:
        Counter = None

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)


# Pydantic models for validation
class ScenarioMetric(BaseModel):
    """Pydantic model for validating scenario metrics."""

    description: str = Field(..., description="Description of the metric")
    default_value: float = Field(..., description="Default value as a float")
    unit: str = Field(..., description="Unit of measurement")
    range: List[float] = Field(..., description="Valid range as a list of floats")
    aggregation_method: str = Field(
        ..., description="Aggregation method for multiverse"
    )

    @validator("range")
    def validate_range(cls, v):
        """
        Validates that the range is a list of two floats where the first element is
        less than or equal to the second.
        """
        if len(v) != 2 or v[0] > v[1]:
            raise ValueError("Range must be a list of two floats where min <= max")
        return v


class ScenarioTemplate(BaseModel):
    """Pydantic model for validating scenario templates."""

    impact: float = Field(..., description="Impact factor as a float")
    label: str = Field(..., description="Human-readable label")
    active: bool = Field(..., description="Whether the scenario is active")
    description: str = Field(..., description="Detailed description")
    priority: float = Field(
        ..., ge=0.0, le=1.0, description="Priority for DecisionOptimizer"
    )


class TrackedDict(Mapping):
    """Immutable dictionary that tracks access to metrics or templates using singleton Prometheus counters.

    PERFORMANCE NOTE: Uses ProcessSafeLock() which is called on every __getitem__ access.
    This can cause lock contention across processes on hot paths. Consider:
    - Using thread-local or process-local counters if multi-process access is not required
    - Caching frequently accessed items to reduce lock contention
    - Monitoring lock wait times in production to identify bottlenecks
    """

    _metrics_counter: Optional["Counter"] = None
    _templates_counter: Optional["Counter"] = None
    _lock = ProcessSafeLock()  # Process-safe lock for Counter initialization
    _counter_class: type = (
        None  # Injectable Counter class, defaults to app.metrics.Counter
    )

    @classmethod
    def set_counter_class(cls, counter_class: type) -> None:
        """
        Set a custom Counter class for metric tracking.

        Args:
            counter_class: The Counter class to use (must support labels and inc methods).
        """
        cls._counter_class = counter_class

    def __init__(self, data: Dict, is_metrics: bool = False):
        """
        Initialize TrackedDict with data and an explicit metrics flag.

        Args:
            data (Dict): The dictionary to wrap.
            is_metrics (bool): True if tracking metrics, False for templates.
        """
        self._data = dict(data)
        self._is_metrics = is_metrics

    def __getitem__(self, key: str) -> Any:
        # Delayed import to avoid circular dependency and ensure metrics are initialized
        try:
            from omnicore_engine.metrics import get_or_create_counter
        except ImportError:
            # If metrics module is not available, just return the data without tracking
            return self._data[key]

        with self._lock:  # Ensure process-safe Counter initialization
            if self._is_metrics:
                if self._metrics_counter is None:
                    self._metrics_counter = get_or_create_counter(
                        "omnicore_scenario_metrics_accessed_total",
                        "Total accesses to scenario metrics",
                        ["metric_name"],
                    )
                counter = self._metrics_counter
                label = {"metric_name": key}
            else:
                if self._templates_counter is None:
                    self._templates_counter = get_or_create_counter(
                        "omnicore_scenario_templates_accessed_total",
                        "Total accesses to scenario templates",
                        ["template_name"],
                    )
                counter = self._templates_counter
                label = {"template_name": key}

        try:
            value = self._data[key]
            counter.labels(**label).inc()
            return value
        except KeyError:
            logger.warning(
                f"Attempted to access non-existent key '{key}' in {'metrics' if self._is_metrics else 'templates'}"
            )
            raise KeyError(
                f"Key '{key}' not found in {'metrics' if self._is_metrics else 'templates'}"
            )

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


# Explicitly export public symbols
__all__ = ["ScenarioMetric", "ScenarioTemplate", "TrackedDict"]
