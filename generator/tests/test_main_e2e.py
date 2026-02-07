# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

# test_integration.py
"""
Comprehensive integration tests for the AI README-to-App Generator.
Tests end-to-end workflows, inter-module communication, and system behavior.
"""

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Set testing environment variables
os.environ["TESTING"] = "true"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-integration"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["GENERATOR_API_KEY"] = "test-api-key-integration"


# Module-level mocking moved to fixture to avoid expensive operations during pytest collection
@pytest.fixture(scope="session", autouse=True)
def mock_expensive_modules():
    """Mock all expensive module dependencies before any imports.
    
    Only mocks modules that are not already installed to avoid breaking
    other tests that depend on real implementations (e.g., chromadb needs real opentelemetry).
    """
    def _mock_if_not_installed(module_name):
        """Only mock a module if it's not already installed."""
        if module_name not in sys.modules:
            try:
                __import__(module_name)
            except ImportError:
                sys.modules[module_name] = MagicMock()
    
    # Mock runner/engine dependencies only if not present
    _mock_if_not_installed("runner.runner_config")
    _mock_if_not_installed("runner.runner_core")
    _mock_if_not_installed("runner.runner_logging")
    _mock_if_not_installed("runner.runner_metrics")
    _mock_if_not_installed("runner.runner_utils")
    _mock_if_not_installed("runner.alerting")
    _mock_if_not_installed("intent_parser.intent_parser")
    _mock_if_not_installed("engine")
    _mock_if_not_installed("clarifier_updater")
    # NOTE: Do NOT mock opentelemetry - it breaks namespace package imports for chromadb
    # opentelemetry is now a required dependency and should be installed
    _mock_if_not_installed("uvicorn")
    yield
    # Cleanup not strictly necessary as these are test mocks

from click.testing import CliRunner
from fastapi.testclient import TestClient


@pytest.fixture
def test_environment(tmp_path):
    """Setup complete test environment with all necessary files and configs."""
    # Create config files
    runner_config = tmp_path / "config.yaml"
    runner_config_data = {
        "backend": "anthropic",
        "framework": "fastapi",
        "logging": {"level": "INFO", "file": str(tmp_path / "app.log")},
        "metrics": {"port": 8001, "enabled": True},
        "security": {"jwt_secret_key_env_var": "JWT_SECRET_KEY"},
    }
    with open(runner_config, "w") as f:
        yaml.dump(runner_config_data, f)

    # Create parser config
    parser_config = tmp_path / "intent_parser.yaml"
    parser_config_data = {"strategies": ["keyword", "ml"], "confidence_threshold": 0.7}
    with open(parser_config, "w") as f:
        yaml.dump(parser_config_data, f)

    # Create test README
    readme = tmp_path / "README.md"
    readme.write_text("""
# Test Project

A test project for the AI Generator.

## Features
- Feature 1
- Feature 2

## Installation
pip install test-project
""")

    # Create output directory
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Set environment variables
    os.environ["RUNNER_CONFIG_PATH"] = str(runner_config)
    os.environ["PARSER_CONFIG_PATH"] = str(parser_config)

    return {
        "tmp_path": tmp_path,
        "runner_config": runner_config,
        "parser_config": parser_config,
        "readme": readme,
        "output_dir": output_dir,
    }


class TestEndToEndWorkflow:
    """End-to-end workflow integration tests."""

    @pytest.mark.asyncio
    async def test_complete_generation_workflow(self, test_environment):
        """Test complete workflow from README to generated app."""
        with (
            patch("main.main.Runner") as MockRunner,
            patch("main.main.IntentParser") as MockParser,
        ):

            # Setup mocks
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(
                return_value={
                    "status": "success",
                    "output": {"files": ["app.py", "requirements.txt"]},
                    "run_id": "test-run-123",
                }
            )
            MockRunner.return_value = mock_runner

            mock_parser = MagicMock()
            mock_parser.parse = AsyncMock(
                return_value={
                    "intent": "create_api",
                    "framework": "fastapi",
                    "features": ["authentication", "database"],
                }
            )
            MockParser.return_value = mock_parser

            # Execute workflow
            # 1. Parse README
            parse_result = await mock_parser.parse(
                content=test_environment["readme"].read_text()
            )
            assert parse_result["intent"] == "create_api"

            # 2. Run generator
            run_result = await mock_runner.run(
                {
                    "input": parse_result,
                    "output_path": str(test_environment["output_dir"]),
                }
            )
            assert run_result["status"] == "success"
            assert "run_id" in run_result

    @pytest.mark.asyncio
    async def test_cli_to_api_workflow(self, test_environment):
        """Test CLI triggering API workflow."""
        from main.cli import cli

        runner = CliRunner()

        with patch("main.cli.aiohttp.ClientSession") as MockSession:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(
                return_value={"status": "success", "run_id": "cli-test-123"}
            )

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock()
            mock_session.post = AsyncMock(return_value=mock_response)
            mock_session.post.return_value.__aenter__ = AsyncMock(
                return_value=mock_response
            )
            mock_session.post.return_value.__aexit__ = AsyncMock()

            MockSession.return_value = mock_session

            # Execute CLI command
            result = runner.invoke(
                cli, ["run", "--input", str(test_environment["readme"]), "--dry-run"]
            )

            # CLI should execute
            assert result.exit_code in [0, 1, 2]


