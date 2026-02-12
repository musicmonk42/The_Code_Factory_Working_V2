# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
test_pipeline_issue_fixes.py

Focused tests for the three interconnected pipeline issues:
1. LLM Ensemble API Missing Provider Key (already fixed)
2. Deploy Placeholder Substitution Failure (BASE_IMAGE)
3. TestGen Fallback Test Syntax Errors

These tests validate the minimal changes made to fix production issues.
"""

import ast
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the fixed modules
from generator.agents.deploy_agent.deploy_response_handler import (
    HandlerRegistry,
    handle_deploy_response,
)
from generator.agents.testgen_agent.testgen_agent import TestgenAgent

# Set loop scope to class to prevent "Event loop is closed" errors
# when multiple async tests in the same class share fixtures
pytestmark = pytest.mark.asyncio(loop_scope="class")


class TestDeployBasePlaceholderFix:
    """Test that BASE_IMAGE placeholder is properly substituted."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / ".git").mkdir()
            (repo_path / "README.md").write_text("# Test Repo")
            yield repo_path

    @pytest.fixture
    def registry(self):
        """Provide a handler registry."""
        return HandlerRegistry()

    @pytest.mark.asyncio
    async def test_base_image_placeholder_substitution(self, temp_repo, registry):
        """
        Test that {BASE_IMAGE} placeholder is substituted with default value.
        
        This test validates Issue 2 fix: Deploy configs with {BASE_IMAGE}
        placeholder should be substituted with 'python:3.11-slim' by default.
        """
        # Dockerfile with BASE_IMAGE placeholder
        dockerfile_with_placeholder = """FROM {BASE_IMAGE}
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "app.py"]
"""
        
        # Process the deploy response
        result = await handle_deploy_response(
            raw_response=dockerfile_with_placeholder,
            handler_registry=registry,
            output_format="dockerfile",
            repo_path=str(temp_repo),
        )
        
        # Verify placeholder was substituted
        assert "{BASE_IMAGE}" not in result["final_config_output"], (
            "BASE_IMAGE placeholder should be substituted"
        )
        assert "python:3.11-slim" in result["final_config_output"], (
            "BASE_IMAGE should default to python:3.11-slim"
        )

    @pytest.mark.asyncio
    async def test_multiple_placeholders_substitution(self, temp_repo, registry):
        """
        Test that multiple common placeholders are substituted.
        """
        dockerfile_with_placeholders = """FROM {BASE_IMAGE}
WORKDIR /app
COPY . .
EXPOSE {PORT}
ENV NODE_ENV={NODE_ENV}
ENV HOST={HOST}
CMD ["python", "app.py"]
"""
        
        result = await handle_deploy_response(
            raw_response=dockerfile_with_placeholders,
            handler_registry=registry,
            output_format="dockerfile",
            repo_path=str(temp_repo),
        )
        
        # Verify all placeholders were substituted
        output = result["final_config_output"]
        assert "{BASE_IMAGE}" not in output
        assert "{PORT}" not in output
        assert "{NODE_ENV}" not in output
        assert "{HOST}" not in output
        
        # Verify defaults
        assert "python:3.11-slim" in output
        assert "8000" in output
        assert "production" in output
        assert "0.0.0.0" in output


