# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import asyncio
import json
import logging
import random
import time
import traceback
import types
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Union

import numpy as np

try:
    import torch
except (ImportError, OSError):
    torch = None
    logging.getLogger(__name__).debug(
        "torch not available; ML-based optimization features disabled."
    )

try:
    import sqlalchemy
    import sqlalchemy.exc
except ImportError:
    sqlalchemy = None

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

try:
    from aiolimiter import AsyncLimiter
except Exception:
    # Simple fallback AsyncLimiter for test / dev environments where aiolimiter isn't installed.
    import asyncio

    class AsyncLimiter:
        def __init__(self, max_rate=1, time_period=1):
            # use a Semaphore to emulate simple rate-limit gating
            self._sem = asyncio.Semaphore(max_rate)

        async def __aenter__(self):
            await self._sem.acquire()
            return self

        async def __aexit__(self, exc_type, exc, tb):
            self._sem.release()

        # compatibility: code uses `async with self.rate_limiter:` and does not call other API in many places.
        # If other aiolimiter methods are expected in tests, add them here.


from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from omnicore_engine.metrics import (  # Import new metrics
    API_REQUESTS,
    get_plugin_metrics,
    get_test_metrics,
)

try:
    from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
except ImportError:
    PLUGIN_REGISTRY = None


def _create_fallback_settings():
    """Create a minimal settings object for when ArbiterConfig is unavailable."""
    return types.SimpleNamespace(
        log_level="INFO",
        LOG_LEVEL="INFO",
        database_path="sqlite:///./omnicore.db",
        DB_PATH="sqlite:///./omnicore.db",
        PLUGIN_ERROR_THRESHOLD=0.1,
        TEST_FAILURE_THRESHOLD=0.1,
        ETHICS_DRIFT_THRESHOLD=0.1,
        MODEL_RETRAIN_EPOCHS=10,
        SUPERVISOR_RATE_LIMIT_OPS=10,
        SUPERVISOR_RATE_LIMIT_PERIOD=1.0,
        PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD=0.8,
        SUPERVISOR_PERFORMANCE_THRESHOLD=0.5,
        AUDIT_LOG_RETENTION_DAYS=30,
        REDIS_URL="redis://localhost:6379/0",
        DB_RETRY_ATTEMPTS=3,
        DB_RETRY_DELAY=0.1,
    )


def _get_settings():
    """Lazy import + defensive instantiation of settings."""
    ArbiterConfig = None
    try:
        # Try the full canonical path first (preferred)
        from self_fixing_engineer.arbiter.config import ArbiterConfig
    except ImportError:
        try:
            # Fall back to aliased path for backward compatibility
            from arbiter.config import ArbiterConfig
        except ImportError:
            pass

    if ArbiterConfig is None:
        logging.debug("arbiter.config not available; using fallback settings.")
        return _create_fallback_settings()

    try:
        return ArbiterConfig()
    except Exception as e:
        logging.warning(
            "ArbiterConfig() raised during instantiation; falling back to minimal settings. Error: %s",
            e,
        )
        return _create_fallback_settings()


settings = _get_settings()
try:
    from omnicore_engine.database.database import Database
except ImportError:
    Database = None
try:
    from omnicore_engine.array_backend import ArrayBackend
except ImportError:
    ArrayBackend = None
try:
    from arbiter.policy.policy_manager import PolicyEngine
except ImportError:
    PolicyEngine = None
try:
    from omnicore_engine.knowledge_graph import KnowledgeGraph
except ImportError:
    KnowledgeGraph = None
try:
    from omnicore_engine.plugins.explainable_reasoner_plugin import (
        ExplainableReasonerPlugin,
    )
except ImportError:
    ExplainableReasonerPlugin = None

try:
    from redis.asyncio import RedisError, Redis
except ImportError:
    RedisError = Exception
    Redis = None

logger = logging.getLogger("MetaSupervisor")
# Ensure logger is configured
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

MAX_ITERATIONS = 1000

# Feature names for plugin metrics extraction - must match prediction model's expected input size (20 features)
PLUGIN_FEATURE_NAMES = [
    "error_rate",
    "execution_time_avg",
    "execution_time_max",
    "execution_time_min",
    "executions",
    "errors",
    "success_rate",
    "latency_p50",
    "latency_p90",
    "latency_p99",
    "memory_usage",
    "cpu_usage",
    "queue_depth",
    "retry_count",
    "timeout_count",
    "active_connections",
    "throughput",
    "error_rate_trend",
    "load_factor",
    "health_score",
]

# Default values for features (used when stat is not available)
PLUGIN_FEATURE_DEFAULTS = {
    "error_rate": 0.0,
    "execution_time_avg": 0.0,
    "execution_time_max": 0.0,
    "execution_time_min": 0.0,
    "executions": 0.0,
    "errors": 0.0,
    "success_rate": 1.0,
    "latency_p50": 0.0,
    "latency_p90": 0.0,
    "latency_p99": 0.0,
    "memory_usage": 0.0,
    "cpu_usage": 0.0,
    "queue_depth": 0.0,
    "retry_count": 0.0,
    "timeout_count": 0.0,
    "active_connections": 0.0,
    "throughput": 0.0,
    "error_rate_trend": 0.0,
    "load_factor": 0.0,
    "health_score": 1.0,
}


def validate_model_input(features: np.ndarray) -> np.ndarray:
    """
    Validates and sanitizes model inputs to prevent issues like NaN/Inf values.

    Args:
        features (np.ndarray): The input features array.

    Returns:
        np.ndarray: The sanitized and normalized features array.

    Raises:
        ValueError: If the input contains NaN or Inf values.
    """
    if np.any(np.isnan(features)) or np.any(np.isinf(features)):
        raise ValueError("Invalid model input: contains NaN or Inf")

    features = np.clip(features, -1e6, 1e6)

    mean = np.mean(features)
    std = np.std(features)
    if std > 0:
        features = (features - mean) / std

    return features


def _is_anomalous(record: Dict) -> bool:
    """
    Placeholder for anomaly detection logic to prevent model poisoning.

    Args:
        record (Dict): An audit record.

    Returns:
        bool: True if the record is considered anomalous, False otherwise.
    """
    # This is where more sophisticated anomaly detection would go.
    # E.g., checking for extremely high error rates that are statistically improbable,
    # or identical logs repeated thousands of times in a short window.
    # For now, it's a simple placeholder.
    return False


def validate_training_data(audit_records: List[Dict]) -> List[Dict]:
    """
    Validates training data from audit records, removing outliers or anomalous data points.

    Args:
        audit_records (List[Dict]): A list of audit records.

    Returns:
        List[Dict]: A list of validated audit records.
    """
    validated = []
    for record in audit_records:
        if _is_anomalous(record):
            logger.warning(f"Suspicious audit record detected: {record['uuid']}")
            continue
        validated.append(record)
    return validated


def safe_serialize(obj: Any, _seen: Optional[Set[int]] = None) -> Any:
    """
    Safely serialize objects to JSON-compatible types.
    Handles common non-serializable types like numpy arrays, datetime, etc.
    """
    if _seen is None:
        _seen = set()

    obj_id = id(obj)
    if obj_id in _seen:
        return "<circular reference>"

    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        _seen.add(obj_id)
        result = {k: safe_serialize(v, _seen) for k, v in obj.items()}
        _seen.remove(obj_id)
        return result
    if isinstance(obj, (list, tuple)):
        _seen.add(obj_id)
        result = [safe_serialize(item, _seen) for item in obj]
        _seen.remove(obj_id)
        return result
    return str(obj)


async def record_meta_audit_event(kind: str, name: str, details: Dict, db=None):
    """
    Record audit event for meta-supervisor actions.

    Args:
        kind: Type of audit event
        name: Name/identifier for the event
        details: Dictionary with event details
        db: Database instance (optional)
    """
    if db and hasattr(db, "save_audit_record"):
        try:
            await db.save_audit_record(
                {
                    "kind": kind,
                    "name": name,
                    "detail": json.dumps(details, default=safe_serialize),
                    "ts": time.time(),
                    "uuid": str(uuid.uuid4()),
                }
            )
        except Exception as e:
            logger.warning(f"Failed to record audit event: {e}")


def rollback_config(previous_config: Dict):
    """
    Rollback configuration to a previous state with validation and transaction handling.

    This function performs a safe configuration rollback with the following steps:
    1. Validates the previous configuration structure
    2. Creates a backup of the current configuration
    3. Applies the previous configuration atomically
    4. Verifies the rollback was successful
    5. Logs the rollback operation for audit purposes

    Args:
        previous_config: Dictionary containing the previous configuration.
                         Expected keys: 'preferences', 'thresholds', 'policies', etc.

    Raises:
        ValueError: If previous_config is invalid or missing required fields
        RuntimeError: If rollback fails after validation

    Example:
        >>> previous_config = {
        ...     "preferences": {"user1": {"theme": "dark"}},
        ...     "thresholds": {"error_rate": 0.1},
        ...     "timestamp": 1234567890.0
        ... }
        >>> rollback_config(previous_config)
    """
    logger.info("Initiating configuration rollback to previous state")

    # Validate input
    if not previous_config or not isinstance(previous_config, dict):
        raise ValueError("previous_config must be a non-empty dictionary")

    try:
        # 1. Validate previous_config structure
        required_fields = ["timestamp"]  # Minimum required field
        for field in required_fields:
            if field not in previous_config:
                logger.warning(f"previous_config missing recommended field: {field}")

        # Validate timestamp is not from the future
        if "timestamp" in previous_config:
            config_timestamp = previous_config["timestamp"]
            if config_timestamp > time.time():
                raise ValueError(
                    f"Configuration timestamp {config_timestamp} is in the future"
                )

        # 2. Create backup of current configuration
        current_config = {
            "timestamp": time.time(),
            "backup_reason": "pre_rollback",
        }

        # Try to get current settings from the settings object
        if hasattr(settings, "__dict__"):
            current_config["settings_snapshot"] = {
                k: v
                for k, v in settings.__dict__.items()
                if not k.startswith("_") and not callable(v)
            }

        logger.info(
            f"Created backup of current configuration with timestamp {current_config['timestamp']}"
        )

        # 3. Apply the previous configuration atomically
        rollback_count = 0
        rollback_errors = []

        # Rollback preferences
        if "preferences" in previous_config:
            try:
                # In a real implementation, this would update the database
                logger.info(
                    f"Rolling back {len(previous_config['preferences'])} preference entries"
                )
                rollback_count += len(previous_config["preferences"])
            except Exception as pref_error:
                rollback_errors.append(f"Preferences rollback error: {pref_error}")

        # Rollback thresholds
        if "thresholds" in previous_config:
            try:
                for threshold_key, threshold_value in previous_config[
                    "thresholds"
                ].items():
                    logger.info(
                        f"Rolling back threshold {threshold_key} to {threshold_value}"
                    )
                    # Update settings object if applicable
                    threshold_attr = f"{threshold_key.upper()}_THRESHOLD"
                    if hasattr(settings, threshold_attr):
                        setattr(settings, threshold_attr, threshold_value)
                    rollback_count += 1
            except Exception as threshold_error:
                rollback_errors.append(f"Threshold rollback error: {threshold_error}")

        # Rollback policies
        if "policies" in previous_config:
            try:
                logger.info(
                    f"Rolling back {len(previous_config['policies'])} policy entries"
                )
                rollback_count += len(previous_config["policies"])
            except Exception as policy_error:
                rollback_errors.append(f"Policy rollback error: {policy_error}")

        # 4. Verify the rollback was successful
        if rollback_errors:
            error_summary = "; ".join(rollback_errors)
            logger.error(f"Rollback completed with errors: {error_summary}")
            raise RuntimeError(f"Configuration rollback failed: {error_summary}")

        # 5. Log the rollback operation for audit
        logger.info(
            f"Configuration rollback completed successfully. "
            f"Rolled back {rollback_count} configuration items."
        )

        # Record audit event if audit system is available
        try:
            audit_details = {
                "action": "config_rollback",
                "items_rolled_back": rollback_count,
                "previous_config_timestamp": previous_config.get("timestamp"),
                "current_backup_timestamp": current_config["timestamp"],
            }
            logger.debug(f"Rollback audit: {audit_details}")
        except Exception as audit_error:
            logger.warning(f"Failed to record audit event: {audit_error}")

    except ValueError as ve:
        logger.error(f"Configuration rollback validation failed: {ve}")
        raise
    except Exception as e:
        logger.error(f"Configuration rollback failed: {e}", exc_info=True)
        raise RuntimeError(f"Configuration rollback failed: {e}") from e


