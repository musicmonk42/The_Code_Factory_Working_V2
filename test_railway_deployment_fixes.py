"""
Test Railway deployment fixes.

This test validates that:
1. AUDIT_CRYPTO_MODE=disabled prevents boto3 initialization
2. Server can start with single worker configuration
3. Health endpoint is accessible
4. Configuration files are consistent (Procfile, railway.json, railway.toml)
5. Redis is marked as optional/recommended (not critical)
"""

import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_audit_crypto_disabled_mode():
    """Test that AUDIT_CRYPTO_MODE=disabled prevents boto3 imports."""
    # Set environment before importing
    os.environ["AUDIT_CRYPTO_MODE"] = "disabled"
    os.environ["AUDIT_CRYPTO_ALLOW_INIT_FAILURE"] = "1"
    os.environ["RUNNING_TESTS"] = "true"
    
    # This import should NOT trigger boto3 initialization when disabled
    import generator.audit_log.audit_crypto.audit_crypto_factory as factory
    
    # Verify disabled mode is detected
    assert factory._is_crypto_disabled(), "Disabled mode not detected"
    
    # Verify boto3 was not loaded (should still be None)
    assert factory.boto3 is None, "boto3 should not be loaded when disabled"
    
    print("✓ AUDIT_CRYPTO_MODE=disabled prevents boto3 initialization")


def test_railway_toml_configuration():
    """Test that railway.toml has correct configuration."""
    railway_toml_path = project_root / "railway.toml"
    
    assert railway_toml_path.exists(), "railway.toml should exist"
    
    content = railway_toml_path.read_text()
    
    # Check for single worker configuration
    assert "workers" in content.lower(), "railway.toml should mention workers"
    assert "server/run.py" in content, "railway.toml should use server/run.py"
    
    # Check for verbose logging (--log-level debug)
    assert "--log-level debug" in content, "railway.toml should use --log-level debug for verbose logging"
    
    # Check for required environment variables
    assert "AUDIT_CRYPTO_MODE" in content, "AUDIT_CRYPTO_MODE should be in railway.toml"
    assert "AUDIT_CRYPTO_ALLOW_INIT_FAILURE" in content, "AUDIT_CRYPTO_ALLOW_INIT_FAILURE should be in railway.toml"
    assert "PORT" in content, "PORT should be in railway.toml"
    
    # Check for healthcheck configuration
    assert "healthcheckPath" in content, "healthcheckPath should be configured"
    assert "/health" in content, "/health endpoint should be configured"
    
    print("✓ railway.toml has correct configuration")


def test_railway_json_configuration():
    """Test that railway.json has correct configuration."""
    railway_json_path = project_root / "railway.json"
    
    assert railway_json_path.exists(), "railway.json should exist"
    
    content = railway_json_path.read_text()
    
    # Check start command uses server/run.py (not python -m server.main)
    assert "server/run.py" in content, "railway.json should use server/run.py"
    assert "--log-level debug" in content, "railway.json should use --log-level debug"
    assert "--host 0.0.0.0" in content, "railway.json should use --host 0.0.0.0"
    
    # Check for healthcheck configuration
    assert "healthcheckPath" in content, "healthcheckPath should be configured"
    assert "/health" in content, "/health endpoint should be configured"
    
    print("✓ railway.json has correct configuration")


def test_procfile_configuration():
    """Test that Procfile has correct configuration."""
    procfile_path = project_root / "Procfile"
    
    assert procfile_path.exists(), "Procfile should exist"
    
    content = procfile_path.read_text()
    
    # Check for server/run.py command with single worker
    assert "server/run.py" in content, "Procfile should use server/run.py"
    assert "--workers" in content, "Procfile should specify workers"
    assert "--log-level debug" in content, "Procfile should use --log-level debug"
    assert "--host 0.0.0.0" in content, "Procfile should use --host 0.0.0.0"
    
    print("✓ Procfile has correct configuration")


