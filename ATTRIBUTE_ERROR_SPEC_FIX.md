# Fix Summary: AttributeError __spec__ in pytest test collection

## Problem
Pytest test collection was failing with `AttributeError: __spec__` errors in multiple test files. The error occurred when pytest's import machinery tried to access `__spec__` on mock modules that were created with `__spec__ = None`.

## Root Cause
Test files were creating stub modules using `types.ModuleType()` and setting `__spec__ = None`, which breaks pytest's import system. When these stubs are registered in `sys.modules`, pytest's `importlib.util.find_spec()` function throws `ValueError: <module>.__spec__ is None` during collection.

## Solution
Replaced all occurrences of stub module creation that set `__spec__ = None` with proper `importlib.machinery.ModuleSpec` objects.

### Before (Broken):
```python
mod = ModuleType(name)
mod.__path__ = []
mod.__spec__ = None  # ❌ WRONG - causes ValueError in pytest
mod.__file__ = "<mocked>"
sys.modules[name] = mod
```

### After (Fixed):
```python
import importlib.machinery

mod = ModuleType(name)
mod.__path__ = []
mod.__spec__ = importlib.machinery.ModuleSpec(
    name=name,
    loader=None,
    is_package=True
)
mod.__file__ = f"<mocked {name}>"
sys.modules[name] = mod
```

## Files Modified

### 1. test_audit_log_audit_backend_streaming_utils.py
**Changes:**
- Removed problematic module-level imports: `import prometheus_client.core as _core` and `import prometheus_client.registry as _reg`
- Updated `fresh_prom_registry` fixture to patch modules via string paths instead of direct module references
- This prevents pytest from accessing potentially-mocked modules during collection

**Lines changed:** 22-23, 41-47

### 2. test_audit_log_audit_log.py
**Changes:**
- Added `import importlib.machinery` to imports
- Fixed stub module creation for parent packages (generator, generator.audit_log, generator.audit_log.audit_backend)
- Fixed `audit_backend_core` stub module
- Fixed `audit_crypto_pkg` stub module
- Fixed `audit_crypto_factory` stub module
- Fixed `audit_keystore` stub module

**Lines changed:** 21-28, 115-127, 129-166, 173-177, 201-210, 226-235

### 3. test_audit_log_audit_backend_cloud.py
**Changes:**
- Updated `stub_module()` function to create proper ModuleSpec for each level in the hierarchy
- Fixed package stub creation for `audit_log` and `audit_log.audit_backend`

**Lines changed:** 39-54, 254-265

### 4. test_audit_log_audit_backend_file_sql.py
**Changes:**
- Updated package shim section to create proper ModuleSpec for `audit_log` and `audit_log.audit_backend`

**Lines changed:** 45-63

## Verification

A verification script was created and run to confirm the fix:

1. **Test 1: Old method** - Confirmed that `__spec__ = None` causes `ValueError` when `importlib.util.find_spec()` is called
2. **Test 2: New method** - Confirmed that proper `ModuleSpec` allows `importlib.util.find_spec()` to work correctly
3. **Test 3: Hierarchical creation** - Confirmed that the hierarchical stub creation function works correctly with all levels having proper `__spec__`

**Result:** ✅ All tests passed

## Expected Outcome
After these fixes:
- All stub modules have proper `__spec__` attributes
- Pytest's import machinery can introspect modules during collection without errors
- Test collection completes successfully without `AttributeError: __spec__` or `ValueError: <module>.__spec__ is None`

## Impact
These changes are minimal and surgical:
- Only affects test file module stubs, not production code
- No changes to test logic or assertions
- Maintains backward compatibility with existing test structure
- Follows the pattern already established in the root `conftest.py` file

## Related Files
The root `conftest.py` already has a `_create_stub_module()` function that creates proper `ModuleSpec` objects. These fixes align the individual test files with the same best practice.
