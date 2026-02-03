#!/usr/bin/env python3
"""
Validation script for CI pipeline fixes.

This script validates that:
1. Async/await issues are fixed
2. Pytest collection warnings are resolved
3. Class renames work correctly with backward compatibility
"""

import sys
import ast
import inspect


def validate_async_await_fixes():
    """Validate that await is properly used with async functions."""
    print("\n=== Validating Async/Await Fixes ===")
    
    # Check codegen_agent.py
    with open("generator/agents/codegen_agent/codegen_agent.py", "r") as f:
        content = f.read()
        if "await audit_logger.log_action" in content:
            print("✓ codegen_agent.py: await added to audit_logger.log_action")
        else:
            print("✗ codegen_agent.py: Missing await on audit_logger.log_action")
            return False
    
    # Check critique_prompt.py
    with open("generator/agents/critique_agent/critique_prompt.py", "r") as f:
        content = f.read()
        if "await log_action" in content:
            print("✓ critique_prompt.py: await added to log_action")
        else:
            print("✗ critique_prompt.py: Missing await on log_action")
            return False
    
    return True


def validate_pytest_collection_fixes():
    """Validate that pytest collection warnings are resolved."""
    print("\n=== Validating Pytest Collection Fixes ===")
    
    # Check test_audit_log_audit_plugins.py
    with open("generator/tests/test_audit_log_audit_plugins.py", "r") as f:
        content = f.read()
        
        if "class _TestPlugin(AuditPlugin):" in content:
            print("✓ test_audit_log_audit_plugins.py: TestPlugin renamed to _TestPlugin")
        else:
            print("✗ test_audit_log_audit_plugins.py: TestPlugin not renamed")
            return False
        
        if "class _TestCommercialPlugin(CommercialPlugin):" in content:
            print("✓ test_audit_log_audit_plugins.py: TestCommercialPlugin renamed to _TestCommercialPlugin")
        else:
            print("✗ test_audit_log_audit_plugins.py: TestCommercialPlugin not renamed")
            return False
        
        # Verify references are updated
        if "_TestPlugin()" in content and "_TestCommercialPlugin()" in content:
            print("✓ test_audit_log_audit_plugins.py: Class references updated")
        else:
            print("⚠ test_audit_log_audit_plugins.py: Some class references may not be updated")
    
    # Check runner_parsers.py
    with open("generator/runner/runner_parsers.py", "r") as f:
        content = f.read()
        
        if "class TestCaseResultModel(BaseModel):" in content:
            print("✓ runner_parsers.py: TestCaseResult renamed to TestCaseResultModel")
        else:
            print("✗ runner_parsers.py: TestCaseResult not renamed")
            return False
        
        if "class TestReportModel(BaseModel):" in content:
            print("✓ runner_parsers.py: TestReportSchema renamed to TestReportModel")
        else:
            print("✗ runner_parsers.py: TestReportSchema not renamed")
            return False
        
        # Check backward compatibility aliases
        if "TestCaseResult = TestCaseResultModel" in content:
            print("✓ runner_parsers.py: TestCaseResult backward compatibility alias added")
        else:
            print("✗ runner_parsers.py: Missing TestCaseResult alias")
            return False
        
        if "TestReportSchema = TestReportModel" in content:
            print("✓ runner_parsers.py: TestReportSchema backward compatibility alias added")
        else:
            print("✗ runner_parsers.py: Missing TestReportSchema alias")
            return False
    
    return True


def validate_syntax():
    """Validate that all modified files have valid Python syntax."""
    print("\n=== Validating Python Syntax ===")
    
    files_to_check = [
        "generator/agents/codegen_agent/codegen_agent.py",
        "generator/agents/critique_agent/critique_prompt.py",
        "generator/runner/runner_parsers.py",
        "generator/tests/test_audit_log_audit_plugins.py",
    ]
    
    for file_path in files_to_check:
        try:
            with open(file_path, "r") as f:
                ast.parse(f.read())
            print(f"✓ {file_path}: Valid Python syntax")
        except SyntaxError as e:
            print(f"✗ {file_path}: Syntax error - {e}")
            return False
    
    return True


def main():
    """Run all validations."""
    print("=" * 60)
    print("CI Pipeline Fixes Validation")
    print("=" * 60)
    
    results = []
    
    # Run validations
    results.append(("Syntax Validation", validate_syntax()))
    results.append(("Async/Await Fixes", validate_async_await_fixes()))
    results.append(("Pytest Collection Fixes", validate_pytest_collection_fixes()))
    
    # Summary
    print("\n" + "=" * 60)
    print("Validation Summary")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n✓ All validations passed!")
        return 0
    else:
        print("\n✗ Some validations failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
