# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Enterprise-grade unit tests for critical production bugfixes.

These tests validate the highest industry standards for:
- Error handling and recovery
- Async task management
- Provider loading
- Graceful degradation
"""

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSafeAsyncTaskCreation:
    """Test suite for industry-standard async task creation utility."""
    
    def test_safe_create_async_task_no_event_loop(self):
        """Test graceful handling when no event loop is running."""
        from generator.runner.runner_logging import _safe_create_async_task
        
        async def dummy_coro():
            return "test"
        
        # Should return False and not raise when no event loop
        result = _safe_create_async_task(
            dummy_coro(),
            task_name="test_task",
            context={"test": "value"},
        )
        
        assert result is False, "Should return False when no event loop"
    
    @pytest.mark.asyncio
    async def test_safe_create_async_task_with_event_loop(self):
        """Test successful task creation when event loop is running."""
        from generator.runner.runner_logging import _safe_create_async_task
        
        executed = []
        
        async def dummy_coro():
            executed.append(True)
            return "test"
        
        # Should create task successfully
        result = _safe_create_async_task(
            dummy_coro(),
            task_name="test_task",
            context={"test": "value"},
        )
        
        assert result is True, "Should return True when task created"
        
        # Give task time to execute
        await asyncio.sleep(0.1)
        
        assert len(executed) == 1, "Task should have executed"
    
    @pytest.mark.asyncio
    async def test_safe_create_async_task_error_handling(self):
        """Test error handling in async tasks."""
        from generator.runner.runner_logging import _safe_create_async_task
        
        async def failing_coro():
            raise ValueError("Test error")
        
        # Should create task but handle the error gracefully
        result = _safe_create_async_task(
            failing_coro(),
            task_name="failing_task",
            context={"test": "error"},
        )
        
        assert result is True, "Should return True even if task will fail"
        
        # Give task time to fail
        await asyncio.sleep(0.1)
        
        # Task should have logged the error but not crashed


class TestLLMProviderLoading:
    """Test suite for LLM provider loading with path validation."""
    
    def test_provider_directory_validation(self):
        """Test that provider directory is validated on initialization."""
        from generator.runner.runner_config import RunnerConfig
        from generator.runner.llm_client import LLMClient
        
        # Create a config
        config = RunnerConfig(
            backend="docker",
            framework="pytest",
            instance_id="test_instance",
            redis_url="redis://localhost:6379"
        )
        
        # Mock the provider directory to not exist
        with patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(ValueError, match="provider directory not found"):
                LLMClient(config)
    
    def test_provider_directory_not_a_directory(self):
        """Test error when provider path exists but is not a directory."""
        from generator.runner.runner_config import RunnerConfig
        from generator.runner.llm_client import LLMClient
        
        config = RunnerConfig(
            backend="docker",
            framework="pytest",
            instance_id="test_instance",
            redis_url="redis://localhost:6379"
        )
        
        # Mock the provider path to be a file, not a directory
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=False):
                with pytest.raises(ValueError, match="not a directory"):
                    LLMClient(config)


class TestHookRegistration:
    """Test suite for hook registration with enhanced validation."""
    
    def test_add_custom_metrics_hook_validation(self):
        """Test that metrics hook validates callable."""
        from generator.runner.runner_logging import add_custom_metrics_hook
        
        # Should raise TypeError for non-callable
        with pytest.raises(TypeError, match="Hook must be callable"):
            add_custom_metrics_hook("not_a_callable")
    
    def test_add_custom_logging_hook_validation(self):
        """Test that logging hook validates callable."""
        from generator.runner.runner_logging import add_custom_logging_hook
        
        # Should raise TypeError for non-callable
        with pytest.raises(TypeError, match="Hook must be callable"):
            add_custom_logging_hook("not_a_callable")
    
    def test_metrics_hook_registration_returns_true(self):
        """Test successful hook registration returns True."""
        from generator.runner.runner_logging import add_custom_metrics_hook
        
        def test_hook(name: str, value: float, context: dict):
            pass
        
        result = add_custom_metrics_hook(test_hook)
        assert result is True, "Should return True on successful registration"
    
    def test_logging_hook_registration_returns_true(self):
        """Test successful hook registration returns True."""
        from generator.runner.runner_logging import add_custom_logging_hook
        
        def test_hook(record: logging.LogRecord):
            pass
        
        result = add_custom_logging_hook(test_hook)
        assert result is True, "Should return True on successful registration"


class TestAnomalyDetection:
    """Test suite for anomaly detection with industry-standard error handling."""
    
    def test_anomaly_detection_no_event_loop(self):
        """Test anomaly detection works even without event loop."""
        from generator.runner.runner_logging import detect_anomaly
        
        # Should not raise even when no event loop running
        detect_anomaly(
            metric_name="test_metric",
            value=100.0,
            threshold=50.0,
            severity="high",
            anomaly_type="threshold_breach",
        )
        
        # No assertion needed - just verify it doesn't crash
    
    @pytest.mark.asyncio
    async def test_anomaly_detection_with_event_loop(self):
        """Test anomaly detection with event loop running."""
        from generator.runner.runner_logging import detect_anomaly
        
        # Should work fine with event loop
        detect_anomaly(
            metric_name="test_metric",
            value=100.0,
            threshold=50.0,
            severity="high",
            anomaly_type="threshold_breach",
        )
        
        # Give async tasks time to execute
        await asyncio.sleep(0.1)
        
        # No assertion needed - just verify it works


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "-s"])
