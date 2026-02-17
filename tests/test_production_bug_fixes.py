# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for production bug fixes from 2026-02-16 and 2026-02-17.

This test suite validates the 5 bug fixes:
1. BugManager.__init__() missing settings argument
2. Missing SFE action handlers (detect_bugs, fix_imports, get_learning_insights)
3. SFE analysis not displayed (check for existing report first)
4. ModuleNotFoundError in codebase_analyzer
5. Helm Chart.yaml fallback writes invalid content
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


class TestBug1_BugManagerSettings:
    """Test Bug 1: BugManager requires settings argument."""
    
    @pytest.mark.asyncio
    async def test_bugmanager_instantiation_with_settings(self):
        """Test that BugManager is instantiated with settings object."""
        from server.services.omnicore_service import OmniCoreService
        
        service = OmniCoreService()
        
        # Create a mock job with code path
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path = Path(tmpdir) / "test_code"
            code_path.mkdir()
            (code_path / "main.py").write_text("print('hello')")
            
            # Mock the BugManager import and detect_errors method
            with patch("server.services.omnicore_service.BugManager") as mock_bug_manager:
                mock_instance = AsyncMock()
                mock_instance.detect_errors = AsyncMock(return_value=[])
                mock_bug_manager.return_value = mock_instance
                
                # Test detect_errors action
                result = await service._dispatch_sfe_action(
                    job_id="test-job",
                    action="detect_errors",
                    payload={"code_path": str(code_path)}
                )
                
                # Verify BugManager was called with settings argument
                assert mock_bug_manager.called
                call_args = mock_bug_manager.call_args
                # Should have settings keyword argument
                assert "settings" in call_args.kwargs or len(call_args.args) > 0
                assert result["status"] == "completed"


class TestBug2_MissingSFEHandlers:
    """Test Bug 2: Missing SFE action handlers."""
    
    @pytest.mark.asyncio
    async def test_detect_bugs_handler(self):
        """Test that detect_bugs action handler exists and works."""
        from server.services.omnicore_service import OmniCoreService
        
        service = OmniCoreService()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path = Path(tmpdir) / "test_code"
            code_path.mkdir()
            (code_path / "main.py").write_text("print('hello')")
            
            # Mock CodebaseAnalyzer
            with patch("server.services.omnicore_service.CodebaseAnalyzer") as mock_analyzer:
                mock_instance = AsyncMock()
                mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
                mock_instance.__aexit__ = AsyncMock(return_value=None)
                mock_instance.scan_codebase = AsyncMock(return_value={
                    "defects": [
                        {"file": str(code_path / "main.py"), "severity": "high", "type": "error"}
                    ]
                })
                mock_analyzer.return_value = mock_instance
                
                result = await service._dispatch_sfe_action(
                    job_id="test-job",
                    action="detect_bugs",
                    payload={"code_path": str(code_path), "scan_depth": "full"}
                )
                
                # Should not return "Unknown SFE action" error
                assert result["status"] == "completed"
                assert "bugs" in result
    
    @pytest.mark.asyncio
    async def test_fix_imports_handler(self):
        """Test that fix_imports action handler exists."""
        from server.services.omnicore_service import OmniCoreService
        
        service = OmniCoreService()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path = Path(tmpdir) / "test_code"
            code_path.mkdir()
            (code_path / "main.py").write_text("import missing_module")
            
            result = await service._dispatch_sfe_action(
                job_id="test-job",
                action="fix_imports",
                payload={"code_path": str(code_path), "auto_install": False}
            )
            
            # Should not return "Unknown SFE action" error
            assert result["status"] == "completed"
            assert "fixes" in result
    
    @pytest.mark.asyncio
    async def test_get_learning_insights_handler(self):
        """Test that get_learning_insights action handler exists."""
        from server.services.omnicore_service import OmniCoreService
        
        service = OmniCoreService()
        
        result = await service._dispatch_sfe_action(
            job_id="test-job",
            action="get_learning_insights",
            payload={"job_id": "test-job"}
        )
        
        # Should not return "Unknown SFE action" error
        assert result["status"] == "completed"
        assert "insights" in result


