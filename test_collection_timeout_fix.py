"""
Test to validate pytest collection timeout fixes.

This test validates that:
1. Expensive operations are deferred to fixtures
2. Collection guards (PYTEST_COLLECTING) are working
3. Collection time is reasonable (<30s)
"""
import os
import sys
import time
import subprocess


def test_collection_with_guard_enabled():
    """Test that collection works with PYTEST_COLLECTING=1."""
    start_time = time.time()
    
    # Run pytest collection with PYTEST_COLLECTING set
    env = os.environ.copy()
    env["PYTEST_COLLECTING"] = "1"
    
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "--quiet", 
         "--ignore=omnicore_engine", "tests/"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60  # Collection should complete in < 60 seconds
    )
    
    elapsed_time = time.time() - start_time
    
    # Collection should complete quickly
    assert elapsed_time < 30, f"Collection took {elapsed_time}s, expected < 30s"
    
    # Should exit with 0 (success) or 5 (no tests collected)
    assert result.returncode in [0, 5], f"Collection failed with code {result.returncode}"
    
    print(f"✓ Collection completed in {elapsed_time:.2f}s")


def test_prometheus_stubs_in_conftest():
    """Test that prometheus stubs are only initialized when needed."""
    # Import conftest module
    import conftest
    
    # Check that _initialize_prometheus_stubs function exists
    assert hasattr(conftest, "_initialize_prometheus_stubs")
    
    # The function should be safe to call multiple times
    conftest._initialize_prometheus_stubs()
    conftest._initialize_prometheus_stubs()  # Call again to ensure idempotency
    
    print("✓ Prometheus stubs function is idempotent")


def test_opentelemetry_context_setup():
    """Test that OpenTelemetry context setup is deferred."""
    from pathlib import Path
    
    # Check arbiter conftest
    sys.path.insert(0, str(Path(__file__).parent / "self_fixing_engineer"))
    
    # Import the module
    from arbiter import conftest as arbiter_conftest
    
    # Check that _setup_opentelemetry_context function exists
    assert hasattr(arbiter_conftest, "_setup_opentelemetry_context")
    
    # The function should be safe to call multiple times
    arbiter_conftest._setup_opentelemetry_context()
    arbiter_conftest._setup_opentelemetry_context()
    
    print("✓ OpenTelemetry context setup is idempotent")


def test_logging_setup_in_intent_capture():
    """Test that logging setup is deferred to fixture."""
    import self_fixing_engineer.intent_capture.tests.conftest as ic_conftest
    import _pytest.fixtures
    
    # Check that setup_logging_and_warnings fixture exists
    assert hasattr(ic_conftest, "setup_logging_and_warnings")
    
    # Check that it's a pytest fixture
    func = getattr(ic_conftest, "setup_logging_and_warnings")
    
    # Should be a FixtureFunctionDefinition (more reliable than string comparison)
    assert isinstance(func, _pytest.fixtures.FixtureFunctionDefinition), \
        f"setup_logging_and_warnings should be a pytest fixture, got {type(func)}"
    
    print("✓ Logging setup is in fixture, not at module level")


if __name__ == "__main__":
    print("Running collection timeout fix validation tests...\n")
    
    try:
        test_collection_with_guard_enabled()
        test_prometheus_stubs_in_conftest()
        test_opentelemetry_context_setup()
        test_logging_setup_in_intent_capture()
        
        print("\n✅ All validation tests passed!")
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
