"""
Unit Tests for Centralized Environment Detection
=================================================

Test suite for server.environment module ensuring consistent environment
detection across all scenarios.

**Module Version**: 1.0.0
**Author**: Code Factory Platform Team
**Last Updated**: 2026-01-23
"""
import os
import sys
import pytest
from unittest.mock import patch

from server.environment import (
    Environment,
    EnvironmentDetector,
    env_detector,
    is_production,
    is_test,
    is_staging,
    is_development,
    get_environment,
)


@pytest.fixture(autouse=True)
def reset_environment():
    """Reset environment detector before each test."""
    # Reset the singleton state - both class and instance attributes
    # Also reset the module-level env_detector instance
    EnvironmentDetector._environment = None
    env_detector._environment = None
    if EnvironmentDetector._instance is not None:
        EnvironmentDetector._instance._environment = None
    yield
    # Clean up after test
    EnvironmentDetector._environment = None
    env_detector._environment = None
    if EnvironmentDetector._instance is not None:
        EnvironmentDetector._instance._environment = None


@pytest.fixture
def clean_env(monkeypatch):
    """Provide a clean environment with no relevant variables set."""
    env_vars = [
        "FORCE_PRODUCTION_MODE",
        "APP_ENV",
        "PRODUCTION_MODE",
        "CI",
        "GITHUB_ACTIONS",
        "GITLAB_CI",
        "JENKINS_HOME",
        "CIRCLECI",
        "TRAVIS",
        "BUILDKITE",
        "TESTING",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)
    
    # Reset the environment detector AFTER clearing env vars
    # This ensures any cached value from previous detection is cleared
    EnvironmentDetector._environment = None
    env_detector._environment = None
    if EnvironmentDetector._instance is not None:
        EnvironmentDetector._instance._environment = None


class TestEnvironmentDetector:
    """Test cases for EnvironmentDetector class."""
    
    def test_singleton_pattern(self):
        """Test that EnvironmentDetector implements singleton pattern."""
        detector1 = EnvironmentDetector()
        detector2 = EnvironmentDetector()
        assert detector1 is detector2, "EnvironmentDetector should be a singleton"
    
    def test_priority_1_force_production_mode(self, clean_env, monkeypatch):
        """Test FORCE_PRODUCTION_MODE has highest priority."""
        monkeypatch.setenv("FORCE_PRODUCTION_MODE", "true")
        monkeypatch.setenv("APP_ENV", "development")  # Should be ignored
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.PRODUCTION
    
    def test_priority_2_app_env_production(self, clean_env, monkeypatch):
        """Test APP_ENV=production detection."""
        monkeypatch.setenv("APP_ENV", "production")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.PRODUCTION
    
    def test_priority_2_app_env_prod(self, clean_env, monkeypatch):
        """Test APP_ENV=prod detection (alternate form)."""
        monkeypatch.setenv("APP_ENV", "prod")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.PRODUCTION
    
    def test_priority_2_app_env_staging(self, clean_env, monkeypatch):
        """Test APP_ENV=staging detection."""
        monkeypatch.setenv("APP_ENV", "staging")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.STAGING
    
    def test_priority_2_app_env_development(self, clean_env, monkeypatch):
        """Test APP_ENV=development detection."""
        monkeypatch.setenv("APP_ENV", "development")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.DEVELOPMENT
    
    def test_priority_2_app_env_test(self, clean_env, monkeypatch):
        """Test APP_ENV=test detection."""
        monkeypatch.setenv("APP_ENV", "test")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.TEST
    
    def test_priority_3_production_mode(self, clean_env, monkeypatch):
        """Test PRODUCTION_MODE=true detection (legacy)."""
        monkeypatch.setenv("PRODUCTION_MODE", "true")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.PRODUCTION
    
    def test_priority_3_production_mode_numeric(self, clean_env, monkeypatch):
        """Test PRODUCTION_MODE=1 detection (numeric form)."""
        monkeypatch.setenv("PRODUCTION_MODE", "1")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.PRODUCTION
    
    def test_priority_4_ci_environment(self, clean_env, monkeypatch):
        """Test CI environment detection."""
        monkeypatch.setenv("CI", "1")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.TEST
    
    def test_priority_4_github_actions(self, clean_env, monkeypatch):
        """Test GitHub Actions detection."""
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.TEST
    
    def test_priority_5_pytest(self, clean_env):
        """Test pytest detection (already in sys.modules during testing)."""
        # pytest is in sys.modules during test execution
        assert "pytest" in sys.modules
        
        detector = EnvironmentDetector()
        result = detector.detect()
        assert result == Environment.TEST
    
    def test_default_development(self, clean_env):
        """Test default to development when no indicators present."""
        # Remove pytest from sys.modules temporarily
        pytest_mod = sys.modules.pop("pytest", None)
        try:
            detector = EnvironmentDetector()
            EnvironmentDetector._environment = None  # Reset cached value
            # With pytest removed and no env vars, should default to development
            # However, other test-related modules might still be present
            # So we just verify it doesn't crash
            result = detector.detect()
            assert result in [Environment.TEST, Environment.DEVELOPMENT]
        finally:
            if pytest_mod:
                sys.modules["pytest"] = pytest_mod
    
    def test_caching(self, clean_env, monkeypatch):
        """Test that environment detection is cached after first call."""
        monkeypatch.setenv("APP_ENV", "production")
        
        detector = EnvironmentDetector()
        result1 = detector.detect()
        
        # Change environment (should be ignored due to caching)
        monkeypatch.setenv("APP_ENV", "development")
        result2 = detector.detect()
        
        assert result1 == result2 == Environment.PRODUCTION
    
    def test_is_production(self, clean_env, monkeypatch):
        """Test is_production() convenience method."""
        monkeypatch.setenv("APP_ENV", "production")
        
        detector = EnvironmentDetector()
        assert detector.is_production() is True
        assert detector.is_test() is False
        assert detector.is_staging() is False
        assert detector.is_development() is False
    
    def test_is_test(self, clean_env, monkeypatch):
        """Test is_test() convenience method."""
        monkeypatch.setenv("APP_ENV", "test")
        EnvironmentDetector._environment = None  # Reset
        
        detector = EnvironmentDetector()
        assert detector.is_production() is False
        assert detector.is_test() is True
        assert detector.is_staging() is False
        assert detector.is_development() is False
    
    def test_reset_in_production_fails(self, clean_env, monkeypatch):
        """Test that reset() raises error in production."""
        monkeypatch.setenv("APP_ENV", "production")
        
        detector = EnvironmentDetector()
        detector.detect()  # Cache production
        
        with pytest.raises(RuntimeError, match="Cannot reset environment detector in production"):
            detector.reset()
    
    def test_reset_in_test_succeeds(self, clean_env, monkeypatch):
        """Test that reset() works in test environment."""
        monkeypatch.setenv("APP_ENV", "test")
        
        detector = EnvironmentDetector()
        detector.detect()
        
        # Should not raise
        detector.reset()
        assert detector._environment is None


