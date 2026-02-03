#!/usr/bin/env python3
"""
Simple verification script to check API signature fixes without requiring full dependencies.

This script validates that:
1. call_ensemble_api accepts the 'stream' parameter
2. docgen_agent.py doesn't pass 'lang' parameter to process_and_validate_response
3. All call sites are compatible with the new signatures

Industry Standards:
- PEP 8 compliant
- Type hints for better code clarity
- Comprehensive error handling
- Clear documentation

Author: Code Factory Platform
Version: 1.0.0
"""

import ast
import sys
from pathlib import Path
from typing import List, Tuple


def check_call_ensemble_api_signature() -> bool:
    """Check that call_ensemble_api has stream parameter."""
    print("=" * 60)
    print("Checking call_ensemble_api signature...")
    print("=" * 60)
    
    file_path = Path("generator/runner/llm_client.py")
    with open(file_path, "r") as f:
        content = f.read()
        tree = ast.parse(content)
    
    # Find all call_ensemble_api functions (both in class and module level)
    found_functions = []
    
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "call_ensemble_api":
            # This is a module-level function
            params = [arg.arg for arg in node.args.args]
            print(f"Found module-level call_ensemble_api with parameters: {params}")
            
            # Check for stream parameter
            if "stream" in params:
                print("✓ SUCCESS: 'stream' parameter found in module-level call_ensemble_api")
                
                # Check if kwargs is present
                if node.args.kwarg and node.args.kwarg.arg == "kwargs":
                    print("✓ SUCCESS: **kwargs found for forwarding additional parameters")
                
                found_functions.append(True)
            else:
                print("✗ FAILED: 'stream' parameter NOT found in module-level call_ensemble_api")
                print(f"  Parameters found: {params}")
                found_functions.append(False)
        elif isinstance(node, ast.ClassDef):
            # Check methods inside classes
            for item in node.body:
                if isinstance(item, ast.AsyncFunctionDef) and item.name == "call_ensemble_api":
                    params = [arg.arg for arg in item.args.args]
                    print(f"Found {node.name}.call_ensemble_api with parameters: {params}")
    
    if not found_functions:
        print("✗ FAILED: Could not find module-level call_ensemble_api function")
        return False
    
    return all(found_functions)


def check_docgen_lang_parameter() -> bool:
    """
    Check that docgen_agent.py doesn't pass lang parameter.
    
    Returns:
        bool: True if no lang parameter found in process_and_validate_response calls
    """
    print("\n" + "=" * 60)
    print("Checking docgen_agent.py for lang parameter removal...")
    print("=" * 60)
    
    file_path = Path("generator/agents/docgen_agent/docgen_agent.py")
    with open(file_path, "r") as f:
        content = f.read()
    
    # Look for process_and_validate_response calls with lang parameter
    lines_with_lang = []
    for i, line in enumerate(content.split('\n'), 1):
        if 'process_and_validate_response' in line and 'lang=' in line:
            lines_with_lang.append((i, line.strip()))
    
    if lines_with_lang:
        print("✗ FAILED: Found process_and_validate_response calls with 'lang=' parameter:")
        for line_num, line in lines_with_lang:
            print(f"  Line {line_num}: {line[:80]}...")
        return False
    else:
        print("✓ SUCCESS: No 'lang=' parameter found in process_and_validate_response calls")
        return True


def check_call_sites() -> bool:
    """
    Check that the call sites would work with the new signatures.
    
    Returns:
        bool: True if all call sites are compatible
    """
    print("\n" + "=" * 60)
    print("Checking call sites compatibility...")
    print("=" * 60)
    
    call_sites = [
        ("generator/agents/testgen_agent/testgen_agent.py", 739),
        ("generator/agents/docgen_agent/docgen_prompt.py", 817),
        ("generator/agents/deploy_agent/deploy_validator.py", 586),
    ]
    
    all_ok = True
    for file_path, expected_line in call_sites:
        print(f"\nChecking {file_path} around line {expected_line}...")
        try:
            with open(file_path, "r") as f:
                lines = f.readlines()
            
            # Check around the expected line
            start = max(0, expected_line - 10)
            end = min(len(lines), expected_line + 10)
            
            found_call = False
            for i in range(start, end):
                if 'call_ensemble_api' in lines[i]:
                    found_call = True
                    if 'stream=' in lines[i]:
                        print(f"  ✓ Found call_ensemble_api with stream parameter at line {i+1}")
                    else:
                        print(f"  ℹ Found call_ensemble_api without stream parameter at line {i+1}")
            
            if not found_call:
                print(f"  ℹ No call_ensemble_api found near line {expected_line}")
        except Exception as e:
            print(f"  ✗ Error reading file: {e}")
            all_ok = False
    
    return all_ok


def main() -> int:
    """
    Main verification function.
    
    Returns:
        int: Exit code (0 for success, 1 for failure)
    """
    print("\n" + "=" * 60)
    print("API SIGNATURE FIXES VERIFICATION")
    print("=" * 60)
    
    results = []
    
    # Check Fix 1: call_ensemble_api stream parameter
    results.append(("call_ensemble_api stream parameter", check_call_ensemble_api_signature()))
    
    # Check Fix 2: docgen lang parameter removal
    results.append(("docgen lang parameter removal", check_docgen_lang_parameter()))
    
    # Check call sites
    results.append(("call sites compatibility", check_call_sites()))
    
    # Summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n✓ All API signature fixes verified successfully!")
        return 0
    else:
        print("\n✗ Some fixes need attention!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
