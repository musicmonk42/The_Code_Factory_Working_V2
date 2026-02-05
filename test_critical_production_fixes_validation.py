"""
Test suite for critical production fixes.

This test suite validates the fixes for the 5 critical production issues:
1. Dockerfile generation - Invalid syntax at line 1
2. Empty code block generation
3. Config import failure
4. LLM fallback returns invalid content
5. Syntax validation for non-code files
"""

import json
import pytest

# Issue 2 & 5: File extension detection and validation skipping
from generator.agents.codegen_agent.codegen_response_handler import (
    _infer_language_from_filename,
    _should_skip_syntax_validation,
    _validate_syntax,
    parse_llm_response,
)

# Issue 4: LLM fallback content
from generator.clarifier.clarifier_llm import GrokLLM, UnifiedLLMProvider, FallbackConfig


class TestFileExtensionDetection:
    """Test suite for Issue 2 & 5: File extension detection for proper validation."""
    
    def test_infer_language_from_python_file(self):
        """Python files should be detected correctly."""
        assert _infer_language_from_filename("main.py") == "python"
        assert _infer_language_from_filename("script.pyw") == "python"
        assert _infer_language_from_filename("types.pyi") == "python"
    
    def test_infer_language_from_javascript_file(self):
        """JavaScript files should be detected correctly."""
        assert _infer_language_from_filename("app.js") == "javascript"
        assert _infer_language_from_filename("module.mjs") == "javascript"
        assert _infer_language_from_filename("config.cjs") == "javascript"
        assert _infer_language_from_filename("component.jsx") == "javascript"
    
    def test_infer_language_from_typescript_file(self):
        """TypeScript files should be detected correctly."""
        assert _infer_language_from_filename("app.ts") == "typescript"
        assert _infer_language_from_filename("component.tsx") == "typescript"
    
    def test_infer_language_from_documentation_file(self):
        """Documentation files should be detected as non-code."""
        assert _infer_language_from_filename("README.md") == "markdown"
        assert _infer_language_from_filename("notes.txt") == "text"
        assert _infer_language_from_filename("doc.rst") == "restructuredtext"
    
    def test_infer_language_from_config_file(self):
        """Configuration files should be detected as non-code."""
        assert _infer_language_from_filename("config.json") == "json"
        assert _infer_language_from_filename("settings.yaml") == "yaml"
        assert _infer_language_from_filename("config.yml") == "yaml"
        assert _infer_language_from_filename("pyproject.toml") == "toml"
    
    def test_should_skip_validation_for_documentation(self):
        """Documentation files should skip syntax validation."""
        assert _should_skip_syntax_validation("README.md") is True
        assert _should_skip_syntax_validation("CHANGELOG.md") is True
        assert _should_skip_syntax_validation("notes.txt") is True
        assert _should_skip_syntax_validation("docs.rst") is True
    
    def test_should_skip_validation_for_config_files(self):
        """Configuration files should skip syntax validation."""
        assert _should_skip_syntax_validation("package.json") is True
        assert _should_skip_syntax_validation("config.yaml") is True
        assert _should_skip_syntax_validation("settings.toml") is True
        assert _should_skip_syntax_validation("index.html") is True
    
    def test_should_not_skip_validation_for_code_files(self):
        """Code files should not skip syntax validation."""
        assert _should_skip_syntax_validation("main.py") is False
        assert _should_skip_syntax_validation("app.js") is False
        assert _should_skip_syntax_validation("Main.java") is False
        assert _should_skip_syntax_validation("main.go") is False


class TestSyntaxValidationWithFileDetection:
    """Test that syntax validation uses file extension detection."""
    
    def test_readme_skips_python_validation(self):
        """README.md should not be validated as Python code."""
        # This was the original issue - README.md was being validated as Python
        valid, msg = _validate_syntax(
            "# My Project\n\nThis is a readme.",
            lang="python",  # Even with python lang, should skip based on filename
            filename="README.md"
        )
        assert valid is True
        assert "Skipped validation" in msg or "markdown" in msg.lower()
    
    def test_config_yaml_skips_validation(self):
        """YAML files should not be validated as code."""
        valid, msg = _validate_syntax(
            "key: value\nlist:\n  - item1",
            lang="python",
            filename="config.yaml"
        )
        assert valid is True
        assert "Skipped validation" in msg or "yaml" in msg.lower()
    
    def test_python_file_validates_correctly(self):
        """Python files should still be validated."""
        # Valid Python
        valid, msg = _validate_syntax(
            "def hello():\n    print('world')",
            lang="python",
            filename="main.py"
        )
        assert valid is True
        
        # Invalid Python
        valid, msg = _validate_syntax(
            "def hello(\n    print('world')",
            lang="python",
            filename="main.py"
        )
        assert valid is False
    
    def test_empty_code_block_detected(self):
        """Empty code blocks should be properly detected and reported."""
        valid, msg = _validate_syntax("", lang="python", filename="main.py")
        assert valid is False
        assert "Empty code block" in msg


