"""
Test suite for generator/conftest.py performance improvements.

This test verifies that the deferred mock setup mechanism significantly improves
import times and prevents CPU timeout issues in CI environments.
"""

import os
import sys
import time
import subprocess


def test_generator_conftest_import_speed():
    """
    Test that generator.conftest imports very quickly.
    
    With deferred mock setup, the conftest should import in well under 1 second,
    preventing CPU timeout errors in CI environments.
    """
    # Clear any cached imports
    for module in list(sys.modules.keys()):
        if module.startswith('generator'):
            del sys.modules[module]
    
    start_time = time.time()
    import generator.conftest
    elapsed_time = time.time() - start_time
    
    # Should import in less than 1 second
    assert elapsed_time < 1.0, f"Import took {elapsed_time:.3f}s, expected < 1.0s"
    print(f"✓ generator.conftest imported in {elapsed_time:.3f}s")


def test_mock_setup_is_deferred():
    """
    Test that mock setup is deferred until explicitly called.
    
    After importing conftest, mocks should not be initialized yet.
    They are only set up when the pytest fixture runs.
    """
    # Clear any cached imports
    for module in list(sys.modules.keys()):
        if module.startswith('generator'):
            del sys.modules[module]
    
    # Import conftest - should NOT set up mocks automatically
    import generator.conftest
    
    # Verify mocks are not initialized at import time
    assert not generator.conftest._mocks_initialized, \
        "Mocks should not be initialized at import time"
    
    print(f"✓ Mock setup is properly deferred")


def test_mock_setup_function():
    """
    Test that the mock setup function works correctly when called.
    """
    # Clear any cached imports
    for module in list(sys.modules.keys()):
        if module.startswith('generator'):
            del sys.modules[module]
    
    import generator.conftest
    
    # Manually trigger mock setup
    start_time = time.time()
    generator.conftest._setup_optional_dependency_mocks()
    elapsed_time = time.time() - start_time
    
    # Verify mocks are now initialized
    assert generator.conftest._mocks_initialized, \
        "Mocks should be initialized after calling setup function"
    
    # Calling again should be a no-op (fast)
    start_time = time.time()
    generator.conftest._setup_optional_dependency_mocks()
    second_elapsed = time.time() - start_time
    
    # Second call should be much faster (near instant)
    assert second_elapsed < 0.01, \
        f"Second call took {second_elapsed:.3f}s, should be near instant"
    
    print(f"✓ Mock setup function works correctly ({elapsed_time:.3f}s)")


def test_no_cpu_timeout_on_import():
    """
    Integration test: verify that importing generator.conftest doesn't cause CPU timeout.
    
    This simulates the workflow step that was failing with CPU time limit exceeded.
    """
    # Run the same command that was failing in CI
    cmd = [
        sys.executable,
        '-c',
        "import generator.conftest; print('Generator conftest OK')"
    ]
    
    env = os.environ.copy()
    
    start_time = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,  # 10 second timeout (CI was hitting CPU limit)
        env=env,
        cwd=os.path.dirname(os.path.dirname(__file__))
    )
    elapsed_time = time.time() - start_time
    
    # Should complete successfully
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert 'Generator conftest OK' in result.stdout, \
        f"Expected success message not found in output: {result.stdout}"
    assert elapsed_time < 5.0, \
        f"Import took {elapsed_time:.3f}s, expected < 5.0s (was timing out before fix)"
    
    print(f"✓ No CPU timeout: {elapsed_time:.3f}s")


def test_environment_variables_still_set():
    """
    Test that critical environment variables are still set at import time.
    
    These lightweight operations should remain at import time for proper test setup.
    """
    # Clear any cached imports
    for module in list(sys.modules.keys()):
        if module.startswith('generator'):
            del sys.modules[module]
    
    # Save current environment variables
    saved_vars = {}
    for var in ['TESTING', 'PYTEST_CURRENT_TEST', 'OTEL_SDK_DISABLED']:
        saved_vars[var] = os.environ.get(var)
        if var in os.environ:
            del os.environ[var]
    
    try:
        # Import conftest
        import generator.conftest
        
        # Verify environment variables are set
        assert os.environ.get('TESTING') == '1', "TESTING environment variable not set"
        assert os.environ.get('PYTEST_CURRENT_TEST') == 'true', \
            "PYTEST_CURRENT_TEST environment variable not set"
        assert os.environ.get('OTEL_SDK_DISABLED') == '1', \
            "OTEL_SDK_DISABLED environment variable not set"
        
        print(f"✓ Environment variables properly set at import time")
    finally:
        # Restore original environment variables
        for var, value in saved_vars.items():
            if value is not None:
                os.environ[var] = value
            elif var in os.environ:
                del os.environ[var]


def test_opentelemetry_stub_available():
    """
    Test that OpenTelemetry stubs are still available at import time.
    
    The OpenTelemetry stub is kept at import time because it's critical
    for preventing errors in other imports.
    """
    # Clear any cached imports
    for module in list(sys.modules.keys()):
        if module.startswith('generator') or module.startswith('opentelemetry'):
            del sys.modules[module]
    
    # Import conftest
    import generator.conftest
    
    # Verify OpenTelemetry stub is available
    if 'opentelemetry' in sys.modules:
        import opentelemetry
        assert hasattr(opentelemetry, 'trace'), \
            "OpenTelemetry stub should have trace module"
        print(f"✓ OpenTelemetry stub available at import time")
    else:
        # OpenTelemetry is installed, skip this test
        print(f"✓ OpenTelemetry is installed (stub not needed)")
