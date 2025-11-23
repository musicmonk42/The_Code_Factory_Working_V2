import logging
import os
import sys
from typing import Dict

import pytest
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from pytest_mock import MockerFixture

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Sample environment variables for testing
SAMPLE_ENV = {"ENVIRONMENT": "test", "CLUSTER_NAME": "test-cluster"}


@pytest.fixture(autouse=True)
def setup_env(mocker: MockerFixture):
    """Set up environment variables and reload metrics module."""
    # Set environment variables
    for key, value in SAMPLE_ENV.items():
        mocker.patch.dict(os.environ, {key: value})

    # Force reload of the metrics module to pick up new env vars
    if "arbiter.meta_learning_orchestrator.metrics" in sys.modules:
        del sys.modules["arbiter.meta_learning_orchestrator.metrics"]

    yield

    # Clean up
    for key in SAMPLE_ENV:
        os.environ.pop(key, None)


@pytest.fixture
def metric_registry():
    """Fixture for MetricRegistry instance."""
    from arbiter.meta_learning_orchestrator.metrics import MetricRegistry

    return MetricRegistry()


def parse_metrics_output(metrics_text: str) -> Dict[str, float]:
    """Helper to parse Prometheus metrics output into a dictionary."""
    metrics = {}
    for line in metrics_text.splitlines():
        if line and not line.startswith("#") and " " in line:
            name_and_labels, value = line.rsplit(" ", 1)
            metrics[name_and_labels] = float(value)
    return metrics


@pytest.mark.parametrize(
    "metric_class, name, doc, labelnames, buckets",
    [
        (Counter, "test_counter", "Test counter", (), None),
        (Gauge, "test_gauge", "Test gauge", (), None),
        (Histogram, "test_histogram", "Test histogram", (), (0.1, 0.5, 1.0)),
        (Counter, "test_labeled_counter", "Test labeled counter", ("label1",), None),
    ],
)
def test_get_or_create_metric_internal(
    metric_class, name, doc, labelnames, buckets, caplog
):
    """Test _get_or_create_metric_internal creates metrics correctly."""
    from arbiter.meta_learning_orchestrator.metrics import (
        _get_or_create_metric_internal,
    )

    caplog.set_level(logging.WARNING)
    wrapped_metric = _get_or_create_metric_internal(
        metric_class, name, doc, labelnames, buckets
    )

    # Check the underlying metric through the wrapper
    assert hasattr(wrapped_metric, "_metric")
    assert isinstance(wrapped_metric._metric, metric_class)
    assert wrapped_metric._name == name
    assert wrapped_metric._documentation == doc
    assert set(wrapped_metric._labelnames) == set(
        labelnames + ("environment", "cluster")
    )


@pytest.mark.parametrize(
    "metric_class, name, doc",
    [
        (Gauge, "test_mismatch", "Mismatched type"),
        (Histogram, "test_mismatch", "Mismatched type"),
    ],
)
def test_get_or_create_metric_type_mismatch(metric_class, name, doc, caplog):
    """Test _get_or_create_metric_internal handles type mismatches."""
    from arbiter.meta_learning_orchestrator.metrics import (
        _get_or_create_metric_internal,
    )

    caplog.set_level(logging.WARNING)
    # Create a Counter first
    _get_or_create_metric_internal(Counter, name, "Original counter", (), None)
    # Create a different metric type with the same name
    wrapped_metric = _get_or_create_metric_internal(metric_class, name, doc, (), None)
    assert isinstance(wrapped_metric._metric, metric_class)
    assert "Unregistered existing metric" in caplog.text


def test_metric_registry_get_or_create(metric_registry):
    """Test MetricRegistry.get_or_create retrieves or creates metrics."""
    counter1 = metric_registry.get_or_create(Counter, "test_counter", "Test counter")
    counter2 = metric_registry.get_or_create(Counter, "test_counter", "Test counter")
    assert counter1 is counter2
    assert counter1._name == "test_counter"
    assert counter1 in metric_registry.metrics.values()


def test_metric_registry_global_labels(metric_registry):
    """Test global labels are applied to all metrics."""
    counter = metric_registry.get_or_create(
        Counter, "test_counter_with_labels", "Test counter", ("event_type",)
    )
    # The wrapper should handle global labels automatically
    counter.labels(event_type="test_event").inc()

    metrics_text = generate_latest().decode("utf-8")
    metrics = parse_metrics_output(metrics_text)

    # Check that the metric exists with all labels (Counter adds _total suffix)
    expected_label_string = 'test_counter_with_labels_total{cluster="test-cluster",environment="test",event_type="test_event"}'
    assert expected_label_string in metrics
    assert metrics[expected_label_string] == 1.0


