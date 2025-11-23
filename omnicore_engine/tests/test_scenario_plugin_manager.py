"""
Test suite for omnicore_engine/scenario_plugin_manager.py
Tests the OmniCoreEngine, Base class, and utility functions.
"""

import pytest
import asyncio
from datetime import datetime
from unittest.mock import Mock, patch, AsyncMock
import sys
import os

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.scenario_plugin_manager import (
    safe_serialize,
    Base,
    get_plugin_metrics,
    get_test_metrics,
    ExplainableAI,
    OmniCoreEngine,
    omnicore_engine,
)


class TestSafeSerialize:
    """Test the safe_serialize utility function"""

    def test_primitive_types(self):
        """Test serialization of primitive types"""
        assert safe_serialize("string") == "string"
        assert safe_serialize(42) == 42
        assert safe_serialize(3.14) == 3.14
        assert safe_serialize(True) == True
        assert safe_serialize(None) is None

    def test_datetime_serialization(self):
        """Test datetime serialization"""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        assert safe_serialize(dt) == "2024-01-01T12:00:00"

    def test_bytes_serialization(self):
        """Test bytes serialization"""
        assert safe_serialize(b"hello") == "hello"
        assert safe_serialize(b"\x00\x01\x02") == "\x00\x01\x02"

    def test_collection_serialization(self):
        """Test serialization of collections"""
        # Set
        assert safe_serialize({1, 2, 3}) == [1, 2, 3]

        # List and tuple
        assert safe_serialize([1, 2, 3]) == [1, 2, 3]
        assert safe_serialize((1, 2, 3)) == [1, 2, 3]

        # Dict
        assert safe_serialize({"key": "value"}) == {"key": "value"}

    def test_nested_structures(self):
        """Test serialization of nested structures"""
        nested = {
            "list": [1, 2, {"inner": "dict"}],
            "set": {4, 5, 6},
            "tuple": (7, 8, 9),
            "datetime": datetime(2024, 1, 1),
        }

        result = safe_serialize(nested)
        assert result["list"] == [1, 2, {"inner": "dict"}]
        assert result["set"] == [4, 5, 6]
        assert result["tuple"] == [7, 8, 9]
        assert result["datetime"] == "2024-01-01T00:00:00"

    def test_model_dump_objects(self):
        """Test objects with model_dump method"""
        mock_obj = Mock()
        mock_obj.model_dump.return_value = {"field": "value"}

        assert safe_serialize(mock_obj) == {"field": "value"}

    def test_dict_method_objects(self):
        """Test objects with dict method"""
        mock_obj = Mock()
        mock_obj.dict.return_value = {"field": "value"}
        del mock_obj.model_dump  # Ensure model_dump doesn't exist

        assert safe_serialize(mock_obj) == {"field": "value"}

    def test_unserializable_objects(self):
        """Test handling of unserializable objects"""

        class UnserializableClass:
            def __str__(self):
                raise Exception("Cannot convert to string")

        obj = UnserializableClass()
        result = safe_serialize(obj)
        assert "<unserializable object" in result


class TestBase:
    """Test the Base abstract class"""

    def test_base_cannot_be_instantiated(self):
        """Test that Base class cannot be directly instantiated"""
        with pytest.raises(TypeError):
            Base()

    def test_base_subclass_implementation(self):
        """Test proper subclass implementation of Base"""

        class TestComponent(Base):
            async def initialize(self):
                pass

            async def shutdown(self):
                pass

            async def health_check(self):
                return {"status": "ok"}

            @property
            def is_healthy(self):
                return True

        component = TestComponent()
        assert hasattr(component, "settings")
        assert hasattr(component, "initialize")
        assert hasattr(component, "shutdown")
        assert hasattr(component, "health_check")
        assert hasattr(component, "is_healthy")