class TestFallbackTestSyntax:
    """Test that fallback test generation produces valid Python syntax."""

    @pytest.fixture
    def temp_repo(self):
        """Create a temporary repository for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            (repo_path / ".git").mkdir()
            (repo_path / "README.md").write_text("# Test Repo")
            yield repo_path

    @pytest.fixture
    def testgen_agent(self, temp_repo):
        """Create a TestgenAgent instance."""
        # Note: TestgenAgent requires repo_path, not RunnerConfig
        return TestgenAgent(repo_path=str(temp_repo), arbiter_bridge=None)

    @pytest.mark.asyncio
    async def test_fallback_test_has_valid_syntax(self, testgen_agent):
        """
        Test that fallback tests generated for files with syntax errors
        are themselves syntactically valid Python.
        
        This test validates Issue 3 fix: The f-string template should
        generate valid Python code without syntax errors.
        """
        # Create a code file with syntax errors to trigger fallback
        code_files = {
            "bad_syntax.py": "def func( invalid syntax here"
        }
        
        # Generate basic tests (which should use fallback for syntax errors)
        basic_tests = await testgen_agent._generate_basic_tests(
            code_files, "python", "test-run-123"
        )
        
        # Verify we got a test file
        assert len(basic_tests) > 0, "Should generate fallback tests"
        
        # Get the generated test content
        test_file_path = list(basic_tests.keys())[0]
        test_content = basic_tests[test_file_path]
        
        # Verify the generated test has valid Python syntax
        try:
            ast.parse(test_content)
        except SyntaxError as e:
            pytest.fail(
                f"Generated fallback test has syntax error at line {e.lineno}: {e.msg}\n"
                f"Content:\n{test_content}"
            )
        
        # Verify it contains expected test functions
        assert "def test_" in test_content, "Should contain test functions"
        assert "assert" in test_content, "Should contain assertions"

    @pytest.mark.asyncio
    async def test_fallback_test_contains_proper_escaping(self, testgen_agent):
        """
        Test that fallback tests properly escape f-strings.
        
        Validates that the generated code has properly escaped braces
        for nested f-strings ({{{{}}}} becomes {{}} in output).
        """
        code_files = {
            "syntax_error.py": "invalid python code {"
        }
        
        basic_tests = await testgen_agent._generate_basic_tests(
            code_files, "python", "test-run-456"
        )
        
        test_content = list(basic_tests.values())[0]
        
        # The generated test should have f-strings with single braces
        # after template evaluation (e.g., f"{file_path}")
        assert 'f"' in test_content or "f'" in test_content, (
            "Generated tests should contain f-strings"
        )
        
        # Should NOT have quadruple braces in output
        assert "{{{{" not in test_content, (
            "Output should not contain quadruple braces"
        )

    @pytest.mark.asyncio  
    async def test_fallback_test_structure(self, testgen_agent):
        """
        Test that fallback tests have expected structure.
        """
        code_files = {
            "module.py": "this is not valid python"
        }
        
        basic_tests = await testgen_agent._generate_basic_tests(
            code_files, "python", "test-run-789"
        )
        
        test_content = list(basic_tests.values())[0]
        
        # Verify expected components
        assert "import os" in test_content
        assert "import pytest" in test_content
        assert "@pytest.fixture" in test_content
        assert "@pytest.mark.skip" in test_content
        assert "class Test" in test_content
        assert "def test_" in test_content

    @pytest.mark.asyncio
    async def test_valid_python_file_generates_proper_tests(self, testgen_agent):
        """
        Test that valid Python files generate proper functional tests,
        not fallback structural tests.
        """
        code_files = {
            "calculator.py": """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
"""
        }
        
        basic_tests = await testgen_agent._generate_basic_tests(
            code_files, "python", "test-run-valid"
        )
        
        test_content = list(basic_tests.values())[0]
        
        # Verify it generates functional tests, not structural fallback
        assert "def test_add" in test_content or "test_add" in test_content
        assert "AUTO-GENERATED FALLBACK TESTS" not in test_content
        assert "syntax errors" not in test_content.lower()


class TestProviderInferenceFallback:
    """
    Test that LLM ensemble API properly infers provider when missing.
    
    Note: This test validates that Issue 1 is already fixed in the codebase.
    The provider inference fallback exists at lines 733-738 in llm_client.py.
    """

    def test_provider_inference_utility_exists(self):
        """
        Verify that the provider inference utility exists and works.
        """
        from generator.utils.llm_provider_utils import (
            create_model_config,
            infer_provider_from_model,
        )
        
        # Test provider inference
        assert infer_provider_from_model("gpt-4o") == "openai"
        assert infer_provider_from_model("claude-3-opus") == "claude"
        assert infer_provider_from_model("gemini-pro") == "gemini"
        
        # Test model config creation
        config = create_model_config("gpt-4o")
        assert config["provider"] == "openai"
        assert config["model"] == "gpt-4o"

    def test_create_model_config_with_explicit_provider(self):
        """Test that explicit provider takes precedence."""
        from generator.utils.llm_provider_utils import create_model_config
        
        config = create_model_config("gpt-4o", provider="openai")
        assert config["provider"] == "openai"
        assert config["model"] == "gpt-4o"

    def test_create_model_config_infers_when_missing(self):
        """Test that provider is inferred when not provided."""
        from generator.utils.llm_provider_utils import create_model_config
        
        # Should infer 'openai' from 'gpt-4o' prefix
        config = create_model_config("gpt-4o")
        assert config["provider"] == "openai"
        
        # Should infer 'claude' from 'claude-3' prefix
        config = create_model_config("claude-3-opus")
        assert config["provider"] == "claude"
