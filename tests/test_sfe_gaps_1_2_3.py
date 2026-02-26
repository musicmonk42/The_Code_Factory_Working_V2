# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unit tests for the new Gap 1/2/3 functionality:
- FileProvenanceRegistry (file_provenance.py)
- Arbiter._invoke_sfe_fix_pipeline()
- Arbiter.validate_generated_code_in_sandbox()
- ArbiterBridge.publish_event() file_paths enrichment
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# FileProvenanceRegistry tests
# ---------------------------------------------------------------------------


class TestFileProvenanceRegistry:
    """Tests for the new FileProvenanceRegistry class."""

    @pytest.mark.asyncio
    async def test_register_and_is_generated(self, tmp_path):
        """register_generated_file should make is_generated return True."""
        from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

        registry = FileProvenanceRegistry(
            provenance_path=str(tmp_path / "provenance.json")
        )
        await registry.initialize()

        await registry.register_generated_file(
            "/src/foo.py",
            {"generator_id": "gen-1", "language": "python"},
        )

        assert await registry.is_generated("/src/foo.py") is True
        assert await registry.is_generated("/src/bar.py") is False

    @pytest.mark.asyncio
    async def test_get_provenance(self, tmp_path):
        """get_provenance should return the registered metadata."""
        from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

        registry = FileProvenanceRegistry(
            provenance_path=str(tmp_path / "provenance.json")
        )
        await registry.initialize()

        meta = {"generator_id": "gen-2", "language": "python", "workflow_id": "wf-99"}
        await registry.register_generated_file("/src/baz.py", meta)

        prov = await registry.get_provenance("/src/baz.py")
        assert prov is not None
        assert prov["generator_id"] == "gen-2"
        assert prov["workflow_id"] == "wf-99"

    @pytest.mark.asyncio
    async def test_list_generated_files(self, tmp_path):
        """list_generated_files should return all registered entries."""
        from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

        registry = FileProvenanceRegistry(
            provenance_path=str(tmp_path / "provenance.json")
        )
        await registry.initialize()

        await registry.register_generated_file("/a.py", {"generator_id": "g1"})
        await registry.register_generated_file("/b.py", {"generator_id": "g2"})

        files = await registry.list_generated_files()
        paths = [f["file_path"] for f in files]
        assert "/a.py" in paths
        assert "/b.py" in paths

    @pytest.mark.asyncio
    async def test_mark_validated(self, tmp_path):
        """mark_validated should update the validated flag."""
        from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

        registry = FileProvenanceRegistry(
            provenance_path=str(tmp_path / "provenance.json")
        )
        await registry.initialize()

        await registry.register_generated_file("/c.py", {"generator_id": "g3"})
        prov_before = await registry.get_provenance("/c.py")
        assert not prov_before.get("validated")

        await registry.mark_validated("/c.py")
        prov_after = await registry.get_provenance("/c.py")
        assert prov_after["validated"] is True

    @pytest.mark.asyncio
    async def test_get_generated_files_needing_review(self, tmp_path):
        """get_generated_files_needing_review returns only unvalidated files."""
        from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

        registry = FileProvenanceRegistry(
            provenance_path=str(tmp_path / "provenance.json")
        )
        await registry.initialize()

        await registry.register_generated_file("/x.py", {"generator_id": "gx"})
        await registry.register_generated_file("/y.py", {"generator_id": "gy"})
        await registry.mark_validated("/y.py")

        needing_review = await registry.get_generated_files_needing_review()
        paths = [f["file_path"] for f in needing_review]
        assert "/x.py" in paths
        assert "/y.py" not in paths

    @pytest.mark.asyncio
    async def test_json_persistence(self, tmp_path):
        """Provenance records should survive a restart via JSON persistence."""
        from self_fixing_engineer.arbiter.file_provenance import FileProvenanceRegistry

        prov_file = str(tmp_path / "provenance.json")
        r1 = FileProvenanceRegistry(provenance_path=prov_file)
        await r1.initialize()
        await r1.register_generated_file("/persist.py", {"generator_id": "gp"})
        # Write to disk explicitly
        await r1._persist_to_json()

        # New instance loads from disk
        r2 = FileProvenanceRegistry(provenance_path=prov_file)
        await r2.initialize()
        assert await r2.is_generated("/persist.py") is True