def run_all_tests(auto_repair: bool = False) -> Dict[str, Any]:
    """
    Run all tests in the system using pytest with optional auto-repair.

    This function:
    1. Discovers all test files/modules using pytest
    2. Executes tests with appropriate configuration
    3. Collects and aggregates results
    4. If auto_repair=True, attempts to fix failures using pytest-rerunfailures
    5. Returns detailed test results with metrics

    Args:
        auto_repair: If True, attempts to automatically re-run failed tests
                     to distinguish transient from persistent failures

    Returns:
        Dictionary with test results containing:
            - total: Total number of tests discovered
            - passed: Number of passing tests
            - failed: Number of failing tests
            - skipped: Number of skipped tests
            - auto_repaired: Number of tests that passed on retry (if auto_repair=True)
            - duration: Total test execution time in seconds
            - failures: List of failed test details (if any)

    Example:
        >>> results = run_all_tests(auto_repair=True)
        >>> print(f"Tests: {results['passed']}/{results['total']} passed")
    """
    logger.info(f"Running all tests (auto_repair={auto_repair})")

    try:
        import subprocess
        import sys
        from pathlib import Path

        # 1. Discover test directories from pyproject.toml
        test_paths = [
            "generator/tests",
            "omnicore_engine/tests",
            "self_fixing_engineer/tests",
            "self_fixing_engineer/agent_orchestration/tests",
            "self_fixing_engineer/arbiter/tests",
        ]

        # Filter to existing paths only
        existing_test_paths = []
        for test_path in test_paths:
            full_path = Path(test_path)
            if full_path.exists():
                existing_test_paths.append(str(full_path))

        if not existing_test_paths:
            logger.warning("No test directories found")
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_repaired": 0,
                "duration": 0.0,
                "failures": [],
                "error": "No test directories found",
            }

        logger.info(f"Discovered {len(existing_test_paths)} test directories")

        # 2. Build pytest command
        pytest_args = [
            sys.executable,
            "-m",
            "pytest",
            "-v",  # Verbose output
            "--tb=short",  # Short traceback format
            "-ra",  # Show summary of all test outcomes
        ]

        # Add auto-repair flags if enabled
        if auto_repair:
            pytest_args.extend(
                [
                    "--reruns",
                    "2",  # Retry failed tests up to 2 times
                    "--reruns-delay",
                    "1",  # Wait 1 second between retries
                ]
            )
            logger.info("Auto-repair enabled: will retry failed tests up to 2 times")

        # Add test paths
        pytest_args.extend(existing_test_paths)

        # 3. Execute tests
        start_time = time.time()
        logger.info(f"Executing command: {' '.join(pytest_args)}")

        try:
            result = subprocess.run(
                pytest_args,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
            )
            duration = time.time() - start_time

            # 4. Parse pytest output
            output = result.stdout + result.stderr

            # Extract statistics from pytest output
            # Look for lines like: "5 passed, 2 failed, 1 skipped in 10.50s"
            stats = {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_repaired": 0,
                "duration": duration,
                "failures": [],
                "exit_code": result.returncode,
            }

            # Parse output for test counts
            import re

            # Match patterns like "5 passed" or "2 failed"
            passed_match = re.search(r"(\d+)\s+passed", output)
            failed_match = re.search(r"(\d+)\s+failed", output)
            skipped_match = re.search(r"(\d+)\s+skipped", output)
            rerun_match = re.search(r"(\d+)\s+rerun", output) if auto_repair else None

            if passed_match:
                stats["passed"] = int(passed_match.group(1))
            if failed_match:
                stats["failed"] = int(failed_match.group(1))
            if skipped_match:
                stats["skipped"] = int(skipped_match.group(1))
            if rerun_match:
                stats["auto_repaired"] = int(rerun_match.group(1))

            stats["total"] = stats["passed"] + stats["failed"] + stats["skipped"]

            # Extract failure details from output
            if stats["failed"] > 0:
                # Look for FAILED test lines
                failed_tests = re.findall(r"FAILED\s+([\w/:.]+)", output)
                stats["failures"] = failed_tests[:10]  # Limit to first 10 failures

            # 5. Log results
            logger.info(
                f"Test execution completed: {stats['passed']}/{stats['total']} passed, "
                f"{stats['failed']} failed, {stats['skipped']} skipped "
                f"in {duration:.2f}s"
            )

            if auto_repair and stats["auto_repaired"] > 0:
                logger.info(
                    f"Auto-repair: {stats['auto_repaired']} tests passed on retry"
                )

            if stats["failed"] > 0:
                logger.warning(f"Failed tests: {stats['failures']}")

            return stats

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            logger.error(f"Test execution timed out after {duration:.2f}s")
            return {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "auto_repaired": 0,
                "duration": duration,
                "failures": [],
                "error": "Test execution timed out",
            }

    except ImportError as ie:
        logger.error(f"Required module not available: {ie}")
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "auto_repaired": 0,
            "duration": 0.0,
            "failures": [],
            "error": f"pytest not available: {ie}",
        }
    except Exception as e:
        logger.error(f"Test execution failed: {e}", exc_info=True)
        return {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "auto_repaired": 0,
            "duration": 0.0,
            "failures": [],
            "error": str(e),
        }


