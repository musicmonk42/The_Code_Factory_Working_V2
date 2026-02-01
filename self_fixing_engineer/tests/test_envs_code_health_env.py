"""
Comprehensive Test Suite for CodeHealthEnv Module
Tests all features including async handling, thread safety, memory management,
automatic rollback, cooldowns, and rendering.
"""

import asyncio
import os
import sys
import threading
import time
from unittest.mock import Mock

import numpy as np
import pytest

from self_fixing_engineer.envs.code_health_env import (
    ActionType,
    AsyncActionExecutor,
    AuditLogger,
    CodeHealthEnv,
    EnvironmentConfig,
    SystemMetrics,
)


# Fixtures
@pytest.fixture
def basic_config():
    """Basic environment configuration for testing"""
    return EnvironmentConfig(
        observation_keys=["pass_rate", "latency", "alert_ratio"],
        max_steps=10,
        unacceptable_threshold=0.4,
        critical_threshold=0.2,
        max_action_history=50,
        enable_auto_rollback=True,
    )


@pytest.fixture
def mock_metrics():
    """Mock metrics function that returns consistent values"""

    def get_metrics():
        return [0.8, 0.3, 0.1]  # pass_rate, latency, alert_ratio

    return get_metrics


@pytest.fixture
def mock_action():
    """Mock action function that returns success"""

    def apply_action(action_id: int):
        return {"success": True, "action_id": action_id}

    return apply_action


@pytest.fixture
def mock_audit_logger():
    """Mock audit logger for testing"""
    logger = Mock(spec=AuditLogger)
    logger.log_event = Mock()
    return logger


# Test EnvironmentConfig
class TestEnvironmentConfig:
    """Test configuration validation and defaults"""

    def test_default_configuration(self):
        """Test default configuration values"""
        config = EnvironmentConfig()
        assert len(config.observation_keys) == 5
        assert config.max_steps == 100
        assert config.unacceptable_threshold == 0.2
        assert config.critical_threshold == 0.1
        assert config.enable_auto_rollback

    def test_configuration_validation(self):
        """Test configuration validation rules"""
        # Invalid unacceptable threshold
        config = EnvironmentConfig(unacceptable_threshold=1.5)
        with pytest.raises(ValueError, match="unacceptable_threshold"):
            config.validate()

        # Invalid critical threshold
        config = EnvironmentConfig(critical_threshold=-0.1)
        with pytest.raises(ValueError, match="critical_threshold"):
            config.validate()

        # Critical >= unacceptable
        config = EnvironmentConfig(critical_threshold=0.5, unacceptable_threshold=0.4)
        with pytest.raises(ValueError, match="less than unacceptable"):
            config.validate()

        # Invalid max_steps
        config = EnvironmentConfig(max_steps=0)
        with pytest.raises(ValueError, match="max_steps"):
            config.validate()

    def test_action_costs_and_cooldowns(self):
        """Test action costs and cooldowns configuration"""
        config = EnvironmentConfig()
        assert ActionType.ROLLBACK.value in config.action_cooldowns
        assert ActionType.RESTART.value in config.action_costs
        assert config.action_cooldowns[ActionType.ROLLBACK.value] == 5


# Test SystemMetrics
class TestSystemMetrics:
    """Test system metrics container"""

    def test_metrics_validation(self):
        """Test metrics are validated on creation"""
        metrics = SystemMetrics(
            pass_rate=1.5,  # Should be clipped to 1.0
            latency=-0.5,  # Should be set to 0
            alert_ratio=-0.1,  # Should be clipped to 0
        )
        assert metrics.pass_rate == 1.0
        assert metrics.latency == 0
        assert metrics.alert_ratio == 0

    def test_metrics_to_array(self):
        """Test conversion to numpy array"""
        metrics = SystemMetrics(pass_rate=0.9, latency=0.2, alert_ratio=0.1)
        arr = metrics.to_array(["pass_rate", "latency", "alert_ratio"])
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert list(arr) == [0.9, 0.2, 0.1]

    def test_metrics_to_dict(self):
        """Test conversion to dictionary"""
        metrics = SystemMetrics(pass_rate=0.9)
        d = metrics.to_dict()
        assert "pass_rate" in d
        assert "timestamp" in d
        assert d["pass_rate"] == 0.9


