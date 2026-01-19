"""
Test suite for omnicore_engine/meta_supervisor.py
Tests the MetaSupervisor orchestration and optimization system.
"""

import asyncio
import os

# Add the parent directory to path for imports
import sys
import time
from unittest.mock import AsyncMock, Mock, patch

import numpy as np
import pytest

# Handle torch import error gracefully
try:
    import torch

    TORCH_AVAILABLE = True
except (ImportError, OSError):
    torch = None
    TORCH_AVAILABLE = False

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.meta_supervisor import (
    MetaSupervisor,
    _create_fallback_settings,
    _is_anomalous,
    validate_model_input,
    validate_training_data,
)


class TestInputValidation:
    """Test input validation functions"""

    def test_validate_model_input_valid(self):
        """Test validation of valid model inputs"""
        features = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = validate_model_input(features)

        assert isinstance(result, np.ndarray)
        # Should be normalized (mean 0, std 1)
        assert np.abs(np.mean(result)) < 0.01
        assert np.abs(np.std(result) - 1.0) < 0.01

    def test_validate_model_input_with_nan(self):
        """Test validation rejects NaN values"""
        features = np.array([1.0, np.nan, 3.0])

        with pytest.raises(ValueError, match="contains NaN"):
            validate_model_input(features)

    def test_validate_model_input_with_inf(self):
        """Test validation rejects Inf values"""
        features = np.array([1.0, np.inf, 3.0])

        with pytest.raises(ValueError, match="contains.*Inf"):
            validate_model_input(features)

    def test_validate_model_input_clipping(self):
        """Test that extreme values are clipped"""
        features = np.array([1e7, -1e7, 1.0])
        result = validate_model_input(features)

        assert np.all(result <= 1e6)
        assert np.all(result >= -1e6)

    def test_validate_training_data(self):
        """Test training data validation"""
        audit_records = [
            {"uuid": "1", "kind": "test"},
            {"uuid": "2", "kind": "test"},
            {
                "uuid": "3",
                "kind": "anomaly",
            },  # Would be filtered if _is_anomalous returns True
        ]

        result = validate_training_data(audit_records)
        assert len(result) == 3  # Since _is_anomalous returns False by default

    def test_is_anomalous_placeholder(self):
        """Test anomaly detection placeholder"""
        record = {"uuid": "test", "error_rate": 0.99}
        assert _is_anomalous(record) == False  # Always returns False in placeholder


def create_mock_settings():
    """Helper function to create properly configured mock settings with real numerical values"""
    settings = Mock()
    settings.DATABASE_URL = "sqlite:///:memory:"
    settings.REDIS_URL = "redis://localhost"
    settings.PLUGIN_ERROR_THRESHOLD = 0.1
    settings.TEST_FAILURE_THRESHOLD = 0.2
    settings.ETHICS_DRIFT_THRESHOLD = 0.05
    settings.SUPERVISOR_RATE_LIMIT_OPS = 10
    settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
    settings.ENABLE_PROACTIVE_MODEL_RETRAINING = False
    settings.DB_RETRY_ATTEMPTS = 3
    settings.DB_RETRY_DELAY = 0.1
    settings.MODEL_RETRAIN_EPOCHS = 5
    settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
    settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7
    settings.AUDIT_LOG_RETENTION_DAYS = 30
    return settings


