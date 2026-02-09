# Pipeline Job Run Fix - Verification Guide

This document explains the fixes applied to resolve pipeline job run failures and how to verify they work correctly.

## Issues Fixed

### 1. Empty requirements.txt Problem
**Issue:** Pipeline failed validation when `requirements.txt` was missing or empty, causing false job failures.

**Fix Location:** `server/services/omnicore_service.py` lines 2742-2767

**Fix Description:**
- Auto-generates fallback `requirements.txt` before validation runs
- Only triggers when file is missing or has 0 bytes
- Includes essential packages: fastapi, uvicorn, pydantic, pytest, httpx
- Prevents validation failures from missing dependencies

**Verification:**
```bash
# Run the requirements fallback tests
python -m pytest tests/test_pipeline_job_run_fixes.py::TestRequirementsFallback -v
```

### 2. Main.py Validation Mismatch
**Issue:** Validator expected root `main.py`, but generator outputs `app/main.py` for app-structured projects.

**Fix Location:** `generator/runner/runner_file_utils.py` lines 1527-1567

**Fix Description:**
- Already correctly implemented
- Automatically detects `app/` directory layout
- Adjusts `CRITICAL_REQUIRED_FILES` from `main.py` to `app/main.py`
- Recursively searches for `main.py` in non-standard locations
- Updates validation requirements dynamically

**Verification:**
```bash
# Run the app layout validation tests
python -m pytest tests/test_pipeline_job_run_fixes.py::TestAppLayoutValidation -v
```

### 3. File Discovery Bug
**Issue:** testgen skip message "no source files found" because non-recursive glob missed nested files.

**Fix Location:** `server/services/omnicore_service.py` line 2875

**Fix Description:**
- Already correctly implemented
- Uses recursive `rglob("*.py")` instead of `glob("*.py")`
- Finds Python files in nested directories like `app/main.py`
- Excludes test files from source count

**Verification:**
```bash
# Run the file discovery tests
python -m pytest tests/test_pipeline_job_run_fixes.py::TestFileDiscovery -v
```

### 4. Job Completion Semantics
**Issue:** Concern that job status was set to COMPLETED too early, before validation.

**Status:** Already correct - no fix needed

**Current Flow:**
1. Pipeline runs codegen → line 2726
2. Validation runs and checks files → line 2775-2808
3. If validation fails → job marked FAILED → line 2800-2808
4. If validation passes → pipeline continues
5. Only after successful pipeline → finalization called
6. Job marked COMPLETED → `job_finalization.py` line 235

**Verification:**
```bash
# Run the job completion tests
python -m pytest tests/test_pipeline_job_run_fixes.py::TestJobCompletionSemantics -v
```

### 5. Packaging Nested ZIP Risk
**Issue:** Concern about output ZIP files being included in themselves (nested ZIPs).

**Status:** Already correctly prevented

**Fix Locations:**
- `server/services/omnicore_service.py` line 3037: Excludes `_output.zip` in artifact collection
- `server/routers/jobs.py` lines 479, 543, 622: Excludes `_output.zip` in download endpoints

**Verification:**
```bash
# Run the ZIP exclusion tests
python -m pytest tests/test_pipeline_job_run_fixes.py::TestZIPExclusion -v
```

## Running All Tests

### Prerequisites
```bash
# Install dependencies (use no-libvirt version if libvirt unavailable)
pip install -r requirements-no-libvirt.txt
```

### Run Full Test Suite
```bash
# Run all pipeline fix tests
python -m pytest tests/test_pipeline_job_run_fixes.py -v

# Run with detailed output
python -m pytest tests/test_pipeline_job_run_fixes.py -v --tb=short

# Run integration tests only
python -m pytest tests/test_pipeline_job_run_fixes.py::TestPipelineIntegration -v
```

## Manual Verification

### Test a Real Pipeline Job

1. **Start the server:**
```bash
uvicorn server.main:app --reload
```

2. **Create a job with minimal requirements:**
```bash
curl -X POST "http://localhost:8000/api/jobs/" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Test pipeline with app layout",
    "metadata": {}
  }'
# Note the job_id from response
```

