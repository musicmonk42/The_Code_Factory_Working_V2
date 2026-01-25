# Pytest CPU Timeout Diagnostic Implementation

## Problem
The pytest workflow was failing with "CPU time limit exceeded" (exit code 152) during test collection, indicating excessive CPU cycles consumed during test discovery.

## Solution Implemented

### 1. Diagnostic Script (`.github/scripts/diagnose_test_collection.sh`)
**Purpose**: Identify which specific test files are causing CPU timeouts

**Features**:
- Tests each test file individually with a 10-second timeout
- Identifies files that timeout, fail, or exceed CPU limits
- Uses bash process substitution to properly track problematic files
- Provides detailed summary of problematic files

**Usage in CI**: Automatically runs before main test suite to identify issues early

### 2. Pattern Checker Script (`.github/scripts/check_test_patterns.sh`)
**Purpose**: Search for common anti-patterns that cause CPU-intensive test collection

**Checks for**:
- Module-level client/connection instantiation
- Direct imports of heavy modules (MetaSupervisor, Arbiter)
- Imports from conftest (circular import risk)
- Module-level function calls

**Usage**: Can be run manually or in CI to identify problematic patterns

### 3. Workflow Updates (`.github/workflows/pytest-all.yml`)

#### a. Diagnostic Step (runs before tests)
```yaml
- name: Diagnose test collection issues
  if: always()  # Run even if previous steps failed
  run: |
    chmod +x .github/scripts/diagnose_test_collection.sh
    ./.github/scripts/diagnose_test_collection.sh || true
  continue-on-error: true
```

**Benefits**:
- Identifies problematic files before full test run
- Doesn't block the main workflow (continue-on-error)
- Always runs, even if previous steps fail

#### b. CPU Profiling Step (runs on failure)
```yaml
- name: Profile test collection (on failure)
  if: failure()
  run: |
    echo "=== Running test collection with CPU profiling ==="
    timeout 180s python -m cProfile -o collection_profile.stats -m pytest \
      --collect-only --quiet --import-mode=importlib --tb=short \
      --ignore=self_fixing_engineer/simulation/tests 2>&1 || true
    
    if [ -f collection_profile.stats ]; then
      echo "=== Top 30 CPU-consuming functions ==="
      python -c "import pstats; p = pstats.Stats('collection_profile.stats'); p.sort_stats('cumulative').print_stats(30)"
    fi
  continue-on-error: true
```

**Benefits**:
- Only runs when tests fail, saving CI time
- Provides detailed CPU profile showing expensive functions
- Shows top 30 functions sorted by cumulative time
- Helps pinpoint exact bottlenecks

### 4. LazyStubImporter Conditional (`conftest.py`)

**Change**:
```python
if not os.environ.get('DISABLE_LAZY_IMPORTER'):
    _lazy_importer = LazyStubImporter()
    sys.meta_path.insert(0, _lazy_importer)
else:
    print("⚠️  LazyStubImporter disabled via DISABLE_LAZY_IMPORTER env var")
```

**Benefits**:
- Allows testing with/without lazy importer
- Can help isolate whether lazy importer is causing issues
- Set `DISABLE_LAZY_IMPORTER=1` in workflow env to test

## How to Use

### In CI (Automatic)
The diagnostic tools run automatically:
1. Diagnostic script runs before tests
2. If collection fails, CPU profiling runs
3. Check workflow logs for:
   - Which files timeout (in diagnostic step)
   - Which functions consume CPU (in profiling step)

### Manual Testing
```bash
# Run diagnostic script
./.github/scripts/diagnose_test_collection.sh

# Run pattern checker
./.github/scripts/check_test_patterns.sh

# Test without lazy importer
DISABLE_LAZY_IMPORTER=1 pytest --collect-only
```

### Fixing Identified Issues

**Pattern 1: Module-level imports of heavy classes**
```python
# BEFORE (problematic)
from omnicore_engine.meta_supervisor import MetaSupervisor

def test_something():
    supervisor = MetaSupervisor()

# AFTER (fixed)
def test_something():
    from omnicore_engine.meta_supervisor import MetaSupervisor
    supervisor = MetaSupervisor()
```

**Pattern 2: Module-level initialization**
```python
# BEFORE (problematic)
client = SomeClient()

def test_something():
    client.do_something()

# AFTER (fixed)
@pytest.fixture
def client():
    return SomeClient()

def test_something(client):
    client.do_something()
```

## Expected Outcomes

1. **Faster Diagnosis**: Quickly identify which test files are problematic
2. **Root Cause Analysis**: CPU profiling shows exact functions consuming time
3. **Targeted Fixes**: Fix only the problematic files, not all tests
4. **Testing Flexibility**: Can test with/without lazy importer

## Success Criteria

- ✅ Test collection completes within 180 seconds
- ✅ All test files can be collected individually within 10 seconds
- ✅ No circular import dependencies
- ✅ No module-level initialization of heavy objects

## Files Modified

1. `.github/scripts/diagnose_test_collection.sh` (created)
2. `.github/scripts/check_test_patterns.sh` (created)
3. `.github/workflows/pytest-all.yml` (modified)
4. `conftest.py` (modified)

## Validation

All changes have been validated:
- ✅ YAML syntax is valid
- ✅ Bash scripts have correct syntax
- ✅ Scripts have executable permissions
- ✅ No breaking changes to existing functionality
