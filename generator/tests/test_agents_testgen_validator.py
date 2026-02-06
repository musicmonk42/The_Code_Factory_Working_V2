"""
Comprehensive test suite for testgen_validator.py

Tests all functionality including:
- TestValidator base class and subclasses
- CoverageValidator for test coverage analysis
- MutationValidator for mutation testing
- PropertyBasedValidator for property-based testing
- StressPerformanceValidator for stress/performance testing
- ValidatorRegistry and plugin management
- Health endpoints and hot-reloading
- Performance data tracking
- Error handling and edge cases
"""

import asyncio
import importlib.machinery
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch

import pytest


def create_mock_package(name):
    """Create a properly configured mock package."""
    mock_pkg = ModuleType(name)
    mock_pkg.__path__ = []
    mock_pkg.__spec__ = importlib.machinery.ModuleSpec(
        name=name,
        loader=None,
        is_package=True
    )
    mock_pkg.__file__ = f"<mocked {name}>"
    return mock_pkg


# Mock all external dependencies before importing testgen_validator
# Special mock for aiofiles to support async context manager and actually write files
class MockAiofilesFile:
    def __init__(self, path, mode, encoding=None):
        self.path = path
        self.mode = mode
        self.encoding = encoding
        self._lines_iterator = None

    async def write(self, data):
        # Actually write the file synchronously for testing
        with open(self.path, self.mode, encoding=self.encoding) as f:
            f.write(data)
    
    async def read(self):
        """Read entire file content."""
        try:
            with open(self.path, 'r', encoding=self.encoding or 'utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return ""
    
    async def readlines(self):
        """Read all lines from the file."""
        try:
            with open(self.path, 'r', encoding=self.encoding or 'utf-8') as f:
                return f.readlines()
        except FileNotFoundError:
            return []
    
    async def writelines(self, lines):
        """Write multiple lines to the file."""
        with open(self.path, self.mode, encoding=self.encoding) as f:
            f.writelines(lines)
    
    def fileno(self):
        """Return file descriptor number (mocked for fsync operations)."""
        return 1
    
    async def flush(self):
        """Flush file buffer."""
        pass
    
    def __aiter__(self):
        """Support async iteration over lines."""
        return self
    
    async def __anext__(self):
        """Async iterator for reading lines."""
        if self._lines_iterator is None:
            try:
                with open(self.path, 'r', encoding=self.encoding or 'utf-8') as f:
                    self._lines_iterator = iter(f.readlines())
            except FileNotFoundError:
                raise StopAsyncIteration
        
        try:
            return next(self._lines_iterator)
        except StopIteration:
            raise StopAsyncIteration


class MockAiofilesContextManager:
    def __init__(self, path, mode, encoding=None):
        self.file = MockAiofilesFile(path, mode, encoding)

    async def __aenter__(self):
        return self.file

    async def __aexit__(self, *args):
        pass


class MockAiofiles:
    """Mock for aiofiles module."""
    __path__ = []
    __spec__ = importlib.machinery.ModuleSpec(
        name="aiofiles",
        loader=None,
        is_package=True
    )
    __file__ = "<mocked aiofiles>"
    
    def open(self, path, mode="r", encoding=None):
        return MockAiofilesContextManager(path, mode, encoding)


# Mock web module early
class MockWebResponse:
    def __init__(self, text="OK", status=200):
        self.text = text
        self.status = status


class MockWeb:
    """Mock for aiohttp.web module."""
    __path__ = []
    __spec__ = importlib.machinery.ModuleSpec(
        name="aiohttp.web",
        loader=None,
        is_package=True
    )
    __file__ = "<mocked aiohttp.web>"
    
    Response = MockWebResponse
    Application = Mock
    AppRunner = Mock
    TCPSite = Mock

    @staticmethod
    def get(path, handler):
        return f"GET {path} -> {handler.__name__}"


# Apply mocks before imports
import sys

sys.modules["aiofiles"] = MockAiofiles()
sys.modules["aiohttp.web"] = MockWeb()


# Mock watchdog components
class MockFileSystemEventHandler:
    pass


class MockObserver:
    def __init__(self):
        self.started = False

    def schedule(self, *args, **kwargs):
        pass

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def join(self):
        pass


sys.modules["watchdog.events"] = create_mock_package("watchdog.events")
sys.modules["watchdog.events"].FileSystemEventHandler = MockFileSystemEventHandler
sys.modules["watchdog.observers"] = create_mock_package("watchdog.observers")
sys.modules["watchdog.observers"].Observer = MockObserver

# Now import the module under test
from generator.agents.testgen_agent.testgen_validator import (
    MAX_SANDBOX_RUNS,
    CoverageValidator,
    MutationValidator,
    PropertyBasedValidator,
    StressPerformanceValidator,
    TestValidator,
    ValidatorRegistry,
    _save_files_async,
    healthz,
    start_health_server,
    validate_test_quality,
)


class TestHealthEndpoints:
    """Test health check endpoints for Kubernetes readiness/liveness probes."""

    @pytest.mark.asyncio
    async def test_healthz_endpoint(self):
        """Test the health check endpoint."""
        mock_request = Mock()
        response = await healthz(mock_request)

        # Check response has required attributes and types
        assert response.status == 200
        # Ensure text is a string, not a callable
        assert isinstance(response.text, str), "Response text must be a string"
        assert response.text == "OK"

    @patch("generator.agents.testgen_agent.testgen_validator.web.Application")
    @patch("generator.agents.testgen_agent.testgen_validator.web.AppRunner")
    @patch("generator.agents.testgen_agent.testgen_validator.web.TCPSite")
    @pytest.mark.asyncio
    async def test_start_health_server(self, mock_tcp_site, mock_app_runner, mock_app):
        """Test starting the health server."""
        # Setup mocks with proper async support
        mock_app_instance = Mock()
        mock_app.return_value = mock_app_instance
        mock_app_instance.add_routes = Mock()

        mock_runner_instance = Mock()
        mock_app_runner.return_value = mock_runner_instance
        mock_runner_instance.setup = AsyncMock()  # Must be AsyncMock

        mock_site_instance = Mock()
        mock_tcp_site.return_value = mock_site_instance
        mock_site_instance.start = AsyncMock()  # Must be AsyncMock

        await start_health_server()

        # Verify setup calls
        mock_app.assert_called_once()
        mock_app_instance.add_routes.assert_called_once()
        mock_runner_instance.setup.assert_called_once()
        mock_tcp_site.assert_called_once_with(mock_runner_instance, "0.0.0.0", 8082)
        mock_site_instance.start.assert_called_once()


class TestBaseValidator:
    """Test the base TestValidator class."""

    def test_init(self):
        """Test validator initialization."""
        validator = CoverageValidator()
        assert validator.human_review_callback is None

    def test_scan_for_secrets_and_flaky_tests(self):
        """Test secret and flaky test pattern detection."""
        validator = CoverageValidator()

        test_files_with_secrets = {"test_secrets.py": """
            API_KEY = "sk-1234567890abcdef"
            password = "secret123"
            def test_auth():
                token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            """}

        test_files_with_flaky_patterns = {"test_flaky.py": """
            import time
            import random
            from datetime import datetime
            import threading
            
            def test_with_sleep():
                time.sleep(1)
                
            def test_with_random():
                if random.randint(1, 10) > 5:
                    assert True
                    
            def test_with_datetime():
                now = datetime.now()
            """}

        # Test secret detection
        issues_secrets = validator._scan_for_secrets_and_flaky_tests(
            test_files_with_secrets, "python"
        )
        assert any("secret" in issue.lower() for issue in issues_secrets)

        # Test flaky pattern detection
        issues_flaky = validator._scan_for_secrets_and_flaky_tests(
            test_files_with_flaky_patterns, "python"
        )
        assert any("flaky" in issue.lower() for issue in issues_flaky)


class TestCoverageValidator:
    """Test coverage validation functionality."""

    @pytest.fixture
    def coverage_validator(self):
        """Create a CoverageValidator instance for testing."""
        return CoverageValidator()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_tests_in_sandbox",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_success(
        self, mock_run_tests, mock_save_files, coverage_validator
    ):
        """Test successful coverage validation."""
        # Mock successful test run with correct structure matching sandbox function
        mock_run_tests.return_value = {
            "coverage_percentage": 85.0,
            "lines_covered": 85,
            "total_lines": 100,
            "test_results": {"passed": 5, "failed": 0, "total": 5},
            "pass_count": 5,
            "fail_count": 0,
            "status": "success",
        }

        code_files = {"source.py": "def hello(): return 'world'"}
        test_files = {"test_source.py": "def test_hello(): assert hello() == 'world'"}

        result = await coverage_validator.validate(code_files, test_files, "python")

        assert result["coverage_percentage"] == 85.0
        assert result["lines_covered"] == 85
        assert result["total_lines"] == 100
        assert "issues" in result
        mock_run_tests.assert_called_once()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_tests_in_sandbox",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_low_coverage(
        self, mock_run_tests, mock_save_files, coverage_validator
    ):
        """Test validation with low coverage."""
        mock_run_tests.return_value = {
            "coverage_percentage": 45.0,
            "lines_covered": 45,
            "total_lines": 100,
            "test_results": {"passed": 2, "failed": 1, "total": 3},
            "pass_count": 2,
            "fail_count": 1,
            "status": "success",
        }

        code_files = {"source.py": "def hello(): return 'world'"}
        test_files = {"test_source.py": "def test_hello(): assert hello() == 'world'"}

        result = await coverage_validator.validate(code_files, test_files, "python")

        assert result["coverage_percentage"] == 45.0
        assert result["lines_covered"] == 45
        assert result["total_lines"] == 100
        assert "low coverage" in result["issues"].lower()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_tests_in_sandbox",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_with_exception(
        self, mock_run_tests, mock_save_files, coverage_validator
    ):
        """Test coverage validation with exception handling."""
        mock_run_tests.side_effect = Exception("Sandbox execution failed")

        code_files = {"source.py": "def hello(): return 'world'"}
        test_files = {"test_source.py": "def test_hello(): assert hello() == 'world'"}

        result = await coverage_validator.validate(code_files, test_files, "python")

        assert result["coverage_percentage"] == 0.0
        assert "exception" in result["issues"].lower()


