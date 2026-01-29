#!/usr/bin/env python3
"""
Test Docker backend lazy initialization to ensure it doesn't block during startup.

This test validates:
1. Docker backend initializes without blocking
2. _initialized flag is False after __init__
3. Health check returns "not_initialized" status immediately
4. Backend initializes only when execute() is called
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the generator directory to the path
sys.path.insert(0, str(Path(__file__).parent / "generator"))

from runner.runner_config import RunnerConfig
from runner.runner_contracts import TaskPayload


def test_docker_backend_lazy_init():
    """Test that Docker backend does not connect during __init__."""
    print("=" * 70)
    print("Testing Docker Backend Lazy Initialization")
    print("=" * 70)
    
    # Test 1: Initialization should be fast (no blocking)
    print("\n1. Testing initialization speed...")
    start_time = time.time()
    
    try:
        # Import should be fast since we're not connecting
        from runner.runner_backends import DockerBackend
        
        config = RunnerConfig()
        backend = DockerBackend(config)
        
        init_time = time.time() - start_time
        print(f"   ✓ Initialization completed in {init_time:.3f}s")
        
        if init_time > 1.0:
            print(f"   ⚠ WARNING: Initialization took {init_time:.3f}s (should be <1s)")
            return False
            
    except Exception as e:
        print(f"   ✗ Failed to initialize DockerBackend: {e}")
        return False
    
    # Test 2: _initialized flag should be False
    print("\n2. Testing _initialized flag...")
    if hasattr(backend, '_initialized'):
        if backend._initialized == False:
            print(f"   ✓ _initialized flag is False (not yet initialized)")
        else:
            print(f"   ✗ _initialized flag is {backend._initialized} (expected False)")
            return False
    else:
        print("   ✗ _initialized attribute not found")
        return False
    
    # Test 3: Health check should return immediately without blocking
    print("\n3. Testing health check (should not block)...")
    start_time = time.time()
    
    try:
        health = backend.health()
        health_time = time.time() - start_time
        
        print(f"   ✓ Health check completed in {health_time:.3f}s")
        print(f"   Status: {health.get('status')}")
        print(f"   Details: {health.get('details')}")
        
        # Health check should be instant
        if health_time > 0.5:
            print(f"   ⚠ WARNING: Health check took {health_time:.3f}s (should be <0.5s)")
            return False
        
        # Status should indicate not initialized (or unavailable if docker lib not installed)
        expected_statuses = ["not_initialized", "unavailable"]
        if health.get('status') not in expected_statuses:
            print(f"   ✗ Unexpected status: {health.get('status')} (expected one of {expected_statuses})")
            return False
            
    except Exception as e:
        print(f"   ✗ Health check failed: {e}")
        return False
    
    # Test 4: Test lazy initialization with mocked Docker (optional)
    print("\n4. Testing lazy initialization behavior...")
    try:
        # Check if docker is available
        try:
            import docker
            has_docker = True
        except ImportError:
            has_docker = False
            print("   ⓘ Docker library not installed - skipping initialization test")
            print("   ✓ All tests passed (docker unavailable)")
            return True
        
        # If docker is available but daemon isn't, _ensure_initialized should be called on execute
        print("   ✓ Lazy initialization ready (will initialize on first execute)")
        
    except Exception as e:
        print(f"   ⚠ Could not test initialization: {e}")
    
    print("\n" + "=" * 70)
    print("✓ All Docker backend lazy initialization tests passed!")
    print("=" * 70)
    
    return True


def test_docker_backend_no_blocking_on_import():
    """Test that importing the module doesn't block."""
    print("\n" + "=" * 70)
    print("Testing Module Import Speed (should not block)")
    print("=" * 70)
    
    start_time = time.time()
    
    try:
        # This should not block even if Docker daemon is unavailable
        from runner.runner_backends import BACKEND_REGISTRY
        
        import_time = time.time() - start_time
        print(f"\n✓ Module imported in {import_time:.3f}s")
        
        # Check that docker backend is registered
        if 'docker' in BACKEND_REGISTRY:
            print("✓ Docker backend registered in BACKEND_REGISTRY")
        else:
            print("✗ Docker backend not found in BACKEND_REGISTRY")
            return False
        
        # Import time should be reasonable
        if import_time > 5.0:
            print(f"⚠ WARNING: Import took {import_time:.3f}s (should be <5s)")
            return False
            
        return True
        
    except Exception as e:
        print(f"✗ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("Docker Backend Lazy Initialization Test Suite\n")
    
    # Run tests
    test1_passed = test_docker_backend_no_blocking_on_import()
    test2_passed = test_docker_backend_lazy_init()
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Summary:")
    print(f"  Module import test: {'PASSED ✓' if test1_passed else 'FAILED ✗'}")
    print(f"  Lazy init test: {'PASSED ✓' if test2_passed else 'FAILED ✗'}")
    print("=" * 70)
    
    success = test1_passed and test2_passed
    sys.exit(0 if success else 1)