class TestMetaSupervisorInitialization:
    """Test MetaSupervisor initialization"""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings"""
        return create_mock_settings()

    @patch("omnicore_engine.meta_supervisor.settings")
    def test_initialization(self, mock_global_settings):
        """Test basic initialization"""
        mock_global_settings.PLUGIN_ERROR_THRESHOLD = 0.1
        mock_global_settings.TEST_FAILURE_THRESHOLD = 0.2
        mock_global_settings.ETHICS_DRIFT_THRESHOLD = 0.05
        mock_global_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
        mock_global_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60

        supervisor = MetaSupervisor(interval=60, backend_mode="numpy")

        assert supervisor.interval == 60
        assert supervisor.focus is None
        assert supervisor.backend.mode == "numpy"
        assert not supervisor._stopped.is_set()
        assert supervisor.thresholds["plugin_error"] == 0.1

    @patch("omnicore_engine.meta_supervisor.settings")
    @pytest.mark.skipif(not TORCH_AVAILABLE, reason="torch not available")
    def test_initialization_with_torch_backend(self, mock_global_settings):
        """Test initialization with PyTorch backend"""
        mock_global_settings.PLUGIN_ERROR_THRESHOLD = 0.1
        mock_global_settings.TEST_FAILURE_THRESHOLD = 0.2
        mock_global_settings.ETHICS_DRIFT_THRESHOLD = 0.05
        mock_global_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
        mock_global_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
        mock_global_settings.DB_RETRY_ATTEMPTS = 3
        mock_global_settings.DB_RETRY_DELAY = 0.1
        mock_global_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
        mock_global_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

        supervisor = MetaSupervisor(interval=60, backend_mode="torch")

        assert supervisor.backend.mode == "torch"
        # rl_model and prediction_model are initialized in async initialize(), not __init__
        # So after __init__, they are None until initialize() is called
        assert supervisor.rl_model is None
        assert supervisor.prediction_model is None

        # Verify that _init_rl_model would work with torch backend
        model = supervisor._init_rl_model()
        assert model is not None
        assert isinstance(model, torch.nn.Module)

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.Database")
    async def test_async_initialization(self, mock_db_class, mock_settings):
        """Test async initialization"""
        mock_db = Mock()
        mock_db.create_tables = AsyncMock()
        mock_db.get_preferences = AsyncMock(return_value={})
        mock_db_class.return_value = mock_db

        with patch("omnicore_engine.meta_supervisor.settings", mock_settings):
            supervisor = MetaSupervisor(interval=60)
            await supervisor.initialize()

            assert supervisor.db is not None
            mock_db.create_tables.assert_called_once()


class TestPluginInspection:
    """Test plugin inspection functionality"""

    @pytest.fixture
    def supervisor(self):
        """Create supervisor instance with mocked dependencies"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            # Set ALL required numerical settings as real values
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1

            # Use numpy backend to avoid torch dependency
            supervisor = MetaSupervisor(interval=60, backend_mode="numpy")
            supervisor.db = Mock()
            supervisor.explainer = Mock()
            supervisor.explainer.explain = AsyncMock(
                return_value={"explanation": "test"}
            )
            return supervisor

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.get_plugin_metrics")
    @patch("omnicore_engine.meta_supervisor.record_meta_audit_event")
    async def test_inspect_plugins_with_metrics(
        self, mock_record, mock_get_metrics, supervisor
    ):
        """Test plugin inspection with available metrics"""
        mock_get_metrics.return_value = {
            "execution:test_plugin": {
                "error_rate": 0.05,
                "execution_time_avg": 0.1,
                "executions": 100,
            }
        }
        mock_record.return_value = None

        supervisor._evaluate_self_performance = AsyncMock(return_value=0.9)

        await supervisor.inspect_plugins()

        mock_get_metrics.assert_called_once()
        supervisor._evaluate_self_performance.assert_called_once()

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.get_plugin_metrics")
    async def test_inspect_plugins_no_metrics(self, mock_get_metrics, supervisor):
        """Test plugin inspection with no metrics"""
        mock_get_metrics.return_value = {}

        await supervisor.inspect_plugins()

        # Should return early without errors
        mock_get_metrics.assert_called_once()

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.get_plugin_metrics")
    @patch("omnicore_engine.meta_supervisor.record_meta_audit_event")
    async def test_inspect_plugins_high_error_rate(
        self, mock_record, mock_get_metrics, supervisor
    ):
        """Test plugin inspection triggers hot-swap on high error rate"""
        mock_get_metrics.return_value = {
            "execution:failing_plugin": {
                "error_rate": 0.3,  # Above threshold of 0.1
                "execution_time_avg": 0.1,
                "executions": 100,
            }
        }
        mock_record.return_value = None

        supervisor._evaluate_self_performance = AsyncMock(return_value=0.9)

        await supervisor.inspect_plugins()

        # Should log warning about reactive hot-swap
        assert supervisor.explainer.explain.called


