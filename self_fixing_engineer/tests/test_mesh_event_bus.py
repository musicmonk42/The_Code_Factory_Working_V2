# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_event_bus.py
"""
Complete test suite for event_bus module with proper cleanup.
"""

import asyncio
import atexit
import hashlib
import hmac
import importlib
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Set environment BEFORE any imports
os.environ["PYTEST_CURRENT_TEST"] = "test"
os.environ["PROD_MODE"] = "false"
os.environ["REDIS_URL"] = "redis://localhost:6379"
os.environ["EVENT_BUS_ENCRYPTION_KEY"] = "Ek3R7wjjB6pgYlCjzvl8xu4OhRxLgLglVuVvvvD8WmY="
os.environ["EVENT_BUS_HMAC_KEY"] = "test_hmac_key_32_chars_minimum!!"
os.environ["REDIS_USER"] = "test_user"
os.environ["REDIS_PASSWORD"] = "test_password"
os.environ["TENANT"] = "test_tenant"
os.environ["ENV"] = "test"
os.environ["USE_REDIS_STREAMS"] = "false"
os.environ["EVENT_BUS_MAX_RETRIES"] = "3"
os.environ["EVENT_BUS_RETRY_DELAY"] = "0.01"


# Create REAL exception classes that inherit from BaseException
class ConnectionError(Exception):
    pass


class TimeoutError(Exception):
    pass


class RedisError(Exception):
    pass


class ResponseError(Exception):
    pass


