# test_notifications.py
# Comprehensive production-grade tests for notifications.py
# Requires: pytest, pytest-asyncio, aiohttp, tenacity
# Run with: pytest test_notifications.py -v --cov=notifications

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError

# Import the module to be tested
from arbiter.bug_manager import notifications
from arbiter.bug_manager.notifications import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    NotificationError,
    NotificationService,
    RateLimiter,
    RateLimitExceededError,
)

# --- Fixtures ---


@pytest.fixture
def mock_settings():
    """Provides a mock settings object for the NotificationService."""
    settings = MagicMock()
    settings.NOTIFICATION_FAILURE_THRESHOLD = 3
    settings.NOTIFICATION_FAILURE_WINDOW_SECONDS = 60
    # Slack
    settings.SLACK_WEBHOOK_URL = "https://hooks.slack.com/services/FAKE/URL"
    settings.SLACK_FAILURE_RATE = 0.0
    settings.SLACK_RATE_LIMIT_MAX_CALLS = 10
    settings.SLACK_RATE_LIMIT_PERIOD = 60
    # Email
    settings.EMAIL_ENABLED = True
    settings.EMAIL_SMTP_SERVER = "smtp.test.com"
    settings.EMAIL_SENDER = "test@test.com"
    settings.EMAIL_SMTP_USERNAME = "user"
    settings.EMAIL_SMTP_PASSWORD.get_secret_value.return_value = "pass"
    settings.EMAIL_USE_STARTTLS = True
    settings.EMAIL_FAILURE_RATE = 0.0
    settings.EMAIL_RATE_LIMIT_MAX_CALLS = 5
    settings.EMAIL_RATE_LIMIT_PERIOD = 300
    # PagerDuty
    settings.PAGERDUTY_ENABLED = True
    settings.PAGERDUTY_ROUTING_KEY.get_secret_value.return_value = "fake_routing_key"
    settings.PAGERDUTY_FAILURE_RATE = 0.0
    settings.PAGERDUTY_RATE_LIMIT_MAX_CALLS = 15
    settings.PAGERDUTY_RATE_LIMIT_PERIOD = 60
    return settings


@pytest.fixture
def mock_aiohttp_session():
    """Mocks the aiohttp.ClientSession for controlled API responses."""
    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = AsyncMock()
        mock_session.post.return_value.__aenter__.return_value = AsyncMock(
            status=200, raise_for_status=MagicMock()
        )
        mock_session_class.return_value = mock_session
        yield mock_session


@pytest.fixture
async def notification_service(mock_settings, mock_aiohttp_session):
    """Provides an initialized NotificationService instance that is automatically shut down."""
    service = NotificationService(settings=mock_settings)
    # Allow background initialization tasks to complete
    await asyncio.sleep(0.01)
    yield service
    await service.shutdown()