class TestTestInspection:
    """Test test inspection functionality"""

    @pytest.fixture
    def supervisor(self):
        """Create supervisor instance"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 10
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            supervisor.db = Mock()
            supervisor.explainer = Mock()
            supervisor.explainer.explain = AsyncMock(
                return_value={"explanation": "test"}
            )
            return supervisor

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.get_test_metrics")
    @patch("omnicore_engine.meta_supervisor.run_all_tests")
    @patch("omnicore_engine.meta_supervisor.record_meta_audit_event")
    async def test_inspect_tests_high_failures(
        self, mock_record, mock_run_tests, mock_get_metrics, supervisor
    ):
        """Test test inspection with high failures triggers auto-repair"""
        mock_get_metrics.return_value = {"failures": 20, "total": 100}
        mock_run_tests.return_value = {
            "failures": 5
        }  # Still some failures after repair
        mock_record.return_value = None

        supervisor.spawn_supervisor = AsyncMock(return_value="sub_test_123")

        await supervisor.inspect_tests()

        mock_run_tests.assert_called_once_with(auto_repair=True)
        supervisor.spawn_supervisor.assert_called_once_with(focused_task="tests")

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.get_test_metrics")
    async def test_inspect_tests_low_failures(self, mock_get_metrics, supervisor):
        """Test test inspection with acceptable failures"""
        mock_get_metrics.return_value = {"failures": 5, "total": 100}

        await supervisor.inspect_tests()

        # Should not trigger auto-repair
        mock_get_metrics.assert_called_once()


class TestConfigInspection:
    """Test configuration inspection functionality"""

    @pytest.fixture
    def supervisor(self):
        """Create supervisor instance"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            supervisor.db = Mock()
            supervisor.policy_engine = Mock()
            supervisor.knowledge_graph = Mock()
            supervisor.explainer = Mock()
            supervisor.explainer.explain = AsyncMock(
                return_value={"explanation": "test"}
            )
            return supervisor

    @pytest.mark.asyncio
    @patch("omnicore_engine.meta_supervisor.rollback_config")
    @patch("omnicore_engine.meta_supervisor.record_meta_audit_event")
    async def test_inspect_config_ethical_drift(
        self, mock_record, mock_rollback, supervisor
    ):
        """Test config inspection detects and rolls back ethical drift"""
        mock_record.return_value = None

        test_changes = [
            {
                "user_id": "user1",
                "new_value": {"setting": "bad"},
                "previous": {"setting": "good"},
            }
        ]
        # Override _rate_limited_operation to return our test data
        supervisor._rate_limited_operation = AsyncMock(return_value=test_changes)
        supervisor.detect_ethical_drift = AsyncMock(return_value=True)

        await supervisor.inspect_config()

        # Should be called once for our single config change
        supervisor.detect_ethical_drift.assert_called_once()

    @pytest.mark.asyncio
    async def test_detect_ethical_drift_policy_denied(self, supervisor):
        """Test ethical drift detection when policy denies"""
        # Use regular Mock since the call goes through lambda wrapper
        # which makes it synchronous (returns the coroutine, not awaits it)
        # But the lambda returns the result directly, so we need sync mock
        supervisor.policy_engine.should_auto_learn = Mock(
            return_value=(False, "Denied")
        )

        change = {"user_id": "user1", "new_value": {"setting": "bad"}}
        result = await supervisor.detect_ethical_drift(change)

        assert result == True

    @pytest.mark.asyncio
    async def test_detect_ethical_drift_high_impact(self, supervisor):
        """Test ethical drift detection with high knowledge graph impact"""
        # Use regular Mock since the call goes through lambda wrapper
        supervisor.policy_engine.should_auto_learn = Mock(
            return_value=(True, "Allowed")
        )
        supervisor.knowledge_graph.add_fact = Mock(return_value={"ethical_impact": 0.8})
        supervisor.thresholds["ethics_drift"] = 0.05

        change = {"user_id": "user1", "new_value": {"setting": "questionable"}}
        result = await supervisor.detect_ethical_drift(change)

        assert result == True


class TestThresholdOptimization:
    """Test threshold optimization functionality"""

    @pytest.fixture
    def supervisor_torch(self):
        """Create supervisor with torch backend"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60, backend_mode="torch")
            supervisor.db = Mock()
            supervisor.db.save_preferences = AsyncMock()
            # Initialize models for tests that require torch-backed functionality
            if TORCH_AVAILABLE:
                supervisor.rl_model = supervisor._init_rl_model()
                supervisor.prediction_model = supervisor._init_prediction_model()
            return supervisor

    @pytest.mark.asyncio
    @pytest.mark.skipif(not TORCH_AVAILABLE, reason="torch not available")
    async def test_optimize_thresholds_with_rl_model(self, supervisor_torch):
        """Test threshold optimization with RL model"""
        supervisor_torch._get_system_state = AsyncMock(
            return_value=np.array([0.1] * 10)
        )
        supervisor_torch._save_thresholds = AsyncMock()

        await supervisor_torch.optimize_thresholds()

        supervisor_torch._get_system_state.assert_called_once()
        supervisor_torch._save_thresholds.assert_called_once()

        # Thresholds should be updated
        assert 0 <= supervisor_torch.thresholds["plugin_error"] <= 1
        assert 0 <= supervisor_torch.thresholds["test_failure"] <= 1
        assert 0 <= supervisor_torch.thresholds["ethics_drift"] <= 1

    @pytest.mark.asyncio
    async def test_optimize_thresholds_no_rl_model(self):
        """Test threshold optimization without RL model"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1

            supervisor = MetaSupervisor(interval=60, backend_mode="numpy")

            await supervisor.optimize_thresholds()

            # Should return early without errors
            assert supervisor.rl_model is None


