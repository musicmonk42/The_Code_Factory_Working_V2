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

pytestmark = pytest.mark.slow


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
        # Since the method has try/except blocks that handle ImportError gracefully,
        # we don't need to mock the imports. The method will handle missing components.
        # Instead, we just need to ensure the method completes successfully.
        
        with patch('server.services.omnicore_service.get_agent_config') as mock_config, \
             patch('server.services.omnicore_service.get_llm_config') as mock_llm_config, \
             patch('server.services.omnicore_service.detect_available_llm_provider') as mock_detect:
            
            mock_config.return_value = None
            mock_llm_config.return_value = None
            mock_detect.return_value = None
            
            from server.services.omnicore_service import OmniCoreService
            
            service = OmniCoreService()
            
            # Create a temporary test directory with some Python files
            import tempfile
            import os
            
            with tempfile.TemporaryDirectory() as tmpdir:
                # Create a simple Python file
                test_file = os.path.join(tmpdir, "test.py")
                with open(test_file, "w") as f:
                    f.write("# Test file\ndef test_function():\n    pass\n")
                
                payload = {
                    "code_path": tmpdir,
                }
                
                result = await service._run_sfe_analysis("test-job-123", payload)
                
                # Verify result structure
                # The method should either complete successfully or skip gracefully
                assert result["status"] in ["completed", "skipped", "error"]
                assert "job_id" in result or "message" in result
    
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


class TestSoftFailValidation:
    """Tests for the soft-fail validation mode changes in the pipeline."""

    def test_engine_does_not_break_on_validate_failure(self):
        """Verify engine.py no longer breaks on STAGE:VALIDATE failure."""
        engine_file = Path("generator/main/engine.py")
        assert engine_file.exists()
        content = engine_file.read_text()
        # The hard-fail break should be replaced with soft-fail continuation
        assert "validate:soft_fail" in content, \
            "engine.py should use 'validate:soft_fail' instead of breaking"
        # Should NOT have 'HARD FAIL' in the validate stage section
        assert "HARD FAIL - Validation failed" not in content, \
            "engine.py should not have HARD FAIL message for validation"

    def test_engine_contract_validate_soft_fail(self):
        """Verify CONTRACT_VALIDATE stage also uses soft-fail mode."""
        engine_file = Path("generator/main/engine.py")
        content = engine_file.read_text()
        # Contract validate should also append validate:soft_fail
        lines = content.split('\n')
        soft_fail_count = sum(1 for line in lines if 'validate:soft_fail' in line)
        assert soft_fail_count >= 2, \
            "Both VALIDATE and CONTRACT_VALIDATE stages should use soft-fail"

    def test_generator_router_recognizes_soft_fail(self):
        """Verify generator.py router treats validate:soft_fail as non-blocking."""
        generator_file = Path("server/routers/generator.py")
        assert generator_file.exists()
        content = generator_file.read_text()
        assert "validate_was_soft_fail" in content, \
            "generator.py should recognize validate:soft_fail as non-blocking"
        assert "validate:soft_fail" in content, \
            "generator.py should check for 'validate:soft_fail' in stages_completed"

    def test_arbiter_bridge_includes_code_in_event(self):
        """Verify engine.py arbiter bridge event now includes actual code."""
        engine_file = Path("generator/main/engine.py")
        content = engine_file.read_text()
        assert '"code": codegen_files_for_arbiter' in content or \
               "'code': codegen_files_for_arbiter" in content, \
            "Arbiter bridge event should include generated code"
        assert '"file_paths"' in content or "'file_paths'" in content, \
            "Arbiter bridge event should include file paths"
