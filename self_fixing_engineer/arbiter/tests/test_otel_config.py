"""
test_otel_config.py - Tests for OpenTelemetry Configuration Module

Tests for the enterprise OpenTelemetry configuration with proper mocking
of external dependencies and comprehensive coverage of functionality.
"""

import pytest
import os
import sys
import threading
from unittest.mock import MagicMock, patch
import asyncio

# Import the module under test
from arbiter.otel_config import (
    OpenTelemetryConfig,
    Environment,
    CollectorEndpoint,
    SamplingStrategy,
    NoOpTracer,
    NoOpSpan,
    get_tracer,
    trace_operation,
)


class TestEnvironment:
    """Tests for Environment enumeration and detection."""

    def test_environment_values(self):
        """Test that all environment values are properly defined."""
        assert Environment.DEVELOPMENT.value == "development"
        assert Environment.STAGING.value == "staging"
        assert Environment.PRODUCTION.value == "production"
        assert Environment.TESTING.value == "testing"

    def test_current_detects_testing(self):
        """Test that testing environment is detected when running tests."""
        # This should always return TESTING when pytest is running
        assert Environment.current() == Environment.TESTING

    def test_current_detects_pytest_module(self):
        """Test detection of pytest in modules."""
        # pytest should be in sys.modules during test run
        assert "pytest" in sys.modules or "unittest" in sys.modules
        assert Environment.current() == Environment.TESTING


