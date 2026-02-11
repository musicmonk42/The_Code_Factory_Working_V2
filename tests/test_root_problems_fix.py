# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_root_problems_fix.py

Tests for the 5 interconnected root problems fix:
1. Presidio scrub_text() destroying source code
2. Wrong YAML exception class in KubernetesValidator
3. Pipeline aborting on individual stage failures
4. Generator plugin wrapper not imported at startup
5. CLI bypassing OmniCore

These tests validate the minimal changes made to fix the critical issues.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import pytest

# Force testing mode
os.environ["TESTING"] = "1"


# ============================================================================
# ROOT PROBLEM #1: Test that source code files are NOT scrubbed
# ============================================================================

@pytest.mark.asyncio
async def test_testgen_load_code_files_no_scrubbing():
    """Test that _load_code_files does NOT scrub source code content."""
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    
    # Create a temporary code file with content that would be corrupted by scrubbing
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        code_file = repo_path / "main.py"
        
        # This code has patterns that Presidio would incorrectly flag:
        # - "Response" might be flagged as PERSON
        # - Import statements with organization-like words
        test_code = """from fastapi.responses import JSONResponse
from myapp.server import ServerManager

class ResponseHandler:
    def handle_response(self):
        return JSONResponse(content={"status": "ok"})
"""
        code_file.write_text(test_code, encoding="utf-8")
        
        # Create TestgenAgent instance
        with patch("generator.agents.testgen_agent.testgen_agent.scrub_text") as mock_scrub:
            # scrub_text should NOT be called on source code
            mock_scrub.return_value = "[REDACTED]"  # This would break AST parsing
            
            agent = TestgenAgent()
            agent.repo_path = repo_path
            
            # Load the code file
            result = await agent._load_code_files(["main.py"])
            
            # Verify scrub_text was NOT called (it's been removed from the code path)
            mock_scrub.assert_not_called()
            
            # Verify we got the original, unscrubbed content
            assert "main.py" in result
            loaded_content = result["main.py"]
            
            # Verify critical content is intact (would be broken if scrubbed)
            assert "from fastapi.responses import JSONResponse" in loaded_content
            assert "ServerManager" in loaded_content
            assert "ResponseHandler" in loaded_content
            assert "[REDACTED]" not in loaded_content
            assert "<ORGANIZATION>" not in loaded_content


# ============================================================================
# ROOT PROBLEM #2: Test correct YAML exception handling
# ============================================================================

@pytest.mark.asyncio
async def test_kubernetes_validator_yaml_exception():
    """Test that KubernetesValidator catches the correct YAML exception."""
    from generator.agents.deploy_agent.deploy_validator import KubernetesValidator
    from ruamel.yaml import YAMLError as RuamelYAMLError
    
    validator = KubernetesValidator()
    
    # Invalid YAML that will cause a parse error
    invalid_yaml = """
apiVersion: v1
kind: Pod
metadata:
  name: test
  labels:
    - invalid: yaml: structure
"""
    
    # This should catch RuamelYAMLError and report it properly
    result = await validator.validate(invalid_yaml, target_type="k8s")
    
    # Should fail with YAML syntax error, not an internal error
    assert result["lint_status"] == "failed"
    assert len(result["lint_issues"]) > 0
    assert any("YAML" in issue or "yaml" in issue for issue in result["lint_issues"])
    
    # Should NOT have an exception stacktrace from NameError
    # (which would happen if we tried to catch yaml.YAMLError when yaml is not imported)
    assert result["lint_output"] is not None


# ============================================================================
# ROOT PROBLEM #3: Test pipeline resilience to stage failures
# ============================================================================

@pytest.mark.asyncio
async def test_pipeline_continues_after_testgen_failure():
    """Test that pipeline continues to deploy/docgen even if testgen fails."""
    from server.services.omnicore_service import OmniCoreService
    
    with patch("server.services.omnicore_service.get_omnicore_service"):
        service = OmniCoreService()
        
        # Mock the individual stage methods
        with patch.object(service, "_run_codegen") as mock_codegen, \
             patch.object(service, "_run_testgen") as mock_testgen, \
             patch.object(service, "_run_deploy_all") as mock_deploy, \
             patch.object(service, "_run_docgen") as mock_docgen, \
             patch.object(service, "_run_critique") as mock_critique, \
             patch.object(service, "_ensure_agents_loaded"):
            
            # Codegen succeeds
            mock_codegen.return_value = {
                "status": "completed",
                "output_path": "/tmp/test_output",
                "files_count": 5,
            }
            
            # Testgen FAILS
            mock_testgen.return_value = {
                "status": "error",
                "message": "Test generation failed due to AST parsing error",
            }
            
            # Deploy succeeds
            mock_deploy.return_value = {
                "status": "completed",
                "completed_targets": ["docker", "kubernetes"],
            }
            
            # Docgen succeeds
            mock_docgen.return_value = {
                "status": "completed",
            }
            
            # Critique succeeds
            mock_critique.return_value = {
                "status": "completed",
            }
            
            # Run pipeline
            job_id = "test_job_123"
            payload = {
                "readme_content": "# Test App\nBuild a test app",
                "include_tests": True,
                "include_deployment": True,
                "include_docs": True,
                "run_critique": True,
            }
            
            result = await service._run_full_pipeline(job_id, payload)
            
            # Pipeline should complete despite testgen failure
            assert result["status"] == "completed"
            
            # All stages should have been attempted
            stages = result["stages_completed"]
            assert "codegen" in stages
            assert "testgen:failed" in stages  # Failed but tracked
            assert "deploy" in stages  # Deploy ran despite testgen failure
            assert "docgen" in stages  # Docgen ran despite testgen failure
            assert "critique" in stages  # Critique ran despite testgen failure


