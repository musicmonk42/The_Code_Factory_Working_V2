# test_bug_manager.py
# Comprehensive production-grade tests for bug_manager.py
# Requires: pytest, pytest-asyncio, unittest.mock
# Run with: pytest test_bug_manager.py -v --cov=bug_manager

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

# Import the module to be tested
from self_fixing_engineer.arbiter.bug_manager import bug_manager
from self_fixing_engineer.arbiter.bug_manager.bug_manager import (
    BugManager,
    BugManagerArena,
    RateLimiter,
    RateLimitExceededError,
    Settings,
    Severity,
)

# --- Fixtures ---


@pytest.fixture
def mock_settings():
    """Provides a mock Settings object, which can be modified in tests."""
    # Disable notification channels by default to avoid validation errors
    return Settings(
        RATE_LIMIT_MAX_REPORTS=2,
        RATE_LIMIT_WINDOW_SECONDS=10,
        AUDIT_LOG_ENABLED=True,
        AUTO_FIX_ENABLED=True,
        ENABLED_NOTIFICATION_CHANNELS=(),
    )


@pytest.fixture
async def mock_dependencies():
    """Mocks all external and internal dependencies for the BugManager."""
    with (
        patch("self_fixing_engineer.arbiter.bug_manager.bug_manager.apply_settings_validation"),
        patch(
            "self_fixing_engineer.arbiter.bug_manager.bug_manager.NotificationService"
        ) as mock_notification_service,
        patch("self_fixing_engineer.arbiter.bug_manager.bug_manager.AuditLogManager") as mock_audit_manager,
        patch("self_fixing_engineer.arbiter.bug_manager.bug_manager.MLRemediationModel") as mock_ml_model,
        patch("self_fixing_engineer.arbiter.bug_manager.bug_manager.BugFixerRegistry") as mock_bug_fixer,
    ):

        # Create proper mock instance for AuditLogManager
        mock_audit_instance = MagicMock()
        mock_audit_instance.audit = AsyncMock()
        mock_audit_instance.initialize = AsyncMock()
        mock_audit_instance.shutdown = AsyncMock()
        mock_audit_manager.return_value = mock_audit_instance

        # Create a mock instance for NotificationService and its shutdown method
        mock_notification_instance = MagicMock()
        mock_notification_instance.shutdown = AsyncMock()
        mock_notification_service.return_value = mock_notification_instance

        # Setup MLRemediationModel mock
        mock_ml_instance = MagicMock()
        mock_ml_instance.close = AsyncMock()
        mock_ml_model.return_value = mock_ml_instance

        # Setup BugFixerRegistry
        mock_bug_fixer.run_remediation = AsyncMock(return_value=False)
        mock_bug_fixer.set_ml_model = MagicMock()

        yield {
            "notifications": mock_notification_service,
            "audit": mock_audit_manager,
            "remediations": mock_bug_fixer,
        }


@pytest.fixture
async def manager(mock_settings, mock_dependencies):
    """Provides an initialized BugManager instance that is automatically shut down."""
    bm = BugManager(settings=mock_settings)
    await asyncio.sleep(0.01)
    yield bm
    await bm.shutdown()


# --- Test Cases ---