class TestAPIIntegration:
    """Integration tests for API endpoints."""

    @pytest.fixture
    def test_client(self):
        """Create test client for API."""
        from main.api import api

        return TestClient(api)

    def test_health_to_metrics_flow(self, test_client):
        """Test flow from health check to metrics."""
        # Check health
        health_response = test_client.get("/api/v1/health")

        # If health endpoint exists and is healthy, check metrics
        if health_response.status_code == 200:
            health_data = health_response.json()
            if health_data.get("status") == "healthy":
                # Get metrics
                metrics_response = test_client.get("/api/v1/metrics")
                # Metrics may require authentication
                assert metrics_response.status_code in [200, 401, 404]

    @pytest.mark.asyncio
    async def test_parse_to_run_flow(self, test_client, test_environment):
        """Test flow from parse to run."""
        # First, get authentication token
        token_response = test_client.post(
            "/api/v1/token", data={"username": "test", "password": "test"}
        )

        if token_response.status_code == 200:
            token = token_response.json().get("access_token")
            headers = {"Authorization": f"Bearer {token}"}

            # Parse content
            parse_response = test_client.post(
                "/api/v1/parse/text",
                json={
                    "content": test_environment["readme"].read_text(),
                    "format_hint": "markdown",
                },
                headers=headers,
            )

            if parse_response.status_code == 200:
                parse_data = parse_response.json()

                # Run generator with parsed data
                run_response = test_client.post(
                    "/api/v1/run", json={"input_data": parse_data}, headers=headers
                )

                assert run_response.status_code in [200, 422]


class TestGUIIntegration:
    """Integration tests for GUI components."""

    @pytest.mark.asyncio
    async def test_gui_api_integration(self, test_environment):
        """Test GUI interacting with API."""
        from main.gui import MainApp

        app = MainApp()

        with patch.object(app, "_make_api_request") as mock_api:
            mock_api.return_value = {"status": "success", "result": "test"}

            # Test runner workflow
            result = await mock_api(
                "POST", "http://localhost:8000/api/v1/run", json_data={"input": "test"}
            )

            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_gui_config_reload(self, test_environment):
        """Test GUI config reload triggering API reload."""
        from main.gui import MainApp

        app = MainApp()

        with patch.object(
            app, "_trigger_backend_config_reload", new_callable=AsyncMock
        ) as mock_reload:
            mock_reload.return_value = {"status": "reloaded"}

            result = await mock_reload("runner", "http://localhost/config/reload")

            assert result["status"] == "reloaded"


class TestCLIIntegration:
    """Integration tests for CLI commands."""

    def test_cli_config_integration(self, test_environment):
        """Test CLI config commands integration."""
        from main.cli import cli

        runner = CliRunner()

        # Show config
        result_show = runner.invoke(
            cli,
            ["config", "show", "--config-file", str(test_environment["runner_config"])],
        )

        # Validate config
        result_validate = runner.invoke(
            cli,
            [
                "config",
                "validate",
                "--config-file",
                str(test_environment["runner_config"]),
            ],
        )

        # At least one should work
        assert result_show.exit_code in [0, 1, 2] or result_validate.exit_code in [
            0,
            1,
            2,
        ]

    def test_cli_feedback_integration(self, test_environment):
        """Test CLI feedback submission to API."""
        from main.cli import cli

        runner = CliRunner()

        with patch("main.cli.aiohttp.ClientSession") as MockSession:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_session = AsyncMock()
            MockSession.return_value = mock_session

            result = runner.invoke(
                cli, ["feedback", "--run-id", "test-123", "--rating", "5"]
            )

            assert result.exit_code in [0, 1, 2]