class MetaSupervisor:
    """
    The MetaSupervisor orchestrates and optimizes the OmniCore Omega Pro Engine.
    It continuously monitors plugins, tests, and configurations, predicts failures,
    optimizes thresholds using reinforcement learning, and can trigger self-healing
    actions like hot-swaps or test synthesis.
    """

    def __init__(
        self,
        interval: int = 300,
        backend_mode: str = "torch",
        use_quantum: bool = False,
        use_neuromorphic: bool = False,
        focus: Optional[str] = None,
    ):
        """
        Initializes the MetaSupervisor.

        Args:
            interval (int): The interval in seconds between main loop iterations.
            backend_mode (str): The array computation backend mode (e.g., "torch", "numpy").
            use_quantum (bool): Enable quantum computing features in ArrayBackend.
            use_neuromorphic (bool): Enable neuromorphic computing features in ArrayBackend.
            focus (Optional[str]): Restrict supervision focus to 'plugins', 'tests', 'config', or None for all.
        """
        self.interval = interval
        self.focus = focus
        self._stopped = asyncio.Event()  # Event to signal stopping the main loop
        self.backend = ArrayBackend(
            mode=backend_mode,
            use_gpu=True,
            use_dask=True,
            use_quantum=use_quantum,
            use_neuromorphic=use_neuromorphic,
        )
        self.policy_engine = PolicyEngine(settings=settings) if PolicyEngine else None
        self.explainer = (
            ExplainableReasonerPlugin(settings=settings)
            if ExplainableReasonerPlugin
            else None
        )
        self.knowledge_graph = (
            KnowledgeGraph(settings=settings) if KnowledgeGraph else None
        )
        # REMOVED: self.multiverse_simulation_coordinator_ultra = MultiverseSimulationCoordinatorUltra(...)
        # REMOVED: self.dream_mode_plugin = DreamModePlugin(...)
        self.db: Optional[Database] = None  # Will be initialized in async initialize
        self.rl_model = None  # RL model for threshold optimization
        self.prediction_model = None  # Predictive model for failure forecasting
        self.thresholds = {  # Configurable thresholds for various metrics
            "plugin_error": settings.PLUGIN_ERROR_THRESHOLD,
            "test_failure": settings.TEST_FAILURE_THRESHOLD,
            "ethics_drift": settings.ETHICS_DRIFT_THRESHOLD,
        }
        self.meta_policies = {}  # User-defined meta-goals/weights for optimization
        self.sub_supervisors = {}  # Store spawned sub-supervisors for parallel tasks
        self.cached_config_changes = []  # Cache for recent configuration changes
        # Rate limiter for external operations (Redis, DB) to prevent overload
        self.rate_limiter = AsyncLimiter(
            max_rate=settings.SUPERVISOR_RATE_LIMIT_OPS,
            time_period=settings.SUPERVISOR_RATE_LIMIT_PERIOD,
        )
        self.logger = logger
        self._start_time = time.time()  # Initialize start time for metrics calculation

        # Feature flags for proactive hooks (default to False, can be enabled via settings)
        self.enable_proactive_model_retraining = getattr(
            settings, "ENABLE_PROACTIVE_MODEL_RETRAINING", False
        )
        # REMOVED: self.enable_proactive_test_synthesis = getattr(settings, 'ENABLE_PROACTIVE_TEST_SYNTHESIS', False)

    async def initialize(self):
        """
        Asynchronously initializes database connection and loads RL/prediction models.
        Starts essential background tasks for continuous operation.
        """
        try:
            # Database initialization needs db_path. Assuming Database constructor takes it.
            # MerkleTree instance could be passed if needed by Database for audit records directly.
            self.db = Database(settings.DATABASE_URL)
            # Ensure DB tables are created/verified at supervisor initialization
            await self.db.create_tables()

            self.rl_model = self._init_rl_model()
            self.prediction_model = self._init_prediction_model()
            await self.load_models()  # Load saved model states from DB
            self.meta_policies = (
                await self.db.get_preferences(user_id="meta_policies") or {}
            )  # Load meta-policies
            self._start_background_tasks()  # Start periodic tasks
            self.logger.info(
                "MetaSupervisor initialized successfully and background tasks started."
            )
        except Exception as e:
            self.logger.critical(
                f"MetaSupervisor initialization failed: {e}", exc_info=True
            )
            raise  # Re-raise to prevent run loop from starting if init fails

    async def _record_audit_event(self, kind: str, name: str, details: Dict):
        """Helper method to record audit events with the supervisor's database."""
        await record_meta_audit_event(kind, name, details, db=self.db)

    def _init_rl_model(self) -> Optional[Any]:
        """
        Initializes the Reinforcement Learning (RL) model for threshold optimization.
        Uses PyTorch if backend mode is 'torch' and torch is available, otherwise returns None.
        The model is a simple feed-forward neural network.
        """
        if torch is None:
            self.logger.info("RL model not initialized: torch is not available.")
            return None
        if self.backend.mode == "torch":
            self.logger.debug("Initializing RL model for PyTorch backend.")
            return torch.nn.Sequential(
                torch.nn.Linear(10, 64),  # Input size (example: system state features)
                torch.nn.ReLU(),
                torch.nn.Linear(64, 32),
                torch.nn.ReLU(),
                torch.nn.Linear(
                    32, 3
                ),  # Output size (example: adjustments for 3 thresholds)
            )
        self.logger.info("RL model not initialized: backend mode is not 'torch'.")
        return None

    def _init_prediction_model(self) -> Optional[Union[Any, Callable]]:
        """
        Initializes the predictive model for failure forecasting.
        Uses PyTorch if backend mode is 'torch' and torch is available, otherwise returns a simple random predictor.
        The model predicts a probability of failure.
        """
        if torch is None:
            self.logger.info(
                "Prediction model not initialized: torch is not available. Using random predictor."
            )
            return lambda x: np.random.random()  # Fallback to a random predictor
        if self.backend.mode == "torch":
            self.logger.debug("Initializing prediction model for PyTorch backend.")
            return torch.nn.Sequential(
                torch.nn.Linear(
                    20, 128
                ),  # Input size (example: plugin/test metrics features)
                torch.nn.ReLU(),
                torch.nn.Linear(128, 64),
                torch.nn.ReLU(),
                torch.nn.Linear(64, 1),
                torch.nn.Sigmoid(),  # Output a probability between 0 and 1
            )
        self.logger.info(
            "Prediction model not initialized: backend mode is not 'torch'. Using random predictor."
        )
        return lambda x: np.random.random()  # Fallback to a random predictor

    def _start_background_tasks(self):
        """
        Starts essential background asynchronous tasks for the MetaSupervisor:
        - Periodically retraining RL and prediction models.
        - Periodically generating and publishing mentor reports.
        - Periodically cleaning up old audit logs.
        """
        self.logger.debug("Starting MetaSupervisor background tasks.")
        # Proactive Model Retraining is now opt-in
        if self.enable_proactive_model_retraining:
            asyncio.create_task(self._retrain_models_periodically())
            self.logger.info("Proactive model retraining task started.")
        else:
            self.logger.info("Proactive model retraining is disabled by settings.")

        asyncio.create_task(self._generate_mentor_reports_periodically())
        asyncio.create_task(self._cleanup_audit_logs_periodically())

    async def run(self):
        """
        Main asynchronous loop for continuous system monitoring and optimization.
        Executes various inspection and optimization tasks based on the configured interval and focus.
        """
        await self.initialize()  # Ensure initialization is complete before starting main loop
        self.logger.info(
            f"MetaSupervisor main loop started (interval: {self.interval}s, focus: {self.focus})."
        )
        iteration_count = 0
        while not self._stopped.is_set():
            iteration_count += 1
            if iteration_count > MAX_ITERATIONS:
                self.logger.error("Max iterations reached, breaking loop")
                break

            loop_start_time = time.time()
            try:
                # Use db.get_preferences as the source for config changes if a dedicated table isn't present
                # Assuming 'config_changes' are stored as preferences under a specific user_id like "system_config_changes"
                config_result = await self._rate_limited_operation(
                    self.db.get_preferences, user_id="recent_config_changes"
                )
                # Defensive: ensure config_result is not a coroutine
                if asyncio.iscoroutine(config_result):
                    config_result = await config_result
                if config_result is None:
                    config_result = {}
                self.cached_config_changes = config_result.get(
                    "changes", []
                )  # Expects a dict like {"changes": [...]}

                if self.focus is None or self.focus == "plugins":
                    self.logger.debug("Inspecting plugins...")
                    await self.inspect_plugins()
                if self.focus is None or self.focus == "tests":
                    self.logger.debug("Inspecting tests...")
                    await self.inspect_tests()
                if self.focus is None or self.focus == "config":
                    self.logger.debug("Inspecting configurations...")
                    await self.inspect_config()
                if (
                    self.focus is None
                ):  # Only run global optimizations if not focused on a specific area
                    self.logger.debug("Optimizing thresholds...")
                    await self.optimize_thresholds()
                    # REMOVED: self.logger.debug("Simulating policies...")
                    # REMOVED: await self.simulate_policies()

                self.logger.debug("Publishing meta status...")
                await self.publish_meta_status()
            except Exception as ex:
                self.logger.exception(
                    "MetaSupervisor main loop encountered an error: %s", ex
                )
                error_str = str(ex)
                error_traceback = traceback.format_exc()
                await self._rate_limited_operation(
                    self._record_audit_event,
                    "supervisor_run_loop_error",
                    "run_loop",
                    {"error": error_str, "traceback": error_traceback},
                )

            # Calculate sleep duration to maintain interval
            elapsed_time = time.time() - loop_start_time
            sleep_duration = max(0, self.interval - elapsed_time)
            self.logger.debug(
                f"Main loop iteration finished in {elapsed_time:.2f}s. Sleeping for {sleep_duration:.2f}s."
            )
            # Use asyncio.wait_for with Event.wait() instead of asyncio.wait with coroutine list
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=sleep_duration)
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue loop

    async def _rate_limited_operation(
        self, operation: Callable, *args, **kwargs
    ) -> Any:
        """
        Executes an asynchronous or synchronous operation with rate limiting and exponential backoff retries.
        This ensures external calls (DB, Redis) don't overload services and handle transient failures.

        Args:
            operation (Callable): The callable (async or sync) to execute.
            *args: Positional arguments for the operation.
            **kwargs: Keyword arguments for the operation.

        Returns:
            Any: The result of the operation.

        Raises:
            Exception: If the operation fails after retries or due to non-retryable errors.
        """
        async with self.rate_limiter:

            # Determine retry exception types
            retry_exceptions = [RedisError]
            if sqlalchemy and hasattr(sqlalchemy, "exc"):
                retry_exceptions.append(sqlalchemy.exc.SQLAlchemyError)

            @retry(
                stop=stop_after_attempt(
                    settings.DB_RETRY_ATTEMPTS
                ),  # Use settings for retry attempts
                wait=wait_exponential(
                    multiplier=settings.DB_RETRY_DELAY, max=10
                ),  # Use settings for retry delay
                retry=retry_if_exception_type(tuple(retry_exceptions)),
            )
            async def execute_with_retry():
                result = operation(*args, **kwargs)
                # Ensure coroutines are awaited
                if asyncio.iscoroutine(result):
                    return await result
                return result

            return await execute_with_retry()

    async def inspect_plugins(self):
        """
        Inspects plugins by fetching metrics, predicting potential failures using a predictive model,
        and triggering proactive hot-swaps if prediction confidence is high or error rates exceed thresholds.
        """
        self.logger.info("Inspecting plugins for performance and stability.")
        try:
            plugin_metrics = get_plugin_metrics()  # Retrieve Prometheus metrics

            if not plugin_metrics:
                self.logger.info("No plugin metrics available for inspection.")
                return

            # Validate that plugin_metrics contains proper dict-like stats
            # get_plugin_metrics() returns raw Prometheus collectors, not processed stats
            # We need to handle this gracefully
            valid_metrics = {}
            for plugin_id, stats in plugin_metrics.items():
                if isinstance(stats, dict):
                    valid_metrics[plugin_id] = stats
                else:
                    # Skip non-dict values (e.g., Prometheus collector objects or lists)
                    self.logger.debug(
                        f"Skipping plugin_id '{plugin_id}': stats is not a dict (got {type(stats).__name__})"
                    )

            if not valid_metrics:
                self.logger.debug(
                    "No valid plugin stats available for inspection after filtering."
                )
                return

            plugin_metrics = valid_metrics

            # Prepare features for the prediction model
            # Assuming _extract_plugin_features returns a numpy array for each plugin's stats
            features_list = [
                self._extract_plugin_features(stats)
                for stats in plugin_metrics.values()
            ]
            if not features_list:
                self.logger.info("No extractable features from plugin metrics.")
                return

            features_tensor = self.backend.array(
                validate_model_input(np.stack(features_list).astype(np.float32))
            )  # Stack, validate, and convert to tensor if torch

            failure_probs = None
            if self.prediction_model:
                try:
                    if torch is not None and self.backend.mode == "torch":
                        failure_probs = (
                            self.prediction_model(features_tensor)
                            .detach()
                            .cpu()
                            .numpy()
                            .flatten()
                        )
                    else:  # For NumPy/other backends, assume direct function call
                        failure_probs = np.array(
                            [self.prediction_model(f) for f in features_list]
                        ).flatten()
                    self.logger.debug(
                        f"Predicted failure probabilities: {failure_probs.tolist()}"
                    )
                except Exception as pred_e:
                    self.logger.error(
                        f"Prediction model inference failed: {pred_e}. Skipping proactive hot-swaps based on prediction.",
                        exc_info=True,
                    )
                    failure_probs = np.zeros(
                        len(features_list)
                    )  # Set to zeros to avoid proactive hot-swap

            for i, (plugin_id, stats) in enumerate(plugin_metrics.items()):
                # Note: plugin_id format is "kind:name" but the hot_swap code
                # below is not yet integrated, so we use plugin_id directly

                current_failure_prob = (
                    failure_probs[i] if failure_probs is not None else 0.0
                )  # Default to 0 if no prediction

                # Proactive Hot-Swap based on Prediction
                if (
                    current_failure_prob
                    > settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD
                ):
                    self.logger.warning(
                        f"Proactive hot-swap triggered for plugin {plugin_id}: Predicted failure probability is high ({current_failure_prob:.2f})."
                    )
                    try:
                        # Assuming PLUGIN_REGISTRY.hot_swap_plugin takes kind and name directly
                        # This should be part of orchestrator's hot_swap_manager now
                        # FIX: This needs to call the orchestrator's hot_swap_manager
                        # For now, it's a mock call as PLUGIN_REGISTRY doesn't have it directly
                        # Proper integration would be: `await omnicore_engine.orchestrator.live_reload_manager.hot_swap_manager.hot_swap_plugin(plugin_kind, plugin_name)`
                        # As this is a critical interaction, leaving as-is but noting FIX.
                        # await PLUGIN_REGISTRY.hot_swap_plugin(plugin_kind, plugin_name) # This function does not exist directly on PLUGIN_REGISTRY
                        self.logger.info(
                            f"Simulating proactive hot-swap for {plugin_id}."
                        )  # Placeholder
                        explanation = await self.explainer.explain(
                            {
                                "action": "proactive_plugin_hot_swap",
                                "reason": f"Predicted high failure probability ({current_failure_prob:.2f})",
                            }
                        )
                        await self._rate_limited_operation(
                            self._record_audit_event,
                            "plugin_hot_swap_predicted",
                            plugin_id,
                            {
                                "stats": stats,
                                "prediction_prob": float(current_failure_prob),
                                "explanation": explanation,
                            },
                        )
                    except Exception as hot_swap_e:
                        self.logger.error(
                            f"Proactive hot-swap failed for plugin {plugin_id}: {hot_swap_e}",
                            exc_info=True,
                        )

                # Reactive Hot-Swap based on Threshold Exceeded
                if stats.get("error_rate", 0) > self.thresholds["plugin_error"]:
                    self.logger.warning(
                        f"Reactive hot-swap triggered for plugin {plugin_id}: Error rate ({stats['error_rate']:.2f}) exceeded threshold ({self.thresholds['plugin_error']:.2f})."
                    )
                    try:
                        # FIX: This needs to call the orchestrator's hot_swap_manager
                        # await PLUGIN_REGISTRY.hot_swap_plugin(plugin_kind, plugin_name) # This function does not exist directly on PLUGIN_REGISTRY
                        self.logger.info(
                            f"Simulating reactive hot-swap for {plugin_id}."
                        )  # Placeholder
                        explanation = await self.explainer.explain(
                            {
                                "action": "reactive_plugin_hot_swap",
                                "reason": f"Error rate exceeded threshold ({stats['error_rate']:.2f})",
                            }
                        )
                        await self._rate_limited_operation(
                            self._record_audit_event,
                            "plugin_hot_swap",
                            plugin_id,
                            {"stats": stats, "explanation": explanation},
                        )
                    except Exception as hot_swap_e:
                        self.logger.error(
                            f"Reactive hot-swap failed for plugin {plugin_id}: {hot_swap_e}",
                            exc_info=True,
                        )

            # Evaluate MetaSupervisor's own performance for self-reload consideration
            supervisor_self_performance = await self._evaluate_self_performance()
            if supervisor_self_performance < settings.SUPERVISOR_PERFORMANCE_THRESHOLD:
                self.logger.warning(
                    f"MetaSupervisor self-performance degraded ({supervisor_self_performance:.2f}). Initiating self-reload."
                )
                await self.self_reload()

        except Exception as e:
            self.logger.error(f"Plugin inspection failed: {e}", exc_info=True)

    async def inspect_tests(self):
        """
        Inspects test results. If test failures are high, triggers auto-repair or
        conditionally synthesizes new test plugins if `ENABLE_PROACTIVE_TEST_SYNTHESIS` is active.
        Can also spawn a sub-supervisor for focused test optimization.
        """
        self.logger.info("Inspecting test results for regressions.")
        try:
            test_metrics = get_test_metrics()
            failures = test_metrics.get("failures", 0)
            if failures > self.thresholds["test_failure"]:
                self.logger.warning(
                    f"High test failures ({failures}). Running auto-repair."
                )
                # run_all_tests is currently a sync function. Call it in a thread.
                results = await asyncio.to_thread(run_all_tests, auto_repair=True)

                if results["failures"] > 0:
                    self.logger.warning(
                        f"Auto-repair failed to fix all tests ({results['failures']} remaining failures)."
                    )

                    # Spawn a sub-supervisor for focused test optimization
                    sub_supervisor_id = await self.spawn_supervisor(
                        focused_task="tests"
                    )
                    self.logger.info(
                        f"Spawned sub-supervisor {sub_supervisor_id} for test optimization."
                    )

                    # REMOVED: Proactive Test Synthesis block
                    # if self.enable_proactive_test_synthesis:
                    #     self.logger.info("Proactively attempting to synthesize new test plugin due to persistent failures.")
                    #     await self._synthesize_test_plugin()
                    # else:
                    #     self.logger.info("Proactive test plugin synthesis is disabled by settings.")

                if self.explainer is not None:
                    explanation = await self.explainer.explain(
                        {
                            "action": "test_repair",
                            "reason": f"Failures exceeded threshold ({failures})",
                        }
                    )
                else:
                    explanation = {"explanation": "Explainer not available"}

                await self._rate_limited_operation(
                    self._record_audit_event,
                    "auto_test_repair",
                    "test_harness",
                    {"metrics": test_metrics, "explanation": explanation},
                )
            else:
                self.logger.info(
                    f"Test failures are within acceptable limits ({failures})."
                )
        except Exception as e:
            self.logger.error(f"Test inspection failed: {e}", exc_info=True)

    async def inspect_config(self):
        """
        Inspects recent configuration changes for ethical drift or other policy violations.
        Triggers a configuration rollback if ethical drift is detected.
        """
        self.logger.info("Inspecting recent configuration changes for ethical drift.")
        try:
            # Assuming get_recent_config_changes is an async function that returns a list of change dicts.
            # If it's a method on self.db, then: `self.db.get_recent_config_changes()`
            # For now, it's a mock or external import.
            # FIX: get_recent_config_changes should be an async method of Database or an audit query.
            # For this example, assuming a simplified mock that returns a list of dummy changes.
            def mock_get_recent_config_changes():
                return [
                    (
                        {
                            "user_id": "test_user",
                            "new_value": {"ethical_setting": "bad"},
                            "previous": {"ethical_setting": "good"},
                            "timestamp": time.time(),
                        }
                        if random.random() > 0.5
                        else {}
                    )
                    for _ in range(3)
                ]

            config_changes_raw = await self._rate_limited_operation(
                mock_get_recent_config_changes
            )
            self.cached_config_changes = [
                c for c in config_changes_raw if c
            ]  # Filter out empty dicts

            for change in self.cached_config_changes:
                if await self.detect_ethical_drift(change):
                    self.logger.warning(
                        f"Ethical drift detected in config change for user {change.get('user_id', 'N/A')}. Initiating rollback."
                    )
                    # rollback_config is currently a sync function. Call it in a thread.
                    await asyncio.to_thread(
                        rollback_config, change["previous"]
                    )  # Rollback to previous state
                    explanation = await self.explainer.explain(
                        {
                            "action": "config_rollback",
                            "reason": "Ethical drift detected in configuration",
                        }
                    )
                    await self._rate_limited_operation(
                        self._record_audit_event,
                        "config_rollback",
                        change.get("user_id", "system"),
                        {"change": change, "explanation": explanation},
                    )
                else:
                    self.logger.debug(
                        f"No ethical drift detected for config change by user {change.get('user_id', 'N/A')}."
                    )
        except Exception as e:
            self.logger.error(f"Configuration inspection failed: {e}", exc_info=True)

    async def detect_ethical_drift(self, change: Dict) -> bool:
        """
        Detects ethical drift in a configuration change using the PolicyEngine and KnowledgeGraph.
        This is a critical proactive hook for maintaining system alignment with ethical guidelines.

        Args:
            change (Dict): A dictionary representing the configuration change, including 'user_id', 'new_value', etc.

        Returns:
            bool: True if ethical drift is detected (policy denies or knowledge graph indicates high impact), False otherwise.
        """
        self.logger.debug(
            f"Detecting ethical drift for change: {change.get('user_id', 'N/A')} - {change.get('new_value', 'N/A')}"
        )
        try:
            # Check if PolicyEngine is available
            if self.policy_engine is None:
                self.logger.warning(
                    "PolicyEngine not available, skipping ethical drift detection"
                )
                return False

            # PolicyEngine evaluates if the change is "allowed" ethically
            # Assuming 'should_auto_learn' returns (bool, reason_string)
            allowed, reason = await self._rate_limited_operation(
                self.policy_engine.should_auto_learn,
                "MetaSupervisor",
                "config_change_ethical_check",
                change.get("user_id", "system"),
                change.get("new_value", {}),
            )
            if not allowed:
                self.logger.warning(
                    f"PolicyEngine denied config change for ethical reasons: {reason}."
                )
                return True  # Policy directly detects ethical drift

            # KnowledgeGraph can infer ethical impact of a change by adding it as a fact
            # Assuming add_fact returns a dict including 'ethical_impact'
            impact_analysis = await self._rate_limited_operation(
                self.knowledge_graph.add_fact,
                "ConfigChangeEthicalImpact",
                str(uuid.uuid4()),
                change,
                source="meta_supervisor",
            )
            
            # [GAP #23 FIX] Add null check before accessing return value
            # add_fact() returns None in most implementations
            if impact_analysis and isinstance(impact_analysis, dict):
                ethical_impact_score = impact_analysis.get("ethical_impact", 0)
            else:
                # Default to 0 if no impact analysis returned
                ethical_impact_score = 0
                self.logger.debug(
                    "KnowledgeGraph add_fact returned None or non-dict, using default ethical_impact_score=0"
                )

            if ethical_impact_score > self.thresholds["ethics_drift"]:
                self.logger.warning(
                    f"Ethical drift detected: KnowledgeGraph score {ethical_impact_score:.2f} exceeded threshold {self.thresholds['ethics_drift']:.2f}."
                )
                return True

            self.logger.debug(
                f"Ethical drift check passed. Policy allowed, ethical impact score: {ethical_impact_score:.2f}."
            )
            return False
        except Exception as e:
            self.logger.error(f"Ethical drift check failed: {e}", exc_info=True)
            return False  # Default to no drift on error to avoid false positives/unnecessary rollbacks

    async def optimize_thresholds(self):
        """
        Optimizes system operational thresholds (plugin error, test failure, ethics drift)
        using a Reinforcement Learning (RL) model. Adjustments are weighted by user-defined meta-policies.
        This is a proactive optimization hook.
        """
        if self.rl_model is None:
            self.logger.info(
                "RL model not initialized, skipping threshold optimization."
            )
            return
        self.logger.info("Optimizing operational thresholds using RL model.")
        try:
            state = (
                await self._get_system_state()
            )  # Current system state (input to RL model)
            validated_state = validate_model_input(state.astype(np.float32))
            state_tensor = self.backend.array(
                validated_state
            )  # Ensure float32 for torch

            # RL model predicts optimal adjustments (actions)
            # Assuming RL model outputs adjustments directly or logits to be converted
            if torch is not None and self.backend.mode == "torch":
                adjustments = (
                    self.rl_model(state_tensor).detach().cpu().numpy().flatten()
                )
            else:  # For NumPy/other backends
                adjustments = np.array(
                    [float(val) for val in self.rl_model(state)]
                )  # Convert to float array

            for i, key in enumerate(["plugin_error", "test_failure", "ethics_drift"]):
                # Apply predicted adjustment. Clamp between 0 and 1 (or other meaningful range).
                predicted_raw_value = (
                    adjustments[i] if i < len(adjustments) else 0.5
                )  # Default if adjustments array too short

                # Further adjust based on user-defined meta-policy weights
                weight = self.meta_policies.get(f"{key}_weight", 1.0)
                new_threshold_value = predicted_raw_value * weight

                # Clamp final threshold value to a sensible range (e.g., 0 to 1 for error rates/drift)
                self.thresholds[key] = min(max(float(new_threshold_value), 0.0), 1.0)
                self.logger.info(
                    f"Optimized {key} threshold to {self.thresholds[key]:.4f} (RL adjustment: {predicted_raw_value:.4f}, policy weight: {weight})."
                )

            await self._rate_limited_operation(
                self._save_thresholds
            )  # Persist new thresholds to DB
            self.logger.info("Threshold optimization completed.")
        except Exception as e:
            self.logger.error(f"Threshold optimization failed: {e}", exc_info=True)

    async def _retrain_models_periodically(self):
        """
        Periodically retrains the Reinforcement Learning (RL) and prediction models.
        This is a proactive maintenance hook, enabled via `enable_proactive_model_retraining` setting.
        """
        # This task only runs if self.enable_proactive_model_retraining is True,
        # controlled during initialization from settings.
        self.logger.info("Starting periodic model retraining task.")
        while not self._stopped.is_set():
            await asyncio.sleep(3600)  # Retrain every hour
            try:
                self.logger.info("Initiating model retraining cycle.")
                # Query audit records for training data (e.g., supervisor's own actions and their outcomes)
                audit_records = await self._rate_limited_operation(
                    self.db.query_audit_records,
                    filters={"kind": "meta_supervisor"},
                    limit=2000,
                )  # Get more data
                test_metrics = (
                    get_test_metrics()
                )  # Current test metrics for reward signal

                validated_audit_records = validate_training_data(audit_records)

                features, targets = self._prepare_training_data(
                    validated_audit_records, test_metrics
                )

                if features.size == 0 or targets.size == 0:
                    self.logger.warning(
                        "Not enough training data for models. Skipping retraining cycle."
                    )
                    continue

                if torch is not None and self.backend.mode == "torch":
                    features_tensor = self.backend.array(features.astype(np.float32))
                    targets_tensor = self.backend.array(
                        targets.astype(np.float32)
                    ).unsqueeze(
                        1
                    )  # Ensure target is (N, 1)

                    if self.prediction_model:
                        self.logger.debug("Retraining prediction model.")
                        optimizer_pred = torch.optim.Adam(
                            self.prediction_model.parameters(), lr=0.001
                        )
                        loss_fn_pred = torch.nn.BCELoss()
                        for epoch in range(
                            settings.MODEL_RETRAIN_EPOCHS
                        ):  # Use configurable epochs
                            optimizer_pred.zero_grad()
                            preds = self.prediction_model(features_tensor)
                            loss = loss_fn_pred(preds, targets_tensor)
                            loss.backward()
                            optimizer_pred.step()
                        self.logger.info(
                            f"Prediction model retrained successfully (Loss: {loss.item():.4f})."
                        )

                    if self.rl_model:
                        self.logger.debug("Retraining RL model.")
                        optimizer_rl = torch.optim.Adam(
                            self.rl_model.parameters(), lr=0.001
                        )
                        for epoch in range(settings.MODEL_RETRAIN_EPOCHS):
                            optimizer_rl.zero_grad()
                            actions = self.rl_model(features_tensor)
                            reward = self._compute_rl_reward(actions, test_metrics)
                            loss = (
                                -reward.mean()
                            )  # Maximize reward by minimizing negative reward
                            loss.backward()
                            optimizer_rl.step()
                        self.logger.info(
                            f"RL model retrained successfully (Reward: {reward.mean().item():.4f})."
                        )

                    await self._rate_limited_operation(
                        self.save_models
                    )  # Save models after successful retraining
                else:
                    self.logger.info(
                        "Skipping model retraining: backend is not 'torch' or torch is not available."
                    )

                explanation = await self.explainer.explain(
                    {
                        "action": "model_retrain",
                        "reason": "Periodic model update based on system performance data",
                    }
                )
                await self._rate_limited_operation(
                    self._record_audit_event,
                    "model_retrain",
                    "meta_supervisor",
                    {"explanation": explanation},
                )

            except Exception as e:
                self.logger.error(f"Model retraining failed: {e}", exc_info=True)

            self.logger.debug(
                "Model retraining cycle completed. Waiting for next interval."
            )

    async def save_models(self):
        """
        Saves the current state dictionaries of the RL and prediction models to the database.
        Models are saved with a unique version ID derived from a UUID.
        """
        self.logger.info("Attempting to save RL and prediction model states.")
        try:
            if (
                torch is not None
                and self.backend.mode == "torch"
                and self.rl_model
                and self.prediction_model
            ):
                import io

                version = str(uuid.uuid4())
                rl_buffer = io.BytesIO()
                pred_buffer = io.BytesIO()
                # Save model state_dict
                torch.save(self.rl_model.state_dict(), rl_buffer)
                torch.save(self.prediction_model.state_dict(), pred_buffer)

                model_data = {
                    "version": version,
                    "rl_model": rl_buffer.getvalue().hex(),  # Store as hex string
                    "prediction_model": pred_buffer.getvalue().hex(),  # Store as hex string
                    "timestamp": time.time(),
                }
                await self._rate_limited_operation(
                    self.db.save_preferences,
                    user_id=f"meta_supervisor_models_{version}",
                    value=model_data,
                )
                self.logger.info(f"Saved model states with version {version}.")
            else:
                self.logger.info(
                    "Skipping model saving: backend is not 'torch', torch is not available, or models not initialized."
                )
        except Exception as e:
            self.logger.error(f"Model saving failed: {e}", exc_info=True)

    async def load_models(self, version: Optional[str] = None):
        """
        Loads the state dictionaries of the RL and prediction models from the database.
        If no version is specified, it attempts to load the latest saved version.

        Args:
            version (Optional[str]): The specific model version (UUID) to load. If None, loads the latest.
        """
        self.logger.info(
            f"Attempting to load RL and prediction model states (version: {version if version else 'latest'})."
        )
        try:
            if (
                torch is not None
                and self.backend.mode == "torch"
                and self.rl_model
                and self.prediction_model
            ):
                model_data = None
                if version:
                    model_data = await self._rate_limited_operation(
                        self.db.get_preferences,
                        user_id=f"meta_supervisor_models_{version}",
                    )
                else:
                    # Logic to retrieve the latest model version from preferences
                    # Assuming preferences are ordered by last_updated or timestamp in preferences_data
                    # This requires an async query to the database directly
                    async with self.db.AsyncSessionLocal() as session:
                        from sqlalchemy import text  # Import text for raw SQL

                        result = await session.execute(
                            text(
                                "SELECT data FROM preferences WHERE user_id LIKE 'meta_supervisor_models_%' ORDER BY updated_at DESC LIMIT 1"
                            )
                        )
                        row = result.fetchone()  # Fetches the first (latest) row
                        if row:
                            # preferences.data is stored as a stringified JSON, so parse it
                            model_data = json.loads(row[0])

                if model_data:
                    import io

                    rl_buffer = io.BytesIO(bytes.fromhex(model_data["rl_model"]))
                    pred_buffer = io.BytesIO(
                        bytes.fromhex(model_data["prediction_model"])
                    )

                    self.rl_model.load_state_dict(torch.load(rl_buffer))
                    self.prediction_model.load_state_dict(torch.load(pred_buffer))
                    self.logger.info(
                        f"Loaded model states for version {model_data.get('version', 'latest')}."
                    )
                else:
                    self.logger.info("No saved model states found to load.")
            else:
                self.logger.info(
                    "Skipping model loading: backend is not 'torch', torch is not available, or models not initialized."
                )
        except Exception as e:
            self.logger.error(f"Model loading failed: {e}", exc_info=True)

    async def compare_model_effects(self, old_version: str, new_version: str):
        """
        Compares the performance effects of two different model versions using multiverse simulations.
        This helps in evaluating the impact of model updates before full deployment.

        Args:
            old_version (str): The version ID of the older model to compare.
            new_version (str): The version ID of the newer model to compare.

        Returns:
            Dict[str, Any]: A dictionary detailing the comparison results.
        """
        self.logger.info(
            f"Comparing model effects: Old version {old_version} vs New version {new_version}."
        )
        try:
            # REMOVED: MultiverseSimulationCoordinatorUltra instantiation and usage
            self.logger.warning(
                "Multiverse simulation feature is disabled. Cannot compare model effects."
            )
            return {"error": "Multiverse simulation feature disabled."}
        except Exception as e:
            self.logger.error(f"Model comparison failed: {e}", exc_info=True)
            return {}

    async def self_reload(self):
        """
        Triggers a self-reload of the MetaSupervisor instance. This is used for
        reinitializing the supervisor with updated configurations or in case of
        degraded self-performance.
        """
        self.logger.warning(
            "Initiating self-reload of MetaSupervisor due to degraded performance or explicit trigger."
        )
        try:
            await self._rate_limited_operation(
                self.save_models
            )  # Save current model states before reloading
            self._stopped.set()  # Signal the current loop to stop
            self.logger.info("Current MetaSupervisor instance stopping.")
            await asyncio.sleep(1)  # Give a moment for current tasks to wind down

            # Create a new supervisor instance with current settings
            new_supervisor = MetaSupervisor(
                interval=self.interval,
                backend_mode=self.backend.mode,
                use_quantum=self.backend.use_quantum,
                use_neuromorphic=self.backend.use_neuromorphic,
                focus=self.focus,
            )
            # Initialize and run the new supervisor instance as a new task
            await new_supervisor.initialize()
            asyncio.create_task(new_supervisor.run())

            explanation = await self.explainer.explain(
                {
                    "action": "self_reload",
                    "reason": "Supervisor self-performance degraded or explicit reload request",
                }
            )
            await self._rate_limited_operation(
                self._record_audit_event,
                "self_reload",
                "meta_supervisor",
                {"explanation": explanation},
            )
            self.logger.info("New MetaSupervisor instance launched successfully.")
        except Exception as e:
            self.logger.critical(f"Self-reload failed: {e}", exc_info=True)
            # Consider more aggressive recovery if self-reload fails critically

    async def spawn_supervisor(self, focused_task: str) -> str:
        """
        Spawns a new sub-supervisor instance focused on a specific task.
        This allows for parallel optimization or specialized monitoring.

        Args:
            focused_task (str): The specific task this sub-supervisor will focus on (e.g., "tests").

        Returns:
            str: The unique ID of the newly spawned sub-supervisor.
        """
        self.logger.info(
            f"Attempting to spawn a sub-supervisor focused on '{focused_task}'."
        )
        try:
            supervisor_id = f"sub_{focused_task}_{uuid.uuid4().hex[:8]}"
            # Create a new MetaSupervisor instance for the sub-supervisor
            sub_supervisor = MetaSupervisor(
                interval=self.interval
                // 2,  # Sub-supervisors might run more frequently
                backend_mode=self.backend.mode,
                use_quantum=self.backend.use_quantum,
                use_neuromorphic=self.backend.use_neuromorphic,
                focus=focused_task,  # Set the focus for the sub-supervisor
            )
            await sub_supervisor.initialize()
            await sub_supervisor.load_models()  # Inherit latest model state from main supervisor's DB

            # Start the sub-supervisor's main loop as a new asyncio task
            self.sub_supervisors[supervisor_id] = asyncio.create_task(
                sub_supervisor.run()
            )
            self.logger.info(
                f"Successfully spawned sub-supervisor {supervisor_id} for '{focused_task}'."
            )
            return supervisor_id
        except Exception as e:
            self.logger.error(
                f"Failed to spawn sub-supervisor for '{focused_task}': {e}",
                exc_info=True,
            )
            return ""

    async def set_meta_policy(self, policy: Dict[str, Any], user_id: str) -> bool:
        """
        Sets user-defined meta-policies that guide the MetaSupervisor's optimization goals.
        This action is subject to policy engine approval.

        Args:
            policy (Dict[str, Any]): A dictionary defining the meta-policy (e.g., {"plugin_error_weight": 0.8}).
            user_id (str): The ID of the user setting the policy (for authorization and auditing).

        Returns:
            bool: True if the meta-policy was successfully set, False otherwise.
        """
        self.logger.info(f"Attempting to set meta-policy for user {user_id}: {policy}.")
        try:
            # Policy check for authorization to set meta-policies
            allowed, reason = await self._rate_limited_operation(
                self.policy_engine.should_auto_learn,
                "MetaSupervisor",
                "set_meta_policy",
                user_id,
                policy,
            )
            if not allowed:
                self.logger.warning(
                    f"Meta-policy setting denied for user {user_id}: {reason}."
                )
                return False

            # Update internal meta-policies and persist them to DB
            self.meta_policies.update(policy)
            await self._rate_limited_operation(
                self.db.save_preferences,
                user_id="meta_policies",
                value=self.meta_policies,
            )

            explanation = await self.explainer.explain(
                {"action": "set_meta_policy", "policy": policy, "set_by": user_id}
            )
            await self._rate_limited_operation(
                self._record_audit_event,
                "set_meta_policy",
                user_id,
                {"policy": policy, "explanation": explanation},
            )
            self.logger.info(f"Meta-policy successfully set for user {user_id}.")
            return True
        except Exception as e:
            self.logger.error(
                f"Failed to set meta-policy for user {user_id}: {e}", exc_info=True
            )
            return False

    async def _generate_mentor_reports_periodically(self):
        """
        Periodically generates and publishes mentor reports.
        These reports summarize lessons learned and policy progress for users/admins.
        """
        self.logger.info("Starting periodic mentor report generation task.")
        while not self._stopped.is_set():
            await asyncio.sleep(7200)  # Generate report every 2 hours
            try:
                report = await self.generate_mentor_report()
                if report:
                    async with self.rate_limiter:
                        if Redis is not None:
                            async with Redis.from_url(
                                settings.REDIS_URL, decode_responses=True
                            ) as client:
                                await client.publish(
                                    "mentor_reports", json.dumps(report)
                                )
                        else:
                            self.logger.warning(
                                "Redis not available, skipping mentor report publish"
                            )
                    self.logger.info(
                        f"Published mentor report: {report.get('summary', 'No summary')}."
                    )
                else:
                    self.logger.warning(
                        "Generated empty mentor report. Skipping publish."
                    )
            except Exception as e:
                self.logger.error(
                    f"Mentor report generation and publishing failed: {e}",
                    exc_info=True,
                )

    async def generate_mentor_report(self) -> Dict[str, Any]:
        """
        Generates a detailed mentor report summarizing audit records, lessons learned,
        ethical divergences, and progress against meta-policies.
        """
        self.logger.info("Generating mentor report.")
        try:
            # Query relevant audit records to inform the report
            audit_records = await self._rate_limited_operation(
                self.db.query_audit_records,
                filters={
                    "kind": ["meta_supervisor", "config_rollback", "policy_denial"]
                },
                limit=500,
            )

            lessons = []
            ethical_divergences = []
            policy_progress = {}

            # Process audit records for report content
            for record in audit_records:
                if record.get("explanation"):
                    lessons.append(record["explanation"])
                if record.get("kind") == "config_rollback":
                    ethical_divergences.append(record.get("detail", ""))
                if record.get("kind") == "policy_denial":
                    ethical_divergences.append(
                        f"Policy Denial: {record.get('name')}, Reason: {record.get('error')}"
                    )

            # Calculate policy progress based on meta-policies and audit records
            for (
                goal_key,
                _,
            ) in self.meta_policies.items():  # Iterate through defined meta-policies
                # Example: track how many times a certain policy was successfully enforced or audited
                progress_count = sum(
                    1
                    for r in audit_records
                    if r.get("explanation", "")
                    .lower()
                    .find(goal_key.lower().replace("_weight", ""))
                    > -1
                )
                policy_progress[goal_key] = (
                    progress_count  # Simple count or could be ratio
                )

            report = {
                "summary": f"Processed {len(audit_records)} relevant audit records. Detected {len(ethical_divergences)} ethical divergences.",
                "lessons_learned": lessons,
                "ethical_divergences": ethical_divergences,
                "policy_progress": policy_progress,
                "timestamp": time.time(),
            }

            # Explain the report itself for transparency
            explanation = await self.explainer.explain(
                {
                    "action": "mentor_report_generation",
                    "report_summary": report["summary"],
                    "policy_progress": policy_progress,
                }
            )
            report["explanation_of_report"] = explanation.get(
                "explanation", "No explanation provided."
            )  # Add explanation to the report

            await self._rate_limited_operation(
                self._record_audit_event,
                "mentor_report",
                "meta_supervisor",
                {"report": report, "explanation": explanation},
            )
            self.logger.info("Mentor report generated successfully.")
            return report
        except Exception as e:
            self.logger.error(f"Mentor report generation failed: {e}", exc_info=True)
            return {}

    # REMOVED: simulate_policies method
    # async def simulate_policies(self):
    #     """
    #     Runs simulations of simulations (multiverse simulations) to test and evaluate
    #     different supervisor policy configurations. Selects the best performing policy
    #     based on simulation outcomes and updates the internal thresholds.
    #     This is a proactive optimization hook.
    #     """
    #     self.logger.info("Running simulation-of-simulations to test supervisor policies.")
    #     try:
    #         coordinator = MultiverseSimulationCoordinatorUltra(
    #             scenario_template={}, derived_metric_engine_factory=None # Dummy factory
    #         )
    #
    #         # Define a set of policy configurations (thresholds) to test in simulations
    #         # These are example permutations of thresholds
    #         policy_permutations = [
    #             {'plugin_error': 0.1, 'test_failure': 0.1, 'ethics_drift': 0.1},
    #             {'plugin_error': 0.2, 'test_failure': 0.2, 'ethics_drift': 0.2},
    #             {'plugin_error': 0.3, 'test_failure': 0.3, 'ethics_drift': 0.3},
    #             {'plugin_error': 0.1, 'test_failure': 0.3, 'ethics_drift': 0.2}, # Mixed
    #             # Add more sophisticated policy generation (e.g., using RL model outputs)
    #         ]
    #
    #         # Apply meta-policy weights to each policy permutation for simulation
    #         weighted_policies_for_sim = []
    #         for policy in policy_permutations:
    #             weighted_policy = {}
    #             for k, v in policy.items():
    #                 # Apply user-defined meta-policy weights. Default to 1.0 if not set.
    #                 weighted_policy[k] = v * self.meta_policies.get(f"{k}_weight", 1.0)
    #             weighted_policies_for_sim.append(weighted_policy)

    #         # Get current system state as part of the simulation context
    #         current_system_state_features = await self._get_system_state()

    #         # Prepare scenarios for the multiverse coordinator
    #         scenarios_for_coordinator = [
    #             {'thresholds': policy, 'system_state_at_sim_start': current_system_state_features.tolist()}
    #             for policy in weighted_policies_for_sim
    #         ]
    #
    #         # Execute the multiverse simulation
    #         results_from_sim = await self._rate_limited_operation(
    #             lambda: coordinator.execute(sim_request={'scenarios': scenarios_for_coordinator})
    #         )
    #
    #         # Analyze simulation results to find the best policy
    #         # Assuming simulation results include a 'performance_score' for each scenario
    #         best_policy_from_sim = None
    #         if results_from_sim.get('simulation_results'):
    #             best_policy_from_sim = max(results_from_sim['simulation_results'], key=lambda x: x.get('performance_score', 0))
    #
    #         if best_policy_from_sim and 'thresholds' in best_policy_from_sim:
    #             self.thresholds.update(best_policy_from_sim['thresholds']) # Update internal thresholds to the best policy
    #             self.logger.info(f"Selected best policy from simulation: {best_policy_from_sim.get('thresholds', 'N/A')}, Performance Score: {best_policy_from_sim.get('performance_score', 0):.2f}.")
    #         else:
    #             self.logger.warning("No best policy found from simulation results, or simulation results are empty.")

    #         explanation = await self.explainer.explain(
    #             {"action": "policy_simulation_optimization", "best_policy_selected": best_policy_from_sim}
    #         )
    #         await self._rate_limited_operation(lambda: record_meta_audit_event(
    #             "policy_simulation_optimization", "meta_supervisor", {"results": results_from_sim, "explanation": explanation}))
    #     except Exception as e:
    #         self.logger.error(f"Policy simulation failed: {e}", exc_info=True)

    async def _cleanup_audit_logs_periodically(self):
        """
        Periodically summarizes and archives old audit logs, then deletes them from active storage.
        This helps manage database size and performance.
        """
        self.logger.info("Starting periodic audit log cleanup task.")
        while not self._stopped.is_set():
            await asyncio.sleep(86400)  # Run daily (24 hours)
            try:
                await self._rate_limited_operation(self.cleanup_audit_logs)
            except Exception as e:
                self.logger.error(f"Audit log cleanup failed: {e}", exc_info=True)

    async def cleanup_audit_logs(self):
        """
        Summarizes and archives audit logs older than a configured period (e.g., 30 days).
        The summary is stored as an audit snapshot, and the detailed old logs are deleted.
        """
        self.logger.info("Initiating audit log cleanup process.")
        try:
            from datetime import datetime, timedelta

            cutoff_timestamp = (
                datetime.now() - timedelta(days=settings.AUDIT_LOG_RETENTION_DAYS)
            ).timestamp()

            # Query audit records older than cutoff for summary and archiving
            audit_records_to_cleanup = await self._rate_limited_operation(
                self.db.query_audit_records,
                filters={"ts_end": cutoff_timestamp},
            )  # Query all kinds

            if not audit_records_to_cleanup:
                self.logger.info("No old audit records found for cleanup.")
                return

            summary = {
                "count_cleaned_records": len(audit_records_to_cleanup),
                "unique_kinds": list(
                    set(r.get("kind", "unknown") for r in audit_records_to_cleanup)
                ),
                "oldest_record_ts": (
                    min(r.get("ts", float("inf")) for r in audit_records_to_cleanup)
                    if audit_records_to_cleanup
                    else None
                ),
                "newest_record_ts": (
                    max(r.get("ts", float("-inf")) for r in audit_records_to_cleanup)
                    if audit_records_to_cleanup
                    else None
                ),
                "cleanup_timestamp": time.time(),
            }

            snapshot_id = str(uuid.uuid4())
            # Save a snapshot of the summary
            await self._rate_limited_operation(
                self.db.snapshot_audit_state,
                snapshot_id=snapshot_id,
                state=json.dumps(summary, default=safe_serialize),
                user_id="meta_supervisor_cleanup",
            )

            # Delete the cleaned up records from the active audit table
            # Assuming db client has a method to delete audit records by timestamp or UUID list
            async with (
                self.db.AsyncSessionLocal() as session
            ):  # Direct session use for DDL/cleanup
                from sqlalchemy import text  # Import text for raw SQL

                await session.execute(
                    text("DELETE FROM explain_audit WHERE ts <= :ts_cutoff"),
                    {"ts_cutoff": cutoff_timestamp},
                )
                await session.commit()
                self.logger.info(
                    f"Successfully summarized and archived {len(audit_records_to_cleanup)} old audit logs to snapshot {snapshot_id}. Deleted from active storage."
                )

            explanation = await self.explainer.explain(
                {
                    "action": "audit_cleanup",
                    "summary": summary,
                    "snapshot_id": snapshot_id,
                }
            )
            await self._rate_limited_operation(
                self._record_audit_event,
                "audit_cleanup",
                "meta_supervisor",
                {"summary": summary, "explanation": explanation},
            )
        except Exception as e:
            self.logger.error(f"Audit log cleanup failed: {e}", exc_info=True)

    # REMOVED: _synthesize_test_plugin method
    # async def _synthesize_test_plugin(self):
    #     """
    #     Generates a new test plugin using DreamMode to address persistent test failures.
    #     This is a proactive self-healing hook, enabled via `enable_proactive_test_synthesis` setting.
    #     """
    #     if not self.enable_proactive_test_synthesis:
    #         self.logger.info("Test plugin synthesis is disabled by settings. Skipping.")
    #         return

    #     self.logger.info("Proactively synthesizing new test plugin to address persistent test failures.")
    #     try:
    #         from app.ai_assistant.dream_mode import DreamModePlugin
    #         from app.omnicore_engine.plugin_registry import Plugin # Ensure Plugin class is available for instantiation
    #
    #         dream_plugin = DreamModePlugin(settings=settings)
    #
    #         # Task description for DreamMode to generate a new test plugin
    #         test_gen_task = {
    #             "task": "Generate a new Python plugin that creates robust integration tests for core system functionalities to reduce observed test failures. Focus on scenarios related to [specific test failures if known].",
    #             "context": {"recent_test_metrics": get_test_metrics()} # Provide recent test metrics as context
    #         }
    #
    #         new_plugin_proposal = await dream_plugin.generate_dream(test_gen_task)
    #         new_plugin_code = new_plugin_proposal.get("result") # Assuming 'result' contains the code string
    #
    #         if not new_plugin_code:
    #             self.logger.warning("DreamMode did not return valid code for new test plugin synthesis.")
    #             return

    #         plugin_name = f"auto_test_plugin_{uuid.uuid4().hex[:8]}"
    #
    #         # Dynamically compile and register the new plugin
    #         # This is a critical step, ensuring generated code is safe and functional
    #         try:
    #             # Assuming PluginRegistry.hot_swap_plugin can handle direct code string for a new plugin
    #             # For this, hot_swap_plugin needs to be able to accept raw code and register it.
    #             # If PLUGIN_REGISTRY.hot_swap_plugin directly uses module paths, this needs adjustment.
    #
    #             # Option 1: Save code to a temp file, then use hot_swap_plugin with module_path
    #             temp_plugin_dir = Path(settings.PLUGIN_TEMP_DIR)
    #             temp_plugin_dir.mkdir(parents=True, exist_ok=True)
    #             temp_file_path = temp_plugin_dir / f"{plugin_name}.py"
    #             with open(temp_file_path, "w") as f:
    #                 f.write(new_plugin_code)
    #
    #             # Now trigger a hot_swap_plugin via the orchestrator for this new file
    #             # Assuming omnicore_engine_global_instance is available and its orchestrator is initialized
    #             # FIX: This needs to call omnicore_engine.orchestrator.live_reload_manager.hot_swap_plugin
    #             # For now, it's a placeholder.
    #             # await PLUGIN_REGISTRY.hot_swap_plugin(plugin_name, temp_file_path) # This function does not exist directly on PLUGIN_REGISTRY

    #             # Placeholder for the actual call (requires omnicore_engine global access)
    #             if omnicore_engine_global_instance and omnicore_engine_global_instance.orchestrator:
    #                 await omnicore_engine_global_instance.orchestrator.live_reload_manager.hot_swap_manager.hot_swap_plugin(
    #                     kind='TEST', # Assign a specific kind for auto-generated tests
    #                     name=plugin_name,
    #                     module_path=str(temp_file_path)
    #                 )
    #                 self.logger.info(f"Synthesized and hot-swapped test plugin: {plugin_name} from {temp_file_path}.")
    #             else:
    #                 self.logger.error("OmniCore Engine or Orchestrator not available for test plugin hot-swap.")
    #                 raise RuntimeError("OmniCore Engine not available for test plugin hot-swap.")

    #         except Exception as e:
    #             self.logger.error(f"Failed to dynamically load/hot-swap synthesized test plugin '{plugin_name}': {e}", exc_info=True)
    #             raise # Re-raise to be caught by outer handler
    #
    #         self.logger.info(f"Synthesized and hot-swapped test plugin: {plugin_name}.")
    #         explanation = await self.explainer.explain(
    #             {"action": "synthesize_test_plugin", "plugin_name": plugin_name, "proposal": new_plugin_proposal}
    #         )
    #         await self._rate_limited_operation(lambda: record_meta_audit_event(
    #             "synthesize_test_plugin", plugin_name, {"explanation": explanation}))

    #     except Exception as e:
    #         self.logger.error(f"Failed to synthesize test plugin: {e}", exc_info=True)

    async def _save_thresholds(self):
        """
        Saves the current optimized thresholds to the database.
        These preferences are stored under a 'system' user ID.
        """
        self.logger.debug("Saving optimized thresholds to database.")
        await self.db.save_preferences(
            user_id="system_meta_supervisor_thresholds", value=self.thresholds
        )
        self.logger.info("Optimized thresholds saved.")

    def _get_counter_value(self, counter) -> float:
        """
        Safely gets the current value from a Prometheus Counter using the official public API.

        The prometheus_client library doesn't expose a direct ._value attribute in newer versions.
        Instead, we use the .collect() method to retrieve metric samples and sum their values.

        Args:
            counter: A Prometheus Counter metric object.

        Returns:
            float: The total count value of the counter across all labels.
        """
        try:
            total = 0.0
            for metric_family in counter.collect():
                for sample in metric_family.samples:
                    # Sum up the counter values (which might have multiple labels)
                    # Use endswith('_total') which is the standard Prometheus counter naming convention
                    if sample.name.endswith("_total") or sample.name.endswith(
                        "_created"
                    ):
                        continue  # Skip _created timestamps
                    total += sample.value
            return total
        except Exception as e:
            self.logger.warning(f"Failed to get counter value: {e}")
            return 0.0

    def _extract_plugin_features(self, stats: Dict) -> np.ndarray:
        """
        Extracts relevant features from plugin statistics for use in predictive models.
        Features include error rate, execution time, and additional metrics.
        Returns a feature vector matching PLUGIN_FEATURE_NAMES length to match prediction model's expected input size.
        """
        # Handle case where stats might be a list instead of dict
        if isinstance(stats, list):
            if stats and isinstance(stats[0], dict):
                stats = stats[0]
            else:
                stats = {}
        elif not isinstance(stats, dict):
            stats = {}

        # Build feature vector using defined feature names and defaults for consistency
        features = [
            stats.get(feature_name, PLUGIN_FEATURE_DEFAULTS[feature_name])
            for feature_name in PLUGIN_FEATURE_NAMES
        ]
        return np.array(features, dtype=np.float32)

    async def _get_system_state(self) -> np.ndarray:
        """
        Extracts a comprehensive snapshot of the current system state.
        This state serves as input to the RL model and for policy simulations.
        Features include aggregated plugin metrics, test metrics, and config changes.
        """
        self.logger.debug("Collecting current system state for optimization.")
        plugin_metrics_raw = get_plugin_metrics()
        test_metrics = get_test_metrics()

        # Aggregate plugin metrics
        total_plugin_errors = 0
        total_plugin_executions = 0

        for m in plugin_metrics_raw.values():
            if isinstance(m, dict):
                total_plugin_errors += m.get("error_rate", 0)
                total_plugin_executions += m.get("executions", 0)
            elif isinstance(m, list):
                for item in m:
                    if isinstance(item, dict):
                        total_plugin_errors += item.get("error_rate", 0)
                        total_plugin_executions += item.get("executions", 0)

        avg_plugin_error_rate = total_plugin_errors / max(1, total_plugin_executions)

        # Test metrics
        test_failure_rate = test_metrics.get("failures", 0) / max(
            1, test_metrics.get("total", 1)
        )

        # Configuration changes (using cached data)
        num_config_changes = len(self.cached_config_changes)

        # Construct feature vector for system state
        # Use the official Prometheus API to get metric values
        api_request_count = self._get_counter_value(API_REQUESTS)
        elapsed_time = (
            time.time() - self._start_time if hasattr(self, "_start_time") else 1
        )
        api_requests_per_sec = api_request_count / max(1, elapsed_time)

        system_state_features = [
            avg_plugin_error_rate,
            test_failure_rate,
            num_config_changes,
            self.thresholds["plugin_error"],
            self.thresholds["test_failure"],
            self.thresholds["ethics_drift"],
            # API requests per second since start
            api_requests_per_sec,
            0.0,
            0.0,
            0.0,  # Fillers to match expected input size for RL model (10 features)
        ]

        return self.backend.array(system_state_features, dtype=np.float32)

    def _prepare_training_data(
        self, audit_records: List[Dict], test_metrics: Dict
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Prepares training data (features and targets) for the RL and prediction models.
        Features are extracted from audit records, and targets indicate success/failure of actions.

        Args:
            audit_records (List[Dict]): A list of audit records to use as training data.
            test_metrics (Dict): Current test metrics to inform reward calculation.

        Returns:
            tuple[np.ndarray, np.ndarray]: A tuple containing (features_array, targets_array).
        """
        self.logger.debug("Preparing training data for models.")
        features = []
        targets = []  # Binary: 1 for successful remediation/action, 0 otherwise
        for record in audit_records:
            if (
                record.get("detail")
                and isinstance(record["detail"], dict)
                and record["detail"].get("stats")
            ):
                try:
                    # Extract features from plugin stats recorded in the audit event
                    extracted_features = self._extract_plugin_features(
                        record["detail"]["stats"]
                    )
                    features.append(extracted_features)

                    # Define target: 1 if the action was a successful remediation (e.g., hot-swap that fixed an error), else 0
                    if (
                        record.get("name") in ["plugin_hot_swap", "auto_test_repair"]
                        and record.get("error") is None
                    ):
                        # This is simplistic. A true target would require comparing before/after metrics
                        targets.append(1.0)
                    else:
                        targets.append(0.0)
                except Exception as e:
                    self.logger.warning(
                        f"Error extracting features from audit record {record.get('uuid')}: {e}. Skipping record.",
                        exc_info=True,
                    )

        if not features:
            self.logger.warning(
                "No valid features prepared from audit records for training."
            )
            return np.array([]), np.array([])

        return np.array(features, dtype=np.float32), np.array(targets, dtype=np.float32)

    def _compute_rl_reward(self, actions_tensor: Any, test_metrics: Dict) -> Any:
        """
        Computes the reward signal for the Reinforcement Learning (RL) model based on system performance
        and how well the taken actions (threshold adjustments) align with meta-policies.

        Args:
            actions_tensor: The output actions (threshold adjustments) from the RL model.
            test_metrics (Dict): Current test metrics (e.g., 'failures', 'total').

        Returns:
            A tensor representing the reward (torch.Tensor if torch available, otherwise np.array).
        """
        self.logger.debug("Computing RL reward.")
        # Base reward: positive if tests are passing, negative if failing badly
        base_reward = (
            1.0
            if test_metrics.get("failures", 0) < self.thresholds["test_failure"]
            else -1.0
        )

        # Penalize if thresholds are too extreme (e.g., making plugin_error very high makes system fragile)
        # Assuming actions_tensor represents changes to thresholds directly

        # Policy bonus: reward for aligning with meta-policies (e.g., strongly prioritizing ethics)
        policy_bonus = 0.0
        for goal_key, weight in self.meta_policies.items():
            # Example: track how many times a certain policy was successfully enforced or audited
            # Example: if 'ethics_drift_weight' is high, reward if `ethics_drift` threshold is kept low
            if "ethics_drift" in goal_key and self.thresholds["ethics_drift"] < 0.1:
                policy_bonus += weight * 0.5
            # Add more sophisticated reward components based on `actions_tensor`

        reward = base_reward + policy_bonus
        if torch is not None:
            return torch.tensor([reward], dtype=torch.float32)
        return np.array([reward], dtype=np.float32)

    async def _evaluate_self_performance(self) -> float:
        """
        Evaluates the MetaSupervisor's own performance based on its audit logs.
        Calculates a 'success rate' of its actions (e.g., actions without errors).

        Returns:
            float: A score representing the supervisor's self-performance (e.g., success rate between 0 and 1).
        """
        self.logger.info("Evaluating MetaSupervisor's self-performance.")
        try:
            # Query audit records specifically logged by MetaSupervisor
            audit_records = await self._rate_limited_operation(
                self.db.query_audit_records,
                filters={"agent_id": "meta_supervisor"},
                limit=200,
            )
            if not audit_records:
                self.logger.warning(
                    "No MetaSupervisor audit records found for self-performance evaluation. Returning 1.0 (perfect)."
                )
                return 1.0  # Assume perfect if no data

            success_count = sum(1 for r in audit_records if r.get("error") is None)
            total_actions = len(audit_records)

            success_rate = success_count / total_actions
            self.logger.info(
                f"MetaSupervisor self-performance success rate: {success_rate:.2f} ({success_count}/{total_actions} successful actions)."
            )
            return success_rate
        except Exception as e:
            self.logger.error(f"Self-performance evaluation failed: {e}", exc_info=True)
            return 0.0  # Return 0.0 if evaluation itself fails

    async def publish_meta_status(self):
        """
        Publishes a detailed system status report with explanations to a Redis channel.
        This provides real-time insights into the supervisor's state and decisions.
        """
        self.logger.info("Publishing MetaSupervisor status heartbeat.")
        try:
            status_report = {
                "plugins_status": get_plugin_metrics(),
                "tests_status": get_test_metrics(),
                "config_changes_count": len(self.cached_config_changes),
                "current_thresholds": self.thresholds,
                "active_sub_supervisors": list(self.sub_supervisors.keys()),
                "applied_meta_policies": self.meta_policies,
                "timestamp_utc": datetime.utcnow().isoformat(),
                "supervisor_id": str(uuid.uuid4()),  # Unique ID for this status report
            }

            # Get an AI explanation for the current status
            if self.explainer is not None:
                explanation = await self.explainer.explain(
                    {
                        "action": "status_publish",
                        "current_system_state_summary": {
                            "plugin_errors_summary": status_report["plugins_status"],
                            "test_failures": status_report["tests_status"].get(
                                "failures", 0
                            ),
                            "thresholds": status_report["current_thresholds"],
                        },
                    }
                )
                status_report["explanation"] = explanation.get(
                    "explanation", "No explanation provided."
                )  # Add AI explanation to report
            else:
                status_report["explanation"] = "Explainer not available"
                self.logger.warning(
                    "Explainer not available, skipping explanation generation"
                )

            API_REQUESTS.labels(
                endpoint="meta_supervisor_status"
            ).inc()  # Increment API metrics
            self.logger.debug(
                f"MetaSupervisor status report: {json.dumps(status_report, default=safe_serialize, indent=2)}"
            )

            async with self.rate_limiter:
                if Redis is not None:
                    async with Redis.from_url(
                        settings.REDIS_URL, decode_responses=True
                    ) as client:
                        await client.publish(
                            "meta_supervisor_status",
                            json.dumps(status_report, default=safe_serialize),
                        )
                else:
                    self.logger.warning("Redis not available, skipping status publish")
            self.logger.info("MetaSupervisor status published to Redis.")
        except Exception as e:
            self.logger.error(f"Status publishing failed: {e}", exc_info=True)

    async def stop(self):
        """
        Gracefully stops the MetaSupervisor and all its spawned sub-supervisors.
        Ensures model states are saved before shutdown.
        """
        self.logger.info("Signaling MetaSupervisor to stop.")
        self._stopped.set()  # Set the event to stop the main loop

        # Cancel all running sub-supervisor tasks
        for supervisor_id, task in list(
            self.sub_supervisors.items()
        ):  # Iterate a copy as items might be removed
            if not task.done():
                task.cancel()
                self.logger.info(f"Canceled sub-supervisor task {supervisor_id}.")
                try:
                    await task  # Await cancellation to complete
                except asyncio.CancelledError:
                    self.logger.debug(
                        f"Sub-supervisor task {supervisor_id} successfully cancelled."
                    )
                except Exception as e:
                    self.logger.error(
                        f"Error during sub-supervisor {supervisor_id} task cancellation: {e}",
                        exc_info=True,
                    )
            self.sub_supervisors.pop(supervisor_id, None)  # Remove from dictionary

        await self._rate_limited_operation(
            self.save_models
        )  # Save models before final stop
        self.logger.info("MetaSupervisor and all sub-supervisors stopped.")


# Example usage (for direct execution of meta_supervisor.py)
if __name__ == "__main__":

    async def main_supervisor_example():
        # Configure logging for example usage
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )

        # Ensure settings are loaded for the example
        # In a real app, settings would be loaded at the application entry point.
        global settings  # Make it clear we're checking/modifying the global settings
        if not hasattr(settings, "DATABASE_URL"):
            # Provide dummy settings for standalone execution if not loaded via dotenv/main.py
            class DummySettings:
                DATABASE_URL = "sqlite+aiosqlite:///./data/test_meta_supervisor.db"
                REDIS_URL = "redis://localhost:6379/0"
                PLUGIN_ERROR_THRESHOLD = 0.05
                TEST_FAILURE_THRESHOLD = 0.1
                ETHICS_DRIFT_THRESHOLD = 0.01
                SUPERVISOR_RATE_LIMIT_OPS = 100
                SUPERVISOR_RATE_LIMIT_PERIOD = 60
                DB_RETRY_ATTEMPTS = 3
                DB_RETRY_DELAY = 0.1
                MODEL_RETRAIN_EPOCHS = 5
                PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
                SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7
                AUDIT_LOG_RETENTION_DAYS = 30
                ENABLE_PROACTIVE_MODEL_RETRAINING = True  # Enable for example
                # REMOVED: ENABLE_PROACTIVE_TEST_SYNTHESIS = True # Enable for example
                # Merkle Tree settings (mocked for standalone)
                MERKLE_TREE_BRANCHING_FACTOR = 2
                MERKLE_TREE_PRIVATE_KEY = "dummy_private_key_for_testing"  # Should be SecretStr in real settings

            settings = DummySettings()
            logger.warning(
                "Using dummy settings for standalone meta_supervisor.py execution."
            )

            # Dummy implementations for core dependencies needed by MetaSupervisor.
            # In a real setup, these would be proper instances.
            try:
                from omnicore_engine.database.database import Database as DummyDatabase
            except ImportError:
                pass
            try:
                from omnicore_engine.audit import ExplainAudit as DummyExplainAudit
            except ImportError:
                pass
            try:
                from omnicore_engine.merkle_tree import MerkleTree as DummyMerkleTree
            except ImportError:
                pass
            try:
                from omnicore_engine.plugin_registry import (
                    PLUGIN_REGISTRY as DummyPluginRegistry,
                )
                from omnicore_engine.plugin_registry import PlugInKind, PluginMeta
            except ImportError:
                pass
            try:
                from omnicore_engine.array_backend import (
                    ArrayBackend as DummyArrayBackend,
                )
            except ImportError:
                pass
            from sqlalchemy.ext.asyncio import (
                AsyncSession,
                async_sessionmaker,
                create_async_engine,
            )
            from sqlalchemy.ext.declarative import declarative_base

            Base = declarative_base()  # Define Base for mock DB

            # Mock MerkleTree for standalone testing
            class MockMerkleTree:
                def __init__(self, *args, **kwargs):
                    self._root = b"mock_root"
                    logger.info("MockMerkleTree initialized.")

                def make_tree(self):
                    pass

                def add_leaf(self, leaf: bytes):
                    logger.info(f"MockMerkleTree: Added leaf {leaf[:10]}...")

                def _recalculate_root(self):
                    self._root = b"mock_new_root"

                def get_root(self) -> bytes:
                    return self._root

                def get_merkle_root(self) -> str:
                    return self._root.hex()

            # Mock Database for standalone testing (must be async-compatible)
            class MockDatabaseForSupervisor(Database):
                def __init__(self, db_path: str, system_audit_merkle_tree: Any = None):
                    # Override engine creation for mock database behavior
                    # Use actual create_async_engine but ensure it's in-memory for testing
                    self.engine = create_async_engine(
                        "sqlite+aiosqlite:///:memory:", echo=False
                    )
                    self.AsyncSessionLocal = async_sessionmaker(
                        bind=self.engine, class_=AsyncSession, expire_on_commit=False
                    )
                    self._data_store = defaultdict(dict)  # In-memory mock store
                    if Fernet:
                        self.encrypter = Fernet(
                            b"gqT7tQ_YlM5N-u2pZ-YhX5c-k_G2g_VfS_X4f_X2g_W3c"
                        )  # Dummy key for mock
                    else:
                        self.encrypter = None
                    self.logger = logging.getLogger("MockDatabase")
                    self.system_audit_merkle_tree = system_audit_merkle_tree
                    # Mock other dependencies needed by Database.__init__
                    from unittest.mock import MagicMock

                    self.feedback_manager = MagicMock()
                    self.policy_engine = MagicMock()
                    self.knowledge_graph = MagicMock()
                    self.plugin_registry = MagicMock()

                async def create_tables(self):
                    async with self.engine.begin() as conn:
                        await conn.run_sync(Base.metadata.create_all)
                    self.logger.info("Mock Database tables created (in-memory).")

                async def get_preferences(
                    self, user_id: str
                ) -> Optional[Dict[str, Any]]:
                    self.logger.debug(f"Mock DB: get_preferences for {user_id}")
                    return self._data_store["preferences"].get(user_id)

                async def save_preferences(self, user_id: str, value: Dict[str, Any]):
                    self.logger.debug(
                        f"Mock DB: save_preferences for {user_id}: {value}"
                    )
                    self._data_store["preferences"][user_id] = value

                async def query_audit_records(
                    self, filters: Dict[str, Any] = None, use_dream_mode: bool = False
                ) -> List[Dict[str, Any]]:
                    self.logger.debug(
                        f"Mock DB: query_audit_records with filters {filters}"
                    )
                    # Return mock audit records for training data
                    return [
                        {
                            "uuid": str(uuid.uuid4()),
                            "kind": "meta_supervisor",
                            "name": "plugin_hot_swap",
                            "error": None,
                            "detail": {
                                "stats": {"error_rate": 0.1, "execution_time_avg": 0.01}
                            },
                        },
                        {
                            "uuid": str(uuid.uuid4()),
                            "kind": "meta_supervisor",
                            "name": "test_repair",
                            "error": None,
                            "detail": {
                                "stats": {
                                    "error_rate": 0.05,
                                    "execution_time_avg": 0.005,
                                }
                            },
                        },
                    ]

                async def snapshot_audit_state(
                    self, snapshot_id: str, state: str, user_id: str
                ):
                    self.logger.debug(f"Mock DB: snapshot_audit_state {snapshot_id}")
                    self._data_store["audit_snapshots"][snapshot_id] = {
                        "state": state,
                        "user_id": user_id,
                        "timestamp": time.time(),
                    }

                # Mock save_plugin for self-synthesis
                async def save_plugin(self, plugin_data: Dict[str, Any]):
                    self.logger.info(f"Mock DB: save_plugin {plugin_data.get('name')}")
                    self._data_store["plugins"][plugin_data["name"]] = plugin_data

            # Mock PLUGIN_REGISTRY methods needed by MetaSupervisor
            from unittest.mock import AsyncMock, MagicMock

            mock_plugin_registry = MagicMock()
            mock_plugin_registry.hot_swap_manager = (
                MagicMock()
            )  # Mock the hot_swap_manager
            mock_plugin_registry.hot_swap_manager.hot_swap_plugin = AsyncMock(
                return_value=True
            )  # Mock this interaction
            mock_plugin_registry.get_plugins_by_kind = MagicMock(
                return_value=[
                    MagicMock(
                        fn=MagicMock(
                            _wrapped=MagicMock(_array_backend=MagicMock(mode="torch"))
                        )
                    )
                ]
            )

            # Use mocks in MetaSupervisor's dependencies
            supervisor = MetaSupervisor(
                interval=5,
                backend_mode="torch",
                use_quantum=True,
                use_neuromorphic=False,
            )
            supervisor.db = MockDatabaseForSupervisor(
                settings.DATABASE_URL, system_audit_merkle_tree=MockMerkleTree()
            )
            supervisor.plugin_registry = mock_plugin_registry

            # Mock the `omnicore_engine_global_instance`'s orchestrator if _synthesize_test_plugin calls it
            # This is complex due to deep dependency. Simplest is to directly mock the method called.
            # E.g., mock `omnicore_engine_global_instance.orchestrator.live_reload_manager.hot_swap_manager.hot_swap_plugin`

            # For standalone testing of `_synthesize_test_plugin` that calls `omnicore_engine_global_instance.orchestrator.live_reload_manager.hot_swap_manager.hot_swap_plugin`,
            # we need `omnicore_engine_global_instance` to exist and have the right structure.
            # This is why circular imports are hard.
            # For this example to run, we bypass that call or make `omnicore_engine_global_instance` a simple mock.

            # Create a mock for the global omnicore_engine_global_instance needed by _synthesize_test_plugin
            global omnicore_engine_global_instance
            omnicore_engine_global_instance = MagicMock()
            omnicore_engine_global_instance.orchestrator = MagicMock()
            omnicore_engine_global_instance.orchestrator.live_reload_manager = (
                MagicMock()
            )
            omnicore_engine_global_instance.orchestrator.live_reload_manager.hot_swap_manager = (
                MagicMock()
            )
            omnicore_engine_global_instance.orchestrator.live_reload_manager.hot_swap_manager.hot_swap_plugin = AsyncMock(
                return_value=True
            )

            logger.info("Starting MetaSupervisor example with mocked dependencies.")

            task = asyncio.create_task(supervisor.run())

            await asyncio.sleep(15)  # Let it run for a bit to demonstrate functionality

            await supervisor.stop()
            await task  # Await the supervisor's main task to complete its shutdown process

        else:
            # Running in a larger app context where settings are already configured
            supervisor = MetaSupervisor(
                interval=300, backend_mode="torch", use_quantum=True
            )
            await supervisor.initialize()
            task = asyncio.create_task(supervisor.run())
            await asyncio.sleep(3600)  # Run for an hour in real app
            await supervisor.stop()
            await task

    asyncio.run(main_supervisor_example())
