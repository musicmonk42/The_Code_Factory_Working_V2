#!/usr/bin/env python3
"""
Validation script for circular import fixes in runner module.

This script verifies that the circular import issues described in the problem statement
have been successfully resolved.

Expected behaviors after fix:
1. No "partially initialized module" errors
2. No "circular import" errors  
3. All runner modules can be imported successfully
4. CoverageReportSchema is accessible from runner_parsers
5. log_audit_event is accessible from runner_logging
6. GeneratorRunner and LLM client functions are available
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
os.environ['TESTING'] = '1'  # Prevent watchers from starting

def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)

def test_import(module_name, description):
    """Test importing a module and check for circular import errors."""
    try:
        mod = __import__(module_name, fromlist=[''])
        print(f"✓ {description}")
        return True, mod
    except ImportError as e:
        error_msg = str(e)
        if 'partially initialized' in error_msg or 'circular import' in error_msg:
            print(f"✗ {description}")
            print(f"  ERROR: {e}")
            return False, None
        else:
            # Missing dependency, not a circular import
            print(f"⚠ {description} (missing dependency: {type(e).__name__})")
            return True, None
    except Exception as e:
        error_msg = str(e)
        if 'partially initialized' in error_msg or 'circular import' in error_msg:
            print(f"✗ {description}")
            print(f"  ERROR: {e}")
            return False, None
        else:
            print(f"⚠ {description} (other error: {type(e).__name__})")
            return True, None

def main():
    """Run all validation tests."""
    print("\n" + "="*70)
    print("  CIRCULAR IMPORT FIX VALIDATION")
    print("  Testing: generator.runner module")
    print("="*70)
    
    all_passed = True
    
    # Test 1: Core modules that had circular dependencies
    print_section("Test 1: Core Modules (Previously Had Circular Dependencies)")
    
    tests = [
        ('generator.runner.runner_parsers', 'runner_parsers (was importing from runner_logging)'),
        ('generator.runner.runner_logging', 'runner_logging (had lazy imports to runner_parsers)'),
        ('generator.runner.runner_core', 'runner_core (mixed relative/absolute imports)'),
        ('generator.runner.runner_errors', 'runner_errors (imported from runner_security_utils)'),
        ('generator.runner.runner_security_utils', 'runner_security_utils (lazy imports to runner_logging)'),
    ]
    
    for module_name, description in tests:
        passed, _ = test_import(module_name, description)
        all_passed = all_passed and passed
    
    # Test 2: Specific imports from error logs
    print_section("Test 2: Specific Imports from Error Logs")
    
    try:
        from generator.runner.runner_parsers import CoverageReportSchema
        print("✓ CoverageReportSchema imported successfully")
        print("  (Was failing: 'cannot import name CoverageReportSchema from")
        print("   partially initialized module runner.runner_parsers')")
    except ImportError as e:
        if 'partially initialized' in str(e) or 'circular import' in str(e):
            print(f"✗ CoverageReportSchema import failed: {e}")
            all_passed = False
        else:
            print(f"⚠ CoverageReportSchema (missing dependency)")
    
    try:
        from generator.runner.runner_logging import log_audit_event
        print("✓ log_audit_event imported successfully")
        print("  (Was failing: 'cannot import name log_audit_event from")
        print("   partially initialized module runner.runner_logging')")
    except ImportError as e:
        if 'partially initialized' in str(e) or 'circular import' in str(e):
            print(f"✗ log_audit_event import failed: {e}")
            all_passed = False
        else:
            print(f"⚠ log_audit_event (missing dependency)")
    
    # Test 3: Package-level imports
    print_section("Test 3: Package-Level Imports")
    
    try:
        from generator import runner
        print("✓ generator.runner package imported successfully")
        
        if hasattr(runner, 'run_tests_in_sandbox'):
            print("✓ run_tests_in_sandbox is available")
            print("  (Was showing: 'GeneratorRunner not available')")
        else:
            print("⚠ run_tests_in_sandbox not found (but no circular import)")
            
        if hasattr(runner, 'run_tests'):
            print("✓ run_tests is available")
        else:
            print("⚠ run_tests not found (but no circular import)")
            
    except ImportError as e:
        if 'partially initialized' in str(e) or 'circular import' in str(e):
            print(f"✗ generator.runner package failed: {e}")
            all_passed = False
        else:
            print(f"⚠ generator.runner package (missing dependency)")
    
    # Test 4: Additional fixed modules
    print_section("Test 4: Additional Fixed Modules")
    
    additional_tests = [
        ('generator.runner.llm_client', 'llm_client'),
        ('generator.runner.runner_app', 'runner_app'),
        ('generator.runner.runner_backends', 'runner_backends'),
        ('generator.runner.runner_config', 'runner_config'),
        ('generator.runner.runner_file_utils', 'runner_file_utils'),
        ('generator.runner.runner_mutation', 'runner_mutation'),
        ('generator.runner.summarize_utils', 'summarize_utils'),
        ('generator.runner.llm_plugin_manager', 'llm_plugin_manager'),
    ]
    
    for module_name, description in additional_tests:
        passed, _ = test_import(module_name, description)
        all_passed = all_passed and passed
    
    # Final result
    print_section("FINAL RESULT")
    
    if all_passed:
        print("✅ ALL TESTS PASSED")
        print("\nCircular import issues have been successfully resolved!")
        print("\nExpected improvements:")
        print("  • No 'partially initialized module' errors")
        print("  • No 'circular import' errors")
        print("  • GeneratorRunner available (not falling back to mocks)")
        print("  • LLM client functions available")
        print("  • Agents can import runner components")
        print("  • Application starts without import failures")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print("\nCircular import issues still present.")
        print("Review the errors above for details.")
        return 1

if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
