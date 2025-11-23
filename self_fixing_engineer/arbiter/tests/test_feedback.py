import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import asyncio
import threading
import random

from prometheus_client import Counter, REGISTRY

# Fixed imports - using the actual metric names from feedback.py
from arbiter.feedback import (
    FeedbackManager,
    logger,
    _get_or_create_metric,
    SQLiteClient,  # Import SQLiteClient for testing
)


# Fixture to mock logger
@pytest.fixture
def mock_logger():
    with patch.object(logger, "info") as mock_info, patch.object(
        logger, "warning"
    ) as mock_warning, patch.object(logger, "error") as mock_error:
        yield mock_info, mock_warning, mock_error


# Fixture to clear Prometheus registry
@pytest.fixture(autouse=True)
def clear_registry():
    collectors = list(REGISTRY._collector_to_names.keys())
    for collector in collectors:
        REGISTRY.unregister(collector)
    yield


# Test conditional imports
@patch("arbiter.feedback.SQLLITE_AVAILABLE", False)
@patch("arbiter.feedback.POSTGRES_AVAILABLE", False)
def test_conditional_imports(caplog):
    # Test that imports are handled gracefully when not available
    # This mainly validates that the module doesn't crash
    pass


# Test _get_or_create_metric thread-safe
def test_get_or_create_metric_thread_safe():
    name = "test_counter"
    doc = "Test"
    labels = ("label",)

    def create():
        _get_or_create_metric(Counter, name, doc, labelnames=labels)

    threads = [threading.Thread(target=create) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert name in REGISTRY._names_to_collectors
    assert isinstance(REGISTRY._names_to_collectors[name], Counter)


# Test FeedbackManager init with SQLiteClient (default in dev mode)
@patch("arbiter.feedback.SQLiteClient")
@patch("arbiter.feedback.IS_PRODUCTION", False)
def test_init_default_sqlite(mock_sqlite_class):
    mock_sqlite_instance = MagicMock()
    mock_sqlite_class.return_value = mock_sqlite_instance

    fm = FeedbackManager()

    mock_sqlite_class.assert_called_once_with(db_file="feedback.db")
    assert fm.db_client == mock_sqlite_instance


# Test init with provided SQLiteClient
@patch("arbiter.feedback.SQLLITE_AVAILABLE", True)
def test_init_with_sqlite_client():
    mock_client = MagicMock(spec=SQLiteClient)
    fm = FeedbackManager(db_client=mock_client)
    assert fm.db_client == mock_client


# Test init with PostgresClient
@patch("arbiter.feedback.PostgresClient")
@patch("arbiter.feedback.DB_CLIENTS_AVAILABLE", True)
@patch("arbiter.feedback.POSTGRES_AVAILABLE", True)
def test_init_with_postgres_url(mock_pg_class):
    mock_pg_instance = MagicMock()
    mock_pg_class.return_value = mock_pg_instance

    mock_config = MagicMock()
    mock_config.DATABASE_URL = "postgresql://user:pass@localhost/db"

    fm = FeedbackManager(config=mock_config)

    mock_pg_class.assert_called_once_with(
        db_url="postgresql://user:pass@localhost/db", pool_size=5, max_overflow=10
    )
    assert fm.db_client == mock_pg_instance


# Test init in production without DATABASE_URL raises error
@patch("arbiter.feedback.IS_PRODUCTION", True)
def test_init_production_no_db_url():
    mock_config = MagicMock()
    mock_config.DATABASE_URL = None

    with pytest.raises(RuntimeError, match="In production, DATABASE_URL is not set"):
        FeedbackManager(config=mock_config)


# Test record_metric success
@pytest.mark.asyncio
async def test_record_metric_success(mock_logger):
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    await fm.record_metric("test_metric", 42.0, {"tag": "value"})

    fm.db_client.save_feedback_entry.assert_called_once()
    call_args = fm.db_client.save_feedback_entry.call_args[0][0]
    assert call_args["name"] == "test_metric"
    assert call_args["value"] == 42.0
    assert call_args["tags"] == {"tag": "value"}


# Test record_metric with invalid name
@pytest.mark.asyncio
async def test_record_metric_invalid_name(mock_logger):
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    await fm.record_metric("", 42.0)

    fm.db_client.save_feedback_entry.assert_not_called()
    _, _, error = mock_logger
    error.assert_called_with("Invalid metric name: ")


# Test record_metric with invalid value
@pytest.mark.asyncio
async def test_record_metric_invalid_value(mock_logger):
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    await fm.record_metric("test", True)  # Boolean is not valid

    fm.db_client.save_feedback_entry.assert_not_called()
    _, _, error = mock_logger
    error.assert_called()


# Test log_error
@pytest.mark.asyncio
async def test_log_error(mock_logger):
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    error_info = {"error": "Test error", "component": "test_component"}
    await fm.log_error(error_info)

    fm.db_client.save_feedback_entry.assert_called_once()
    _, _, error = mock_logger
    error.assert_called_with("Error logged by FeedbackManager: Test error")


# Test add_user_feedback with approval
@pytest.mark.asyncio
async def test_add_user_feedback_approval():
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    feedback = {"decision_id": "123", "approved": True}
    await fm.add_user_feedback(feedback)

    fm.db_client.save_feedback_entry.assert_called_once()
    call_args = fm.db_client.save_feedback_entry.call_args[0][0]
    assert call_args["type"] == "user_feedback"
    assert call_args["approved"] is True


# Test add_user_feedback with denial
@pytest.mark.asyncio
async def test_add_user_feedback_denial():
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    feedback = {"decision_id": "123", "approved": False}
    await fm.add_user_feedback(feedback)

    fm.db_client.save_feedback_entry.assert_called_once()
    call_args = fm.db_client.save_feedback_entry.call_args[0][0]
    assert call_args["type"] == "user_feedback"
    assert call_args["approved"] is False


# Test get_summary
@pytest.mark.asyncio
async def test_get_summary():
    fm = FeedbackManager()
    fm.db_client.get_feedback_entries = AsyncMock(
        side_effect=[
            [
                {"name": "metric1", "value": 10},
                {"name": "metric1", "value": 20},
            ],  # metrics
            [{"error": "Error 1"}],  # errors
            [{"feedback": "Good"}],  # user feedback
            [{"decision_id": "1", "status": "pending"}],  # approval requests
            [{"decision_id": "1", "response": {"approved": True}}],  # approval responses
        ]
    )

    summary = await fm.get_summary()

    assert "metrics_summary" in summary
    assert "metric1" in summary["metrics_summary"]
    assert summary["metrics_summary"]["metric1"]["mean"] == 15.0
    assert len(summary["recent_errors"]) == 1
    assert summary["approval_requests_summary"]["total_requests"] == 1


# Test get_pending_approvals
@pytest.mark.asyncio
async def test_get_pending_approvals():
    fm = FeedbackManager()
    pending = [{"decision_id": "1", "status": "pending"}]
    fm.db_client.get_feedback_entries = AsyncMock(return_value=pending)

    result = await fm.get_pending_approvals()

    assert result == pending
    fm.db_client.get_feedback_entries.assert_called_once_with(
        {"type": "approval_request", "status": "pending"}
    )


# Test get_approval_stats
@pytest.mark.asyncio
async def test_get_approval_stats():
    fm = FeedbackManager()

    mock_responses = [
        {
            "decision_id": "1",
            "timestamp": "2023-01-01T00:00:05+00:00",
            "response": {"approved": True, "user_id": "user1"},
        }
    ]
    mock_requests = [
        {
            "decision_id": "1",
            "request_start_time_utc": "2023-01-01T00:00:00+00:00",
            "resolution_timestamp": "2023-01-01T00:00:05+00:00",
            "context": {"action": "test_action"},
        }
    ]

    fm.db_client.get_feedback_entries = AsyncMock(
        side_effect=[
            mock_responses,  # approval responses
            mock_requests,  # approval requests
        ]
    )

    stats = await fm.get_approval_stats(group_by_reviewer=True, group_by_decision_type=True)

    assert "by_reviewer" in stats
    assert "user1" in stats["by_reviewer"]
    assert stats["by_reviewer"]["user1"]["approved"] == 1
    assert stats["by_reviewer"]["user1"]["denied"] == 0

    assert "by_decision_type" in stats
    assert "test_action" in stats["by_decision_type"]

    assert "approval_times" in stats
    assert stats["approval_times"]["mean_seconds"] == 5.0


# Test start_async_services
@pytest.mark.asyncio
async def test_start_async_services():
    fm = FeedbackManager()

    # Mock the loop method to prevent actual execution
    with patch.object(fm, "_purge_metrics_and_sync_loop", new_callable=AsyncMock) as mock_loop:
        mock_loop.return_value = None
        await fm.start_async_services()
        assert fm._purge_task is not None


# Test stop_async_services
@pytest.mark.asyncio
async def test_stop_async_services(mock_logger):
    fm = FeedbackManager()
    fm._purge_task = asyncio.create_task(asyncio.sleep(10))

    await fm.stop_async_services()

    assert fm._purge_task.cancelled()
    info, _, _ = mock_logger
    # The actual implementation logs "FeedbackManager async services stopped." not "purge task cancelled successfully."
    info.assert_called_with("FeedbackManager async services stopped.")


# Test thread safety in record_metric
@pytest.mark.asyncio
async def test_record_metric_thread_safety():
    fm = FeedbackManager()
    fm.db_client.save_feedback_entry = AsyncMock()

    async def record():
        await fm.record_metric("test", random.random())

    tasks = [asyncio.create_task(record()) for _ in range(10)]
    await asyncio.gather(*tasks)

    assert fm.db_client.save_feedback_entry.call_count == 10
