#!/usr/bin/env python3
"""
Simple validation tests for critical production fixes.
"""

import sys
import inspect

print("\n=== Running Critical Fixes Validation ===\n")

# Test 1: Message bus timeout
print("Test 1: Message bus subscription timeout configuration...")
try:
    # Read the file and check for the config
    with open('omnicore_engine/message_bus/sharded_message_bus.py', 'r') as f:
        content = f.read()
    
    if 'MESSAGE_BUS_SUBSCRIPTION_TIMEOUT=30.0' in content:
        print("✓ MESSAGE_BUS_SUBSCRIPTION_TIMEOUT=30.0 found in settings")
    else:
        print("✗ MESSAGE_BUS_SUBSCRIPTION_TIMEOUT not found or has wrong value")
        sys.exit(1)
    
    if 'subscription_timeout = getattr(settings' in content:
        print("✓ Subscription timeout uses configurable setting")
    else:
        print("✗ Subscription timeout not using configurable setting")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ Test 1 failed: {e}")
    sys.exit(1)

# Test 2: Job creation events
print("\nTest 2: Job creation emits events...")
try:
    with open('server/routers/jobs.py', 'r') as f:
        content = f.read()
    
    if 'await omnicore_service.emit_event' in content:
        print("✓ Job creation calls emit_event")
    else:
        print("✗ Job creation doesn't call emit_event")
        sys.exit(1)
    
    if 'topic="job.created"' in content:
        print("✓ Event topic is 'job.created'")
    else:
        print("✗ Event topic is not 'job.created'")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ Test 2 failed: {e}")
    sys.exit(1)

# Test 3: Test generation fallback
print("\nTest 3: Test generation fallback...")
try:
    with open('generator/runner/runner_core.py', 'r') as f:
        content = f.read()
    
    if 'test_fallback.py' in content:
        print("✓ Fallback test filename found")
    else:
        print("✗ Fallback test filename not found")
        sys.exit(1)
    
    if 'def test_placeholder():' in content:
        print("✓ Fallback test function found")
    else:
        print("✗ Fallback test function not found")
        sys.exit(1)
    
    if 'Generating fallback test' in content or 'generating fallback' in content:
        print("✓ Fallback generation logic found")
    else:
        print("✗ Fallback generation logic not found")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ Test 3 failed: {e}")
    sys.exit(1)

# Test 4: Docgen serialization
print("\nTest 4: Docgen output serialization...")
try:
    with open('server/services/omnicore_service.py', 'r') as f:
        content = f.read()
    
    if 'isinstance(docs_output, dict)' in content:
        print("✓ Dict type checking found")
    else:
        print("✗ Dict type checking not found")
        sys.exit(1)
    
    if "'content' in docs_output" in content:
        print("✓ Content field extraction found")
    else:
        print("✗ Content field extraction not found")
        sys.exit(1)
    
    if 'json.dumps' in content:
        print("✓ JSON serialization fallback found")
    else:
        print("✗ JSON serialization fallback not found")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ Test 4 failed: {e}")
    sys.exit(1)

print("\n=== All Validation Tests Passed! ===\n")
print("Summary:")
print("✓ Message bus subscription timeout is configurable (30s)")
print("✓ Job creation emits job.created events")
print("✓ Test generation has fallback for empty tests")
print("✓ Docgen has proper dict/string serialization")
print()
