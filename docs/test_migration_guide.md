# Test Configuration Migration Guide

**Version**: 1.0  
**Last Updated**: 2026-02-05  
**Target Completion**: 2026-02-12

## Table of Contents

1. [Overview](#overview)
2. [Migration Steps](#migration-steps)
3. [Before and After Comparison](#before-and-after-comparison)
4. [Rollback Plan](#rollback-plan)
5. [Expected Improvements](#expected-improvements)
6. [Validation Checklist](#validation-checklist)

---

## Overview

This guide provides step-by-step instructions for migrating from the current complex test configuration to an optimized setup.

### Migration Goals

1. ✅ Delete 46 temporary root-level test files
2. ✅ Simplify root `conftest.py` from 3,487 to < 1,000 lines
3. ✅ Optimize `pyproject.toml` pytest configuration
4. ✅ Add test categorization markers
5. ✅ Improve test collection performance by 75%
6. ✅ Eliminate CPU timeout errors (exit code 152)

### Timeline

- **Phase 1** (Day 1): Cleanup - Delete root test files
- **Phase 2** (Day 2-3): Simplify conftest.py
- **Phase 3** (Day 4): Optimize pyproject.toml
- **Phase 4** (Day 5-6): Add test markers
- **Phase 5** (Day 7): Validation and benchmarking

### Prerequisites

- ✅ Backup current configuration (git commit)
- ✅ Review analysis report (`docs/test_analysis_report.md`)
- ✅ Review testing guidelines (`docs/testing_guidelines.md`)
- ✅ Ensure working test environment

---

## Migration Steps

### Phase 1: Cleanup Root Test Files

**Duration**: 1 hour  
**Risk Level**: LOW

#### Step 1.1: Backup Current State

```bash
# Create a backup branch
git checkout -b backup/pre-test-optimization
git push origin backup/pre-test-optimization

# Return to working branch
git checkout main
```

#### Step 1.2: Identify Root Test Files

```bash
# List all root-level test files (should show 46 files)
ls -1 test_*.py | wc -l

# Review the files
ls -1 test_*.py
```

Expected output: 46 files including:
- test_audit_config_*.py
- test_conftest_*.py
- test_production_*.py
- test_startup_*.py
- etc.

#### Step 1.3: Delete Root Test Files

```bash
# Delete all root-level test files
rm test_*.py

# Verify deletion
ls -1 test_*.py 2>&1
# Expected: "ls: cannot access 'test_*.py': No such file or directory"
```

#### Step 1.4: Update .gitignore

```bash
# Add to .gitignore to prevent future root test files
echo "" >> .gitignore
echo "# Prevent test files in repository root" >> .gitignore
echo "/test_*.py" >> .gitignore
echo "/validate_*.py" >> .gitignore
echo "/verify_*.py" >> .gitignore
```

#### Step 1.5: Commit Changes

```bash
git add .gitignore
git add . 
git commit -m "test: Remove 46 temporary root-level test files

- Deleted test files created for specific bug fixes
- Added .gitignore patterns to prevent future root test files
- All permanent tests remain in module-specific tests/ directories
- See docs/test_migration_guide.md for details"

git push origin main
```

### Phase 2: Simplify conftest.py

**Duration**: 4-6 hours  
**Risk Level**: MEDIUM

#### Step 2.1: Backup Current conftest.py

```bash
# Create backup
cp conftest.py conftest.py.backup

# Check line count
wc -l conftest.py
# Expected: 3487 conftest.py
```

#### Step 2.2: Analyze Current Structure

The current conftest.py has these sections:
1. Path setup and NLTK configuration (lines 1-70)
2. Module validation helpers (lines 71-114)
3. **prometheus_client mock infrastructure (lines 115-289) ← TARGET**
4. Real module validation (lines 290-400)
5. More mocking logic (lines 400-3487)

**Focus Areas for Simplification:**
- Prometheus mock creation (150+ lines → 30 lines)
- OpenTelemetry mocks (if present)
- Defensive validation logic
- Duplicate patterns

#### Step 2.3: Create Simplified Version

**Strategy**: Simplify prometheus_client mocking

**Current approach** (complex):
```python
# Creates full module specs, submodules, etc. (~150 lines)
prom_spec = importlib.machinery.ModuleSpec(...)
prom_module = importlib.util.module_from_spec(prom_spec)
# ... many lines of setup ...
```

**New approach** (simple):
```python
# Simplified lazy mock (~30 lines)
def _create_prometheus_mock():
    """Create simplified prometheus_client mock."""
    if "prometheus_client" in sys.modules:
        return  # Already loaded
    
    from unittest.mock import MagicMock
    mock = MagicMock()
    
    # Add essential attributes
    mock.Counter = MagicMock
    mock.Histogram = MagicMock
    mock.Gauge = MagicMock
    mock.Info = MagicMock
    mock.REGISTRY = MagicMock()
    
    sys.modules["prometheus_client"] = mock
    sys.modules["prometheus_client.core"] = MagicMock()
    sys.modules["prometheus_client.multiprocess"] = MagicMock()

# Only call if prometheus_client not installed
try:
    import prometheus_client
except ImportError:
    _create_prometheus_mock()
```

#### Step 2.4: Implementation Plan

**Create new conftest.py structure:**

```python
# conftest.py (simplified version)

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# 1. Path Setup (keep as-is, ~70 lines)
project_root = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, project_root)
# ... rest of path setup ...

# 2. Environment Variables (keep as-is, ~20 lines)
os.environ["TESTING"] = "1"
os.environ.setdefault("OTEL_SDK_DISABLED", "1")
# ... rest of env vars ...

# 3. Simplified Mock Infrastructure (NEW - ~100 lines total)

def _create_simple_mock(module_name, attributes=None):
    """Create a simple mock module."""
    if module_name in sys.modules:
        return
    
    mock = MagicMock()
    mock.__name__ = module_name
    mock.__spec__ = MagicMock()
    
    if attributes:
        for attr_name, attr_value in attributes.items():
            setattr(mock, attr_name, attr_value)
    
    sys.modules[module_name] = mock

# Mock prometheus_client if not installed
try:
    import prometheus_client
except ImportError:
    _create_simple_mock("prometheus_client", {
        "Counter": MagicMock,
        "Histogram": MagicMock,
        "Gauge": MagicMock,
        "Info": MagicMock,
        "REGISTRY": MagicMock(),
    })
    _create_simple_mock("prometheus_client.core")
    _create_simple_mock("prometheus_client.multiprocess")

# Mock opentelemetry if not installed
try:
    import opentelemetry
except ImportError:
    _create_simple_mock("opentelemetry")
    _create_simple_mock("opentelemetry.trace")
    _create_simple_mock("opentelemetry.sdk")

# 4. Pytest Fixtures (keep existing fixtures, ~200 lines)
import pytest

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment."""
    # ... existing fixture code ...
    pass

# ... other fixtures ...
```

**Target line count**: ~500-800 lines (down from 3,487)

#### Step 2.5: Test Simplified conftest.py

```bash
# Test collection with new conftest.py
pytest --collect-only --quiet

# Test a few modules
pytest generator/tests/test_codegen_agent.py --collect-only
pytest omnicore_engine/tests/test_core.py --collect-only

# Run actual tests
pytest tests/ -v
```

#### Step 2.6: Commit Simplified conftest.py

```bash
git add conftest.py
git commit -m "test: Simplify conftest.py from 3,487 to ~800 lines

- Simplified prometheus_client mocking (150 lines → 30 lines)
- Removed unnecessary module validation logic
- Consolidated mock creation patterns
- Maintained all essential fixtures
- Test collection time improved significantly

See docs/test_migration_guide.md for details"

git push origin main
```

### Phase 3: Optimize pyproject.toml

**Duration**: 2 hours  
**Risk Level**: LOW

#### Step 3.1: Backup Current Configuration

```bash
cp pyproject.toml pyproject.toml.backup
```

#### Step 3.2: Update [tool.pytest.ini_options]

**Changes to make:**

1. **Add explicit testpaths**
2. **Update addopts to skip heavy/slow tests by default**
3. **Improve async configuration**

**New configuration:**

```toml
[tool.pytest.ini_options]
minversion = "7.4"
pythonpath = ["."]

# Explicit test paths - pytest will only scan these directories
testpaths = [
    "tests",
    "generator/tests",
    "omnicore_engine/tests",
    "self_fixing_engineer/tests",
    "server/tests",
]

# Optimized addopts - skip heavy and slow tests by default
addopts = [
    "-ra",                              # Show extra test summary
    "-q",                               # Quiet mode
    "--tb=short",                       # Short traceback format
    "-p no:randomly",                   # Disable pytest-randomly plugin
    "-p no:cacheprovider",              # Disable cache for faster collection
    "--import-mode=importlib",          # Use importlib for imports
    "--maxfail=5",                      # Stop after 5 failures
    "-m not (heavy or slow)",           # Skip heavy and slow tests by default
]

python_files = ["test_*.py"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

# Timeout configuration
timeout = 30
timeout_method = "thread"
timeout_func_only = true

# Directories to ignore
norecursedirs = ".* _* build dist venv env node_modules __pycache__"

# Test markers
markers = [
    "unit: marks tests as unit tests (fast, no external deps)",
    "integration: marks tests as integration tests",
    "slow: marks tests as slow (> 5 seconds)",
    "heavy: marks tests requiring heavy dependencies (numpy, pandas, torch)",
    "requires_redis: marks tests requiring Redis connection",
    "requires_db: marks tests requiring database connection",
    "forked: marks tests that should run in isolated forked process",
    "flaky: marks tests as flaky (may need retries)",
]

# Filter warnings
filterwarnings = [
    "ignore:.*PydanticDeprecatedSince20.*:DeprecationWarning",
    "ignore::pydantic.warnings.PydanticDeprecatedSince20",
    "ignore:.*OpenTelemetry.*:UserWarning",
    "ignore:pkg_resources is deprecated:DeprecationWarning",
    "ignore::DeprecationWarning",
]
```

#### Step 3.3: Test New Configuration

```bash
# Test collection with new config
pytest --collect-only

# Verify testpaths are respected
pytest --collect-only | grep "collected"

# Test that heavy tests are skipped by default
pytest --collect-only -m "not (heavy or slow)"
```

#### Step 3.4: Commit Changes

```bash
git add pyproject.toml
git commit -m "test: Optimize pytest configuration in pyproject.toml

- Added explicit testpaths to improve collection performance
- Skip heavy and slow tests by default (-m not (heavy or slow))
- Added unit and requires_db markers
- Improved async configuration
- Reduced test collection time significantly

See docs/test_migration_guide.md for details"

git push origin main
```

### Phase 4: Add Test Markers

**Duration**: 4-6 hours  
**Risk Level**: LOW

#### Step 4.1: Identify Tests to Mark

From analysis report, these tests need `@pytest.mark.heavy`:

**self_fixing_engineer/tests:**
- test_arbiter_decision_optimizer.py
- test_envs_code_health_env.py
- test_arbiter_models_feature_store_client.py
- test_envs_e2e_env.py

**omnicore_engine/tests:**
- test_array_backend.py
- test_core.py
- test_meta_supervisor.py

#### Step 4.2: Add @pytest.mark.heavy

**Example for test_array_backend.py:**

```python
# omnicore_engine/tests/test_array_backend.py

import pytest

@pytest.mark.heavy
def test_numpy_array_operations():
    """Test array operations using numpy."""
    import numpy as np
    # ... rest of test ...

@pytest.mark.heavy  
def test_another_numpy_operation():
    """Another test requiring numpy."""
    import numpy as np
    # ... rest of test ...
```

#### Step 4.3: Mark Integration Tests

Add `@pytest.mark.integration` to tests requiring Redis or other services:

```python
@pytest.mark.integration
@pytest.mark.requires_redis
async def test_redis_cache():
    """Test Redis caching functionality."""
    # ... test code ...
```

#### Step 4.4: Mark Slow Tests

Add `@pytest.mark.slow` to tests taking > 5 seconds:

```python
@pytest.mark.slow
def test_long_running_operation():
    """Test that takes a long time."""
    # ... test code ...
```

#### Step 4.5: Test Marker Functionality

```bash
# Run only heavy tests
pytest -m heavy

# Run without heavy tests (default)
pytest -m "not (heavy or slow)"

# Run specific marker
pytest -m unit
pytest -m integration
```

#### Step 4.6: Commit Marker Changes

```bash
git add .
git commit -m "test: Add markers to categorize tests

- Added @pytest.mark.heavy to 7 tests using numpy/pandas
- Added @pytest.mark.integration to tests requiring services
- Added @pytest.mark.slow to long-running tests
- Tests are now properly categorized for selective execution

See docs/test_migration_guide.md for details"

git push origin main
```

### Phase 5: Validation and Benchmarking

**Duration**: 4 hours  
**Risk Level**: LOW

#### Step 5.1: Measure Collection Time

```bash
# Measure collection time before (use backup)
time pytest --collect-only --quiet

# Measure collection time after
time pytest --collect-only --quiet
```

#### Step 5.2: Run Full Test Suite

```bash
# Run all tests (except heavy and slow by default)
pytest

# Run full suite including heavy tests
pytest -m ""

# Run per module
pytest generator/tests/
pytest omnicore_engine/tests/
pytest self_fixing_engineer/tests/
pytest server/tests/
```

#### Step 5.3: Verify CI Workflow

```bash
# Push changes and monitor CI
git push origin main

# Check GitHub Actions workflow
# URL: https://github.com/musicmonk42/The_Code_Factory_Working_V2/actions
```

#### Step 5.4: Document Improvements

Create `docs/test_optimization_results.md` with:
- Before/after collection times
- Before/after conftest.py line counts
- CI workflow improvements
- Any remaining issues

---

## Before and After Comparison

### Configuration Files

| File | Before | After | Change |
|------|--------|-------|--------|
| **conftest.py** | 3,487 lines | ~800 lines | -77% |
| **pyproject.toml** | 151 lines | ~160 lines | +6% (added markers) |
| **Root test files** | 46 files | 0 files | -100% |
| **CI workflow** | 976 lines | 976 lines | No change needed |

### Test Execution

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Collection time** | 120-180s | <30s | 75-85% faster |
| **Root conftest execution** | ~5-10s | <1s | 80-90% faster |
| **CPU timeout errors** | Frequent | None | 100% eliminated |
| **Marked tests** | 0 | 358+ | Full categorization |

### Developer Experience

| Aspect | Before | After |
|--------|--------|-------|
| **Local test run** | `pytest` (slow, includes everything) | `pytest` (fast, skips heavy/slow) |
| **Run heavy tests** | No easy way | `pytest -m heavy` |
| **Test organization** | Cluttered with 46 root files | Clean, organized by module |
| **CI failures** | Common CPU timeouts | Rare, predictable |

---

## Rollback Plan

If issues arise, follow these steps to rollback:

### Rollback conftest.py

```bash
# Restore original conftest.py
cp conftest.py.backup conftest.py
git add conftest.py
git commit -m "rollback: Restore original conftest.py"
git push origin main
```

### Rollback pyproject.toml

```bash
# Restore original pyproject.toml
cp pyproject.toml.backup pyproject.toml
git add pyproject.toml
git commit -m "rollback: Restore original pyproject.toml"
git push origin main
```

### Rollback Test Markers

```bash
# Markers don't break anything - they just categorize tests
# Can be removed gradually if needed
git revert <commit-hash>
```

### Restore Root Test Files

```bash
# Checkout from backup branch
git checkout backup/pre-test-optimization -- test_*.py
git add test_*.py
git commit -m "rollback: Restore root test files"
git push origin main
```

### Full Rollback

```bash
# Reset to backup branch
git reset --hard backup/pre-test-optimization
git push --force origin main  # Use with caution!
```

---

## Expected Improvements

### Performance Improvements

1. **Test Collection**: 75-85% faster (120-180s → <30s)
2. **conftest.py Execution**: 80-90% faster (~5-10s → <1s)
3. **CI Workflow**: More stable, fewer timeouts
4. **Local Development**: Faster test iterations

### Maintenance Improvements

1. **Cleaner Repository**: No cluttered root test files
2. **Simpler conftest.py**: Easier to understand and modify
3. **Better Categorization**: Clear test types and execution tiers
4. **Improved Documentation**: Comprehensive guides for developers

### Quality Improvements

1. **Fewer Flaky Tests**: Reduced complexity = fewer issues
2. **Faster Feedback**: Quick unit tests catch issues early
3. **Better CI Stability**: Eliminated CPU timeout errors
4. **Selective Testing**: Run only relevant tests locally

---

## Validation Checklist

After completing migration, verify:

### Phase 1: Cleanup ✓
- [ ] All 46 root test files deleted
- [ ] .gitignore updated to prevent future root test files
- [ ] No test files remain in repository root
- [ ] Changes committed and pushed

### Phase 2: conftest.py ✓
- [ ] conftest.py reduced from 3,487 to <1,000 lines
- [ ] All tests still pass with new conftest.py
- [ ] pytest collection works correctly
- [ ] No import errors or missing mocks
- [ ] Changes committed and pushed

### Phase 3: pyproject.toml ✓
- [ ] testpaths added and working
- [ ] addopts updated to skip heavy/slow by default
- [ ] New markers defined
- [ ] Async configuration improved
- [ ] Changes committed and pushed

### Phase 4: Test Markers ✓
- [ ] 7 heavy tests marked with @pytest.mark.heavy
- [ ] Integration tests marked appropriately
- [ ] Slow tests marked with @pytest.mark.slow
- [ ] Markers work correctly (`pytest -m heavy`, etc.)
- [ ] Changes committed and pushed

### Phase 5: Validation ✓
- [ ] Test collection time reduced by >75%
- [ ] All tests pass (except expected failures)
- [ ] CI workflow runs successfully
- [ ] No CPU timeout errors (exit code 152)
- [ ] Documentation updated with results

---

## Post-Migration Tasks

### Update CI Workflow (Optional)

If needed, optimize `.github/workflows/pytest-all.yml`:

```yaml
- name: Run tests
  run: |
    # Run tests excluding heavy by default
    pytest ${{ matrix.module }}/tests \
      -m "not heavy" \
      --maxfail=5 \
      --timeout=30
      
- name: Run heavy tests (optional)
  if: github.event_name == 'schedule'  # Only on nightly
  run: |
    pytest ${{ matrix.module }}/tests \
      -m heavy \
      --maxfail=5 \
      --timeout=60
```

### Monitor Performance

- Track test collection times
- Monitor CI workflow duration
- Watch for new timeouts or failures
- Collect feedback from developers

### Continuous Improvement

- Review and update markers as tests evolve
- Keep conftest.py simple - resist adding complexity
- Enforce "no root test files" policy in code reviews
- Update documentation as needed

---

## Support and Questions

If you encounter issues during migration:

1. **Review documentation**:
   - `docs/test_analysis_report.md`
   - `docs/testing_guidelines.md`
   - This migration guide

2. **Check git history**:
   ```bash
   git log --oneline -- conftest.py
   git show <commit-hash>
   ```

3. **Use backup branch**:
   ```bash
   git checkout backup/pre-test-optimization
   # Review original files
   ```

4. **Ask for help**:
   - Open GitHub issue
   - Team chat
   - Code review

---

## Success Criteria

Migration is successful when:

✅ **All 46 root test files deleted**  
✅ **conftest.py reduced to <1,000 lines**  
✅ **Test collection time <30 seconds**  
✅ **Zero CPU timeout errors**  
✅ **All tests properly marked**  
✅ **CI workflow stable**  
✅ **Documentation complete**  
✅ **Team trained on new structure**

---

**Document maintained by**: Code Factory Team  
**Last updated**: 2026-02-05  
**Next review**: After migration completion
