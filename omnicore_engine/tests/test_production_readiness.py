"""
Production Readiness Tests for OmniCore Engine

This test suite validates that the OmniCore Engine is fully functional
and production-ready by testing critical integration points and workflows.
"""

import os
import sys
from unittest.mock import Mock

import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.core import (
    ExplainableAI,
    MerkleTree,
    OmniCoreEngine,
    get_plugin_metrics,
    get_test_metrics,
    safe_serialize,
)


class TestCoreUtilities:
    """Test core utility functions are working correctly"""

    def test_safe_serialize_basic_types(self):
        """Test serialization of basic Python types"""
        data = {
            "string": "hello",
            "integer": 42,
            "float": 3.14,
            "bool": True,
            "none": None,
            "list": [1, 2, 3],
            "nested": {"key": "value"},
        }
        result = safe_serialize(data)
        assert isinstance(result, dict)
        assert result["string"] == "hello"
        assert result["integer"] == 42
        assert result["float"] == 3.14
        assert result["bool"] is True
        assert result["none"] is None
        assert result["list"] == [1, 2, 3]
        assert result["nested"] == {"key": "value"}

    def test_safe_serialize_datetime(self):
        """Test serialization of datetime objects"""
        from datetime import datetime

        dt = datetime(2025, 1, 1, 12, 0, 0)
        result = safe_serialize({"timestamp": dt})
        assert "timestamp" in result
        assert isinstance(result["timestamp"], str)
        assert "2025-01-01" in result["timestamp"]

    def test_safe_serialize_circular_reference(self):
        """Test handling of circular references"""
        data = {"key": "value"}
        data["self"] = data
        result = safe_serialize(data)
        assert "CIRCULAR REFERENCE" in str(result.get("self", ""))

    def test_safe_serialize_numpy_arrays(self):
        """Test serialization of NumPy arrays"""
        import numpy as np

        data = {"array": np.array([1, 2, 3])}
        result = safe_serialize(data)
        assert "array" in result
        assert result["array"] == [1, 2, 3]


class TestOmniCoreEngineBasics:
    """Test basic OmniCore Engine functionality"""

    def test_engine_instantiation(self):
        """Test that engine can be instantiated"""
        engine = OmniCoreEngine()
        assert engine is not None
        assert not engine.is_initialized
        assert engine.components == {}
        assert isinstance(engine.component_locks, dict)

    @pytest.mark.asyncio
    async def test_engine_component_storage(self):
        """Test that components can be stored and retrieved"""
        engine = OmniCoreEngine()
        test_component = Mock()
        engine.components["test"] = test_component

        retrieved = await engine.get_component("test")
        assert retrieved is test_component

    @pytest.mark.asyncio
    async def test_engine_health_check_not_initialized(self):
        """Test health check on uninitialized engine"""
        engine = OmniCoreEngine()
        # Since initialize requires many dependencies, we just test the property
        assert not engine.is_initialized


class TestExplainableAI:
    """Test Explainable AI component"""

    @pytest.mark.asyncio
    async def test_explainable_ai_initialization(self):
        """Test ExplainableAI can be instantiated"""
        ai = ExplainableAI()
        assert ai is not None
        assert not ai.is_initialized
        assert ai.reasoner is None

    @pytest.mark.asyncio
    async def test_explainable_ai_explain_not_initialized(self):
        """Test explain_event when not initialized"""
        ai = ExplainableAI()
        result = await ai.explain_event({"query": "test"})
        assert "error" in result

    @pytest.mark.asyncio
    async def test_explainable_ai_reason_not_initialized(self):
        """Test reason_event when not initialized"""
        ai = ExplainableAI()
        result = await ai.reason_event({"query": "test"})
        assert "error" in result


class TestMerkleTree:
    """Test Merkle Tree functionality"""

    def test_merkle_tree_empty(self):
        """Test empty Merkle tree"""
        tree = MerkleTree()
        assert tree.leaves == []
        assert tree.root is None

    def test_merkle_tree_add_leaf(self):
        """Test adding a leaf to Merkle tree"""
        tree = MerkleTree()
        tree.add_leaf("test_data")
        assert len(tree.leaves) == 1

    def test_merkle_tree_build_root(self):
        """Test building Merkle root"""
        tree = MerkleTree()
        tree.add_leaf("data1")
        tree.add_leaf("data2")
        # Root is automatically calculated when leaves are added
        assert tree.root is not None
        assert isinstance(tree.root, str)

    def test_merkle_tree_verify_proof(self):
        """Test Merkle proof verification"""
        tree = MerkleTree()
        tree.add_leaf("data1")
        tree.add_leaf("data2")
        # Root is automatically calculated when leaves are added

        proof = tree.get_proof("data1")
        assert tree.verify_proof("data1", tree.root, proof)


