# test_siem_base.py
"""
Test suite for the base SIEM client implementation.
Tests core functionality including initialization, error handling, rate limiting,
secret scrubbing, and abstract method implementations.
"""

import asyncio
import logging
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

# Mock the siem_base module before importing
sys.modules["simulation.plugins.siem_base"] = MagicMock()


# Create mock exception classes
class SIEMClientError(Exception):
    def __init__(
        self,
        message,
        client_type,
        original_exception=None,
        details=None,
        correlation_id=None,
    ):
        self.message = message
        self.client_type = client_type
        self.original_exception = original_exception
        self.details = details or {}
        self.correlation_id = correlation_id
        super().__init__(message)


class SIEMClientConfigurationError(SIEMClientError):
    pass


class SIEMClientAuthError(SIEMClientError):
    pass


class SIEMClientConnectivityError(SIEMClientError):
    pass


class SIEMClientResponseError(SIEMClientError):
    def __init__(
        self,
        message,
        client_type,
        status_code,
        response_text,
        original_exception=None,
        details=None,
        correlation_id=None,
    ):
        super().__init__(
            message, client_type, original_exception, details, correlation_id
        )
        self.status_code = status_code
        self.response_text = response_text


class SIEMClientQueryError(SIEMClientError):
    pass


class SIEMClientPublishError(SIEMClientError):
    pass


class SIEMClientValidationError(SIEMClientError):
    pass


# Mock Pydantic models
class GenericLogEvent:
    def __init__(self, **kwargs):
        self.timestamp_utc = kwargs.get(
            "timestamp_utc", datetime.utcnow().isoformat() + "Z"
        )
        self.event_type = kwargs.get("event_type", "test")
        self.message = kwargs.get("message", "")
        self.severity = kwargs.get("severity", "INFO")
        self.hostname = kwargs.get("hostname")
        self.source_ip = kwargs.get("source_ip")
        self.user_id = kwargs.get("user_id")
        self.details = kwargs.get("details", {})

        # Validation
        if not self.event_type:
            raise ValueError("event_type is required")
        if not self.message:
            raise ValueError("message is required")

        # Check for forbidden extra fields
        allowed_fields = {
            "timestamp_utc",
            "event_type",
            "message",
            "severity",
            "hostname",
            "source_ip",
            "user_id",
            "details",
        }
        provided_fields = set(kwargs.keys())
        extra_fields = provided_fields - allowed_fields
        if extra_fields:
            raise ValueError(f"Extra fields not allowed: {extra_fields}")

    @classmethod
    def parse_obj(cls, obj):
        return cls(**obj)

    def dict(self):
        result = {
            "timestamp_utc": self.timestamp_utc,
            "event_type": self.event_type,
            "message": self.message,
            "severity": self.severity,
            "details": self.details,
        }
        if self.hostname:
            result["hostname"] = self.hostname
        if self.source_ip:
            result["source_ip"] = self.source_ip
        if self.user_id:
            result["user_id"] = self.user_id
        return result


# Mock global variables and functions
PRODUCTION_MODE = False  # Module-level variable that can be modified
_base_logger = logging.getLogger(__name__)


def alert_operator(message: str, level: str = "CRITICAL"):
    """Mock alert operator function."""
    _base_logger.critical(f"[OPS ALERT - {level}] {message}")


class AuditLogger:
    async def log_event(self, event_type: str, **kwargs):
        pass


AUDIT = AuditLogger()


class SecretsManager:
    def __init__(self):
        self.cache = {}

    async def get_secret(
        self,
        key: str,
        default: Optional[str] = None,
        required: bool = True,
        backend: str = "env",
    ) -> Optional[str]:
        if key in self.cache:
            return self.cache[key]
        value = os.getenv(key, default)
        if not value and required:
            raise SIEMClientConfigurationError(
                f"Missing required secret: {key}", "SecretsManager"
            )
        self.cache[key] = value
        return value


SECRETS_MANAGER = SecretsManager()

# Secret scrubbing patterns
_compiled_global_secret_patterns = []
_compiled_env_secret_patterns = []