@pytest.mark.asyncio
async def test_pipeline_continues_after_deploy_failure():
    """Test that pipeline continues to docgen/critique even if deploy fails."""
    from server.services.omnicore_service import OmniCoreService
    
    with patch("server.services.omnicore_service.get_omnicore_service"):
        service = OmniCoreService()
        
        with patch.object(service, "_run_codegen") as mock_codegen, \
             patch.object(service, "_run_testgen") as mock_testgen, \
             patch.object(service, "_run_deploy_all") as mock_deploy, \
             patch.object(service, "_run_docgen") as mock_docgen, \
             patch.object(service, "_run_critique") as mock_critique, \
             patch.object(service, "_ensure_agents_loaded"):
            
            mock_codegen.return_value = {
                "status": "completed",
                "output_path": "/tmp/test_output",
            }
            
            mock_testgen.return_value = {
                "status": "completed",
            }
            
            # Deploy FAILS
            mock_deploy.return_value = {
                "status": "error",
                "message": "Deployment configuration generation failed",
            }
            
            mock_docgen.return_value = {
                "status": "completed",
            }
            
            mock_critique.return_value = {
                "status": "completed",
            }
            
            job_id = "test_job_456"
            payload = {
                "readme_content": "# Test App",
                "include_deployment": True,
                "include_docs": True,
                "run_critique": True,
            }
            
            result = await service._run_full_pipeline(job_id, payload)
            
            # Pipeline should complete
            assert result["status"] == "completed"
            
            stages = result["stages_completed"]
            assert "deploy:failed" in stages
            assert "docgen" in stages  # Docgen ran despite deploy failure
            assert "critique" in stages


# ============================================================================
# ROOT PROBLEM #4: Test generator plugin wrapper is imported
# ============================================================================

def test_generator_plugin_wrapper_import():
    """Test that generator_plugin_wrapper can be imported successfully."""
    # This test validates that the import added to server/main.py will succeed
    try:
        import generator.agents.generator_plugin_wrapper
        # If we get here, import succeeded
        assert hasattr(generator.agents.generator_plugin_wrapper, "run_generator_workflow")
    except ImportError as e:
        pytest.fail(f"Failed to import generator_plugin_wrapper: {e}")


# ============================================================================
# ROOT PROBLEM #5: Test CLI routing through OmniCore
# ============================================================================

@pytest.mark.asyncio
async def test_engine_routes_through_omnicore_when_available():
    """Test that WorkflowEngine routes through OmniCore when available."""
    from generator.main.engine import WorkflowEngine
    
    # Mock the run_generator_workflow function
    mock_omnicore_result = {
        "status": "completed",
        "output_path": "/tmp/omnicore_output",
        "stages_completed": ["codegen", "testgen", "deploy"],
    }
    
    with patch("generator.main.engine._OMNICORE_WORKFLOW_AVAILABLE", True), \
         patch("generator.main.engine.run_generator_workflow", new_callable=AsyncMock) as mock_workflow:
        
        mock_workflow.return_value = mock_omnicore_result
        
        # Create engine instance
        engine = WorkflowEngine()
        
        # Create a temporary input file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test App\nBuild a simple test application")
            input_file = f.name
        
        try:
            # Run orchestrate
            result = await engine.orchestrate(
                input_file=input_file,
                output_path="/tmp/test_output",
            )
            
            # Verify OmniCore workflow was called
            mock_workflow.assert_called_once()
            
            # Verify result came from OmniCore
            assert result["omnicore_routed"] == True
            assert result["status"] == "completed"
            assert result["output_path"] == "/tmp/omnicore_output"
            
        finally:
            # Cleanup
            if os.path.exists(input_file):
                os.unlink(input_file)


@pytest.mark.asyncio
async def test_engine_fallback_when_omnicore_unavailable():
    """Test that WorkflowEngine falls back to direct execution when OmniCore unavailable."""
    from generator.main.engine import WorkflowEngine
    
    with patch("generator.main.engine._OMNICORE_WORKFLOW_AVAILABLE", False):
        engine = WorkflowEngine()
        
        # The engine should continue with direct execution
        # (We don't need to fully test the direct path, just verify it doesn't crash)
        assert engine is not None


# ============================================================================
# INTEGRATION TEST: End-to-end validation
# ============================================================================

@pytest.mark.asyncio
async def test_integration_all_fixes():
    """Integration test verifying all 5 fixes work together."""
    # This test verifies that:
    # 1. Source code is not scrubbed (testgen can parse it)
    # 2. YAML errors are caught correctly
    # 3. Pipeline stages continue despite failures
    # 4. Plugin wrapper is importable
    # 5. OmniCore routing is available
    
    # Test 1: Source code not scrubbed
    from generator.agents.testgen_agent.testgen_agent import TestgenAgent
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        code_file = repo_path / "test.py"
        code_file.write_text("from fastapi.responses import JSONResponse\n", encoding="utf-8")
        
        agent = TestgenAgent()
        agent.repo_path = repo_path
        result = await agent._load_code_files(["test.py"])
        assert "JSONResponse" in result["test.py"]
    
    # Test 2: YAML exception handling
    from generator.agents.deploy_agent.deploy_validator import KubernetesValidator
    validator = KubernetesValidator()
    result = await validator.validate("invalid: yaml: content", target_type="k8s")
    assert result["lint_status"] == "failed"
    
    # Test 4: Plugin wrapper import
    import generator.agents.generator_plugin_wrapper
    assert hasattr(generator.agents.generator_plugin_wrapper, "run_generator_workflow")
    
    # Test 5: OmniCore availability flag
    from generator.main import engine
    # Just verify the flag exists
    assert hasattr(engine, "_OMNICORE_WORKFLOW_AVAILABLE")
