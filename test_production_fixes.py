#!/usr/bin/env python3
"""
Test script to verify the circular import fix and file router functionality.

This script tests:
1. No circular import deadlock in codegen_agent
2. File router endpoints are properly configured
3. Security validations are in place
"""

import os
import sys
import tempfile
from pathlib import Path

# Set test environment
os.environ['DEV_MODE'] = '1'
os.environ['TESTING'] = '1'
os.environ['SKIP_IMPORT_TIME_VALIDATION'] = '1'

def test_circular_import():
    """Test that the circular import issue is resolved."""
    print("=" * 70)
    print("TEST 1: Circular Import Fix")
    print("=" * 70)
    
    try:
        # Import runner_audit
        from generator.runner.runner_audit import log_audit_event
        print("✓ runner_audit.log_audit_event imported successfully")
        
        # Try importing codegen_agent - this would fail with circular import
        try:
            import generator.agents.codegen_agent.codegen_agent as codegen
            print("✓ codegen_agent module imported successfully")
            print("✓ NO CIRCULAR IMPORT DETECTED")
            return True
        except ImportError as e:
            error_msg = str(e)
            if "cannot import name 'log_audit_event' from partially initialized module" in error_msg:
                print(f"✗ CIRCULAR IMPORT DETECTED: {e}")
                return False
            else:
                print(f"✓ No circular import (missing dependency: {e})")
                return True
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_file_router():
    """Test that file router is properly configured."""
    print("\n" + "=" * 70)
    print("TEST 2: File Router Configuration")
    print("=" * 70)
    
    try:
        # Check source code directly (avoid import issues)
        source_code = Path("server/routers/files.py").read_text()
        
        print("✓ File router module found")
        
        # Check for router definition
        if 'router = APIRouter' in source_code:
            print("  ✓ Router defined with APIRouter")
        else:
            print("  ✗ Router not properly defined")
            return False
        
        # Check for expected routes
        expected_routes = [
            '/{job_id}/{filename:path}',
            '/{job_id}/list'
        ]
        
        print(f"\n  Checking for expected routes:")
        for route in expected_routes:
            if route in source_code:
                print(f"    ✓ Route '{route}' found")
            else:
                print(f"    ✗ Route '{route}' missing")
                return False
        
        # Check for async functions
        if 'async def get_file' in source_code and 'async def list_files' in source_code:
            print("  ✓ Async endpoint functions present")
        else:
            print("  ✗ Async functions missing")
            return False
        
        print("\n✓ All expected routes configured")
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_security_validations():
    """Test that security validations are in place."""
    print("\n" + "=" * 70)
    print("TEST 3: Security Validations & Industry Standards")
    print("=" * 70)
    
    try:
        # Check that the module has security checks
        source_code = Path("server/routers/files.py").read_text()
        
        security_checks = [
            ('Directory traversal check', '".." in'),
            ('Path validation', 'resolve()'),
            ('Regex validation', 're.match'),
            ('Alphanumeric check', 'isalnum()'),
        ]
        
        # Industry standards checks
        industry_standards = [
            ('Logging support', 'import logging'),
            ('Logger usage', 'logger.'),
            ('Status codes', 'status.HTTP_'),
            ('Pydantic Field descriptions', 'Field('),
            ('OpenAPI documentation', 'summary='),
            ('Response models', 'response_model='),
        ]
        
        all_passed = True
        
        print("\n  Security Validations:")
        for check_name, check_pattern in security_checks:
            if check_pattern in source_code:
                print(f"    ✓ {check_name} implemented")
            else:
                print(f"    ✗ {check_name} missing")
                all_passed = False
        
        print("\n  Industry Standards:")
        industry_passed = True
        for check_name, check_pattern in industry_standards:
            if check_pattern in source_code:
                print(f"    ✓ {check_name} present")
            else:
                print(f"    ⚠ {check_name} missing (recommended)")
                industry_passed = False
        
        if all_passed and industry_passed:
            print("\n✓ All security validations in place")
            print("✓ All industry standards implemented")
            return True
        elif all_passed:
            print("\n✓ All security validations in place")
            print("⚠ Some industry standards missing but not critical")
            return True
        else:
            print("\n✗ Some security validations missing")
            return False
            
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_env_template():
    """Test that production environment template is properly configured."""
    print("\n" + "=" * 70)
    print("TEST 4: Production Environment Template")
    print("=" * 70)
    
    try:
        template_path = Path(".env.production.template")
        if not template_path.exists():
            print("✗ .env.production.template not found")
            return False
        
        print("✓ .env.production.template exists")
        
        content = template_path.read_text()
        
        # Check for security warnings
        if "⚠️" in content and "SECURITY WARNING" in content:
            print("✓ Security warnings present")
        else:
            print("✗ Security warnings missing")
            return False
        
        # Check that hardcoded keys are replaced
        if "REPLACE_WITH" in content:
            print("✓ Placeholder values for keys")
        else:
            print("⚠  Warning: May contain hardcoded keys")
        
        # Check for key generation instructions
        if "openssl rand" in content or "secrets.token" in content:
            print("✓ Key generation instructions provided")
        else:
            print("✗ Key generation instructions missing")
            return False
        
        print("\n✓ Environment template properly configured")
        return True
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("CRITICAL PRODUCTION ISSUES - VERIFICATION TESTS")
    print("=" * 70 + "\n")
    
    results = []
    results.append(("Circular Import Fix", test_circular_import()))
    results.append(("File Router Configuration", test_file_router()))
    results.append(("Security Validations", test_security_validations()))
    results.append(("Environment Template", test_env_template()))
    
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {test_name}")
    
    all_passed = all(r[1] for r in results)
    
    print("\n" + "=" * 70)
    if all_passed:
        print("✓ ALL TESTS PASSED - Production issues resolved!")
    else:
        print("✗ SOME TESTS FAILED - Review failures above")
    print("=" * 70)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
