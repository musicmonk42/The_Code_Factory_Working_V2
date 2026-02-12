# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Tests for pipeline fixes:
1. Gemini fallback model remapping
2. TypeScript support and language detection
3. Deploy agent validation logic
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
import tempfile
import shutil

from server.services.omnicore_service import (
    _detect_project_language,
    _is_test_file,
    LANGUAGE_FILE_EXTENSIONS,
    TEST_FILE_PATTERNS,
)


class TestLanguageDetection:
    """Test language detection and file pattern matching."""
    
    def test_detect_python_project(self):
        """Test detection of Python projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            # Create Python files
            (test_path / "main.py").write_text("print('hello')")
            (test_path / "utils.py").write_text("def util(): pass")
            (test_path / "test_main.py").write_text("def test(): pass")
            
            detected = _detect_project_language(test_path)
            assert detected == "python"
    
    def test_detect_typescript_project(self):
        """Test detection of TypeScript projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            # Create TypeScript files
            (test_path / "index.ts").write_text("console.log('hello');")
            (test_path / "utils.ts").write_text("export function util() {}")
            (test_path / "component.tsx").write_text("export const App = () => {}")
            
            detected = _detect_project_language(test_path)
            assert detected == "typescript"
    
    def test_detect_javascript_project(self):
        """Test detection of JavaScript projects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            # Create JavaScript files
            (test_path / "index.js").write_text("console.log('hello');")
            (test_path / "utils.js").write_text("export function util() {}")
            
            detected = _detect_project_language(test_path)
            assert detected == "javascript"
    
    def test_detect_mixed_project_prefers_dominant(self):
        """Test that mixed projects detect the dominant language."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            # More TypeScript than JavaScript
            (test_path / "index.ts").write_text("")
            (test_path / "utils.ts").write_text("")
            (test_path / "config.ts").write_text("")
            (test_path / "helper.js").write_text("")
            
            detected = _detect_project_language(test_path)
            assert detected == "typescript"
    
    def test_detect_defaults_to_python_empty(self):
        """Test that empty directories default to Python."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_path = Path(tmpdir)
            detected = _detect_project_language(test_path)
            assert detected == "python"
    
    def test_detect_nonexistent_path(self):
        """Test that nonexistent paths default to Python."""
        test_path = Path("/nonexistent/path")
        detected = _detect_project_language(test_path)
        assert detected == "python"


class TestFilePatternMatching:
    """Test test file pattern matching for different languages."""
    
    def test_python_test_files(self):
        """Test Python test file detection."""
        assert _is_test_file(Path("test_main.py"), "python") == True
        assert _is_test_file(Path("main_test.py"), "python") == True
        assert _is_test_file(Path("main.py"), "python") == False
        assert _is_test_file(Path("utils.py"), "python") == False
    
    def test_typescript_test_files(self):
        """Test TypeScript test file detection."""
        assert _is_test_file(Path("main.test.ts"), "typescript") == True
        assert _is_test_file(Path("main.spec.ts"), "typescript") == True
        assert _is_test_file(Path("component.test.tsx"), "typescript") == True
        assert _is_test_file(Path("component.spec.tsx"), "typescript") == True
        assert _is_test_file(Path("main.ts"), "typescript") == False
        assert _is_test_file(Path("component.tsx"), "typescript") == False
    
    def test_javascript_test_files(self):
        """Test JavaScript test file detection."""
        assert _is_test_file(Path("main.test.js"), "javascript") == True
        assert _is_test_file(Path("main.spec.js"), "javascript") == True
        assert _is_test_file(Path("main.js"), "javascript") == False
    
    def test_java_test_files(self):
        """Test Java test file detection."""
        assert _is_test_file(Path("MainTest.java"), "java") == True
        assert _is_test_file(Path("MainTests.java"), "java") == True
        assert _is_test_file(Path("Main.java"), "java") == False
    
    def test_go_test_files(self):
        """Test Go test file detection."""
        assert _is_test_file(Path("main_test.go"), "go") == True
        assert _is_test_file(Path("main.go"), "go") == False
    
    def test_unknown_language_fallback(self):
        """Test fallback behavior for unknown languages."""
        # Should fall back to checking for 'test' in name
        assert _is_test_file(Path("test_something.xyz"), "unknown") == True
        assert _is_test_file(Path("something_test.xyz"), "unknown") == True
        assert _is_test_file(Path("something.xyz"), "unknown") == False


class TestLanguageFileExtensions:
    """Test language file extension mappings."""
    
    def test_all_languages_have_extensions(self):
        """Test that all defined languages have file extensions."""
        expected_languages = ["python", "typescript", "javascript", "java", "go", "rust"]
        for lang in expected_languages:
            assert lang in LANGUAGE_FILE_EXTENSIONS
            assert len(LANGUAGE_FILE_EXTENSIONS[lang]) > 0
    
    def test_all_languages_have_test_patterns(self):
        """Test that all defined languages have test patterns."""
        expected_languages = ["python", "typescript", "javascript", "java", "go", "rust"]
        for lang in expected_languages:
            assert lang in TEST_FILE_PATTERNS
            assert callable(TEST_FILE_PATTERNS[lang])


# Model remapping tests would go in generator/tests/test_runner_llm_client.py
# but we'll add a basic test here to verify the constants are defined

class TestModelRemappingConstants:
    """Test that model remapping constants are properly defined."""
    
    def test_provider_default_models_exists(self):
        """Test that _PROVIDER_DEFAULT_MODELS is defined."""
        from generator.runner.llm_client import _PROVIDER_DEFAULT_MODELS
        assert _PROVIDER_DEFAULT_MODELS is not None
        assert isinstance(_PROVIDER_DEFAULT_MODELS, dict)
    
    def test_provider_default_models_has_all_providers(self):
        """Test that all providers have default models."""
        from generator.runner.llm_client import _PROVIDER_DEFAULT_MODELS
        expected_providers = ["openai", "gemini", "local", "grok", "claude"]
        for provider in expected_providers:
            assert provider in _PROVIDER_DEFAULT_MODELS
            assert isinstance(_PROVIDER_DEFAULT_MODELS[provider], str)
            assert len(_PROVIDER_DEFAULT_MODELS[provider]) > 0


class TestLLMClientModelRemapping:
    """Test LLM client model remapping functionality."""
    
    def test_detect_model_provider_openai(self):
        """Test detection of OpenAI models."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        assert client._detect_model_provider("gpt-4o") == "openai"
        assert client._detect_model_provider("gpt-3.5-turbo") == "openai"
        assert client._detect_model_provider("gpt-4") == "openai"
    
    def test_detect_model_provider_gemini(self):
        """Test detection of Gemini models."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        assert client._detect_model_provider("gemini-pro") == "gemini"
        assert client._detect_model_provider("gemini-1.5-pro") == "gemini"
    
    def test_detect_model_provider_claude(self):
        """Test detection of Claude models."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        assert client._detect_model_provider("claude-3-sonnet-20240229") == "claude"
        assert client._detect_model_provider("claude-2") == "claude"
    
    def test_detect_model_provider_grok(self):
        """Test detection of Grok models."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        assert client._detect_model_provider("grok-beta") == "grok"
        assert client._detect_model_provider("grok-1") == "grok"
    
    def test_detect_model_provider_local(self):
        """Test detection of local models."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        assert client._detect_model_provider("codellama") == "local"
        assert client._detect_model_provider("llama-2") == "local"
        assert client._detect_model_provider("mistral") == "local"
    
    def test_remap_model_for_provider_no_change_needed(self):
        """Test that models are not remapped when already correct."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        # OpenAI model to OpenAI provider - no change
        assert client._remap_model_for_provider("gpt-4o", "openai") == "gpt-4o"
        # Gemini model to Gemini provider - no change
        assert client._remap_model_for_provider("gemini-pro", "gemini") == "gemini-pro"
    
    def test_remap_model_for_provider_remapping(self):
        """Test that models are remapped when provider changes."""
        from generator.runner.llm_client import LLMClient
        from runner.runner_config import RunnerConfig
        
        config = RunnerConfig()
        client = LLMClient(config)
        
        # OpenAI model to Gemini provider - should remap
        remapped = client._remap_model_for_provider("gpt-4o", "gemini")
        assert remapped == "gemini-pro"
        
        # Gemini model to OpenAI provider - should remap
        remapped = client._remap_model_for_provider("gemini-pro", "openai")
        assert remapped == "gpt-4o"
        
        # Claude model to Grok provider - should remap
        remapped = client._remap_model_for_provider("claude-3-sonnet", "grok")
        assert remapped == "grok-beta"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