def get_metrics_for_test():
    """Helper function to import metrics when needed."""
    from arbiter.meta_learning_orchestrator.metrics import (
        ML_AUDIT_EVENTS_TOTAL,
        ML_AUDIT_HASH_MISMATCH,
        ML_AUDIT_SIGNATURE_MISMATCH,
        ML_CURRENT_MODEL_VERSION,
        ML_DATA_QUEUE_SIZE,
        ML_DEPLOYMENT_FAILURE_COUNT,
        ML_DEPLOYMENT_RETRIES_EXHAUSTED,
        ML_DEPLOYMENT_SUCCESS_COUNT,
        ML_DEPLOYMENT_TRIGGER_COUNT,
        ML_EVALUATION_COUNT,
        ML_INGESTION_COUNT,
        ML_LEADER_STATUS,
        ML_ORCHESTRATOR_ERRORS,
        ML_TRAINING_FAILURE_COUNT,
        ML_TRAINING_SUCCESS_COUNT,
        ML_TRAINING_TRIGGER_COUNT,
    )

    return [
        (ML_INGESTION_COUNT, lambda x: x.inc(), 1.0),
        (ML_TRAINING_TRIGGER_COUNT, lambda x: x.inc(), 1.0),
        (ML_TRAINING_SUCCESS_COUNT, lambda x: x.inc(), 1.0),
        (ML_TRAINING_FAILURE_COUNT, lambda x: x.inc(), 1.0),
        (ML_EVALUATION_COUNT, lambda x: x.inc(), 1.0),
        (ML_DEPLOYMENT_TRIGGER_COUNT, lambda x: x.inc(), 1.0),
        (ML_DEPLOYMENT_SUCCESS_COUNT, lambda x: x.inc(), 1.0),
        (ML_DEPLOYMENT_FAILURE_COUNT, lambda x: x.inc(), 1.0),
        (ML_ORCHESTRATOR_ERRORS, lambda x: x.inc(), 1.0),
        (ML_CURRENT_MODEL_VERSION, lambda x: x.set(2.0), 2.0),
        (ML_DATA_QUEUE_SIZE, lambda x: x.set(100), 100.0),
        (ML_DEPLOYMENT_RETRIES_EXHAUSTED, lambda x: x.inc(), 1.0),
        (ML_LEADER_STATUS, lambda x: x.set(1), 1.0),
        (ML_AUDIT_EVENTS_TOTAL, lambda x: x.labels(event_type="login").inc(), 1.0),
        (ML_AUDIT_HASH_MISMATCH, lambda x: x.inc(), 1.0),
        (ML_AUDIT_SIGNATURE_MISMATCH, lambda x: x.inc(), 1.0),
    ]


@pytest.mark.parametrize("metric_idx", range(16))
def test_metric_operations(metric_idx):
    """Test operations on all defined metrics."""
    # Import and get metrics inside the test to ensure env vars are set
    test_cases = get_metrics_for_test()
    metric, operation, value = test_cases[metric_idx]

    operation(metric)
    metrics_text = generate_latest().decode("utf-8")
    metrics = parse_metrics_output(metrics_text)

    metric_name = metric._name
    # Prometheus automatically adds _total suffix to Counters
    if hasattr(metric, "_metric"):
        if isinstance(metric._metric, Counter) and not metric_name.endswith("_total"):
            output_name = f"{metric_name}_total"
        else:
            output_name = metric_name
    else:
        output_name = metric_name

    # Prometheus sorts label names alphabetically
    if hasattr(metric, "_labelnames") and "event_type" in metric._labelnames:
        labels = 'cluster="test-cluster",environment="test",event_type="login"'
    else:
        labels = 'cluster="test-cluster",environment="test"'

    expected_label_string = f"{output_name}{{{labels}}}"
    assert (
        expected_label_string in metrics
    ), f"Expected {expected_label_string} not found in metrics"
    assert metrics[expected_label_string] == value


def test_histogram_metrics(metric_registry):
    """Test histogram metrics observe values correctly."""
    from arbiter.meta_learning_orchestrator.metrics import ML_TRAINING_LATENCY

    with ML_TRAINING_LATENCY.time():
        import time

        time.sleep(0.1)

    metrics_text = generate_latest().decode("utf-8")
    metrics = parse_metrics_output(metrics_text)

    # Prometheus sorts labels alphabetically
    sum_label = (
        'ml_training_latency_seconds_sum{cluster="test-cluster",environment="test"}'
    )
    count_label = (
        'ml_training_latency_seconds_count{cluster="test-cluster",environment="test"}'
    )

    assert sum_label in metrics, f"Expected {sum_label} not found in metrics"
    assert metrics[sum_label] > 0
    assert count_label in metrics, f"Expected {count_label} not found in metrics"
    assert metrics[count_label] == 1.0