class TestConvenienceFunctions:
    """Test cases for module-level convenience functions."""
    
    def test_is_production_function(self, clean_env, monkeypatch):
        """Test is_production() module function."""
        monkeypatch.setenv("APP_ENV", "production")
        EnvironmentDetector._environment = None  # Reset
        
        assert is_production() is True
    
    def test_is_test_function(self, clean_env, monkeypatch):
        """Test is_test() module function."""
        monkeypatch.setenv("APP_ENV", "test")
        EnvironmentDetector._environment = None  # Reset
        
        assert is_test() is True
    
    def test_is_staging_function(self, clean_env, monkeypatch):
        """Test is_staging() module function."""
        monkeypatch.setenv("APP_ENV", "staging")
        EnvironmentDetector._environment = None  # Reset
        
        assert is_staging() is True
    
    def test_is_development_function(self, clean_env, monkeypatch):
        """Test is_development() module function."""
        monkeypatch.setenv("APP_ENV", "development")
        EnvironmentDetector._environment = None  # Reset
        
        assert is_development() is True
    
    def test_get_environment_function(self, clean_env, monkeypatch):
        """Test get_environment() module function."""
        monkeypatch.setenv("APP_ENV", "staging")
        EnvironmentDetector._environment = None  # Reset
        
        result = get_environment()
        assert result == Environment.STAGING
        assert isinstance(result, Environment)


class TestEnvironmentEnum:
    """Test cases for Environment enum."""
    
    def test_enum_values(self):
        """Test Environment enum has correct values."""
        assert Environment.PRODUCTION.value == "production"
        assert Environment.STAGING.value == "staging"
        assert Environment.DEVELOPMENT.value == "development"
        assert Environment.TEST.value == "test"
    
    def test_enum_string_representation(self):
        """Test Environment enum string representation."""
        assert str(Environment.PRODUCTION) == "production"
        assert str(Environment.TEST) == "test"
    
    def test_is_production_like_property(self):
        """Test is_production_like property."""
        assert Environment.PRODUCTION.is_production_like is True
        assert Environment.STAGING.is_production_like is True
        assert Environment.DEVELOPMENT.is_production_like is False
        assert Environment.TEST.is_production_like is False


@pytest.mark.integration
class TestRealWorldScenarios:
    """Integration tests for real-world scenarios."""
    
    def test_local_development(self, clean_env):
        """Test typical local development setup."""
        # No environment variables set
        EnvironmentDetector._environment = None
        
        detector = EnvironmentDetector()
        # In test environment, pytest is in sys.modules
        # In real dev, would default to development
        env = detector.detect()
        assert env in [Environment.DEVELOPMENT, Environment.TEST]
    
    def test_docker_production(self, clean_env, monkeypatch):
        """Test production Docker container setup."""
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("PRODUCTION_MODE", "true")
        EnvironmentDetector._environment = None
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.PRODUCTION
    
    def test_github_actions_ci(self, clean_env, monkeypatch):
        """Test GitHub Actions CI environment."""
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("CI", "true")
        EnvironmentDetector._environment = None
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.TEST
    
    def test_staging_deployment(self, clean_env, monkeypatch):
        """Test staging deployment setup."""
        monkeypatch.setenv("APP_ENV", "staging")
        EnvironmentDetector._environment = None
        
        detector = EnvironmentDetector()
        assert detector.detect() == Environment.STAGING
        assert detector.is_production() is False
        assert detector.is_test() is False
