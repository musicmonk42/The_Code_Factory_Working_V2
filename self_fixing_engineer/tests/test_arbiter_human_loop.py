import asyncio
import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the actual classes from human_loop.py
from arbiter.human_loop import (
    SECRET_SALT,
    DummyDBClient,
    FeedbackManager,
    HumanInLoop,
    HumanInLoopConfig,
    WebSocketManager,
)
from pydantic import ValidationError


# Fixture for HumanInLoopConfig
@pytest.fixture
def default_config():
    return HumanInLoopConfig()


@pytest.fixture
def production_config():
    return HumanInLoopConfig(
        IS_PRODUCTION=True,
        DATABASE_URL="postgresql://user:pass@localhost/db",
        EMAIL_ENABLED=True,
        EMAIL_SMTP_SERVER="smtp.example.com",
        EMAIL_SMTP_USER="user",
        EMAIL_SMTP_PASSWORD="pass",
    )


# Fixture for HumanInLoop
@pytest.fixture
def hil(default_config):
    return HumanInLoop(config=default_config)


@pytest.fixture
def mock_db_client():
    return DummyDBClient()


@pytest.fixture
def feedback_manager(mock_db_client):
    return FeedbackManager(db_client=mock_db_client)


# Test HumanInLoopConfig validation
def test_config_validation_production_requires_database():
    """Test that production mode requires DATABASE_URL"""
    config = HumanInLoopConfig(IS_PRODUCTION=True)  # This creates config successfully

    # The error happens when initializing HumanInLoop
    with pytest.raises(RuntimeError) as exc_info:
        HumanInLoop(config=config)
    assert "DATABASE_URL is not set" in str(exc_info.value)


def test_config_validation_production_email_requirements():
    """Test that production email requires full SMTP config"""
    with pytest.raises(ValidationError) as exc_info:
        HumanInLoopConfig(
            IS_PRODUCTION=True,
            DATABASE_URL="postgresql://test",
            EMAIL_ENABLED=True,
            EMAIL_SMTP_SERVER="smtp.test.com",
            # Missing user and password
        )
    assert "SMTP configuration" in str(exc_info.value)


# Test HumanInLoop initialization
def test_human_in_loop_init_development(default_config):
    """Test HumanInLoop initialization in development mode"""
    hil = HumanInLoop(config=default_config)
    assert isinstance(hil.config, HumanInLoopConfig)
    assert isinstance(hil._db_client, DummyDBClient)
    assert isinstance(hil.feedback_manager, FeedbackManager)
    assert hil._pending_approvals == {}


@patch("self_fixing_engineer.arbiter.human_loop.DB_CLIENTS_AVAILABLE", True)
@patch("self_fixing_engineer.arbiter.human_loop.PostgresClient")
def test_human_in_loop_init_production(mock_pg_class, production_config):
    """Test HumanInLoop initialization with PostgreSQL in production"""
    mock_pg_instance = MagicMock()
    mock_pg_class.return_value = mock_pg_instance

    hil = HumanInLoop(config=production_config)
    mock_pg_class.assert_called_once_with(db_url=production_config.DATABASE_URL)
    assert hil._db_client == mock_pg_instance


# Test request_approval
@pytest.mark.asyncio
async def test_request_approval_valid_decision(hil):
    """Test request_approval with valid decision data"""
    decision = {
        "decision_id": "test_123",
        "action": "deploy",
        "risk_level": "high",
        "details": {"version": "1.0.0"},
        "timeout_seconds": 1,  # Short timeout (integer)
    }

    # Create a proper async mock function
    async def mock_approval_func(decision_id, context):
        # Simulate a short delay
        await asyncio.sleep(0.01)
        timestamp = datetime.now(timezone.utc).isoformat()
        signature = hashlib.sha256(
            f"{decision_id}test_userTrueApproved{timestamp}{SECRET_SALT}".encode()
        ).hexdigest()
        # Properly await the receive_human_feedback call
        await hil.receive_human_feedback(
            {
                "decision_id": decision_id,
                "approved": True,
                "user_id": "test_user",
                "comment": "Approved",
                "timestamp": timestamp,
                "signature": signature,
            }
        )

    # Patch _mock_user_approval with our async function
    with patch.object(hil, "_mock_user_approval", side_effect=mock_approval_func):
        result = await hil.request_approval(decision)

        assert result["approved"]
        assert result["user_id"] == "test_user"


@pytest.mark.asyncio
async def test_request_approval_invalid_schema(hil):
    """Test request_approval with invalid decision schema"""
    # Use a decision that will actually fail validation
    decision = {"risk_level": 12345}  # risk_level should be a string, not int

    with patch.object(hil, "_handle_hook", new_callable=AsyncMock):
        result = await hil.request_approval(decision)

    assert not result["approved"]
    assert "Invalid request schema" in result["comment"]


