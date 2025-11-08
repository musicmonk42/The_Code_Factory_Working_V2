
# test_migrate_prompts.py
# Industry-grade test suite for migrate_prompts.py, ensuring compliance with regulated standards.
# Covers unit and integration tests for prompt migration, with traceability, reproducibility, and security.

import pytest
import asyncio
import os
import json
import ast
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import logging
import uuid
import sys
from jinja2 import TemplateSyntaxError

# Import functions from migrate_prompts
from migrate_prompts import (
    extract_prompts_from_dict, lint_template, replace_prompt_dict_with_loader,
    migrate_file, migrate_dir, generate_summary_report
)

# Configure logging for traceability and auditability
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Fixture for temporary directory
@pytest.fixture
def tmp_path(tmp_path_factory):
    """Create a temporary directory for test files."""
    return tmp_path_factory.mktemp("migrate_prompts_test")

# Fixture for audit log
@pytest.fixture
def audit_log(tmp_path):
    """Set up an audit log file for traceability."""
    log_file = tmp_path / "audit.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s [trace_id=%(trace_id)s]'
    ))
    logger.addHandler(handler)
    yield log_file
    logger.removeHandler(handler)

# Helper function to log test execution for auditability
def log_test_execution(test_name, result, trace_id):
    """Log test execution details for audit trail."""
    logger.debug(
        f"Test {test_name}: {result}",
        extra={'trace_id': trace_id}
    )

# Test class for utility functions
class TestMigratePromptsUtilities:
    """Tests for utility functions in migrate_prompts.py."""

    def test_extract_prompts_from_dict(self, audit_log):
        """Test extraction of prompts from a dictionary node."""
        trace_id = str(uuid.uuid4())
        source_code = """
PROMPT_TEMPLATES = {
    'prompt1': '''Hello {{ name }}''',
    'prompt2': '''Multiline\nprompt'''
}
"""
        tree = ast.parse(source_code)
        dict_node = tree.body[0].value  # Get the dict node
        prompts = extract_prompts_from_dict(dict_node)
        assert prompts == [('prompt1', 'Hello {{ name }}'), ('prompt2', 'Multiline\nprompt')]
        log_test_execution("test_extract_prompts_from_dict", "Passed", trace_id)

    def test_extract_prompts_empty_dict(self, audit_log):
        """Test extraction from an empty dictionary."""
        trace_id = str(uuid.uuid4())
        source_code = "PROMPT_TEMPLATES = {}"
        tree = ast.parse(source_code)
        dict_node = tree.body[0].value
        prompts = extract_prompts_from_dict(dict_node)
        assert prompts == []
        log_test_execution("test_extract_prompts_empty_dict", "Passed", trace_id)

    def test_lint_template_valid(self, audit_log):
        """Test linting a valid Jinja2 template."""
        trace_id = str(uuid.uuid4())
        template = "Hello {{ name }}"
        result = lint_template(template)
        assert result is None
        log_test_execution("test_lint_template_valid", "Passed", trace_id)

    def test_lint_template_invalid(self, audit_log):
        """Test linting an invalid Jinja2 template."""
        trace_id = str(uuid.uuid4())
        template = "Hello {{ name "
        result = lint_template(template)
        assert isinstance(result, TemplateSyntaxError)
        log_test_execution("test_lint_template_invalid", "Passed", trace_id)

    def test_replace_prompt_dict_with_loader(self, audit_log):
        """Test replacing prompt dict with loader code."""
        trace_id = str(uuid.uuid4())
        source_code = """
PROMPT_TEMPLATES = {
    'prompt1': '''Hello {{ name }}'''
}
"""
        tree = ast.parse(source_code)
        new_tree = replace_prompt_dict_with_loader(tree, Path("prompts"))
        new_code = ast.unparse(new_tree).strip()
        assert "from jinja2 import Environment, FileSystemLoader" in new_code
        assert "PROMPT_TEMPLATES = {" in new_code
        assert "env = Environment(loader=FileSystemLoader('prompts'))" in new_code
        log_test_execution("test_replace_prompt_dict_with_loader", "Passed", trace_id)

