"""
Test for testgen agent path resolution fix.

This test validates that the _run_testgen method properly:
1. Resolves paths to absolute before computing relative paths
2. Handles files outside repo_path gracefully
3. Logs resolved paths for debugging
4. Prevents path duplication errors
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
import tempfile
import os

from server.services.omnicore_service import OmniCoreService


class TestTestgenPathResolution:
    """Test suite for testgen path resolution fix."""

    @pytest.fixture
    def mock_testgen_service(self):
        """Create service with mocked testgen agent."""
        with patch("server.services.omnicore_service.CONFIG_AVAILABLE", True), \
             patch("server.services.omnicore_service.get_agent_config") as mock_agent_cfg, \
             patch("server.services.omnicore_service.get_llm_config") as mock_llm_cfg, \
             patch("server.services.omnicore_service.get_agent_loader"):
            
            # Configure mocks
            mock_agent_cfg.return_value = Mock(
                strict_mode=False,
                use_llm_clarifier=False,
            )
            mock_llm_cfg.return_value = Mock(
                get_available_providers=Mock(return_value=["openai"]),
            )
            
            service = OmniCoreService()
            
            # Enable testgen agent
            service.agents_available["testgen"] = True
            
            # Mock the testgen class
            mock_testgen_instance = Mock()
            mock_testgen_instance.generate_tests = AsyncMock(return_value={
                "test_files": ["test_main.py"],
                "coverage": 85.0,
                "report": "Tests generated successfully"
            })
            
            # Mock the testgen class constructor
            mock_testgen_class = Mock(return_value=mock_testgen_instance)
            service._testgen_class = mock_testgen_class
            
            # Mock the policy class
            service._testgen_policy_class = Mock(return_value=Mock())
            
            return service

    @pytest.mark.asyncio
    async def test_path_resolution_with_relative_paths(self, mock_testgen_service, tmp_path):
        """Test that paths are properly resolved to absolute before computing relative paths."""
        # Create a temporary directory structure
        job_id = "test-job-123"
        upload_dir = tmp_path / "uploads" / job_id
        generated_dir = upload_dir / "generated"
        generated_dir.mkdir(parents=True)
        
        # Create a sample Python file
        sample_file = generated_dir / "main.py"
        sample_file.write_text("def hello():\n    return 'world'\n")
        
        # Change to temp directory to test relative path handling
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            payload = {
                "code_path": str(generated_dir),
                "coverage_target": 80.0,
            }
            
            # Run testgen
            result = await mock_testgen_service._run_testgen(job_id, payload)
            
            # Verify it succeeded
            assert result["status"] == "success"
            
            # Verify testgen was called with proper arguments
            mock_testgen_service._testgen_class.assert_called_once()
            call_args = mock_testgen_service._testgen_class.call_args
            
            # The repo_path should be absolute
            repo_path_arg = call_args[0][0]
            assert Path(repo_path_arg).is_absolute()
            
            # Verify generate_tests was called
            mock_testgen_instance = mock_testgen_service._testgen_class.return_value
            mock_testgen_instance.generate_tests.assert_called_once()
            
            # Check that target_files are relative paths
            gen_call_args = mock_testgen_instance.generate_tests.call_args
            target_files = gen_call_args[1]["target_files"]
            
            # All files should be relative paths (not absolute)
            for file_path in target_files:
                assert not Path(file_path).is_absolute()
                # Should be like "generated/main.py" not "uploads/job-id/uploads/job-id/generated/main.py"
                assert file_path.count(job_id) <= 1
        
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_files_outside_repo_path_are_skipped(self, mock_testgen_service, tmp_path, caplog):
        """Test that files outside repo_path are gracefully skipped with a warning."""
        # Create a temporary directory structure with files outside repo_path
        job_id = "test-job-456"
        upload_dir = tmp_path / "uploads" / job_id
        upload_dir.mkdir(parents=True)
        
        # Create a different directory outside the repo
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(parents=True)
        outside_file = outside_dir / "external.py"
        outside_file.write_text("def external():\n    pass\n")
        
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Point code_path to the outside directory
            payload = {
                "code_path": str(outside_dir),
                "coverage_target": 80.0,
            }
            
            # Run testgen - should handle gracefully
            result = await mock_testgen_service._run_testgen(job_id, payload)
            
            # Should get an error because no files are in repo_path
            assert result["status"] == "error"
            assert "No code files found" in result["message"]
            
            # Check that a warning was logged
            assert any("outside repo_path" in record.message for record in caplog.records)
        
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_path_resolution_logging(self, mock_testgen_service, tmp_path, caplog):
        """Test that resolved paths are properly logged for debugging."""
        # Create a temporary directory structure
        job_id = "test-job-789"
        upload_dir = tmp_path / "uploads" / job_id
        generated_dir = upload_dir / "generated"
        generated_dir.mkdir(parents=True)
        
        # Create a sample Python file
        sample_file = generated_dir / "calculator.py"
        sample_file.write_text("def add(a, b):\n    return a + b\n")
        
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            # Enable info level logging to capture logs
            import logging
            caplog.set_level(logging.INFO)
            
            payload = {
                "code_path": str(generated_dir),
                "coverage_target": 90.0,
            }
            
            # Run testgen
            result = await mock_testgen_service._run_testgen(job_id, payload)
            
            # Verify logging messages exist
            log_messages = [record.message for record in caplog.records]
            
            # Should log resolved paths
            assert any("Resolved repo_path" in msg for msg in log_messages)
            assert any("Resolved code_dir" in msg for msg in log_messages)
            assert any("Code files (relative to repo_path)" in msg for msg in log_messages)
        
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_multiple_files_path_resolution(self, mock_testgen_service, tmp_path):
        """Test path resolution with multiple Python files."""
        # Create a temporary directory structure with multiple files
        job_id = "test-job-multi"
        upload_dir = tmp_path / "uploads" / job_id
        generated_dir = upload_dir / "generated"
        generated_dir.mkdir(parents=True)
        
        # Create multiple Python files
        files = ["main.py", "utils.py", "models.py"]
        for filename in files:
            file_path = generated_dir / filename
            file_path.write_text(f"# {filename}\n")
        
        # Also create a test file that should be excluded
        test_file = generated_dir / "test_main.py"
        test_file.write_text("# test file\n")
        
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            
            payload = {
                "code_path": str(generated_dir),
                "coverage_target": 75.0,
            }
            
            # Run testgen
            result = await mock_testgen_service._run_testgen(job_id, payload)
            
            # Verify it succeeded
            assert result["status"] == "success"
            
            # Verify generate_tests was called
            mock_testgen_instance = mock_testgen_service._testgen_class.return_value
            gen_call_args = mock_testgen_instance.generate_tests.call_args
            target_files = gen_call_args[1]["target_files"]
            
            # Should have 3 files (test_main.py excluded)
            assert len(target_files) == 3
            
            # All should be relative paths
            for file_path in target_files:
                assert not Path(file_path).is_absolute()
                # Should contain "generated/" prefix
                assert "generated" in file_path
                # Should not have path duplication
                assert file_path.count("generated") == 1
        
        finally:
            os.chdir(original_cwd)

    @pytest.mark.asyncio
    async def test_agent_unavailable_early_exit(self, mock_testgen_service):
        """Test that method returns early when agent is unavailable."""
        # Disable testgen agent
        mock_testgen_service.agents_available["testgen"] = False
        
        payload = {
            "code_path": "./some/path",
            "coverage_target": 80.0,
        }
        
        result = await mock_testgen_service._run_testgen("test-job", payload)
        
        # Should return error immediately
        assert result["status"] == "error"
        assert "not available" in result["message"]
        assert result["agent_available"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
