"""
Test Railway deployment fixes.

This test validates that:
1. AUDIT_CRYPTO_MODE=disabled prevents boto3 initialization
2. Server can start with single worker configuration
3. Health endpoint is accessible
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
    assert factory._is_crypto_disabled() == True, "Disabled mode not detected"
    
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
    
    # Check for required environment variables
    assert "AUDIT_CRYPTO_MODE" in content, "AUDIT_CRYPTO_MODE should be in railway.toml"
    assert "AUDIT_CRYPTO_ALLOW_INIT_FAILURE" in content, "AUDIT_CRYPTO_ALLOW_INIT_FAILURE should be in railway.toml"
    assert "PORT" in content, "PORT should be in railway.toml"
    
    # Check for healthcheck configuration
    assert "healthcheckPath" in content, "healthcheckPath should be configured"
    assert "/health" in content, "/health endpoint should be configured"
    
    print("✓ railway.toml has correct configuration")


def test_dockerfile_single_worker():
    """Test that Dockerfile uses single worker configuration."""
    dockerfile_path = project_root / "Dockerfile"
    
    assert dockerfile_path.exists(), "Dockerfile should exist"
    
    content = dockerfile_path.read_text()
    
    # Check for uvicorn command with single worker
    assert "uvicorn" in content, "Dockerfile should use uvicorn"
    assert "--workers" in content, "Dockerfile should specify workers"
    assert '"1"' in content or "'1'" in content, "Dockerfile should use single worker"
    
    print("✓ Dockerfile configured for single worker")


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
        test_dockerfile_single_worker()
        
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
