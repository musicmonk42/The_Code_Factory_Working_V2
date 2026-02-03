# test_multi_modal_plugin.py
import json
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest
from self_fixing_engineer.arbiter.plugins.multi_modal_plugin import (
    AuditLogger,
    CacheManager,
    InputValidator,
    InvalidInputError,
    MetricsCollector,
    MultiModalException,
    MultiModalPlugin,
    OutputValidator,
    ProcessingError,
    ProcessingResult,
    SandboxExecutor,
)


class TestAuditLogger:
    """Test suite for AuditLogger."""

    def test_audit_logger_initialization(self):
        """Test AuditLogger initialization."""
        config = Mock()
        config.destination = "console"
        config.log_level = "INFO"

        audit_logger = AuditLogger(config)
        assert audit_logger.config == config
        assert audit_logger.audit_log.level == 20  # INFO level

    def test_log_event(self):
        """Test logging an event."""
        config = Mock()
        config.destination = "console"
        config.log_level = "INFO"

        audit_logger = AuditLogger(config)

        with patch.object(audit_logger.audit_log, "info") as mock_info:
            audit_logger.log_event(
                user_id="test_user",
                event_type="test_event",
                timestamp="2024-01-01T00:00:00",
                success=True,
                operation_id="op123",
                latency_ms=100.5,
            )

            mock_info.assert_called_once()
            logged_data = json.loads(mock_info.call_args[0][0])
            assert logged_data["user_id"] == "test_user"
            assert logged_data["success"] is True
            assert logged_data["latency_ms"] == 100.5


class TestMetricsCollector:
    """Test suite for MetricsCollector."""

    def test_metrics_collector_disabled(self):
        """Test MetricsCollector when disabled."""
        config = Mock()
        config.enabled = False

        collector = MetricsCollector(config)
        # Should not crash when calling methods
        collector.increment_successful_requests("image")
        collector.increment_failed_requests("text")
        collector.observe_latency("audio", 100)

    @patch("self_fixing_engineer.arbiter.plugins.multi_modal_plugin.get_or_create_counter")
    @patch("self_fixing_engineer.arbiter.plugins.multi_modal_plugin.get_or_create_histogram")
    def test_metrics_collector_enabled(self, mock_histogram, mock_counter):
        """Test MetricsCollector when enabled."""
        config = Mock()
        config.enabled = True

        mock_metric = Mock()
        mock_counter.return_value = mock_metric
        mock_histogram.return_value = mock_metric

        collector = MetricsCollector(config)

        # Test metric operations
        collector.increment_successful_requests("image")
        collector.increment_cache_hits("text")
        collector.observe_latency("audio", 500)

        assert mock_counter.call_count == 3  # requests_total, cache_hits, cache_misses
        assert mock_histogram.call_count == 1  # processing_latency


class TestCacheManager:
    """Test suite for CacheManager."""

    @pytest.mark.asyncio
    async def test_cache_disabled(self):
        """Test CacheManager when disabled."""
        config = Mock()
        config.enabled = False

        cache = CacheManager(config)
        await cache.connect()

        result = await cache.get("test_key")
        assert result is None

        await cache.set("test_key", {"data": "value"}, 60)
        # Should not crash

    @pytest.mark.asyncio
    async def test_cache_connection_failure(self):
        """Test CacheManager handling connection failure."""
        config = Mock()
        config.enabled = True
        config.type = "redis"
        config.host = "localhost"
        config.port = 6379

        with patch(
            "self_fixing_engineer.arbiter.plugins.multi_modal_plugin.redis.asyncio.Redis"
        ) as mock_redis:
            mock_redis_instance = AsyncMock()
            mock_redis.return_value = mock_redis_instance
            mock_redis_instance.ping.side_effect = Exception("Connection failed")

            cache = CacheManager(config)
            await cache.connect()

            assert cache.config.enabled is False  # Should be disabled after failure

    @pytest.mark.asyncio
    async def test_cache_operations(self):
        """Test CacheManager get/set operations."""
        config = Mock()
        config.enabled = True
        config.type = "redis"
        config.host = "localhost"
        config.port = 6379

        with patch("self_fixing_engineer.arbiter.plugins.multi_modal_plugin.redis") as mock_redis_module:
            mock_redis = AsyncMock()
            mock_redis_module.asyncio.Redis.return_value = mock_redis
            mock_redis.ping.return_value = True
            mock_redis.get.return_value = json.dumps({"cached": "data"})

            cache = CacheManager(config)
            await cache.connect()

            # Test get
            result = await cache.get("test_key")
            assert result == {"cached": "data"}

            # Test set
            await cache.set("test_key", {"new": "data"}, 60)
            mock_redis.setex.assert_called_once()


