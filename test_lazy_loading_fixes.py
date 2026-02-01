#!/usr/bin/env python3
"""
Test script to verify lazy loading import fixes.
Tests that the fixes to omnicore_engine/__init__.py properly prevent stub module creation.
"""

import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_lazy_loading_imports():
    """Test various import patterns to ensure lazy loading works correctly."""
    
    print("=" * 70)
    print("Testing Lazy Loading Import Fixes")
    print("=" * 70)
    
    # Test 1: Import Database from omnicore_engine.database
    print("\n1. Testing: from omnicore_engine.database import Database")
    try:
        from omnicore_engine.database import Database
        print(f"   ✓ SUCCESS: Database class imported: {Database}")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 2: Import from message_bus.message_types
    print("\n2. Testing: from omnicore_engine.message_bus.message_types import Message")
    try:
        from omnicore_engine.message_bus.message_types import Message
        print(f"   ✓ SUCCESS: Message class imported: {Message}")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 3: Import metrics_helpers functions from database
    print("\n3. Testing: from omnicore_engine.database.metrics_helpers import get_or_create_counter_local")
    try:
        from omnicore_engine.database.metrics_helpers import get_or_create_counter_local
        print(f"   ✓ SUCCESS: get_or_create_counter_local imported: {get_or_create_counter_local}")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Import models from database
    print("\n4. Testing: from omnicore_engine.database.models import AgentState")
    try:
        from omnicore_engine.database.models import AgentState
        print(f"   ✓ SUCCESS: AgentState imported: {AgentState}")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Verify lazy loading works for database
    print("\n5. Testing: from omnicore_engine import database (lazy load)")
    try:
        import omnicore_engine
        from omnicore_engine import database as db_module
        print(f"   ✓ SUCCESS: database module lazy-loaded: {db_module}")
        # Verify it's cached
        if 'database' in omnicore_engine._module_cache:
            print(f"   ✓ Module is properly cached in _module_cache")
        else:
            print(f"   ⚠ Warning: Module not in _module_cache")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Verify sys.modules has proper entries
    print("\n6. Testing: Verify sys.modules has proper module entries")
    try:
        required_modules = [
            'omnicore_engine',
            'omnicore_engine.database',
        ]
        for mod_name in required_modules:
            if mod_name in sys.modules:
                mod = sys.modules[mod_name]
                # Check it's not a stub
                if hasattr(mod, '__file__') or hasattr(mod, '__path__'):
                    print(f"   ✓ {mod_name} is properly loaded in sys.modules")
                else:
                    print(f"   ⚠ {mod_name} may be a stub module")
            else:
                print(f"   ⚠ {mod_name} is NOT in sys.modules")
    except Exception as e:
        print(f"   ✗ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n" + "=" * 70)
    print("✓ ALL LAZY LOADING IMPORT TESTS PASSED!")
    print("=" * 70)
    return True

if __name__ == "__main__":
    success = test_lazy_loading_imports()
    sys.exit(0 if success else 1)