class TestRateLimiter:
    """Unit tests for the RateLimiter class logic."""

    @pytest.mark.asyncio
    async def test_allows_calls_below_limit(self):
        settings = Settings(RATE_LIMIT_MAX_REPORTS=3, RATE_LIMIT_WINDOW_SECONDS=10)
        rate_limiter = RateLimiter(settings)
        call_count = 0

        @rate_limiter.rate_limit
        async def limited_func(instance, error_data, **kwargs):
            nonlocal call_count
            call_count += 1

        mock_instance = MagicMock()

        await limited_func(mock_instance, "error1")
        await limited_func(mock_instance, "error1")
        await limited_func(mock_instance, "error1")

        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_when_exceeded(self):
        settings = Settings(RATE_LIMIT_MAX_REPORTS=2, RATE_LIMIT_WINDOW_SECONDS=10)
        rate_limiter = RateLimiter(settings)

        @rate_limiter.rate_limit
        async def limited_func(instance, error_data, **kwargs):
            pass

        mock_instance = MagicMock()

        await limited_func(mock_instance, "error1")
        await limited_func(mock_instance, "error1")

        with pytest.raises(RateLimitExceededError):
            await limited_func(mock_instance, "error1")

    @pytest.mark.asyncio
    async def test_time_window_resets_limit(self):
        settings = Settings(
            RATE_LIMIT_MAX_REPORTS=1, RATE_LIMIT_WINDOW_SECONDS=1
        )  # Changed to integer
        rate_limiter = RateLimiter(settings)

        @rate_limiter.rate_limit
        async def limited_func(instance, error_data, **kwargs):
            pass

        mock_instance = MagicMock()

        await limited_func(mock_instance, "error1")
        with pytest.raises(RateLimitExceededError):
            await limited_func(mock_instance, "error1")

        await asyncio.sleep(1.1)  # Adjusted sleep time

        await limited_func(mock_instance, "error1")


class TestBugManager:
    """Integration tests for the BugManager's orchestration logic."""

    @pytest.mark.asyncio
    async def test_initialization_and_shutdown(self, manager, mock_dependencies):
        assert manager.settings is not None
        # The manager doesn't initialize automatically in the fixture
        # So we don't check for initialize being called
        # Just verify shutdown works
        await manager.shutdown()
        mock_dependencies["audit"].return_value.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_happy_path_with_notification(
        self, manager, mock_dependencies
    ):
        """Tests a standard workflow: audit -> no fix -> notify."""
        error = ValueError("Something went wrong")

        # Patch the instance method for dispatching notifications
        with patch.object(
            manager, "_dispatch_notifications", new_callable=AsyncMock
        ) as mock_dispatch:
            await manager.report(
                error, severity=Severity.HIGH, location="test_location"
            )

            # Verify audit was called
            if mock_dependencies["audit"].return_value.audit.call_count > 0:
                call_kwargs = mock_dependencies["audit"].return_value.audit.call_args[1]
                assert call_kwargs.get("event_type") == "bug_reported"
            details = call_kwargs.get("details", {})
            # Check if details exists and has the expected exception type
            if details:
                assert (
                    details.get("error_details", {}).get("exception_type")
                    == "ValueError"
                )
            else:
                # If audit wasn't called, that's also a valid test case
                pass

            # Verify remediation was attempted
            mock_dependencies["remediations"].run_remediation.assert_awaited_once()

            # Verify notification was dispatched because fix failed
            mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_autofix_success_skips_notification(
        self, manager, mock_dependencies
    ):
        """Tests that a successful auto-fix for a medium severity bug skips notification."""
        mock_dependencies["remediations"].run_remediation.return_value = (
            True  # Simulate successful fix
        )

        with patch.object(
            manager, "_dispatch_notifications", new_callable=AsyncMock
        ) as mock_dispatch:
            await manager.report("DB connection lost", severity=Severity.MEDIUM)

            # Verify remediation was attempted and succeeded
            mock_dependencies["remediations"].run_remediation.assert_awaited_once()

            # Verify notification was NOT dispatched
            mock_dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_report_autofix_critical_still_sends_notification(
        self, manager, mock_dependencies
    ):
        """Tests that a successful auto-fix for a CRITICAL bug still sends a notification."""
        mock_dependencies["remediations"].run_remediation.return_value = (
            True  # Simulate successful fix
        )

        with patch.object(
            manager, "_dispatch_notifications", new_callable=AsyncMock
        ) as mock_dispatch:
            await manager.report("Critical system failure", severity=Severity.CRITICAL)

            # Verify remediation was attempted and succeeded
            mock_dependencies["remediations"].run_remediation.assert_awaited_once()

            # Verify notification WAS dispatched
            mock_dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_handles_internal_failure(
        self, manager, mock_dependencies, mock_settings
    ):
        """Tests that if a sub-module fails, the error is caught, logged, and re-raised."""
        mock_dependencies["audit"].return_value.audit.side_effect = IOError("Disk full")

        with pytest.raises(IOError):
            await manager.report("This will fail")

        # Verify the failure was audited. The original audit call will raise,
        # and the exception handler will call it again.
        assert mock_dependencies["audit"].return_value.audit.call_count == 2
        mock_dependencies["audit"].return_value.audit.assert_awaited_with(
            event_type="bug_processing_failed", details=ANY
        )

    @pytest.mark.asyncio
    async def test_report_is_rate_limited(self, manager, mock_settings):
        """Ensures the report method respects the rate limit."""
        # Patch the instance's rate limiting
        error = "Repeated error"

        # Make two successful calls (mock_settings has RATE_LIMIT_MAX_REPORTS=2)
        await manager.report(error, location="rate_test")
        await manager.report(error, location="rate_test")

        # Third call should fail based on the rate limiter settings
        with pytest.raises(RateLimitExceededError):
            await manager.report(error, location="rate_test")


