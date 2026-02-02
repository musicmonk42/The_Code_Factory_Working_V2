# Circular Import Fix - Summary

## Problem Statement
The application logs showed circular import errors when loading `critique` and `docgen` agents:
```
cannot import name 'log_audit_event' from partially initialized module 'runner.runner_logging'
(most likely due to a circular import)
```

## Root Cause
1. **OmniCoreService** called `self._load_agents()` immediately during `__init__()`
2. **generator/agents/__init__.py** loaded all agents at module import time
3. This triggered imports of agent modules which depend on `runner.runner_audit` and `runner.runner_logging`
4. But the `runner` package initialization hadn't finished, causing the circular import

## Solution Implemented

### 1. Lazy Loading in `server/services/omnicore_service.py`
- ✅ Removed immediate `_load_agents()` call from `__init__`
- ✅ Added `_agents_loaded` flag to track loading state
- ✅ Created `_ensure_agents_loaded()` method for on-demand loading
- ✅ Updated all agent methods (`_run_codegen`, `_run_testgen`, etc.) to call `_ensure_agents_loaded()` before use

### 2. Lazy Loading in `generator/agents/__init__.py`
- ✅ Added `_AGENTS_LOADED` flag at module level
- ✅ Created `_load_all_agents()` function to load agents on-demand
- ✅ Removed immediate agent loading at module import time
- ✅ Updated public API functions (`get_available_agents()`, `is_agent_available()`, etc.) to trigger lazy loading
- ✅ Deferred strict mode validation until agents are actually accessed

### 3. Added Tests
- ✅ Created `server/tests/test_lazy_agent_loading.py` with comprehensive tests

## Verification Results

### Before Fix
```
[err] cannot import name 'log_audit_event' from partially initialized module 'runner.runner_logging'
[err] Agent 'critique' failed to load... circular import
[err] Agent 'docgen' failed to load... circular import
```

### After Fix
```
✓ OmniCore initialized - agents will be loaded on demand
✓ generator.agents imported (agents loaded: False)
✓ Service created (_agents_loaded: False)
✓ Loading agents on demand...
✓ Agents loaded successfully
```

**Key Improvements:**
- ✅ No "partially initialized module" errors
- ✅ No circular import errors
- ✅ Agents load only when needed (first job request)
- ✅ Application starts successfully
- ✅ All agent methods work correctly

## Expected Production Behavior

### Application Startup
```
2026-02-02 15:43:36 INFO OmniCoreService initializing...
2026-02-02 15:43:36 INFO OmniCore initialized - agents will be loaded on demand
2026-02-02 15:43:36 INFO Server started successfully
```

### First Job Request
```
2026-02-02 15:44:12 INFO Loading agents on demand...
2026-02-02 15:44:13 INFO ✓ Codegen agent loaded successfully
2026-02-02 15:44:13 INFO ✓ Testgen agent loaded successfully
2026-02-02 15:44:13 INFO ✓ Deploy agent loaded successfully
2026-02-02 15:44:13 INFO ✓ Docgen agent loaded successfully
2026-02-02 15:44:13 INFO ✓ Critique agent loaded successfully
2026-02-02 15:44:13 INFO Agents loaded. Available: codegen, testgen, deploy, docgen, critique
```

## Files Modified

1. **server/services/omnicore_service.py** (47 lines changed)
   - Modified `__init__()` to defer agent loading
   - Added `_ensure_agents_loaded()` method
   - Updated 7 agent methods to use lazy loading

2. **generator/agents/__init__.py** (207 lines changed)
   - Added lazy loading infrastructure
   - Created `_load_all_agents()` function
   - Updated API functions to trigger loading

3. **server/tests/test_lazy_agent_loading.py** (new file, 201 lines)
   - Comprehensive test suite for lazy loading

## Testing

Run validation:
```bash
python3 validate_circular_import_fix.py
```

Expected output:
```
✅ ALL TESTS PASSED
Circular import issues have been successfully resolved!
```

## Notes

- The fix ensures backward compatibility - all existing code continues to work
- Agents are loaded exactly once, on first access
- The solution follows the same pattern in both `omnicore_service.py` and `generator/agents/__init__.py`
- Strict mode validation is deferred until agents are accessed, preventing startup failures