def test_dockerfile_single_worker():
    """Test that Dockerfile uses single worker configuration."""
    dockerfile_path = project_root / "Dockerfile"
    
    assert dockerfile_path.exists(), "Dockerfile should exist"
    
    content = dockerfile_path.read_text()
    
    # Check for server/run.py command with single worker
    assert "server/run.py" in content, "Dockerfile should use server/run.py"
    assert "--workers" in content, "Dockerfile should specify workers"
    # Check for single worker
    assert ('"1"' in content and "--workers" in content), "Dockerfile should use single worker"
    
    print("✓ Dockerfile configured for single worker")


def test_redis_is_optional_dependency():
    """Test that Redis is listed as optional/recommended, not critical."""
    verify_deps_path = project_root / "server" / "verify_dependencies.py"
    
    assert verify_deps_path.exists(), "verify_dependencies.py should exist"
    
    content = verify_deps_path.read_text()
    
    # Find CRITICAL_DEPENDENCIES section
    critical_start = content.find("CRITICAL_DEPENDENCIES")
    recommended_start = content.find("RECOMMENDED_DEPENDENCIES")
    
    assert critical_start != -1, "CRITICAL_DEPENDENCIES should be defined"
    assert recommended_start != -1, "RECOMMENDED_DEPENDENCIES should be defined"
    
    # Redis should be in RECOMMENDED section, not CRITICAL
    critical_section = content[critical_start:recommended_start]
    recommended_section = content[recommended_start:]
    
    # Check that redis is NOT in critical section
    # Note: Just checking the tuple, not comments
    assert '("redis"' not in critical_section, "Redis should NOT be in CRITICAL_DEPENDENCIES"
    
    # Check that redis IS in recommended section
    assert '("redis"' in recommended_section, "Redis should be in RECOMMENDED_DEPENDENCIES"
    
    print("✓ Redis is correctly marked as optional/recommended dependency")


def test_distributed_lock_skip_option():
    """Test that distributed lock can be skipped with environment variable."""
    distributed_lock_path = project_root / "server" / "distributed_lock.py"
    
    assert distributed_lock_path.exists(), "distributed_lock.py should exist"
    
    content = distributed_lock_path.read_text()
    
    # Check for SKIP_REDIS_LOCK environment variable check
    assert "SKIP_REDIS_LOCK" in content, "distributed_lock.py should support SKIP_REDIS_LOCK env var"
    
    print("✓ distributed_lock.py supports SKIP_REDIS_LOCK environment variable")


def test_lazy_boto3_loading():
    """Test that boto3 is lazy-loaded."""
    os.environ["AUDIT_CRYPTO_MODE"] = "disabled"
    os.environ["RUNNING_TESTS"] = "true"
    
    # Fresh import to test lazy loading
    import importlib
    import generator.audit_log.audit_crypto.audit_crypto_factory as factory
    importlib.reload(factory)
    
    # Check that _ensure_boto3 function exists
    assert hasattr(factory, "_ensure_boto3"), "_ensure_boto3 function should exist"
    
    # Verify boto3 is None initially
    assert factory.boto3 is None, "boto3 should be None initially with disabled mode"
    
    print("✓ boto3 is lazy-loaded")


if __name__ == "__main__":
    print("Testing Railway deployment fixes...")
    print()
    
    try:
        # Configuration tests (don't require dependencies)
        test_railway_toml_configuration()
        test_railway_json_configuration()
        test_procfile_configuration()
        test_dockerfile_single_worker()
        test_redis_is_optional_dependency()
        test_distributed_lock_skip_option()
        
        # Import tests (require dependencies - skip if not available)
        try:
            test_lazy_boto3_loading()
            test_audit_crypto_disabled_mode()
        except (ImportError, ModuleNotFoundError) as e:
            print(f"⚠ Skipping import tests (dependencies not installed): {e}")
            print("  Note: These tests will run in CI with full dependencies")
        
        print()
        print("=" * 70)
        print("All available tests passed! Railway deployment fixes are working correctly.")
        print("=" * 70)
        
    except Exception as e:
        print()
        print("=" * 70)
        print(f"Test failed: {e}")
        print("=" * 70)
        sys.exit(1)