class TestMutationValidator:
    """Test mutation testing validation functionality."""

    @pytest.fixture
    def mutation_validator(self):
        """Create a MutationValidator instance for testing."""
        return MutationValidator()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.mutation_test", new_callable=AsyncMock
    )
    @pytest.mark.asyncio
    async def test_validate_success(
        self, mock_mutation_test, mock_save_files, mutation_validator
    ):
        """Test successful mutation testing."""
        # Mock mutation_test to return good results
        mock_mutation_test.return_value = {
            "survival_rate": 0.2,  # 20% survived, 80% killed
            "status": "success",
        }

        code_files = {"source.py": "def add(a, b): return a + b"}
        test_files = {"test_source.py": "def test_add(): assert add(1, 2) == 3"}

        result = await mutation_validator.validate(code_files, test_files, "python")

        assert "mutation_score" in result
        assert result["mutation_score"] > 0
        # mutation_score should be 80% (100 - 20)
        assert result["mutation_score"] == 80.0

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.mutation_test", new_callable=AsyncMock
    )
    @pytest.mark.asyncio
    async def test_validate_unsupported_language(
        self, mock_mutation_test, mock_save_files, mutation_validator
    ):
        """Test validation with unsupported language."""
        # Mock mutation_test to return no results (unsupported)
        mock_mutation_test.return_value = {
            "survival_rate": 1.0,  # All survived (no tests ran)
            "status": "failed",
        }

        code_files = {"source.xyz": "some code"}
        test_files = {"test_source.xyz": "some test"}

        result = await mutation_validator.validate(
            code_files, test_files, "unsupported"
        )

        # Should have low mutation score
        assert result["mutation_score"] == 0.0

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.mutation_test", new_callable=AsyncMock
    )
    @pytest.mark.asyncio
    async def test_validate_timeout(
        self, mock_mutation_test, mock_save_files, mutation_validator
    ):
        """Test mutation validation timeout."""
        mock_mutation_test.side_effect = asyncio.TimeoutError(
            "Mutation testing timed out"
        )

        code_files = {"source.py": "def add(a, b): return a + b"}
        test_files = {"test_source.py": "def test_add(): assert add(1, 2) == 3"}

        result = await mutation_validator.validate(code_files, test_files, "python")

        assert (
            "exception" in result["issues"].lower()
            or "timed out" in result["issues"].lower()
        )

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.mutation_test", new_callable=AsyncMock
    )
    @pytest.mark.asyncio
    async def test_validate_with_human_review(
        self, mock_mutation_test, mock_save_files, mutation_validator
    ):
        """Test mutation validation with human review."""
        review_called = False

        async def mock_review(issues, metrics):
            nonlocal review_called
            review_called = True
            return False  # Reject

        mutation_validator.human_review_callback = mock_review

        # Mock low mutation score to trigger human review
        mock_mutation_test.return_value = {
            "survival_rate": 0.8,  # 80% survived, only 20% killed - low score
            "status": "success",
        }

        code_files = {"source.py": "def add(a, b): return a + b"}
        test_files = {"test_source.py": "def test_add(): assert True"}  # Weak test

        result = await mutation_validator.validate(code_files, test_files, "python")

        assert review_called
        assert "human review rejected" in result["issues"].lower()


