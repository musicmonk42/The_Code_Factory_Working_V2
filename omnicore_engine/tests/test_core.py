"""
Test suite for omnicore_engine/core.py
Tests the core orchestration engine, component initialization, and utility functions.
"""

import asyncio
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import numpy as np
import pytest

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defer heavy imports to test functions to reduce time during collection


class TestSafeSerialize:
    """Test the safe_serialize utility function"""

    def test_primitive_types(self):
        """Test serialization of primitive types"""
        from omnicore_engine.core import safe_serialize
        
        assert safe_serialize("string") == "string"
        assert safe_serialize(42) == 42
        assert safe_serialize(3.14) == 3.14
        assert safe_serialize(True) == True
        assert safe_serialize(None) is None

    def test_datetime_serialization(self):
        """Test datetime and date serialization"""
        from omnicore_engine.core import safe_serialize
        
        dt = datetime(2024, 1, 1, 12, 0, 0)
        assert safe_serialize(dt) == "2024-01-01T12:00:00"

        d = date(2024, 1, 1)
        assert safe_serialize(d) == "2024-01-01"

    def test_bytes_serialization(self):
        """Test bytes serialization"""
        from omnicore_engine.core import safe_serialize
        
        assert safe_serialize(b"hello") == "hello"
        assert safe_serialize(b"\x00\x01\x02") == "\x00\x01\x02"

    def test_collection_serialization(self):
        """Test serialization of collections"""
        from omnicore_engine.core import safe_serialize
        
        # Set and frozenset
        assert safe_serialize({1, 2, 3}) == [1, 2, 3]
        assert safe_serialize(frozenset([1, 2])) == [1, 2]

        # List and tuple
        assert safe_serialize([1, 2, 3]) == [1, 2, 3]
        assert safe_serialize((1, 2, 3)) == [1, 2, 3]

        # Dict
        assert safe_serialize({"key": "value"}) == {"key": "value"}

    def test_numpy_serialization(self):
        """Test NumPy array serialization"""
        from omnicore_engine.core import safe_serialize
        
        arr = np.array([1, 2, 3])
        assert safe_serialize(arr) == [1, 2, 3]

        scalar = np.float32(3.14)
        assert safe_serialize(scalar) == pytest.approx(3.14)

    def test_decimal_uuid_serialization(self):
        """Test Decimal and UUID serialization"""
        from omnicore_engine.core import safe_serialize
        
        d = Decimal("3.14159")
        assert safe_serialize(d) == pytest.approx(3.14159)

        u = UUID("12345678-1234-5678-1234-567812345678")
        assert safe_serialize(u) == "12345678-1234-5678-1234-567812345678"

    def test_circular_reference_handling(self):
        """Test handling of circular references"""
        from omnicore_engine.core import safe_serialize
        
        obj = {}
        obj["self"] = obj

        result = safe_serialize(obj)
        assert "<<<CIRCULAR REFERENCE" in str(result["self"])

    def test_model_dump_objects(self):
        """Test objects with model_dump method"""
        from omnicore_engine.core import safe_serialize
        
        mock_obj = Mock()
        mock_obj.model_dump.return_value = {"field": "value"}

        assert safe_serialize(mock_obj) == {"field": "value"}

    def test_dict_method_objects(self):
        """Test objects with dict method"""
        from omnicore_engine.core import safe_serialize
        
        mock_obj = Mock()
        mock_obj.dict.return_value = {"field": "value"}
        del mock_obj.model_dump  # Ensure model_dump doesn't exist

        assert safe_serialize(mock_obj) == {"field": "value"}

    def test_unserializable_objects(self):
        """Test handling of unserializable objects"""
        from omnicore_engine.core import safe_serialize
        
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
        from omnicore_engine.core import Base
        
        with pytest.raises(TypeError):
            Base()

    def test_base_subclass_implementation(self):
        """Test proper subclass implementation of Base"""
        from omnicore_engine.core import Base
        
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


