#!/usr/bin/env python3
"""
Validation test for pytest collection fix.

This test verifies that the pytest collection failure has been fixed by:
1. Ensuring PLUGIN_REGISTRY is never None
2. Ensuring @plugin decorator works during collection
3. Ensuring the original error (AttributeError) does not occur
"""

import os
import sys
import subprocess


def test_plugin_registry_import_with_collection_env():
    """Test that PLUGIN_REGISTRY can be imported during pytest collection"""
    # Set the environment variable
    env = os.environ.copy()
    env['PYTEST_COLLECTING'] = '1'
    env['SKIP_AUDIT_INIT'] = '1'
    
    # Try to import and check PLUGIN_REGISTRY
    code = """
import sys
sys.path.insert(0, '.')
from omnicore_engine.plugin_registry import PLUGIN_REGISTRY
assert PLUGIN_REGISTRY is not None, "PLUGIN_REGISTRY should not be None"
assert hasattr(PLUGIN_REGISTRY, 'performance_tracker'), "Should have performance_tracker attribute"
print("✅ PLUGIN_REGISTRY imported successfully during collection mode")
"""
    
    # Get the project root directory (where this test file is located)
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True
    )
    
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise AssertionError(f"Import failed with exit code {result.returncode}")


def test_plugin_decorator_with_collection_env():
    """Test that @plugin decorator works during pytest collection"""
    env = os.environ.copy()
    env['PYTEST_COLLECTING'] = '1'
    env['SKIP_AUDIT_INIT'] = '1'
    
    code = """
import sys
sys.path.insert(0, '.')
from omnicore_engine.plugin_registry import plugin, PlugInKind

# This should not raise AttributeError
@plugin(
    kind=PlugInKind.FIX,
    name='test_plugin',
    version='1.0.0',
    description='Test plugin'
)
def test_func():
    return 'test'

print("✅ @plugin decorator works during collection mode")
"""
    
    # Get the project root directory (where this test file is located)
    project_root = os.path.dirname(os.path.abspath(__file__))
    
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True
    )
    
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise AssertionError(f"Plugin decorator failed with exit code {result.returncode}")


def test_clarifier_prompt_module_can_be_parsed():
    """Test that the clarifier_prompt module can at least be parsed (not necessarily imported due to deps)"""
    env = os.environ.copy()
    env['PYTEST_COLLECTING'] = '1'
    env['SKIP_AUDIT_INIT'] = '1'
    
    # Get the project root directory (where this test file is located)
    project_root = os.path.dirname(os.path.abspath(__file__))
    clarifier_file = os.path.join('generator', 'clarifier', 'clarifier_prompt.py')
    
    # Just check that the @plugin decorator at line 371 doesn't cause an immediate error
    code = f"""
import sys
import ast
import os
sys.path.insert(0, '.')

# Parse the file to ensure the @plugin decorator syntax is valid
file_path = os.path.join('{clarifier_file}')
with open(file_path, 'r') as f:
    content = f.read()
    try:
        ast.parse(content)
        print("✅ clarifier_prompt.py parsed successfully")
    except SyntaxError as e:
        print(f"❌ Syntax error in clarifier_prompt.py: {{e}}")
        sys.exit(1)
"""
    
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True
    )
    
    print(result.stdout)
    if result.returncode != 0:
        print("STDERR:", result.stderr)
        raise AssertionError(f"Parsing failed with exit code {result.returncode}")


if __name__ == '__main__':
    print("=" * 80)
    print("Validation Tests for Pytest Collection Fix")
    print("=" * 80)
    
    try:
        print("\n1. Testing PLUGIN_REGISTRY import during collection...")
        test_plugin_registry_import_with_collection_env()
        
        print("\n2. Testing @plugin decorator during collection...")
        test_plugin_decorator_with_collection_env()
        
        print("\n3. Testing clarifier_prompt.py can be parsed...")
        test_clarifier_prompt_module_can_be_parsed()
        
        print("\n" + "=" * 80)
        print("✅ All validation tests passed!")
        print("=" * 80)
        
    except AssertionError as e:
        print("\n" + "=" * 80)
        print(f"❌ Validation test failed: {e}")
        print("=" * 80)
        sys.exit(1)