# Test AsyncActionExecutor
class TestAsyncActionExecutor:
    """Test async action executor"""

    @pytest.mark.asyncio
    async def test_async_execution(self):
        """Test executing async functions"""
        executor = AsyncActionExecutor()

        async def async_function(x):
            await asyncio.sleep(0.1)
            return x * 2

        try:
            result = executor.execute(async_function(5))
            assert result == 10
        finally:
            executor.close()

    def test_executor_cleanup(self):
        """Test executor cleanup"""
        executor = AsyncActionExecutor()
        assert executor._thread.is_alive()

        executor.close()
        time.sleep(0.5)
        assert not executor._thread.is_alive()

    def test_executor_error_handling(self):
        """Test error handling in executor"""
        executor = AsyncActionExecutor()

        async def failing_function():
            raise ValueError("Test error")

        try:
            with pytest.raises(ValueError, match="Test error"):
                executor.execute(failing_function())
        finally:
            executor.close()


# Test CodeHealthEnv Basic Functionality
class TestCodeHealthEnvBasics:
    """Test basic environment functionality"""

    def test_environment_initialization(self, basic_config, mock_metrics, mock_action):
        """Test environment initialization"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        assert env.session_id is not None
        assert env.observation_space.shape == (3,)
        assert env.action_space.n == 7
        assert not env.done
        assert env.steps == 0

        env.close()

    def test_backward_compatibility(self, mock_metrics, mock_action):
        """Test backward compatibility with old parameter style"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics,
            apply_action=mock_action,
            observation_keys=["pass_rate", "latency"],
            max_steps=50,
            unacceptable_threshold=0.3,
        )

        assert env.config.observation_keys == ["pass_rate", "latency"]
        assert env.config.max_steps == 50
        assert env.config.unacceptable_threshold == 0.3

        env.close()

    def test_invalid_parameters(self):
        """Test error handling for invalid parameters"""
        with pytest.raises(TypeError, match="get_metrics must be callable"):
            CodeHealthEnv(get_metrics="not_callable", apply_action=lambda x: {})

        with pytest.raises(TypeError, match="apply_action must be callable"):
            CodeHealthEnv(get_metrics=lambda: [], apply_action="not_callable")

    def test_reset(self, basic_config, mock_metrics, mock_action):
        """Test environment reset"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        # Take some steps
        env.step(ActionType.NOOP.value)
        env.step(ActionType.RESTART.value)
        assert env.steps == 2
        assert len(env.action_history) == 2

        # Reset
        obs, info = env.reset()
        assert env.steps == 0
        assert len(env.action_history) == 0
        assert env.cumulative_reward == 0.0
        assert not env.done
        assert obs.shape == (3,)
        assert "session_id" in info

        env.close()


# Test Step Functionality
class TestCodeHealthEnvStep:
    """Test step functionality"""

    def test_basic_step(self, basic_config, mock_metrics, mock_action):
        """Test basic step execution"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        obs, _ = env.reset()
        new_obs, reward, done, info = env.step(ActionType.NOOP.value)

        assert env.steps == 1
        assert isinstance(reward, float)
        assert isinstance(done, bool)
        assert "step" in info
        assert "timestamp" in info
        assert new_obs.shape == obs.shape

        env.close()

    def test_invalid_action(self, basic_config, mock_metrics, mock_action):
        """Test handling of invalid action"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()
        with pytest.raises(ValueError, match="Invalid action"):
            env.step(999)

        env.close()

    def test_action_cooldown(self, basic_config, mock_metrics, mock_action):
        """Test action cooldown mechanism"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()

        # Execute rollback (has 5 step cooldown)
        obs1, reward1, done1, info1 = env.step(ActionType.ROLLBACK.value)
        assert not info1.get("cooldown", False)

        # Try rollback again immediately - should be on cooldown
        obs2, reward2, done2, info2 = env.step(ActionType.ROLLBACK.value)
        assert info2.get("cooldown", False)
        assert info2.get("cooldown_remaining", 0) > 0

        env.close()

    def test_max_steps_termination(self, mock_metrics, mock_action):
        """Test termination at max steps"""
        config = EnvironmentConfig(max_steps=3)
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=config
        )

        env.reset()

        for i in range(3):
            obs, reward, done, info = env.step(ActionType.NOOP.value)
            if i < 2:
                assert not done
            else:
                assert done  # Should terminate at max_steps

        env.close()


