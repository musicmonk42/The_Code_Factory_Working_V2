<!-- Copyright © 2025 Novatrax Labs LLC. All Rights Reserved. -->

# Testing Guidelines - The Code Factory

**Version**: 1.1  
**Last Updated**: 2026-02-11

## Table of Contents

1. [Overview](#overview)
2. [Test Organization](#test-organization)
3. [Writing Tests](#writing-tests)
4. [Test Markers](#test-markers)
5. [Running Tests](#running-tests)
6. [Best Practices](#best-practices)
7. [CI/CD Integration](#cicd-integration)
8. [Load Testing](#load-testing)

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
# Fast unit tests (skip heavy, slow, and integration tests)
pytest -m "not (heavy or slow or integration)"

# Skip only heavy tests (used in CI)
pytest -m "not heavy"

# Run all tests (full suite)
pytest

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
- **Used in CI/CD**

#### Tier 3: Full Suite (< 15 minutes)
```bash
pytest
```
- Includes all tests including heavy ones
- Run periodically or before releases
- Requires all dependencies (numpy, pandas, etc.)

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

## Load Testing

The Code Factory includes K6-based load testing to verify scalability under increasing user loads.

### Overview

Load tests simulate multiple concurrent users making API requests to test the system's performance, reliability, and scalability. The tests use staged ramp-up to gradually increase load and verify that response times and error rates remain within acceptable thresholds.

### Prerequisites

To run load tests locally, you need:
- K6 installed (see [Installation](#installing-k6))
- Python 3.11+
- Redis running (via Docker or local install)
- API server running

### Installing k6

#### macOS
```bash
brew install k6
```

#### Linux (Debian/Ubuntu)
```bash
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update
sudo apt-get install k6
```

#### Windows
```powershell
choco install k6
```

For other installation methods, see: https://k6.io/docs/get-started/installation/

### Running Load Tests Locally

#### 1. Start Required Services

```bash
# Start Redis
docker-compose up -d redis

# Or use local Redis
redis-server
```

#### 2. Start the API Server

```bash
# Start the server
python server/run.py --host 0.0.0.0 --port 8000

# Or with auto-reload for development
python server/run.py --reload
```

#### 3. Run the Load Test

```bash
# Run with default settings (http://localhost:8000, max 100 VUs)
k6 run loadtest.js

# Run against a different URL
k6 run -e API_URL=http://myserver:8000 loadtest.js

# Run with custom maximum virtual users (adjusts all stages proportionally)
k6 run -e MAX_VUS=200 loadtest.js

# Run with custom URL and VUs
k6 run -e API_URL=http://myserver:8000 -e MAX_VUS=50 loadtest.js

# Run with JSON output for analysis
k6 run loadtest.js --out json=results.json

# Note: The loadtest-summary.json file is automatically generated by the
# handleSummary() function in loadtest.js - no additional flags needed
```

### Load Test Configuration

The load test script (`loadtest.js`) tests these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check endpoint |
| `/api/v1/generate` | POST | Code generation (main workload) |
| `/api/v1/generations` | GET | List generations |

#### Load Profile

The test uses staged ramp-up with configurable maximum virtual users (VUs):

1. **Warm-up**: 0 → 10% of MAX_VUS over 30 seconds
2. **Sustain**: Hold at 10% for 1 minute
3. **Scale**: 10% → 50% of MAX_VUS over 1 minute
4. **Sustain**: Hold at 50% for 2 minutes
5. **Scale**: 50% → 100% of MAX_VUS over 1 minute
6. **Peak**: Hold at 100% (MAX_VUS) for 2 minutes
7. **Ramp-down**: 100% → 0 VUs over 30 seconds

**Default MAX_VUS**: 100 (can be customized via `-e MAX_VUS=N`)  
**Total duration**: ~8 minutes

#### Thresholds

Tests fail if these thresholds are exceeded:

- **p95 Response Time**: < 500ms (95th percentile)
- **Error Rate**: < 1% (request failure rate)

### Interpreting Results

After running a load test, k6 displays a summary:

```
✓ health check status is 200
✓ generate status is 200 or 202
✓ list generations status is 200

checks.........................: 100.00% ✓ 45000  ✗ 0
data_received..................: 15 MB   31 kB/s
data_sent......................: 7.5 MB  15 kB/s
http_req_blocked...............: avg=1.2ms   min=1µs     med=4µs     max=500ms  p(90)=8µs    p(95)=12µs
http_req_duration..............: avg=120ms   min=50ms    med=100ms   max=800ms  p(90)=200ms  p(95)=300ms
http_req_failed................: 0.00%   ✓ 0      ✗ 45000
http_reqs......................: 45000   91.836734/s
iteration_duration.............: avg=1.2s    min=1.05s   med=1.15s   max=2s     p(90)=1.3s   p(95)=1.5s
iterations.....................: 15000   30.612245/s
vus............................: 100     min=0    max=100
vus_max........................: 100     min=100  max=100
```

#### Key Metrics

- **http_req_duration**: Response time statistics
  - `p(95)`: 95th percentile - should be < 500ms
  - `avg`: Average response time
  - `max`: Maximum response time observed
- **http_req_failed**: Percentage of failed requests - should be < 1%
- **http_reqs**: Total requests made and requests per second
- **checks**: Percentage of successful validation checks

#### What Good Results Look Like

✅ **Pass criteria:**
- p95 response time < 500ms
- Error rate < 1%
- All checks passing (100%)
- Consistent performance across all stages

❌ **Warning signs:**
- p95 > 500ms: Performance degradation under load
- Error rate > 1%: System stability issues
- Increasing response times: Resource saturation
- Failed checks: API returning invalid responses

### Running Load Tests in CI/CD

Load tests are configured to run:
- **Weekly**: Every Monday at 3 AM UTC
- **Manually**: Via GitHub Actions `workflow_dispatch`

#### Triggering Manual Load Tests

1. Go to GitHub Actions in the repository
2. Select "Load Test - K6" workflow
3. Click "Run workflow"
4. (Optional) Customize parameters:
   - **target_url**: URL to test (default: http://localhost:8000)
   - **vus**: Max virtual users (default: 50)
5. Click "Run workflow" to start

#### Viewing CI Results

After a workflow run:
1. Go to the workflow run page
2. Download the artifacts:
   - `loadtest-results.json`: Detailed per-request data
   - `loadtest-summary.json`: Summary statistics (automatically generated by handleSummary() in loadtest.js)
3. Review the test summary in the workflow logs

**Note**: The `loadtest-summary.json` file is now generated by a custom `handleSummary()` function in `loadtest.js`, which ensures threshold pass/fail booleans are correctly serialized. This replaces the deprecated `--summary-export` flag which had a bug that inverted threshold values.

### Customizing Load Tests

#### Adjusting Load Profile

Edit `loadtest.js` to modify the load profile:

```javascript
export const options = {
    stages: [
        { duration: '30s', target: 10 },   // Ramp to 10 users
        { duration: '2m', target: 50 },    // Ramp to 50 users
        // Add more stages as needed
    ],
};
```

#### Adjusting Thresholds

Modify thresholds in `loadtest.js`:

```javascript
thresholds: {
    'http_req_duration': ['p(95)<1000'],  // Increase to 1 second
    'http_req_failed': ['rate<0.05'],     // Allow 5% failure rate
},
```

#### Testing Different Endpoints

Add new test functions in `loadtest.js`:

```javascript
function testMyEndpoint() {
    const response = http.get(`${API_URL}/my/endpoint`, {
        tags: { type: 'custom' },
    });
    
    check(response, {
        'my endpoint status is 200': (r) => r.status === 200,
    });
}
```

### Best Practices

1. **Start Small**: Begin with low VU counts and gradually increase
2. **Monitor Resources**: Watch CPU, memory, and Redis during tests
3. **Test Realistic Scenarios**: Use payloads similar to production usage
4. **Run Regularly**: Schedule weekly tests to catch performance regressions
5. **Baseline Metrics**: Establish baseline performance for comparison
6. **Test Different Scales**: Test at different load levels (small/medium/large)

### Troubleshooting

#### Server Not Ready
```bash
# Problem: Load test fails because server isn't ready
# Solution: Wait for health check before running test
while ! curl -s http://localhost:8000/health > /dev/null; do
    echo "Waiting for server..."
    sleep 2
done
k6 run loadtest.js
```

#### High Error Rates
```bash
# Problem: Many requests fail during load test
# Possible causes:
# 1. Server not scaled properly (increase workers)
# 2. Redis connection pool exhausted
# 3. Rate limiting triggered (check Flask-Limiter config)
# 4. Database connection limits reached
```

#### Slow Response Times
```bash
# Problem: p95 > 500ms
# Check:
# 1. Redis latency: redis-cli --latency
# 2. CPU usage: top or htop
# 3. Database query times
# 4. Network latency if testing remote server
```

### Related Documentation

- [SCALABLE_ARCHITECTURE.md](./SCALABLE_ARCHITECTURE.md) - Scaling tiers and architecture
- [DEPLOYMENT.md](../DEPLOYMENT.md) - Production deployment guidelines
- [ci_compatibility.md](./ci_compatibility.md) - CI/CD configuration

---

**Document maintained by**: Code Factory Team  
**Last reviewed**: 2026-02-05  
**Next review**: 2026-03-05