# ---------------------------------------------------------------------------
# ArbiterBridge.publish_event enrichment tests
# ---------------------------------------------------------------------------


class TestArbiterBridgePublishEventEnrichment:
    """Verify that generator_output events include file_paths provenance data."""

    @pytest.mark.asyncio
    async def test_generator_output_file_paths_injected(self):
        """When file_paths is absent, bridge should derive from file_path field."""
        captured: list = []

        async def mock_publish(topic, message):
            captured.append(message)

        with patch("generator.arbiter_bridge.MessageQueueService") as mock_mqs_cls, \
             patch("generator.arbiter_bridge.PolicyEngine"), \
             patch("generator.arbiter_bridge.KnowledgeGraph"), \
             patch("generator.arbiter_bridge.BugManager"), \
             patch("generator.arbiter_bridge.HumanInLoop"):
            mock_mqs = MagicMock()
            mock_mqs.publish = mock_publish
            mock_mqs_cls.return_value = mock_mqs

            from generator.arbiter_bridge import ArbiterBridge

            bridge = ArbiterBridge(message_queue=mock_mqs)
            await bridge.publish_event(
                "generator_output",
                {
                    "code": "print('hello')",
                    "language": "python",
                    "generator_id": "gen-001",
                    "file_path": "/src/generated_hello.py",
                },
            )

        assert len(captured) == 1
        msg = captured[0]
        assert "file_paths" in msg
        assert "/src/generated_hello.py" in msg["file_paths"]

    @pytest.mark.asyncio
    async def test_generator_output_file_paths_passthrough(self):
        """When file_paths is already present, bridge should not overwrite it."""
        captured: list = []

        async def mock_publish(topic, message):
            captured.append(message)

        with patch("generator.arbiter_bridge.MessageQueueService") as mock_mqs_cls, \
             patch("generator.arbiter_bridge.PolicyEngine"), \
             patch("generator.arbiter_bridge.KnowledgeGraph"), \
             patch("generator.arbiter_bridge.BugManager"), \
             patch("generator.arbiter_bridge.HumanInLoop"):
            mock_mqs = MagicMock()
            mock_mqs.publish = mock_publish
            mock_mqs_cls.return_value = mock_mqs

            from generator.arbiter_bridge import ArbiterBridge

            bridge = ArbiterBridge(message_queue=mock_mqs)
            await bridge.publish_event(
                "generator_output",
                {
                    "code": "pass",
                    "language": "python",
                    "file_paths": ["/src/a.py", "/src/b.py"],
                },
            )

        assert len(captured) == 1
        msg = captured[0]
        assert msg["file_paths"] == ["/src/a.py", "/src/b.py"]

    @pytest.mark.asyncio
    async def test_non_generator_event_unchanged(self):
        """Non-generator_output events should not have file_paths injected."""
        captured: list = []

        async def mock_publish(topic, message):
            captured.append(message)

        with patch("generator.arbiter_bridge.MessageQueueService") as mock_mqs_cls, \
             patch("generator.arbiter_bridge.PolicyEngine"), \
             patch("generator.arbiter_bridge.KnowledgeGraph"), \
             patch("generator.arbiter_bridge.BugManager"), \
             patch("generator.arbiter_bridge.HumanInLoop"):
            mock_mqs = MagicMock()
            mock_mqs.publish = mock_publish
            mock_mqs_cls.return_value = mock_mqs

            from generator.arbiter_bridge import ArbiterBridge

            bridge = ArbiterBridge(message_queue=mock_mqs)
            await bridge.publish_event(
                "workflow_completed",
                {"workflow_id": "wf-1"},
            )

        assert len(captured) == 1
        msg = captured[0]
        # file_paths should NOT have been injected for non-generator_output events
        assert "file_paths" not in msg