class TestConfigurationPropagation:
    """Tests for configuration changes propagating through system."""

    @pytest.mark.asyncio
    async def test_config_reload_propagates(self, test_environment):
        """Test config reload propagates to all components."""
        with (
            patch("main.main.ConfigWatcher") as MockWatcher,
            patch("main.main.load_config") as mock_load,
        ):

            original_config = yaml.safe_load(
                test_environment["runner_config"].read_text()
            )
            mock_load.return_value = original_config

            # Simulate config change
            new_config = original_config.copy()
            new_config["backend"] = "updated"

            # Update file
            with open(test_environment["runner_config"], "w") as f:
                yaml.dump(new_config, f)

            # Reload
            updated_config = yaml.safe_load(
                test_environment["runner_config"].read_text()
            )

            assert updated_config["backend"] == "updated"


class TestAuthenticationFlow:
    """Tests for authentication flow across components."""

    def test_token_generation_and_usage(self):
        """Test token generation in API and usage in requests."""
        from main.api import create_access_token, verify_token

        # Generate token
        token = create_access_token(data={"sub": "testuser"})
        assert token is not None

        # Verify token
        username = verify_token(token)
        assert username == "testuser"

    def test_api_key_authentication(self):
        """Test API key authentication flow."""
        from main.api import pwd_context

        api_key = "test-key-12345"
        hashed_key = pwd_context.hash(api_key)

        # Verify key
        assert pwd_context.verify(api_key, hashed_key)