class TestMetricsFunctions:
    """Test plugin and test metrics functions"""

    def test_get_plugin_metrics_success(self):
        """Test successful plugin metrics retrieval"""
        from omnicore_engine.core import get_plugin_metrics
        
        mock_metrics_func = Mock(return_value={"metric1": 10, "metric2": 20})
        mock_metrics_module = Mock(get_plugin_metrics=mock_metrics_func)

        with patch.dict(
            "sys.modules", {"omnicore_engine.metrics": mock_metrics_module}
        ):
            result = get_plugin_metrics()
            assert result == {"metric1": 10, "metric2": 20}

    def test_get_plugin_metrics_import_error(self):
        """Test plugin metrics when module not available"""
        from omnicore_engine.core import get_plugin_metrics
        
        with patch.dict("sys.modules", {"omnicore_engine.metrics": None}):
            result = get_plugin_metrics()
            assert "error" in result
            assert "Metrics module not available" in result["error"]

    def test_get_test_metrics_success(self):
        """Test successful test metrics retrieval"""
        from omnicore_engine.core import get_test_metrics
        
        mock_metrics_func = Mock(return_value={"tests_run": 100, "tests_passed": 95})
        mock_metrics_module = Mock(get_test_metrics=mock_metrics_func)

        with patch.dict(
            "sys.modules", {"omnicore_engine.metrics": mock_metrics_module}
        ):
            result = get_test_metrics()
            assert result == {"tests_run": 100, "tests_passed": 95}


class TestExplainableAI:
    """Test ExplainableAI class"""

    @pytest.mark.asyncio
    async def test_initialization_success(self):
        """Test successful initialization of ExplainableAI"""
        from omnicore_engine.core import ExplainableAI
        
        mock_reasoner = Mock()
        mock_reasoner.initialize = AsyncMock()
        mock_reasoner_class = Mock(return_value=mock_reasoner)
        mock_module = Mock(ExplainableReasonerPlugin=mock_reasoner_class)

        with patch.dict(
            "sys.modules", {"omnicore_engine.explainable_reasoner": mock_module}
        ):
            ai = ExplainableAI()
            await ai.initialize()

            assert ai.is_initialized
            assert ai.reasoner is not None
            mock_reasoner.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialization_import_error(self):
        """Test initialization when reasoner module not available"""
        from omnicore_engine.core import ExplainableAI
        
        with patch.dict("sys.modules", {"omnicore_engine.explainable_reasoner": None}):
            ai = ExplainableAI()
            await ai.initialize()

            assert not ai.is_initialized
            assert ai.reasoner is None

    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Test shutdown of ExplainableAI"""
        from omnicore_engine.core import ExplainableAI
        
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.shutdown = AsyncMock()

        await ai.shutdown()

        assert not ai.is_initialized
        ai.reasoner.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_explain_event_success(self):
        """Test successful event explanation"""
        from omnicore_engine.core import ExplainableAI
        
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.explain = AsyncMock(
            return_value={"explanation": "Test explanation"}
        )

        result = await ai.explain_event({"query": "test", "context": {}})

        assert result == {"explanation": "Test explanation"}

    @pytest.mark.asyncio
    async def test_explain_event_not_initialized(self):
        """Test explain_event when not initialized"""
        from omnicore_engine.core import ExplainableAI
        
        ai = ExplainableAI()
        ai.is_initialized = False

        result = await ai.explain_event({"query": "test"})

        assert "error" in result
        assert "not available" in result["error"]

    @pytest.mark.asyncio
    async def test_reason_event_success(self):
        """Test successful event reasoning"""
        from omnicore_engine.core import ExplainableAI
        
        ai = ExplainableAI()
        ai.is_initialized = True
        ai.reasoner = Mock()
        ai.reasoner.reason = AsyncMock(return_value={"reasoning": "Test reasoning"})

        result = await ai.reason_event({"query": "test", "context": {}})

        assert result == {"reasoning": "Test reasoning"}


class TestMerkleTree:
    """Test MerkleTree class"""

    def test_empty_tree(self):
        """Test empty Merkle tree"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()
        assert tree.root is None
        assert tree.leaves == []

    def test_add_single_leaf(self):
        """Test adding a single leaf"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()
        tree.add_leaf(b"test_data")

        assert len(tree.leaves) == 1
        assert tree.root is not None
        assert tree.root == tree.leaves[0]

    def test_add_multiple_leaves(self):
        """Test adding multiple leaves"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()

        for i in range(4):
            tree.add_leaf(f"data_{i}".encode())

        assert len(tree.leaves) == 4
        assert tree.root is not None

    def test_verify_proof_valid(self):
        """Test proof verification with valid proof"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()

        leaf_data = b"test_leaf"
        tree.add_leaf(leaf_data)
        tree.add_leaf(b"another_leaf")

        proof = tree.get_proof(leaf_data)
        assert tree.verify_proof(leaf_data, tree.root, proof)

    def test_verify_proof_invalid(self):
        """Test proof verification with invalid proof"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()

        tree.add_leaf(b"leaf1")
        tree.add_leaf(b"leaf2")

        # Invalid proof
        assert not tree.verify_proof(b"invalid_leaf", tree.root, [])

    def test_get_proof_leaf_not_found(self):
        """Test get_proof with non-existent leaf"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()
        tree.add_leaf(b"existing_leaf")

        with pytest.raises(ValueError, match="Leaf not found"):
            tree.get_proof(b"non_existent_leaf")

    def test_get_root_empty_tree(self):
        """Test get_root() returns empty string for empty tree"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()
        assert tree.get_root() == ""

    def test_get_root_with_data(self):
        """Test get_root() returns root hash when tree has data"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()
        tree.add_leaf(b"test_data")

        root = tree.get_root()
        assert root != ""
        assert isinstance(root, str)
        assert root == tree.root
        assert root == tree.get_merkle_root()

    def test_get_root_matches_get_merkle_root(self):
        """Test get_root() is equivalent to get_merkle_root()"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()

        # Empty tree
        assert tree.get_root() == tree.get_merkle_root()

        # Add leaves
        for i in range(5):
            tree.add_leaf(f"data_{i}".encode())
            assert tree.get_root() == tree.get_merkle_root()
            assert tree.get_root() == tree.root

    def test_get_root_returns_hex_string(self):
        """Test get_root() returns a valid hex string"""
        from omnicore_engine.core import MerkleTree
        
        tree = MerkleTree()
        tree.add_leaf(b"test_data")

        root = tree.get_root()
        # Should be a hex string (can be decoded from hex)
        try:
            bytes.fromhex(root)
            is_hex = True
        except ValueError:
            is_hex = False

        assert is_hex, "get_root() should return a hex string"