class TestMetricsFunctions:
    """Test plugin and test metrics functions"""

    @patch("omnicore_engine.scenario_plugin_manager.actual_get_plugin_metrics")
    def test_get_plugin_metrics_success(self, mock_metrics):
        """Test successful plugin metrics retrieval"""
        mock_metrics.return_value = {"metric1": 10, "metric2": 20}

        with patch.dict(
            "sys.modules",
            {"omnicore_engine.metrics": Mock(get_plugin_metrics=mock_metrics)},
        ):
            result = get_plugin_metrics()
            assert result == {"metric1": 10, "metric2": 20}

    def test_get_plugin_metrics_import_error(self):
        """Test plugin metrics when module not available"""
        with patch.dict("sys.modules", {"omnicore_engine.metrics": None}):
            result = get_plugin_metrics()
            assert "error" in result
            assert "Metrics system not available" in result["error"]

    @patch("omnicore_engine.scenario_plugin_manager.actual_get_plugin_metrics")
    def test_get_plugin_metrics_exception(self, mock_metrics):
        """Test plugin metrics with exception"""
        mock_metrics.side_effect = Exception("Metrics error")

        with patch.dict(
            "sys.modules",
            {"omnicore_engine.metrics": Mock(get_plugin_metrics=mock_metrics)},
        ):
            result = get_plugin_metrics()
            assert "error" in result
            assert "Metrics error" in result["error"]

    def test_get_test_metrics_import_error(self):
        """Test test metrics when module not available"""
        with patch.dict("sys.modules", {"omnicore_engine.metrics": None}):
            result = get_test_metrics()
            assert "error" in result
            assert "Metrics system not available" in result["error"]


class TestExplainableAI:
    """Test ExplainableAI class"""

    def test_initialization_with_reasoner(self):
        """Test ExplainableAI initialization with reasoner available"""
        mock_reasoner = Mock()

        with patch(
            "omnicore_engine.scenario_plugin_manager.ExplainableReasoner",
            return_value=mock_reasoner,
        ):
            ai = ExplainableAI()
            assert ai.reasoner == mock_reasoner
            assert not ai.is_initialized

    def test_initialization_without_reasoner(self):
        """Test ExplainableAI initialization when reasoner not available"""
        with patch.dict("sys.modules", {"omnicore_engine.explainable_reasoner": None}):
            ai = ExplainableAI()
            assert ai.reasoner is None
            assert not ai.is_initialized

    @pytest.mark.asyncio
    async def test_initialize(self):
        """Test ExplainableAI initialization"""
        ai = ExplainableAI()
        await ai.initialize()

        assert ai.is_initialized

    @pytest.mark.asyncio
    async def test_shutdown_with_reasoner(self):
        """Test ExplainableAI shutdown with reasoner"""
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.shutdown_executor = Mock()

        await ai.shutdown()

        assert not ai.is_initialized
        ai.reasoner.shutdown_executor.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_without_reasoner(self):
        """Test ExplainableAI shutdown without reasoner"""
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = None

        await ai.shutdown()

        assert not ai.is_initialized

    @pytest.mark.asyncio
    async def test_explain_event_with_reasoner(self):
        """Test explain_event with reasoner available"""
        ai = ExplainableAI()
        ai.reasoner = Mock()
        ai.reasoner.explain_event = AsyncMock(return_value={"explanation": "Test explanation"})

        result = await ai.explain_event({"event": "test"})

        assert result == {"explanation": "Test explanation"}
        ai.reasoner.explain_event.assert_called_once_with({"event": "test"})

    @pytest.mark.asyncio
    async def test_explain_event_without_reasoner(self):
        """Test explain_event without reasoner"""
        ai = ExplainableAI()
        ai.reasoner = None

        result = await ai.explain_event({"event": "test"})

        assert "explanation" in result
        assert "Mock explanation" in result["explanation"]

    @pytest.mark.asyncio
    async def test_reason_event_with_reasoner(self):
        """Test reason_event with reasoner available"""
        ai = ExplainableAI()
        ai.reasoner = Mock()
        ai.reasoner.reason_event = AsyncMock(return_value={"reasoning": "Test reasoning"})

        result = await ai.reason_event({"event": "test"})

        assert result == {"reasoning": "Test reasoning"}
        ai.reasoner.reason_event.assert_called_once_with({"event": "test"})

    @pytest.mark.asyncio
    async def test_reason_event_without_reasoner(self):
        """Test reason_event without reasoner"""
        ai = ExplainableAI()
        ai.reasoner = None

        result = await ai.reason_event({"event": "test"})

        assert "reasoning" in result
        assert "Mock reasoning" in result["reasoning"]