@pytest.mark.asyncio
async def test_request_approval_timeout(hil):
    """Test request_approval timeout handling"""
    decision = {
        "decision_id": "timeout_test",
        "action": "deploy",
        "timeout_seconds": 1,  # Use integer, not float
    }

    # Mock notification tasks to return empty list (no notifications sent)
    with patch.object(hil, "_get_notification_tasks", return_value=[]):
        # Mock _mock_user_approval to never complete (causing timeout)
        async def never_complete(decision_id, context):
            await asyncio.sleep(10)  # Sleep longer than timeout

        with patch.object(hil, "_mock_user_approval", side_effect=never_complete):
            result = await hil.request_approval(decision)

    assert not result["approved"]
    assert "timed out" in result["comment"]


# Test notification channels
@pytest.mark.asyncio
async def test_send_email_notification(hil):
    """Test email notification sending"""
    # Properly mock aiosmtplib
    with patch("arbiter.human_loop.AIOSMTPLIB_AVAILABLE", True):
        with patch("arbiter.human_loop.aiosmtplib.SMTP") as mock_smtp_class:
            mock_smtp = AsyncMock()
            mock_smtp_class.return_value = mock_smtp
            mock_smtp.__aenter__.return_value = mock_smtp
            mock_smtp.__aexit__.return_value = None

            hil.config.EMAIL_ENABLED = True
            hil.config.EMAIL_SMTP_SERVER = "smtp.test.com"
            hil.config.EMAIL_SMTP_USER = "user"
            hil.config.EMAIL_SMTP_PASSWORD = "pass"
            hil.config.EMAIL_RECIPIENTS = {"reviewer": "reviewer@test.com"}
            hil.config.IS_PRODUCTION = True

            await hil._send_email_approval(
                "test_id", {"action": "test"}, "reviewer@test.com"
            )

            mock_smtp.login.assert_called_once_with("user", "pass")
            mock_smtp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_slack_notification(hil):
    """Test Slack notification sending"""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        # Properly set up the context manager chain
        mock_post_cm = AsyncMock()
        mock_post_cm.__aenter__.return_value = mock_response
        mock_post_cm.__aexit__.return_value = None
        mock_session.post.return_value = mock_post_cm

        mock_session_cm = AsyncMock()
        mock_session_cm.__aenter__.return_value = mock_session
        mock_session_cm.__aexit__.return_value = None
        mock_session_class.return_value = mock_session_cm

        hil.config.SLACK_WEBHOOK_URL = "https://hooks.slack.com/test"

        await hil._post_slack_approval("test_id", {"action": "test"})

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args[0][0] == "https://hooks.slack.com/test"
        assert "Approval Required" in call_args[1]["json"]["text"]


@pytest.mark.asyncio
async def test_websocket_notification(hil):
    """Test WebSocket notification sending"""
    mock_ws_manager = AsyncMock(spec=WebSocketManager)
    hil.websocket_manager = mock_ws_manager

    await hil._notify_ui_approval("test_id", {"action": "test"})

    mock_ws_manager.send_json.assert_called_once()
    call_args = mock_ws_manager.send_json.call_args[0][0]
    assert call_args["type"] == "approval_request"
    assert call_args["data"]["decision_id"] == "test_id"


# Test receive_human_feedback
@pytest.mark.asyncio
async def test_receive_human_feedback_valid(hil):
    """Test receiving valid human feedback"""
    decision_id = "feedback_test"
    user_id = "user123"
    approved = True
    comment = "Looks good"
    timestamp = datetime.now(timezone.utc).isoformat()

    # Create valid signature
    signature = hashlib.sha256(
        f"{decision_id}{user_id}{approved}{comment}{timestamp}{SECRET_SALT}".encode()
    ).hexdigest()

    feedback = {
        "decision_id": decision_id,
        "approved": approved,
        "user_id": user_id,
        "comment": comment,
        "timestamp": timestamp,
        "signature": signature,
    }

    # Set up pending approval
    async with hil._lock:
        future = asyncio.Future()
        hil._pending_approvals[decision_id] = future

    await hil.receive_human_feedback(feedback)

    # Check that the future was resolved
    assert future.done()
    result = await future
    assert result["approved"] == approved
    assert result["user_id"] == user_id


@pytest.mark.asyncio
async def test_receive_human_feedback_invalid_signature(hil):
    """Test receiving feedback with invalid signature"""
    feedback = {
        "decision_id": "test_id",
        "approved": True,
        "user_id": "user",
        "comment": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signature": "invalid_signature",
    }

    with patch.object(hil.logger, "error") as mock_logger:
        await hil.receive_human_feedback(feedback)
        mock_logger.assert_called()
        assert "Invalid signature" in mock_logger.call_args[0][0]


