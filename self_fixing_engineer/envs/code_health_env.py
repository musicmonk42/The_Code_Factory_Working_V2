"""
Enhanced Code Health Environment for Reinforcement Learning
This module provides a production-ready RL environment for monitoring and managing
code, infrastructure, and test health with proper async handling, memory management,
and thread safety.
"""

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import logging
import uuid
from typing import Dict, Any, Optional, List, Callable, Tuple, Union
import matplotlib.pyplot as plt
import io
import datetime
import asyncio
import threading
from dataclasses import dataclass, field, asdict
from enum import Enum
from termcolor import colored
import time
import os
import subprocess
import sys
from collections import deque

# Try to import guardrails, provide mock if not available
try:
    from guardrails.audit_log import AuditLogger
except ImportError:

    class AuditLogger:
        """Mock AuditLogger for when guardrails is not installed"""

        @classmethod
        def from_environment(cls):
            return cls()

        def log_event(self, event_type: str, details: Dict[str, Any], **kwargs):
            logger.info(f"[Audit] {event_type}: {details}")


# Configure module logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ActionType(Enum):
    """Enumeration of available actions in the environment"""

    NOOP = 0
    RESTART = 1
    ROLLBACK = 2
    APPLY_PATCH = 3
    RUN_LINTER = 4
    RUN_TESTS = 5
    RUN_FORMATTER = 6


@dataclass
class EnvironmentConfig:
    """Configuration for the CodeHealthEnv"""

    observation_keys: List[str] = field(
        default_factory=lambda: [
            "pass_rate",
            "latency",
            "alert_ratio",
            "code_coverage",
            "complexity",
        ]
    )
    max_steps: int = 100
    unacceptable_threshold: float = 0.2
    critical_threshold: float = 0.1
    reward_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "pass_rate": 1.0,
            "latency": -0.5,
            "alert_ratio": -1.0,
            "code_coverage": 0.8,
            "complexity": -0.3,
        }
    )
    action_costs: Dict[int, float] = field(
        default_factory=lambda: {
            ActionType.RESTART.value: -0.1,
            ActionType.ROLLBACK.value: -0.2,
            ActionType.APPLY_PATCH.value: -0.05,
            ActionType.RUN_LINTER.value: -0.01,
            ActionType.RUN_TESTS.value: -0.01,
            ActionType.RUN_FORMATTER.value: -0.01,
        }
    )
    action_cooldowns: Dict[int, int] = field(
        default_factory=lambda: {
            ActionType.ROLLBACK.value: 5,
            ActionType.RESTART.value: 3,
        }
    )
    max_action_history: int = 1000
    enable_auto_rollback: bool = True
    latency_normalization_factor: float = 10000.0
    render_dpi: int = 100

    def validate(self) -> None:
        """Validate configuration parameters"""
        if not 0 <= self.unacceptable_threshold <= 1:
            raise ValueError("unacceptable_threshold must be between 0 and 1")
        if not 0 <= self.critical_threshold <= 1:
            raise ValueError("critical_threshold must be between 0 and 1")
        if self.critical_threshold >= self.unacceptable_threshold:
            raise ValueError("critical_threshold must be less than unacceptable_threshold")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.max_action_history <= 0:
            raise ValueError("max_action_history must be positive")


@dataclass
class SystemMetrics:
    """Container for system metrics with validation"""

    pass_rate: float = 0.0
    latency: float = 0.0
    alert_ratio: float = 0.0
    code_coverage: float = 0.0
    complexity: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """Validate metrics are within expected ranges"""
        for field_name in ["pass_rate", "alert_ratio", "code_coverage", "complexity"]:
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                setattr(self, field_name, np.clip(value, 0, 1))

        if self.latency < 0:
            self.latency = 0

    def to_array(self, keys: List[str]) -> np.ndarray:
        """Convert metrics to numpy array based on requested keys"""
        return np.array([getattr(self, key, 0.0) for key in keys], dtype=np.float32)

    def to_dict(self) -> Dict[str, float]:
        """Convert metrics to dictionary"""
        return {
            "pass_rate": self.pass_rate,
            "latency": self.latency,
            "alert_ratio": self.alert_ratio,
            "code_coverage": self.code_coverage,
            "complexity": self.complexity,
            "timestamp": self.timestamp,
        }