class TestMultiFileResponseWithMixedTypes:
    """Test that multi-file responses handle different file types correctly."""
    
    def test_multi_file_with_readme_and_code(self, monkeypatch):
        """Multi-file response with both code and documentation should work."""
        # Mock _scan_for_secrets to avoid dependency issues
        def mock_scan(*args, **kwargs):
            return []
        monkeypatch.setattr(
            "generator.agents.codegen_agent.codegen_response_handler._scan_for_secrets",
            mock_scan
        )
        
        response = json.dumps({
            "files": {
                "main.py": "def hello():\n    print('world')",
                "README.md": "# My Project\n\nThis project does things.",
                "config.yaml": "port: 8000\nhost: localhost"
            }
        })
        
        result = parse_llm_response(response, lang="python")
        
        # All files should be present (no validation errors for non-code files)
        assert "main.py" in result
        assert "README.md" in result
        assert "config.yaml" in result
        assert "error.txt" not in result  # No errors should be generated


class TestLLMFallbackCodeGeneration:
    """Test suite for Issue 4: LLM fallback returns appropriate content."""
    
    def test_grok_fallback_detects_code_generation(self):
        """GrokLLM fallback should detect code generation requests."""
        llm = GrokLLM(api_key="")  # Empty API key triggers fallback
        
        # Test with code generation prompt
        code_prompt = "Generate a Python function to calculate fibonacci numbers"
        response = llm._generate_fallback_response(code_prompt)
        
        # Should return valid JSON with files
        data = json.loads(response)
        assert "files" in data
        assert isinstance(data["files"], dict)
        assert len(data["files"]) > 0
        
        # Should contain placeholder code
        assert "main.py" in data["files"]
        assert "def main()" in data["files"]["main.py"]
    
    def test_grok_fallback_clarification_request(self):
        """GrokLLM fallback should handle clarification requests."""
        llm = GrokLLM(api_key="")
        
        clarification_prompt = "What are the requirements for this unclear feature?"
        response = llm._generate_fallback_response(clarification_prompt)
        
        data = json.loads(response)
        assert "clarifications" in data
        assert isinstance(data["clarifications"], list)
        assert len(data["clarifications"]) > 0
    
    def test_unified_fallback_detects_code_generation(self):
        """UnifiedLLMProvider fallback should detect code generation requests."""
        llm = UnifiedLLMProvider(provider="test", model="test")
        
        code_prompt = "Write a JavaScript function to sort an array"
        response = llm._generate_fallback_response(code_prompt)
        
        data = json.loads(response)
        assert "files" in data
        assert isinstance(data["files"], dict)
        assert len(data["files"]) > 0
    
    def test_fallback_code_has_valid_structure(self):
        """Fallback code should be valid Python that parses without errors."""
        llm = GrokLLM(api_key="")
        
        code_prompt = "create a python script"
        response = llm._generate_fallback_response(code_prompt)
        
        data = json.loads(response)
        main_code = data["files"]["main.py"]
        
        # Should be valid Python (compile should not raise)
        compile(main_code, "main.py", "exec")
        
        # Should have main function with colon
        assert "def main():" in main_code
    
    def test_fallback_generic_response(self):
        """Generic prompts should get generic guidance."""
        llm = GrokLLM(api_key="")
        
        generic_prompt = "Tell me about software architecture"
        response = llm._generate_fallback_response(generic_prompt)
        
        # Should return string guidance (not JSON)
        assert isinstance(response, str)
        assert "clarify" in response.lower() or "functionality" in response.lower()


class TestDockerfileGeneration:
    """Test suite for Issue 1: Dockerfile generation validation."""
    
    def test_dockerfile_does_not_start_with_shebang(self):
        """Dockerfiles should not contain shebang lines."""
        from generator.agents.deploy_agent.plugins.docker import DockerPlugin
        
        plugin = DockerPlugin()
        
        # Test with a Dockerfile that might have issues
        test_dockerfile = "#!/bin/bash\nFROM python:3.11-slim\nRUN echo hello"
        
        # The _fix_dockerfile_syntax should remove shebang
        fixed = plugin._fix_dockerfile_syntax(test_dockerfile)
        
        # Should not start with shebang
        assert not fixed.strip().startswith("#!")
        
        # Should start with FROM or comment
        first_line = fixed.strip().split('\n')[0]
        assert first_line.startswith("FROM") or first_line.startswith("#")
    
    def test_dockerfile_starts_with_from(self):
        """Generated Dockerfiles should start with FROM instruction."""
        from generator.agents.deploy_agent.plugins.docker import DockerPlugin
        
        plugin = DockerPlugin()
        dockerfile = plugin._generate_dockerfile("python", "", [], {})
        
        # Should not be empty
        assert dockerfile.strip()
        
        # First non-comment line should be FROM
        for line in dockerfile.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                assert stripped.startswith('FROM')
                break


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
