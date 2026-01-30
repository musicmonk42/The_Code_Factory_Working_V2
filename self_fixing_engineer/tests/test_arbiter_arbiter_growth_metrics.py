import asyncio
from unittest.mock import MagicMock, patch

import pytest
from arbiter.arbiter_growth.config_store import ConfigStore
from arbiter.arbiter_growth.metrics import get_or_create_metric
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# --- Fixtures ---


@pytest.fixture
def isolated_registry():
    """Provides an isolated Prometheus registry for testing."""
    return CollectorRegistry()


@pytest.fixture
def mock_config_store():
    """Provides a mock ConfigStore for testing metric configuration."""
    mock = MagicMock(spec=ConfigStore)

    def get_side_effect(key, default=None):
        configs = {
            "metrics.growth_events_total.labels": ["arbiter", "extra_label"],
            "metrics.growth_skill_improvement_value.buckets": [
                0.1,
                0.5,
                1.0,
                5.0,
                10.0,
                float("inf"),
            ],
        }
        return configs.get(key, default)

    mock.get.side_effect = get_side_effect
    return mock


# --- Test Cases ---


def test_get_or_create_counter_new(isolated_registry):
    """Tests creating a new Counter metric."""
    metric = get_or_create_metric(
        Counter, "test_counter", "Test counter", registry=isolated_registry
    )
    assert isinstance(metric, Counter)
    assert metric._name == "test_counter"
    assert metric._documentation == "Test counter"


def test_get_or_create_gauge_new(isolated_registry):
    """Tests creating a new Gauge metric."""
    metric = get_or_create_metric(
        Gauge, "test_gauge", "Test gauge", registry=isolated_registry
    )
    assert isinstance(metric, Gauge)
    assert metric._name == "test_gauge"


def test_get_or_create_histogram_new(isolated_registry):
    """Tests creating a new Histogram metric with default buckets."""
    # Create a mock config that returns None for buckets
    mock_config = MagicMock(spec=ConfigStore)
    mock_config.get.return_value = None

    metric = get_or_create_metric(
        Histogram,
        "test_histogram",
        "Test histogram",
        labelnames=(),
        config_store=mock_config,
        registry=isolated_registry,
    )
    assert isinstance(metric, Histogram)
    assert metric._name == "test_histogram"

    # Verify default buckets are used
    # The internal representation includes INF as the last bucket
    expected_buckets = list(Histogram.DEFAULT_BUCKETS)
    actual_buckets = metric._upper_bounds

    # Compare lengths
    assert len(actual_buckets) == len(expected_buckets)

    # Compare values (handling float comparison)
    for actual, expected in zip(actual_buckets, expected_buckets):
        if expected == float("inf"):
            assert actual == float("inf")
        else:
            assert abs(actual - expected) < 0.0001


def test_get_or_create_existing_metric_same_type(isolated_registry):
    """Tests that getting an existing metric returns the same instance."""
    # Create first metric
    metric1 = get_or_create_metric(
        Counter, "test_metric", "Test metric", registry=isolated_registry
    )

    # Try to create again with same name
    metric2 = get_or_create_metric(
        Counter, "test_metric", "Test metric", registry=isolated_registry
    )

    # Should be the same instance
    assert metric1 is metric2


def test_get_or_create_handles_conflicting_metric_type(isolated_registry):
    """Tests that conflicting metric types are handled by unregistering and recreating."""
    # Create a Counter
    counter = get_or_create_metric(
        Counter, "test_metric", "Test metric", registry=isolated_registry
    )
    assert isinstance(counter, Counter)

    # Try to create a Gauge with the same name
    gauge = get_or_create_metric(
        Gauge, "test_metric", "Test metric", registry=isolated_registry
    )

    # Should have replaced the Counter with a Gauge
    assert isinstance(gauge, Gauge)
    assert gauge._name == "test_metric"


def test_get_or_create_uses_custom_labels_from_config(
    mock_config_store, isolated_registry
):
    """Tests that custom labels are correctly applied from the ConfigStore."""
    metric = get_or_create_metric(
        Counter,
        "growth_events_total",
        "Growth events total",
        labelnames=["arbiter", "extra_label"],
        config_store=mock_config_store,
        registry=isolated_registry,
    )

    # Check that the metric has the correct labels
    assert set(metric._labelnames) == {"arbiter", "extra_label"}


def test_get_or_create_uses_custom_buckets_from_config(
    mock_config_store, isolated_registry
):
    """Tests that custom histogram buckets are correctly applied from the ConfigStore."""
    # Configure mock to return specific buckets
    custom_buckets = [0.1, 0.5, 1.0, 5.0, 10.0]
    mock_config_store.get.side_effect = lambda key, default=None: {
        "metrics.growth_skill_improvement_value.buckets": custom_buckets
    }.get(key, default)

    metric = get_or_create_metric(
        Histogram,
        "growth_skill_improvement_value",
        "Skill improvement value",
        labelnames=("arbiter", "skill"),
        config_store=mock_config_store,
        registry=isolated_registry,
    )

    assert isinstance(metric, Histogram)

    # Verify custom buckets are used (with INF added)
    expected_buckets = custom_buckets + [float("inf")]
    actual_buckets = metric._upper_bounds

    assert len(actual_buckets) == len(expected_buckets)
    for actual, expected in zip(actual_buckets, expected_buckets):
        if expected == float("inf"):
            assert actual == float("inf")
        else:
            assert abs(actual - expected) < 0.0001


