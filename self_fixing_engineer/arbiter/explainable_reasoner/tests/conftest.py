# conftest.py
import pytest
from unittest.mock import patch, MagicMock
from prometheus_client import CollectorRegistry

# Import the function that initializes your metrics
from arbiter.explainable_reasoner.metrics import initialize_metrics


@pytest.fixture(autouse=True)
def isolated_metrics():
    """
    Ensures every test runs with a clean Prometheus registry and metrics dictionary.
    This prevents state from leaking between tests.
    """
    # Create a mock metric class
    mock_metric = MagicMock()
    mock_metric.labels.return_value = MagicMock(
        inc=MagicMock(),
        dec=MagicMock(),
        set=MagicMock(),
        observe=MagicMock()
    )

    # Pre-populate the mock METRICS dictionary with expected keys to prevent KeyError
    mock_metrics_dict = {
        "reasoner_history_operations_total": mock_metric,
        "reasoner_history_operation_latency_seconds": mock_metric,
        "reasoner_history_db_connection_failures_total": mock_metric,
        "reasoner_history_pruned_entries_total": mock_metric,
        "reasoner_history_entries_current": mock_metric,
        "reasoner_requests_total": mock_metric,
        "reasoner_inference_success": mock_metric,
        "reasoner_inference_errors": mock_metric,
        "reasoner_prompt_truncations": mock_metric,
        "reasoner_cache_hits": mock_metric,
        "reasoner_cache_misses": mock_metric,
        "reasoner_cache_errors": mock_metric,
        "reasoner_model_reload_attempts": mock_metric,
        "reasoner_model_reload_success": mock_metric,
        "reasoner_model_load_errors": mock_metric,
        "reasoner_health_check_success": mock_metric,
        "reasoner_health_check_errors": mock_metric,
        "reasoner_instances": mock_metric,
        "reasoner_shutdown_duration_seconds": mock_metric,
        "reasoner_prompt_size_bytes": mock_metric,
        "reasoner_inference_duration_seconds": mock_metric,
        "reasoner_history_entries_used": mock_metric,
        "reasoner_sensitive_data_redaction_total": mock_metric,
        "reasoner_executor_restarts_total": mock_metric,
        "reasoner_executor_queue_size": mock_metric,
        "reasoner_model_load_success": mock_metric,
        "reasoner_model_unload_total": mock_metric,
        "reasoner_init_duration_seconds": mock_metric,
        # Adding keys from prompt_strategies.py as well
        "prompt_size_bytes": mock_metric,
        "inference_duration_seconds": mock_metric
    }
    
    with patch(
        "arbiter.explainable_reasoner.metrics.METRICS_REGISTRY", new=CollectorRegistry()
    ) as registry, patch(
        "arbiter.explainable_reasoner.metrics.METRICS", new=mock_metrics_dict
    ) as metrics_dict:
        # Re-initialize the default metrics into the new, empty dictionary
        # This fixes the KeyError in tests that rely on pre-populated metrics.
        initialize_metrics()

        yield registry, metrics_dict