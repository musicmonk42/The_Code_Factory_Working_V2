#!/usr/bin/env python3
"""
Integration test for Docker backend lazy initialization.
This test validates that the server can start quickly without Docker.
"""

import os
import sys
import time
import subprocess

print("=" * 70)
print("Docker Backend Lazy Initialization - Startup Test")
print("=" * 70)

# Test 1: Check that Python can import the module quickly
print("\n[Test 1] Module import speed test...")
start = time.time()

try:
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import os
os.environ['APP_STARTUP'] = '1'
sys.path.insert(0, '.')

# Import the backends module
from generator.runner import runner_backends

# Check docker backend is registered
if 'docker' not in runner_backends.BACKEND_REGISTRY:
    print('ERROR: Docker backend not registered')
    sys.exit(1)

print('SUCCESS: Module imported and Docker backend registered')
"""],
        cwd="/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2",
        capture_output=True,
        text=True,
        timeout=10
    )
    
    elapsed = time.time() - start
    
    if result.returncode == 0 and "SUCCESS" in result.stdout:
        print(f"  ✓ Module imported in {elapsed:.2f}s")
        print(f"  ✓ Docker backend registered")
        if elapsed < 5.0:
            print(f"  ✓ Import was fast (<5s)")
        else:
            print(f"  ⚠ Import took {elapsed:.2f}s (expected <5s)")
    else:
        print(f"  ✗ Test failed:")
        print(f"    stdout: {result.stdout}")
        print(f"    stderr: {result.stderr}")
        sys.exit(1)
        
except subprocess.TimeoutExpired:
    print(f"  ✗ Import timed out (>10s) - likely blocking!")
    sys.exit(1)
except Exception as e:
    print(f"  ✗ Test failed: {e}")
    sys.exit(1)

# Test 2: Verify the startup doesn't hang
print("\n[Test 2] Server startup simulation...")
start = time.time()

try:
    # This simulates what happens during server startup
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import os
import asyncio

# Set environment
os.environ['APP_STARTUP'] = '1'
os.environ['APP_ENV'] = 'production'
sys.path.insert(0, '.')

# Import and instantiate backend
from generator.runner.runner_backends import DockerBackend
from generator.runner.runner_config import RunnerConfig

# This should complete quickly
config = RunnerConfig()
backend = DockerBackend(config)

# Check initialization state
if not hasattr(backend, '_initialized'):
    print('ERROR: Missing _initialized attribute')
    sys.exit(1)

if backend._initialized:
    print('ERROR: Backend initialized during __init__ (should be lazy)')
    sys.exit(1)

# Check health (should not block)
health = backend.health()
status = health.get('status')

if status not in ['not_initialized', 'unavailable']:
    print(f'ERROR: Unexpected health status: {status}')
    sys.exit(1)

print(f'SUCCESS: Backend initialized lazily, health status: {status}')
"""],
        cwd="/home/runner/work/The_Code_Factory_Working_V2/The_Code_Factory_Working_V2",
        capture_output=True,
        text=True,
        timeout=10
    )
    
    elapsed = time.time() - start
    
    if result.returncode == 0 and "SUCCESS" in result.stdout:
        print(f"  ✓ Backend initialized in {elapsed:.2f}s")
        print(f"  ✓ Lazy initialization working")
        if "health status: not_initialized" in result.stdout:
            print(f"  ✓ Health returns 'not_initialized' (correct)")
        elif "health status: unavailable" in result.stdout:
            print(f"  ✓ Health returns 'unavailable' (Docker lib not installed)")
        
        if elapsed < 5.0:
            print(f"  ✓ Initialization was fast (<5s)")
        else:
            print(f"  ⚠ Initialization took {elapsed:.2f}s (expected <5s)")
    else:
        print(f"  ✗ Test failed:")
        print(f"    stdout: {result.stdout}")
        print(f"    stderr: {result.stderr}")
        sys.exit(1)
        
except subprocess.TimeoutExpired:
    print(f"  ✗ Backend initialization timed out (>10s) - blocking detected!")
    sys.exit(1)
except Exception as e:
    print(f"  ✗ Test failed: {e}")
    sys.exit(1)

print("\n" + "=" * 70)
print("✓ All tests passed!")
print("=" * 70)
print("\nConclusion:")
print("  • Docker backend no longer blocks during startup")
print("  • Health checks return immediately")
print("  • Backend will initialize on first use (lazy loading)")
print("  • Server should start quickly on Railway")
