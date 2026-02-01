"""
Test to verify the redis mock poisoning fix.

This test validates that:
1. Redis and redis.asyncio are NOT mocked early in conftest.py
2. Portalocker can import successfully without SyntaxError
3. Redis client types can be imported for type annotations
"""
import sys
import pytest


def test_redis_not_in_early_mocks():
    """Verify redis is not mocked early by conftest"""
    # If redis was mocked early, it would be in sys.modules already
    # But it should either not be there, or be the real module
    if 'redis' in sys.modules:
        redis_mod = sys.modules['redis']
        # Check it's not a mock
        assert hasattr(redis_mod, '__file__'), "redis should be real, not a mock"
        assert '<mocked' not in str(redis_mod.__file__), "redis should not be mocked"


def test_portalocker_imports_successfully():
    """Verify portalocker can be imported without SyntaxError"""
    try:
        import portalocker
        assert portalocker is not None
        print("✓ portalocker imported successfully")
    except SyntaxError as e:
        pytest.fail(f"portalocker import failed with SyntaxError: {e}")
    except ImportError as e:
        # If portalocker is not installed, that's okay for this test
        pytest.skip(f"portalocker not installed: {e}")


def test_redis_client_types_available():
    """Verify redis.client types can be imported for type annotations"""
    try:
        from redis.client import PubSubWorkerThread
        assert PubSubWorkerThread is not None
        print("✓ PubSubWorkerThread imported successfully")
    except ImportError as e:
        pytest.skip(f"redis not installed: {e}")
    except SyntaxError as e:
        pytest.fail(f"redis.client import failed with SyntaxError: {e}")


def test_redis_in_never_mock_list():
    """Verify redis is in the _NEVER_MOCK list in conftest"""
    import conftest
    
    assert hasattr(conftest, '_NEVER_MOCK'), "conftest should have _NEVER_MOCK"
    
    never_mock = conftest._NEVER_MOCK
    assert 'redis' in never_mock, "redis should be in _NEVER_MOCK"
    assert 'redis.asyncio' in never_mock, "redis.asyncio should be in _NEVER_MOCK"
    assert 'redis.client' in never_mock, "redis.client should be in _NEVER_MOCK"
    print("✓ redis modules are in _NEVER_MOCK list")


def test_redis_not_in_optional_dependencies():
    """Verify redis is NOT in the _OPTIONAL_DEPENDENCIES list in conftest"""
    import conftest
    
    assert hasattr(conftest, '_OPTIONAL_DEPENDENCIES'), "conftest should have _OPTIONAL_DEPENDENCIES"
    
    optional_deps = conftest._OPTIONAL_DEPENDENCIES
    assert 'redis' not in optional_deps, "redis should NOT be in _OPTIONAL_DEPENDENCIES"
    assert 'redis.asyncio' not in optional_deps, "redis.asyncio should NOT be in _OPTIONAL_DEPENDENCIES"
    print("✓ redis modules are NOT in _OPTIONAL_DEPENDENCIES list")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
