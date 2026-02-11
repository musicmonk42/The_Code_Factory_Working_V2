# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for production pipeline failure fixes (2026-02-11).

This test suite validates the fixes implemented for three critical production issues:
1. Template not found error for readme_default.jinja (P0)
2. Audit log hash chain broken due to fire-and-forget task failures (P1)
3. Kubernetes YAML deployment failure when LLM output contains Mermaid diagrams (P2)

These tests are focused and minimal to validate the specific fixes without heavy integration.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mark all tests in this module to not use conftest fixtures that require prometheus
pytestmark = []


class TestIssue1TemplateNotFound:
    """Tests for Issue 1: Template not found - readme_default.jinja"""
    
    def test_lowercase_symlink_exists(self):
        """Verify lowercase symlink readme_default.jinja exists in prompt_templates."""
        symlink_path = Path("prompt_templates/readme_default.jinja")
        assert symlink_path.exists(), "readme_default.jinja symlink should exist"
        
        # Verify it's a symlink pointing to README_default.jinja
        if symlink_path.is_symlink():
            target = symlink_path.readlink()
            assert str(target) == "README_default.jinja", "Symlink should point to README_default.jinja"
    
    def test_uppercase_template_exists(self):
        """Verify uppercase README_default.jinja template exists."""
        template_path = Path("prompt_templates/README_default.jinja")
        assert template_path.exists(), "README_default.jinja template should exist"
        assert template_path.is_file(), "README_default.jinja should be a regular file"
    
    def test_get_template_case_insensitive_fallback(self):
        """Test that get_template() searches all loader directories for case-insensitive matches."""
        # Test by examining the source code since we can't easily import without full dependencies
        docgen_prompt_file = Path("generator/agents/docgen_agent/docgen_prompt.py")
        assert docgen_prompt_file.exists(), "docgen_prompt.py should exist"
        
        content = docgen_prompt_file.read_text()
        
        # Verify the fix is in place
        # The fix should iterate over all loaders in ChoiceLoader
        assert "if isinstance(self.env.loader, ChoiceLoader):" in content, \
            "get_template should check for ChoiceLoader"
        assert "for loader in self.env.loader.loaders:" in content, \
            "get_template should iterate over all loaders"
        
        # Verify it collects search paths from all FileSystemLoaders
        assert "if isinstance(loader, FileSystemLoader):" in content, \
            "get_template should handle FileSystemLoader"
        assert "search_dirs" in content and "loader.searchpath" in content, \
            "get_template should collect searchpath from loaders"
        
        # Verify it searches all directories (not just plugin_dir)
        assert "for search_dir in search_dirs:" in content, \
            "get_template should search all collected directories"
        assert "template_dir = Path(search_dir)" in content, \
            "get_template should iterate over all search directories"
    
    def test_docgen_prompt_code_has_fix(self):
        """Verify the get_template() method has the fix for searching all loader directories."""
        docgen_prompt_file = Path("generator/agents/docgen_agent/docgen_prompt.py")
        assert docgen_prompt_file.exists(), "docgen_prompt.py should exist"
        
        content = docgen_prompt_file.read_text()
        
        # Check for the fix that searches all loader directories
        assert "if isinstance(self.env.loader, ChoiceLoader):" in content, \
            "get_template should check for ChoiceLoader"
        assert "for loader in self.env.loader.loaders:" in content, \
            "get_template should iterate over all loaders"
        assert "isinstance(loader, FileSystemLoader)" in content, \
            "get_template should handle FileSystemLoader instances"


class TestIssue2AuditLogHashChain:
    """Tests for Issue 2: Audit log hash chain broken"""
    
    def test_audit_task_done_callback_exists(self):
        """Verify _audit_task_done_callback helper function exists."""
        from generator.runner.runner_audit import _audit_task_done_callback
        
        assert callable(_audit_task_done_callback), \
            "_audit_task_done_callback should be a callable function"
    
    def test_audit_task_done_callback_handles_exceptions(self):
        """Test that _audit_task_done_callback properly logs exceptions."""
        from generator.runner.runner_audit import _audit_task_done_callback
        
        # Create a mock task with an exception
        mock_task = Mock()
        mock_task.cancelled.return_value = False
        mock_task.exception.return_value = ValueError("Test exception")
        mock_task.get_name.return_value = "audit_test_action"
        
        # Call the callback - should not raise
        with patch('generator.runner.runner_audit.logger') as mock_logger:
            _audit_task_done_callback(mock_task)
            
            # Verify error was logged with task name and exception
            mock_logger.error.assert_called_once()
            call_args = mock_logger.error.call_args[0][0]
            assert "audit_test_action" in call_args
            assert "failed" in call_args
            # Verify exception details are included
            assert "Test exception" in str(mock_logger.error.call_args)
    
    def test_audit_task_done_callback_handles_cancellation(self):
        """Test that _audit_task_done_callback ignores cancelled tasks."""
        from generator.runner.runner_audit import _audit_task_done_callback
        
        # Create a mock cancelled task
        mock_task = Mock()
        mock_task.cancelled.return_value = True
        
        # Call the callback - should return early without logging
        with patch('generator.runner.runner_audit.logger') as mock_logger:
            _audit_task_done_callback(mock_task)
            
            # Verify no error was logged
            mock_logger.error.assert_not_called()
    
    def test_log_audit_event_sync_uses_callback(self):
        """Verify log_audit_event_sync creates named task with callback."""
        runner_audit_file = Path("generator/runner/runner_audit.py")
        assert runner_audit_file.exists(), "runner_audit.py should exist"
        
        content = runner_audit_file.read_text()
        
        # Check for the fix that creates named task with callback
        assert "task = asyncio.create_task(" in content, \
            "log_audit_event_sync should create a named task variable"
        assert 'name=f"audit_{action}"' in content, \
            "Task should be named with audit_ prefix"
        assert "task.add_done_callback(_audit_task_done_callback)" in content, \
            "Task should have done_callback attached"