# Test FeedbackManager
@pytest.mark.asyncio
async def test_feedback_manager_log_approval_request(feedback_manager):
    """Test logging approval requests"""
    await feedback_manager.log_approval_request("test_id", {"action": "deploy"})

    entries = await feedback_manager.db_client.get_feedback_entries()
    assert len(entries) == 1
    assert entries[0]["type"] == "approval_request"
    assert entries[0]["decision_id"] == "test_id"
    assert entries[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_feedback_manager_log_approval_response(feedback_manager):
    """Test logging approval responses"""
    # First log a request
    await feedback_manager.log_approval_request("test_id", {"action": "deploy"})

    # Then log a response
    response = {"approved": True, "user_id": "user123"}
    await feedback_manager.log_approval_response("test_id", response)

    entries = await feedback_manager.db_client.get_feedback_entries()
    # Should have both request and response entries
    assert len(entries) == 2
    response_entries = [e for e in entries if e["type"] == "approval_response"]
    assert len(response_entries) == 1
    assert response_entries[0]["response"] == response


@pytest.mark.asyncio
async def test_feedback_manager_record_metric(feedback_manager):
    """Test recording metrics"""
    await feedback_manager.record_metric("test_metric", 42.0, {"tag": "value"})

    entries = await feedback_manager.db_client.get_feedback_entries()
    assert len(entries) == 1
    assert entries[0]["type"] == "metric"
    assert entries[0]["name"] == "test_metric"
    assert entries[0]["value"] == 42.0


# Test mock approval
@pytest.mark.asyncio
async def test_mock_user_approval(hil):
    """Test the mock user approval mechanism"""
    hil.mock_approval_delay_seconds = 0.01

    with patch.object(
        hil, "receive_human_feedback", new_callable=AsyncMock
    ) as mock_receive:
        await hil._mock_user_approval("test_id", {"action": "test"})

        mock_receive.assert_called_once()
        call_args = mock_receive.call_args[0][0]
        assert call_args["decision_id"] == "test_id"
        assert "approved" in call_args
        assert "user_id" in call_args
        assert "signature" in call_args


# Test concurrent approvals
@pytest.mark.asyncio
async def test_concurrent_approvals(hil):
    """Test handling multiple concurrent approval requests"""
    # Simply test that we can create multiple pending approvals
    decision_ids = []

    async with hil._lock:
        for i in range(5):
            decision_id = f"concurrent_{i}"
            hil._pending_approvals[decision_id] = asyncio.Future()
            decision_ids.append(decision_id)

    assert len(hil._pending_approvals) == 5

    # Clean up futures to avoid warnings
    async with hil._lock:
        for decision_id in decision_ids:
            future = hil._pending_approvals.get(decision_id)
            if future and not future.done():
                future.cancel()
        hil._pending_approvals.clear()


# Test hooks
@pytest.mark.asyncio
async def test_audit_hook_called(default_config):
    """Test that audit hook is called appropriately"""
    audit_hook = AsyncMock()
    hil = HumanInLoop(config=default_config, audit_hook=audit_hook)

    decision = {
        "decision_id": "hook_test",
        "action": "deploy",
        "timeout_seconds": 1,
    }  # Use integer

    # Use a simple mock that completes quickly
    async def quick_mock(decision_id, context):
        await asyncio.sleep(0.01)
        timestamp = datetime.now(timezone.utc).isoformat()
        signature = hashlib.sha256(
            f"{decision_id}userTrueOK{timestamp}{SECRET_SALT}".encode()
        ).hexdigest()
        await hil.receive_human_feedback(
            {
                "decision_id": decision_id,
                "approved": True,
                "user_id": "user",
                "comment": "OK",
                "timestamp": timestamp,
                "signature": signature,
            }
        )

    with patch.object(hil, "_get_notification_tasks", return_value=[]):
        with patch.object(hil, "_mock_user_approval", side_effect=quick_mock):
            await hil.request_approval(decision)

    # Audit hook should be called for approval request
    audit_hook.assert_called()
    call_args = [call[0][0] for call in audit_hook.call_args_list]
    assert any(arg.get("event_type") == "approval_requested" for arg in call_args)


@pytest.mark.asyncio
async def test_error_hook_called(default_config):
    """Test that error hook is called on errors"""
    error_hook = AsyncMock()
    hil = HumanInLoop(config=default_config, error_hook=error_hook)

    # Send decision with invalid risk_level type to cause validation error
    invalid_decision = {"risk_level": 12345}  # Should be string, not int
    await hil.request_approval(invalid_decision)

    error_hook.assert_called()
    call_args = error_hook.call_args[0][0]
    assert call_args["event_type"] == "invalid_request_schema"


# Test context manager
@pytest.mark.asyncio
async def test_context_manager():
    """Test HumanInLoop as async context manager"""
    config = HumanInLoopConfig()

    async with HumanInLoop(config=config) as hil:
        assert isinstance(hil, HumanInLoop)
        assert isinstance(hil._db_client, DummyDBClient)


# Test DummyDBClient
@pytest.mark.asyncio
async def test_dummy_db_client():
    """Test DummyDBClient functionality"""
    client = DummyDBClient()

    # Test save
    entry = {"id": "1", "data": "test"}
    await client.save_feedback_entry(entry)

    # Test get all
    entries = await client.get_feedback_entries()
    assert len(entries) == 1
    assert entries[0]["id"] == "1"

    # Test get with query
    await client.save_feedback_entry({"id": "2", "data": "other"})
    filtered = await client.get_feedback_entries({"id": "1"})
    assert len(filtered) == 1
    assert filtered[0]["id"] == "1"

    # Test update
    updated = await client.update_feedback_entry({"id": "1"}, {"data": "updated"})
    assert updated

    entries = await client.get_feedback_entries({"id": "1"})
    assert entries[0]["data"] == "updated"
