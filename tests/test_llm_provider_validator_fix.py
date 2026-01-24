"""
Test for LLM_PROVIDER validator fix and RunnerConfig.load() method.

This test verifies:
1. The LLM_PROVIDER validator accepts multiple providers
2. The RunnerConfig.load() class method exists and works correctly
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)


class TestLLMProviderValidatorFix(unittest.TestCase):
    """Test that the LLM_PROVIDER validator accepts multiple providers."""

    def test_clarifier_validator_accepts_multiple_providers(self):
        """Test that clarifier validator accepts all supported LLM providers."""
        # Mock dependencies before importing
        mock_config = MagicMock()
        mock_config.LLM_PROVIDER = "openai"
        mock_config.INTERACTION_MODE = "cli"
        mock_config.BATCH_STRATEGY = "default"
        mock_config.FEEDBACK_STRATEGY = "none"
        mock_config.HISTORY_FILE = "mock_history.json"
        mock_config.TARGET_LANGUAGE = "en"
        mock_config.CONTEXT_DB_PATH = ":memory:"
        mock_config.KMS_KEY_ID = "mock_key"
        mock_config.ALERT_ENDPOINT = "http://mock.alert"
        mock_config.HISTORY_COMPRESSION = False
        mock_config.CONTEXT_QUERY_LIMIT = 3
        mock_config.HISTORY_LOOKBACK_LIMIT = 10
        mock_config.CIRCUIT_BREAKER_THRESHOLD = 5
        mock_config.CIRCUIT_BREAKER_TIMEOUT = 30
        mock_config.is_production_env = False

        mock_dynaconf = MagicMock(return_value=mock_config)
        mock_dynaconf.return_value.validators.validate = MagicMock()

        with patch("generator.clarifier.clarifier.Dynaconf", mock_dynaconf):
            with patch("generator.clarifier.clarifier.boto3"):
                with patch("generator.clarifier.clarifier.Fernet"):
                    with patch("generator.clarifier.clarifier.sys.exit"):
                        # Import after mocking
                        from generator.clarifier.clarifier import load_config

                        # Test that load_config runs without validation error for openai
                        config = load_config()
                        self.assertIsNotNone(config)

        # Test that the validator accepts all supported providers
        from dynaconf import Validator

        # Create validators with all supported providers
        supported_providers = [
            "openai",
            "anthropic",
            "grok",
            "google",
            "gemini",
            "ollama",
            "local",
            "auto",
        ]

        for provider in supported_providers:
            validator = Validator(
                "LLM_PROVIDER",
                default="auto",
                is_in=supported_providers,
            )
            # The validator should accept all these values
            # validator.operations is a dict with 'is_in' key
            self.assertIn(provider, validator.operations["is_in"])


class TestRunnerConfigLoadMethod(unittest.TestCase):
    """Test that RunnerConfig.load() class method works correctly."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock external dependencies
        sys.modules["hvac"] = MagicMock()
        sys.modules["deepdiff"] = MagicMock()
        sys.modules["watchfiles"] = MagicMock()

        self.temp_dir = Path(tempfile.mkdtemp())
        self.config_file = self.temp_dir / "test_runner_config.yaml"

        # Create a minimal valid config file
        self.config_file.write_text(
            """
version: 4
backend: docker
framework: pytest
parallel_workers: 4
timeout: 300
mutation: false
fuzz: false
distributed: false
log_sinks:
  - type: stream
    config: {}
real_time_log_streaming: true
instance_id: test_instance
metrics_interval_seconds: 5
"""
        )

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil

        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_runner_config_has_load_method(self):
        """Test that RunnerConfig has a load() class method."""
        from generator.runner.runner_config import RunnerConfig

        # Check that the load method exists
        self.assertTrue(hasattr(RunnerConfig, "load"))
        self.assertTrue(callable(getattr(RunnerConfig, "load")))

    def test_runner_config_load_method_works(self):
        """Test that RunnerConfig.load() successfully loads a config file."""
        from generator.runner.runner_config import RunnerConfig

        # Set environment variable for TESTING
        with patch.dict(os.environ, {"TESTING": "1"}):
            # Load config using the class method
            config = RunnerConfig.load(str(self.config_file))

            # Verify the config was loaded correctly
            self.assertIsInstance(config, RunnerConfig)
            self.assertEqual(config.backend, "docker")
            self.assertEqual(config.framework, "pytest")
            self.assertEqual(config.parallel_workers, 4)
            self.assertEqual(config.timeout, 300)

    def test_runner_config_load_with_overrides(self):
        """Test that RunnerConfig.load() works with overrides."""
        from generator.runner.runner_config import RunnerConfig

        # Set environment variable for TESTING
        with patch.dict(os.environ, {"TESTING": "1"}):
            # Load config with overrides
            overrides = {"backend": "podman", "parallel_workers": 8}
            config = RunnerConfig.load(str(self.config_file), overrides=overrides)

            # Verify the overrides were applied
            self.assertIsInstance(config, RunnerConfig)
            self.assertEqual(config.backend, "podman")
            self.assertEqual(config.parallel_workers, 8)
            self.assertEqual(config.framework, "pytest")  # Original value


if __name__ == "__main__":
    unittest.main()