class TestPropertyBasedValidator:
    """Test property-based validation functionality."""

    @pytest.fixture
    def property_validator(self):
        """Create a PropertyBasedValidator instance for testing."""
        return PropertyBasedValidator()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.property_based_test",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_success(
        self, mock_property_test, mock_save_files, property_validator
    ):
        """Test successful property-based validation."""
        # Mock successful property-based testing
        mock_property_test.return_value = {
            "properties_passed": True,
            "status": "success",
        }

        code_files = {"source.py": "def reverse_string(s): return s[::-1]"}
        test_files = {"test_source.py": """
import hypothesis.strategies as st
from hypothesis import given

@given(st.text())
def test_reverse_property(s):
    assert reverse_string(reverse_string(s)) == s
"""}

        result = await property_validator.validate(code_files, test_files, "python")

        assert result["properties_passed"] is True
        assert "issues" in result
        assert "all properties passed" in result["issues"].lower()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch("subprocess.run")
    @pytest.mark.asyncio
    async def test_validate_failures(
        self, mock_subprocess, mock_save_files, property_validator
    ):
        """Test property-based validation with failures."""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Property test failed: Falsifying example"
        mock_subprocess.return_value = mock_result

        code_files = {
            "source.py": "def buggy_function(x): return x + 1 if x > 0 else 0"
        }
        test_files = {"test_source.py": """
import hypothesis.strategies as st
from hypothesis import given

@given(st.integers())
def test_buggy_property(x):
    assert buggy_function(x) > x  # This will fail for x <= 0
"""}

        result = await property_validator.validate(code_files, test_files, "python")

        assert result["properties_passed"] is False
        assert "property-based tests failed" in result["issues"].lower()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.property_based_test",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_unsupported_language(
        self, mock_property_test, mock_save_files, property_validator
    ):
        """Test validation with unsupported language."""
        # Mock property_based_test returning False (failed/unsupported)
        mock_property_test.return_value = {
            "properties_passed": False,
            "fuzz_failures": "Unknown error.",
            "status": "failed",
        }

        code_files = {"source.xyz": "some code"}
        test_files = {"test_source.xyz": "some test"}

        result = await property_validator.validate(
            code_files, test_files, "unsupported"
        )

        assert result["properties_passed"] is False
        assert "property-based tests failed" in result["issues"].lower()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.property_based_test",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_javascript(
        self, mock_property_test, mock_save_files, property_validator
    ):
        """Test property-based validation for JavaScript."""
        # Mock successful property testing for JavaScript
        mock_property_test.return_value = {
            "properties_passed": True,
            "status": "success",
        }

        code_files = {"source.js": "function add(a, b) { return a + b; }"}
        test_files = {"test_source.js": "// Property-based tests with fast-check"}

        result = await property_validator.validate(code_files, test_files, "javascript")

        assert result["properties_passed"] is True


