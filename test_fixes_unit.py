#!/usr/bin/env python3
"""
Unit tests for the 3 critical pipeline fixes - isolated tests without heavy dependencies.
"""

import sys
import tempfile
from pathlib import Path

def test_dockerfile_fix_logic():
    """Test the Dockerfile fix logic without importing full module."""
    print("=" * 80)
    print("TEST: Dockerfile Fix Logic")
    print("=" * 80)
    
    # Simulate the fix logic
    def fix_dockerfile_syntax(dockerfile_content: str) -> str:
        """Simplified version of the fix."""
        lines = dockerfile_content.split('\n')
        fixed_lines = []
        
        for line in lines:
            stripped = line.strip()
            # Skip shebang lines
            if stripped.startswith('#!'):
                continue
            # Skip empty comments
            if stripped == '#':
                continue
            fixed_lines.append(line)
        
        result = '\n'.join(fixed_lines)
        
        # Ensure starts with FROM
        result_stripped = result.strip()
        if result_stripped and not any(
            result_stripped.upper().startswith(cmd) 
            for cmd in ['FROM', 'ARG']
        ):
            result = 'FROM python:3.11-slim\n\n' + result
        
        return result
    
    # Test 1: Shebang removal
    input1 = """#!/bin/bash
FROM python:3.11
WORKDIR /app"""
    
    output1 = fix_dockerfile_syntax(input1)
    assert "#!/bin/bash" not in output1, "Shebang should be removed"
    assert output1.strip().startswith('FROM'), "Should start with FROM"
    print("✓ Test 1 passed: Shebang removed")
    
    # Test 2: Missing FROM
    input2 = """WORKDIR /app
COPY . ."""
    
    output2 = fix_dockerfile_syntax(input2)
    assert output2.strip().startswith('FROM'), "Should add FROM instruction"
    print("✓ Test 2 passed: FROM instruction added")
    
    # Test 3: ARG before FROM (valid)
    input3 = """ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}"""
    
    output3 = fix_dockerfile_syntax(input3)
    assert output3.strip().startswith('ARG'), "ARG before FROM is valid"
    assert 'FROM' in output3, "Should contain FROM"
    print("✓ Test 3 passed: ARG before FROM preserved")
    
    print("✓ All Dockerfile fix logic tests passed")
    return True


def test_docgen_serialization_logic():
    """Test the docgen serialization logic."""
    print("=" * 80)
    print("TEST: Docgen Serialization Logic")
    print("=" * 80)
    
    import json
    
    # Simulate the serialization logic
    def serialize_docs_output(docs_output):
        """Simplified version of the serialization."""
        content_to_write = ""
        strategy = "unknown"
        
        if isinstance(docs_output, dict):
            if 'content' in docs_output:
                content_to_write = str(docs_output['content'])
                strategy = "dict_content_field"
            elif 'markdown' in docs_output:
                content_to_write = str(docs_output['markdown'])
                strategy = "dict_markdown_field"
            elif 'text' in docs_output:
                content_to_write = str(docs_output['text'])
                strategy = "dict_text_field"
            else:
                # Serialize as JSON
                content_to_write = json.dumps(docs_output, indent=2)
                strategy = "dict_json_serialization"
        else:
            content_to_write = str(docs_output)
            strategy = "direct_string"
        
        return content_to_write, strategy
    
    # Test 1: Dict with content field
    test1 = {"content": "# API Docs\n\nSome content", "metadata": {}}
    result1, strategy1 = serialize_docs_output(test1)
    assert result1 == "# API Docs\n\nSome content", "Should extract content"
    assert strategy1 == "dict_content_field", "Should use content strategy"
    print("✓ Test 1 passed: Dict with content field")
    
    # Test 2: Dict with markdown field
    test2 = {"markdown": "# README\n\nSome markdown", "version": "1.0"}
    result2, strategy2 = serialize_docs_output(test2)
    assert result2 == "# README\n\nSome markdown", "Should extract markdown"
    assert strategy2 == "dict_markdown_field", "Should use markdown strategy"
    print("✓ Test 2 passed: Dict with markdown field")
    
    # Test 3: Dict without special fields (JSON serialization)
    test3 = {"title": "Docs", "body": "Content"}
    result3, strategy3 = serialize_docs_output(test3)
    parsed = json.loads(result3)
    assert parsed == test3, "Should serialize as JSON"
    assert strategy3 == "dict_json_serialization", "Should use JSON strategy"
    print("✓ Test 3 passed: Dict JSON serialization")
    
    # Test 4: Direct string
    test4 = "# Direct Content"
    result4, strategy4 = serialize_docs_output(test4)
    assert result4 == "# Direct Content", "Should return string as-is"
    assert strategy4 == "direct_string", "Should use string strategy"
    print("✓ Test 4 passed: Direct string")
    
    print("✓ All docgen serialization logic tests passed")
    return True