def test_get_or_create_handles_unregister_failure(isolated_registry):
    """Tests that unregister failures are handled gracefully."""
    # Create a Counter
    get_or_create_metric(
        Counter, "test_metric", "Test metric", registry=isolated_registry
    )

    # Mock the unregister to raise an exception
    with patch.object(
        isolated_registry, "unregister", side_effect=Exception("Unregister failed")
    ):
        # Should still create the new metric type
        gauge = get_or_create_metric(
            Gauge, "test_metric", "Test metric", registry=isolated_registry
        )

        # The old counter should still exist, but we get a new gauge
        assert isinstance(gauge, Gauge)


def test_concurrent_metric_creation(isolated_registry):
    """Tests that concurrent metric creation is handled correctly."""
    import threading

    metrics = []

    def create_metric():
        metric = get_or_create_metric(
            Counter,
            "concurrent_metric",
            "Concurrent metric",
            registry=isolated_registry,
        )
        metrics.append(metric)

    # Create multiple threads trying to create the same metric
    threads = [threading.Thread(target=create_metric) for _ in range(10)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    # All threads should get the same metric instance
    assert len(metrics) == 10
    assert all(m is metrics[0] for m in metrics)


def test_metric_usage_integration(isolated_registry):
    """Integration test for metric creation and usage."""
    # Create a counter with labels
    counter = get_or_create_metric(
        Counter,
        "test_counter",
        "Test counter",
        labelnames=["status", "method"],
        registry=isolated_registry,
    )

    # Increment the counter
    counter.labels(status="success", method="GET").inc()
    counter.labels(status="success", method="POST").inc(2)
    counter.labels(status="error", method="GET").inc()

    # Verify the values
    assert (
        isolated_registry.get_sample_value(
            "test_counter_total", {"status": "success", "method": "GET"}
        )
        == 1.0
    )
    assert (
        isolated_registry.get_sample_value(
            "test_counter_total", {"status": "success", "method": "POST"}
        )
        == 2.0
    )
    assert (
        isolated_registry.get_sample_value(
            "test_counter_total", {"status": "error", "method": "GET"}
        )
        == 1.0
    )


def test_histogram_with_observations(isolated_registry):
    """Test histogram metric with observations."""
    histogram = get_or_create_metric(
        Histogram,
        "request_latency",
        "Request latency in seconds",
        labelnames=(),
        registry=isolated_registry,
    )

    # Observe some values
    histogram.observe(0.05)
    histogram.observe(0.15)
    histogram.observe(0.3)
    histogram.observe(1.5)
    histogram.observe(7.0)

    # Get the histogram data by collecting samples
    samples = list(histogram.collect())[0].samples

    # Find the count sample and assert its value
    count_sample = next(s for s in samples if s.name.endswith("_count"))
    assert count_sample.value == 5

    # Find the sum sample and assert its value
    sum_sample = next(s for s in samples if s.name.endswith("_sum"))
    assert sum_sample.value > 0


def test_gauge_set_and_inc_dec(isolated_registry):
    """Test gauge metric operations."""
    gauge = get_or_create_metric(
        Gauge, "queue_size", "Current queue size", registry=isolated_registry
    )

    # Set value
    gauge.set(10)
    assert isolated_registry.get_sample_value("queue_size") == 10

    # Increment
    gauge.inc(5)
    assert isolated_registry.get_sample_value("queue_size") == 15

    # Decrement
    gauge.dec(3)
    assert isolated_registry.get_sample_value("queue_size") == 12


def test_metric_with_no_labels(isolated_registry):
    """Test creating metrics without labels."""
    counter = get_or_create_metric(
        Counter,
        "simple_counter",
        "Simple counter without labels",
        registry=isolated_registry,
    )

    # Should be able to use without labels
    counter.inc()
    counter.inc(5)

    assert isolated_registry.get_sample_value("simple_counter_total") == 6


def test_invalid_metric_type(isolated_registry):
    """Test that invalid metric types raise appropriate errors."""
    with pytest.raises(TypeError):
        get_or_create_metric(
            str,  # Invalid metric type
            "bad_metric",
            "Bad metric",
            registry=isolated_registry,
        )


def test_metric_documentation_preserved(isolated_registry):
    """Test that metric documentation is preserved."""
    documentation = "This is a detailed description of the metric"

    metric = get_or_create_metric(
        Counter, "documented_metric", documentation, registry=isolated_registry
    )

    assert metric._documentation == documentation


def test_get_or_create_with_config_override(mock_config_store, isolated_registry):
    """Test that explicit parameters override config values."""
    # Config says use certain labels
    mock_config_store.get.return_value = ["config_label"]

    # But we explicitly pass different labels
    metric = get_or_create_metric(
        Counter,
        "override_metric",
        "Override metric",
        labelnames=("explicit_label",),
        config_store=mock_config_store,
        registry=isolated_registry,
    )

    assert metric._labelnames == ("explicit_label",)


def test_metric_labels(isolated_registry):
    """Tests that metric labels are correctly applied and can be used to retrieve values."""
    metric = get_or_create_metric(
        Counter,
        "test_counter_labels",
        "Test counter with labels",
        labelnames=("arbiter",),
        registry=isolated_registry,
    )
    metric.labels(arbiter="test_instance").inc()
    assert (
        isolated_registry.get_sample_value(
            "test_counter_labels_total", {"arbiter": "test_instance"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_concurrent_metric_updates(isolated_registry):
    """Tests that concurrent updates to a metric are handled correctly."""
    counter = get_or_create_metric(
        Counter,
        "concurrent_counter",
        "Test concurrent counter",
        registry=isolated_registry,
    )

    # Use asyncio.run_in_executor to simulate concurrent updates from different threads
    tasks = [
        asyncio.get_event_loop().run_in_executor(None, counter.inc) for _ in range(10)
    ]

    # Await all tasks to complete
    await asyncio.gather(*tasks)

    # The final value should be the sum of all increments
    assert isolated_registry.get_sample_value("concurrent_counter_total") == 10