class TestInputValidator:
    """Test suite for InputValidator."""

    def test_validate_text_input(self):
        """Test validation of text input."""
        security_config = Mock()
        security_config.input_validation_rules = {"text": {"max_length": 100}}
        security_config.mask_pii_in_logs = False
        security_config.pii_patterns = []

        # Valid text
        result = InputValidator.validate("text", "Valid text", security_config)
        assert result == "Valid text"

        # Text too long
        with pytest.raises(InvalidInputError, match="exceeds max length"):
            InputValidator.validate("text", "x" * 101, security_config)

        # Invalid type
        with pytest.raises(InvalidInputError, match="must be a string"):
            InputValidator.validate("text", 123, security_config)

    def test_validate_binary_input(self):
        """Test validation of binary input (image/audio/video)."""
        security_config = Mock()
        security_config.input_validation_rules = {"image": {"max_size": 1024}}

        # Valid bytes
        result = InputValidator.validate("image", b"image_data", security_config)
        assert result == b"image_data"

        # Bytes too large
        with pytest.raises(InvalidInputError, match="exceeds max size"):
            InputValidator.validate("image", b"x" * 1025, security_config)

    def test_pii_masking(self):
        """Test PII masking in text input."""
        security_config = Mock()
        security_config.input_validation_rules = {}
        security_config.mask_pii_in_logs = True
        security_config.pii_patterns = [r"\b[\w\.-]+@[\w\.-]+\.\w+\b"]  # Email pattern

        text = "Contact john@example.com for info"
        result = InputValidator.validate("text", text, security_config)
        assert "[PII_MASKED]" in result
        assert "john@example.com" not in result


class TestOutputValidator:
    """Test suite for OutputValidator."""

    def test_validate_output_success(self):
        """Test successful output validation."""
        security_config = Mock()
        security_config.output_validation_rules = {
            "text": {"min_confidence": 0.5, "require_success_flag": True}
        }

        result = {"success": True, "model_confidence": 0.8, "data": {}}

        # Should not raise
        OutputValidator.validate("text", result, security_config)

    def test_validate_output_confidence_too_low(self):
        """Test output validation with low confidence."""
        security_config = Mock()
        security_config.output_validation_rules = {"text": {"min_confidence": 0.9}}

        result = {"model_confidence": 0.5}

        with pytest.raises(MultiModalException, match="below threshold"):
            OutputValidator.validate("text", result, security_config)