class TestOmniCoreEngine:
    """Test OmniCoreEngine class"""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings"""
        settings = Mock()
        settings.database_path = "sqlite:///:memory:"
        settings.redis_url = "redis://localhost"
        settings.plugin_dir = "/tmp/plugins"
        settings.ENCRYPTION_KEY = Mock(get_secret_value=lambda: "test_key")
        return settings

    @pytest.fixture
    def engine(self, mock_settings):
        """Create engine instance with mock settings"""
        return OmniCoreEngine(mock_settings)

    def test_initialization(self, engine):
        """Test engine initialization state"""
        assert not engine.is_initialized
        assert engine.components == {}
        assert engine.component_locks == {}

    @pytest.mark.asyncio
    async def test_initialize_already_initialized(self, engine):
        """Test initialize when already initialized"""
        engine._is_initialized = True

        await engine.initialize()

        # Should return early without initializing components
        assert engine.components == {}

    @pytest.mark.asyncio
    @patch("omnicore_engine.scenario_plugin_manager.Database")
    @patch("omnicore_engine.scenario_plugin_manager.ExplainAudit")
    @patch("omnicore_engine.scenario_plugin_manager.PLUGIN_REGISTRY")
    @patch("omnicore_engine.scenario_plugin_manager.start_plugin_observer")
    @patch("omnicore_engine.scenario_plugin_manager.FeedbackManager")
    async def test_initialize_components(
        self, mock_feedback, mock_observer, mock_registry, mock_audit, mock_db, engine
    ):
        """Test component initialization"""
        # Setup mocks
        mock_db_instance = Mock()
        mock_db_instance.initialize = AsyncMock()
        mock_db.return_value = mock_db_instance

        mock_audit_instance = Mock()
        mock_audit_instance.initialize = AsyncMock()
        mock_audit.return_value = mock_audit_instance

        mock_feedback_instance = Mock()
        mock_feedback_instance.initialize = AsyncMock()
        mock_feedback.return_value = mock_feedback_instance

        await engine.initialize()

        assert engine.is_initialized
        assert "database" in engine.components
        assert "audit" in engine.components
        assert "plugin_registry" in engine.components
        assert "feedback_manager" in engine.components
        assert "explainable_ai" in engine.components

    @pytest.mark.asyncio
    async def test_shutdown(self, engine):
        """Test engine shutdown"""
        # Create mock components
        mock_component1 = Mock()
        mock_component1.shutdown = AsyncMock()
        mock_component2 = Mock()
        mock_component2.shutdown = AsyncMock()

        engine._is_initialized = True
        engine.components = {"comp1": mock_component1, "comp2": mock_component2}
        engine.component_locks = {"comp1": asyncio.Lock(), "comp2": asyncio.Lock()}

        await engine.shutdown()

        assert not engine.is_initialized
        assert engine.components == {}

    @pytest.mark.asyncio
    async def test_shutdown_not_initialized(self, engine):
        """Test shutdown when not initialized"""
        engine._is_initialized = False

        await engine.shutdown()

        # Should return early
        assert not engine.is_initialized

    @pytest.mark.asyncio
    async def test_get_component(self, engine):
        """Test getting a component"""
        mock_component = Mock()
        engine.components["test"] = mock_component

        result = await engine.get_component("test")
        assert result == mock_component

        result = await engine.get_component("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_health_check_all(self, engine):
        """Test health check for all components"""
        mock_comp1 = Mock()
        mock_comp1.health_check = AsyncMock(return_value={"status": "ok"})

        mock_comp2 = Mock()
        mock_comp2.health_check = AsyncMock(return_value={"status": "unhealthy"})

        engine.components = {"comp1": mock_comp1, "comp2": mock_comp2}

        result = await engine.health_check_all()

        assert result["comp1"]["status"] == "ok"
        assert result["comp2"]["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_all_with_error(self, engine):
        """Test health check with component error"""
        mock_comp = Mock()
        mock_comp.health_check = AsyncMock(side_effect=Exception("Health check failed"))

        engine.components = {"comp": mock_comp}

        result = await engine.health_check_all()

        assert result["comp"]["status"] == "error"
        assert "Health check failed" in result["comp"]["message"]

    @pytest.mark.asyncio
    async def test_perform_task_with_plugin(self, engine):
        """Test performing task with available plugin"""
        mock_plugin = Mock()
        mock_plugin.execute = AsyncMock(return_value="task_result")

        with patch("omnicore_engine.scenario_plugin_manager.PLUGIN_REGISTRY") as mock_registry:
            mock_registry.get_plugin_for_task = Mock(return_value=mock_plugin)

            result = await engine.perform_task("test_task", param1="value1")

            assert result == "task_result"
            mock_plugin.execute.assert_called_once_with(action="test_task", param1="value1")

    @pytest.mark.asyncio
    async def test_perform_task_no_plugin(self, engine):
        """Test performing task without available plugin"""
        with patch("omnicore_engine.scenario_plugin_manager.PLUGIN_REGISTRY") as mock_registry:
            mock_registry.get_plugin_for_task = Mock(return_value=None)

            result = await engine.perform_task("test_task")

            assert result is None

    @pytest.mark.asyncio
    async def test_perform_task_with_error(self, engine):
        """Test performing task with plugin error"""
        mock_plugin = Mock()
        mock_plugin.execute = AsyncMock(side_effect=Exception("Plugin error"))

        with patch("omnicore_engine.scenario_plugin_manager.PLUGIN_REGISTRY") as mock_registry:
            mock_registry.get_plugin_for_task = Mock(return_value=mock_plugin)

            result = await engine.perform_task("test_task")

            assert result is None


class TestComponentLifecycle:
    """Test component initialization and shutdown lifecycle"""

    @pytest.fixture
    def engine(self):
        """Create engine with mock settings"""
        settings = Mock()
        return OmniCoreEngine(settings)

    @pytest.mark.asyncio
    async def test_initialize_component_instance(self, engine):
        """Test initializing a component instance"""
        mock_component_class = Mock()
        mock_instance = Mock()
        mock_instance.initialize = AsyncMock()
        mock_component_class.return_value = mock_instance

        result = await engine._initialize_component_instance(
            "test_component", mock_component_class, arg1="value1"
        )

        assert result == mock_instance
        assert "test_component" in engine.components
        assert "test_component" in engine.component_locks
        mock_instance.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_component_already_exists(self, engine):
        """Test initializing already existing component"""
        existing_component = Mock()
        engine.components["test_component"] = existing_component
        engine.component_locks["test_component"] = asyncio.Lock()

        mock_component_class = Mock()

        result = await engine._initialize_component_instance("test_component", mock_component_class)

        assert result == existing_component
        mock_component_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_component_instance(self, engine):
        """Test shutting down a component instance"""
        mock_component = Mock()
        mock_component.shutdown = AsyncMock()

        engine.components["test_component"] = mock_component
        engine.component_locks["test_component"] = asyncio.Lock()

        await engine._shutdown_component_instance("test_component")

        assert "test_component" not in engine.components
        mock_component.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_nonexistent_component(self, engine):
        """Test shutting down non-existent component"""
        await engine._shutdown_component_instance("nonexistent")

        # Should complete without error


class TestGlobalSingleton:
    """Test the global omnicore_engine singleton"""

    def test_singleton_exists(self):
        """Test that global singleton exists"""
        assert omnicore_engine is not None
        assert isinstance(omnicore_engine, OmniCoreEngine)

    def test_singleton_has_settings(self):
        """Test that singleton has settings"""
        assert hasattr(omnicore_engine, "settings")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