class TestModelManagement:
    """Test model saving and loading"""

    @pytest.fixture
    def supervisor_torch(self):
        """Create supervisor with torch backend"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60, backend_mode="torch")
            supervisor.db = Mock()
            # Use regular Mock with return_value since lambda wraps sync calls
            supervisor.db.save_preferences = Mock()
            supervisor.db.get_preferences = Mock()
            # Initialize models for tests that require torch-backed functionality
            if TORCH_AVAILABLE:
                supervisor.rl_model = supervisor._init_rl_model()
                supervisor.prediction_model = supervisor._init_prediction_model()
            return supervisor

    @pytest.mark.asyncio
    @pytest.mark.skipif(not TORCH_AVAILABLE, reason="torch not available")
    async def test_save_models(self, supervisor_torch):
        """Test saving models to database"""
        await supervisor_torch.save_models()

        supervisor_torch.db.save_preferences.assert_called_once()
        call_args = supervisor_torch.db.save_preferences.call_args
        assert "meta_supervisor_models_" in call_args[1]["user_id"]
        assert "rl_model" in call_args[1]["value"]
        assert "prediction_model" in call_args[1]["value"]

    @pytest.mark.asyncio
    @pytest.mark.skipif(not TORCH_AVAILABLE, reason="torch not available")
    async def test_load_models_specific_version(self, supervisor_torch):
        """Test loading specific model version"""
        # Create proper model data with hex-encoded model states
        import io

        rl_buffer = io.BytesIO()
        pred_buffer = io.BytesIO()
        torch.save(supervisor_torch.rl_model.state_dict(), rl_buffer)
        torch.save(supervisor_torch.prediction_model.state_dict(), pred_buffer)

        model_data = {
            "version": "test_version",
            "rl_model": rl_buffer.getvalue().hex(),
            "prediction_model": pred_buffer.getvalue().hex(),
            "timestamp": time.time(),
        }

        supervisor_torch.db.get_preferences.return_value = model_data

        await supervisor_torch.load_models(version="test_version")

        supervisor_torch.db.get_preferences.assert_called_once()


class TestSupervisorLifecycle:
    """Test supervisor lifecycle management"""

    @pytest.mark.asyncio
    async def test_self_reload(self):
        """Test self-reload functionality"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            # Use regular Mock since save_models goes through _rate_limited_operation
            supervisor.save_models = Mock()
            supervisor.explainer = Mock()
            supervisor.explainer.explain = AsyncMock(
                return_value={"explanation": "test"}
            )

            with patch("omnicore_engine.meta_supervisor.record_meta_audit_event"):
                await supervisor.self_reload()

                assert supervisor._stopped.is_set()
                supervisor.save_models.assert_called_once()

    @pytest.mark.asyncio
    async def test_spawn_supervisor(self):
        """Test spawning sub-supervisor"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            supervisor.db = Mock()
            supervisor.db.create_tables = AsyncMock()
            supervisor.db.get_preferences = AsyncMock(return_value={})

            with patch(
                "omnicore_engine.meta_supervisor.MetaSupervisor"
            ) as mock_meta_class:
                mock_sub = Mock()
                mock_sub.initialize = AsyncMock()
                mock_sub.load_models = AsyncMock()
                mock_sub.run = AsyncMock()
                mock_meta_class.return_value = mock_sub

                sub_id = await supervisor.spawn_supervisor("tests")

                assert sub_id.startswith("sub_tests_")
                assert sub_id in supervisor.sub_supervisors

    @pytest.mark.asyncio
    async def test_stop(self):
        """Test graceful stop"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            # Use regular Mock since save_models goes through _rate_limited_operation
            supervisor.save_models = Mock()

            # Create a mock task that properly mimics an asyncio Task
            mock_task = Mock()
            mock_task.done.return_value = False
            mock_task.cancel.return_value = None

            # Create an async coroutine for when the task is awaited after cancellation
            async def cancelled_task():
                raise asyncio.CancelledError()

            # Mock __await__ to make it awaitable
            mock_task.__await__ = lambda self: cancelled_task().__await__()

            supervisor.sub_supervisors["sub_test"] = mock_task

            await supervisor.stop()

            assert supervisor._stopped.is_set()
            mock_task.cancel.assert_called_once()
            supervisor.save_models.assert_called_once()