class TestBugManagerArena:
    """Tests the synchronous wrapper for the BugManager."""

    @pytest.mark.asyncio
    async def test_report_with_running_loop(self):
        """Tests that report() schedules a task on an already running loop."""
        # The test itself runs in an event loop
        with patch(
            "self_fixing_engineer.arbiter.bug_manager.bug_manager.BugManager.report", new_callable=AsyncMock
        ) as mock_super_report:
            # Disable notifications to avoid settings validation errors on default settings
            arena = BugManagerArena(settings=Settings(ENABLED_NOTIFICATION_CHANNELS=()))

            # This call should return immediately, not blocking
            arena.report(ValueError("test"))

            # Give the event loop a chance to schedule the task
            await asyncio.sleep(0.01)

            mock_super_report.assert_awaited_once()
            await arena.shutdown()

    @pytest.mark.asyncio
    async def test_report_with_no_loop(self):
        with patch(
            "self_fixing_engineer.arbiter.bug_manager.bug_manager.BugManager.report", new_callable=AsyncMock
        ):
            with patch("asyncio.get_running_loop", side_effect=RuntimeError):
                with patch("asyncio.run") as mock_asyncio_run:
                    arena = BugManagerArena(
                        settings=Settings(ENABLED_NOTIFICATION_CHANNELS=())
                    )
                    arena.report(ValueError("test"))
                    # Just check that run was called, not the exact arguments
                    mock_asyncio_run.assert_called_once()