# Create proper mock classes for tracer BEFORE any module setup
class MockSpan:
    def set_attribute(self, key, value):
        pass

    def set_status(self, status):
        pass

    def add_event(self, name, attributes=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MockTracer:
    def start_as_current_span(self, name, **kwargs):
        class SpanContext:
            def __enter__(self_):
                return MockSpan()

            def __exit__(self_, *args):
                pass

        return SpanContext()


# Store original modules for restoration
_ORIGINAL_MODULES = {}


# Setup mocks for dependencies EXCEPT Prometheus metrics
def setup_mocks():

    # Save original modules before mocking
    modules_to_mock = [
        "redis",
        "redis.asyncio",
        "redis.exceptions",
        "aiolimiter",
        "opentelemetry",
        "opentelemetry.trace",
        "opentelemetry.instrumentation",
        "opentelemetry.instrumentation.asyncio",
        "opentelemetry.instrumentation.redis",
        "opentelemetry.sdk",
        "opentelemetry.sdk.resources",
        "opentelemetry.sdk.trace",
        "opentelemetry.sdk.trace.export",
        "opentelemetry.exporter",
        "opentelemetry.exporter.otlp",
        "opentelemetry.exporter.otlp.proto",
        "opentelemetry.exporter.otlp.proto.http",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    ]
    for mod in modules_to_mock:
        if mod in sys.modules:
            _ORIGINAL_MODULES[mod] = sys.modules[mod]

    # Redis mocks with real exceptions
    mock_redis = MagicMock()
    mock_redis_async = MagicMock()
    mock_redis_exceptions = MagicMock()
    mock_redis_exceptions.ConnectionError = ConnectionError
    mock_redis_exceptions.TimeoutError = TimeoutError
    mock_redis_exceptions.RedisError = RedisError
    mock_redis_exceptions.ResponseError = ResponseError

    sys.modules["redis"] = mock_redis
    sys.modules["redis.asyncio"] = mock_redis_async
    sys.modules["redis.asyncio"].Redis = MagicMock()
    sys.modules["redis.asyncio"].ConnectionPool = MagicMock()
    sys.modules["redis.asyncio"].ClusterConnectionPool = MagicMock()
    sys.modules["redis.exceptions"] = mock_redis_exceptions

    # NOTE: Do NOT mock cryptography.fernet - other tests need the real module

    # Other dependency mocks
    sys.modules["aiolimiter"] = MagicMock()

    # OpenTelemetry mocks
    mock_trace = MagicMock()
    mock_trace.get_tracer = MagicMock(return_value=MockTracer())

    for module in [
        "opentelemetry",
        "opentelemetry.trace",
        "opentelemetry.instrumentation",
        "opentelemetry.instrumentation.asyncio",
        "opentelemetry.instrumentation.redis",
        "opentelemetry.sdk",
        "opentelemetry.sdk.resources",
        "opentelemetry.sdk.trace",
        "opentelemetry.sdk.trace.export",
        "opentelemetry.exporter",
        "opentelemetry.exporter.otlp",
        "opentelemetry.exporter.otlp.proto",
        "opentelemetry.exporter.otlp.proto.http",
        "opentelemetry.exporter.otlp.proto.http.trace_exporter",
    ]:
        sys.modules[module] = MagicMock()

    sys.modules["opentelemetry.trace"] = mock_trace


# Call mocks setup
setup_mocks()

# Import event_bus directly
current_dir = Path(__file__).parent
parent_dir = current_dir.parent
event_bus_path = parent_dir / "mesh" / "event_bus.py"

spec = importlib.util.spec_from_file_location("event_bus", event_bus_path)
event_bus = importlib.util.module_from_spec(spec)

# Inject fixed dependencies
event_bus.ConnectionError = ConnectionError
event_bus.TimeoutError = TimeoutError
event_bus.RedisError = RedisError
event_bus.ResponseError = ResponseError

# Override tracer
event_bus.tracer = MockTracer()
if hasattr(event_bus, "MockTracer"):
    event_bus.MockTracer = MockTracer

# Set prometheus async flag to FALSE to use sync mocks
event_bus.PROMETHEUS_ASYNC_AVAILABLE = False

# Load the module
spec.loader.exec_module(event_bus)

# Force REDIS_AVAILABLE to True after module loading
event_bus.REDIS_AVAILABLE = True

# Register module
sys.modules["event_bus"] = event_bus


# Set up Prometheus mocks after module is loaded (using SYNC mocks since we set PROMETHEUS_ASYNC_AVAILABLE to False)
def setup_prometheus_mocks():
    mock_counter = MagicMock()
    mock_counter.labels = MagicMock(return_value=MagicMock())
    mock_counter.labels().inc = MagicMock()

    mock_gauge = MagicMock()
    mock_gauge.set = MagicMock()

    mock_histogram = MagicMock()
    mock_histogram.observe = MagicMock()

    event_bus.EVENTS_PUBLISHED = mock_counter
    event_bus.EVENTS_SUBSCRIBED = mock_counter
    event_bus.PUBLISH_LATENCY = mock_histogram
    event_bus.SUBSCRIBE_LATENCY = mock_histogram
    event_bus.BUS_LIVENESS = mock_gauge

    # Override the metric functions to use sync versions
    async def mock_inc_counter(counter, **labels):
        counter.labels(**labels).inc()

    async def mock_set_gauge(gauge, value):
        gauge.set(value)

    event_bus._inc_counter = mock_inc_counter
    event_bus._set_gauge = mock_set_gauge


setup_prometheus_mocks()

# Import functions
publish_event = event_bus.publish_event
publish_events = event_bus.publish_events
subscribe_event = event_bus.subscribe_event
replay_dlq = event_bus.replay_dlq
CircuitBreaker = event_bus.CircuitBreaker
AsyncSafeLogger = event_bus.AsyncSafeLogger
get_redis_client = event_bus.get_redis_client

# Track all created loggers
created_loggers = []
original_logger_init = AsyncSafeLogger.__init__


def tracked_init(self, *args, **kwargs):
    original_logger_init(self, *args, **kwargs)
    created_loggers.append(self)


AsyncSafeLogger.__init__ = tracked_init


# Cleanup function to be called at test session end
def cleanup_all_loggers():
    """Stop all logger threads"""
    # Stop any test-created loggers
    for logger in created_loggers:
        if hasattr(logger, "_started") and logger._started:
            try:
                logger.stop()
            except:
                pass

    # Stop the module-level logger
    if hasattr(event_bus, "logger"):
        try:
            event_bus.logger.stop()
        except:
            pass

    # Give threads time to terminate
    time.sleep(0.3)


# Register cleanup to run at interpreter exit
atexit.register(cleanup_all_loggers)


# Fixtures
@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    """Session-scoped fixture to ensure cleanup at the end"""
    yield
    cleanup_all_loggers()


@pytest.fixture(autouse=True)
def patch_tracer():
    original = event_bus.tracer
    event_bus.tracer = MockTracer()
    yield
    event_bus.tracer = original


@pytest.fixture
def mock_redis_client():
    client = AsyncMock()
    client.publish = AsyncMock(return_value=1)
    client.xadd = AsyncMock(return_value=b"123-0")
    client.xread = AsyncMock(return_value=[])
    client.xreadgroup = AsyncMock(return_value=[])
    client.xgroup_create = AsyncMock()
    client.xack = AsyncMock()
    client.xdel = AsyncMock()
    client.xautoclaim = AsyncMock(return_value=(None, []))
    client.xpending_range = AsyncMock(return_value=[])
    client.ping = AsyncMock()
    client.close = AsyncMock()

    pipeline = AsyncMock()
    pipeline.execute = AsyncMock(return_value=[1, 1])
    pipeline.publish = AsyncMock()
    pipeline.xadd = AsyncMock()
    client.pipeline = MagicMock()
    client.pipeline.return_value.__aenter__ = AsyncMock(return_value=pipeline)
    client.pipeline.return_value.__aexit__ = AsyncMock(return_value=None)

    # Create a properly cancellable pubsub mock
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()

    # Make get_message cancellable
    async def get_message_impl(**kwargs):
        # Check if we're being cancelled
        try:
            await asyncio.sleep(0)
            return None
        except asyncio.CancelledError:
            raise

    pubsub.get_message = AsyncMock(side_effect=get_message_impl)
    client.pubsub = MagicMock(return_value=pubsub)

    return client


@pytest.fixture
def reset_circuit_breaker():
    if hasattr(event_bus, "circuit_breaker"):
        event_bus.circuit_breaker = CircuitBreaker()
    yield
    if hasattr(event_bus, "circuit_breaker"):
        event_bus.circuit_breaker = CircuitBreaker()


# Tests
class TestAsyncSafeLogger:
    def test_logger_creation(self):
        logger = AsyncSafeLogger("test_create")
        assert logger.name == "test_create"
        # Don't start it, just test creation

    def test_logger_operations(self):
        logger = AsyncSafeLogger("test_ops")
        try:
            logger.start()
            logger.info("test message")
            logger.error("error message")
            # Give time for messages to be processed
            time.sleep(0.1)
        finally:
            # Always stop the logger
            logger.stop()
            # Wait for thread to terminate
            time.sleep(0.1)


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker()
        assert cb.can_proceed() is True
        assert cb.is_open is False

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True
        assert cb.can_proceed() is False


class TestPublishing:
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_basic_publish(self, mock_redis_client, reset_circuit_breaker):
        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            await publish_event("test", {"data": "value"})
            mock_redis_client.publish.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_publish_with_retry(self, mock_redis_client, reset_circuit_breaker):
        mock_redis_client.publish.side_effect = [
            ConnectionError(),
            ConnectionError(),
            1,
        ]
        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await publish_event("test", {"data": "value"})
                assert mock_redis_client.publish.call_count == 3

    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_publish_batch(self, mock_redis_client, reset_circuit_breaker):
        events = [
            {"event_type": "e1", "data": {"a": 1}},
            {"event_type": "e2", "data": {"b": 2}},
        ]
        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            await publish_events(events)
            mock_redis_client.pipeline.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_publish_fails_after_max_retries(
        self, mock_redis_client, reset_circuit_breaker
    ):
        mock_redis_client.publish.side_effect = ConnectionError()
        mock_redis_client.xadd.return_value = b"dlq-1"
        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(
                    RuntimeError, match="Event publish failed permanently"
                ):
                    await publish_event("test", {"data": "value"})
                assert mock_redis_client.publish.call_count == 3
                mock_redis_client.xadd.assert_called()


class TestSubscription:
    @pytest.mark.asyncio
    @pytest.mark.timeout(2)  # Reduced timeout
    async def test_subscribe_setup(self, mock_redis_client, reset_circuit_breaker):
        async def handler(data):
            pass

        # Short-circuit the listener loop to exit quickly
        mock_redis_client.pubsub.return_value.get_message = AsyncMock(
            side_effect=asyncio.CancelledError()
        )

        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            task = await subscribe_event("test", handler)
            # Give it a moment to start
            await asyncio.sleep(0.01)
            # Task should already be done due to CancelledError
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            mock_redis_client.pubsub.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.timeout(2)  # Reduced timeout
    async def test_subscribe_receives_message(
        self, mock_redis_client, reset_circuit_breaker
    ):
        received = []

        async def handler(data):
            received.append(data)

        pubsub = mock_redis_client.pubsub.return_value
        test_data = {"test": "data"}
        payload_bytes = json.dumps(test_data).encode("utf-8")
        
        # Use proper Fernet encryption instead of mock encryption
        from cryptography.fernet import Fernet
        fernet_key = os.environ["EVENT_BUS_ENCRYPTION_KEY"]
        fernet = Fernet(fernet_key.encode())
        encrypted_payload = fernet.encrypt(payload_bytes)
        
        signature = hmac.new(
            os.environ["EVENT_BUS_HMAC_KEY"].encode(), encrypted_payload, hashlib.sha256
        ).hexdigest()
        message = {
            "data": json.dumps(
                {"payload": encrypted_payload.decode("utf-8"), "signature": signature}
            )
        }

        # Return message once, then cancel
        call_count = 0

        async def get_message_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return message
            # Cancel after first message
            raise asyncio.CancelledError()

        pubsub.get_message = AsyncMock(side_effect=get_message_side_effect)

        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            task = await subscribe_event("test", handler)
            # Wait longer for message to be processed (increased from 0.1 to 0.3)
            await asyncio.sleep(0.3)
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert len(received) == 1
            assert received[0] == test_data


class TestDLQ:
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_dlq_replay(self, mock_redis_client, reset_circuit_breaker):
        mock_redis_client.xread.return_value = [
            (
                b"test_tenant:test:event_bus:dlq",
                [
                    (
                        b"123-0",
                        {
                            b"event_type": b"test",
                            b"payload": b'{"key": "value"}',
                            b"error": b"error",
                            b"timestamp": b"12345",
                            b"original_id": b"",
                        },
                    )
                ],
            )
        ]
        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            with patch("event_bus.publish_event", new_callable=AsyncMock) as mock_pub:
                await replay_dlq()
                mock_pub.assert_called_once_with(
                    "test", {"key": "value"}, is_replay=True
                )
                mock_redis_client.xdel.assert_called_once()


class TestIntegration:
    @pytest.mark.asyncio
    @pytest.mark.timeout(5)
    async def test_concurrent_publishers(
        self, mock_redis_client, reset_circuit_breaker
    ):
        with patch("event_bus.get_redis_client", return_value=mock_redis_client):
            tasks = [publish_event(f"event_{i}", {"id": i}) for i in range(5)]
            await asyncio.gather(*tasks)
            assert mock_redis_client.publish.call_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
    # Ensure cleanup happens if running directly
    cleanup_all_loggers()
