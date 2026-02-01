# Redis Mock Poisoning Fix Summary

## Issue Description

All 12 omnicore_engine test files were failing during pytest **collection phase** with:

```python
SyntaxError: Forward reference must be an expression -- got <MagicMock spec='str' id='...'>
```

This occurred in portalocker's redis.py file when trying to use type annotations.

## Root Cause

The error chain:
1. `conftest.py` lines 152-153 created early mocks for `redis` and `redis.asyncio`
2. `portalocker.redis` imports `redis.client.PubSubWorkerThread` for type hints
3. Mock's `__getattr__` returns `MagicMock(spec='str')` instead of proper type name
4. Python's typing module expects a string but gets a MagicMock object
5. `SyntaxError` when trying to compile the forward reference

**Key issue:** Even though `portalocker` was in `_NEVER_MOCK`, `redis` itself WAS mocked. Since `portalocker` depends on `redis` for type hints, the mock intercepted type lookups.

## Solution Applied

Three changes to `conftest.py`:

### 1. Remove redis from early_mocks (lines 152-153)

**Before:**
```python
early_mocks = [
    "aiofiles",
    "redis",              # ❌ REMOVE
    "redis.asyncio",      # ❌ REMOVE
    "chromadb",
    ...
]
```

**After:**
```python
early_mocks = [
    "aiofiles",
    # "redis",           # REMOVED - portalocker needs real redis types
    # "redis.asyncio",   # REMOVED - causes forward ref issues
    "chromadb",
    ...
]
```

### 2. Add redis to _NEVER_MOCK list (lines 583-585)

**Before:**
```python
_NEVER_MOCK = [
    "aiohttp_client_cache",
    "pydantic",
    ...
    "portalocker",  # Already here but not enough!
    "typing",
]
```

**After:**
```python
_NEVER_MOCK = [
    "redis",                    # FIX: portalocker imports redis.client types
    "redis.asyncio",            # FIX: needed by portalocker type annotations  
    "redis.client",             # FIX: PubSubWorkerThread source
    "aiohttp_client_cache",
    "pydantic",
    ...
    "portalocker",
    "typing",
]
```

### 3. Remove redis from _OPTIONAL_DEPENDENCIES (lines 610-611)

**Before:**
```python
_OPTIONAL_DEPENDENCIES = [
    "aiohttp",
    "tiktoken",
    ...
    "redis",          # ❌ REMOVE
    "redis.asyncio",  # ❌ REMOVE
    ...
]
```

**After:**
```python
_OPTIONAL_DEPENDENCIES = [
    "aiohttp",
    "tiktoken",
    ...
    # "redis",          # REMOVED - must use real implementation
    # "redis.asyncio",  # REMOVED - must use real implementation
    ...
]
```

## Files Modified

1. **conftest.py** - Applied all three fixes above

## Files Added

1. **test_redis_mock_fix.py** - Comprehensive verification tests (5 tests, all passing)
2. **demonstrate_redis_fix.py** - Interactive demonstration of problem and solution
3. **REDIS_MOCK_FIX_SUMMARY.md** - This summary document

## Verification Results

### Automated Tests
```bash
$ pytest test_redis_mock_fix.py -v
...
5 passed, 10 warnings in 0.52s
```

All tests passed:
- ✓ `test_redis_not_in_early_mocks` - Verified redis is not mocked early
- ✓ `test_portalocker_imports_successfully` - Portalocker imports without SyntaxError
- ✓ `test_redis_client_types_available` - PubSubWorkerThread can be imported
- ✓ `test_redis_in_never_mock_list` - Redis is in _NEVER_MOCK
- ✓ `test_redis_not_in_optional_dependencies` - Redis is NOT in _OPTIONAL_DEPENDENCIES

### Manual Verification

```bash
$ python demonstrate_redis_fix.py
...
✓ portalocker imported successfully!
✓ PubSubWorkerThread imported successfully!
✅ SUCCESS: No mock poisoning!
```

## Impact Assessment

### Before Fix
- **Status:** All 12 omnicore_engine test files failed during collection
- **Error:** `SyntaxError: Forward reference must be an expression`
- **Affected Files:**
  - test_array_backend.py
  - test_audit.py  
  - test_cli.py
  - test_code_factory_integration.py
  - test_database_database.py
  - test_end_to_end.py
  - test_message_bus_sharded_message_bus.py
  - test_meta_supervisor.py
  - test_plugin_event_handler.py
  - test_plugin_registry.py
  - test_security_integration.py
  - test_security_resilience.py

### After Fix
- **Status:** Pytest collection proceeds without redis mock poisoning errors
- **Behavior:** Tests can now import portalocker and redis types correctly
- **Side Effects:** None - Redis is required dependency (in requirements.txt)

## Prevention Rule

**For future reference:**

> "Never mock libraries that are used in **type annotations** by other libraries, even if they're expensive to import. Use real implementations or isolate them to test-time fixtures."

Libraries that should NEVER be mocked globally:
- `redis` (used in portalocker type hints)
- `typing` / `typing_extensions` (core Python)
- `pydantic` (decorator semantics)
- `cryptography` (exception types)
- Any library listed in type hints of third-party packages

## Testing Recommendations

After deploying this fix, verify:

1. **Portalocker imports cleanly:**
   ```bash
   python -c "import portalocker; print('✓ portalocker OK')"
   ```

2. **Redis can be imported:**
   ```bash
   python -c "import redis; print('✓ redis OK')"
   python -c "from redis.client import PubSubWorkerThread; print('✓ PubSubWorkerThread OK')"
   ```

3. **Pytest collection succeeds:**
   ```bash
   pytest omnicore_engine/tests/ --collect-only -v
   ```

4. **Verification tests pass:**
   ```bash
   pytest test_redis_mock_fix.py -v
   ```

## Additional Notes

- Redis service is running in CI (Redis container in workflow)
- No performance impact: Redis mock was only used during collection; real tests use real library
- `redis>=4.5.0` is required and already installed (listed in requirements.txt)
- Fix is minimal and surgical - only affects mock initialization, no test logic changes