class TestReportGeneration:
    """Test report generation functionality"""

    @pytest.mark.asyncio
    async def test_generate_mentor_report(self):
        """Test mentor report generation"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            supervisor.db = Mock()
            # Use regular Mock since query_audit_records goes through _rate_limited_operation
            supervisor.db.query_audit_records = Mock(
                return_value=[
                    {"kind": "meta_supervisor", "explanation": "Lesson 1"},
                    {"kind": "config_rollback", "detail": "Ethical issue"},
                    {"kind": "policy_denial", "name": "bad_action", "error": "Denied"},
                ]
            )
            supervisor.explainer = Mock()
            supervisor.explainer.explain = AsyncMock(
                return_value={"explanation": "Report explanation"}
            )

            with patch("omnicore_engine.meta_supervisor.record_meta_audit_event"):
                report = await supervisor.generate_mentor_report()

                assert "summary" in report
                assert "lessons_learned" in report
                assert "ethical_divergences" in report
                assert len(report["lessons_learned"]) > 0
                assert len(report["ethical_divergences"]) > 0


class TestMainLoop:
    """Test main run loop"""

    @pytest.mark.asyncio
    async def test_run_loop_max_iterations(self):
        """Test run loop stops at max iterations"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(
                interval=0.01
            )  # Very short interval for testing
            supervisor.initialize = AsyncMock()
            supervisor.inspect_plugins = AsyncMock()
            supervisor.inspect_tests = AsyncMock()
            supervisor.inspect_config = AsyncMock()
            supervisor.optimize_thresholds = AsyncMock()
            supervisor.publish_meta_status = AsyncMock()
            # Mock _rate_limited_operation to return a dict with "changes" key
            supervisor._rate_limited_operation = AsyncMock(return_value={"changes": []})

            with patch("omnicore_engine.meta_supervisor.MAX_ITERATIONS", 2):
                await supervisor.run()

                # Should stop after 2 iterations
                assert supervisor.inspect_plugins.call_count <= 2

    @pytest.mark.asyncio
    async def test_run_loop_with_focus(self):
        """Test run loop with specific focus"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=0.01, focus="plugins")
            supervisor.initialize = AsyncMock()
            supervisor.inspect_plugins = AsyncMock()
            supervisor.inspect_tests = AsyncMock()
            supervisor.inspect_config = AsyncMock()
            supervisor.publish_meta_status = AsyncMock()
            # Mock _rate_limited_operation to return a dict with "changes" key
            supervisor._rate_limited_operation = AsyncMock(return_value={"changes": []})

            # Run with max iterations of 1 to verify focus works
            with patch("omnicore_engine.meta_supervisor.MAX_ITERATIONS", 1):
                await supervisor.run()

            supervisor.inspect_plugins.assert_called()
            supervisor.inspect_tests.assert_not_called()
            supervisor.inspect_config.assert_not_called()


class TestMetaPolicies:
    """Test meta-policy functionality"""

    @pytest.mark.asyncio
    async def test_set_meta_policy_allowed(self):
        """Test setting meta-policy when allowed"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            supervisor.db = Mock()
            # Use regular Mock since calls go through _rate_limited_operation with lambda
            supervisor.db.save_preferences = Mock()
            supervisor.policy_engine = Mock()
            supervisor.policy_engine.should_auto_learn = Mock(
                return_value=(True, "Allowed")
            )
            supervisor.explainer = Mock()
            supervisor.explainer.explain = AsyncMock(
                return_value={"explanation": "test"}
            )

            with patch("omnicore_engine.meta_supervisor.record_meta_audit_event"):
                result = await supervisor.set_meta_policy(
                    {"plugin_error_weight": 0.8}, "user123"
                )

                assert result == True
                assert supervisor.meta_policies["plugin_error_weight"] == 0.8

    @pytest.mark.asyncio
    async def test_set_meta_policy_denied(self):
        """Test setting meta-policy when denied"""
        with patch("omnicore_engine.meta_supervisor.settings") as mock_settings:
            mock_settings.PLUGIN_ERROR_THRESHOLD = 0.1
            mock_settings.TEST_FAILURE_THRESHOLD = 0.2
            mock_settings.ETHICS_DRIFT_THRESHOLD = 0.05
            mock_settings.SUPERVISOR_RATE_LIMIT_OPS = 10
            mock_settings.SUPERVISOR_RATE_LIMIT_PERIOD = 60
            mock_settings.DB_RETRY_ATTEMPTS = 3
            mock_settings.DB_RETRY_DELAY = 0.1
            mock_settings.PROACTIVE_HOT_SWAP_PREDICTION_THRESHOLD = 0.8
            mock_settings.SUPERVISOR_PERFORMANCE_THRESHOLD = 0.7

            supervisor = MetaSupervisor(interval=60)
            supervisor.policy_engine = Mock()
            # Use regular Mock since calls go through _rate_limited_operation with lambda
            supervisor.policy_engine.should_auto_learn = Mock(
                return_value=(False, "Denied")
            )

            result = await supervisor.set_meta_policy(
                {"plugin_error_weight": 0.8}, "user123"
            )

            assert result == False


