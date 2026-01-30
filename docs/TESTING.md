# Testing Guide

This guide explains how to run tests for The Code Factory Platform.

## Quick Start

To run all tests in the repository, simply execute:

```bash
pytest
```

This will discover and run all tests in the `generator/`, `omnicore_engine/`, and `self_fixing_engineer/` modules.

## Prerequisites

### 1. Install Dependencies

First, install all required dependencies:

```bash
pip install -r requirements.txt
```

**Important**: The repository has many dependencies. Ensure you have sufficient disk space (at least 5GB free) before installing.

### 2. Environment Setup

Some tests may require environment variables or services. Check module-specific README files for details:
- `generator/README.md`
- `omnicore_engine/README.md`  
- `self_fixing_engineer/README.md`

## Running Tests

### Run All Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov
```

### Run Specific Modules

```bash
# Test Generator module
pytest generator/tests/

# Test OmniCore Engine
pytest omnicore_engine/tests/

# Test Self-Fixing Engineer
pytest self_fixing_engineer/tests/
```

### Run Specific Test Files

```bash
# Run a specific test file
pytest self_fixing_engineer/simulation/tests/test_dlt_base.py

# Run a specific test class
pytest self_fixing_engineer/simulation/tests/test_dlt_base.py::TestDLTBase

# Run a specific test function
pytest self_fixing_engineer/simulation/tests/test_dlt_base.py::TestDLTBase::test_metric_creation
```

### Useful Options

```bash
# Stop at first failure
pytest -x

# Show local variables on failures
pytest -l

# Run only failed tests from last run
pytest --lf

# Run tests in parallel (requires pytest-xdist)
pytest -n auto

# Generate HTML coverage report
pytest --cov --cov-report=html
```

## Test Configuration

Test configuration is defined in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
minversion = "7.4"
addopts = "-ra -q --tb=short"
testpaths = [
    "generator/tests",
    "omnicore_engine/tests",
    "self_fixing_engineer/tests"
]
```

## Troubleshooting

### Missing Dependencies

If you encounter `ModuleNotFoundError`, ensure all dependencies are installed:

```bash
pip install -r requirements.txt
```

### Disk Space Issues

If installation fails with "No space left on device":

1. Clear pip cache: `pip cache purge`
2. Free up disk space
3. Install only critical packages or install incrementally

### Permission Issues

Some tests may require write permissions to certain directories (e.g., `/var/log/`). Run tests with appropriate permissions or configure the application to use user-writable directories.

### Import Errors

If tests fail to import modules, ensure you're running pytest from the repository root directory and that the repository is properly structured.

## Recent Fixes

The following import and type errors have been fixed (December 2025):

1. ✅ Fixed `ModuleNotFoundError: No module named 'bleach'` - module is in requirements.txt
2. ✅ Fixed `cannot import name '_cache'` - added missing module-level variable
3. ✅ Fixed `cannot import name 'AnalyzerCriticalError'` - added alias
4. ✅ Fixed `TypeError: isinstance() arg 2 must be a type` - improved type checking

## CI/CD

Tests are automatically run in GitHub Actions on:
- Pull requests
- Pushes to main/develop branches

See `.github/workflows/` for CI configuration details.

## Additional Resources

- [CI/CD Guide](CI_CD_GUIDE.md)
- [Contributing Guide](CONTRIBUTING.md) (if available)
- [Platform Verification Report](PLATFORM_VERIFICATION_REPORT.md)
