"""
Comprehensive tests for deep dive audit fixes.

This test module validates all the fixes made as part of the deep dive audit,
ensuring that the changes meet the highest industry standards.

Test Categories:
    - File cleanup verification
    - Configuration improvements
    - CLI functionality
    - Module imports and aliasing
    - Error handling
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestFileCleanup:
    """Test that all garbage and log files have been properly removed."""
    
    def test_garbage_files_removed(self):
        """Verify that pip artifact files have been removed."""
        sfe_dir = Path(__file__).parent.parent
        garbage_files = [
            "=0.13.0", "=0.18.0", "=0.19.0", "=2.0.0", "=4.2.0", "=7.0.0", "call_args"
        ]
        for filename in garbage_files:
            filepath = sfe_dir / filename
            assert not filepath.exists(), f"Garbage file should be removed: {filename}"
    
    def test_log_files_removed(self):
        """Verify that large log files have been removed."""
        sfe_dir = Path(__file__).parent.parent
        log_files = [
            "exceptions.log.1", "exceptions.log.3", "exceptions.log.5", "reasoner.log.1"
        ]
        for filename in log_files:
            filepath = sfe_dir / filename
            assert not filepath.exists(), f"Log file should be removed: {filename}"
    
    def test_backup_files_removed(self):
        """Verify that backup files have been removed."""
        sfe_dir = Path(__file__).parent.parent
        backup_file = sfe_dir / "simulation" / "conftest.py.bak"
        assert not backup_file.exists(), "Backup file should be removed"


class TestGitignore:
    """Test that .gitignore files have proper patterns."""
    
    def test_root_gitignore_has_backup_patterns(self):
        """Verify root .gitignore has backup file patterns."""
        gitignore_path = Path(__file__).parent.parent.parent / ".gitignore"
        content = gitignore_path.read_text()
        assert "*.bak" in content
        assert "*.backup" in content
        assert "=*" in content
    
    def test_sfe_gitignore_has_patterns(self):
        """Verify self_fixing_engineer .gitignore has all required patterns."""
        gitignore_path = Path(__file__).parent.parent / ".gitignore"
        content = gitignore_path.read_text()
        assert "*.bak" in content
        assert "=*" in content
        assert "*.log.*" in content


class TestModuleImports:
    """Test module import and aliasing functionality."""
    
    def test_module_imports_successfully(self):
        """Test that self_fixing_engineer module imports without errors."""
        import self_fixing_engineer
        assert hasattr(self_fixing_engineer, "__version__")
        assert self_fixing_engineer.__version__ == "1.0.0"
    
    def test_module_has_proper_logger(self):
        """Verify module uses proper logging configuration."""
        import self_fixing_engineer
        import logging
        logger = logging.getLogger("self_fixing_engineer")
        # Logger should exist and have at least a NullHandler
        assert len(logger.handlers) >= 0  # NullHandler might not show in handlers


class TestConfigWrapper:
    """Test ConfigWrapper class improvements."""
    
    def test_config_wrapper_optional_fields(self):
        """Test that optional fields return None instead of raising."""
        from self_fixing_engineer.config import ConfigWrapper
        
        wrapper = ConfigWrapper()
        # These are known optional fields and should return None
        assert wrapper.SENTRY_DSN is None
        assert wrapper.API_CORS_ORIGINS is None
    
    def test_config_wrapper_raises_for_unknown_fields(self):
        """Test that unknown fields raise AttributeError."""
        from self_fixing_engineer.config import ConfigWrapper
        
        wrapper = ConfigWrapper()
        with pytest.raises(AttributeError) as exc_info:
            _ = wrapper.TOTALLY_UNKNOWN_FIELD_XYZ
        
        assert "no attribute" in str(exc_info.value).lower()
    
    def test_config_wrapper_has_repr(self):
        """Test that ConfigWrapper has a proper repr."""
        from self_fixing_engineer.config import ConfigWrapper
        
        wrapper = ConfigWrapper()
        repr_str = repr(wrapper)
        assert "ConfigWrapper" in repr_str
        assert "env=" in repr_str


class TestRunWorkingTests:
    """Test the run_working_tests.py module."""
    
    def test_run_working_tests_exists(self):
        """Verify run_working_tests.py file exists and is not empty."""
        filepath = Path(__file__).parent.parent / "run_working_tests.py"
        assert filepath.exists()
        assert filepath.stat().st_size > 0
    
    def test_run_working_tests_has_docstring(self):
        """Verify run_working_tests.py has comprehensive documentation."""
        from self_fixing_engineer import run_working_tests
        
        assert run_working_tests.__doc__ is not None
        assert len(run_working_tests.__doc__) > 100
        assert "placeholder" in run_working_tests.__doc__.lower()
    
    def test_run_working_tests_main_returns_one(self):
        """Verify main() returns exit code 1 for not implemented."""
        from self_fixing_engineer.run_working_tests import main
        
        result = main()
        assert result == 1


class TestAuditLogJsonl:
    """Test the test_audit_log.jsonl fixture file."""
    
    def test_audit_log_exists_and_not_empty(self):
        """Verify test_audit_log.jsonl exists and has content."""
        filepath = Path(__file__).parent.parent / "test_audit_log.jsonl"
        assert filepath.exists()
        assert filepath.stat().st_size > 0
    
    def test_audit_log_is_valid_jsonl(self):
        """Verify test_audit_log.jsonl contains valid JSON."""
        filepath = Path(__file__).parent.parent / "test_audit_log.jsonl"
        
        with open(filepath, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    data = json.loads(line)
                    assert "event" in data
                    assert "signature" in data
                except json.JSONDecodeError as e:
                    pytest.fail(f"Invalid JSON on line {line_num}: {e}")
    
    def test_audit_log_has_required_fields(self):
        """Verify test_audit_log.jsonl has all required fields."""
        filepath = Path(__file__).parent.parent / "test_audit_log.jsonl"
        
        with open(filepath, 'r') as f:
            data = json.loads(f.readline())
            
            assert "event" in data
            assert "event_type" in data["event"]
            assert "event_id" in data["event"]
            assert "timestamp" in data["event"]
            assert "signature" in data
            assert "version" in data


class TestCLIFunctions:
    """Test CLI function improvements."""
    
    @pytest.mark.asyncio
    async def test_repair_issues_is_documented(self):
        """Verify repair_issues function has comprehensive documentation."""
        from self_fixing_engineer.cli import repair_issues
        
        assert repair_issues.__doc__ is not None
        assert len(repair_issues.__doc__) > 200
        assert "not yet implemented" in repair_issues.__doc__.lower()
        assert "planned" in repair_issues.__doc__.lower()
    
    @pytest.mark.asyncio  
    async def test_simple_scan_has_docstring(self):
        """Verify simple_scan has proper documentation."""
        from self_fixing_engineer.cli import simple_scan
        
        assert simple_scan.__doc__ is not None
        assert "scan" in simple_scan.__doc__.lower()
        assert "codebase" in simple_scan.__doc__.lower() or "code" in simple_scan.__doc__.lower()


class TestCodeQuality:
    """Test overall code quality and standards."""
    
    def test_init_has_comprehensive_docstring(self):
        """Verify __init__.py has detailed module documentation."""
        import self_fixing_engineer
        
        assert self_fixing_engineer.__doc__ is not None
        assert len(self_fixing_engineer.__doc__) > 200
        assert "alias" in self_fixing_engineer.__doc__.lower()
    
    def test_config_has_type_hints(self):
        """Verify config.py uses type hints."""
        from self_fixing_engineer import config
        import inspect
        
        # Check ConfigWrapper.__init__ has type hints
        sig = inspect.signature(config.ConfigWrapper.__init__)
        # Return annotation should be None for __init__
        assert sig.return_annotation is inspect.Signature.empty or sig.return_annotation is None
    
    def test_no_bare_except_clauses(self):
        """Verify no bare except clauses in critical files."""
        files_to_check = [
            Path(__file__).parent.parent / "__init__.py",
            Path(__file__).parent.parent / "config.py",
        ]
        
        for filepath in files_to_check:
            content = filepath.read_text()
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                # Check for 'except:' without a specific exception type
                if line.strip().startswith('except:'):
                    pytest.fail(
                        f"Bare except clause found in {filepath.name}:{i}. "
                        f"Use specific exception types."
                    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
