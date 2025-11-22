import pytest
import logging
from unittest.mock import Mock, AsyncMock, ANY
from test_generation.orchestrator.audit import (
    audit_event,
    RUN_ID,
)
from pathlib import Path

# Fix: Added missing import.

# Fix: The `_audit` function now requires a `run_id`.
# The `RUN_ID` is imported from the `audit` module.
# The user's provided test cases have been incorporated and fixed.


@pytest.mark.asyncio
async def test_audit_with_arbiter(monkeypatch):
    """Tests auditing when the arbiter is available and a valid log event is sent."""
    mock_arbiter = Mock(log_event=AsyncMock())
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.arbiter_audit", mock_arbiter
    )
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.AUDIT_LOGGER_AVAILABLE", True
    )

    # The _audit function needs a `run_id` now.
    await audit_event("test_event", {"key": "value"}, run_id=RUN_ID)

    # Use ANY to match the details dictionary because it contains a dynamic timestamp.
    mock_arbiter.log_event.assert_called_once_with(
        event_type="test_event", details=ANY, critical=False, run_id=RUN_ID
    )


@pytest.mark.asyncio
async def test_audit_fallback(monkeypatch, caplog):
    """Tests logging to the console when the arbiter is not available."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.AUDIT_LOGGER_AVAILABLE", False
    )

    # The _audit function needs a `run_id` now.
    await audit_event("test_event", {"key": "value"}, run_id=RUN_ID)

    # The assertion is updated to check for the correct log message format.
    assert any(
        f'{{"event": "test_event", "level": "INFO", "key": "value", "run_id": "{RUN_ID}"'
        in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_audit_arbiter_failure(monkeypatch, caplog):
    """Tests graceful fallback when the arbiter audit logger raises an exception."""
    caplog.set_level(logging.INFO)
    mock_arbiter = Mock(log_event=AsyncMock(side_effect=Exception("Arbiter failed")))
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.arbiter_audit", mock_arbiter
    )
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.AUDIT_LOGGER_AVAILABLE", True
    )

    await audit_event("test_event", {"key": "value"}, run_id=RUN_ID)

    assert any("arbiter_error" in record.message for record in caplog.records)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A pytest fixture to create a temporary project directory structure."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "atco_artifacts").mkdir()
    return root


@pytest.mark.parametrize("bad_obj", [(x for x in []), lambda: None, object()])
@pytest.mark.asyncio
async def test_audit_non_serializable(project, bad_obj, monkeypatch):
    """
    Tests that non-serializable objects are handled correctly and logged as a string
    when the arbiter is not available.
    """
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.AUDIT_LOGGER_AVAILABLE", False
    )

    # Ensure the audit log file path is correctly configured.
    audit_log_path = project / "atco_artifacts/atco_audit.log"
    monkeypatch.setattr(
        "test_generation.orchestrator.config.AUDIT_LOG_FILE", audit_log_path
    )

    data = {"obj": bad_obj}
    await audit_event("test_event", data, run_id=RUN_ID)

    assert audit_log_path.exists()

    with open(audit_log_path, "r") as f:
        log_content = f.read()

    # The assertion is updated to check for the correct string representation.
    assert "<Non-serializable object" in log_content


@pytest.mark.asyncio
async def test_audit_serialization_failure_handling(monkeypatch, caplog):
    """
    Tests that a serialization failure is logged as an error and does not crash the
    _audit function.
    """
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(
        "test_generation.orchestrator.audit.AUDIT_LOGGER_AVAILABLE", False
    )

    class NonSerializable:
        def __json__(self):
            raise TypeError("Cannot serialize")

    data = {"obj": NonSerializable()}

    await audit_event("test_event", data, run_id=RUN_ID)

    # The assertion checks if the specific error message is in the logs.
    assert "Failed to serialize audit log" in caplog.text


def test_audit_export():
    """Verifies that `audit_event` is correctly exported and callable."""
    from test_generation.orchestrator.audit import audit_event

    assert callable(audit_event)