# Test class for file migration
class TestMigratePromptsFile:
    """Tests for file migration in migrate_prompts.py."""

    @pytest.mark.asyncio
    async def test_migrate_file_success(self, tmp_path, audit_log):
        """Test successful migration of a single file."""
        trace_id = str(uuid.uuid4())
        source_file = tmp_path / "source.py"
        source_file.write_text("""
PROMPT_TEMPLATES = {
    'prompt1': '''Hello {{ name }}''',
    'prompt2': '''Multiline\nprompt'''
}
""")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        with patch('builtins.open', new=MagicMock()) as mock_open:
            mock_open.side_effect = [MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: source_file.read_text(), write=lambda x: None)))]
            report = await migrate_file(source_file, dest_dir, dry_run=False, verbose=True, backup=True)

        assert report["status"] == "success"
        assert report["file"] == str(source_file)
        assert (dest_dir / "prompt1.j2").exists()
        assert (dest_dir / "prompt2.j2").exists()
        assert (tmp_path / "source.py.bak").exists()
        log_test_execution("test_migrate_file_success", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_migrate_file_dry_run(self, tmp_path, audit_log):
        """Test migration in dry-run mode."""
        trace_id = str(uuid.uuid4())
        source_file = tmp_path / "source.py"
        source_file.write_text("""
PROMPT_TEMPLATES = {
    'prompt1': '''Hello {{ name }}'''
}
""")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        with patch('builtins.open', new=MagicMock()):
            report = await migrate_file(source_file, dest_dir, dry_run=True, verbose=True, backup=True)

        assert report["status"] == "success"
        assert not (dest_dir / "prompt1.j2").exists()  # No files written in dry-run
        assert not (tmp_path / "source.py.bak").exists()
        log_test_execution("test_migrate_file_dry_run", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_migrate_file_no_prompts(self, tmp_path, audit_log):
        """Test migration with no PROMPT_TEMPLATES in file."""
        trace_id = str(uuid.uuid4())
        source_file = tmp_path / "source.py"
        source_file.write_text("x = 1")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        report = await migrate_file(source_file, dest_dir, dry_run=False, verbose=True, backup=True)
        assert report["status"] == "skipped"
        assert report["reason"] == "No PROMPT_TEMPLATES found"
        log_test_execution("test_migrate_file_no_prompts", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_migrate_file_permission_error(self, tmp_path, audit_log):
        """Test handling of permission errors during file migration."""
        trace_id = str(uuid.uuid4())
        source_file = tmp_path / "source.py"
        source_file.write_text("PROMPT_TEMPLATES = {'prompt1': '''Hello {{ name }}'''}")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            report = await migrate_file(source_file, dest_dir, dry_run=False, verbose=True, backup=True)
        assert report["status"] == "error"
        assert "Permission denied" in report["error"]
        log_test_execution("test_migrate_file_permission_error", "Passed", trace_id)

# Test class for directory migration
class TestMigratePromptsDir:
    """Tests for directory migration in migrate_prompts.py."""

    @pytest.mark.asyncio
    async def test_migrate_dir_recursive(self, tmp_path, audit_log):
        """Test recursive directory migration."""
        trace_id = str(uuid.uuid4())
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "file1.py").write_text("PROMPT_TEMPLATES = {'prompt1': '''Hello {{ name }}'''}")
        sub_dir = source_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "file2.py").write_text("PROMPT_TEMPLATES = {'prompt2': '''World {{ user }}'''}")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        with patch('builtins.open', new=MagicMock()) as mock_open:
            mock_open.side_effect = [MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: x.read_text(), write=lambda x: None))) for x in [source_dir / "file1.py", sub_dir / "file2.py"]]
            reports = await migrate_dir(source_dir, dest_dir, recursive=True, dry_run=False, verbose=True, backup=True)

        assert len(reports) == 2
        assert all(r["status"] == "success" for r in reports)
        assert (dest_dir / "prompt1.j2").exists()
        assert (dest_dir / "prompt2.j2").exists()
        assert (source_dir / "file1.py.bak").exists()
        assert (sub_dir / "file2.py.bak").exists()
        log_test_execution("test_migrate_dir_recursive", "Passed", trace_id)

    @pytest.mark.asyncio
    async def test_migrate_dir_non_recursive(self, tmp_path, audit_log):
        """Test non-recursive directory migration."""
        trace_id = str(uuid.uuid4())
        source_dir = tmp_path / "src"
        source_dir.mkdir()
        (source_dir / "file1.py").write_text("PROMPT_TEMPLATES = {'prompt1': '''Hello {{ name }}'''}")
        sub_dir = source_dir / "sub"
        sub_dir.mkdir()
        (sub_dir / "file2.py").write_text("PROMPT_TEMPLATES = {'prompt2': '''World {{ user }}'''}")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        reports = await migrate_dir(source_dir, dest_dir, recursive=False, dry_run=False, verbose=True, backup=True)
        assert len(reports) == 1
        assert reports[0]["status"] == "success"
        assert (dest_dir / "prompt1.j2").exists()
        assert not (dest_dir / "prompt2.j2").exists()
        log_test_execution("test_migrate_dir_non_recursive", "Passed", trace_id)

# Test class for summary reporting
class TestMigratePromptsReporting:
    """Tests for summary reporting in migrate_prompts.py."""

    def test_generate_summary_report(self, audit_log):
        """Test generating a summary report."""
        trace_id = str(uuid.uuid4())
        reports = [
            {"file": "file1.py", "status": "success", "prompts_migrated": ["prompt1"]},
            {"file": "file2.py", "status": "error", "error": "Syntax error"}
        ]
        summary = generate_summary_report(reports)
        assert "Processed 2 files" in summary
        assert "1 succeeded" in summary
        assert "1 failed" in summary
        log_test_execution("test_generate_summary_report", "Passed", trace_id)

# Integration test class
class TestMigratePromptsIntegration:
    """Integration tests for migrate_prompts.py."""

    @pytest.mark.asyncio
    async def test_full_migration_pipeline(self, tmp_path, audit_log):
        """Test full migration pipeline from file to report."""
        trace_id = str(uuid.uuid4())
        source_file = tmp_path / "source.py"
        source_file.write_text("""
PROMPT_TEMPLATES = {
    'prompt1': '''Hello {{ name }}''',
    'prompt2': '''Invalid {{ syntax'''
}
""")
        dest_dir = tmp_path / "prompts"
        dest_dir.mkdir()

        with patch('builtins.open', new=MagicMock()) as mock_open:
            mock_open.side_effect = [MagicMock(__enter__=MagicMock(return_value=MagicMock(read=lambda: source_file.read_text(), write=lambda x: None)))]
            report = await migrate_file(source_file, dest_dir, dry_run=False, verbose=True, backup=True)

        assert report["status"] == "error"  # Due to invalid Jinja2 syntax in prompt2
        assert (dest_dir / "prompt1.j2").exists()
        assert (tmp_path / "source.py.bak").exists()
        with open(dest_dir / "prompt1.j2", "r", encoding="utf-8") as f:
            assert f.read() == "Hello {{ name }}"
        with open(audit_log, "r", encoding="utf-8") as f:
            audit_content = f.read()
        assert trace_id in audit_content
        log_test_execution("test_full_migration_pipeline", "Passed", trace_id)

# Run tests with audit logging
if __name__ == "__main__":
    pytest.main(["-v", "--log-level=DEBUG"])
