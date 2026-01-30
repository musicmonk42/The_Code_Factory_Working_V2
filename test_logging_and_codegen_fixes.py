"""
Test for logging format string and codegen pipeline fixes.

This test validates:
1. Logging statements using f-strings instead of % formatting
2. Codegen payload transformation (readme_content -> requirements)
3. Debug logging is present
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLoggingFormatFixes:
    """Test that logging statements use f-strings to avoid PII filter corruption."""

    def test_deploy_agent_logging_uses_fstring(self):
        """Test that deploy_agent.py uses f-string for plugin loading log."""
        with open("generator/agents/deploy_agent/deploy_agent.py", "r") as f:
            content = f.read()
        
        # Should use f-string, not % formatting
        assert 'f"Loaded {len(self.plugins)} plugins from {self.plugin_dir}"' in content
        # Should NOT use % formatting
        assert '"Loaded %d plugins from %s"' not in content

    def test_deploy_prompt_logging_uses_fstring(self):
        """Test that deploy_prompt.py uses f-string for few-shot loading log."""
        with open("generator/agents/deploy_agent/deploy_prompt.py", "r") as f:
            content = f.read()
        
        # Should use f-string, not % formatting
        assert 'f"Loaded {len(examples)} few-shot examples from {few_shot_dir}"' in content
        # Should NOT use % formatting
        assert '"Loaded %d few-shot examples from %s"' not in content

    def test_docgen_prompt_already_uses_fstring(self):
        """Test that docgen_prompt.py already uses f-string (no change needed)."""
        with open("generator/agents/docgen_agent/docgen_prompt.py", "r") as f:
            content = f.read()
        
        # Should already use f-string
        assert 'f"Loaded {len(examples)} few-shot examples from {few_shot_dir}' in content


class TestCodegenPayloadTransformation:
    """Test that codegen receives proper payload with 'requirements' key."""

    def test_run_full_pipeline_transforms_payload(self):
        """Test that _run_full_pipeline transforms readme_content to requirements."""
        with open("server/services/omnicore_service.py", "r") as f:
            content = f.read()
        
        # Check that payload transformation exists
        assert 'codegen_payload = {' in content
        assert '"requirements": payload.get("readme_content", payload.get("requirements", ""))' in content
        assert 'codegen_result = await self._run_codegen(job_id, codegen_payload)' in content

    def test_pipeline_logging_added(self):
        """Test that pipeline logging was added."""
        with open("server/services/omnicore_service.py", "r") as f:
            content = f.read()
        
        # Check for pipeline step logging
        assert 'logger.info(f"[PIPELINE] Job {job_id} starting step: codegen")' in content
        assert 'logger.info(f"[PIPELINE] Job {job_id} completed step: codegen")' in content
        assert 'logger.error(f"[PIPELINE] Job {job_id} failed step: codegen' in content

    def test_codegen_debug_logging_added(self):
        """Test that codegen debug logging was added."""
        with open("server/services/omnicore_service.py", "r") as f:
            content = f.read()
        
        # Check for codegen debug logging
        assert '[CODEGEN] Processing requirements for job' in content

    def test_upload_endpoint_logging_added(self):
        """Test that upload endpoint logging was added."""
        with open("server/routers/generator.py", "r") as f:
            content = f.read()
        
        # Check for README extraction logging
        assert 'logger.info(f"Extracted README content for job {job_id}: {len(readme_content)} bytes")' in content
        assert 'logger.warning(' in content
        assert 'No README content found' in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

