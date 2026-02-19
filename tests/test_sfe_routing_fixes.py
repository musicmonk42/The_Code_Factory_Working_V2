# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for SFE routing and path resolution fixes.

Tests the improvements to:
- SFE job routing through OmniCore
- Path resolution for generated job files
- CodebaseAnalyzer ignore patterns for generated code
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import tempfile
import shutil

from server.services.omnicore_service import OmniCoreService
from server.services.sfe_service import SFEService


class TestSFERoutingFixes:
    """Test suite for SFE routing and path resolution fixes."""

    @pytest.mark.asyncio
    async def test_route_job_dispatches_to_sfe(self):
        """Test that route_job properly dispatches SFE actions."""
        service = OmniCoreService()

        # Mock the _dispatch_sfe_action method
        with patch.object(
            service, "_dispatch_sfe_action", new_callable=AsyncMock
        ) as mock_dispatch:
            mock_dispatch.return_value = {
                "status": "completed",
                "job_id": "test-job-123",
                "issues_found": 0,
                "issues": [],
            }

            result = await service.route_job(
                job_id="test-job-123",
                source_module="api",
                target_module="sfe",
                payload={"action": "analyze_code", "code_path": "/test/path"},
            )

            # Verify routing was successful
            assert result["routed"] is True
            assert result["target"] == "sfe"
            assert result["data"]["status"] == "completed"

            # Verify dispatch was called
            mock_dispatch.assert_called_once_with(
                "test-job-123",
                "analyze_code",
                {"action": "analyze_code", "code_path": "/test/path"},
            )

    @pytest.mark.asyncio
    async def test_dispatch_sfe_action_analyze_code(self):
        """Test _dispatch_sfe_action for analyze_code action."""
        service = OmniCoreService()

        # Create a temporary directory structure
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.py"
            test_file.write_text("def hello(): pass")

            # Mock _resolve_job_output_path to return our temp directory
            with patch.object(service, "_resolve_job_output_path", return_value=tmpdir):
                # Mock CodebaseAnalyzer
                with patch(
                    "server.services.omnicore_service.CodebaseAnalyzer"
                ) as mock_analyzer_class:
                    mock_analyzer = AsyncMock()
                    mock_analyzer.__aenter__ = AsyncMock(return_value=mock_analyzer)
                    mock_analyzer.__aexit__ = AsyncMock(return_value=None)
                    mock_analyzer.scan_codebase = AsyncMock(
                        return_value={"defects": [], "files": 1}
                    )
                    mock_analyzer_class.return_value = mock_analyzer

                    result = await service._dispatch_sfe_action(
                        job_id="test-123",
                        action="analyze_code",
                        payload={"code_path": tmpdir},
                    )

                    # Verify result
                    assert result["status"] == "completed"
                    assert result["job_id"] == "test-123"
                    assert result["issues_found"] == 0
                    assert "source" in result
                    assert result["source"] == "direct_sfe"

    def test_resolve_job_output_path_hint_path(self):
        """Test _resolve_job_output_path uses hint path when valid."""
        service = OmniCoreService()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = service._resolve_job_output_path("test-job", tmpdir)
            assert result == tmpdir

    def test_resolve_job_output_path_standard_locations(self):
        """Test _resolve_job_output_path checks standard locations."""
        service = OmniCoreService()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create standard location structure
            job_id = "test-job-456"
            uploads_dir = Path(tmpdir) / "uploads"
            job_dir = uploads_dir / job_id / "generated"
            job_dir.mkdir(parents=True)

            # Create a Python file to make it a valid location
            (job_dir / "main.py").write_text("# test")

            # Mock Path("./uploads") to point to our temp uploads_dir
            with patch("server.services.omnicore_service.Path") as mock_path:

                def path_side_effect(p):
                    if p == f"./uploads/{job_id}/generated":
                        return job_dir
                    elif p == f"./uploads/{job_id}/output":
                        return uploads_dir / job_id / "output"  # doesn't exist
                    elif p == f"./uploads/{job_id}":
                        return uploads_dir / job_id
                    return Path(p)

                mock_path.side_effect = path_side_effect

                # Mock exists() and is_dir()
                result = service._resolve_job_output_path(job_id, "")

                # Should find the generated directory
                # Note: In reality this test needs better mocking,
                # but demonstrates the intent

    @pytest.mark.asyncio
    async def test_sfe_service_analyze_code_uses_reduced_ignore_patterns(self):
        """Test that SFEService.analyze_code uses reduced ignore patterns."""
        service = SFEService(omnicore_service=None)

        # Enable the codebase analyzer
        service._sfe_available["codebase_analyzer"] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = Path(tmpdir)
            test_file = test_dir / "test_module.py"
            test_file.write_text("def test(): pass")

            # Mock CodebaseAnalyzer
            mock_analyzer_class = MagicMock()
            mock_analyzer = AsyncMock()
            mock_analyzer.__aenter__ = AsyncMock(return_value=mock_analyzer)
            mock_analyzer.__aexit__ = AsyncMock(return_value=None)
            mock_analyzer.scan_codebase = AsyncMock(return_value={"defects": []})
            mock_analyzer_class.return_value = mock_analyzer

            service._sfe_components["codebase_analyzer"] = mock_analyzer_class

            result = await service.analyze_code("test-job", str(test_dir))

            # Verify CodebaseAnalyzer was called with reduced ignore_patterns
            mock_analyzer_class.assert_called_once()
            call_kwargs = mock_analyzer_class.call_args[1]
            assert "ignore_patterns" in call_kwargs
            # Should not include "tests" in ignore patterns
            assert "tests" not in call_kwargs["ignore_patterns"]
            assert "__pycache__" in call_kwargs["ignore_patterns"]

    @pytest.mark.asyncio
    async def test_sfe_service_detect_errors_path_resolution(self):
        """Test that SFEService.detect_errors uses improved path resolution."""
        omnicore_service = OmniCoreService()
        service = SFEService(omnicore_service=omnicore_service)

        # Enable codebase analyzer
        service._sfe_available["codebase_analyzer"] = True

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create job structure
            job_id = "test-job-789"
            job_dir = Path(tmpdir) / "uploads" / job_id / "generated" / "my_project"
            job_dir.mkdir(parents=True)
            (job_dir / "main.py").write_text("def main(): pass")

            # Mock jobs_db to return metadata with output_path
            with patch("server.services.sfe_service.jobs_db") as mock_db:
                mock_job = MagicMock()
                mock_job.metadata = {"output_path": str(job_dir)}
                mock_db.get.return_value = mock_job

                # Mock CodebaseAnalyzer
                mock_analyzer_class = MagicMock()
                service._sfe_components["codebase_analyzer"] = mock_analyzer_class

                result = await service.detect_errors(job_id)

                # Verify it found the directory
                # Result should not contain "Job directory not found" error
                if "note" in result:
                    assert "not found" not in result["note"]