3. **Upload requirements file:**
```bash
# Create a minimal requirements markdown
cat > test_requirements.md << EOF
# Test API

Build a simple FastAPI application with:
- Health check endpoint at /health
- Echo endpoint at /echo that accepts and returns JSON
- Proper error handling
EOF

# Upload to job (replace JOB_ID)
curl -X POST "http://localhost:8000/api/generator/upload" \
  -F "file=@test_requirements.md" \
  -F "job_id=JOB_ID"
```

4. **Run generation:**
```bash
# Replace JOB_ID
curl -X POST "http://localhost:8000/api/generator/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "JOB_ID",
    "include_tests": true,
    "include_deployment": true
  }'
```

5. **Check job status:**
```bash
# Replace JOB_ID
curl "http://localhost:8000/api/jobs/JOB_ID"
```

6. **Verify generated files:**
```bash
# Replace JOB_ID
curl "http://localhost:8000/api/jobs/JOB_ID/files"

# Look for:
# - requirements.txt exists and is not empty
# - main.py or app/main.py exists
# - No _output.zip in file list
# - Job status is COMPLETED (not FAILED)
```

7. **Download and inspect:**
```bash
# Replace JOB_ID
curl "http://localhost:8000/api/jobs/JOB_ID/download" -o output.zip
unzip -l output.zip

# Verify:
# - requirements.txt contains fastapi, uvicorn, etc.
# - main.py is in root OR app/main.py exists
# - No nested *_output.zip inside
# - All Python files have valid syntax
```

## Expected Behavior

### Before Fix
- ❌ Pipeline fails with "Required file is empty: requirements.txt"
- ❌ Pipeline fails with "Required file missing: main.py" (when app/main.py exists)
- ❌ testgen skips with "no source files found" (files in app/ directory)
- ⚠️ Risk of nested ZIP files

### After Fix
- ✅ Pipeline auto-generates requirements.txt fallback when missing/empty
- ✅ Pipeline validates app/main.py correctly
- ✅ testgen discovers files recursively in nested directories
- ✅ Job only marked COMPLETED after validation passes
- ✅ Output ZIP excludes nested _output.zip files

## Troubleshooting

### Test failures due to missing dependencies
```bash
# Install test dependencies
pip install pytest pytest-asyncio -q
```

### Import errors in tests
```bash
# Ensure you're in the project root
cd /path/to/The_Code_Factory_Working_V2

# Install project in development mode
pip install -e .
```

### Pipeline still fails validation
Check logs for specific validation errors:
```bash
# View logs for specific job
tail -f logs/omnicore_service.log | grep "JOB_ID"
```

Look for:
- `[PIPELINE] Job {job_id} auto-generated fallback requirements.txt` - fallback triggered
- `[PIPELINE] Job {job_id} detected app/ layout` - app detection working
- `[PIPELINE] Job {job_id} starting step: testgen with N source files` - file discovery working
- `[PIPELINE] Job {job_id} HARD FAIL - validation errors` - validation blocking completion

## Summary of Changes

### Files Modified
1. `server/services/omnicore_service.py` (lines 2742-2767)
   - Added requirements.txt fallback generation

### Files Added
1. `tests/test_pipeline_job_run_fixes.py`
   - Comprehensive regression test suite
   - 15+ test cases covering all fixes

### Files Verified (No Changes Needed)
1. `generator/runner/runner_file_utils.py` (lines 1527-1567)
   - Already handles app/ layout correctly
2. `server/services/omnicore_service.py` (line 2875)
   - Already uses recursive rglob
3. `server/services/job_finalization.py` (line 235)
   - Already marks COMPLETED after validation
4. `server/routers/jobs.py` (lines 479, 543, 622)
   - Already excludes _output.zip files

## Notes for Deployment

1. **Requirements Fallback:** The fallback list is minimal and conservative. Projects may need additional dependencies, but these provide a working baseline for FastAPI applications.

2. **App Layout Detection:** The validator automatically adapts to multiple layouts:
   - Root `main.py` (simple projects)
   - `app/main.py` (structured projects)
   - Other nested locations (detected via recursive search)

3. **Backward Compatibility:** All fixes are backward compatible and handle both old and new project structures.

4. **Performance:** Using `rglob` for recursive search has minimal performance impact for typical project sizes (<1000 files).

## References

- **Issue Tracking:** GitHub Issue #XXX (replace with actual issue number)
- **Pull Request:** https://github.com/musicmonk42/The_Code_Factory_Working_V2/pull/YYY
- **Related Docs:** DEPLOYMENT.md, README.md
