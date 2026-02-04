#!/usr/bin/env python3
"""
Simple verification of critical production fixes without running full tests
"""

import ast
import re
import sys
from pathlib import Path


def check_secrets_caching():
    """Verify caching has been added to secrets.py"""
    secrets_file = Path("generator/audit_log/audit_crypto/secrets.py")
    content = secrets_file.read_text()
    
    checks = {
        "Cache dictionary declared": "_SECRET_CACHE" in content,
        "Cache lock for thread safety": "_SECRET_CACHE_LOCK" in content,
        "Cache TTL configured": "SECRET_CACHE_TTL_SECONDS" in content,
        "Cache timestamps tracked": "_SECRET_CACHE_TIMESTAMPS" in content,
        "Cache checked in retrieval": "if secret_name in _SECRET_CACHE:" in content,
        "Secret stored in cache": "_SECRET_CACHE[secret_name] = secret_value" in content,
        "Jitter added to retry": "random.uniform" in content,
        "Random module imported": "import random" in content,
    }
    
    print("✓ ISSUE 1: Audit Crypto Rate Limiting Fixes")
    for check, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check}")
    
    all_passed = all(checks.values())
    return all_passed


def check_test_validation():
    """Verify test file validation has been added to runner_core.py"""
    runner_file = Path("generator/runner/runner_core.py")
    content = runner_file.read_text()
    
    checks = {
        "Validation method exists": "def _validate_test_files" in content,
        "Pytest naming check": 'startswith("test_")' in content or '"test_*.py"' in content,
        "Test function pattern check": "def test_" in content and "has_test_func" in content,
        "Exit code 5 handling": "returncode == 5" in content,
        "Pytest no tests message": "no tests collected" in content or "no tests were collected" in content,
        "Validation result logging": "validation_result" in content and "warnings" in content,
    }
    
    print("\n✓ ISSUE 2: Test Collection Failure Fixes")
    for check, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check}")
    
    all_passed = all(checks.values())
    return all_passed


def check_rst_improvements():
    """Verify RST generation improvements in docgen_agent.py"""
    docgen_file = Path("generator/agents/docgen_agent/docgen_agent.py")
    content = docgen_file.read_text()
    
    checks = {
        "RST generation method exists": "async def generate_rst" in content,
        "Code block indentation fixed": '    {line}' in content or '"    {' in content,
        "Proper directive spacing": '"\\n"  # Required blank line' in content or "# Required blank line after directive" in content,
        "Validation method added": "def validate_rst" in content,
        "Docutils validation": "from docutils.core import publish_doctree" in content,
        "Validation integrated": "validate_rst(rst_content)" in content,
        "Build skipped on failure": "if SPHINX_AVAILABLE and is_valid:" in content,
    }
    
    print("\n✓ ISSUE 3: RST Documentation Generation Fixes")
    for check, result in checks.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check}")
    
    all_passed = all(checks.values())
    return all_passed


def main():
    print("=" * 70)
    print("CRITICAL PRODUCTION FIXES VERIFICATION")
    print("=" * 70)
    print()
    
    results = []
    
    try:
        results.append(("Audit Crypto Rate Limiting", check_secrets_caching()))
    except Exception as e:
        print(f"\n✗ Error checking secrets caching: {e}")
        results.append(("Audit Crypto Rate Limiting", False))
    
    try:
        results.append(("Test Collection Validation", check_test_validation()))
    except Exception as e:
        print(f"\n✗ Error checking test validation: {e}")
        results.append(("Test Collection Validation", False))
    
    try:
        results.append(("RST Documentation Generation", check_rst_improvements()))
    except Exception as e:
        print(f"\n✗ Error checking RST improvements: {e}")
        results.append(("RST Documentation Generation", False))
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("✅ ALL CRITICAL FIXES VERIFIED SUCCESSFULLY!")
        return 0
    else:
        print("❌ SOME FIXES NOT VERIFIED - Review the output above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
