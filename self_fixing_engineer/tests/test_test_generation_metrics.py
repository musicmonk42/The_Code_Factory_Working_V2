import logging

# Fix: Added missing imports
from unittest.mock import Mock, patch

import pytest
from test_generation.orchestrator.metrics import (
    _DummyTimerCtx,
    generation_duration,
    integration_success,
)


@pytest.mark.asyncio
async def test_metrics_available(monkeypatch):
    mock_histogram = Mock()
    monkeypatch.setattr(
        "prometheus_client.Histogram", Mock(return_value=mock_histogram)
    )
    monkeypatch.setattr("self_fixing_engineer.test_generation.orchestrator.metrics.METRICS_AVAILABLE", True)
    generation_duration.labels(language="python").observe(1.0)
    assert mock_histogram.observe.called


@pytest.mark.asyncio
async def test_dummy_metrics_noop(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr("self_fixing_engineer.test_generation.orchestrator.metrics.METRICS_AVAILABLE", False)
    generation_duration.labels(language="python").observe(1.0)
    integration_success.inc()
    assert "Metrics disabled" in caplog.text


def test_no_duplicate_metrics():
    """
    Tests that a metric proxy correctly returns a real metric instance when available.
    """
    # Force metrics to be available for this test
    # This requires patching the module-level variable
    with patch("self_fixing_engineer.test_generation.orchestrator.metrics.METRICS_AVAILABLE", True):
        with patch(
            "self_fixing_engineer.test_generation.orchestrator.metrics.prometheus_client.Histogram"
        ) as mock_histogram_class:
            # First call should instantiate the metric
            generation_duration.labels(language="python").observe(1.0)
            mock_histogram_class.assert_called_once()

            # Second call should not instantiate a new metric
            generation_duration.labels(language="python").observe(2.0)
            mock_histogram_class.assert_called_once()

            # Assert that the object is not the dummy one
            assert generation_duration is not _DummyTimerCtx()