class TestCollectorEndpoint:
    """Tests for CollectorEndpoint configuration."""

    def test_endpoint_initialization(self):
        """Test endpoint initialization with default values."""
        endpoint = CollectorEndpoint(url="http://localhost:4317")
        assert endpoint.url == "http://localhost:4317"
        assert endpoint.protocol == "grpc"
        assert endpoint.timeout == 10.0
        assert endpoint.headers == {}
        assert endpoint.insecure is False
        assert endpoint.compression == "gzip"

    def test_endpoint_with_custom_values(self):
        """Test endpoint initialization with custom values."""
        headers = {"Authorization": "Bearer token"}
        endpoint = CollectorEndpoint(
            url="https://collector.example.com",
            protocol="http",
            timeout=5.0,
            headers=headers,
            tls_cert_path="/path/to/cert",
            insecure=True,
        )
        assert endpoint.protocol == "http"
        assert endpoint.timeout == 5.0
        assert endpoint.headers == headers
        assert endpoint.tls_cert_path == "/path/to/cert"
        assert endpoint.insecure is True

    @patch("socket.socket")
    def test_is_reachable_success(self, mock_socket_class):
        """Test endpoint reachability check when successful."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0
        mock_socket_class.return_value = mock_socket

        endpoint = CollectorEndpoint(url="http://localhost:4317")
        assert endpoint.is_reachable() is True

        mock_socket.connect_ex.assert_called_once_with(("localhost", 4317))
        mock_socket.close.assert_called_once()

    @patch("socket.socket")
    def test_is_reachable_failure(self, mock_socket_class):
        """Test endpoint reachability check when connection fails."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 1  # Connection failed
        mock_socket_class.return_value = mock_socket

        endpoint = CollectorEndpoint(url="http://localhost:4317")
        assert endpoint.is_reachable() is False

    @patch("socket.socket")
    def test_is_reachable_with_custom_port(self, mock_socket_class):
        """Test reachability check with custom port in URL."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0
        mock_socket_class.return_value = mock_socket

        endpoint = CollectorEndpoint(url="http://localhost:8080")
        assert endpoint.is_reachable() is True

        mock_socket.connect_ex.assert_called_once_with(("localhost", 8080))

    @patch("socket.socket")
    def test_is_reachable_http_default_port(self, mock_socket_class):
        """Test that HTTP protocol uses port 4318 by default."""
        mock_socket = MagicMock()
        mock_socket.connect_ex.return_value = 0
        mock_socket_class.return_value = mock_socket

        endpoint = CollectorEndpoint(url="http://localhost", protocol="http")
        endpoint.is_reachable()

        mock_socket.connect_ex.assert_called_once_with(("localhost", 4318))


class TestSamplingStrategy:
    """Tests for SamplingStrategy configuration."""

    def test_default_initialization(self):
        """Test sampling strategy with default values."""
        strategy = SamplingStrategy()
        assert strategy.base_rate == 0.1
        assert strategy.error_rate == 1.0
        assert strategy.high_latency_threshold_ms == 1000.0
        assert strategy.high_latency_rate == 0.5
        assert strategy.adaptive_enabled is True
        assert strategy.target_spans_per_second == 100

    def test_should_sample_error(self):
        """Test that errors are sampled at error_rate."""
        strategy = SamplingStrategy(error_rate=1.0)

        # Errors should always be sampled when error_rate is 1.0
        for _ in range(10):
            assert strategy.should_sample("test_span", "test_service", {"error": True}) is True

    def test_should_sample_high_latency(self):
        """Test high latency sampling."""
        strategy = SamplingStrategy(high_latency_threshold_ms=100, high_latency_rate=1.0)

        # High latency should always be sampled when rate is 1.0
        assert strategy.should_sample("test_span", "test_service", {"latency_ms": 200}) is True

    def test_should_sample_operation_override(self):
        """Test operation-specific sampling rate."""
        strategy = SamplingStrategy(base_rate=0.0, operation_rates={"critical_operation": 1.0})

        # Critical operation should always be sampled
        assert strategy.should_sample("critical_operation", "test_service", {}) is True

        # Other operations should not be sampled with base_rate=0
        assert strategy.should_sample("normal_operation", "test_service", {}) is False

    def test_should_sample_service_override(self):
        """Test service-specific sampling rate."""
        strategy = SamplingStrategy(base_rate=0.0, service_rates={"important_service": 1.0})

        # Important service should always be sampled
        assert strategy.should_sample("any_operation", "important_service", {}) is True

        # Other services should not be sampled
        assert strategy.should_sample("any_operation", "normal_service", {}) is False


class TestOpenTelemetryConfig:
    """Tests for OpenTelemetryConfig singleton."""

    def setup_method(self):
        """Reset singleton before each test."""
        OpenTelemetryConfig._instance = None
        OpenTelemetryConfig._initialized = False

    def test_singleton_pattern(self):
        """Test that only one instance is created."""
        instance1 = OpenTelemetryConfig.get_instance()
        instance2 = OpenTelemetryConfig.get_instance()
        assert instance1 is instance2

    def test_direct_instantiation_raises_error(self):
        """Test that direct instantiation raises an error after singleton exists."""
        OpenTelemetryConfig.get_instance()
        with pytest.raises(RuntimeError, match="Use OpenTelemetryConfig.get_instance()"):
            OpenTelemetryConfig()

    @patch.dict(
        os.environ,
        {"OTEL_SERVICE_NAME": "test_service", "OTEL_SERVICE_VERSION": "2.0.0"},
    )
    def test_service_configuration_from_env(self):
        """Test service name and version from environment."""
        config = OpenTelemetryConfig.get_instance()
        assert config.service_name == "test_service"
        assert config.service_version == "2.0.0"

    def test_testing_environment_uses_noop_tracer(self):
        """Test that testing environment uses NoOpTracer."""
        # Environment.current() will return TESTING in test environment
        config = OpenTelemetryConfig.get_instance()
        assert isinstance(config.tracer, NoOpTracer)

    @patch("arbiter.otel_config.OTEL_AVAILABLE", False)
    def test_missing_opentelemetry_uses_noop_tracer(self):
        """Test fallback to NoOpTracer when OpenTelemetry is not available."""
        with patch.object(Environment, "current", return_value=Environment.DEVELOPMENT):
            config = OpenTelemetryConfig.get_instance()
            assert isinstance(config.tracer, NoOpTracer)

    @patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"})
    def test_endpoints_from_env(self):
        """Test endpoint configuration from environment variables."""
        config = OpenTelemetryConfig.get_instance()
        endpoints = config._endpoints_from_env()

        assert len(endpoints) > 0
        assert endpoints[0].url == "http://localhost:4317"

    @patch.dict(
        os.environ,
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector.example.com",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer token,X-Custom=value",
        },
    )
    def test_parse_headers(self):
        """Test header parsing from environment variable."""
        config = OpenTelemetryConfig.get_instance()
        headers = config._parse_headers("Authorization=Bearer token,X-Custom=value")

        assert headers == {"Authorization": "Bearer token", "X-Custom": "value"}

    @patch.object(Environment, "current", return_value=Environment.PRODUCTION)
    def test_production_requires_tls(self, mock_env):
        """Test that production environment requires TLS endpoints."""
        config = OpenTelemetryConfig.get_instance()

        # Non-TLS endpoint should be rejected
        http_endpoint = CollectorEndpoint(url="http://collector.example.com")
        assert config._validate_endpoint(http_endpoint) is False

        # HTTPS endpoint should be accepted
        https_endpoint = CollectorEndpoint(url="https://collector.example.com")
        https_endpoint.is_reachable = MagicMock(return_value=True)
        assert config._validate_endpoint(https_endpoint) is True

        # Explicitly insecure endpoint should be accepted
        insecure_endpoint = CollectorEndpoint(url="http://internal-collector", insecure=True)
        insecure_endpoint.is_reachable = MagicMock(return_value=True)
        assert config._validate_endpoint(insecure_endpoint) is True

    @patch("arbiter.otel_config.CONSUL_AVAILABLE", True)
    @patch.dict(os.environ, {"CONSUL_ENABLED": "true", "CONSUL_HOST": "consul.local"})
    def test_discover_from_consul(self):
        """Test service discovery from Consul."""
        config = OpenTelemetryConfig.get_instance()

        # Create mock consul module and inject it
        mock_consul_module = MagicMock()
        mock_consul = MagicMock()
        mock_consul_module.Consul.return_value = mock_consul
        mock_consul.health.service.return_value = (
            None,
            [{"Service": {"Address": "10.0.0.1", "Port": 4317}}],
        )

        # Temporarily inject the mock
        import arbiter.otel_config as otel

        original_consul = getattr(otel, "consul", None)
        otel.consul = mock_consul_module

        try:
            endpoints = config._discover_from_consul()
            assert len(endpoints) == 1
            assert endpoints[0].url == "grpc://10.0.0.1:4317"
        finally:
            # Restore original state
            if original_consul:
                otel.consul = original_consul
            elif hasattr(otel, "consul"):
                delattr(otel, "consul")

    def test_shutdown(self):
        """Test graceful shutdown."""
        config = OpenTelemetryConfig.get_instance()
        config._executor = MagicMock()

        config.shutdown()
        config._executor.shutdown.assert_called_once_with(wait=True, timeout=5)


class TestNoOpImplementations:
    """Tests for NoOp tracer and span implementations."""

    def test_noop_span_interface(self):
        """Test NoOpSpan implements required interface."""
        span = NoOpSpan()

        # Test context manager
        with span as s:
            assert s is span

        # Test methods don't raise errors
        span.set_attribute("key", "value")
        span.add_event("event", {"attr": "value"})
        span.set_status("status")
        span.record_exception(Exception("test"))

        # Test span context
        context = span.get_span_context()
        assert context.trace_id == 0
        assert context.span_id == 0
        assert context.is_remote is False

    def test_noop_tracer_interface(self):
        """Test NoOpTracer implements required interface."""
        tracer = NoOpTracer()

        # Test context manager
        with tracer.start_as_current_span("test") as span:
            assert isinstance(span, NoOpSpan)

        # Test start_span
        span = tracer.start_span("test")
        assert isinstance(span, NoOpSpan)


class TestModuleFunctions:
    """Tests for module-level convenience functions."""

    def setup_method(self):
        """Reset module state before each test."""
        import arbiter.otel_config as otel

        otel._config = None
        OpenTelemetryConfig._instance = None
        OpenTelemetryConfig._initialized = False

    def test_get_tracer_initializes_config(self):
        """Test that get_tracer initializes configuration if needed."""
        tracer = get_tracer("test_component")
        assert tracer is not None

        import arbiter.otel_config as otel

        assert otel._config is not None

    def test_get_tracer_with_name(self):
        """Test get_tracer with component name."""
        tracer = get_tracer("my_component")
        assert isinstance(tracer, NoOpTracer)

    @pytest.mark.asyncio
    async def test_trace_operation_decorator_async(self):
        """Test trace_operation decorator with async function."""
        call_count = {"count": 0}

        @trace_operation("test_async_op")
        async def async_function(value):
            call_count["count"] += 1
            await asyncio.sleep(0.01)
            return value * 2

        result = await async_function(5)
        assert result == 10
        assert call_count["count"] == 1

    def test_trace_operation_decorator_sync(self):
        """Test trace_operation decorator with sync function."""
        call_count = {"count": 0}

        @trace_operation("test_sync_op")
        def sync_function(value):
            call_count["count"] += 1
            return value * 2

        result = sync_function(5)
        assert result == 10
        assert call_count["count"] == 1

    def test_trace_operation_decorator_with_exception(self):
        """Test trace_operation decorator handles exceptions."""

        @trace_operation()
        def failing_function():
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_function()

    @pytest.mark.asyncio
    async def test_trace_operation_decorator_async_with_exception(self):
        """Test trace_operation decorator handles async exceptions."""

        @trace_operation()
        async def failing_async_function():
            raise ValueError("Async test error")

        with pytest.raises(ValueError, match="Async test error"):
            await failing_async_function()


class TestResourceCreation:
    """Tests for resource creation with metadata."""

    @patch.dict(os.environ, {"AWS_REGION": "us-west-2", "AWS_ACCOUNT_ID": "123456789"})
    @patch("socket.gethostname", return_value="test-host")
    @patch("os.getpid", return_value=1234)
    def test_create_resource_with_aws_metadata(self, mock_pid, mock_hostname):
        """Test resource creation includes AWS metadata."""
        config = OpenTelemetryConfig.get_instance()

        # Mock Resource class
        with patch("arbiter.otel_config.Resource") as mock_resource:
            mock_resource.create.return_value = MagicMock()

            config._create_resource()

            # Check that AWS attributes were included
            call_args = mock_resource.create.call_args[0][0]
            assert call_args["cloud.provider"] == "aws"
            assert call_args["cloud.region"] == "us-west-2"
            assert call_args["cloud.account.id"] == "123456789"

    @patch.dict(
        os.environ,
        {
            "KUBERNETES_SERVICE_HOST": "10.0.0.1",
            "K8S_NAMESPACE": "production",
            "K8S_POD_NAME": "app-pod-123",
        },
    )
    def test_create_resource_with_k8s_metadata(self):
        """Test resource creation includes Kubernetes metadata."""
        config = OpenTelemetryConfig.get_instance()

        with patch("arbiter.otel_config.Resource") as mock_resource:
            mock_resource.create.return_value = MagicMock()

            config._create_resource()

            # Check that Kubernetes attributes were included
            call_args = mock_resource.create.call_args[0][0]
            assert call_args["k8s.namespace.name"] == "production"
            assert call_args["k8s.pod.name"] == "app-pod-123"


class TestTraceContext:
    """Tests for trace context manager."""

    def test_trace_context_with_tracer(self):
        """Test trace_context when tracer is available."""
        config = OpenTelemetryConfig.get_instance()
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        config.tracer = mock_tracer

        with config.trace_context("test_operation", key1="value1", key2="value2"):
            pass

        mock_tracer.start_as_current_span.assert_called_once_with("test_operation")
        mock_span.set_attribute.assert_any_call("key1", "value1")
        mock_span.set_attribute.assert_any_call("key2", "value2")

    def test_trace_context_without_tracer(self):
        """Test trace_context when tracer is not available."""
        config = OpenTelemetryConfig.get_instance()
        config.tracer = None

        with config.trace_context("test_operation") as span:
            assert span is None


class TestThreadSafety:
    """Tests for thread-safe singleton initialization."""

    def setup_method(self):
        """Reset singleton before each test."""
        OpenTelemetryConfig._instance = None
        OpenTelemetryConfig._initialized = False

    def test_concurrent_initialization(self):
        """Test that concurrent calls to get_instance are thread-safe."""
        instances = []

        def get_instance():
            instances.append(OpenTelemetryConfig.get_instance())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All instances should be the same
        assert len(instances) == 10
        assert all(inst is instances[0] for inst in instances)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
