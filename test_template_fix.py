#!/usr/bin/env python3
"""
Test to verify the fix for circular import and missing templates.

This test validates:
1. Templates exist with correct naming convention
2. Templates can be loaded via Jinja2
3. No import errors with runner_audit functions
4. log_audit_event_sync is available and callable
"""

import os
import sys

# Set up environment for testing
os.environ['DEV_MODE'] = '1'
os.environ['TESTING'] = '1'

def test_templates_exist():
    """Test that all required templates exist with correct naming."""
    template_dir = os.path.join(
        os.path.dirname(__file__),
        'generator/agents/testgen_agent/testgen_templates'
    )
    
    expected_templates = [
        'default_generation.jinja',
        'default_critique.jinja',
        'default_refinement.jinja',
        'default_self_heal.jinja'
    ]
    
    if not os.path.exists(template_dir):
        print(f"✗ Template directory does not exist: {template_dir}")
        return False
    
    templates = os.listdir(template_dir)
    print(f"✓ Template directory exists with {len(templates)} files")
    
    for template_name in expected_templates:
        if template_name not in templates:
            print(f"✗ Missing template: {template_name}")
            return False
        print(f"  ✓ {template_name}")
    
    return True


def test_templates_loadable():
    """Test that templates can be loaded via Jinja2."""
    from jinja2 import Environment, FileSystemLoader
    
    template_dir = os.path.join(
        os.path.dirname(__file__),
        'generator/agents/testgen_agent/testgen_templates'
    )
    
    env = Environment(loader=FileSystemLoader(template_dir))
    
    templates = [
        'default_generation.jinja',
        'default_critique.jinja',
        'default_refinement.jinja',
        'default_self_heal.jinja'
    ]
    
    for template_name in templates:
        try:
            template = env.get_template(template_name)
            print(f"✓ Successfully loaded: {template_name}")
        except Exception as e:
            print(f"✗ Failed to load {template_name}: {e}")
            return False
    
    return True


def test_audit_imports():
    """Test that audit logging functions can be imported."""
    try:
        from generator.runner.runner_audit import log_audit_event, log_audit_event_sync
        print("✓ Successfully imported log_audit_event and log_audit_event_sync")
        
        # Verify they are callable
        if not callable(log_audit_event):
            print("✗ log_audit_event is not callable")
            return False
        
        if not callable(log_audit_event_sync):
            print("✗ log_audit_event_sync is not callable")
            return False
        
        print("✓ Both audit functions are callable")
        return True
        
    except Exception as e:
        print(f"✗ Failed to import audit functions: {e}")
        return False


def test_sync_wrapper_safe():
    """Test that log_audit_event_sync can be called safely in sync context."""
    try:
        from generator.runner.runner_audit import log_audit_event_sync
        
        # This should not raise an error even without an event loop
        # It will just log a debug message
        log_audit_event_sync("test_event", {"test": "data"})
        print("✓ log_audit_event_sync can be called safely in sync context")
        return True
        
    except Exception as e:
        print(f"✗ log_audit_event_sync raised an error: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("Testing Circular Import and Missing Templates Fix")
    print("=" * 60)
    print()
    
    tests = [
        ("Templates exist", test_templates_exist),
        ("Templates loadable", test_templates_loadable),
        ("Audit imports", test_audit_imports),
        ("Sync wrapper safe", test_sync_wrapper_safe),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        print(f"\n[Test: {test_name}]")
        try:
            if test_func():
                passed += 1
                print(f"✓ {test_name} PASSED")
            else:
                failed += 1
                print(f"✗ {test_name} FAILED")
        except Exception as e:
            failed += 1
            print(f"✗ {test_name} FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
    
    print()
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