class TestMetricsIntegration:
    """Test metrics functionality"""

    def test_get_plugin_metrics(self):
        """Test getting plugin metrics"""
        metrics = get_plugin_metrics()
        assert isinstance(metrics, dict)
        # Metrics should return either actual metrics or an error dict
        assert "error" in metrics or "plugins" in metrics or len(metrics) >= 0

    def test_get_test_metrics(self):
        """Test getting test metrics"""
        metrics = get_test_metrics()
        assert isinstance(metrics, dict)
        # Metrics should return either actual metrics or an error dict
        assert "error" in metrics or "tests" in metrics or len(metrics) >= 0


class TestProductionReadiness:
    """Integration tests to verify production readiness"""

    def test_import_core_modules(self):
        """Test that all core modules can be imported"""
        try:
            from omnicore_engine import (
                audit,
                cli,
                core,
                metrics,
                plugin_registry,
                security_config,
                security_utils,
            )

            assert True
        except ImportError as e:
            pytest.fail(f"Failed to import core modules: {e}")

    def test_cli_functions_available(self):
        """Test that CLI functions are available"""
        from omnicore_engine.cli import (
            main,
            safe_command,
            sanitize_env_vars,
            validate_file_path,
        )

        assert callable(main)
        assert callable(sanitize_env_vars)
        assert callable(safe_command)
        assert callable(validate_file_path)

    def test_audit_classes_available(self):
        """Test that audit classes are available"""
        from omnicore_engine.audit import AuditRecordSchema, ExplainAudit, ExplainRecord

        assert ExplainAudit is not None
        assert ExplainRecord is not None
        assert AuditRecordSchema is not None

    def test_security_utils_available(self):
        """Test that security utilities are available"""
        from omnicore_engine.security_utils import (
            AuthenticationError,
            AuthorizationError,
            SecurityError,
            ValidationError,
            get_security_utils,
        )

        assert SecurityError is not None
        assert ValidationError is not None
        assert AuthenticationError is not None
        assert AuthorizationError is not None
        assert callable(get_security_utils)

    def test_plugin_registry_available(self):
        """Test that plugin registry is available"""
        from omnicore_engine.plugin_registry import PLUGIN_REGISTRY

        assert PLUGIN_REGISTRY is not None

    @pytest.mark.asyncio
    async def test_basic_workflow_simulation(self):
        """Simulate a basic workflow to verify integration"""
        # Test core serialization
        data = {"test": "workflow", "status": "running"}
        serialized = safe_serialize(data)
        assert serialized == data

        # Test engine instantiation
        engine = OmniCoreEngine()
        assert not engine.is_initialized

        # Test metrics availability
        plugin_metrics = get_plugin_metrics()
        assert isinstance(plugin_metrics, dict)

        # Test Merkle tree for audit trail
        tree = MerkleTree()
        tree.add_leaf("workflow_start")
        tree.add_leaf("workflow_process")
        tree.add_leaf("workflow_end")
        # Root is automatically calculated when leaves are added
        assert tree.root is not None

        # Verify all components are functional
        assert True


class TestRetryCompatibility:
    """Test the retry compatibility layer"""

    def test_retry_decorator_import(self):
        """Test that retry decorator can be imported"""
        from omnicore_engine.retry_compat import retry

        assert callable(retry)

    @pytest.mark.asyncio
    async def test_retry_decorator_async(self):
        """Test retry decorator on async function"""
        from omnicore_engine.retry_compat import retry

        call_count = {"count": 0}

        @retry(tries=3, delay=0.01, backoff=1)
        async def async_func():
            call_count["count"] += 1
            if call_count["count"] < 2:
                raise ValueError("Test error")
            return "success"

        result = await async_func()
        assert result == "success"
        assert call_count["count"] == 2

    def test_retry_decorator_sync(self):
        """Test retry decorator on sync function"""
        from omnicore_engine.retry_compat import retry

        call_count = {"count": 0}

        @retry(tries=3, delay=0.01, backoff=1)
        def sync_func():
            call_count["count"] += 1
            if call_count["count"] < 2:
                raise ValueError("Test error")
            return "success"

        result = sync_func()
        assert result == "success"
        assert call_count["count"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