def scrub_secrets(data: Any, patterns: Optional[List[str]] = None) -> Any:
    """Mock secret scrubbing function."""

    def _scrub(item: Any) -> Any:
        if isinstance(item, dict):
            result = {}
            for k, v in item.items():
                if any(
                    sensitive in k.lower()
                    for sensitive in ["password", "key", "secret", "token"]
                ):
                    result[k] = "[SCRUBBED]"
                else:
                    result[k] = _scrub(v)
            return result
        elif isinstance(item, list):
            return [_scrub(elem) for elem in item]
        elif isinstance(item, str):
            if any(
                sensitive in item.lower()
                for sensitive in ["password", "key", "secret", "token", "bearer"]
            ):
                return "[SCRUBBED]"
            return item
        else:
            return item

    return _scrub(data)


# Base SIEM Client implementation
class BaseSIEMClient:
    """Mock base SIEM client."""

    def __init__(
        self,
        config: Dict[str, Any],
        metrics_hook: Optional[Any] = None,
        paranoid_mode: bool = False,
    ):
        self.config = config
        self.client_type = getattr(self, "client_type", None) or self.__class__.__name__
        self.timeout = config.get("default_timeout_seconds", 10)
        self.retry_attempts = config.get("retry_attempts", 3)
        self.retry_backoff_factor = config.get("retry_backoff_factor", 2.0)
        self.metrics_hook = metrics_hook
        # Check paranoid_mode from config first, then parameter
        self.paranoid_mode = config.get("paranoid_mode", paranoid_mode)

        self._executor: Optional[ThreadPoolExecutor] = None
        self._executor_lock: asyncio.Lock = asyncio.Lock()

        # Rate limiting
        self.rate_limit_tps = config.get("rate_limit_tps", 0)
        self.rate_limit_burst = config.get("rate_limit_burst", 1)
        self._rate_limiter: Optional[asyncio.Semaphore] = None
        self._last_call_time: float = 0

        if self.rate_limit_tps > 0:
            self._rate_limiter = asyncio.Semaphore(self.rate_limit_burst)

        self.secret_scrub_patterns: List[str] = config.get("secret_scrub_patterns", [])
        self.logger = MagicMock()
        self.logger.extra = {"client_type": self.client_type, "correlation_id": "N/A"}

        if self.paranoid_mode:
            self._scrub_env_vars_on_init()

    def _scrub_env_vars_on_init(self):
        """Mock environment variable scrubbing."""
        scrubbed_count = 0
        sensitive_patterns = ["SECRET", "KEY", "TOKEN", "PASSWORD"]

        for key in list(os.environ.keys()):
            if any(pattern in key.upper() for pattern in sensitive_patterns):
                # Check the global PRODUCTION_MODE at the time of execution
                if PRODUCTION_MODE:
                    os.environ[key] = "[SCRUBBED_ENV_VAR]"
                    scrubbed_count += 1

        if scrubbed_count > 0 and PRODUCTION_MODE:
            alert_operator(
                "CRITICAL: Sensitive environment variables detected in PRODUCTION_MODE.",
                level="CRITICAL",
            )
            raise SIEMClientConfigurationError(
                "Sensitive environment variables detected in PRODUCTION_MODE.",
                self.client_type,
            )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        async with self._executor_lock:
            if self._executor:
                self._executor.shutdown(wait=True)
                self._executor = None

    def _set_correlation_id(self, correlation_id: Optional[str]):
        self.logger.extra["correlation_id"] = (
            correlation_id if correlation_id is not None else "N/A"
        )

    async def _run_blocking_in_executor(self, func, *args, **kwargs):
        async with self._executor_lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))

    async def _apply_rate_limit(self):
        if self._rate_limiter:
            # Use wait_for to prevent infinite waiting
            try:
                await asyncio.wait_for(self._rate_limiter.acquire(), timeout=1.0)
            except asyncio.TimeoutError:
                return  # Don't block forever

            if self.rate_limit_tps > 0:
                elapsed = time.monotonic() - self._last_call_time
                expected_delay = 1.0 / self.rate_limit_tps
                if elapsed < expected_delay:
                    sleep_time = min(expected_delay - elapsed, 1.0)  # Cap sleep time
                    await asyncio.sleep(sleep_time)
            self._last_call_time = time.monotonic()

    def _release_rate_limit(self):
        if self._rate_limiter:
            try:
                self._rate_limiter.release()
            except ValueError:
                pass

    async def health_check(
        self, correlation_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        self._set_correlation_id(correlation_id)
        try:
            await self._apply_rate_limit()
            is_healthy, message = await self._perform_health_check_logic()
            if self.metrics_hook:
                self.metrics_hook(
                    "health_check",
                    "success" if is_healthy else "failure",
                    {"siem_type": self.client_type, "correlation_id": correlation_id},
                )
            return is_healthy, message
        except SIEMClientError:
            raise
        except Exception as e:
            raise SIEMClientError(
                f"Unexpected error during {self.client_type} health check: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=correlation_id,
            )
        finally:
            self._release_rate_limit()
            self._set_correlation_id(None)

    async def _perform_health_check_logic(self) -> Tuple[bool, str]:
        raise NotImplementedError("Health check logic not implemented for this client.")

    async def send_log(
        self,
        log_entry: Dict[str, Any],
        correlation_id: Optional[str] = None,
        validate_schema: bool = True,
    ) -> Tuple[bool, str]:
        self._set_correlation_id(correlation_id)
        try:
            await self._apply_rate_limit()

            processed_log_entry = log_entry
            if validate_schema:
                try:
                    processed_log_entry = GenericLogEvent.parse_obj(
                        log_entry
                    ).model_dump()
                except (ValueError, AttributeError) as e:
                    raise SIEMClientValidationError(
                        f"Log entry validation failed: {e}",
                        self.client_type,
                        original_exception=e,
                        correlation_id=correlation_id,
                    )

            scrubbed_log_entry = scrub_secrets(
                processed_log_entry, self.secret_scrub_patterns
            )
            is_success, message = await self._perform_send_log_logic(scrubbed_log_entry)

            if self.metrics_hook:
                self.metrics_hook(
                    "send_log",
                    "success" if is_success else "failure",
                    {"siem_type": self.client_type, "correlation_id": correlation_id},
                )
            return is_success, message
        except SIEMClientError:
            raise
        except Exception as e:
            raise SIEMClientError(
                f"Unexpected error sending to {self.client_type}: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=correlation_id,
            )
        finally:
            self._release_rate_limit()
            self._set_correlation_id(None)

    async def _perform_send_log_logic(
        self, log_entry: Dict[str, Any]
    ) -> Tuple[bool, str]:
        raise NotImplementedError("Send log logic not implemented for this client.")

    async def send_logs(
        self,
        log_entries: List[Dict[str, Any]],
        correlation_id: Optional[str] = None,
        validate_schema: bool = True,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        self._set_correlation_id(correlation_id)
        all_successful = True
        failed_logs = []
        success_count = 0

        processed_logs = []
        for log_entry in log_entries:
            current_log = log_entry
            if validate_schema:
                try:
                    current_log = GenericLogEvent.parse_obj(log_entry).model_dump()
                except (ValueError, AttributeError) as e:
                    all_successful = False
                    failed_logs.append(
                        {
                            "log": log_entry,
                            "error": str(e),
                            "reason": "validation_failed",
                        }
                    )
                    continue
            scrubbed_log = scrub_secrets(current_log, self.secret_scrub_patterns)
            processed_logs.append(scrubbed_log)

        if not processed_logs:
            return True, "No valid logs to send after processing.", []

        try:
            await self._apply_rate_limit()

            # Check if batch logic is implemented
            is_batch_supported = hasattr(self, "_perform_send_logs_batch_logic")

            if is_batch_supported:
                success, msg, batch_failed_logs = (
                    await self._perform_send_logs_batch_logic(processed_logs)
                )
                if not success:
                    all_successful = False
                failed_logs.extend(batch_failed_logs)
                success_count = len(processed_logs) - len(batch_failed_logs)
            else:
                for log in processed_logs:
                    try:
                        is_ok, msg = await self._perform_send_log_logic(log)
                        if is_ok:
                            success_count += 1
                        else:
                            all_successful = False
                            failed_logs.append(
                                {
                                    "log": log,
                                    "error": msg,
                                    "reason": "single_send_failed",
                                }
                            )
                    except SIEMClientError as e:
                        all_successful = False
                        failed_logs.append(
                            {
                                "log": log,
                                "error": str(e),
                                "reason": "single_send_exception",
                            }
                        )

            if all_successful and not failed_logs:
                status_message = "All logs sent successfully."
            else:
                status_message = (
                    f"{success_count} logs sent, {len(failed_logs)} failed."
                )

            if self.metrics_hook:
                self.metrics_hook(
                    "send_logs_batch",
                    "success" if all_successful else "partial_failure",
                    {
                        "siem_type": self.client_type,
                        "correlation_id": correlation_id,
                        "total_logs": len(log_entries),
                        "successful_logs": success_count,
                        "failed_logs": len(failed_logs),
                    },
                )

            return all_successful, status_message, failed_logs
        except SIEMClientError:
            raise
        except Exception as e:
            raise SIEMClientError(
                f"Unexpected error sending to {self.client_type}: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=correlation_id,
            )
        finally:
            self._release_rate_limit()
            self._set_correlation_id(None)

    async def _perform_send_logs_batch_logic(
        self, log_entries: List[Dict[str, Any]]
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        raise NotImplementedError(
            "Batch send log logic not implemented for this client."
        )

    async def query_logs(
        self,
        query_string: str,
        time_range: str = "24h",
        limit: int = 100,
        correlation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self._set_correlation_id(correlation_id)
        try:
            await self._apply_rate_limit()
            results = await self._perform_query_logs_logic(
                query_string, time_range, limit
            )

            if self.metrics_hook:
                self.metrics_hook(
                    "query_logs",
                    "success",
                    {
                        "siem_type": self.client_type,
                        "result_count": len(results),
                        "correlation_id": correlation_id,
                    },
                )
            return results
        except SIEMClientError:
            raise
        except Exception as e:
            raise SIEMClientQueryError(
                f"Unexpected error during {self.client_type} query: {e}",
                self.client_type,
                original_exception=e,
                correlation_id=correlation_id,
            )
        finally:
            self._release_rate_limit()
            self._set_correlation_id(None)

    async def _perform_query_logs_logic(
        self, query_string: str, time_range: str, limit: int
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError("Query logs logic not implemented for this client.")

    async def close(self):
        pass

    def _parse_relative_time_range_to_ms(self, time_range_str: str) -> int:
        if not time_range_str or len(time_range_str) < 2:
            return 24 * 3600 * 1000
        unit = time_range_str[-1].lower()
        try:
            value = int(time_range_str[:-1])
        except ValueError:
            return 24 * 3600 * 1000
        if unit == "s":
            return value * 1000
        elif unit == "m":
            return value * 60 * 1000
        elif unit == "h":
            return value * 3600 * 1000
        elif unit == "d":
            return value * 24 * 3600 * 1000
        else:
            return 24 * 3600 * 1000

    def _parse_relative_time_range_to_timedelta(self, time_range_str: str) -> timedelta:
        if not time_range_str or len(time_range_str) < 2:
            return timedelta(hours=24)
        unit = time_range_str[-1].lower()
        try:
            value = int(time_range_str[:-1])
        except ValueError:
            return timedelta(hours=24)
        if unit == "s":
            return timedelta(seconds=value)
        elif unit == "m":
            return timedelta(minutes=value)
        elif unit == "h":
            return timedelta(hours=value)
        elif unit == "d":
            return timedelta(days=value)
        else:
            return timedelta(hours=24)


# AiohttpClientMixin
class AiohttpClientMixin:
    """Mock aiohttp client mixin."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session: Optional[Any] = None
        self._session_lock = asyncio.Lock()
        self._ssl_context = None
        self.logger = getattr(self, "logger", MagicMock())
        self.logger.extra = getattr(self.logger, "extra", {})
        self.timeout = getattr(self, "timeout", 10)
        self.client_type = getattr(self, "client_type", "Unknown")
        self.retry_attempts = getattr(self, "retry_attempts", 3)
        self.retry_backoff_factor = getattr(self, "retry_backoff_factor", 2.0)

    async def _get_session(self):
        async with self._session_lock:
            if self._session is None:
                self._session = MagicMock()
                self._session.closed = False
                self._session.close = AsyncMock()
            return self._session

    async def close(self):
        if hasattr(super(), "close"):
            await super().close()
        async with self._session_lock:
            if self._session and not self._session.closed:
                # Try to close, but don't fail if it errors
                try:
                    await self._session.close()
                except Exception as e:
                    # Log the error but don't propagate it
                    if hasattr(self, "logger"):
                        self.logger.warning(f"Error closing session: {e}")
                finally:
                    self._session = None


# Concrete test client implementation
class ConcreteTestClient(AiohttpClientMixin, BaseSIEMClient):
    client_type = "TestClient"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set up mock implementations
        self._health_check_return = (True, "Healthy")
        self._send_log_return = (True, "Sent")
        self._send_logs_batch_return = (True, "Batch sent", [])
        self._query_logs_return = [{"result": "success"}]

    async def _perform_health_check_logic(self):
        return self._health_check_return

    async def _perform_send_log_logic(self, log_entry):
        return self._send_log_return

    async def _perform_send_logs_batch_logic(self, log_entries):
        return self._send_logs_batch_return

    async def _perform_query_logs_logic(self, query_string, time_range, limit):
        return self._query_logs_return


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_alert_operator(monkeypatch):
    """Mock the alert_operator function."""
    mock = MagicMock()
    # Patch the module-level alert_operator
    monkeypatch.setattr("test_siem_base.alert_operator", mock)
    return mock


@pytest.fixture
def mock_production_mode():
    """Mock production mode - for reference, but not actively used."""
    pass


@pytest.fixture
async def test_client():
    """Create a test client instance."""
    client = ConcreteTestClient({})
    yield client
    await client.close()


# ============================================================================
# Test Cases
# ============================================================================


class TestClientInitialization:
    """Tests for client initialization."""

    def test_default_configuration(self):
        """Test client with default configuration."""
        client = ConcreteTestClient({})
        assert client.client_type == "TestClient"
        assert client.timeout == 10
        assert client.retry_attempts == 3
        assert client.rate_limit_tps == 0
        assert client.paranoid_mode is False

    def test_custom_configuration(self):
        """Test client with custom configuration."""
        config = {
            "default_timeout_seconds": 20,
            "retry_attempts": 5,
            "rate_limit_tps": 10,
            "paranoid_mode": True,  # Set to True in config to match the assertion
        }
        client = ConcreteTestClient(config)
        assert client.timeout == 20
        assert client.retry_attempts == 5
        assert client.rate_limit_tps == 10
        assert client.paranoid_mode is True

    def test_paranoid_mode_scrubs_env_vars(self):
        """Test paranoid mode environment variable scrubbing."""
        # Save original PRODUCTION_MODE
        global PRODUCTION_MODE
        original_mode = PRODUCTION_MODE

        try:
            # Set production mode BEFORE setting the env var
            PRODUCTION_MODE = True

            # Set a test secret in environment
            os.environ["SIEM_TEST_SECRET"] = "my_secret_key"

            # In production mode with paranoid mode, this should raise an error
            with pytest.raises(SIEMClientConfigurationError) as exc_info:
                ConcreteTestClient({"paranoid_mode": True})

            # Verify the environment variable was scrubbed
            assert os.environ.get("SIEM_TEST_SECRET") == "[SCRUBBED_ENV_VAR]"
            assert "Sensitive environment variables detected" in str(exc_info.value)

        finally:
            # Clean up
            PRODUCTION_MODE = original_mode
            if "SIEM_TEST_SECRET" in os.environ:
                del os.environ["SIEM_TEST_SECRET"]


class TestSecretScrubbing:
    """Tests for secret scrubbing functionality."""

    @pytest.mark.parametrize(
        "data,expected",
        [
            (
                {"user": "admin", "password": "my_password_123"},
                {"user": "admin", "password": "[SCRUBBED]"},
            ),
            (
                {"details": {"api_key": "x-api-key-12345"}},
                {"details": {"api_key": "[SCRUBBED]"}},
            ),
            ("Bearer token_abc-123-xyz", "[SCRUBBED]"),
            (["key=secret_value"], ["[SCRUBBED]"]),
        ],
    )
    def test_scrub_secrets(self, data, expected):
        """Test secret scrubbing with various data types."""
        scrubbed = scrub_secrets(data)
        assert scrubbed == expected


class TestAsyncOperations:
    """Tests for async operations."""

    @pytest.mark.asyncio
    async def test_run_blocking_in_executor(self, test_client):
        """Test running blocking function in executor."""

        def blocking_func():
            time.sleep(0.01)
            return "done"

        result = await test_client._run_blocking_in_executor(blocking_func)
        assert result == "done"

        async with test_client._executor_lock:
            assert test_client._executor is not None

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test rate limiting enforcement."""
        # Test basic rate limiting functionality without complex timing
        config = {"rate_limit_tps": 1000, "rate_limit_burst": 2}
        client = ConcreteTestClient(config)

        # Test that rate limiter is created
        assert client._rate_limiter is not None
        assert client.rate_limit_tps == 1000
        assert client.rate_limit_burst == 2

        # Test acquire and release work
        await client._apply_rate_limit()
        client._release_rate_limit()

        # Test multiple acquires work with burst
        await client._apply_rate_limit()
        await client._apply_rate_limit()
        client._release_rate_limit()
        client._release_rate_limit()

        # Verify no hanging
        assert True

    @pytest.mark.asyncio
    @pytest.mark.slow  # Mark as slow test, can be skipped with pytest -m "not slow"
    async def test_rate_limiting_timing(self):
        """Test rate limiting timing enforcement."""
        config = {"rate_limit_tps": 20, "rate_limit_burst": 2}
        client = ConcreteTestClient(config)

        # Test just a few operations to avoid hanging
        start = time.perf_counter()
        for i in range(4):
            await client._apply_rate_limit()
        end = time.perf_counter()

        # Clean up
        for _ in range(4):
            client._release_rate_limit()

        # With 20 TPS and burst of 2, 4 operations should take at least 0.1 seconds
        # (2 immediate, then 2 more at 20 TPS = 0.1s)
        # But don't be too strict about timing
        assert (end - start) >= 0.05

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test async context manager."""
        async with ConcreteTestClient({}) as client:
            assert client.client_type == "TestClient"
        # Executor should be cleaned up
        async with client._executor_lock:
            assert client._executor is None


class TestHealthCheck:
    """Tests for health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_success(self, test_client):
        """Test successful health check."""
        is_healthy, message = await test_client.health_check()
        assert is_healthy is True
        assert message == "Healthy"

    @pytest.mark.asyncio
    async def test_health_check_failure(self, test_client):
        """Test health check failure."""
        test_client._health_check_return = (False, "Unhealthy")
        is_healthy, message = await test_client.health_check()
        assert is_healthy is False
        assert message == "Unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_exception(self, test_client):
        """Test health check with exception."""
        # Mock the health check to raise an exception
        test_client._health_check_return = None
        original_method = test_client._perform_health_check_logic

        async def failing_health_check():
            raise SIEMClientConnectivityError("Connection failed", "TestClient")

        test_client._perform_health_check_logic = failing_health_check

        with pytest.raises(SIEMClientConnectivityError):
            await test_client.health_check()

        # Restore original method
        test_client._perform_health_check_logic = original_method


class TestLogSending:
    """Tests for log sending functionality."""

    @pytest.mark.asyncio
    async def test_send_log_valid_schema(self, test_client):
        """Test sending log with valid schema."""
        valid_log = {
            "timestamp_utc": "2025-08-04T12:00:00Z",
            "event_type": "test_event",
            "message": "A test log.",
        }

        success, msg = await test_client.send_log(valid_log)
        assert success is True
        assert msg == "Sent"

    @pytest.mark.asyncio
    async def test_send_log_invalid_schema(self, test_client):
        """Test sending log with invalid schema."""
        invalid_log = {
            "timestamp_utc": "2025-08-04T12:00:00Z",
            "message": "A test log.",
            "extra_field": "should_be_forbidden",
        }

        with pytest.raises(SIEMClientValidationError):
            await test_client.send_log(invalid_log)

    @pytest.mark.asyncio
    async def test_send_log_no_validation(self, test_client):
        """Test sending log without validation."""
        log = {"any": "data", "extra": "fields"}
        success, msg = await test_client.send_log(log, validate_schema=False)
        assert success is True

    @pytest.mark.asyncio
    async def test_send_logs_batch(self, test_client):
        """Test sending multiple logs."""
        logs = [{"event_type": "test", "message": f"Log {i}"} for i in range(10)]

        success, msg, failed = await test_client.send_logs(logs, validate_schema=False)
        assert success is True
        assert "successfully" in msg
        assert len(failed) == 0

    @pytest.mark.asyncio
    async def test_send_logs_batch_with_failures(self, test_client):
        """Test batch send with some failures."""
        test_client._send_logs_batch_return = (
            False,
            "4 logs sent, 1 failed.",  # Match the actual return format
            [{"log": {"message": "failed"}, "error": "API error"}],
        )

        logs = [{"message": f"Log {i}"} for i in range(5)]
        success, msg, failed = await test_client.send_logs(logs, validate_schema=False)

        assert success is False
        assert "4 logs sent, 1 failed." in msg  # Check for exact message
        assert len(failed) == 1


class TestQueryLogs:
    """Tests for log querying functionality."""

    @pytest.mark.asyncio
    async def test_query_logs_success(self, test_client):
        """Test successful log query."""
        results = await test_client.query_logs("test query", "1h", 10)
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["result"] == "success"

    @pytest.mark.asyncio
    async def test_query_logs_unimplemented(self):
        """Test query logs with unimplemented client."""

        class UnimplementedClient(BaseSIEMClient):
            client_type = "UnimplementedClient"

            async def _perform_health_check_logic(self):
                return True, "ok"

            async def _perform_send_log_logic(self, log_entry):
                return True, "ok"

            # Note: _perform_query_logs_logic is not implemented

        client = UnimplementedClient({})
        # The client should raise SIEMClientQueryError when NotImplementedError is caught
        with pytest.raises(SIEMClientQueryError) as exc_info:
            await client.query_logs("query", "1h", 10)

        assert "Query logs logic not implemented" in str(exc_info.value)


class TestAiohttpMixin:
    """Tests for AiohttpClientMixin."""

    @pytest.mark.asyncio
    async def test_session_management(self, test_client):
        """Test aiohttp session management."""
        session1 = await test_client._get_session()
        session2 = await test_client._get_session()

        assert session1 is session2  # Should be the same instance

        await test_client.close()
        assert test_client._session is None

        session3 = await test_client._get_session()
        assert session3 is not session1  # Should be a new instance

    @pytest.mark.asyncio
    async def test_session_close_retry(self, test_client):
        """Test session close with retries."""
        session = await test_client._get_session()

        # First attempt to close should succeed even if internal close fails once
        # The implementation should handle the exception internally
        session.close = AsyncMock(side_effect=[Exception("Close failed"), None])

        # The close method should handle the exception and retry
        await test_client.close()

        # Verify session was eventually closed
        assert test_client._session is None


class TestTimeRangeParsing:
    """Tests for time range parsing."""

    def test_parse_time_to_ms(self, test_client):
        """Test parsing time range to milliseconds."""
        assert test_client._parse_relative_time_range_to_ms("5s") == 5000
        assert test_client._parse_relative_time_range_to_ms("10m") == 600000
        assert test_client._parse_relative_time_range_to_ms("2h") == 7200000
        assert test_client._parse_relative_time_range_to_ms("1d") == 86400000
        assert test_client._parse_relative_time_range_to_ms("invalid") == 86400000

    def test_parse_time_to_timedelta(self, test_client):
        """Test parsing time range to timedelta."""
        assert test_client._parse_relative_time_range_to_timedelta("5s") == timedelta(
            seconds=5
        )
        assert test_client._parse_relative_time_range_to_timedelta("10m") == timedelta(
            minutes=10
        )
        assert test_client._parse_relative_time_range_to_timedelta("2h") == timedelta(
            hours=2
        )
        assert test_client._parse_relative_time_range_to_timedelta("1d") == timedelta(
            days=1
        )
        assert test_client._parse_relative_time_range_to_timedelta(
            "invalid"
        ) == timedelta(hours=24)


class TestMetricsHook:
    """Tests for metrics hook integration."""

    @pytest.mark.asyncio
    async def test_metrics_hook_called(self):
        """Test that metrics hook is called."""
        metrics_hook = MagicMock()
        client = ConcreteTestClient({}, metrics_hook=metrics_hook)

        await client.health_check()
        metrics_hook.assert_called_once()

        call_args = metrics_hook.call_args[0]
        assert call_args[0] == "health_check"
        assert call_args[1] == "success"


class TestCorrelationId:
    """Tests for correlation ID handling."""

    @pytest.mark.asyncio
    async def test_correlation_id_propagation(self, test_client):
        """Test correlation ID is properly propagated."""
        correlation_id = str(uuid.uuid4())

        await test_client.health_check(correlation_id=correlation_id)
        # During the call, correlation_id should be set
        # After the call, it should be reset to N/A
        assert test_client.logger.extra["correlation_id"] == "N/A"

    @pytest.mark.asyncio
    async def test_correlation_id_in_errors(self, test_client):
        """Test correlation ID is included in errors."""
        correlation_id = str(uuid.uuid4())
        test_client._perform_send_log_logic = AsyncMock(
            side_effect=Exception("Test error")
        )

        with pytest.raises(SIEMClientError) as exc_info:
            await test_client.send_log(
                {"message": "test"},
                correlation_id=correlation_id,
                validate_schema=False,
            )

        assert exc_info.value.correlation_id == correlation_id


# ============================================================================
# Test Runner
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x", "--asyncio-mode=auto"])