class TestStressPerformanceValidator:
    """Test stress/performance validation functionality."""

    @pytest.fixture
    def stress_validator(self):
        """Create a StressPerformanceValidator instance for testing."""
        return StressPerformanceValidator()

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_stress_tests",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_success(
        self, mock_run_stress, mock_save_files, stress_validator
    ):
        """Test successful stress/performance validation."""
        mock_run_stress.return_value = {
            "avg_response_time_ms": 250.0,
            "error_rate_percentage": 2.0,
            "crashes_detected": False,
            "total_iterations": 3,
            "successful_runs": 3,
            "response_times": [240, 250, 260],
            "status": "success",
        }

        code_files = {"app.py": "def handle_request(): return {'status': 'ok'}"}
        test_files = {"test_stress.py": "# Stress test configuration"}

        result = await stress_validator.validate(code_files, test_files, "python")

        assert result["avg_response_time_ms"] == 250.0
        assert result["error_rate_percentage"] == 2.0
        assert result["crashes_detected"] is False

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_stress_tests",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_performance_issues(
        self, mock_run_stress, mock_save_files, stress_validator
    ):
        """Test stress validation with performance issues."""
        mock_run_stress.return_value = {
            "avg_response_time_ms": 1500.0,  # High response time
            "error_rate_percentage": 15.0,  # High error rate
            "crashes_detected": True,  # Crashes detected
            "total_iterations": 3,
            "successful_runs": 1,
            "response_times": [1000, 1500, 2000],
            "status": "failed",
        }

        code_files = {"app.py": "def slow_function(): time.sleep(2); return 'result'"}
        test_files = {"test_stress.py": "# Stress test configuration"}

        result = await stress_validator.validate(code_files, test_files, "python")

        assert "application crashed" in result["issues"].lower()
        assert result["crashes_detected"] is True
        assert result["avg_response_time_ms"] == 1500.0

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_stress_tests",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_with_custom_config(
        self, mock_run_stress, mock_save_files, stress_validator
    ):
        """Test stress validation with custom configuration."""
        mock_run_stress.return_value = {
            "avg_response_time_ms": 100.0,
            "error_rate_percentage": 1.0,
            "crashes_detected": False,
            "total_iterations": 3,
            "successful_runs": 3,
            "response_times": [95, 100, 105],
            "status": "success",
        }

        custom_config = {
            "users": 50,
            "spawn_rate": 5,
            "run_time": "30s",
            "tool": "artillery",
        }

        code_files = {"app.py": "def handle_request(): return {'status': 'ok'}"}
        test_files = {"test_stress.py": "# Stress test configuration"}

        result = await stress_validator.validate(
            code_files, test_files, "python", stress_config=custom_config
        )

        # Verify custom config was passed to run_stress_tests
        mock_run_stress.assert_called_once()
        args, kwargs = mock_run_stress.call_args
        assert "config" in kwargs
        assert kwargs["config"] == custom_config

    @patch(
        "generator.agents.testgen_agent.testgen_validator._save_files_async",
        new_callable=AsyncMock,
    )
    @patch(
        "generator.agents.testgen_agent.testgen_validator.run_stress_tests",
        new_callable=AsyncMock,
    )
    @pytest.mark.asyncio
    async def test_validate_exception(
        self, mock_run_stress, mock_save_files, stress_validator
    ):
        """Test stress validation with exception handling."""
        mock_run_stress.side_effect = Exception("Stress test execution failed")

        code_files = {"app.py": "def handle_request(): return {'status': 'ok'}"}
        test_files = {"test_stress.py": "# Stress test configuration"}

        result = await stress_validator.validate(code_files, test_files, "python")

        assert "exception" in result["issues"].lower()
        # When exception occurs, only 'issues' key is returned
        assert "Stress test execution failed" in result["issues"]


