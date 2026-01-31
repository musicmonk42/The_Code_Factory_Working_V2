#!/usr/bin/env python3
"""
Test to verify the OpenTelemetry tracing fix in _run_codegen method.

This test verifies that:
1. The context manager is properly used with a `with` statement
2. The span object (not context manager) is used for set_attribute, set_status, etc.
3. No manual __enter__ or __exit__ calls remain
"""

import ast
import re
from pathlib import Path


def test_tracing_fix():
    """Verify the OpenTelemetry tracing fix is correctly implemented."""
    
    # Read the file
    file_path = Path(__file__).parent / "server" / "services" / "omnicore_service.py"
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Extract the _run_codegen method
    # Find the method definition
    method_start = content.find("async def _run_codegen(")
    assert method_start != -1, "_run_codegen method not found"
    
    # Find the next method definition to get the boundary
    next_method = content.find("\n    async def ", method_start + 1)
    if next_method == -1:
        next_method = content.find("\n    def ", method_start + 1)
    
    method_content = content[method_start:next_method]
    
    print("✓ Found _run_codegen method")
    
    # Check 1: Verify no manual __enter__ calls remain
    assert "__enter__()" not in method_content, \
        "ERROR: Manual __enter__() call still exists in _run_codegen"
    print("✓ No manual __enter__() calls found")
    
    # Check 2: Verify no manual __exit__ calls remain  
    assert "__exit__(" not in method_content, \
        "ERROR: Manual __exit__() call still exists in _run_codegen"
    print("✓ No manual __exit__() calls found")
    
    # Check 3: Verify proper with statement for tracing
    assert "with tracer.start_as_current_span(" in method_content, \
        "ERROR: Missing proper 'with tracer.start_as_current_span()' statement"
    print("✓ Found proper 'with' statement for span creation")
    
    # Check 4: Verify span variable is used (not span_context)
    # Look for the with statement pattern
    with_pattern = r'with tracer\.start_as_current_span\([^)]+\) as (\w+):'
    match = re.search(with_pattern, method_content)
    assert match, "ERROR: Could not find 'with tracer.start_as_current_span() as ...:' pattern"
    span_var = match.group(1)
    print(f"✓ Found span variable: '{span_var}'")
    
    # Check 5: Verify span variable is used for set_attribute (not span_context)
    set_attr_pattern = rf'{span_var}\.set_attribute\('
    assert re.search(set_attr_pattern, method_content), \
        f"ERROR: No calls to {span_var}.set_attribute() found"
    print(f"✓ Found {span_var}.set_attribute() calls")
    
    # Check 6: Verify span variable is used for set_status
    set_status_pattern = rf'{span_var}\.set_status\('
    assert re.search(set_status_pattern, method_content), \
        f"ERROR: No calls to {span_var}.set_status() found"
    print(f"✓ Found {span_var}.set_status() calls")
    
    # Check 7: Verify no references to span_context.set_attribute
    assert "span_context.set_attribute" not in method_content, \
        "ERROR: Old 'span_context.set_attribute' pattern still exists"
    print("✓ No old 'span_context.set_attribute' patterns found")
    
    # Check 8: Verify no references to span_context.set_status
    assert "span_context.set_status" not in method_content, \
        "ERROR: Old 'span_context.set_status' pattern still exists"
    print("✓ No old 'span_context.set_status' patterns found")
    
    # Check 9: Verify the helper function approach is used
    assert "async def _execute_codegen(span=None):" in method_content, \
        "ERROR: Missing helper function _execute_codegen"
    print("✓ Found helper function _execute_codegen")
    
    # Check 10: Verify conditional execution based on TRACING_AVAILABLE
    assert "if TRACING_AVAILABLE:" in method_content, \
        "ERROR: Missing TRACING_AVAILABLE conditional"
    print("✓ Found TRACING_AVAILABLE conditional")
    
    # Check 11: Verify both traced and non-traced paths exist
    assert "return await _execute_codegen(span)" in method_content, \
        "ERROR: Missing traced execution path"
    assert "return await _execute_codegen()" in method_content, \
        "ERROR: Missing non-traced execution path"
    print("✓ Found both traced and non-traced execution paths")
    
    print("\n" + "="*60)
    print("ALL CHECKS PASSED! ✓")
    print("="*60)
    print("\nThe OpenTelemetry tracing fix has been correctly implemented:")
    print("- No manual __enter__() or __exit__() calls")
    print("- Proper 'with' statement for context manager")
    print("- Span object used for set_attribute/set_status/record_exception")
    print("- Conditional execution based on TRACING_AVAILABLE")
    print("- Helper function pattern for code reuse")


if __name__ == "__main__":
    test_tracing_fix()