class TestAsyncLambdaFixes:
    """Test that async methods are properly awaited without lambda wrappers"""

    @pytest.mark.asyncio
    async def test_rate_limited_operation_with_async_method(self):
        """Test _rate_limited_operation properly awaits async methods passed directly"""
        supervisor = MetaSupervisor(interval=60)
        
        # Mock the database to avoid initialization issues
        supervisor.db = Mock()
        supervisor.db.AsyncSessionLocal = Mock()

        # Mock an async method
        async def mock_async_method(param1, param2):
            await asyncio.sleep(0.01)
            return {"result": f"{param1}_{param2}"}

        # Test that passing the method directly works
        result = await supervisor._rate_limited_operation(
            mock_async_method, "value1", "value2"
        )

        assert result == {"result": "value1_value2"}

    @pytest.mark.asyncio
    async def test_db_get_preferences_without_lambda(self):
        """Test that db.get_preferences works when called without lambda wrapper"""
        supervisor = MetaSupervisor(interval=60)
        
        # Mock the database
        supervisor.db = Mock()
        supervisor.db.get_preferences = AsyncMock(return_value={"test": "data"})

        # Test that the method can be called directly through rate_limited_operation
        result = await supervisor._rate_limited_operation(
            supervisor.db.get_preferences, user_id="test_user"
        )

        assert result == {"test": "data"}
        supervisor.db.get_preferences.assert_called_once_with(user_id="test_user")

    @pytest.mark.asyncio
    async def test_record_audit_event_without_lambda(self):
        """Test that _record_audit_event works when called without lambda wrapper"""
        supervisor = MetaSupervisor(interval=60)
        
        # Mock dependencies
        supervisor.db = Mock()
        supervisor._record_audit_event = AsyncMock()

        # Test that the method can be called directly
        await supervisor._rate_limited_operation(
            supervisor._record_audit_event,
            "test_kind",
            "test_name",
            {"test": "details"},
        )

        # Verify it was called with correct args
        supervisor._record_audit_event.assert_called_once_with(
            "test_kind", "test_name", {"test": "details"}
        )

    @pytest.mark.asyncio
    async def test_policy_engine_should_auto_learn_without_lambda(self):
        """Test that policy_engine.should_auto_learn works without lambda wrapper"""
        supervisor = MetaSupervisor(interval=60)
        
        # Mock policy engine with async method
        supervisor.policy_engine = Mock()
        supervisor.policy_engine.should_auto_learn = AsyncMock(
            return_value=(True, "Allowed")
        )

        # Test calling through rate_limited_operation
        allowed, reason = await supervisor._rate_limited_operation(
            supervisor.policy_engine.should_auto_learn,
            "TestAgent",
            "test_action",
            "test_user",
            {},
        )

        assert allowed == True
        assert reason == "Allowed"
        supervisor.policy_engine.should_auto_learn.assert_called_once_with(
            "TestAgent", "test_action", "test_user", {}
        )

    def test_fallback_settings_include_db_retry_config(self):
        """Test that fallback settings include DB_RETRY_ATTEMPTS and DB_RETRY_DELAY"""
        settings = _create_fallback_settings()

        assert hasattr(settings, "DB_RETRY_ATTEMPTS")
        assert hasattr(settings, "DB_RETRY_DELAY")
        assert settings.DB_RETRY_ATTEMPTS == 3
        assert settings.DB_RETRY_DELAY == 0.1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