# ---------------------------------------------------------------------------
# Arbiter._invoke_sfe_fix_pipeline tests
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Helpers for arbiter method tests (need to bypass PYTEST_COLLECTING guard)
# ---------------------------------------------------------------------------

def _get_real_arbiter_class():
    """Import the real Arbiter class, bypassing the PYTEST_COLLECTING stub guard."""
    import importlib
    import os
    import sys

    saved = os.environ.pop("PYTEST_COLLECTING", None)
    try:
        # Evict cached stub module so we get the real one
        sys.modules.pop("self_fixing_engineer.arbiter.arbiter", None)
        mod = importlib.import_module("self_fixing_engineer.arbiter.arbiter")
        return mod.Arbiter
    except Exception:
        return None
    finally:
        if saved is not None:
            os.environ["PYTEST_COLLECTING"] = saved
        # Re-evict to avoid polluting later imports
        sys.modules.pop("self_fixing_engineer.arbiter.arbiter", None)


def _make_minimal_arbiter(ArbiterCls):
    """Create a bare-bones Arbiter instance with only the required attributes."""
    arbiter = object.__new__(ArbiterCls)
    arbiter.name = "test-arbiter"
    arbiter._arena_ref = None
    arbiter.simulation_engine = None
    arbiter.decision_optimizer = None
    arbiter.knowledge_graph = None
    arbiter.generator_engine = None
    return arbiter


# ---------------------------------------------------------------------------
# Arbiter._invoke_sfe_fix_pipeline tests
# ---------------------------------------------------------------------------


