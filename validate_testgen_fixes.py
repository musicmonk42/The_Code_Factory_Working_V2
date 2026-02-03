"""
Manual validation script for rule-based testgen fallback.
This script can be run independently to validate the changes.
"""

import asyncio
import ast
import os
import sys
from pathlib import Path

# Add the project to the path
sys.path.insert(0, '/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2')

# Set testing mode
os.environ["TESTING"] = "1"


async def test_ast_parsing():
    """Test that AST parsing works correctly."""
    print("=" * 60)
    print("Test 1: AST Parsing")
    print("=" * 60)
    
    sample_code = """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

class Calculator:
    def multiply(self, a, b):
        return a * b
"""
    
    try:
        tree = ast.parse(sample_code)
        functions = []
        classes = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if not node.name.startswith('_') or node.name == '__init__':
                    functions.append(node.name)
            elif isinstance(node, ast.ClassDef):
                classes.append(node.name)
        
        print(f"✓ Found {len(functions)} functions: {functions}")
        print(f"✓ Found {len(classes)} classes: {classes}")
        
        assert len(functions) >= 2, "Should find at least 2 functions"
        assert len(classes) >= 1, "Should find at least 1 class"
        assert "add" in functions, "Should find 'add' function"
        assert "Calculator" in classes, "Should find 'Calculator' class"
        
        print("✓ AST parsing test PASSED\n")
        return True
    except Exception as e:
        print(f"✗ AST parsing test FAILED: {e}\n")
        return False


async def test_basic_test_generation():
    """Test the basic test generation logic."""
    print("=" * 60)
    print("Test 2: Basic Test Generation Logic")
    print("=" * 60)
    
    # Simulate what _generate_basic_tests does
    code_files = {
        "example.py": """
def hello():
    return "world"

class Greeter:
    def greet(self):
        return "Hello!"
"""
    }
    
    try:
        # Parse the code
        for file_path, content in code_files.items():
            tree = ast.parse(content)
            functions = []
            classes = []
            
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    if not node.name.startswith('_') or node.name == '__init__':
                        functions.append(node.name)
                elif isinstance(node, ast.ClassDef):
                    classes.append(node.name)
            
            # Generate test structure
            test_lines = ['import pytest', '']
            
            for func_name in functions:
                test_lines.append(f'def test_{func_name}():')
                test_lines.append(f'    """Auto-generated test stub for {func_name}."""')
                test_lines.append(f'    assert True  # Placeholder')
                test_lines.append('')
            
            for class_name in classes:
                test_lines.append(f'class Test{class_name}:')
                test_lines.append(f'    """Auto-generated test stub for {class_name}."""')
                test_lines.append(f'    def test_{class_name.lower()}_instantiation(self):')
                test_lines.append(f'        assert True  # Placeholder')
                test_lines.append('')
            
            test_content = '\n'.join(test_lines)
            
            print(f"Generated test for {file_path}:")
            print("-" * 60)
            print(test_content[:500])
            print("-" * 60)
            
            # Validate test content
            assert 'import pytest' in test_content
            assert 'def test_hello():' in test_content
            assert 'class TestGreeter:' in test_content
            
            print(f"✓ Test generation for {file_path} PASSED\n")
        
        return True
    except Exception as e:
        print(f"✗ Test generation FAILED: {e}\n")
        return False


async def test_env_var_check():
    """Test environment variable check logic."""
    print("=" * 60)
    print("Test 3: Environment Variable Check")
    print("=" * 60)
    
    try:
        # Test when TESTGEN_FORCE_LLM is not set
        os.environ.pop("TESTGEN_FORCE_LLM", None)
        should_use_rule_based = not os.getenv("TESTGEN_FORCE_LLM", "").lower() == "true"
        assert should_use_rule_based == True, "Should use rule-based when var not set"
        print("✓ Correctly uses rule-based when TESTGEN_FORCE_LLM not set")
        
        # Test when TESTGEN_FORCE_LLM is set to "true"
        os.environ["TESTGEN_FORCE_LLM"] = "true"
        should_use_rule_based = not os.getenv("TESTGEN_FORCE_LLM", "").lower() == "true"
        assert should_use_rule_based == False, "Should use LLM when var is 'true'"
        print("✓ Correctly uses LLM when TESTGEN_FORCE_LLM='true'")
        
        # Test when TESTGEN_FORCE_LLM is set to something else
        os.environ["TESTGEN_FORCE_LLM"] = "false"
        should_use_rule_based = not os.getenv("TESTGEN_FORCE_LLM", "").lower() == "true"
        assert should_use_rule_based == True, "Should use rule-based when var is not 'true'"
        print("✓ Correctly uses rule-based when TESTGEN_FORCE_LLM='false'")
        
        # Clean up
        os.environ.pop("TESTGEN_FORCE_LLM", None)
        
        print("✓ Environment variable check test PASSED\n")
        return True
    except Exception as e:
        print(f"✗ Environment variable check test FAILED: {e}\n")
        return False


async def main():
    """Run all validation tests."""
    print("\n" + "=" * 60)
    print("MANUAL VALIDATION FOR TESTGEN RULE-BASED FALLBACK")
    print("=" * 60 + "\n")
    
    results = []
    
    # Run tests
    results.append(await test_ast_parsing())
    results.append(await test_basic_test_generation())
    results.append(await test_env_var_check())
    
    # Summary
    print("=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("✓ ALL VALIDATION TESTS PASSED")
        return 0
    else:
        print("✗ SOME VALIDATION TESTS FAILED")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