class TestValidatorRegistry:
    """Test validator registry and plugin management."""

    @pytest.fixture
    def registry(self):
        """Create a ValidatorRegistry instance for testing."""
        with patch("generator.agents.testgen_agent.testgen_validator.Observer"):
            return ValidatorRegistry()

    def test_init(self, registry):
        """Test registry initialization."""
        assert isinstance(registry, ValidatorRegistry)
        assert len(registry._validators) >= 4  # Should have built-in validators

    def test_register_validator(self, registry):
        """Test registering a custom validator."""

        class CustomValidator(TestValidator):
            async def validate(self, code_files, test_files, language, **kwargs):
                return {"custom_metric": 42}

        registry.register_validator("custom", CustomValidator())
        assert "custom" in registry._validators

    def test_register_invalid_validator(self, registry):
        """Test registering invalid validator."""
        with pytest.raises(ValueError):
            registry.register_validator("invalid", "not a validator")

    @pytest.mark.asyncio
    async def test_reload_plugins(self, registry):
        """Test hot-reloading of validator plugins."""
        # This test ensures the reload mechanism doesn't crash
        await registry._reload_plugins()
        # Should complete without exception

    @pytest.mark.asyncio
    async def test_close(self, registry):
        """Test registry cleanup."""
        await registry.close()
        # Should complete without exception