# Test Automatic Rollback
class TestAutomaticRollback:
    """Test automatic rollback functionality"""

    def test_automatic_rollback_triggered(self, mock_action):
        """Test automatic rollback when metrics fall below threshold"""
        call_count = 0

        def degrading_metrics():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [0.8, 0.2, 0.1]  # Good metrics
            else:
                return [
                    0.15,
                    0.8,
                    0.5,
                ]  # Bad metrics (pass_rate below critical threshold)

        config = EnvironmentConfig(
            unacceptable_threshold=0.4,
            critical_threshold=0.2,
            enable_auto_rollback=True,
        )

        env = CodeHealthEnv(
            get_metrics=degrading_metrics, apply_action=mock_action, config=config
        )

        env.reset()
        env.step(ActionType.NOOP.value)
        env.step(ActionType.NOOP.value)

        # This step should trigger automatic rollback or termination
        obs, reward, done, info = env.step(ActionType.NOOP.value)

        # Either rollback was triggered OR environment terminated due to critical state
        assert "automatic_rollback" in info or done
        if "automatic_rollback" in info:
            assert info.get("severity") in ["critical", "unacceptable"]
            assert reward < 0  # Should include rollback penalty

        env.close()

    def test_rollback_disabled(self, mock_metrics, mock_action):
        """Test behavior when automatic rollback is disabled"""
        config = EnvironmentConfig(enable_auto_rollback=False)

        def bad_metrics():
            return [
                0.09,
                0.9,
                0.9,
            ]  # Very bad metrics (below default critical threshold of 0.1)

        env = CodeHealthEnv(
            get_metrics=bad_metrics, apply_action=mock_action, config=config
        )

        env.reset()
        obs, reward, done, info = env.step(ActionType.NOOP.value)

        assert "automatic_rollback" not in info
        assert done  # Should still terminate due to critical state

        env.close()


# Test Async Support
class TestAsyncSupport:
    """Test async action support"""

    @pytest.mark.asyncio
    async def test_async_action_function(self, mock_metrics):
        """Test environment with async action function"""

        async def async_action(action_id: int):
            await asyncio.sleep(0.01)
            return {"success": True, "async": True, "action_id": action_id}

        env = CodeHealthEnv(get_metrics=mock_metrics, apply_action=async_action)

        assert env._is_async
        assert env._async_executor is not None

        env.reset()
        obs, reward, done, info = env.step(ActionType.RESTART.value)

        assert info["action_result"]["async"]
        assert info["action_result"]["action_id"] == ActionType.RESTART.value

        env.close()

    def test_sync_action_function(self, mock_metrics, mock_action):
        """Test environment with sync action function"""
        env = CodeHealthEnv(get_metrics=mock_metrics, apply_action=mock_action)

        assert not env._is_async
        assert env._async_executor is None

        env.reset()
        obs, reward, done, info = env.step(ActionType.NOOP.value)

        assert info["action_result"]["success"]

        env.close()


# Test Thread Safety
class TestThreadSafety:
    """Test thread safety of the environment"""

    def test_concurrent_steps(self, mock_metrics, mock_action):
        """Test concurrent step execution"""
        env = CodeHealthEnv(get_metrics=mock_metrics, apply_action=mock_action)

        env.reset()

        results = []
        errors = []

        def run_step(action):
            try:
                result = env.step(action)
                results.append(result)
            except Exception as e:
                errors.append(e)

        # Create multiple threads trying to step simultaneously
        threads = []
        for i in range(5):
            t = threading.Thread(target=run_step, args=(ActionType.NOOP.value,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5
        assert env.steps == 5

        env.close()

    def test_concurrent_reset_and_step(self, mock_metrics, mock_action):
        """Test concurrent reset and step operations"""
        env = CodeHealthEnv(get_metrics=mock_metrics, apply_action=mock_action)

        def reset_env():
            env.reset()

        def step_env():
            try:
                env.step(ActionType.NOOP.value)
            except:
                pass  # May fail if environment is being reset

        threads = []
        for i in range(10):
            if i % 2 == 0:
                t = threading.Thread(target=reset_env)
            else:
                t = threading.Thread(target=step_env)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Environment should still be in valid state
        obs, info = env.reset()
        assert obs is not None
        assert env.steps == 0

        env.close()


# Test Memory Management
class TestMemoryManagement:
    """Test memory management features"""

    def test_action_history_limit(self, mock_metrics, mock_action):
        """Test that action history respects max size"""
        config = EnvironmentConfig(max_action_history=5)
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=config
        )

        env.reset()

        # Take more steps than max_action_history
        for i in range(10):
            env.step(ActionType.NOOP.value)

        assert len(env.action_history) <= 5
        assert env.steps == 10

        env.close()

    def test_metrics_history_limit(self, mock_metrics, mock_action):
        """Test that metrics history respects max size"""
        config = EnvironmentConfig(max_action_history=3)
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=config
        )

        env.reset()

        for i in range(10):
            env.step(ActionType.NOOP.value)

        assert len(env.metrics_history) <= 3

        env.close()