class TestOmniCoreEngine:
    """Test OmniCoreEngine class"""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings"""
        settings = Mock()
        settings.database_path = "sqlite:///:memory:"
        settings.redis_url = "redis://localhost"
        settings.plugin_dir = "/tmp/plugins"
        settings.encryption_key = Mock(get_secret_value=lambda: "test_key")
        settings.encryption_key_bytes = b"test_key_bytes"
        return settings

    @pytest.fixture
    def engine(self, mock_settings):
        """Create engine instance with mock settings"""
        from omnicore_engine.core import OmniCoreEngine
        
        return OmniCoreEngine(mock_settings)

    def test_initialization_state(self, engine):
        """Test initial state of engine"""
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
    async def test_component_initialization(self, engine):
        """Test component initialization helper"""
        mock_component_class = Mock()
        mock_instance = Mock()
        mock_instance.initialize = AsyncMock()
        mock_component_class.return_value = mock_instance

        await engine._initialize_component_instance(
            "test_component", mock_component_class, arg1="value1"
        )

        assert "test_component" in engine.components
        assert engine.components["test_component"] == mock_instance
        mock_instance.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_component_already_initialized(self, engine):
        """Test initializing already initialized component"""
        mock_instance = Mock()
        engine.components["test_component"] = mock_instance
        engine.component_locks["test_component"] = asyncio.Lock()

        mock_component_class = Mock()

        await engine._initialize_component_instance(
            "test_component", mock_component_class
        )

        # Should not create new instance
        mock_component_class.assert_not_called()
        assert engine.components["test_component"] == mock_instance

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
    async def test_shutdown(self, engine):
        """Test engine shutdown"""
        mock_component1 = Mock()
        mock_component1.shutdown = AsyncMock()
        mock_component2 = Mock()
        mock_component2.shutdown = AsyncMock()

        engine._is_initialized = True
        engine.components = {"comp1": mock_component1, "comp2": mock_component2}

        await engine.shutdown()

        assert not engine._is_initialized
        mock_component1.shutdown.assert_called_once()
        mock_component2.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_not_initialized(self, engine):
        """Test shutdown when not initialized"""
        engine._is_initialized = False
        mock_component = Mock()
        mock_component.shutdown = AsyncMock()
        engine.components = {"comp": mock_component}

        await engine.shutdown()

        # Should return early
        mock_component.shutdown.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check(self, engine):
        """Test health check"""
        mock_comp1 = Mock()
        mock_comp1.health_check = AsyncMock(return_value={"status": "ok"})

        mock_comp2 = Mock()
        mock_comp2.health_check = AsyncMock(return_value={"status": "unhealthy"})

        # Create a mock without health_check using spec
        mock_comp3 = Mock(spec=["some_other_method"])

        engine.components = {
            "comp1": mock_comp1,
            "comp2": mock_comp2,
            "comp3": mock_comp3,
        }

        result = await engine.health_check()

        assert result["overall_status"] == "unhealthy"
        assert result["comp1"]["status"] == "ok"
        assert result["comp2"]["status"] == "unhealthy"
        assert result["comp3"]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_health_check_exception(self, engine):
        """Test health check with exception"""
        mock_comp = Mock()
        mock_comp.health_check = AsyncMock(side_effect=Exception("Test error"))

        engine.components = {"comp": mock_comp}

        result = await engine.health_check()

        assert result["overall_status"] == "unhealthy"
        assert result["comp"]["status"] == "error"
        assert "Test error" in result["comp"]["message"]

    def test_is_healthy_property(self, engine):
        """Test is_healthy property"""
        # Not initialized
        assert not engine.is_healthy

        # Initialized with healthy components
        engine._is_initialized = True
        mock_comp = Mock()
        mock_comp.is_healthy = True
        engine.components = {"comp": mock_comp}

        assert engine.is_healthy

        # Initialized with unhealthy component
        mock_comp.is_healthy = False
        assert not engine.is_healthy

    @pytest.mark.asyncio
    async def test_handle_shutdown_message(self, engine):
        """Test handling shutdown message"""
        engine.shutdown = AsyncMock()

        await engine._handle_shutdown({"reason": "test shutdown"})

        engine.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_config_change_message(self, engine):
        """Test handling config change message"""
        engine.shutdown = AsyncMock()
        engine.initialize = AsyncMock()

        await engine._handle_config_change({"changes": "test changes"})

        engine.shutdown.assert_called_once()
        engine.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_system_error_message(self, engine):
        """Test handling system error message"""
        await engine._handle_system_error({"error": "test error", "severity": 5})

        # Should log error (check via logger mock if needed)


class TestGlobalSingleton:
    """Test the global omnicore_engine singleton"""

    def test_singleton_exists(self):
        """Test that global singleton exists"""
        from omnicore_engine.core import OmniCoreEngine, omnicore_engine
        
        assert omnicore_engine is not None
        assert isinstance(omnicore_engine, OmniCoreEngine)

    def test_singleton_has_settings(self):
        """Test that singleton has settings"""
        from omnicore_engine.core import omnicore_engine
        
        assert hasattr(omnicore_engine, "settings")


class TestLoggingConfiguration:
    """Test logging configuration"""

    @patch("omnicore_engine.core.structlog.configure")
    @patch("omnicore_engine.core.logging.basicConfig")
    def test_configure_logging(self, mock_basic_config, mock_structlog_configure):
        """Test logging configuration"""
        from omnicore_engine.core import configure_logging
        
        configure_logging()

        mock_structlog_configure.assert_called_once()
        mock_basic_config.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
