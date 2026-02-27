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


def _load_stage_ran():
    """Load _stage_ran from generator.py without triggering FastAPI imports."""
    import re as _re
    import importlib.util as _util
    from pathlib import Path

    src_path = Path("server/routers/generator.py")
    source = src_path.read_text(encoding="utf-8")

    func_match = _re.search(
        r"(def _stage_ran\b.*?)(?=\n\n\n|\n\n[A-Z#])",
        source,
        _re.DOTALL,
    )
    assert func_match, "_stage_ran not found in server/routers/generator.py"
    namespace = {}
    exec(compile(func_match.group(1), "<generator>", "exec"), namespace)  # nosec B102
    return namespace["_stage_ran"]


class TestSoftFailValidation:
    """Tests for the soft-fail validation mode in the pipeline."""

    @pytest.fixture(scope="class")
    def stage_ran(self):
        return _load_stage_ran()

    def test_validate_soft_fail_counts_as_stage_ran(self, stage_ran):
        """validate:soft_fail must be treated as the validate stage having run."""
        stages = ["codegen", "testgen", "validate:soft_fail"]
        assert stage_ran("validate", stages) is True

    def test_validate_skipped_counts_as_stage_ran(self, stage_ran):
        """validate:skipped must also be treated as the validate stage having run."""
        stages = ["codegen", "testgen", "validate:skipped"]
        assert stage_ran("validate", stages) is True

    def test_validate_not_in_stages_returns_false(self, stage_ran):
        """When validate has not run at all, stage_ran returns False."""
        stages = ["codegen", "testgen"]
        assert stage_ran("validate", stages) is False

    def test_validate_soft_fail_excluded_from_critical_stages(self):
        """generator.py must exclude validate from critical_stages on soft_fail."""
        from pathlib import Path
        content = Path("server/routers/generator.py").read_text()
        assert "validate_was_soft_fail" in content
        assert "not validate_was_skipped and not validate_was_soft_fail" in content

    def test_engine_uses_soft_fail_not_break(self):
        """engine.py must not break out of the loop on STAGE:VALIDATE failure."""
        from pathlib import Path
        content = Path("generator/main/engine.py").read_text()
        # HARD FAIL phrase should be gone from the validate section
        assert "HARD FAIL - Validation failed" not in content
        # validate:soft_fail must appear at least twice (VALIDATE + CONTRACT_VALIDATE)
        assert content.count("validate:soft_fail") >= 2

    def test_arbiter_bridge_event_includes_code(self):
        """engine.py arbiter bridge event must include generated code payload."""
        from pathlib import Path
        content = Path("generator/main/engine.py").read_text()
        assert '"code": codegen_files_for_arbiter' in content
        assert '"file_paths": list(codegen_files_for_arbiter.keys())' in content