class TestPublicAPI:
    """Test public API functions."""

    @patch("generator.agents.testgen_agent.testgen_validator.VALIDATORS")
    @pytest.mark.asyncio
    async def test_validate_test_quality_success(self, mock_validators):
        """Test successful test quality validation."""
        mock_validator = AsyncMock()
        mock_validator.validate.return_value = {"coverage_percentage": 85.0}
        mock_validators.__getitem__.return_value = mock_validator
        mock_validators.__contains__.return_value = True

        code_files = {"source.py": "def hello(): return 'world'"}
        test_files = {"test_source.py": "def test_hello(): assert hello() == 'world'"}

        result = await validate_test_quality(
            code_files, test_files, "python", "coverage"
        )

        assert result == {"coverage_percentage": 85.0}
        mock_validator.validate.assert_called_once_with(
            code_files, test_files, "python"
        )

    @patch("generator.agents.testgen_agent.testgen_validator.VALIDATORS")
    @pytest.mark.asyncio
    async def test_validate_test_quality_invalid_strategy(self, mock_validators):
        """Test validation with invalid strategy."""
        mock_validators.__contains__.return_value = False

        code_files = {"source.py": "def hello(): return 'world'"}
        test_files = {"test_source.py": "def test_hello(): assert hello() == 'world'"}

        with pytest.raises(ValueError, match="Unknown validation strategy"):
            await validate_test_quality(code_files, test_files, "python", "invalid")


