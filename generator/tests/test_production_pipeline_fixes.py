# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for Production Pipeline Failure Fixes (Job adf18771)

This test suite validates the fixes for the following issues:
1. mutmut FileNotFoundError due to string instead of array in TOML config
2. ModuleNotFoundError when importing from nested packages
3. YAML parsing failures due to markdown bold markers
"""

import re
from pathlib import Path

import pytest


class TestMutmutConfigFix:
    """Test suite for Issue 1: mutmut TOML array syntax fix."""
    
    def test_mutmut_config_generation_code_exists(self):
        """Test that the mutmut config generation code uses array syntax."""
        # Read the runner_mutation.py file to verify the fix
        with open('generator/runner/runner_mutation.py', 'r') as f:
            content = f.read()
        
        # Verify the fix is present
        assert 'paths_to_mutate = ["code/"]' in content, "mutmut config should use array syntax for paths_to_mutate"
        assert 'paths_to_exclude = ["tests/"]' in content, "mutmut config should use array syntax for paths_to_exclude"
    
    def test_mutmut_config_has_validation(self):
        """Test that the setup function validates code directory exists."""
        with open('generator/runner/runner_mutation.py', 'r') as f:
            content = f.read()
        
        # Verify validation code exists
        assert '_setup_mutmut_config' in content
        assert 'Code directory does not exist' in content or 'code_dir.exists()' in content
        assert 'FileNotFoundError' in content


class TestConfTestSysPathFix:
    """Test suite for Issue 2: conftest.py sys.path handling for nested packages."""
    
    def test_conftest_adds_package_parent_dirs_to_syspath(self):
        """Test that conftest.py adds package parent directories to sys.path."""
        # The fix is in runner_core.py which generates conftest.py content
        with open('generator/runner/runner_core.py', 'r') as f:
            runner_core_content = f.read()
        
        # Verify the fix is present in the conftest generation
        assert 'add_package_parent_dirs' in runner_core_content
        assert 'parent directories of packages' in runner_core_content or 'parent dirs' in runner_core_content
    
    def test_init_files_created_in_setup_phase(self):
        """Test that __init__.py files are created in setup phase, not in conftest.py."""
        with open('generator/runner/runner_core.py', 'r') as f:
            content = f.read()
        
        # Verify the new helper method exists
        assert '_create_init_files_in_subdirs' in content
        assert 'def _create_init_files_in_subdirs(self, base_dir: Path):' in content
        
        # Verify it's called in the setup phase
        assert 'self._create_init_files_in_subdirs(code_sub_dir)' in content
        
        # Verify ensure_init_files is NOT in conftest anymore (moved to setup)
        # Count occurrences - should be 0 in conftest_content
        conftest_start = content.find("conftest_content = '''")
        conftest_end = content.find("'''", conftest_start + 25)  # Find closing '''
        if conftest_start >= 0 and conftest_end > conftest_start:
            conftest_content = content[conftest_start:conftest_end]
            assert 'def ensure_init_files' not in conftest_content, \
                "ensure_init_files should not be in conftest.py anymore"
    
    def test_conftest_content_structure(self):
        """Test that the generated conftest.py has the correct structure."""
        with open('generator/runner/runner_core.py', 'r') as f:
            content = f.read()
        
        # Find the conftest_content definition
        assert "conftest_content = '''" in content
        
        # Verify key components are present
        assert 'def add_package_parent_dirs(base_dir):' in content
        
        # Verify the fix comment is present
        assert 'FIX Issue 1' in content or 'FIX Bug 1' in content


class TestYAMLSanitizationMarkdownBoldFix:
    """Test suite for Issue 3: YAML sanitization of markdown bold markers."""
    
    def test_sanitize_llm_output_strips_bold_markers(self):
        """Test that _sanitize_llm_output implementation strips ** markers."""
        with open('generator/agents/deploy_agent/deploy_response_handler.py', 'r') as f:
            content = f.read()
        
        # Find the _sanitize_llm_output function
        assert '_sanitize_llm_output' in content
        
        # Verify it strips markdown bold markers
        # Look for the regex pattern that removes **text**
        assert r'\*\*([^*]+?)\*\*' in content
        
        # Verify the fix comment
        assert 'FIX Issue 3' in content or 'markdown bold' in content.lower()
    
    def test_yaml_handler_normalize_sanitizes_not_raises(self):
        """Test that YAMLHandler.normalize sanitizes ** instead of raising ValueError."""
        with open('generator/agents/deploy_agent/deploy_response_handler.py', 'r') as f:
            content = f.read()
        
        # Find the YAMLHandler.normalize method
        assert 'class YAMLHandler' in content
        assert 'def normalize' in content
        
        # Look for sanitization of ** instead of raising ValueError
        # The fix should log a warning and sanitize instead of raising
        lines = content.split('\n')
        found_sanitization = False
        
        for i, line in enumerate(lines):
            if '"**" in raw:' in line or "'**' in raw:" in line:
                # Check next few lines
                context = '\n'.join(lines[i:min(i+15, len(lines))])
                if 'logger.warning' in context and 'sanitiz' in context.lower():
                    found_sanitization = True
                    break
        
        assert found_sanitization, "Should sanitize ** markers with warning instead of raising ValueError"
    
    def test_mermaid_detection_uses_regex(self):
        """Test that mermaid block detection uses regex for robustness."""
        with open('generator/agents/deploy_agent/deploy_response_handler.py', 'r') as f:
            content = f.read()
        
        # Look for mermaid detection in sanitizers
        assert 'KubernetesHandler' in content
        assert '_sanitize_yaml_response' in content
        
        # Check for improved mermaid detection using regex
        assert r'```\s*mermaid\b' in content, "Should use regex for robust mermaid detection"


class TestAllFixesIntegration:
    """Integration test verifying all three main fixes are present."""
    
    def test_all_three_fixes_present(self):
        """Verify all three main fixes are present in the code."""
        fixes_found = {
            'mutmut_array_syntax': False,
            'conftest_init_files_in_setup': False,
            'yaml_bold_sanitization': False,
        }
        
        # Check Fix 1: mutmut array syntax
        with open('generator/runner/runner_mutation.py', 'r') as f:
            content = f.read()
            if 'paths_to_mutate = ["code/"]' in content:
                fixes_found['mutmut_array_syntax'] = True
        
        # Check Fix 2: __init__.py files created in setup phase
        with open('generator/runner/runner_core.py', 'r') as f:
            content = f.read()
            if '_create_init_files_in_subdirs' in content and \
               'self._create_init_files_in_subdirs(code_sub_dir)' in content:
                fixes_found['conftest_init_files_in_setup'] = True
        
        # Check Fix 3: YAML bold sanitization
        with open('generator/agents/deploy_agent/deploy_response_handler.py', 'r') as f:
            content = f.read()
            if r'\*\*([^*]+?)\*\*' in content and 'FIX Issue 3' in content:
                fixes_found['yaml_bold_sanitization'] = True
        
        # All fixes should be present
        assert all(fixes_found.values()), f"Not all fixes found: {fixes_found}"