class TestBug3_SFEAnalysisReport:
    """Test Bug 3: Check for existing SFE analysis report."""
    
    @pytest.mark.asyncio
    async def test_detect_errors_loads_existing_report(self):
        """Test that detect_errors checks for existing report first."""
        from server.services.omnicore_service import OmniCoreService
        
        service = OmniCoreService()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path = Path(tmpdir) / "test_code"
            code_path.mkdir()
            reports_dir = code_path / "reports"
            reports_dir.mkdir()
            
            # Create existing SFE analysis report
            report_data = {
                "job_id": "test-job",
                "all_defects": [
                    {"file": "main.py", "severity": "high", "message": "Error 1"},
                    {"file": "utils.py", "severity": "medium", "message": "Error 2"}
                ]
            }
            report_path = reports_dir / "sfe_analysis_report.json"
            report_path.write_text(json.dumps(report_data))
            
            # Call detect_errors
            result = await service._dispatch_sfe_action(
                job_id="test-job",
                action="detect_errors",
                payload={"code_path": str(code_path)}
            )
            
            # Should load from existing report
            assert result["status"] == "completed"
            assert result["source"] == "sfe_analysis_report"
            assert len(result["errors"]) == 2
    
    @pytest.mark.asyncio
    async def test_sfe_service_detect_errors_loads_report(self):
        """Test that SFEService.detect_errors also checks for existing report."""
        from server.services.sfe_service import SFEService
        from server.storage import jobs_db
        from server.schemas import Job, JobStatus
        from datetime import datetime
        
        with tempfile.TemporaryDirectory() as tmpdir:
            code_path = Path(tmpdir) / "test_code"
            code_path.mkdir()
            reports_dir = code_path / "reports"
            reports_dir.mkdir()
            
            # Create existing SFE analysis report
            report_data = {
                "job_id": "test-job-123",
                "all_defects": [
                    {"file": "main.py", "severity": "critical", "message": "Bug found"}
                ]
            }
            report_path = reports_dir / "sfe_analysis_report.json"
            report_path.write_text(json.dumps(report_data))
            
            # Create job with metadata pointing to code_path
            job = Job(
                id="test-job-123",
                status=JobStatus.COMPLETED,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                metadata={"output_path": str(code_path)}
            )
            jobs_db["test-job-123"] = job
            
            try:
                # Initialize SFEService with direct mode (no OmniCore routing)
                service = SFEService(omnicore_service=None)
                
                # Mark CodebaseAnalyzer as available
                with patch.object(service, '_sfe_available', {"codebase_analyzer": True}):
                    with patch.object(service, '_sfe_components', {"codebase_analyzer": MagicMock()}):
                        result = await service.detect_errors("test-job-123")
                        
                        # Should have loaded from report
                        assert "errors" in result or "count" in result
                        if "source" in result:
                            assert result["source"] == "sfe_analysis_report"
            finally:
                # Cleanup
                if "test-job-123" in jobs_db:
                    del jobs_db["test-job-123"]


class TestBug4_ModuleNotFoundError:
    """Test Bug 4: Handle ModuleNotFoundError in codebase_analyzer."""
    
    @pytest.mark.asyncio
    async def test_extract_dependencies_handles_missing_modules(self):
        """Test that _extract_dependencies_from_file handles ModuleNotFoundError."""
        from self_fixing_engineer.arbiter.codebase_analyzer import CodebaseAnalyzer
        
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            test_file = root_dir / "test.py"
            
            # Write code with imports of non-existent project modules
            test_file.write_text("""
import app
from app import routes
from generated.hello_generator import main
import external_module
""")
            
            # Create app directory to mark it as local
            (root_dir / "app").mkdir()
            (root_dir / "app" / "__init__.py").write_text("")
            
            # Test that it doesn't raise ModuleNotFoundError
            async with CodebaseAnalyzer(root_dir=str(root_dir)) as analyzer:
                # This should not raise an exception
                deps = analyzer._extract_dependencies_from_file(test_file)
                
                # Should have collected dependencies
                assert len(deps) > 0
                
                # Check that local modules are marked correctly
                app_imports = [d for d in deps if d["import_name"] == "app"]
                assert len(app_imports) > 0
                # app should be marked as not external (local)
                assert not app_imports[0]["is_external"]


class TestBug5_HelmFallback:
    """Test Bug 5: Helm Chart.yaml fallback validation."""
    
    def test_helm_handler_returns_default_on_parse_error(self):
        """Test that HelmHandler returns default chart on parse errors."""
        from generator.agents.deploy_agent.deploy_response_handler import HelmHandler
        
        handler = HelmHandler()
        
        # Test with invalid markdown content (no YAML at all)
        invalid_content = """
# This is just markdown
Some prose about the helm chart.
Not actual YAML content!
"""
        
        # Should not raise ValueError, should return default chart
        result = handler.normalize(invalid_content)
        
        # Should have default structure
        assert isinstance(result, dict)
        assert "Chart.yaml" in result
        assert "values.yaml" in result
        assert "templates" in result
        
        # Chart.yaml should have default values
        chart = result["Chart.yaml"]
        assert chart["apiVersion"] == "v2"
        assert chart["name"] == "app"
    
    def test_helm_handler_handles_yaml_parse_error(self):
        """Test that HelmHandler handles YAML parse errors gracefully."""
        from generator.agents.deploy_agent.deploy_response_handler import HelmHandler
        
        handler = HelmHandler()
        
        # Test with invalid YAML
        invalid_yaml = """
apiVersion: v2
name: test
  invalid indentation here
    more bad indentation
"""
        
        # Should not raise, should return default chart
        result = handler.normalize(invalid_yaml)
        
        assert isinstance(result, dict)
        assert "Chart.yaml" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