@pytest.mark.parametrize(
    "metric_name",
    ["ML_TRAINING_LATENCY", "ML_EVALUATION_LATENCY", "ML_DEPLOYMENT_LATENCY"],
)
def test_histogram_buckets(metric_name):
    """Test histogram metrics work with buckets."""
    # Import the specific metric
    from arbiter.meta_learning_orchestrator import metrics

    metric = getattr(metrics, metric_name)

    # Test that the histogram works correctly
    with metric.time():
        import time

        time.sleep(0.05)

    metrics_text = generate_latest().decode("utf-8")
    parsed_metrics = parse_metrics_output(metrics_text)

    # Check that bucket metrics exist (Prometheus sorts labels alphabetically)
    bucket_label = (
        f'{metric._name}_bucket{{cluster="test-cluster",environment="test",le="0.1"}}'
    )
    assert (
        bucket_label in parsed_metrics
    ), f"Expected {bucket_label} not found in metrics"
    # The value should be 1.0 since we slept for 0.05 seconds which is < 0.1
    assert parsed_metrics[bucket_label] == 1.0


def test_metrics_with_no_env_vars(mocker: MockerFixture):
    """Test metrics with default global labels when env vars are missing."""
    # Clear env vars
    mocker.patch.dict(os.environ, {}, clear=True)

    # Force reload of the metrics module
    if "arbiter.meta_learning_orchestrator.metrics" in sys.modules:
        del sys.modules["arbiter.meta_learning_orchestrator.metrics"]

    import arbiter.meta_learning_orchestrator.metrics as metrics_module

    registry = metrics_module.MetricRegistry()
    counter = registry.get_or_create(
        Counter, "test_default_labels_counter", "Test counter"
    )
    counter.inc()

    metrics_text = generate_latest().decode("utf-8")
    metrics = parse_metrics_output(metrics_text)

    # Should use default values (alphabetically sorted)
    expected_label_string = 'test_default_labels_counter_total{cluster="default-cluster",environment="development"}'
    assert (
        expected_label_string in metrics
    ), f"Expected {expected_label_string} not found in metrics"
    assert metrics[expected_label_string] == 1.0


def test_metric_registry_thread_safety(metric_registry, mocker: MockerFixture):
    """Test thread-safety of MetricRegistry by simulating concurrent metric creation."""

    def create_metric(i):
        metric_registry.get_or_create(
            Counter, f"test_thread_counter_{i}", f"Test counter {i}"
        )

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(create_metric, range(10))

    for i in range(10):
        metric = metric_registry.get_or_create(
            Counter, f"test_thread_counter_{i}", f"Test counter {i}"
        )
        assert metric._name == f"test_thread_counter_{i}"
        assert metric in metric_registry.metrics.values()


def test_invalid_label_names(metric_registry, caplog):
    """Test handling of invalid label names."""
    caplog.set_level(logging.ERROR)

    # Valid label names should work
    counter = metric_registry.get_or_create(
        Counter, "test_valid_label_counter", "Test counter", ("valid_label",)
    )
    assert counter is not None

    # Test with empty label name
    with pytest.raises(ValueError):
        metric_registry.get_or_create(
            Counter, "bad_empty_label_counter", "Bad counter", ("",)
        )


def test_metrics_exposition_format():
    """Test metrics are correctly formatted in Prometheus exposition format."""
    from arbiter.meta_learning_orchestrator.metrics import (
        ML_AUDIT_EVENTS_TOTAL,
        ML_CURRENT_MODEL_VERSION,
        ML_EVALUATION_LATENCY,
        ML_INGESTION_COUNT,
    )

    # Get current values to calculate increments
    metrics_text_before = generate_latest().decode("utf-8")
    metrics_before = parse_metrics_output(metrics_text_before)

    # Get initial value of ml_ingestion_total if it exists
    ingestion_key = 'ml_ingestion_total{cluster="test-cluster",environment="test"}'
    initial_ingestion = metrics_before.get(ingestion_key, 0.0)

    ML_INGESTION_COUNT.inc(2)
    ML_AUDIT_EVENTS_TOTAL.labels(event_type="test_event").inc()
    ML_CURRENT_MODEL_VERSION.set(1.5)

    with ML_EVALUATION_LATENCY.time():
        import time

        time.sleep(0.1)

    metrics_text = generate_latest().decode("utf-8")
    metrics_after = parse_metrics_output(metrics_text)

    # Check for help text
    assert "# HELP ml_ingestion_total Total learning records ingested" in metrics_text

    # Check metric values - ingestion should have increased by 2
    assert ingestion_key in metrics_after
    assert (
        metrics_after[ingestion_key] == initial_ingestion + 2.0
    ), f"Expected {initial_ingestion + 2.0}, got {metrics_after[ingestion_key]}"

    # Check other metrics exist with expected values
    assert (
        'ml_audit_events_total{cluster="test-cluster",environment="test",event_type="test_event"}'
        in metrics_after
    )
    assert (
        metrics_after[
            'ml_audit_events_total{cluster="test-cluster",environment="test",event_type="test_event"}'
        ]
        >= 1.0
    )

    assert (
        'ml_current_model_version{cluster="test-cluster",environment="test"}'
        in metrics_after
    )
    assert (
        metrics_after[
            'ml_current_model_version{cluster="test-cluster",environment="test"}'
        ]
        == 1.5
    )

    assert (
        'ml_evaluation_latency_seconds_sum{cluster="test-cluster",environment="test"}'
        in metrics_after
    )