class TestMultiModalPlugin:
    """Test suite for MultiModalPlugin."""

    @pytest.fixture
    def base_config(self):
        """Returns a minimal valid configuration."""
        return {
            "image_processing": {"enabled": True},
            "text_processing": {"enabled": True},
            "audit_log_config": {"enabled": False},
            "metrics_config": {"enabled": False},
            "cache_config": {"enabled": False},
            "circuit_breaker_config": {
                "enabled": True,
                "threshold": 3,
                "timeout_seconds": 30,
                "modalities": ["image", "text"],
            },
        }

    @pytest.fixture
    async def plugin(self, base_config):
        """Creates and initializes a MultiModalPlugin instance."""
        with patch(
            "self_fixing_engineer.arbiter.plugins.multi_modal_plugin.PluginRegistry.get_processor"
        ) as mock_get:
            mock_processor = AsyncMock()
            mock_processor.process.return_value = ProcessingResult(
                success=True,
                data={"result": "processed"},
                summary="Success",
                operation_id="test123",
            )
            mock_get.return_value = mock_processor

            plugin = MultiModalPlugin(base_config)
            await plugin.initialize()
            yield plugin
            await plugin.stop()

    @pytest.mark.asyncio
    async def test_plugin_initialization(self, base_config):
        """Test plugin initialization."""
        plugin = MultiModalPlugin(base_config)

        assert plugin.config.image_processing.enabled is True
        assert plugin.config.text_processing.enabled is True
        assert len(plugin._circuit_breaker_states) == 2

    @pytest.mark.asyncio
    async def test_process_image_success(self, plugin):
        """Test successful image processing."""
        result = await plugin.process_image(b"fake_image_data")

        assert result.success is True
        assert result.data == {"result": "processed"}

    @pytest.mark.asyncio
    async def test_process_disabled_modality(self, plugin):
        """Test processing a disabled modality."""
        plugin.config.audio_processing.enabled = False

        result = await plugin.process_audio(b"audio_data")

        assert result.success is False
        assert "not enabled" in result.error

    @pytest.mark.asyncio
    async def test_circuit_breaker_functionality(self, plugin):
        """Test circuit breaker opening after failures."""
        # Mock processor to always fail
        plugin.image_processor.process.side_effect = ProcessingError("Failed")

        # Fail multiple times to trigger circuit breaker
        for _ in range(3):
            result = await plugin.process_image(b"data")
            assert result.success is False

        assert plugin._circuit_breaker_states["image"] == "open"

        # Next call should fail immediately
        result = await plugin.process_image(b"data")
        assert "Circuit breaker" in result.error

    @pytest.mark.asyncio
    async def test_hooks_execution(self, plugin):
        """Test pre and post processing hooks."""
        pre_hook_called = False
        post_hook_called = False

        def pre_hook(data):
            nonlocal pre_hook_called
            pre_hook_called = True
            return data

        def post_hook(data):
            nonlocal post_hook_called
            post_hook_called = True
            data["hooked"] = True
            return data

        plugin.add_hook("text", pre_hook, "pre")
        plugin.add_hook("text", post_hook, "post")

        await plugin.process_text("test text")

        assert pre_hook_called
        assert post_hook_called

    @pytest.mark.asyncio
    async def test_caching_functionality(self, base_config):
        """Test caching with mock Redis."""
        base_config["cache_config"] = {
            "enabled": True,
            "type": "redis",
            "host": "localhost",
            "port": 6379,
            "ttl_seconds": 60,
        }

        with patch("self_fixing_engineer.arbiter.plugins.multi_modal_plugin.redis") as mock_redis_module:
            mock_redis = AsyncMock()
            mock_redis_module.asyncio.Redis.return_value = mock_redis
            mock_redis.ping.return_value = True

            # First call - cache miss
            mock_redis.get.return_value = None

            with patch(
                "self_fixing_engineer.arbiter.plugins.multi_modal_plugin.PluginRegistry.get_processor"
            ) as mock_get:
                mock_processor = AsyncMock()
                mock_processor.process.return_value = ProcessingResult(
                    success=True, data={"result": "processed"}
                )
                mock_get.return_value = mock_processor

                plugin = MultiModalPlugin(base_config)
                await plugin.initialize()

                # Process data - should cache result
                result1 = await plugin.process_text("test")
                assert result1.success is True

                # Verify cache was written
                mock_redis.setex.assert_called_once()

                # Second call - cache hit
                mock_redis.get.return_value = json.dumps(
                    {"success": True, "data": {"cached": True}}
                )

                await plugin.process_text("test")
                # Should get cached result without processing

    def test_get_supported_providers(self, plugin):
        """Test getting supported providers for a modality."""
        with patch(
            "self_fixing_engineer.arbiter.plugins.multi_modal_plugin.PluginRegistry.get_supported_providers"
        ) as mock_get:
            mock_get.return_value = ["provider1", "provider2"]

            providers = plugin.get_supported_providers("image")
            assert providers == ["provider1", "provider2"]

    def test_set_default_provider(self, plugin):
        """Test setting default provider."""
        plugin.set_default_provider("image", "new_provider")
        assert plugin.config.image_processing.default_provider == "new_provider"

    def test_update_model_version(self, plugin):
        """Test updating model version."""
        plugin.update_model_version("text", "v2.0")
        assert plugin.config.current_model_version["text"] == "v2.0"

    @pytest.mark.asyncio
    async def test_context_manager(self, base_config):
        """Test async context manager."""
        with patch("self_fixing_engineer.arbiter.plugins.multi_modal_plugin.PluginRegistry.get_processor"):
            async with MultiModalPlugin(base_config) as plugin:
                assert plugin is not None
            # Should have called stop() on exit

    @pytest.mark.asyncio
    async def test_health_check(self, plugin):
        """Test health check."""
        result = await plugin.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_get_capabilities(self, plugin):
        """Test getting plugin capabilities."""
        capabilities = await plugin.get_capabilities()
        assert "multimodal_processing" in capabilities
        assert "image" in capabilities
        assert "text" in capabilities


class TestSandboxExecutor:
    """Test suite for SandboxExecutor."""

    @pytest.mark.asyncio
    async def test_sandbox_disabled(self):
        """Test sandbox executor when disabled."""

        async def test_func(data):
            return data + "_processed"

        with patch.dict(os.environ, {"REAL_SANDBOXING_ENABLED": "false"}):
            result = await SandboxExecutor.execute(test_func, "test")
            assert result == "test_processed"

    @pytest.mark.asyncio
    async def test_sandbox_sync_function(self):
        """Test sandbox executor with sync function."""

        def sync_func(data):
            return data + "_sync"

        with patch.dict(os.environ, {"REAL_SANDBOXING_ENABLED": "false"}):
            result = await SandboxExecutor.execute(sync_func, "test")
            assert result == "test_sync"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