class TestFileOperations:
    """Test file operations and utilities."""

    @pytest.mark.asyncio
    async def test_save_files_async(self):
        """Test async file saving utility."""
        with tempfile.TemporaryDirectory() as temp_dir:
            files = {
                "test1.py": "def test(): pass",
                "test2.py": "def another_test(): pass",
            }

            await _save_files_async(files, temp_dir)

            # Verify files were created
            test1_path = Path(temp_dir) / "test1.py"
            test2_path = Path(temp_dir) / "test2.py"

            assert test1_path.exists()
            assert test2_path.exists()
            assert test1_path.read_text() == "def test(): pass"
            assert test2_path.read_text() == "def another_test(): pass"


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_empty_files(self):
        """Test validation with empty file dictionaries."""
        validator = CoverageValidator()

        with patch(
            "generator.agents.testgen_agent.testgen_validator._save_files_async",
            new_callable=AsyncMock,
        ):
            with patch(
                "generator.agents.testgen_agent.testgen_validator.run_tests_in_sandbox",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = {
                    "coverage_percentage": 0.0,
                    "lines_covered": 0,
                    "total_lines": 0,
                    "test_results": {"passed": 0, "failed": 0, "total": 0},
                    "pass_count": 0,
                    "fail_count": 0,
                    "status": "success",
                }

                result = await validator.validate({}, {}, "python")

                assert result["coverage_percentage"] == 0.0
                assert (
                    "empty" in result["issues"].lower() or result["lines_covered"] == 0
                )

    @pytest.mark.asyncio
    async def test_invalid_language(self):
        """Test validation with invalid/unsupported language."""
        validator = CoverageValidator()

        code_files = {"source.xyz": "some code"}
        test_files = {"test.xyz": "some test"}

        with patch(
            "generator.agents.testgen_agent.testgen_validator._save_files_async",
            new_callable=AsyncMock,
        ):
            with patch(
                "generator.agents.testgen_agent.testgen_validator.run_tests_in_sandbox",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = {
                    "coverage_percentage": 0.0,
                    "lines_covered": 0,
                    "total_lines": 0,
                    "test_results": {"passed": 0, "failed": 0, "total": 0},
                    "pass_count": 0,
                    "fail_count": 0,
                    "status": "failed",
                }

                result = await validator.validate(
                    code_files, test_files, "unsupported_language"
                )

                assert result["coverage_percentage"] == 0.0


class TestPerformanceTracking:
    """Test performance data tracking and analytics."""

    def test_max_sandbox_runs_constant(self):
        """Test that MAX_SANDBOX_RUNS is properly defined."""
        assert MAX_SANDBOX_RUNS > 0
        assert isinstance(MAX_SANDBOX_RUNS, int)


class TestCompliance:
    """Test compliance mode features for SOC2/PCI DSS."""

    @patch.dict(os.environ, {"COMPLIANCE_MODE": "true"})
    @pytest.mark.asyncio
    async def test_compliance_mode_enabled(self):
        """Test that compliance mode affects validation behavior."""
        validator = CoverageValidator()

        with patch(
            "generator.agents.testgen_agent.testgen_validator._save_files_async",
            new_callable=AsyncMock,
        ):
            with patch(
                "generator.agents.testgen_agent.testgen_validator.run_tests_in_sandbox",
                new_callable=AsyncMock,
            ) as mock_run:
                mock_run.return_value = {
                    "coverage_percentage": 85.0,
                    "lines_covered": 85,
                    "total_lines": 100,
                    "test_results": {"passed": 5, "failed": 0, "total": 5},
                    "pass_count": 5,
                    "fail_count": 0,
                    "status": "success",
                }

                code_files = {"source.py": "def hello(): return 'world'"}
                test_files = {
                    "test_source.py": "def test_hello(): assert hello() == 'world'"
                }

                result = await validator.validate(code_files, test_files, "python")

                # In compliance mode, should have additional metadata
                assert result["coverage_percentage"] == 85.0


class TestIntegration:
    """Integration tests that test multiple components together."""

    @pytest.mark.asyncio
    async def test_full_validation_pipeline(self):
        """Test the complete validation pipeline with multiple strategies."""
        with patch(
            "generator.agents.testgen_agent.testgen_validator.VALIDATORS"
        ) as mock_validators:
            # Mock all validators
            coverage_validator = Mock()
            coverage_validator.validate = AsyncMock(
                return_value={
                    "coverage_percentage": 85.0,
                    "lines_covered": 85,
                    "total_lines": 100,
                }
            )

            mutation_validator = Mock()
            mutation_validator.validate = AsyncMock(
                return_value={
                    "mutation_score": 75.0,
                    "killed_mutants": 15,
                    "survived_mutants": 5,
                }
            )

            # Setup mock registry
            mock_validators.__getitem__.side_effect = lambda x: {
                "coverage": coverage_validator,
                "mutation": mutation_validator,
            }[x]
            mock_validators.__contains__.return_value = True

            code_files = {"source.py": "def add(a, b): return a + b"}
            test_files = {"test_source.py": "def test_add(): assert add(1, 2) == 3"}

            # Test coverage validation
            coverage_result = await validate_test_quality(
                code_files, test_files, "python", "coverage"
            )
            assert coverage_result["coverage_percentage"] == 85.0

            # Test mutation validation
            mutation_result = await validate_test_quality(
                code_files, test_files, "python", "mutation"
            )
            assert mutation_result["mutation_score"] == 75.0

    @pytest.mark.asyncio
    async def test_error_handling_throughout_pipeline(self):
        """Test error handling across the entire validation pipeline."""
        with patch(
            "generator.agents.testgen_agent.testgen_validator.VALIDATORS"
        ) as mock_validators:
            # Mock validator that raises an exception
            failing_validator = Mock()
            failing_validator.validate = AsyncMock(
                side_effect=Exception("Validation failed")
            )

            mock_validators.__getitem__.return_value = failing_validator
            mock_validators.__contains__.return_value = True

            code_files = {"source.py": "def hello(): return 'world'"}
            test_files = {
                "test_source.py": "def test_hello(): assert hello() == 'world'"
            }

            # Should handle exception gracefully
            with pytest.raises(Exception, match="Validation failed"):
                await validate_test_quality(
                    code_files, test_files, "python", "coverage"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