# Test Reward Calculation
class TestRewardCalculation:
    """Test reward calculation logic"""

    def test_state_based_rewards(self, mock_action):
        """Test state-based reward calculation"""
        config = EnvironmentConfig(
            reward_weights={"pass_rate": 1.0, "latency": -1.0, "alert_ratio": -0.5}
        )

        def good_metrics():
            return [0.9, 0.1, 0.05]  # Good metrics

        env = CodeHealthEnv(
            get_metrics=good_metrics, apply_action=mock_action, config=config
        )

        env.reset()
        obs, reward, done, info = env.step(ActionType.NOOP.value)

        # Reward should be positive with good metrics
        assert reward > 0

        env.close()

    def test_action_costs(self, mock_metrics):
        """Test action cost penalties"""
        config = EnvironmentConfig(
            action_costs={
                ActionType.ROLLBACK.value: -1.0,
                ActionType.RESTART.value: -0.5,
            }
        )

        def mock_action(action_id):
            return {"success": True}

        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=config
        )

        env.reset()

        # Test rollback cost
        _, reward1, _, _ = env.step(ActionType.ROLLBACK.value)

        # Test restart cost
        _, reward2, _, _ = env.step(ActionType.RESTART.value)

        # Rollback should have higher penalty
        assert reward1 < reward2

        env.close()

    def test_success_bonus(self, mock_metrics):
        """Test success/failure bonus in rewards"""

        def action_with_result(success):
            def apply_action(action_id):
                return {"success": success}

            return apply_action

        config = EnvironmentConfig()

        # Test with successful action
        env_success = CodeHealthEnv(
            get_metrics=mock_metrics,
            apply_action=action_with_result(True),
            config=config,
        )
        env_success.reset()
        _, reward_success, _, _ = env_success.step(ActionType.NOOP.value)
        env_success.close()

        # Test with failed action
        env_fail = CodeHealthEnv(
            get_metrics=mock_metrics,
            apply_action=action_with_result(False),
            config=config,
        )
        env_fail.reset()
        _, reward_fail, _, _ = env_fail.step(ActionType.NOOP.value)
        env_fail.close()

        # Success should give higher reward
        assert reward_success > reward_fail


# Test Rendering
class TestRendering:
    """Test rendering functionality"""

    def test_human_render(self, basic_config, mock_metrics, mock_action, capsys):
        """Test human-readable rendering"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()
        env.render(mode="human")

        captured = capsys.readouterr()
        assert "Session" in captured.out
        assert "Step: 0" in captured.out
        assert "Cumulative Reward" in captured.out

        env.close()

    def test_rgb_array_render(self, basic_config, mock_metrics, mock_action):
        """Test RGB array rendering for video"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()
        env.step(ActionType.NOOP.value)

        img = env.render(mode="rgb_array")

        assert isinstance(img, np.ndarray)
        assert img.ndim == 3  # Height, Width, Channels
        assert img.shape[2] in [3, 4]  # RGB or RGBA
        assert img.dtype == np.uint8

        env.close()

    def test_ansi_render(self, basic_config, mock_metrics, mock_action, capsys):
        """Test ANSI colored terminal rendering"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()
        env.render(mode="ansi")

        captured = capsys.readouterr()
        assert "Code Health Monitor" in captured.out
        assert "pass_rate" in captured.out

        env.close()

    def test_invalid_render_mode(self, basic_config, mock_metrics, mock_action):
        """Test invalid render mode"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()
        with pytest.raises(NotImplementedError, match="not supported"):
            env.render(mode="invalid_mode")

        env.close()


