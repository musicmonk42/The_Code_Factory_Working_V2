# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for SFE Pipeline Integration in OmniCoreService
======================================================

This test module verifies that:
- _run_sfe_analysis method exists and has correct signature
- SFE analysis stage is integrated into _run_full_pipeline
- ImportFixerEngine runs on test files after testgen
- SFE results are included in pipeline output and job metadata

Note: These are unit tests that verify integration points, not end-to-end tests.
Run with: pytest server/tests/test_sfe_pipeline_integration.py -v
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock, mock_open
from pathlib import Path
import json


class TestSFEPipelineIntegration:
    """Test suite for SFE pipeline integration."""
    
    def test_run_sfe_analysis_method_exists(self):
        """Test that _run_sfe_analysis method exists with correct signature."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            # Verify method exists
            assert hasattr(service, '_run_sfe_analysis')
            assert callable(service._run_sfe_analysis)
            
            # Verify it's a coroutine (async method)
            import inspect
            assert inspect.iscoroutinefunction(service._run_sfe_analysis)
    
    @pytest.mark.asyncio
    async def test_run_sfe_analysis_graceful_degradation(self):
        """Test that _run_sfe_analysis gracefully degrades when SFE components unavailable."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            # Call _run_sfe_analysis with mock payload
            payload = {
                "code_path": "/nonexistent/path",
            }
            
            result = await service._run_sfe_analysis("test-job-123", payload)
            
            # Should return skipped status if components unavailable
            # OR error if path doesn't exist
            assert result["status"] in ["skipped", "error"]
            assert "job_id" in result or "message" in result
    
    @pytest.mark.asyncio
    async def test_run_sfe_analysis_with_mocked_components(self):
        """Test that _run_sfe_analysis works with mocked SFE components."""
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect, \
             patch('server.services.omnicore_service.CodebaseAnalyzer', create=True) as mock_analyzer_class, \
             patch('server.services.omnicore_service.BugManager', create=True) as mock_bug_mgr_class, \
             patch('server.services.omnicore_service.aiofiles.open', new_callable=AsyncMock) as mock_aiofiles, \
             patch('pathlib.Path.exists', return_value=True), \
             patch('pathlib.Path.mkdir'), \
             patch('pathlib.Path.stat') as mock_stat:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            # Mock CodebaseAnalyzer
            mock_analyzer = AsyncMock()
            mock_analyzer.scan_codebase = AsyncMock(return_value={
                "defects": [
                    {"file": "test.py", "severity": "high", "message": "Test issue"}
                ],
                "files": 1
            })
            mock_analyzer.__aenter__ = AsyncMock(return_value=mock_analyzer)
            mock_analyzer.__aexit__ = AsyncMock(return_value=None)
            mock_analyzer_class.return_value = mock_analyzer
            
            # Mock BugManager
            mock_bug_mgr = MagicMock()
            mock_bug_mgr.detect_errors = AsyncMock(return_value=[])
            mock_bug_mgr_class.return_value = mock_bug_mgr
            
            # Mock file stat
            mock_stat_obj = MagicMock()
            mock_stat_obj.st_size = 1024
            mock_stat.return_value = mock_stat_obj
            
            # Mock aiofiles write
            mock_file = AsyncMock()
            mock_file.write = AsyncMock()
            mock_aiofiles.return_value.__aenter__.return_value = mock_file
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            payload = {
                "code_path": "/tmp/test_code",
            }
            
            result = await service._run_sfe_analysis("test-job-123", payload)
            
            # Verify result structure
            assert result["status"] == "completed"
            assert "issues_found" in result
            assert "issues_fixed" in result
            assert "report_path" in result
    
    def test_sfe_default_timeout_constant_exists(self):
        """Test that DEFAULT_SFE_ANALYSIS_TIMEOUT constant exists."""
        from server.services.omnicore_service import DEFAULT_SFE_ANALYSIS_TIMEOUT
        
        # Should be an integer (600 seconds default)
        assert isinstance(DEFAULT_SFE_ANALYSIS_TIMEOUT, int)
        assert DEFAULT_SFE_ANALYSIS_TIMEOUT > 0
    
    def test_pipeline_includes_sfe_stage_tracking(self):
        """Test that pipeline code includes SFE stage tracking logic."""
        import inspect
        from server.services.omnicore_service import OmniCoreService
        
        # Get the source code of _run_full_pipeline
        source = inspect.getsource(OmniCoreService._run_full_pipeline)
        
        # Verify SFE analysis stage is in the pipeline
        assert "sfe_analysis" in source.lower()
        assert "_run_sfe_analysis" in source
        
        # Verify stage tracking
        assert "stages_completed.append" in source
    
    def test_pipeline_includes_import_fixer_for_tests(self):
        """Test that pipeline code includes ImportFixerEngine for test files."""
        import inspect
        from server.services.omnicore_service import OmniCoreService
        
        # Get the source code of _run_full_pipeline
        source = inspect.getsource(OmniCoreService._run_full_pipeline)
        
        # Verify ImportFixerEngine is used for test files
        assert "ImportFixerEngine" in source
        assert "tests/" in source or "test file" in source.lower()
    
    def test_dispatch_to_sfe_includes_sfe_analysis(self):
        """Test that _dispatch_to_sfe accepts sfe_analysis in validation_context."""
        import inspect
        from server.services.omnicore_service import OmniCoreService
        
        # Get the source code of _finalize_successful_job (which calls _dispatch_to_sfe)
        source = inspect.getsource(OmniCoreService._finalize_successful_job)
        
        # Verify sfe_analysis is included in validation_context
        assert "sfe_analysis" in source


class TestImportFixerTestFileIntegration:
    """Test suite for ImportFixerEngine integration with test files."""
    
    def test_import_fixer_pattern_in_pipeline(self):
        """Test that ImportFixerEngine follows the same pattern as codegen."""
        import inspect
        from server.services.omnicore_service import OmniCoreService
        
        source = inspect.getsource(OmniCoreService._run_full_pipeline)
        
        # Should import and use ImportFixerEngine
        assert "ImportFixerEngine" in source, "ImportFixerEngine class not found in pipeline"
        
        # Should have try/except ImportError for graceful degradation
        assert "except ImportError" in source, "No graceful degradation for missing ImportFixerEngine"
        
        # Should process test files
        assert "test" in source.lower(), "No test file processing found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
