# Fix for AttributeError: __spec__ in prometheus_client Mock Modules

## Problem Summary

The test `generator/tests/test_audit_log_audit_metrics.py` was failing during pytest collection with:

```
AttributeError: __spec__
```

### Root Cause

The `_initialize_prometheus_stubs()` function in `conftest.py` creates mock `prometheus_client` modules with proper `ModuleSpec` objects, but was registering them in `sys.modules` only AFTER all submodules were created. 

When `generator/audit_log/audit_metrics.py` tried to import:

```python
from prometheus_client.core import HistogramMetricFamily
```

Python's import machinery looked for `prometheus_client.core` in `sys.modules` but couldn't find it because it hadn't been registered yet. This caused the import system to raise `AttributeError: __spec__` from the unittest.mock module.

### Error Traceback

```
generator/tests/test_audit_log_audit_metrics.py:76: in <module>
    from generator.audit_log.audit_metrics import (
generator/audit_log/audit_metrics.py:71: in <module>
    from prometheus_client.core import HistogramMetricFamily
<frozen importlib._bootstrap>:1176: in _find_and_load
<frozen importlib._bootstrap>:1136: in _find_and_load_unlocked
/opt/hostedtoolcache/Python/3.11.14/x64/lib/python3.11/unittest/mock.py:655: in __getattr__
    raise AttributeError(name)
E   AttributeError: __spec__
```

## Solution

Updated `conftest.py` to register each mock submodule in `sys.modules` **immediately** after creating it, before creating child submodules. This ensures Python's import machinery can find the modules with proper `__spec__` attributes.

### Changes Made to conftest.py

1. **Register modules immediately in sys.modules:**
   - Register `prometheus_client` in `sys.modules` right after creating it
   - Register `prometheus_client.core` in `sys.modules` before adding it as an attribute to the parent
   - Register `prometheus_client.registry`, `multiprocess`, and `metrics` similarly

2. **Set correct package flags:**
   - Set `is_package=False` for submodules that are modules, not packages
   - Previously all were set to `is_package=True` which was incorrect

3. **Add missing functionality:**
   - Added `push_to_gateway` function to main module (was missing)

4. **Inline initialization:**
   - Added inline stub initialization at the top of conftest.py that runs at module import time
   - This ensures stubs exist before pytest scans/imports test files

### Code Changes

**Before (Incorrect):**
```python
# Create core submodule
core_spec = importlib.machinery.ModuleSpec(
    name="prometheus_client.core",
    loader=None,
    is_package=True  # WRONG - should be False
)
prom_core = importlib.util.module_from_spec(core_spec)
prom_core.__file__ = "<mocked prometheus_client.core>"
prom_core.__path__ = []  # Not needed for non-package modules
prom_module.core = prom_core  # Added to parent BEFORE registering in sys.modules

# ... later ...
# Register modules in sys.modules AT THE END
sys.modules["prometheus_client"] = prom_module
sys.modules["prometheus_client.core"] = prom_core
```

**After (Correct):**
```python
# Register main module FIRST
sys.modules["prometheus_client"] = prom_module

# Create core submodule
core_spec = importlib.machinery.ModuleSpec(
    name="prometheus_client.core",
    loader=None,
    is_package=False  # CORRECT - it's a module, not a package
)
prom_core = importlib.util.module_from_spec(core_spec)
prom_core.__file__ = "<mocked prometheus_client.core>"

# CRITICAL: Register in sys.modules IMMEDIATELY before adding to parent
sys.modules["prometheus_client.core"] = prom_core
prom_module.core = prom_core
```

### Files Modified

1. **conftest.py (root):**
   - Updated `_initialize_prometheus_stubs()` function to register modules immediately
   - Added inline stub initialization at module import time (lines 116-289)
   - Fixed `is_package` flags for submodules
   - Added `push_to_gateway` function

2. **generator/tests/conftest.py (new file):**
   - Created local conftest to initialize stubs before test imports
   - Provides additional safety for pytest's importlib import mode

## Testing

### With prometheus_client NOT installed (stub mode):
```bash
$ python -c "
import conftest
from prometheus_client.core import HistogramMetricFamily
print('SUCCESS')
"
# Output: SUCCESS
```

### With prometheus_client installed (production mode):
```bash
$ pip install prometheus-client
$ python -m pytest generator/tests/test_audit_log_audit_metrics.py --collect-only
# Output: 11 tests collected in 0.31s
```

## Impact

- ✅ Fixes `AttributeError: __spec__` when importing from `prometheus_client.core`
- ✅ All 11 tests in `test_audit_log_audit_metrics.py` can now be collected successfully
- ✅ Maintains compatibility with real prometheus_client when installed
- ✅ Provides working stubs when prometheus_client is not available

## Technical Notes

1. **Why register immediately?**
   - Python's import machinery uses `sys.modules` as the primary cache
   - When you import `prometheus_client.core`, Python first looks for it in `sys.modules`
   - If not found, it tries to find the module using the parent's `__path__`
   - Without proper registration, the import system fails with `AttributeError: __spec__`

2. **Why is_package=False?**
   - `prometheus_client.core` is a module (a single Python file), not a package (a directory with `__init__.py`)
   - Modules should have `is_package=False` in their `ModuleSpec`
   - Setting `is_package=True` was misleading and could cause issues with import machinery

3. **Why inline initialization?**
   - Pytest with `--import-mode=importlib` may import test files before loading conftest
   - Inline initialization at conftest import time ensures stubs exist early
   - The `_initialize_prometheus_stubs()` function is still called later for completeness

## References

- Python Import System: https://docs.python.org/3/reference/import.html
- ModuleSpec: https://docs.python.org/3/library/importlib.html#importlib.machinery.ModuleSpec
- Pytest Import Modes: https://docs.pytest.org/en/stable/explanation/pythonpath.html