def test_testgen_fallback_logic():
    """Test the testgen fallback logic."""
    print("=" * 80)
    print("TEST: Testgen Fallback Logic")
    print("=" * 80)
    
    # Simulate fallback test generation
    def generate_fallback_test(file_path: str, error_line: int, error_msg: str):
        """Simplified version of fallback generation."""
        file_stem = (
            file_path.replace('.py', '')
            .replace('/', '_')
            .replace('-', '_')
            .replace('.', '_')
            .strip('_')
        )
        
        if not file_stem or not file_stem[0].isalpha():
            file_stem = f"file_{file_stem}"
        
        test_content = f'''"""
Structural test suite for {file_path}
Syntax Error at line {error_line}: {error_msg}
"""
import pytest

def test_{file_stem}_exists():
    """Test file exists."""
    import os
    assert os.path.exists("{file_path}")

@pytest.mark.skip(reason="Source file has syntax errors")
def test_{file_stem}_import():
    """Test import (skipped)."""
    pass
'''
        return test_content, file_stem
    
    # Test 1: Normal file path
    test1_path = "src/utils.py"
    content1, stem1 = generate_fallback_test(test1_path, 10, "invalid syntax")
    assert "test_src_utils_exists" in content1, "Should generate test function"
    assert "@pytest.mark.skip" in content1, "Should skip import test"
    assert stem1 == "src_utils", "Should generate valid identifier"
    print("✓ Test 1 passed: Normal file path")
    
    # Test 2: File with special characters
    test2_path = "my-module.helper.py"
    content2, stem2 = generate_fallback_test(test2_path, 5, "unexpected EOF")
    assert "test_my_module_helper_exists" in content2, "Should handle special chars"
    assert stem2 == "my_module_helper", "Should clean identifier"
    print("✓ Test 2 passed: File with special chars")
    
    # Test 3: Root level file
    test3_path = "app.py"
    content3, stem3 = generate_fallback_test(test3_path, 1, "missing parenthesis")
    assert "test_app_exists" in content3, "Should handle simple name"
    assert stem3 == "app", "Should use simple stem"
    print("✓ Test 3 passed: Root level file")
    
    print("✓ All testgen fallback logic tests passed")
    return True


def main():
    """Run all unit tests."""
    print("=" * 80)
    print("UNIT TESTS FOR CRITICAL PIPELINE FIXES")
    print("Testing core logic without heavy dependencies")
    print("=" * 80)
    print()
    
    results = []
    
    try:
        results.append(("Dockerfile Fix Logic", test_dockerfile_fix_logic()))
    except Exception as e:
        print(f"✗ Dockerfile fix test failed: {e}")
        results.append(("Dockerfile Fix Logic", False))
    
    print()
    
    try:
        results.append(("Docgen Serialization Logic", test_docgen_serialization_logic()))
    except Exception as e:
        print(f"✗ Docgen serialization test failed: {e}")
        results.append(("Docgen Serialization Logic", False))
    
    print()
    
    try:
        results.append(("Testgen Fallback Logic", test_testgen_fallback_logic()))
    except Exception as e:
        print(f"✗ Testgen fallback test failed: {e}")
        results.append(("Testgen Fallback Logic", False))
    
    print()
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    failed = len(results) - passed
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print("=" * 80)
    print(f"Total: {len(results)} tests, {passed} passed, {failed} failed")
    print("=" * 80)
    
    if failed > 0:
        sys.exit(1)
    else:
        print("\n✓ All unit tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