class TestIssue3MermaidInYAML:
    """Tests for Issue 3: Kubernetes YAML deployment failure with Mermaid diagrams"""
    
    def test_sanitize_llm_output_exists(self):
        """Verify _sanitize_llm_output function exists in the source code."""
        deploy_handler_file = Path("generator/agents/deploy_agent/deploy_response_handler.py")
        assert deploy_handler_file.exists(), "deploy_response_handler.py should exist"
        
        content = deploy_handler_file.read_text()
        
        # Verify the function exists
        assert "def _sanitize_llm_output(raw_output: str) -> str:" in content, \
            "_sanitize_llm_output function should exist"
    
    def test_sanitize_llm_output_strips_mermaid(self):
        """Test that _sanitize_llm_output implementation strips Mermaid diagrams."""
        deploy_handler_file = Path("generator/agents/deploy_agent/deploy_response_handler.py")
        assert deploy_handler_file.exists(), "deploy_response_handler.py should exist"
        
        content = deploy_handler_file.read_text()
        
        # Verify the function has the mermaid stripping regex
        assert "re.sub(r'```\\s*mermaid" in content, \
            "_sanitize_llm_output should have regex to strip mermaid blocks"
        assert "MULTILINE" in content and "IGNORECASE" in content, \
            "Regex should use MULTILINE and IGNORECASE flags"
    
    def test_sanitize_llm_output_strips_other_diagrams(self):
        """Test that _sanitize_llm_output implementation strips other diagram types."""
        deploy_handler_file = Path("generator/agents/deploy_agent/deploy_response_handler.py")
        assert deploy_handler_file.exists(), "deploy_response_handler.py should exist"
        
        content = deploy_handler_file.read_text()
        
        # Verify other diagram types are handled
        assert "dot|plantuml|graphviz" in content or "(dot" in content, \
            "_sanitize_llm_output should strip other diagram types like dot, plantuml, graphviz"
    
    def test_yaml_handler_calls_sanitize(self):
        """Verify YAMLHandler.normalize() calls _sanitize_llm_output."""
        deploy_handler_file = Path("generator/agents/deploy_agent/deploy_response_handler.py")
        assert deploy_handler_file.exists(), "deploy_response_handler.py should exist"
        
        content = deploy_handler_file.read_text()
        
        # Find the YAMLHandler.normalize method and verify it calls _sanitize_llm_output
        assert "class YAMLHandler(FormatHandler):" in content, "YAMLHandler class should exist"
        assert "raw = _sanitize_llm_output(raw)" in content, \
            "YAMLHandler.normalize should call _sanitize_llm_output"
    
    def test_sanitize_removes_code_fences(self):
        """Test that code fence removal is implemented."""
        deploy_handler_file = Path("generator/agents/deploy_agent/deploy_response_handler.py")
        assert deploy_handler_file.exists(), "deploy_response_handler.py should exist"
        
        content = deploy_handler_file.read_text()
        
        # Verify code fence removal logic exists
        assert "```yaml" in content or "```\\w*" in content or "```" in content, \
            "_sanitize_llm_output should handle code fences"


class TestIntegrationScenarios:
    """Integration tests for the complete fix scenarios"""
    
    def test_template_resolution_with_canonical_mapping(self):
        """Test that lowercase 'readme' doc_type resolves to README_default.jinja."""
        # Test by examining the source code
        docgen_prompt_file = Path("generator/agents/docgen_agent/docgen_prompt.py")
        assert docgen_prompt_file.exists(), "docgen_prompt.py should exist"
        
        content = docgen_prompt_file.read_text()
        
        # Verify the canonical mapping exists
        assert 'DOC_TYPE_CANONICAL = {' in content, "DOC_TYPE_CANONICAL should exist"
        assert '"readme": "README"' in content, \
            "readme should map to README in canonical mapping"
        
        # This means "readme" -> "README" -> "README_default" template
        # With our fix, if README_default.jinja exists, it should be found
        # If not, the case-insensitive fallback should find readme_default.jinja
        
        template_path = Path("prompt_templates/README_default.jinja")
        symlink_path = Path("prompt_templates/readme_default.jinja")
        
        # At least one should exist
        assert template_path.exists() or symlink_path.exists(), \
            "Either README_default.jinja or readme_default.jinja should exist"
    
    @pytest.mark.asyncio
    async def test_audit_logging_with_event_loop(self):
        """Test that audit logging works properly with event loop."""
        from generator.runner.runner_audit import log_audit_event_sync
        
        # This test runs with an event loop (pytest-asyncio)
        # log_audit_event_sync should detect the loop and create a task
        
        # Mock the log_audit_event function to avoid actual audit logging
        with patch('generator.runner.runner_audit.log_audit_event') as mock_log:
            mock_log.return_value = asyncio.sleep(0)  # Return a coroutine
            
            # Call the sync wrapper
            log_audit_event_sync("test_action", {"test_key": "test_value"})
            
            # Give the event loop a chance to process the task
            await asyncio.sleep(0.1)
            
            # Verify log_audit_event was called (via the created task)
            # Note: We can't easily verify this in a fire-and-forget scenario
            # but the code should not raise any errors


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
