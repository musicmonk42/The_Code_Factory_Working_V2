# Pytest Collection Timeout Fix - Summary

## Problem Statement

The pytest job was failing due to test collection timing out after 120 seconds. This was caused by expensive imports and setup code being executed at the module level of test files and conftest.py files, which blocks the test collection phase.

## Root Causes Identified

1. **Temporary Directory Creation at Module Level**: Multiple conftest.py files were calling `tempfile.mkdtemp()` at module level, which blocks during collection
2. **OpenTelemetry Setup During Import**: Heavy OpenTelemetry mocking and context setup was happening during conftest import
3. **Prometheus Import Errors**: prometheus_client was not available during collection, causing import failures
4. **Hardcoded File Paths**: Windows-specific hardcoded paths causing unnecessary file I/O
5. **Module-Level Context Managers**: Mocking operations using context managers at module level

## Solutions Implemented

### 1. Defer Temporary Directory Creation to Fixtures

**Files Modified**:
- `self_fixing_engineer/arbiter/conftest.py`
- `self_fixing_engineer/arbiter/tests/conftest.py`
- `generator/runner/tests/conftest.py`

**Changes**:
```python
# BEFORE (module level - blocks collection):
TEST_TEMP_DIR = tempfile.mkdtemp(prefix="sfe_test_")

# AFTER (inside session fixture - runs after collection):
@pytest.fixture(scope="session", autouse=True)
def isolate_plugin_registry():
    global TEST_TEMP_DIR, TEST_PLUGIN_FILE
    TEST_TEMP_DIR = tempfile.mkdtemp(prefix="sfe_test_")
    # ... rest of setup
    yield
    # cleanup
```

### 2. Initialize Prometheus Stubs Before Collection

**File Modified**: `conftest.py` (root)

**Changes**:
- Moved `_initialize_prometheus_stubs()` call from session fixture to module level (before collection)
- This allows modules that import prometheus_client to succeed during collection
- The function is idempotent (safe to call multiple times)

```python
# Initialize Prometheus stubs early (before collection)
# This MUST happen before test collection because test modules import code
# that depends on prometheus_client (e.g., bug_manager/utils.py)
_initialize_prometheus_stubs()
```

### 3. Conditional OpenTelemetry Setup

**Files Modified**:
- `self_fixing_engineer/arbiter/conftest.py`
- `self_fixing_engineer/arbiter/tests/conftest.py`

**Changes**:
- Made OpenTelemetry context setup conditional on `PYTEST_COLLECTING` environment variable
- This variable is set in root conftest.py before collection starts
- Allows skipping expensive OpenTelemetry initialization during collection

```python
# Run the OpenTelemetry context setup only if not in collection phase
if os.environ.get("PYTEST_COLLECTING") != "1":
    _setup_opentelemetry_context()
```

### 4. Remove Hardcoded Windows Paths

**File Modified**: `generator/audit_log/tests/conftest.py`

**Changes**:
- Removed hardcoded Windows-specific path: `Path(r"D:\SFE\self_fixing_engineer\.env")`
- Added documentation on how to properly configure environment variables
- Prevents unnecessary file I/O during collection

### 5. Defer Streamlit Mocking to Fixture

**File Modified**: `self_fixing_engineer/intent_capture/tests/conftest.py`

**Changes**:
- Moved module-level context manager to session-scoped fixture
- Prevents unconventional pattern of context managers at module level

```python
# BEFORE (module level):
with mock.patch.dict(sys.modules, {"streamlit": mock.MagicMock()}):
    sys.modules["streamlit"].session_state = mock_session_state

# AFTER (in fixture):
@pytest.fixture(scope="session", autouse=True)
def mock_streamlit_setup():
    with mock.patch.dict(sys.modules, {"streamlit": mock.MagicMock()}):
        sys.modules["streamlit"].session_state = mock_session_state
        yield
```

## Impact

### Performance Improvements
- **Collection Phase**: Significantly faster test collection by deferring expensive operations
- **CI/CD**: Should now complete collection within the 120-second timeout
- **Local Development**: Faster test discovery for developers

### Code Quality
- **Best Practices**: All expensive operations now properly deferred to fixtures
- **Cross-Platform**: Removed Windows-specific hardcoded paths
- **Documentation**: Added clear comments explaining design decisions

### Maintainability
- **Clear Separation**: Collection-time vs. execution-time operations clearly separated
- **Fixture Ordering**: Proper use of fixture dependencies for setup ordering
- **Idempotent Operations**: Safe to run initialization multiple times

## Files Changed

1. `conftest.py` (root) - Initialize prometheus stubs before collection
2. `self_fixing_engineer/arbiter/conftest.py` - Defer tempdir creation to fixture
3. `self_fixing_engineer/arbiter/tests/conftest.py` - Defer tempdir and OpenTelemetry setup
4. `generator/runner/tests/conftest.py` - Defer tempdir creation to fixture
5. `generator/audit_log/tests/conftest.py` - Remove hardcoded Windows path
6. `self_fixing_engineer/intent_capture/tests/conftest.py` - Defer Streamlit mocking to fixture

## Testing

### Code Review
- ✅ Completed with all feedback addressed
- ✅ Documentation improved for clarity
- ✅ Comments added explaining design decisions

### Security
- ✅ CodeQL analysis: No security issues detected
- ✅ No new vulnerabilities introduced

### Next Steps
1. Wait for CI workflow to run and validate timeout is resolved
2. Monitor test collection time in CI to ensure it stays under 120 seconds
3. If collection is still slow, investigate other potential bottlenecks

## Lessons Learned

1. **Never Call Expensive Operations at Module Level**: Always defer to fixtures
2. **Understand Collection vs. Execution**: Pytest imports all test files during collection
3. **Use Environment Variables for Conditional Setup**: Skip expensive setup during collection
4. **Initialize Stubs Early**: Create lightweight stubs before collection, full mocks in fixtures
5. **Avoid Hardcoded Paths**: Use environment variables or configuration files

## References

- [Pytest Best Practices](https://docs.pytest.org/en/stable/goodpractices.html)
- [Pytest Fixtures](https://docs.pytest.org/en/stable/fixture.html)
- [Pytest Collection](https://docs.pytest.org/en/stable/goodpractices.html#test-discovery)
