# OpenTelemetry Tracing Fix - Summary

## Problem Statement

The `_run_codegen` method in `server/services/omnicore_service.py` had incorrect usage of the OpenTelemetry tracing span context manager, causing `AttributeError` when jobs were run:

```
AttributeError: '_AgnosticContextManager' object has no attribute 'set_attribute'
AttributeError: '_AgnosticContextManager' object has no attribute 'set_status'
```

## Root Cause

The code was creating a span context manager but not properly capturing the span object returned by `__enter__()`. The context manager itself was being used instead of the actual span object.

### Before (Incorrect):

```python
# Line 854 - Creating context manager but not entering it properly
span_context = tracer.start_as_current_span("codegen_execution") if TRACING_AVAILABLE else None

try:
    # ... code ...
    
    # Lines 880-886 - Manually calling __enter__ but not capturing the returned span
    if TRACING_AVAILABLE and span_context:
        span_context.__enter__()  # Returns the span but we don't capture it!
        span_context.set_attribute("job.id", job_id)  # ❌ WRONG: using context manager, not span
        span_context.set_attribute("job.language", language)
        # ... more attributes ...

except Exception as e:
    # Lines 1191-1197 - Using context manager instead of span
    if TRACING_AVAILABLE and span_context:
        span_context.set_status(Status(StatusCode.ERROR, str(e)))  # ❌ WRONG
        span_context.record_exception(e)  # ❌ WRONG
        span_context.__exit__(type(e), e, e.__traceback__)  # Manual exit
```

## Solution Implemented

Replaced the manual `__enter__()` and `__exit__()` calls with a proper `with` statement that automatically handles the context manager and provides access to the span object.

### After (Correct):

```python
# Helper function containing all the codegen logic
async def _execute_codegen(span=None):
    try:
        # ... code ...
        
        # Lines 878-883 - Now using the span parameter
        if span:
            span.set_attribute("job.id", job_id)  # ✅ CORRECT: using span object
            span.set_attribute("job.language", language)
            # ... more attributes ...
        
        # ... more code ...
        
        # Success case
        if span:
            span.set_attribute("files.generated", len(generated_files))
            span.set_status(Status(StatusCode.OK))  # ✅ CORRECT
        
        return result_dict
        
    except Exception as e:
        # Error handling
        if span:
            span.set_status(Status(StatusCode.ERROR, str(e)))  # ✅ CORRECT
            span.record_exception(e)  # ✅ CORRECT
        
        return error_dict

# Execute with or without tracing
if TRACING_AVAILABLE:
    with tracer.start_as_current_span("codegen_execution") as span:
        return await _execute_codegen(span)  # ✅ Passing the span object
else:
    return await _execute_codegen()  # ✅ No tracing
```

## Key Changes

1. ✅ **Created helper function** `_execute_codegen(span=None)` to contain all the try/except logic
2. ✅ **Removed manual `__enter__()` calls** - The `with` statement handles this automatically
3. ✅ **Removed manual `__exit__()` calls** - The `with` statement handles this automatically
4. ✅ **Changed `span_context.set_attribute()`** → `span.set_attribute()`
5. ✅ **Changed `span_context.set_status()`** → `span.set_status()`
6. ✅ **Changed `span_context.record_exception()`** → `span.record_exception()`
7. ✅ **Proper conditional execution** - Separate code paths for traced and non-traced execution

## Benefits

- **No more AttributeError** - The span object is now properly used
- **Cleaner code** - The `with` statement is the Pythonic way to use context managers
- **Automatic cleanup** - The context manager ensures proper span closure even on exceptions
- **Better maintainability** - The helper function pattern makes the code easier to understand
- **Conditional tracing** - Gracefully handles both traced and non-traced execution

## Testing

Created comprehensive validation test (`test_tracing_fix.py`) that verifies:

1. ✅ No manual `__enter__()` calls remain
2. ✅ No manual `__exit__()` calls remain
3. ✅ Proper `with tracer.start_as_current_span()` statement exists
4. ✅ Span variable is correctly captured from context manager
5. ✅ Span variable is used for `set_attribute()` calls
6. ✅ Span variable is used for `set_status()` calls
7. ✅ No old `span_context.set_attribute` patterns remain
8. ✅ No old `span_context.set_status` patterns remain
9. ✅ Helper function `_execute_codegen` exists
10. ✅ Conditional execution based on `TRACING_AVAILABLE`
11. ✅ Both traced and non-traced execution paths exist

All 11 checks passed! ✓

## Files Modified

- `server/services/omnicore_service.py` - Fixed `_run_codegen` method (lines 782-1204)
- `test_tracing_fix.py` - Added comprehensive validation test

## Verification

```bash
# Python syntax validation
$ python -m py_compile server/services/omnicore_service.py
✓ No syntax errors

# Validation test
$ python test_tracing_fix.py
✓ All 11 checks passed

# Linter
$ ruff check server/services/omnicore_service.py
✓ No errors related to our changes
```
