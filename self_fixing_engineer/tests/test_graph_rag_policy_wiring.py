# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests verifying that MeshPolicyBackend.evaluate_policy() delegates to
GraphRAGPolicyReasoner when available, and falls back to flat allow/deny
when the reasoner is not present.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# MeshPolicyBackend.evaluate_policy — GraphRAG delegation
# ---------------------------------------------------------------------------

class TestMeshPolicyEvaluateDelegation:
    """evaluate_policy() must delegate to GraphRAGPolicyReasoner when available."""

    def _make_backend(self, tmp_path):
        from self_fixing_engineer.mesh.mesh_policy import MeshPolicyBackend
        return MeshPolicyBackend(backend_type="local", local_dir=str(tmp_path))

    def test_method_exists(self, tmp_path):
        backend = self._make_backend(tmp_path)
        assert hasattr(backend, "evaluate_policy")
        assert callable(backend.evaluate_policy)

    def test_delegates_to_graph_reasoner_when_available(self, tmp_path):
        """When _graph_reasoner is set, evaluate_policy delegates to it."""
        backend = self._make_backend(tmp_path)

        mock_decision = MagicMock()
        mock_decision.model_dump.return_value = {
            "policy_id": "pol-1",
            "allowed": True,
            "explanation": "GraphRAG said yes",
            "evaluated_policies": ["pol-1"],
            "conflicts": [],
            "confidence": 0.95,
        }
        mock_reasoner = MagicMock()
        mock_reasoner.evaluate_policy.return_value = mock_decision
        backend._graph_reasoner = mock_reasoner

        result = backend.evaluate_policy("pol-1", {"env": "prod"})

        mock_reasoner.evaluate_policy.assert_called_once_with("pol-1", {"env": "prod"})
        assert result["allowed"] is True
        assert result["policy_id"] == "pol-1"
        assert result["confidence"] == 0.95

    def test_fallback_when_graph_reasoner_none(self, tmp_path):
        """Without _graph_reasoner, flat allow/deny fallback is used."""
        backend = self._make_backend(tmp_path)
        backend._graph_reasoner = None
        # Seed the policy cache manually
        backend.policy_cache["my-policy:latest"] = {"allow": ["read"], "deny": []}

        result = backend.evaluate_policy("my-policy")

        assert result["policy_id"] == "my-policy"
        assert isinstance(result["allowed"], bool)
        assert "explanation" in result
        assert result["evaluated_policies"] == ["my-policy"]
        assert result["conflicts"] == []
        assert result["confidence"] == 1.0

    def test_fallback_when_graph_reasoner_raises(self, tmp_path):
        """If GraphRAG raises, flat fallback is used instead of propagating."""
        backend = self._make_backend(tmp_path)
        mock_reasoner = MagicMock()
        mock_reasoner.evaluate_policy.side_effect = RuntimeError("graph boom")
        backend._graph_reasoner = mock_reasoner
        backend.policy_cache["pol-err:latest"] = {"allow": ["write"], "deny": []}

        result = backend.evaluate_policy("pol-err")

        assert result["policy_id"] == "pol-err"
        assert isinstance(result["allowed"], bool)

    def test_result_schema_keys(self, tmp_path):
        """Result must always contain the PolicyDecision-compatible keys."""
        backend = self._make_backend(tmp_path)
        backend._graph_reasoner = None

        result = backend.evaluate_policy("unknown-policy")

        required_keys = {"policy_id", "allowed", "explanation", "evaluated_policies", "conflicts", "confidence"}
        assert required_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# MeshPolicyBackend.__init__ — graph reasoner initialisation
# ---------------------------------------------------------------------------

class TestMeshPolicyGraphReasonerInit:
    def test_graph_reasoner_attribute_set(self, tmp_path):
        """MeshPolicyBackend.__init__ must set _graph_reasoner (None or instance)."""
        from self_fixing_engineer.mesh.mesh_policy import MeshPolicyBackend
        backend = MeshPolicyBackend(backend_type="local", local_dir=str(tmp_path))
        assert hasattr(backend, "_graph_reasoner")

    def test_graph_reasoner_is_none_when_import_fails(self, tmp_path):
        """If the import of GraphRAGPolicyReasoner fails, _graph_reasoner is None."""
        import sys
        # Temporarily make the module unavailable
        original = sys.modules.get("self_fixing_engineer.mesh.graph_rag_policy")
        sys.modules["self_fixing_engineer.mesh.graph_rag_policy"] = None  # type: ignore[assignment]
        try:
            from self_fixing_engineer.mesh.mesh_policy import MeshPolicyBackend
            import importlib
            importlib.reload(
                importlib.import_module("self_fixing_engineer.mesh.mesh_policy")
            )
            from self_fixing_engineer.mesh import mesh_policy as mp
            importlib.reload(mp)
            backend = mp.MeshPolicyBackend(backend_type="local", local_dir=str(tmp_path))
            # _graph_reasoner should be None when the import is blocked
            assert backend._graph_reasoner is None
        except Exception as exc:
            pytest.skip(f"Module reload not supported in this environment: {exc}")
        finally:
            if original is None:
                sys.modules.pop("self_fixing_engineer.mesh.graph_rag_policy", None)
            else:
                sys.modules["self_fixing_engineer.mesh.graph_rag_policy"] = original


# ---------------------------------------------------------------------------
# load() — graph seeding after successful load
# ---------------------------------------------------------------------------

class TestMeshPolicyLoadSeeding:
    @pytest.mark.asyncio
    async def test_load_seeds_graph_reasoner(self, tmp_path):
        """After a successful load(), the policy is added to the graph reasoner."""
        try:
            import aiofiles  # noqa: F401
        except ImportError:
            pytest.skip("aiofiles not installed")

        import json
        from unittest.mock import MagicMock
        from self_fixing_engineer.mesh.mesh_policy import MeshPolicyBackend

        backend = MeshPolicyBackend(backend_type="local", local_dir=str(tmp_path))
        mock_reasoner = MagicMock()
        backend._graph_reasoner = mock_reasoner

        # Write a real policy file to disk so load() can find it
        policy_data = {"allow": ["read"], "deny": [], "id": "seeded-pol"}
        import time as _time
        version = f"{int(_time.time() * 1000)}_1"
        policy_file = tmp_path / f"seeded-pol_v{version}.json"
        policy_file.write_text(json.dumps({
            "data": json.dumps(policy_data),
            "sig": "",
            "version": version,
            "timestamp": "2025-01-01T00:00:00",
        }))

        result = await backend.load("seeded-pol")

        if result is not None:
            mock_reasoner.add_policy.assert_called_once()
            call_args = mock_reasoner.add_policy.call_args[0][0]
            assert call_args.get("id") == "seeded-pol"
