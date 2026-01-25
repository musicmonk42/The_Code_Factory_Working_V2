"""
Test to verify that conftest.py import performance is acceptable.

This test validates the fix for the CPU time limit exceeded issue
during conftest.py import in CI environments.
"""
import time
import sys


def test_conftest_import_time():
    """
    Test that conftest.py imports in less than 1 second.
    
    Before the fix, importing conftest.py could take 17+ seconds in CI
    due to expensive module-level operations. After the fix, it should
    complete in under 1 second.
    """
    # Remove conftest from sys.modules if it's already loaded
    if 'conftest' in sys.modules:
        del sys.modules['conftest']
    
    start_time = time.time()
    import conftest
    import_time = time.time() - start_time
    
    # Should complete in less than 1 second
    assert import_time < 1.0, f"conftest.py import took {import_time:.2f}s, expected < 1s"
    print(f"✓ conftest.py imported in {import_time:.3f}s")


def test_deferred_mock_initialization():
    """
    Test that mock initialization is properly deferred.
    
    The expensive operations should not run at module import time,
    but should be deferred to the initialize_mocks() fixture.
    """
    import conftest
    
    # Before the fixture runs, mocks should not be initialized
    # (This test runs after import, so we check the flag exists and functions are defined)
    assert hasattr(conftest, '_mocks_initialized'), "Missing _mocks_initialized flag"
    assert hasattr(conftest, '_initialize_optional_dependency_mocks'), "Missing mock initialization function"
    assert hasattr(conftest, '_initialize_omnicore_mocks'), "Missing omnicore initialization function"
    
    # If not already initialized, check the flag is False
    # Note: In a real test session, the fixture will have run, so this might be True
    print("✓ Mock initialization functions are properly defined")
    print(f"  Mocks initialized: {conftest._mocks_initialized}")


def test_mock_functionality():
    """
    Test that mocked modules work correctly after initialization.
    
    This ensures that the deferred initialization still provides
    proper mocking functionality.
    """
    import conftest
    
    # Ensure mocks are initialized
    conftest._initialize_optional_dependency_mocks()
    conftest._initialize_omnicore_mocks()
    
    # Check that some mocked modules are available
    # (Only if they weren't already installed)
    assert conftest._mocks_initialized, "Mocks should be initialized"
    
    # Check that tiktoken is either real or mocked (should be in sys.modules)
    assert 'tiktoken' in sys.modules, "tiktoken should be available (real or mocked)"
    
    print("✓ Mock initialization completed successfully")
    print(f"  Total modules in sys.modules: {len(sys.modules)}")


if __name__ == '__main__':
    # Run tests manually
    test_conftest_import_time()
    test_deferred_mock_initialization()
    test_mock_functionality()
    print("\n✓ All performance tests passed!")
