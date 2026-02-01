#!/usr/bin/env python3
"""
Test script to validate that opentelemetry and prometheus_client
can be imported correctly without AttributeError issues.
"""
import sys
import os

# Set TESTING env var like conftest.py does
os.environ["TESTING"] = "1"

def test_opentelemetry_import():
    """Test that opentelemetry imports with proper __spec__ and __path__."""
    try:
        import opentelemetry
        
        # Check for __spec__
        assert hasattr(opentelemetry, '__spec__'), "opentelemetry is missing __spec__"
        assert opentelemetry.__spec__ is not None, "opentelemetry.__spec__ is None"
        
        # Check for __path__
        assert hasattr(opentelemetry, '__path__'), "opentelemetry is missing __path__"
        assert opentelemetry.__path__ is not None, "opentelemetry.__path__ is None"
        
        print("✅ opentelemetry imports correctly with __spec__ and __path__")
        print(f"   __spec__: {opentelemetry.__spec__}")
        print(f"   __path__: {opentelemetry.__path__}")
        return True
    except Exception as e:
        print(f"❌ opentelemetry import failed: {e}")
        return False


def test_prometheus_client_import():
    """Test that prometheus_client imports with proper __spec__ and __path__."""
    try:
        import prometheus_client
        
        # Check for __spec__
        assert hasattr(prometheus_client, '__spec__'), "prometheus_client is missing __spec__"
        assert prometheus_client.__spec__ is not None, "prometheus_client.__spec__ is None"
        
        # Check for __path__
        assert hasattr(prometheus_client, '__path__'), "prometheus_client is missing __path__"
        assert prometheus_client.__path__ is not None, "prometheus_client.__path__ is None"
        
        print("✅ prometheus_client imports correctly with __spec__ and __path__")
        print(f"   __spec__: {prometheus_client.__spec__}")
        print(f"   __path__: {prometheus_client.__path__}")
        return True
    except Exception as e:
        print(f"❌ prometheus_client import failed: {e}")
        return False


def test_prometheus_client_core_import():
    """Test that prometheus_client.core.HistogramMetricFamily imports correctly."""
    try:
        from prometheus_client.core import HistogramMetricFamily
        
        assert HistogramMetricFamily is not None, "HistogramMetricFamily is None"
        print("✅ prometheus_client.core.HistogramMetricFamily imports correctly")
        print(f"   HistogramMetricFamily: {HistogramMetricFamily}")
        return True
    except Exception as e:
        print(f"❌ prometheus_client.core import failed: {e}")
        return False


def test_conftest_imports():
    """Test that conftest.py can be imported without errors."""
    try:
        # This will trigger the early_mocks creation and validation
        import conftest
        
        print("✅ conftest.py imports successfully")
        
        # Now check if our defensive code removed any broken mocks
        if "prometheus_client" in sys.modules:
            mod = sys.modules["prometheus_client"]
            if hasattr(mod, '__spec__') and mod.__spec__ is not None:
                print("✅ prometheus_client is a valid real module in sys.modules")
            else:
                print("⚠️  prometheus_client in sys.modules but missing __spec__")
        
        if "opentelemetry" in sys.modules:
            mod = sys.modules["opentelemetry"]
            if hasattr(mod, '__spec__') and mod.__spec__ is not None:
                print("✅ opentelemetry is a valid real module in sys.modules")
            else:
                print("⚠️  opentelemetry in sys.modules but missing __spec__")
        
        return True
    except Exception as e:
        print(f"❌ conftest.py import failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Testing opentelemetry and prometheus_client imports")
    print("=" * 60)
    
    results = []
    
    # Test basic imports first (before conftest)
    print("\n1. Testing basic imports (before conftest):")
    results.append(test_opentelemetry_import())
    results.append(test_prometheus_client_import())
    results.append(test_prometheus_client_core_import())
    
    # Test with conftest
    print("\n2. Testing with conftest.py:")
    results.append(test_conftest_imports())
    
    print("\n" + "=" * 60)
    if all(results):
        print("✅ All tests passed!")
        sys.exit(0)
    else:
        print("❌ Some tests failed!")
        sys.exit(1)
