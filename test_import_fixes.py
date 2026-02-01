#!/usr/bin/env python3
"""
Test script to verify critical import error fixes.

This script validates that the following fixes work correctly:
1. Priority 1: Correct aiohttp imports in deploy_agent modules
2. Priority 2: Circular import resolution in clarifier module
3. Priority 3: Defensive imports for omnicore_engine.plugin_registry

Run with: python test_import_fixes.py
"""

import sys


def test_priority_1_aiohttp_imports():
    """Test that aiohttp imports are correct in deploy_agent modules."""
    print("Testing Priority 1: aiohttp imports...")
    
    # Test the correct import pattern
    try:
        from aiohttp.web import Request, Response, RouteTableDef
        print("  ✓ aiohttp.web imports work correctly")
    except ImportError as e:
        print(f"  ✗ FAILED: aiohttp.web import failed: {e}")
        return False
    
    # Verify source files use the correct import by checking file content
    import os
    
    files_to_check = [
        "generator/agents/deploy_agent/deploy_response_handler.py",
        "generator/agents/deploy_agent/deploy_validator.py"
    ]
    
    for filepath in files_to_check:
        if not os.path.exists(filepath):
            print(f"  ✗ FAILED: File not found: {filepath}")
            return False
        
        with open(filepath, 'r') as f:
            content = f.read()
        
        # Check for correct import
        if "from aiohttp.web import Request, Response, RouteTableDef" not in content:
            print(f"  ✗ FAILED: {filepath} does not use correct import pattern")
            return False
        
        # Check for incorrect imports (the bug we fixed)
        if "from aiohttp.web_request import" in content:
            print(f"  ✗ FAILED: {filepath} still has incorrect web_request import")
            return False
        if "from aiohttp.web_response import" in content:
            print(f"  ✗ FAILED: {filepath} still has incorrect web_response import")
            return False
        if "from aiohttp.web_routedef import" in content:
            print(f"  ✗ FAILED: {filepath} still has incorrect web_routedef import")
            return False
    
    print("  ✓ Source files use correct aiohttp.web import pattern")
    print("  ✓ Priority 1 fixes verified\n")
    return True


def test_priority_2_clarifier_circular_import():
    """Test that clarifier circular import is resolved."""
    print("Testing Priority 2: clarifier circular import...")
    
    try:
        # Import the Clarifier class directly
        from generator.clarifier.clarifier import Clarifier
        print("  ✓ Can import Clarifier from clarifier.clarifier")
    except ImportError as e:
        print(f"  ✗ FAILED: Cannot import Clarifier: {e}")
        return False
    
    try:
        # Import through __init__.py which should have wrapper functions
        from generator.clarifier import Clarifier, get_config, get_fernet, get_logger
        print("  ✓ Can import Clarifier and utility functions from __init__.py")
    except ImportError as e:
        print(f"  ✗ FAILED: Cannot import from __init__.py: {e}")
        return False
    
    try:
        # Import clarifier_prompt which was causing circular dependency
        from generator.clarifier import clarifier_prompt
        print("  ✓ Can import clarifier_prompt without circular dependency")
    except ImportError as e:
        print(f"  ✗ FAILED: clarifier_prompt import failed: {e}")
        return False
    
    print("  ✓ Priority 2 fixes verified\n")
    return True


def test_priority_3_plugin_registry_fallback():
    """Test that omnicore_engine.plugin_registry has proper fallback."""
    print("Testing Priority 3: omnicore_engine.plugin_registry fallback...")
    
    try:
        from generator.agents.generator_plugin_wrapper import PlugInKind, plugin, PLUGIN_REGISTRY
        print("  ✓ Can import PlugInKind, plugin, and PLUGIN_REGISTRY")
    except ImportError as e:
        print(f"  ✗ FAILED: Cannot import from generator_plugin_wrapper: {e}")
        return False
    
    # Verify PlugInKind has required attributes
    try:
        execution = PlugInKind.EXECUTION
        print(f"  ✓ PlugInKind.EXECUTION available: {execution}")
    except AttributeError as e:
        print(f"  ✗ FAILED: PlugInKind.EXECUTION not available: {e}")
        return False
    
    # Verify plugin decorator is callable
    if not callable(plugin):
        print("  ✗ FAILED: plugin decorator is not callable")
        return False
    print("  ✓ plugin decorator is callable")
    
    print("  ✓ Priority 3 fixes verified\n")
    return True


def main():
    """Run all import fix tests."""
    print("=" * 70)
    print("Critical Import Error Fixes - Verification Test")
    print("=" * 70)
    print()
    
    results = []
    
    # Test all three priorities
    results.append(("Priority 1 (aiohttp)", test_priority_1_aiohttp_imports()))
    results.append(("Priority 2 (clarifier)", test_priority_2_clarifier_circular_import()))
    results.append(("Priority 3 (plugin_registry)", test_priority_3_plugin_registry_fallback()))
    
    # Print summary
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{name}: {status}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("✓ All import fixes verified successfully!")
        return 0
    else:
        print("✗ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
