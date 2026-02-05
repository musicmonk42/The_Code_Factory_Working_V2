# Testing Guidelines - The Code Factory

**Version**: 1.0  
**Last Updated**: 2026-02-05

## Table of Contents

1. [Overview](#overview)
2. [Test Organization](#test-organization)
3. [Writing Tests](#writing-tests)
4. [Test Markers](#test-markers)
5. [Running Tests](#running-tests)
6. [Best Practices](#best-practices)
7. [CI/CD Integration](#cicd-integration)

---

## Overview

The Code Factory uses pytest as its testing framework. This guide provides standards and best practices for writing and organizing tests across all modules.

### Key Principles

1. **Fast by default** - Unit tests should run in < 1 minute
2. **Clear categorization** - Use markers to identify test types
3. **Minimal dependencies** - Avoid importing heavy libraries unless necessary
4. **Isolated tests** - Tests should not depend on each other
5. **Descriptive names** - Test names should clearly describe what they test

---

## Test Organization

### Directory Structure

```
The_Code_Factory_Working_V2/
├── tests/                          # Core/integration tests
├── generator/
│   └── tests/                      # Generator module tests
├── omnicore_engine/
│   └── tests/                      # OmniCore engine tests
├── self_fixing_engineer/
│   └── tests/                      # SFE/Arbiter tests
└── server/
    └── tests/                      # Server/API tests
```

**Rules:**
- ✅ **DO**: Place tests in module-specific `tests/` directories
- ❌ **DON'T**: Create test files in the repository root
- ✅ **DO**: Use `test_*.py` naming convention
- ❌ **DON'T**: Use `*_test.py` or other patterns

### File Naming Conventions

```python
# Good examples:
test_codegen_agent.py          # Tests for codegen_agent module
test_api_endpoints.py          # Tests for API endpoints
test_meta_supervisor.py        # Tests for meta_supervisor module

# Avoid:
test_fix_issue_123.py          # Don't create fix-specific test files
test_temp.py                   # Don't create temporary test files
validate_something.py          # Use test_ prefix
```

### Test Function Naming

```python
# Good examples:
def test_user_creation_with_valid_data():
    """Test that user is created successfully with valid input."""
    pass

def test_api_returns_404_for_missing_resource():
    """Test API returns 404 status when resource doesn't exist."""
    pass

def test_async_task_completion():
    """Test that async task completes within timeout."""
    pass

# Pattern: test_<what>_<condition/scenario>
```

---

## Writing Tests

### Basic Test Structure

```python
import pytest
from mymodule import MyClass

def test_basic_functionality():
    """Test basic functionality of MyClass."""
    # Arrange
    obj = MyClass()
    
    # Act
    result = obj.do_something()
    
    # Assert
    assert result == expected_value
```

### Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_async_function():
    """Test async function behavior."""
    result = await my_async_function()
    assert result is not None
```

**Note**: `asyncio_mode = "auto"` is configured in `pyproject.toml`, so the `@pytest.mark.asyncio` decorator is optional but recommended for clarity.

### Fixtures

```python
import pytest

@pytest.fixture
def sample_data():
    """Provide sample data for tests."""
    return {"key": "value"}

def test_with_fixture(sample_data):
    """Test using fixture data."""
    assert sample_data["key"] == "value"
```

**Best Practices:**
- Define shared fixtures in `conftest.py` at the appropriate level
- Use `scope` parameter for expensive fixtures: `@pytest.fixture(scope="module")`
- Clean up resources in fixtures using `yield`:

```python
@pytest.fixture
def temp_file():
    """Create temporary file for testing."""
    file = create_temp_file()
    yield file
    file.cleanup()  # Cleanup after test
```

### Parametrized Tests

```python
import pytest

@pytest.mark.parametrize("input,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
])
def test_doubling(input, expected):
    """Test that function doubles input."""
    assert double(input) == expected
```

### Mocking

```python
from unittest.mock import Mock, patch

def test_with_mock():
    """Test with mocked dependency."""
    mock_service = Mock()
    mock_service.get_data.return_value = {"result": "success"}
    
    result = process_data(mock_service)
    
    assert result["result"] == "success"
    mock_service.get_data.assert_called_once()

@patch('mymodule.external_api')
def test_with_patch(mock_api):
    """Test with patched external dependency."""
    mock_api.return_value = "mocked response"
    result = call_external_api()
    assert result == "mocked response"
```

---

## Test Markers

### Available Markers

Use pytest markers to categorize tests:

```python
import pytest

@pytest.mark.unit
def test_unit_function():
    """Fast unit test with no external dependencies."""
    pass

@pytest.mark.integration
def test_integration_with_redis():
    """Integration test requiring external services."""
    pass

@pytest.mark.slow
def test_slow_operation():
    """Test that takes > 5 seconds."""
    pass

@pytest.mark.heavy
def test_with_numpy():
    """Test requiring heavy dependencies (numpy, pandas, etc.)."""
    import numpy as np
    pass

@pytest.mark.requires_redis
def test_redis_connection():
    """Test requiring Redis connection."""
    pass

@pytest.mark.flaky
def test_occasionally_fails():
    """Test that may fail intermittently."""
    pass
```

### When to Use Each Marker

| Marker | Use When | Skip in CI? |
|--------|----------|-------------|
| `@pytest.mark.unit` | Fast test, no external deps | No (default) |
| `@pytest.mark.integration` | Requires services (Redis, DB) | Yes (default) |
| `@pytest.mark.slow` | Takes > 5 seconds | Yes (default) |
| `@pytest.mark.heavy` | Requires numpy, pandas, torch, etc. | Yes (default) |
| `@pytest.mark.requires_redis` | Needs Redis connection | Conditional |
| `@pytest.mark.flaky` | May fail intermittently | Mark for retry |

### Applying Multiple Markers

```python
@pytest.mark.integration
@pytest.mark.requires_redis
@pytest.mark.slow
async def test_complex_integration():
    """Complex integration test with multiple requirements."""
    pass
```

---

## Running Tests

### Local Development

```bash
# Run all fast tests (default)
pytest -m "not (heavy or slow or integration)"

# Run specific module tests
pytest generator/tests/

# Run specific test file
pytest generator/tests/test_codegen_agent.py

# Run specific test function
pytest generator/tests/test_codegen_agent.py::test_specific_function

# Run with coverage
pytest --cov=generator --cov-report=html

# Run in parallel (if pytest-xdist installed)
pytest -n auto

# Run with verbose output
pytest -v
```

### Test Execution Tiers

#### Tier 1: Fast Unit Tests (< 1 minute)
```bash
pytest -m "not (heavy or slow or integration)"
```
- Run before committing
- Should complete in < 1 minute
- No external dependencies

#### Tier 2: Standard Tests (< 5 minutes)
```bash
pytest -m "not heavy"
```
- Includes integration tests with Redis
- Run before pushing
- May require Docker services

#### Tier 3: Full Suite (< 15 minutes)
```bash
pytest
```
- Includes all tests
- Run in CI/CD pipeline
- Requires all dependencies

#### Tier 4: Heavy Tests Only
```bash
pytest -m heavy
```
- Tests requiring numpy, pandas, etc.
- Run periodically or before releases

### Running Tests with Docker

```bash
# Start required services
docker-compose up -d redis

# Run tests in container
docker-compose run --rm app pytest

# Stop services
docker-compose down
```

### Environment Variables

Tests respect these environment variables:

```bash
# Core test environment
export TESTING=1
export CI=1

# Skip expensive initialization
export SKIP_AUDIT_INIT=1
export SKIP_BACKGROUND_TASKS=1
export NO_MONITORING=1
export DISABLE_TELEMETRY=1
export OTEL_SDK_DISABLED=1

# Matplotlib backend (prevent GUI)
export MPLBACKEND=Agg

# Redis connection
export REDIS_URL=redis://localhost:6379
```

---

## Best Practices

### DO ✅

1. **Write descriptive test names**
   ```python
   # Good
   def test_user_login_fails_with_invalid_password():
       pass
   
   # Bad
   def test_login():
       pass
   ```

2. **Test one thing per test**
   ```python
   # Good - separate tests
   def test_user_creation():
       assert create_user() is not None
   
   def test_user_validation():
       assert validate_user() is True
   
   # Bad - testing multiple things
   def test_user():
       assert create_user() is not None
       assert validate_user() is True
       assert delete_user() is True
   ```

3. **Use appropriate markers**
   ```python
   @pytest.mark.heavy
   def test_ml_model():
       import torch  # Heavy dependency
       pass
   ```

4. **Mock external dependencies**
   ```python
   @patch('requests.get')
   def test_api_call(mock_get):
       mock_get.return_value.json.return_value = {}
       pass
   ```

5. **Clean up after tests**
   ```python
   def test_file_operations():
       file = create_temp_file()
       try:
           # test operations
           pass
       finally:
           file.cleanup()
   ```

### DON'T ❌

1. **Don't create root-level test files**
   ```bash
   # Bad - creates in repository root
   touch test_my_fix.py
   
   # Good - creates in module tests directory
   touch generator/tests/test_my_feature.py
   ```

2. **Don't import heavy libraries unnecessarily**
   ```python
   # Bad - imports at module level
   import torch
   import transformers
   
   def test_simple_function():
       pass  # Doesn't use torch or transformers
   
   # Good - import only where needed
   @pytest.mark.heavy
   def test_ml_function():
       import torch  # Import inside test
       pass
   ```

3. **Don't use time.sleep() in tests**
   ```python
   # Bad
   def test_async_operation():
       start_operation()
       time.sleep(5)  # Brittle and slow
       assert is_complete()
   
   # Good
   async def test_async_operation():
       task = start_operation()
       await asyncio.wait_for(task, timeout=5)
       assert is_complete()
   ```

4. **Don't hardcode paths or credentials**
   ```python
   # Bad
   def test_config():
       config = load_config("/home/user/config.yaml")
   
   # Good
   def test_config(tmp_path):
       config_file = tmp_path / "config.yaml"
       config = load_config(config_file)
   ```

5. **Don't test implementation details**
   ```python
   # Bad - tests internal implementation
   def test_internal_method():
       obj = MyClass()
       assert obj._internal_method() == "value"
   
   # Good - tests public interface
   def test_public_interface():
       obj = MyClass()
       assert obj.public_method() == "expected"
   ```

### Performance Tips

1. **Use module-scoped fixtures for expensive setup**
   ```python
   @pytest.fixture(scope="module")
   def expensive_resource():
       resource = create_expensive_resource()
       yield resource
       resource.cleanup()
   ```

2. **Skip tests conditionally**
   ```python
   @pytest.mark.skipif(not redis_available(), reason="Redis not available")
   def test_redis_operation():
       pass
   ```

3. **Mark slow tests**
   ```python
   @pytest.mark.slow
   def test_long_operation():
       # Takes > 5 seconds
       pass
   ```

---

## CI/CD Integration

### GitHub Actions Workflow

Tests run automatically on:
- Push to `main`, `develop`, or `feature/**` branches
- Pull requests to `main` or `develop`
- Nightly schedule (2 AM UTC)

### CI Test Strategy

```yaml
# Tests run per module in parallel
matrix:
  module:
    - omnicore_engine
    - generator
    - self_fixing_engineer
    - server
```

### CI Environment

- Python 3.11
- Redis service container
- Ubuntu latest
- Timeout: 45 minutes per module

### Customizing CI Tests

To modify which tests run in CI, edit `.github/workflows/pytest-all.yml`:

```yaml
- name: Run tests
  run: |
    pytest ${{ matrix.module }}/tests \
      -m "not (heavy or slow)" \
      --maxfail=5 \
      --timeout=30
```

---

## Troubleshooting

### Common Issues

#### Test Collection Timeout
```bash
# Problem: Pytest collection takes too long
# Solution: Run specific test directory
pytest generator/tests/ --collect-only
```

#### Import Errors
```bash
# Problem: Module not found
# Solution: Ensure PYTHONPATH is set correctly
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

#### Redis Connection Errors
```bash
# Problem: Redis not available
# Solution: Start Redis container
docker-compose up -d redis
pytest -m requires_redis
```

#### Async Test Errors
```python
# Problem: RuntimeError: Event loop is closed
# Solution: Use pytest-asyncio properly
@pytest.mark.asyncio
async def test_async():
    pass
```

### Getting Help

- Review this guide
- Check test examples in each module's `tests/` directory
- Review `conftest.py` for available fixtures
- Ask in team chat or open an issue

---

## Examples

### Example 1: Simple Unit Test

```python
# generator/tests/test_utils.py
import pytest
from generator.utils import format_code

def test_format_code_with_valid_python():
    """Test that valid Python code is formatted correctly."""
    code = "def foo():\n  pass"
    result = format_code(code, language="python")
    assert "def foo():" in result
    assert result.endswith("\n")

def test_format_code_with_invalid_syntax():
    """Test that invalid code raises appropriate error."""
    code = "def foo("  # Invalid syntax
    with pytest.raises(SyntaxError):
        format_code(code, language="python")
```

### Example 2: Integration Test with Redis

```python
# server/tests/test_cache.py
import pytest
from server.cache import RedisCache

@pytest.mark.integration
@pytest.mark.requires_redis
async def test_redis_set_and_get():
    """Test Redis cache set and get operations."""
    cache = RedisCache()
    
    await cache.set("test_key", "test_value", ttl=60)
    result = await cache.get("test_key")
    
    assert result == "test_value"
    
    # Cleanup
    await cache.delete("test_key")
```

### Example 3: Test with Heavy Dependencies

```python
# omnicore_engine/tests/test_array_backend.py
import pytest

@pytest.mark.heavy
def test_numpy_array_operations():
    """Test array operations using numpy."""
    import numpy as np  # Import inside test
    
    arr = np.array([1, 2, 3, 4, 5])
    result = arr * 2
    
    assert np.array_equal(result, np.array([2, 4, 6, 8, 10]))
```

### Example 4: Async Test with Mocking

```python
# generator/tests/test_api_client.py
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_api_client_get():
    """Test API client GET request."""
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_response = AsyncMock()
        mock_response.json.return_value = {"status": "success"}
        mock_get.return_value.__aenter__.return_value = mock_response
        
        client = APIClient()
        result = await client.get("/endpoint")
        
        assert result["status"] == "success"
```

---

## Summary

**Key Takeaways:**

1. ✅ Place tests in module-specific `tests/` directories
2. ✅ Use appropriate markers (`@pytest.mark.unit`, `@pytest.mark.heavy`, etc.)
3. ✅ Write descriptive test names
4. ✅ Keep tests fast by default (< 1 minute for unit tests)
5. ✅ Mock external dependencies
6. ❌ Never create test files in repository root
7. ❌ Don't import heavy libraries unless necessary
8. ❌ Don't test implementation details

**Quick Reference:**

```bash
# Run fast tests
pytest -m "not (heavy or slow or integration)"

# Run specific module
pytest generator/tests/

# Run with coverage
pytest --cov=generator

# Run in parallel
pytest -n auto
```

---

**Document maintained by**: Code Factory Team  
**Last reviewed**: 2026-02-05  
**Next review**: 2026-03-05
