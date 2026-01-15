"""Tests for bootstrap_agent_dev.py"""
import os
import tempfile
from pathlib import Path

import pytest


def test_bootstrap_creates_mocks_directory():
    """Test that bootstrap_agent_dev creates files in tests/mocks directory"""
    from bootstrap_agent_dev import create_dummy_files
    
    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        original_dir = os.getcwd()
        try:
            os.chdir(tmpdir)
            
            # Run the bootstrap function
            create_dummy_files()
            
            # Check that tests/mocks directory was created
            mock_dir = Path("tests/mocks")
            assert mock_dir.exists(), "tests/mocks directory should be created"
            assert mock_dir.is_dir(), "tests/mocks should be a directory"
            
            # Check that expected files were created in mock directory
            expected_files = [
                "audit_log.py",
                "utils.py",
                "testgen_prompt.py",
                "testgen_response_handler.py",
                "testgen_validator.py",
                "deploy_llm_call.py",
            ]
            
            for fname in expected_files:
                fpath = mock_dir / fname
                assert fpath.exists(), f"{fname} should be created in tests/mocks"
                assert fpath.is_file(), f"{fname} should be a file"
                
            # Check that llm_providers directory was created
            llm_providers_dir = mock_dir / "llm_providers"
            assert llm_providers_dir.exists(), "llm_providers directory should be created"
            assert llm_providers_dir.is_dir(), "llm_providers should be a directory"
            
        finally:
            os.chdir(original_dir)


def test_bootstrap_does_not_overwrite_production_files():
    """Test that bootstrap_agent_dev doesn't overwrite existing production files"""
    from bootstrap_agent_dev import create_dummy_files
    
    with tempfile.TemporaryDirectory() as tmpdir:
        original_dir = os.getcwd()
        try:
            os.chdir(tmpdir)
            
            # Create a fake production audit_log.py file
            production_content = "# This is a production file\nprint('production')"
            Path("audit_log.py").write_text(production_content)
            
            # Run the bootstrap function
            create_dummy_files()
            
            # Verify the production file was NOT overwritten
            assert Path("audit_log.py").read_text() == production_content, \
                "Production file should not be overwritten"
            
            # Verify mock file was created in tests/mocks
            mock_file = Path("tests/mocks/audit_log.py")
            assert mock_file.exists(), "Mock file should be created in tests/mocks"
            assert "DUMMY AUDIT LOG" in mock_file.read_text(), \
                "Mock file should contain dummy content"
            
        finally:
            os.chdir(original_dir)


def test_bootstrap_idempotent():
    """Test that running bootstrap multiple times is safe"""
    from bootstrap_agent_dev import create_dummy_files
    
    with tempfile.TemporaryDirectory() as tmpdir:
        original_dir = os.getcwd()
        try:
            os.chdir(tmpdir)
            
            # Run bootstrap twice
            create_dummy_files()
            create_dummy_files()
            
            # Should still work without errors
            mock_dir = Path("tests/mocks")
            assert mock_dir.exists()
            assert (mock_dir / "audit_log.py").exists()
            
        finally:
            os.chdir(original_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
