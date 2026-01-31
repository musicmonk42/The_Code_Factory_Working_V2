#!/usr/bin/env python3
"""
Demonstration of the OpenTelemetry tracing bug and fix.

This script shows:
1. The original buggy pattern that causes AttributeError
2. The correct pattern that works properly
"""


class MockSpan:
    """Mock span object to demonstrate the fix."""
    def __init__(self, name):
        self.name = name
        self.attributes = {}
        self.status = None
        
    def set_attribute(self, key, value):
        self.attributes[key] = value
        print(f"  ✓ Span.set_attribute('{key}', '{value}')")
        
    def set_status(self, status):
        self.status = status
        print(f"  ✓ Span.set_status({status})")
        
    def __enter__(self):
        print(f"  → Context manager __enter__() called, returning span object")
        return self
        
    def __exit__(self, *args):
        print(f"  → Context manager __exit__() called")
        return False


class MockTracer:
    """Mock tracer to demonstrate the fix."""
    def start_as_current_span(self, name):
        print(f"\n→ Creating span context manager: '{name}'")
        return MockSpan(name)


def demonstrate_bug():
    """Show the original buggy pattern."""
    print("=" * 70)
    print("BUGGY PATTERN (Original Code)")
    print("=" * 70)
    
    tracer = MockTracer()
    
    # This is what the original code did
    print("\n1. Create context manager but don't enter it properly:")
    span_context = tracer.start_as_current_span("codegen_execution")
    print(f"   span_context = {type(span_context).__name__} object")
    
    print("\n2. Manually call __enter__() but don't capture the return value:")
    span_context.__enter__()  # Returns the span but we don't capture it!
    
    print("\n3. Try to use the context manager as if it were a span:")
    try:
        span_context.set_attribute("job.id", "test-123")  # This works by accident!
        print("   ⚠️  This works because MockSpan is both the context manager AND the span")
        print("   ⚠️  In real OpenTelemetry, the context manager is a DIFFERENT object!")
    except AttributeError as e:
        print(f"   ❌ ERROR: {e}")
    
    print("\n4. Manually call __exit__():")
    span_context.__exit__(None, None, None)
    
    print("\n⚠️  PROBLEM: In real OpenTelemetry, tracer.start_as_current_span() returns")
    print("    an '_AgnosticContextManager' object, NOT the span itself!")
    print("    So span_context.set_attribute() fails with AttributeError!")


def demonstrate_fix():
    """Show the correct pattern."""
    print("\n\n" + "=" * 70)
    print("CORRECT PATTERN (Fixed Code)")
    print("=" * 70)
    
    tracer = MockTracer()
    
    print("\n1. Use 'with' statement to properly manage the context:")
    print("   with tracer.start_as_current_span('codegen_execution') as span:")
    
    with tracer.start_as_current_span("codegen_execution") as span:
        print(f"\n2. The 'as span' clause captures the return value from __enter__():")
        print(f"   span = {type(span).__name__} object")
        
        print("\n3. Use the span object directly:")
        span.set_attribute("job.id", "test-123")
        span.set_attribute("job.language", "python")
        span.set_status("OK")
        
        print("\n4. The 'with' statement automatically calls __exit__() when done")
    
    print("\n✅ SOLUTION: The 'with' statement:")
    print("   - Automatically calls __enter__() and captures the return value")
    print("   - Provides the span object for us to use")
    print("   - Automatically calls __exit__() even if exceptions occur")
    print("   - This is the Pythonic way to use context managers!")


def show_comparison():
    """Show side-by-side comparison."""
    print("\n\n" + "=" * 70)
    print("SIDE-BY-SIDE COMPARISON")
    print("=" * 70)
    
    print("\nBUGGY CODE:")
    print("-" * 70)
    print("""
    # Create context manager
    span_context = tracer.start_as_current_span("codegen") if TRACING else None
    
    try:
        # Manually enter (but don't capture return value!)
        if span_context:
            span_context.__enter__()
            span_context.set_attribute("key", "value")  # ❌ AttributeError!
        
        # ... do work ...
        
    except Exception as e:
        if span_context:
            span_context.set_status("ERROR")  # ❌ AttributeError!
            span_context.__exit__(type(e), e, e.__traceback__)
    else:
        if span_context:
            span_context.__exit__(None, None, None)
    """)
    
    print("\nFIXED CODE:")
    print("-" * 70)
    print("""
    # Execute with or without tracing
    if TRACING_AVAILABLE:
        with tracer.start_as_current_span("codegen") as span:
            # Use span object directly ✅
            span.set_attribute("key", "value")
            # ... do work ...
            # span automatically closed by 'with' statement
    else:
        # ... do work without tracing ...
    """)


if __name__ == "__main__":
    demonstrate_bug()
    demonstrate_fix()
    show_comparison()
    
    print("\n\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print("\nThe fix changes:")
    print("  ❌ Manual __enter__() and __exit__() calls")
    print("  ❌ Using context manager object as if it were a span")
    print("\nTo:")
    print("  ✅ Proper 'with' statement")
    print("  ✅ Using the actual span object returned by __enter__()")
    print("\nThis eliminates the AttributeError and follows Python best practices!")
    print("=" * 70)