# Test Data Export
class TestDataExport:
    """Test training data export and metrics summary"""

    def test_get_training_data(self, basic_config, mock_metrics, mock_action):
        """Test training data export"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=basic_config
        )

        env.reset()

        # Take some steps
        for i in range(3):
            env.step(i % len(ActionType))

        training_data = env.get_training_data()

        assert len(training_data) == 3
        for record in training_data:
            assert "step" in record
            assert "action" in record
            assert "reward" in record
            assert "state_before" in record
            assert "state_after" in record
            assert "timestamp" in record

        env.close()

    def test_get_metrics_summary(self, mock_action):
        """Test metrics summary generation"""
        step_count = 0

        def varying_metrics():
            nonlocal step_count
            step_count += 1
            # Return different metrics each time
            return [
                0.5 + 0.1 * step_count,
                0.3 - 0.05 * step_count,
                0.1 + 0.02 * step_count,
            ]

        config = EnvironmentConfig(
            observation_keys=["pass_rate", "latency", "alert_ratio"]
        )

        env = CodeHealthEnv(
            get_metrics=varying_metrics, apply_action=mock_action, config=config
        )

        env.reset()

        for i in range(5):
            env.step(ActionType.NOOP.value)

        summary = env.get_metrics_summary()

        assert "mean" in summary
        assert "std" in summary
        assert "min" in summary
        assert "max" in summary
        assert "current" in summary
        assert "improvement" in summary

        # Check improvement calculation
        assert "pass_rate" in summary["improvement"]

        env.close()


# Test Audit Logging
class TestAuditLogging:
    """Test audit logging functionality"""

    def test_audit_logging_lifecycle(
        self, basic_config, mock_metrics, mock_action, mock_audit_logger
    ):
        """Test audit logging throughout environment lifecycle"""
        env = CodeHealthEnv(
            get_metrics=mock_metrics,
            apply_action=mock_action,
            config=basic_config,
            audit_logger=mock_audit_logger,
        )

        # Check initialization logged
        mock_audit_logger.log_event.assert_called()

        # Helper function to extract event types
        def get_event_types(call_list, event_name):
            found_calls = []
            for call in call_list:
                event_type = None
                # Check positional args
                if call and len(call) > 0 and call[0]:
                    try:
                        event_type = call[0][0]
                    except (IndexError, TypeError):
                        pass
                # Check keyword args if no positional match
                if not event_type and len(call) > 1 and call[1]:
                    event_type = call[1].get("event_type")

                if event_type == event_name:
                    found_calls.append(call)
            return found_calls

        # Check init event
        init_calls = get_event_types(
            mock_audit_logger.log_event.call_args_list, "rl_env_init"
        )
        assert len(init_calls) == 1

        # Reset
        env.reset()
        reset_calls = get_event_types(
            mock_audit_logger.log_event.call_args_list, "rl_episode_reset"
        )
        assert len(reset_calls) == 1

        # Step
        env.step(ActionType.RESTART.value)
        step_calls = get_event_types(
            mock_audit_logger.log_event.call_args_list, "rl_step"
        )
        assert len(step_calls) >= 1

        # Close
        env.close()
        close_calls = get_event_types(
            mock_audit_logger.log_event.call_args_list, "rl_env_closed"
        )
        assert len(close_calls) == 1


# Integration Tests
class TestIntegration:
    """Integration tests for complete scenarios"""

    def test_full_episode(self, mock_action):
        """Test a complete episode from start to finish"""
        metrics_sequence = [
            [0.9, 0.1, 0.05],  # Good
            [0.8, 0.2, 0.1],  # Okay
            [0.6, 0.4, 0.2],  # Degrading
            [0.3, 0.6, 0.4],  # Bad - should trigger rollback
            [0.7, 0.3, 0.15],  # Recovered after rollback
        ]

        step = 0

        def sequential_metrics():
            nonlocal step
            result = metrics_sequence[min(step, len(metrics_sequence) - 1)]
            step += 1
            return result

        config = EnvironmentConfig(
            max_steps=10, unacceptable_threshold=0.4, enable_auto_rollback=True
        )

        env = CodeHealthEnv(
            get_metrics=sequential_metrics, apply_action=mock_action, config=config
        )

        obs, info = env.reset()
        total_reward = 0

        actions = [
            ActionType.NOOP.value,
            ActionType.RUN_TESTS.value,
            ActionType.APPLY_PATCH.value,
            ActionType.NOOP.value,
        ]

        for action in actions:
            obs, reward, done, info = env.step(action)
            total_reward += reward

            if done:
                break

        # Check that automatic rollback was triggered
        training_data = env.get_training_data()
        assert any(
            "automatic_rollback" in record.get("info", {}) for record in training_data
        )

        env.close()

    def test_stress_test(self, mock_metrics, mock_action):
        """Stress test with many rapid steps"""
        config = EnvironmentConfig(max_steps=1000)
        env = CodeHealthEnv(
            get_metrics=mock_metrics, apply_action=mock_action, config=config
        )

        env.reset()

        start_time = time.time()
        for i in range(100):
            action = np.random.randint(0, env.action_space.n)
            obs, reward, done, info = env.step(action)
            if done:
                env.reset()

        duration = time.time() - start_time

        # Should complete 100 steps reasonably quickly
        assert duration < 5.0  # 5 seconds for 100 steps

        env.close()


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
