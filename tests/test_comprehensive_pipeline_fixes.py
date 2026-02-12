# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test comprehensive pipeline fixes for TypeScript/JavaScript support.

This test suite validates the 6 critical fixes:
1. Critique plugin lang= vs language= argument mismatch
2. Test generation TypeScript support
3. README validation language-awareness
4. Spec fidelity validation for TypeScript/JavaScript
5. Deploy mermaid/markdown stripping (verification)
6. TypeScript critique plugin registration
"""

import sys
import re
from pathlib import Path

# Add generator to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "generator"))
sys.path.insert(0, str(Path(__file__).parent.parent / "self_fixing_engineer"))


def test_typescript_in_test_generation_languages():
    """Test that TypeScript is in SUPPORTED_LANGUAGES for test generation."""
    # Read the file directly to avoid import issues
    gen_plugins_path = Path(__file__).parent.parent / "self_fixing_engineer" / "test_generation" / "gen_plugins.py"
    content = gen_plugins_path.read_text()
    
    # Check for TypeScript in SUPPORTED_LANGUAGES
    assert 'typescript' in content, "TypeScript should be mentioned in gen_plugins.py"
    # More flexible check - just look for typescript in a frozenset definition
    assert re.search(r'SUPPORTED_LANGUAGES\s*=\s*frozenset\([^)]*"typescript"[^)]*\)', content), \
        "TypeScript should be in SUPPORTED_LANGUAGES frozenset"
    
    # Check for TypeScript in SUPPORTED_FRAMEWORKS
    assert re.search(r'"typescript":\s*frozenset\([^)]*"jest"[^)]*\)', content), \
        "TypeScript should have jest framework"
    
    print("✓ test_typescript_in_test_generation_languages passed")


def test_typescript_test_generator_registered():
    """Test that TypeScript test generator is registered."""
    gen_plugins_path = Path(__file__).parent.parent / "self_fixing_engineer" / "test_generation" / "gen_plugins.py"
    content = gen_plugins_path.read_text()
    
    # Check for TypeScriptTestGenerator class
    assert "class TypeScriptTestGenerator(JavaScriptTestGenerator):" in content, \
        "TypeScriptTestGenerator class should exist"
    
    # Check for registration
    assert 'LANGUAGE_GENERATORS.register("typescript", TypeScriptTestGenerator())' in content, \
        "TypeScript generator should be registered"
    
    print("✓ test_typescript_test_generator_registered passed")


def test_typescript_in_testgen_prompt_languages():
    """Test that TypeScript is in testgen_prompt SUPPORTED_LANGUAGES."""
    testgen_prompt_path = Path(__file__).parent.parent / "generator" / "agents" / "testgen_agent" / "testgen_prompt.py"
    content = testgen_prompt_path.read_text()
    
    # Check for TypeScript in SUPPORTED_LANGUAGES
    assert '"typescript"' in content, "TypeScript should be mentioned in testgen_prompt.py"
    # Look for the SUPPORTED_LANGUAGES line containing typescript
    assert re.search(r'SUPPORTED_LANGUAGES\s*=\s*\[[^\]]*"typescript"[^\]]*\]', content), \
        "TypeScript should be in SUPPORTED_LANGUAGES list"
    
    print("✓ test_typescript_in_testgen_prompt_languages passed")


def test_typescript_critique_plugin_registered():
    """Test that TypeScript critique plugin is registered."""
    critique_agent_path = Path(__file__).parent.parent / "generator" / "agents" / "critique_agent" / "critique_agent.py"
    content = critique_agent_path.read_text()
    
    # Check for TypeScriptCritiquePlugin class
    assert "class TypeScriptCritiquePlugin(JavaScriptCritiquePlugin):" in content, \
        "TypeScriptCritiquePlugin class should exist"
    
    # Check for registration
    assert 'register_plugin("typescript", TypeScriptCritiquePlugin)' in content, \
        "TypeScript plugin should be registered"
    
    print("✓ test_typescript_critique_plugin_registered passed")


def test_critique_plugin_uses_language_not_lang():
    """Test that JS and Go critique plugins use 'language=' not 'lang='."""
    critique_agent_path = Path(__file__).parent.parent / "generator" / "agents" / "critique_agent" / "critique_agent.py"
    content = critique_agent_path.read_text()
    
    # Check that lang= is NOT used (should be language=)
    js_section = re.search(r'class JavaScriptCritiquePlugin.*?(?=class|\Z)', content, re.DOTALL)
    if js_section:
        assert 'language="javascript"' in js_section.group(0), "JS plugin should use language='javascript'"
        assert 'lang="javascript"' not in js_section.group(0), "JS plugin should NOT use lang='javascript'"
    
    go_section = re.search(r'class GoCritiquePlugin.*?(?=class|\Z)', content, re.DOTALL)
    if go_section:
        assert 'language="go"' in go_section.group(0), "Go plugin should use language='go'"
        assert 'lang="go"' not in go_section.group(0), "Go plugin should NOT use lang='go'"
    
    print("✓ test_critique_plugin_uses_language_not_lang passed")


def test_typescript_in_linter_config():
    """Test that TypeScript is in LINTER_CONFIG."""
    critique_linter_path = Path(__file__).parent.parent / "generator" / "agents" / "critique_agent" / "critique_linter.py"
    content = critique_linter_path.read_text()
    
    # Check for typescript in LINTER_CONFIG
    assert '"typescript":' in content, "TypeScript should be in LINTER_CONFIG"
    
    print("✓ test_typescript_in_linter_config passed")


def test_readme_validation_has_language_parameter():
    """Test that validate_readme_completeness has language parameter."""
    provenance_path = Path(__file__).parent.parent / "generator" / "main" / "provenance.py"
    content = provenance_path.read_text()
    
    # Check for language parameter in function signature
    assert 'def validate_readme_completeness(readme_content: str, language: str = "python")' in content, \
        "validate_readme_completeness should have language parameter"
    
    # Check for language-specific command logic
    assert 'if language.lower() in ("typescript", "javascript"):' in content, \
        "Should have TypeScript/JavaScript specific logic"
    
    print("✓ test_readme_validation_has_language_parameter passed")


def test_extract_endpoints_supports_typescript():
    """Test that extract_endpoints_from_code has filename parameter and TS/JS patterns."""
    provenance_path = Path(__file__).parent.parent / "generator" / "main" / "provenance.py"
    content = provenance_path.read_text()
    
    # Check for filename parameter
    assert 'def extract_endpoints_from_code(code_content: str, filename: str = "")' in content, \
        "extract_endpoints_from_code should have filename parameter"
    
    # Check for TypeScript/JavaScript patterns
    assert 'ts_js_patterns' in content, "Should have ts_js_patterns variable"
    assert 'Express' in content and 'NestJS' in content, "Should mention Express and NestJS"
    
    print("✓ test_extract_endpoints_supports_typescript passed")


def test_validate_spec_fidelity_processes_ts_js_files():
    """Test that validate_spec_fidelity processes .ts and .js files."""
    provenance_path = Path(__file__).parent.parent / "generator" / "main" / "provenance.py"
    content = provenance_path.read_text()
    
    # Check for .ts and .js in file filter
    assert "endswith(('.py', '.ts', '.js'" in content or "endswith(('.py', '.ts', '.js'" in content, \
        "Should check for .ts and .js file extensions"
    
    print("✓ test_validate_spec_fidelity_processes_ts_js_files passed")


def test_mermaid_rejection_in_deploy_response_handler():
    """Test that deploy response handler rejects mermaid diagrams."""
    deploy_handler_path = Path(__file__).parent.parent / "generator" / "agents" / "deploy_agent" / "deploy_response_handler.py"
    content = deploy_handler_path.read_text()
    
    # Check for mermaid rejection
    assert "```mermaid" in content and "raise ValueError" in content, \
        "Should reject mermaid diagrams with ValueError"
    
    # Check for DOTALL flag in regex
    assert "re.DOTALL" in content, "Should use re.DOTALL flag for multiline matching"
    
    print("✓ test_mermaid_rejection_in_deploy_response_handler passed")


if __name__ == "__main__":
    # Run tests manually
    test_typescript_in_test_generation_languages()
    test_typescript_test_generator_registered()
    test_typescript_in_testgen_prompt_languages()
    test_typescript_critique_plugin_registered()
    test_critique_plugin_uses_language_not_lang()
    test_typescript_in_linter_config()
    test_readme_validation_has_language_parameter()
    test_extract_endpoints_supports_typescript()
    test_validate_spec_fidelity_processes_ts_js_files()
    test_mermaid_rejection_in_deploy_response_handler()
    print("\n✅ All tests passed!")

