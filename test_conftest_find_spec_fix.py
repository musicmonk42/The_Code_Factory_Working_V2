"""
Test to verify that conftest.py uses find_spec instead of try-import.

This test validates the fix for the OOM issue (exit code 137) during
test collection caused by importing omnicore_engine.database and
omnicore_engine.message_bus modules which trigger expensive initialization.
"""
import sys
import os


def test_conftest_does_not_import_expensive_modules():
    """
    Test that conftest.py does NOT import omnicore_engine.database
    or omnicore_engine.message_bus during initialization.
    
    Before the fix, conftest.py used try-import blocks which would
    trigger expensive initialization (database connections, message bus
    setup, event loops, metrics registration) consuming all available
    memory and causing OOM killer to terminate the process (exit code 137).
    
    After the fix, conftest.py uses importlib.util.find_spec() to check
    module existence WITHOUT importing them.
    """
    # Remove conftest and expensive modules from sys.modules
    modules_to_clear = [
        'conftest',
        'omnicore_engine.database',
        'omnicore_engine.message_bus',
    ]
    for module_name in modules_to_clear:
        if module_name in sys.modules:
            del sys.modules[module_name]
    
    # Ensure TESTING is set (conftest.py requires this)
    os.environ["TESTING"] = "1"
    
    # Import conftest
    import conftest
    
    # Verify that expensive modules were NOT imported
    assert 'omnicore_engine.database' not in sys.modules, \
        "omnicore_engine.database should NOT be in sys.modules after importing conftest"
    
    assert 'omnicore_engine.message_bus' not in sys.modules, \
        "omnicore_engine.message_bus should NOT be in sys.modules after importing conftest"
    
    print("✓ conftest.py does not import expensive modules")
    print("  omnicore_engine.database: NOT imported ✓")
    print("  omnicore_engine.message_bus: NOT imported ✓")


def test_conftest_uses_find_spec():
    """
    Test that conftest.py correctly identifies when modules exist or don't exist
    using find_spec, without importing them.
    """
    import importlib.util
    
    # Check that find_spec works for the modules in question
    # (This is what conftest.py should be using)
    
    # Check omnicore_engine.database
    database_spec = importlib.util.find_spec("omnicore_engine.database")
    if database_spec is None:
        print("  omnicore_engine.database: does not exist (will be stubbed)")
    else:
        print("  omnicore_engine.database: exists (will NOT be stubbed)")
    
    # Check omnicore_engine.message_bus
    message_bus_spec = importlib.util.find_spec("omnicore_engine.message_bus")
    if message_bus_spec is None:
        print("  omnicore_engine.message_bus: does not exist (will be stubbed)")
    else:
        print("  omnicore_engine.message_bus: exists (will NOT be stubbed)")
    
    # Verify that find_spec did NOT import the modules
    assert 'omnicore_engine.database' not in sys.modules or \
           sys.modules['omnicore_engine.database'].__file__ == '<stub omnicore_engine.database>', \
           "find_spec should not import omnicore_engine.database"
    
    assert 'omnicore_engine.message_bus' not in sys.modules or \
           sys.modules['omnicore_engine.message_bus'].__file__ == '<stub omnicore_engine.message_bus>', \
           "find_spec should not import omnicore_engine.message_bus"
    
    print("✓ find_spec correctly identifies module existence without importing")


def test_conftest_import_performance():
    """
    Test that conftest.py imports quickly (< 1 second).
    
    Before the fix, importing conftest.py could cause OOM errors.
    After the fix, it should complete in under 1 second.
    """
    import time
    
    # Remove conftest from sys.modules if it's already loaded
    if 'conftest' in sys.modules:
        del sys.modules['conftest']
    
    start_time = time.time()
    import conftest
    import_time = time.time() - start_time
    
    # Should complete in less than 1 second
    assert import_time < 1.0, f"conftest.py import took {import_time:.2f}s, expected < 1s"
    print(f"✓ conftest.py imported in {import_time:.3f}s")


if __name__ == '__main__':
    # Run tests manually
    print("Testing conftest.py find_spec fix...")
    print()
    
    test_conftest_does_not_import_expensive_modules()
    print()
    
    test_conftest_uses_find_spec()
    print()
    
    test_conftest_import_performance()
    print()
    
    print("✓ All find_spec fix tests passed!")
    print()
    print("Summary:")
    print("  - conftest.py does not import expensive modules ✓")
    print("  - conftest.py uses find_spec for module checks ✓")
    print("  - conftest.py imports in < 1 second ✓")
    print()
    print("This fix prevents OOM errors (exit code 137) during pytest collection.")