class AsyncActionExecutor:
    """
    Thread-safe executor for async actions.
    Manages async operations in a dedicated thread with its own event loop.
    """

    def __init__(self):
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._start_loop()

    def _start_loop(self):
        """Start the async event loop in a separate thread"""

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            self._loop.run_until_complete(self._run_until_stopped())

        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)  # Wait for loop to be ready

        if not self._ready.is_set():
            raise RuntimeError("Failed to start async executor")

    async def _run_until_stopped(self):
        """Keep the loop running until stop is signaled"""
        while not self._stop_event.is_set():
            await asyncio.sleep(0.1)

    def execute(self, coro) -> Any:
        """Execute an async coroutine and return the result synchronously"""
        if not self._loop or not self._thread.is_alive():
            raise RuntimeError("Async executor is not running")

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=30)  # 30 second timeout
        except Exception as e:
            logger.error(f"Async execution failed: {e}")
            raise

    def close(self):
        """Clean up the async executor"""
        if self._loop and self._thread:
            self._stop_event.set()

            # Give the loop time to finish
            self._thread.join(timeout=5)

            # Force close if still running
            if self._thread.is_alive():
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=2)


class CodeHealthEnv(gym.Env):
    """
    Production-ready RL environment for code, infrastructure, and test health monitoring.

    Features:
    - Thread-safe state management
    - Proper async/sync handling
    - Memory-efficient action history
    - Configurable automatic rollback
    - Comprehensive audit logging
    - Multiple rendering modes
    """

    def __init__(
        self,
        get_metrics: Callable[[], Union[List[float], SystemMetrics]],
        apply_action: Callable[[int], Dict[str, Any]],
        audit_logger: Optional[AuditLogger] = None,
        session_id: Optional[str] = None,
        config: Optional[EnvironmentConfig] = None,
        observation_keys: Optional[List[str]] = None,  # For backward compatibility
        action_map: Optional[Dict[int, str]] = None,  # For backward compatibility
        max_steps: Optional[int] = None,  # For backward compatibility
        unacceptable_threshold: Optional[float] = None,  # For backward compatibility
        reward_weights: Optional[Dict[str, float]] = None,  # For backward compatibility
    ):
        super().__init__()

        # Validate callable parameters
        if not callable(get_metrics):
            raise TypeError("get_metrics must be callable")
        if not callable(apply_action):
            raise TypeError("apply_action must be callable")

        # Handle backward compatibility
        if config is None:
            config = EnvironmentConfig()
            if observation_keys is not None:
                config.observation_keys = observation_keys
            if max_steps is not None:
                config.max_steps = max_steps
            if unacceptable_threshold is not None:
                config.unacceptable_threshold = unacceptable_threshold
            if reward_weights is not None:
                config.reward_weights = reward_weights

        # Validate configuration
        config.validate()

        self.config = config
        self.get_metrics_func = get_metrics
        self.apply_action_func = apply_action
        self.audit_logger = audit_logger or AuditLogger.from_environment()
        self.session_id = session_id or str(uuid.uuid4())

        # Set up action map (handle backward compatibility)
        if action_map is not None:
            self.action_map = action_map
        else:
            self.action_map = {
                ActionType.NOOP.value: "noop",
                ActionType.RESTART.value: "restart",
                ActionType.ROLLBACK.value: "rollback",
                ActionType.APPLY_PATCH.value: "apply_patch",
                ActionType.RUN_LINTER.value: "run_linter",
                ActionType.RUN_TESTS.value: "run_tests",
                ActionType.RUN_FORMATTER.value: "run_formatter",
            }

        # Set up async executor if needed
        self._async_executor = None
        self._is_async = asyncio.iscoroutinefunction(apply_action)
        if self._is_async:
            self._async_executor = AsyncActionExecutor()

        # Initialize observation and action spaces
        self.observation_space = spaces.Box(
            low=0, high=1, shape=(len(self.config.observation_keys),), dtype=np.float32
        )
        self.action_space = spaces.Discrete(len(self.action_map))

        # Initialize state tracking with thread safety
        self._state_lock = threading.RLock()
        self.state = np.zeros(len(self.config.observation_keys), dtype=np.float32)
        self.steps = 0
        self.done = False
        self.last_state_before_action = None

        # Use deque for memory-efficient action history
        self.action_history = deque(maxlen=self.config.max_action_history)
        self.metrics_history = deque(maxlen=self.config.max_action_history)

        # Track action cooldowns
        self.action_cooldowns = {}

        # Performance tracking
        self.cumulative_reward = 0.0
        self.episode_start_time = time.time()

        logger.info(f"CodeHealthEnv initialized - Session: {self.session_id}")
        self.audit_logger.log_event(
            event_type="rl_env_init",
            details={
                "session_id": self.session_id,
                "config": asdict(self.config),
                "action_map": self.action_map,
                "is_async": self._is_async,
            },
        )

    def _get_current_metrics(self) -> SystemMetrics:
        """Get current metrics and convert to SystemMetrics object"""
        with self._state_lock:
            try:
                metrics = self.get_metrics_func()

                if isinstance(metrics, SystemMetrics):
                    return metrics
                elif isinstance(metrics, (list, tuple, np.ndarray)):
                    # Convert array to SystemMetrics
                    metrics_dict = {}
                    for i, key in enumerate(self.config.observation_keys[: len(metrics)]):
                        metrics_dict[key] = float(metrics[i])

                    # Fill missing metrics with defaults
                    for key in self.config.observation_keys:
                        if key not in metrics_dict:
                            metrics_dict[key] = 0.0

                    return SystemMetrics(**metrics_dict)
                else:
                    raise ValueError(
                        f"get_metrics must return SystemMetrics or array, got {type(metrics)}"
                    )
            except Exception as e:
                logger.error(f"Failed to get metrics: {e}")
                # Return degraded metrics on error
                return SystemMetrics(pass_rate=0.0, latency=1.0, alert_ratio=1.0)

    def _apply_action_wrapper(self, action: int) -> Dict[str, Any]:
        """Wrapper to handle both sync and async apply_action functions"""
        try:
            if self._is_async and self._async_executor:
                return self._async_executor.execute(self.apply_action_func(action))
            else:
                return self.apply_action_func(action)
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return {"success": False, "error": str(e)}

    def _check_action_cooldown(self, action: int) -> Tuple[bool, int]:
        """Check if an action is on cooldown"""
        if action not in self.config.action_cooldowns:
            return True, 0

        cooldown = self.config.action_cooldowns[action]
        last_used = self.action_cooldowns.get(action, -float("inf"))
        remaining = cooldown - (self.steps - last_used)

        return remaining <= 0, max(0, remaining)

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict[str, Any]]:
        """Execute one step in the environment with thread safety"""
        with self._state_lock:
            # Validate action
            if action not in self.action_map:
                raise ValueError(
                    f"Invalid action: {action}. Must be in {list(self.action_map.keys())}"
                )

            self.steps += 1
            info = {"step": self.steps, "timestamp": time.time()}
            action_name = self.action_map[action]

            # Check cooldown
            can_execute, cooldown_remaining = self._check_action_cooldown(action)
            if not can_execute:
                logger.debug(
                    f"Action {action_name} on cooldown for {cooldown_remaining} more steps"
                )
                info["cooldown"] = True
                info["cooldown_remaining"] = cooldown_remaining
                action = ActionType.NOOP.value
                action_name = "noop"

            # Update cooldown tracking
            if action in self.config.action_cooldowns:
                self.action_cooldowns[action] = self.steps

            # Save current state
            self.last_state_before_action = np.copy(self.state)

            # Execute action
            action_result = self._apply_action_wrapper(action)
            info["action_result"] = action_result

            # Update state
            metrics = self._get_current_metrics()
            self.state = metrics.to_array(self.config.observation_keys)
            self.metrics_history.append(metrics)

            # Compute reward
            reward = self._compute_reward(self.state, action, action_result)

            # Check termination conditions FIRST
            self.done = self._check_termination()

            # Then check for automatic rollback only if not done and enabled
            if not self.done and self.config.enable_auto_rollback:
                rollback_info = self._check_and_handle_rollback(action)
                if rollback_info:
                    info.update(rollback_info)
                    reward += rollback_info.get("rollback_reward", 0)
                    # Re-check termination after rollback
                    self.done = self._check_termination()

            # Update cumulative reward with the final step reward
            self.cumulative_reward += reward

            # Record step
            self._record_step(action, action_name, reward, info)

            # Log step summary
            if self.done:
                self._handle_episode_end()
            else:
                logger.debug(
                    f"Step {self.steps}: Action={action_name}, Reward={reward:.2f}, State={self.state.round(2).tolist()}"
                )

            return self.state, reward, self.done, info

    def _check_and_handle_rollback(self, current_action: int) -> Optional[Dict[str, Any]]:
        """Check if automatic rollback is needed and handle it"""
        pass_rate_idx = (
            self.config.observation_keys.index("pass_rate")
            if "pass_rate" in self.config.observation_keys
            else -1
        )

        if pass_rate_idx == -1:
            return None

        pass_rate = self.state[pass_rate_idx]

        # Don't rollback if we just did a rollback
        if current_action == ActionType.ROLLBACK.value:
            return None

        # Check thresholds
        if pass_rate < self.config.critical_threshold:
            severity = "critical"
            rollback_reward = -10.0
        elif pass_rate < self.config.unacceptable_threshold:
            severity = "unacceptable"
            rollback_reward = -5.0
        else:
            return None

        logger.warning(
            f"[SAFEGUARD] {severity.upper()} state detected (pass_rate: {pass_rate:.2f}). Initiating automatic rollback."
        )

        self.audit_logger.log_event(
            event_type=f"rl_{severity}_state_rollback",
            details={
                "session_id": self.session_id,
                "state_before": self.state.tolist(),
                "pass_rate": pass_rate,
                "step": self.steps,
            },
        )

        try:
            rollback_result = self._apply_action_wrapper(ActionType.ROLLBACK.value)

            # Update state after rollback
            metrics = self._get_current_metrics()
            self.state = metrics.to_array(self.config.observation_keys)

            return {
                "automatic_rollback": True,
                "severity": severity,
                "rollback_result": rollback_result,
                "rollback_reward": rollback_reward,
                "state_after_rollback": self.state.tolist(),
            }
        except Exception as e:
            logger.critical(f"[CRITICAL] Automatic rollback failed: {e}")
            self.done = True
            return {
                "automatic_rollback": False,
                "rollback_error": str(e),
                "rollback_reward": -15.0,
            }

    def _check_termination(self) -> bool:
        """Check if episode should terminate"""
        # Check max steps
        if self.steps >= self.config.max_steps:
            return True

        # Check critical state (terminate even if rollback is disabled)
        pass_rate_idx = (
            self.config.observation_keys.index("pass_rate")
            if "pass_rate" in self.config.observation_keys
            else -1
        )
        if pass_rate_idx != -1 and self.state[pass_rate_idx] < self.config.critical_threshold:
            return True

        return False

    def _compute_reward(self, state: np.ndarray, action: int, result: Dict[str, Any]) -> float:
        """Compute reward based on state, action, and result"""
        reward = 0.0

        # State-based rewards
        for key, weight in self.config.reward_weights.items():
            if key in self.config.observation_keys:
                idx = self.config.observation_keys.index(key)

                # Normalize latency if needed, with safety check for division by zero
                if key == "latency":
                    if self.config.latency_normalization_factor <= 0:
                        logger.error(
                            "latency_normalization_factor must be positive, defaulting to 10000.0"
                        )
                        normalized_value = np.clip(state[idx] / 10000.0, 0.0, 1.0)
                    else:
                        normalized_value = np.clip(
                            state[idx] / self.config.latency_normalization_factor,
                            0.0,
                            1.0,
                        )
                else:
                    normalized_value = state[idx]

                reward += normalized_value * weight

        # Action cost
        reward += self.config.action_costs.get(action, 0)

        # Success bonus
        if result.get("success", True):
            reward += 0.05
        else:
            reward -= 0.1

        return float(reward)

    def _record_step(self, action: int, action_name: str, reward: float, info: Dict[str, Any]):
        """Record step in action history"""
        step_record = {
            "step": self.steps,
            "action": action_name,
            "action_id": action,
            "state_before": (
                self.last_state_before_action.tolist()
                if self.last_state_before_action is not None
                else []
            ),
            "state_after": self.state.tolist(),
            "reward": reward,
            "cumulative_reward": self.cumulative_reward,
            "done": self.done,
            "timestamp": time.time(),
            "info": info,
        }

        self.action_history.append(step_record)

        self.audit_logger.log_event(
            event_type="rl_step",
            details={
                "session_id": self.session_id,
                "step": self.steps,
                "action": action_name,
                "reward": reward,
                "state": self.state.tolist(),
            },
        )

    def _handle_episode_end(self):
        """Handle episode termination"""
        duration = time.time() - self.episode_start_time

        # Determine termination reason
        if self.steps >= self.config.max_steps:
            reason = "max_steps_reached"
        else:
            pass_rate_idx = (
                self.config.observation_keys.index("pass_rate")
                if "pass_rate" in self.config.observation_keys
                else -1
            )
            if pass_rate_idx != -1 and self.state[pass_rate_idx] < self.config.critical_threshold:
                reason = "critical_state"
            else:
                reason = "unknown"

        self.audit_logger.log_event(
            event_type="rl_episode_end",
            details={
                "session_id": self.session_id,
                "final_state": self.state.tolist(),
                "total_reward": self.cumulative_reward,
                "total_steps": self.steps,
                "duration_seconds": duration,
                "reason": reason,
            },
        )

        logger.info(
            f"Episode ended - Session: {self.session_id}, Steps: {self.steps}, Reward: {self.cumulative_reward:.2f}, Reason: {reason}"
        )

    def reset(self, seed=None, options=None) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Reset the environment to initial state"""
        super().reset(seed=seed)

        with self._state_lock:
            self.steps = 0
            self.done = False

            # Get initial metrics
            metrics = self._get_current_metrics()
            self.state = metrics.to_array(self.config.observation_keys)

            # Clear histories (deques handle max size automatically)
            self.action_history.clear()
            self.metrics_history.clear()
            self.metrics_history.append(metrics)

            # Reset tracking
            self.cumulative_reward = 0.0
            self.action_cooldowns.clear()
            self.episode_start_time = time.time()
            self.last_state_before_action = None

            self.audit_logger.log_event(
                event_type="rl_episode_reset",
                details={
                    "session_id": self.session_id,
                    "initial_state": self.state.tolist(),
                },
            )

            logger.info(
                f"Environment reset - Session: {self.session_id}, Initial state: {self.state.round(2).tolist()}"
            )

            return self.state, {"session_id": self.session_id}

    def render(self, mode: str = "human") -> Optional[np.ndarray]:
        """Render the environment state"""
        if mode == "human":
            print(f"\n[Session {self.session_id[:8]}...] Step: {self.steps}")
            print(f"State: {dict(zip(self.config.observation_keys, self.state.round(3)))}")
            print(f"Cumulative Reward: {self.cumulative_reward:.2f}")
            return None

        elif mode == "rgb_array":
            return self._render_rgb_array()

        elif mode == "ansi":
            return self._render_ansi()

        else:
            raise NotImplementedError(f"Rendering mode '{mode}' is not supported")

    def _render_rgb_array(self) -> np.ndarray:
        """Render state as RGB array for video recording"""
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

        # Current state bar chart
        colors = []
        for i, key in enumerate(self.config.observation_keys):
            val = self.state[i]
            if key in ["pass_rate", "code_coverage"]:
                colors.append("green" if val > 0.8 else "yellow" if val > 0.5 else "red")
            elif key in ["latency", "alert_ratio", "complexity"]:
                colors.append("green" if val < 0.3 else "yellow" if val < 0.6 else "red")
            else:
                colors.append("blue")

        bars = ax1.bar(self.config.observation_keys, self.state, color=colors)
        ax1.set_ylim(0, 1)
        ax1.set_title(f"Code Health State (Step: {self.steps})")
        ax1.set_ylabel("Value")
        ax1.tick_params(axis="x", rotation=45)

        # Add value labels on bars
        for bar in bars:
            height = bar.get_height()
            ax1.text(
                bar.get_x() + bar.get_width() / 2.0,
                height,
                f"{height:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )

        # Metrics history
        if len(self.metrics_history) > 1:
            history_array = np.array(
                [m.to_array(self.config.observation_keys) for m in self.metrics_history]
            )
            x = range(len(history_array))

            for i, key in enumerate(self.config.observation_keys):
                ax2.plot(x, history_array[:, i], label=key, marker="o", markersize=2)

            ax2.set_xlabel("Step")
            ax2.set_ylabel("Value")
            ax2.set_title("Metrics History")
            ax2.legend(loc="best", fontsize=8)
            ax2.grid(True, alpha=0.3)
        else:
            ax2.text(0.5, 0.5, "No history yet", ha="center", va="center")
            ax2.set_xlim(0, 1)
            ax2.set_ylim(0, 1)

        plt.tight_layout()

        # Convert to RGB array
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=self.config.render_dpi)
        buf.seek(0)
        img = plt.imread(buf)
        plt.close(fig)

        return (img * 255).astype(np.uint8)

    def _render_ansi(self) -> None:
        """Render state with colored terminal output"""
        output = colored("\n" + "=" * 60 + "\n", "blue")
        output += colored(f"Code Health Monitor - Step {self.steps}\n", "white", attrs=["bold"])
        output += colored(f"Session: {self.session_id[:8]}...\n", "cyan")
        output += colored("=" * 60 + "\n", "blue")

        for i, key in enumerate(self.config.observation_keys):
            val = self.state[i]

            # Determine color based on metric type and value
            if key in ["pass_rate", "code_coverage"]:
                color = "green" if val > 0.8 else "yellow" if val > 0.5 else "red"
            elif key in ["latency", "alert_ratio", "complexity"]:
                color = "green" if val < 0.3 else "yellow" if val < 0.6 else "red"
            else:
                color = "white"

            # Create visual bar
            bar_length = int(val * 20)
            bar = "█" * bar_length + "░" * (20 - bar_length)

            output += f"  {key:15s}: {colored(bar, color)} {colored(f'{val:.3f}', color)}\n"

        output += colored(f"\nCumulative Reward: {self.cumulative_reward:.2f}\n", "magenta")

        # Show cooldowns if any
        active_cooldowns = []
        for action_id, cooldown_step in self.action_cooldowns.items():
            if action_id in self.config.action_cooldowns:
                remaining = self.config.action_cooldowns[action_id] - (self.steps - cooldown_step)
                if remaining > 0:
                    active_cooldowns.append(f"{self.action_map[action_id]}: {remaining}")

        if active_cooldowns:
            output += colored(f"Active Cooldowns: {', '.join(active_cooldowns)}\n", "yellow")

        print(output)
        return None

    def get_training_data(self) -> List[Dict[str, Any]]:
        """Get action history for training purposes"""
        return list(self.action_history)

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary statistics of metrics history"""
        if not self.metrics_history:
            return {}

        metrics_array = np.array(
            [m.to_array(self.config.observation_keys) for m in self.metrics_history]
        )

        return {
            "mean": dict(zip(self.config.observation_keys, metrics_array.mean(axis=0))),
            "std": dict(zip(self.config.observation_keys, metrics_array.std(axis=0))),
            "min": dict(zip(self.config.observation_keys, metrics_array.min(axis=0))),
            "max": dict(zip(self.config.observation_keys, metrics_array.max(axis=0))),
            "current": dict(zip(self.config.observation_keys, self.state)),
            "improvement": dict(
                zip(
                    self.config.observation_keys,
                    (
                        self.state - metrics_array[0]
                        if len(metrics_array) > 0
                        else np.zeros_like(self.state)
                    ),
                )
            ),
        }

    def close(self) -> None:
        """Clean up resources"""
        logger.info(f"Closing environment - Session: {self.session_id}")

        # Close async executor if exists
        if self._async_executor:
            self._async_executor.close()
            self._async_executor = None

        # Final audit log
        self.audit_logger.log_event(
            event_type="rl_env_closed",
            details={
                "session_id": self.session_id,
                "total_steps": self.steps,
                "final_reward": self.cumulative_reward,
                "metrics_summary": self.get_metrics_summary(),
            },
        )

        # Clear histories
        self.action_history.clear()
        self.metrics_history.clear()

        # Reset state
        with self._state_lock:
            self.state = np.zeros(len(self.config.observation_keys), dtype=np.float32)