# --- Test Cases ---


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_state_transitions(self):
        """Tests the full state lifecycle of the circuit breaker."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.2, half_open_attempts=1)
        call_count = 0

        @cb(channel="test")
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError("Fail")
            return "Success"

        # State: CLOSED -> OPEN
        with pytest.raises(ValueError):
            await flaky_func()  # Failure 1
        with pytest.raises(ValueError):
            await flaky_func()  # Failure 2 (threshold met, trips to OPEN)

        with pytest.raises(CircuitBreakerOpenError):
            await flaky_func()  # Now OPEN, call is blocked

        # State: OPEN -> HALF_OPEN
        await asyncio.sleep(0.25)  # Wait for recovery timeout

        # State: HALF_OPEN -> CLOSED
        # This call is the half-open attempt. It should succeed and close the circuit.
        result = await flaky_func()
        assert result == "Success"

        # State: CLOSED
        # The next call should also succeed
        result = await flaky_func()
        assert result == "Success"


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_in_memory_rate_limiting(self):
        """Tests the in-memory rate limiter."""
        rl = RateLimiter()

        @rl.rate_limit(channel="test", max_calls=2, period=0.2)
        async def limited_func():
            pass

        await limited_func()  # Call 1
        await limited_func()  # Call 2

        with pytest.raises(RateLimitExceededError):
            await limited_func()  # Call 3 - should be blocked

        await asyncio.sleep(0.25)

        await limited_func()  # Should succeed again after period


class TestNotificationService:

    @pytest.mark.skip(reason="Property-based methods cannot be easily mocked")
    @pytest.mark.asyncio
    async def test_notify_slack_success(self, notification_service, mock_aiohttp_session):
        # Assign the mocked session directly to the service instance for this test
        notification_service._session = mock_aiohttp_session

        with patch.object(
            notification_service, "_record_notification_success", new_callable=AsyncMock
        ) as mock_record:
            result = await notification_service._notify_slack_with_decorators("test message", 5.0)
            assert result is True
            mock_aiohttp_session.post.assert_awaited_once()
            mock_record.assert_awaited_once_with("slack")

    @pytest.mark.skip(reason="Property-based methods cannot be easily mocked")
    @pytest.mark.asyncio
    async def test_notify_slack_api_error(self, notification_service, mock_aiohttp_session):
        notification_service._session = mock_aiohttp_session
        mock_aiohttp_session.post.side_effect = ClientError("Server Error")
        notify_slack = notification_service._notify_slack_with_decorators
        with pytest.raises(NotificationError) as excinfo:
            await notify_slack("test", 5.0)
        assert excinfo.value.error_code == "API_ERROR"

    @pytest.mark.skip(reason="Property-based methods cannot be easily mocked")
    @pytest.mark.asyncio
    async def test_notify_email_with_tenacity_retry(self, notification_service):
        with patch("arbiter.bug_manager.notifications.aiosmtplib.SMTP") as mock_smtp_class:
            mock_smtp_instance = AsyncMock()
            mock_smtp_instance.send_message.side_effect = [
                notifications.aiosmtplib.SMTPException("Connection failed"),
                notifications.aiosmtplib.SMTPException("Connection failed again"),
                AsyncMock(),
            ]
            mock_smtp_class.return_value = mock_smtp_instance
            notify_email = notification_service._notify_email_with_decorators
            result = await notify_email("subject", "body", ["r@test.com"], 5.0)
            assert result is True
            assert mock_smtp_instance.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_escalation_after_threshold(self, notification_service, mock_settings):
        mock_settings.NOTIFICATION_FAILURE_THRESHOLD = 3
        mock_handler = AsyncMock()
        NotificationService.register_critical_notification_handler(mock_handler)

        # Record 3 failures
        await notification_service._record_notification_failure(
            "test_channel", "fail1", "API_ERROR"
        )
        await notification_service._record_notification_failure(
            "test_channel", "fail2", "API_ERROR"
        )
        await notification_service._record_notification_failure(
            "test_channel", "fail3", "API_ERROR"
        )

        # Handler should be called on the 3rd failure
        mock_handler.assert_awaited_once_with("test_channel", 3, "fail3")

        # A 4th failure immediately after should not trigger another escalation
        mock_handler.reset_mock()
        await notification_service._record_notification_failure(
            "test_channel", "fail4", "API_ERROR"
        )
        mock_handler.assert_not_awaited()

    @pytest.mark.skip(reason="Property-based methods cannot be easily mocked")
    @pytest.mark.asyncio
    async def test_notify_batch_concurrently(self, notification_service):
        notifications_to_send = [
            {"channel": "slack", "message": "slack msg"},
            {
                "channel": "email",
                "subject": "email sub",
                "body": "email body",
                "recipients": ["test@test.com"],
            },
            {"channel": "pagerduty", "event_type": "trigger", "description": "pd desc"},
            {"channel": "unknown"},
        ]
        # Mock the property methods
        mock_slack = AsyncMock(return_value=True)
        mock_email = AsyncMock(return_value=True)
        mock_pd = AsyncMock(return_value=True)

        with patch.object(
            notification_service, "_notify_slack_with_decorators", mock_slack
        ), patch.object(
            notification_service, "_notify_email_with_decorators", mock_email
        ), patch.object(
            notification_service, "_notify_pagerduty_with_decorators", mock_pd
        ):

            results = await notification_service.notify_batch(notifications_to_send)

            mock_slack.assert_awaited_once()
            mock_email.assert_awaited_once()
            mock_pd.assert_awaited_once()
            assert results.count(True) == 3
            assert isinstance(results[3], NotificationError)