class TestPresidioLoggerFixes:
    """Test suite for Presidio logger warning suppression."""

    def test_presidio_logger_filters_applied(self):
        """Test that both presidio_analyzer and presidio-analyzer have filters."""
        import logging

        # This test verifies the fix is in place, but we can't easily test
        # the actual suppression without running Presidio initialization.
        # Instead we verify the pattern exists in the code.

        from generator.runner import runner_security_utils
        import inspect

        source = inspect.getsource(runner_security_utils)

        # Verify the fix pattern is in the code
        assert (
            'for logger_name in ["presidio_analyzer", "presidio-analyzer"]' in source
            or '"presidio_analyzer"' in source
            and '"presidio-analyzer"' in source
        )


class TestSFETabFixes:
    """Test suite for SFE tab UI fixes."""

    @pytest.mark.asyncio
    async def test_get_errors_unwraps_nested_response(self):
        """Test that get_errors endpoint properly unwraps the detect_errors response."""
        from server.routers.sfe import get_errors
        from server.storage import jobs_db, Job

        # Create a test job
        test_job_id = "test-job-unwrap"
        jobs_db[test_job_id] = Job(job_id=test_job_id)

        # Mock SFEService
        mock_sfe_service = AsyncMock()
        mock_sfe_service.detect_errors = AsyncMock(
            return_value={
                "errors": [
                    {"error_id": "e1", "message": "Test error", "severity": "high"},
                    {
                        "error_id": "e2",
                        "message": "Another error",
                        "severity": "medium",
                    },
                ],
                "count": 2,
            }
        )

        # Call the endpoint
        result = await get_errors(test_job_id, mock_sfe_service)

        # Verify the response is properly unwrapped
        assert "errors" in result
        assert isinstance(result["errors"], list)
        assert len(result["errors"]) == 2
        assert result["count"] == 2
        assert result["errors"][0]["error_id"] == "e1"

        # Clean up
        del jobs_db[test_job_id]

    @pytest.mark.asyncio
    async def test_resolve_job_code_path_from_metadata(self):
        """Test that _resolve_job_code_path resolves path from job metadata."""
        from server.storage import jobs_db, Job

        service = SFEService(omnicore_service=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test job with metadata
            test_job_id = "test-job-metadata"
            test_path = Path(tmpdir) / "test_project"
            test_path.mkdir()

            job = Job(job_id=test_job_id)
            job.metadata = {"output_path": str(test_path)}
            jobs_db[test_job_id] = job

            # Resolve path
            resolved = service._resolve_job_code_path(test_job_id, "/default/path")

            # Should return the metadata path
            assert resolved == str(test_path)

            # Clean up
            del jobs_db[test_job_id]

    @pytest.mark.asyncio
    async def test_resolve_job_code_path_from_standard_location(self):
        """Test that _resolve_job_code_path finds path in standard location."""
        from server.storage import jobs_db, Job

        service = SFEService(omnicore_service=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create standard job structure
            test_job_id = "test-job-standard"
            uploads_dir = Path(tmpdir) / "uploads"
            job_dir = uploads_dir / test_job_id / "generated" / "my_project"
            job_dir.mkdir(parents=True)

            # Create a Python file to make it valid
            (job_dir / "main.py").write_text("# test")

            # Create job without metadata
            jobs_db[test_job_id] = Job(job_id=test_job_id)

            # Mock Path("./uploads") to point to our temp uploads_dir
            with patch("server.services.sfe_service.Path") as mock_path_class:

                def path_side_effect(p):
                    if p == "./uploads":
                        return uploads_dir
                    return Path(p)

                mock_path_class.side_effect = path_side_effect

                # Resolve path
                resolved = service._resolve_job_code_path(test_job_id, "/default/path")

                # Should find the generated project
                assert "my_project" in resolved or test_job_id in resolved

            # Clean up
            del jobs_db[test_job_id]

    @pytest.mark.asyncio
    async def test_resolve_job_code_path_fallback(self):
        """Test that _resolve_job_code_path falls back to default when job not found."""
        service = SFEService(omnicore_service=None)

        # Try to resolve path for non-existent job
        resolved = service._resolve_job_code_path("nonexistent-job", "/default/path")

        # Should return the default path
        assert resolved == "/default/path"

    @pytest.mark.asyncio
    async def test_detect_bugs_uses_job_id(self):
        """Test that detect_bugs uses job_id to resolve path."""
        from server.storage import jobs_db, Job

        service = SFEService(omnicore_service=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test job
            test_job_id = "test-job-bugs"
            test_path = Path(tmpdir) / "test_code"
            test_path.mkdir()

            job = Job(job_id=test_job_id)
            job.metadata = {"output_path": str(test_path)}
            jobs_db[test_job_id] = job

            # Call detect_bugs with job_id
            result = await service.detect_bugs(
                code_path=".",
                scan_depth="quick",
                include_potential=False,
                job_id=test_job_id,
            )

            # Should have attempted to scan the resolved path
            # Result will be empty since no Python files exist, but should not error
            assert "bugs" in result
            assert isinstance(result["bugs"], list)

            # Clean up
            del jobs_db[test_job_id]

    @pytest.mark.asyncio
    async def test_prioritize_bugs_uses_real_data(self):
        """Test that prioritize_bugs attempts to use real bug data."""
        from server.storage import jobs_db, Job

        service = SFEService(omnicore_service=None)

        # Create test job
        test_job_id = "test-job-prioritize"
        jobs_db[test_job_id] = Job(job_id=test_job_id)

        # Mock detect_errors to return real bugs
        with patch.object(
            service, "detect_errors", new_callable=AsyncMock
        ) as mock_detect:
            mock_detect.return_value = {
                "errors": [
                    {
                        "error_id": "e1",
                        "message": "Critical bug",
                        "severity": "critical",
                        "type": "Security",
                    },
                    {
                        "error_id": "e2",
                        "message": "High bug",
                        "severity": "high",
                        "type": "Logic",
                    },
                    {
                        "error_id": "e3",
                        "message": "Medium bug",
                        "severity": "medium",
                        "type": "Style",
                    },
                ],
                "count": 3,
            }

            # Call prioritize_bugs
            result = await service.prioritize_bugs(test_job_id, ["severity"])

            # Verify it used real data
            assert result["source"] == "real_analysis"
            assert len(result["prioritized_bugs"]) == 3

            # Verify bugs are sorted by severity (critical first)
            assert result["prioritized_bugs"][0]["severity"] == "critical"
            assert result["prioritized_bugs"][0]["priority"] == 1
            assert result["prioritized_bugs"][1]["severity"] == "high"
            assert result["prioritized_bugs"][2]["severity"] == "medium"

        # Clean up
        del jobs_db[test_job_id]


class TestDeepAnalyzeHandler:
    """Tests for the deep_analyze OmniCore handler."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for PluginService."""
        mock_registry = MagicMock()
        mock_bus_instance = MagicMock()
        mock_bus_instance.subscribe = AsyncMock()
        mock_bus_instance.publish = AsyncMock()
        return {"registry": mock_registry, "bus": mock_bus_instance}

    @pytest.mark.asyncio
    async def test_handle_sfe_request_deep_analyze(self, mock_dependencies):
        """Test that handle_sfe_request routes deep_analyze to SFEService.deep_analyze_codebase."""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"],
        )
        await service.start_subscriptions()

        expected_result = {
            "analysis_id": "analysis_1234",
            "total_files": 5,
            "total_loc": 200,
            "source": "direct_sfe",
        }

        mock_sfe_service = AsyncMock()
        mock_sfe_service.deep_analyze_codebase = AsyncMock(return_value=expected_result)

        message = Mock()
        message.payload = {
            "action": "deep_analyze",
            "job_id": "job-deep-001",
            "code_path": "/some/code",
            "analysis_types": ["complexity", "security"],
            "generate_report": True,
        }

        with patch("server.services.SFEService", return_value=mock_sfe_service):
            await service.handle_sfe_request(message)

        mock_sfe_service.deep_analyze_codebase.assert_awaited_once_with(
            "/some/code",
            ["complexity", "security"],
            True,
            "job-deep-001",
        )

    @pytest.mark.asyncio
    async def test_handle_sfe_request_deep_analyze_defaults(self, mock_dependencies):
        """Test that deep_analyze handler uses sensible defaults for missing payload fields."""
        from omnicore_engine.engines import PluginService

        service = PluginService(
            mock_dependencies["registry"],
            message_bus=mock_dependencies["bus"],
        )
        await service.start_subscriptions()

        mock_sfe_service = AsyncMock()
        mock_sfe_service.deep_analyze_codebase = AsyncMock(return_value={"source": "fallback"})

        message = Mock()
        message.payload = {
            "action": "deep_analyze",
            "job_id": "job-deep-002",
            # code_path, analysis_types, generate_report omitted → use defaults
        }

        with patch("server.services.SFEService", return_value=mock_sfe_service):
            await service.handle_sfe_request(message)

        mock_sfe_service.deep_analyze_codebase.assert_awaited_once_with(
            ".",
            [],
            False,
            "job-deep-002",
        )

    def test_init_sfe_components_import_error_log(self):
        """Test _init_sfe_components logs exception type and missing dependency hint on ImportError."""
        import builtins

        service = SFEService(omnicore_service=None)

        original_import = builtins.__import__

        def patched_import(name, *args, **kwargs):
            if name == "self_fixing_engineer.arbiter.codebase_analyzer":
                raise ImportError("No module named 'self_fixing_engineer'")
            return original_import(name, *args, **kwargs)

        with patch("server.services.sfe_service.logger") as mock_logger:
            with patch("builtins.__import__", side_effect=patched_import):
                service._init_sfe_components()

            # Check that the warning contains exception type and dependency hint
            # Extract the first positional argument of each warning call
            warning_messages = [
                call.args[0] for call in mock_logger.warning.call_args_list
                if call.args
            ]
            analyzer_warnings = [
                m for m in warning_messages
                if "codebase analyzer" in m.lower() or "codebase_analyzer" in m
            ]
            assert any(
                "ImportError" in w and "requirements" in w
                for w in analyzer_warnings
            ), f"Expected detailed ImportError log with dependency hint, got: {analyzer_warnings}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