def run_code_health_simulation():
    """Run a comprehensive simulation of the CodeHealthEnv"""

    print(colored("\n" + "=" * 70, "cyan"))
    print(colored("CODE HEALTH ENVIRONMENT SIMULATION", "white", attrs=["bold"]))
    print(colored("=" * 70 + "\n", "cyan"))

    # Simulation state
    class SimulationState:
        def __init__(self):
            self.metrics = SystemMetrics(
                pass_rate=0.95,
                latency=0.15,
                alert_ratio=0.02,
                code_coverage=0.80,
                complexity=0.30,
            )
            self.degradation_rate = 0.01
            self.action_effects = {
                ActionType.RESTART.value: {
                    "pass_rate": 0.05,
                    "latency": -0.03,
                    "alert_ratio": -0.01,
                },
                ActionType.ROLLBACK.value: {
                    "pass_rate": 0.10,
                    "latency": -0.05,
                    "alert_ratio": -0.02,
                },
                ActionType.APPLY_PATCH.value: {
                    "pass_rate": 0.08,
                    "latency": -0.04,
                    "alert_ratio": -0.015,
                    "success_rate": 0.7,
                },
                ActionType.RUN_LINTER.value: {"complexity": -0.02},
                ActionType.RUN_TESTS.value: {"code_coverage": 0.01},
                ActionType.RUN_FORMATTER.value: {"complexity": -0.01},
            }

        def degrade_metrics(self):
            """Simulate natural degradation"""
            self.metrics.pass_rate = max(
                0, self.metrics.pass_rate - np.random.uniform(0.005, 0.015)
            )
            self.metrics.latency = min(1, self.metrics.latency + np.random.uniform(0.002, 0.008))
            self.metrics.alert_ratio = min(
                1, self.metrics.alert_ratio + np.random.uniform(0.001, 0.003)
            )
            self.metrics.complexity = min(
                1, self.metrics.complexity + np.random.uniform(0.001, 0.002)
            )

        def apply_action_effects(self, action_id: int) -> Dict[str, Any]:
            """Apply action effects to metrics"""
            action_name = ActionType(action_id).name if action_id < len(ActionType) else "unknown"
            result = {"action": action_name, "success": True, "timestamp": time.time()}

            if action_id in self.action_effects:
                effects = self.action_effects[action_id]

                # Check success rate for patch
                if action_id == ActionType.APPLY_PATCH.value:
                    if np.random.random() > effects.get("success_rate", 1.0):
                        # Patch failed
                        self.metrics.pass_rate = max(0, self.metrics.pass_rate - 0.1)
                        result["success"] = False
                        result["reason"] = "Patch failed to apply"
                        return result

                # Apply effects
                for metric, change in effects.items():
                    if hasattr(self.metrics, metric):
                        current = getattr(self.metrics, metric)
                        new_value = np.clip(current + change, 0, 1)
                        setattr(self.metrics, metric, new_value)

                result["effects_applied"] = {
                    k: v for k, v in effects.items() if not k.endswith("_rate")
                }

            return result

    sim_state = SimulationState()

    def get_metrics_simulated():
        """Get current simulation metrics"""
        sim_state.degrade_metrics()
        return sim_state.metrics

    def apply_action_simulated(action_id: int) -> Dict[str, Any]:
        """Apply action in simulation"""
        logger.info(
            f"Simulating action: {ActionType(action_id).name if action_id < len(ActionType) else 'unknown'}"
        )
        return sim_state.apply_action_effects(action_id)

    # Configure environment
    config = EnvironmentConfig(
        max_steps=30,
        unacceptable_threshold=0.5,
        critical_threshold=0.3,
        enable_auto_rollback=True,
        max_action_history=100,
    )

    # Create environment
    env = CodeHealthEnv(
        get_metrics=get_metrics_simulated,
        apply_action=apply_action_simulated,
        config=config,
    )

    # Run episode
    observation, info = env.reset()
    print(colored("\nInitial State:", "green", attrs=["bold"]))
    env.render(mode="ansi")

    done = False
    total_reward = 0
    step_count = 0

    print(colored("\n" + "=" * 70, "yellow"))
    print(colored("STARTING SIMULATION", "yellow", attrs=["bold"]))
    print(colored("=" * 70 + "\n", "yellow"))

    while not done and step_count < 20:
        # Simple policy
        if sim_state.metrics.pass_rate < 0.4:
            action = ActionType.ROLLBACK.value
        elif sim_state.metrics.pass_rate < 0.6:
            action = ActionType.RESTART.value
        elif sim_state.metrics.pass_rate < 0.8 and np.random.random() < 0.3:
            action = ActionType.APPLY_PATCH.value
        elif np.random.random() < 0.2:
            action = np.random.choice(
                [
                    ActionType.RUN_LINTER.value,
                    ActionType.RUN_TESTS.value,
                    ActionType.RUN_FORMATTER.value,
                ]
            )
        else:
            action = ActionType.NOOP.value

        observation, reward, done, info = env.step(action)
        total_reward += reward
        step_count += 1

        # Show state periodically
        if step_count % 5 == 0 or done:
            print(colored(f"\n--- Step {step_count} ---", "cyan"))
            env.render(mode="ansi")
            time.sleep(0.5)  # Pause for readability

    # Show final summary
    print(colored("\n" + "=" * 70, "green"))
    print(colored("SIMULATION COMPLETE", "green", attrs=["bold"]))
    print(colored("=" * 70 + "\n", "green"))

    summary = env.get_metrics_summary()
    print(colored("Metrics Summary:", "white", attrs=["bold"]))
    print(f"  Total Steps: {step_count}")
    print(f"  Total Reward: {total_reward:.2f}")
    print(f"  Final State: {dict(zip(config.observation_keys, observation.round(3)))}")

    if summary:
        print(colored("\n  Improvements:", "cyan"))
        for key, value in summary.get("improvement", {}).items():
            symbol = "↑" if value > 0 else "↓" if value < 0 else "→"
            color = "green" if value > 0 else "red" if value < 0 else "yellow"
            print(f"    {key}: {colored(f'{symbol} {value:+.3f}', color)}")

    # Clean up
    env.close()
    print(colored("\nEnvironment closed successfully.\n", "green"))


if __name__ == "__main__":
    # Check if running in sandboxed mode
    if os.environ.get("SANDBOXED_ENV", "") == "1":
        # Configure logging for sandboxed execution
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        run_code_health_simulation()
    else:
        # Launch in sandboxed subprocess
        env = os.environ.copy()
        env["SANDBOXED_ENV"] = "1"

        print(
            f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Launching sandboxed process..."
        )

        try:
            proc = subprocess.Popen(
                [sys.executable, __file__],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Stream output
            for line in proc.stdout:
                print(line, end="")

            proc.wait()

            if proc.returncode != 0:
                print(f"\n[ERROR] Process exited with code: {proc.returncode}")
                for line in proc.stderr:
                    print(line, end="")
        except Exception as e:
            print(f"[ERROR] Failed to launch sandboxed process: {e}")