class TestInvokeSfeFixPipeline:
    """Tests for _invoke_sfe_fix_pipeline arbiter method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_defects(self):
        """An empty defects list should return [] without any pipeline call."""
        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(ArbiterCls, "_invoke_sfe_fix_pipeline"):
            pytest.skip("Real Arbiter class not loadable in this environment")

        arbiter = _make_minimal_arbiter(ArbiterCls)
        result = await arbiter._invoke_sfe_fix_pipeline([], job_id="j1", context="test")
        assert result == []

    @pytest.mark.asyncio
    async def test_uses_arena_when_available(self):
        """Pipeline should prefer arena when _run_sfe_fix_pipeline is available."""
        import weakref
        from unittest.mock import AsyncMock, MagicMock

        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(ArbiterCls, "_invoke_sfe_fix_pipeline"):
            pytest.skip("Real Arbiter class not loadable in this environment")

        mock_arena = MagicMock()
        mock_arena._run_sfe_fix_pipeline = AsyncMock(
            return_value=[{"defect": "test_failure", "status": "applied"}]
        )

        arbiter = _make_minimal_arbiter(ArbiterCls)
        arbiter._arena_ref = weakref.ref(mock_arena)

        defects = [{"type": "test_failure", "severity": "high"}]
        result = await arbiter._invoke_sfe_fix_pipeline(
            defects, job_id="j1", context="test"
        )

        assert len(result) == 1
        assert result[0]["status"] == "applied"
        mock_arena._run_sfe_fix_pipeline.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_gracefully_without_arena_or_sfe(self):
        """Without arena or SFEService, returns empty list (no raise)."""
        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(ArbiterCls, "_invoke_sfe_fix_pipeline"):
            pytest.skip("Real Arbiter class not loadable in this environment")

        arbiter = _make_minimal_arbiter(ArbiterCls)
        arbiter._arena_ref = None

        defects = [{"type": "test_failure", "severity": "high"}]
        result = await arbiter._invoke_sfe_fix_pipeline(
            defects, job_id="j1", context="test"
        )
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Arbiter.validate_generated_code_in_sandbox tests
# ---------------------------------------------------------------------------


class TestValidateGeneratedCodeInSandbox:
    """Tests for validate_generated_code_in_sandbox arbiter method."""

    @pytest.mark.asyncio
    async def test_returns_validated_true_when_no_engines(self):
        """With no simulation_engine, validation passes with empty issues."""
        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(
            ArbiterCls, "validate_generated_code_in_sandbox"
        ):
            pytest.skip("Real Arbiter class not loadable in this environment")

        arbiter = _make_minimal_arbiter(ArbiterCls)
        result = await arbiter.validate_generated_code_in_sandbox(
            "print('hello')", "python", {}
        )
        assert result["validated"] is True
        assert isinstance(result["issues"], list)
        assert "sandbox_result" in result

    @pytest.mark.asyncio
    async def test_marks_invalid_on_critical_issues(self):
        """Simulation engine returning critical issues should set validated=False."""
        from unittest.mock import AsyncMock, MagicMock

        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(
            ArbiterCls, "validate_generated_code_in_sandbox"
        ):
            pytest.skip("Real Arbiter class not loadable in this environment")

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(
            return_value={"issues": [{"severity": "critical", "msg": "bad code"}]}
        )

        arbiter = _make_minimal_arbiter(ArbiterCls)
        arbiter.simulation_engine = mock_engine

        result = await arbiter.validate_generated_code_in_sandbox(
            "bad_code()", "python", {}
        )
        assert result["validated"] is False
        assert len(result["issues"]) >= 1

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_engine_error(self):
        """Sandbox engine errors should not raise; result should still be returned."""
        from unittest.mock import AsyncMock, MagicMock

        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(
            ArbiterCls, "validate_generated_code_in_sandbox"
        ):
            pytest.skip("Real Arbiter class not loadable in this environment")

        mock_engine = MagicMock()
        mock_engine.run = AsyncMock(side_effect=RuntimeError("engine crashed"))

        arbiter = _make_minimal_arbiter(ArbiterCls)
        arbiter.simulation_engine = mock_engine

        # Should not raise; validated defaults to True when engine errors
        result = await arbiter.validate_generated_code_in_sandbox(
            "print('test')", "python", {}
        )
        assert isinstance(result, dict)
        assert "validated" in result


# ---------------------------------------------------------------------------
# Arbiter._on_test_results source-check routing tests
# ---------------------------------------------------------------------------


class TestOnTestResultsRouting:
    """Tests for _on_test_results source-based SFE routing."""

    @pytest.mark.asyncio
    async def test_invokes_sfe_on_generator_source_failures(self):
        """Failures with source='generator' should trigger _invoke_sfe_fix_pipeline."""
        from unittest.mock import patch

        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(ArbiterCls, "_on_test_results"):
            pytest.skip("Real Arbiter class not loadable in this environment")

        arbiter = _make_minimal_arbiter(ArbiterCls)
        called_with: list = []

        async def mock_pipeline(defects, job_id="", context=""):
            called_with.append({"defects": defects, "context": context})
            return []

        arbiter._invoke_sfe_fix_pipeline = mock_pipeline
        arbiter.log_event = lambda *a, **kw: None

        await arbiter._on_test_results({
            "test_id": "t1",
            "failures": [{"test_name": "test_foo", "error": "AssertionError"}],
            "passed": 0,
            "failed": 1,
            "source": "generator",
            "generator_id": "gen-001",
        })

        assert len(called_with) == 1
        assert called_with[0]["context"] == "_on_test_results/generator"

    @pytest.mark.asyncio
    async def test_skips_sfe_for_non_generator_source(self):
        """Failures with source != 'generator' should NOT trigger SFE pipeline."""
        ArbiterCls = _get_real_arbiter_class()
        if ArbiterCls is None or not hasattr(ArbiterCls, "_on_test_results"):
            pytest.skip("Real Arbiter class not loadable in this environment")

        arbiter = _make_minimal_arbiter(ArbiterCls)
        called_with: list = []

        async def mock_pipeline(defects, job_id="", context=""):
            called_with.append(defects)
            return []

        arbiter._invoke_sfe_fix_pipeline = mock_pipeline
        arbiter.log_event = lambda *a, **kw: None

        await arbiter._on_test_results({
            "test_id": "t2",
            "failures": [{"test_name": "test_bar", "error": "Fail"}],
            "passed": 0,
            "failed": 1,
            "source": "manual",
        })

        assert len(called_with) == 0
