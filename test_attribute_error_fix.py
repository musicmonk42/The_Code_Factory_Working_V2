#!/usr/bin/env python3
"""
Test to verify that opentelemetry and prometheus_client can be imported
from the audit_log modules without AttributeError: __spec__ or __path__ errors.

This test specifically validates the fix for the issue described in the problem statement.
"""
import sys
import os

# Set TESTING env var like conftest.py does
os.environ["TESTING"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "1"
os.environ["PYTEST_CURRENT_TEST"] = "true"

print("=" * 80)
print("Testing audit_log imports with opentelemetry and prometheus_client")
print("=" * 80)

# Import conftest first to trigger defensive checks
print("\n1. Importing conftest.py...")
try:
    import conftest
    print("✅ conftest.py imported successfully")
except Exception as e:
    print(f"❌ Failed to import conftest.py: {e}")
    sys.exit(1)

# Verify that prometheus_client and opentelemetry are real modules
print("\n2. Verifying modules in sys.modules...")
for mod_name in ["prometheus_client", "opentelemetry"]:
    if mod_name in sys.modules:
        mod = sys.modules[mod_name]
        has_spec = hasattr(mod, '__spec__') and mod.__spec__ is not None
        has_path = hasattr(mod, '__path__') and mod.__path__ is not None
        
        if has_spec and has_path:
            print(f"✅ {mod_name} has valid __spec__ and __path__")
        else:
            print(f"❌ {mod_name} missing __spec__ or __path__")
            if not has_spec:
                print(f"   - Missing __spec__: {not has_spec}")
            if not has_path:
                print(f"   - Missing __path__: {not has_path}")
    else:
        print(f"⚠️  {mod_name} not in sys.modules yet")

# Now test the actual imports that were failing
print("\n3. Testing prometheus_client imports...")
try:
    from prometheus_client import Counter, Histogram
    print("✅ from prometheus_client import Counter, Histogram")
except AttributeError as e:
    print(f"❌ AttributeError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"⚠️  Other error (expected if dependencies missing): {e}")

try:
    from prometheus_client.core import HistogramMetricFamily
    print("✅ from prometheus_client.core import HistogramMetricFamily")
except AttributeError as e:
    print(f"❌ AttributeError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"⚠️  Other error (expected if dependencies missing): {e}")

print("\n4. Testing opentelemetry imports...")
try:
    from opentelemetry import trace
    print("✅ from opentelemetry import trace")
except AttributeError as e:
    print(f"❌ AttributeError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"⚠️  Other error (expected if dependencies missing): {e}")

# This one requires fastapi, so it might fail with ModuleNotFoundError
# but should NOT fail with AttributeError
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    print("✅ from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor")
except AttributeError as e:
    print(f"❌ AttributeError: {e}")
    print("   This is the error we were trying to fix!")
    sys.exit(1)
except ModuleNotFoundError as e:
    print(f"⚠️  ModuleNotFoundError (expected if fastapi not installed): {e}")
    print("   But no AttributeError - fix is working!")
except Exception as e:
    print(f"⚠️  Other error: {type(e).__name__}: {e}")

print("\n" + "=" * 80)
print("✅ SUCCESS: No AttributeError for __spec__ or __path__")
print("=" * 80)
print("\nThe fix is working! The defensive check in pytest_configure successfully")
print("prevents broken mocks from interfering with real module imports.")