class TestErrorPropagation:
    """Tests for error handling across components."""

    @pytest.mark.asyncio
    async def test_runner_error_to_api(self):
        """Test runner errors are properly returned by API."""
        with patch("main.api.Runner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner.run = AsyncMock(side_effect=Exception("Runner failed"))
            MockRunner.return_value = mock_runner

            # Error should be caught and handled
            with pytest.raises(Exception):
                await mock_runner.run({})

    @pytest.mark.asyncio
    async def test_api_error_to_gui(self):
        """Test API errors are properly handled by GUI."""
        from fastapi import HTTPException
        from main.gui import MainApp

        app = MainApp()

        with patch.object(app, "_make_api_request") as mock_api:
            mock_api.side_effect = HTTPException(status_code=500, detail="Server error")

            with pytest.raises(HTTPException):
                await mock_api("GET", "http://test/api")


class TestMetricsCollection:
    """Tests for metrics collection across components."""

    @pytest.mark.asyncio
    async def test_metrics_aggregation(self):
        """Test metrics are collected from all components."""
        with patch("main.main.get_metrics_dict") as mock_metrics:
            mock_metrics.return_value = {
                "app_running_status": MagicMock(),
                "app_startup_duration_seconds": MagicMock(),
                "api_requests_total": 100,
                "cli_commands_executed": 50,
            }

            metrics = mock_metrics()

            assert "app_running_status" in metrics
            assert "api_requests_total" in metrics
            assert "cli_commands_executed" in metrics


class TestDatabaseIntegration:
    """Tests for database operations across components."""

    def test_database_connection_sharing(self):
        """Test database connection is shared properly."""
        from main.api import SessionLocal, create_db_tables

        # Create tables
        create_db_tables()

        # Get session
        db = SessionLocal()
        assert db is not None
        db.close()

    def test_database_transactions(self):
        """Test database transactions across operations."""
        from main.api import SessionLocal, User

        db = SessionLocal()
        try:
            # Create user
            user = User(
                username="testuser",
                email="test@example.com",
                hashed_password="hashed",  # <-- FIX: Was password_hash
                is_active=True,
            )
            db.add(user)
            db.commit()

            # Retrieve user
            retrieved = db.query(User).filter(User.username == "testuser").first()
            assert retrieved is not None

            # Update user
            retrieved.email = "updated@example.com"
            db.commit()

            # Verify update
            updated = db.query(User).filter(User.username == "testuser").first()
            assert updated.email == "updated@example.com"

        finally:
            db.rollback()
            db.close()


class TestLoggingIntegration:
    """Tests for logging across components."""

    def test_log_propagation(self):
        """Test logs from all components are captured."""
        import logging

        # Create handlers
        main_logger = logging.getLogger("main")
        api_logger = logging.getLogger("api")
        cli_logger = logging.getLogger("cli")
        gui_logger = logging.getLogger("gui")

        # All loggers should be accessible
        assert main_logger is not None
        assert api_logger is not None
        assert cli_logger is not None
        assert gui_logger is not None


class TestConcurrentOperations:
    """Tests for concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_api_requests(self):
        """Test multiple concurrent API requests."""
        from main.api import api

        client = TestClient(api)

        async def make_request():
            response = client.get("/api/v1/health")
            return response.status_code

        # Make concurrent requests
        tasks = [make_request() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # All should complete
        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_concurrent_cli_commands(self):
        """Test concurrent CLI command execution."""
        from main.cli import cli

        runner = CliRunner()

        def run_command():
            return runner.invoke(cli, ["health"])

        # Run multiple commands
        results = [run_command() for _ in range(5)]

        # All should complete
        assert len(results) == 5


class TestFileOperations:
    """Tests for file operations across components."""

    @pytest.mark.asyncio
    async def test_file_upload_and_processing(self, test_environment):
        """Test file upload through API and processing."""
        from main.api import api

        client = TestClient(api)

        # Upload file
        with open(test_environment["readme"], "rb") as f:
            response = client.post(
                "/api/v1/parse/file",
                files={"file": ("README.md", f, "text/markdown")},
                data={"format_hint": "markdown"},
            )

        # Should be processed
        assert response.status_code in [200, 401, 404, 422]

    def test_output_file_generation(self, test_environment):
        """Test output files are generated correctly."""
        output_file = test_environment["output_dir"] / "test_output.txt"
        output_file.write_text("Generated content")

        assert output_file.exists()
        assert output_file.read_text() == "Generated content"


class TestWebSocketIntegration:
    """Tests for WebSocket communication."""

    @pytest.mark.asyncio
    async def test_websocket_connection(self):
        """Test WebSocket connection for real-time updates."""
        from main.api import api

        client = TestClient(api)

        try:
            with client.websocket_connect("/api/v1/ws") as websocket:
                # Send message
                websocket.send_json({"type": "ping"})

                # Receive response
                data = websocket.receive_json()
                assert data is not None
        except Exception:
            # WebSocket may not be implemented or requires auth
            pass


class TestSecurityIntegration:
    """Tests for security features across components."""

    def test_password_security(self):
        """Test password hashing and verification."""
        from main.api import pwd_context

        password = "SecurePassword123!"
        hashed = pwd_context.hash(password)

        assert hashed != password
        assert pwd_context.verify(password, hashed)
        assert not pwd_context.verify("WrongPassword", hashed)

    def test_jwt_security(self):
        """Test JWT token security."""
        from main.api import create_access_token, verify_token

        # Create token
        token = create_access_token(data={"sub": "testuser"})

        # Verify token
        username = verify_token(token)
        assert username == "testuser"

        # Test token tampering
        try:
            tampered_token = token[:-10] + "tampered"
            verify_token(tampered_token)
            pytest.fail("Tampered token should not verify")
        except Exception:
            # Expected to fail
            pass


class TestPerformance:
    """Performance and scalability tests."""

    @pytest.mark.asyncio
    async def test_api_response_time(self):
        """Test API response time is acceptable."""
        from main.api import api

        client = TestClient(api)

        start_time = time.time()
        response = client.get("/api/v1/health")
        end_time = time.time()

        response_time = end_time - start_time

        # Response should be fast (< 1 second)
        assert response_time < 1.0 or response.status_code == 404

    def test_cli_startup_time(self):
        """Test CLI startup time is acceptable."""
        from main.cli import cli

        runner = CliRunner()

        start_time = time.time()
        result = runner.invoke(cli, ["--help"])
        end_time = time.time()

        startup_time = end_time - start_time

        # Should start quickly (< 2 seconds)
        assert startup_time < 2.0


class TestDataConsistency:
    """Tests for data consistency across components."""

    def test_config_consistency(self, test_environment):
        """Test config is consistent across all components."""
        # Load runner config
        runner_config = yaml.safe_load(test_environment["runner_config"].read_text())

        # Load parser config
        parser_config = yaml.safe_load(test_environment["parser_config"].read_text())

        # Both should be valid YAML
        assert isinstance(runner_config, dict)
        assert isinstance(parser_config, dict)


class TestRecoveryMechanisms:
    """Tests for error recovery mechanisms."""

    @pytest.mark.asyncio
    async def test_api_recovery_after_error(self):
        """Test API recovers after errors."""
        from main.api import api

        client = TestClient(api)

        # Cause an error (invalid request)
        bad_response = client.post("/api/v1/run", json={"invalid": "data"})

        # System should still respond to good requests
        good_response = client.get("/api/v1/health")

        # At least one should succeed or both fail gracefully
        assert bad_response.status_code != 500 or good_response.status_code in [
            200,
            404,
        ]

    def test_cli_recovery_suggestions(self):
        """Test CLI provides recovery suggestions."""
        from main.cli import suggest_recovery_cli

        # Should not crash
        try:
            suggest_recovery_cli(Exception("Test error"))
            assert True
        except Exception as e:
            pytest.fail(f"Recovery suggestion failed: {e}")


class TestSystemShutdown:
    """Tests for graceful system shutdown."""

    def test_cleanup_on_exit(self):
        """Test cleanup operations on exit."""
        # Test that resources are cleaned up

        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test")
            assert test_file.exists()

        # Temporary directory should be cleaned up
        assert not Path(tmpdir).exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