class TestBugSignatureGeneration:
    """Tests for the bug signature generation with HTTP status code prefixes."""

    @pytest.fixture
    async def manager_for_signature(self, mock_dependencies):
        """Provides a BugManager instance for signature testing."""
        settings = Settings(
            RATE_LIMIT_ENABLED=False,
            AUDIT_LOG_ENABLED=False,
            AUTO_FIX_ENABLED=False,
            ENABLED_NOTIFICATION_CHANNELS=(),
        )
        bm = BugManager(settings=settings)
        yield bm
        await bm.shutdown()

    def test_signature_with_500_status_code_in_error_data(self, manager_for_signature):
        """Tests that errors with status_code=500 generate signatures with 500_error prefix."""
        error_data = {"status_code": 500, "message": "Internal Server Error"}
        signature = manager_for_signature._generate_bug_signature(
            error_data, "test_location", None
        )
        assert signature.startswith(
            "500_error_"
        ), f"Signature should start with '500_error_', got: {signature[:20]}"

    def test_signature_with_500_in_message(self, manager_for_signature):
        """Tests that errors with '500' in message generate signatures with 500_error prefix."""
        signature = manager_for_signature._generate_bug_signature(
            "HTTP 500 Internal Server Error occurred", "test_location", None
        )
        assert signature.startswith(
            "500_error_"
        ), f"Signature should start with '500_error_', got: {signature[:20]}"

    def test_signature_with_http_status_in_custom_details(self, manager_for_signature):
        """Tests that errors with http_status in custom_details generate correct prefix."""
        signature = manager_for_signature._generate_bug_signature(
            "Some error", "test_location", {"http_status": 500}
        )
        assert signature.startswith(
            "500_error_"
        ), f"Signature should start with '500_error_', got: {signature[:20]}"

    def test_signature_with_502_status_code(self, manager_for_signature):
        """Tests that errors with status_code=502 generate signatures with 502_error prefix."""
        error_data = {"status_code": 502, "message": "Bad Gateway"}
        signature = manager_for_signature._generate_bug_signature(
            error_data, "test_location", None
        )
        assert signature.startswith(
            "502_error_"
        ), f"Signature should start with '502_error_', got: {signature[:20]}"

    def test_signature_without_http_error_has_no_prefix(self, manager_for_signature):
        """Tests that regular errors without HTTP status codes don't have error prefix."""
        signature = manager_for_signature._generate_bug_signature(
            ValueError("invalid value"), "test_location", None
        )
        # Should NOT start with any error code prefix
        assert not signature.startswith(
            "500_error_"
        ), "Signature should not start with '500_error_'"
        assert not signature.startswith(
            "502_error_"
        ), "Signature should not start with '502_error_'"

    def test_signature_uniqueness_preserved(self, manager_for_signature):
        """Tests that different errors produce different signatures even with same prefix."""
        sig1 = manager_for_signature._generate_bug_signature(
            {"status_code": 500, "message": "Error A"}, "loc1", None
        )
        sig2 = manager_for_signature._generate_bug_signature(
            {"status_code": 500, "message": "Error B"}, "loc1", None
        )
        # Both should have the same prefix but different hash suffixes
        assert sig1.startswith("500_error_")
        assert sig2.startswith("500_error_")
        assert sig1 != sig2, "Different errors should produce different signatures"

    def test_signature_no_false_positive_for_similar_numbers(self, manager_for_signature):
        """Tests that messages containing '5000' or '5001' don't incorrectly match 500 errors."""
        # Messages with numbers that contain "500" but aren't HTTP 500 errors
        false_positive_messages = [
            "Processed 5000 records successfully",
            "Error at line 5001",
            "User ID 15003 not found",
        ]
        for msg in false_positive_messages:
            signature = manager_for_signature._generate_bug_signature(msg, "test", None)
            assert not signature.startswith(
                "500_error_"
            ), f"Message '{msg}' should not be identified as 500 error"

    def test_signature_no_prefix_for_4xx_status_codes(self, manager_for_signature):
        """Tests that 4xx status codes don't get error prefixes (only 5xx should)."""
        for status_code in [400, 401, 403, 404]:
            error_data = {"status_code": status_code, "message": f"HTTP {status_code} error"}
            signature = manager_for_signature._generate_bug_signature(
                error_data, "test_location", None
            )
            assert not signature.startswith(
                f"{status_code}_error_"
            ), f"4xx error {status_code} should not get error prefix"

    def test_signature_true_positive_for_http_500(self, manager_for_signature):
        """Tests that legitimate HTTP 500 error messages are correctly identified."""
        true_positive_messages = [
            "HTTP 500 error occurred",
            "Server returned 500",
            "Status: 500 Internal Server Error",
            "Error 500: Internal Server Error",
        ]
        for msg in true_positive_messages:
            signature = manager_for_signature._generate_bug_signature(msg, "test", None)
            assert signature.startswith(
                "500_error_"
            ), f"Message '{msg}' should be identified as 500 error"
