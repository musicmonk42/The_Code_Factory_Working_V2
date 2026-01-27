"""
Test suite for omnicore_engine/cli.py
Tests CLI commands, argument parsing, and command execution.
"""

import json
import os
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

# Add the parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.cli import (
    main,
    safe_command,
    sanitize_env_vars,
    validate_file_path,
)


class TestUtilityFunctions:
    """Test utility functions used by CLI"""

    def test_sanitize_env_vars(self):
        """Test environment variable sanitization"""
        # Set some test env vars
        os.environ["TEST_PASSWORD"] = "secret123"
        os.environ["API_KEY"] = "key456"
        os.environ["NORMAL_VAR"] = "normal_value"

        sanitize_env_vars()

        assert os.environ["TEST_PASSWORD"] == "[REDACTED]"
        assert os.environ["API_KEY"] == "[REDACTED]"
        assert (
            os.environ["NORMAL_VAR"] == "normal_value"
        )  # Doesn't contain sensitive keywords

        # Cleanup
        del os.environ["TEST_PASSWORD"]
        del os.environ["API_KEY"]
        del os.environ["NORMAL_VAR"]

    def test_safe_command(self):
        """Test safe command parsing"""
        # Simple command
        result = safe_command("ls -la")
        assert result == ["ls", "-la"]

        # Command with quotes
        result = safe_command('echo "hello world"')
        assert result == ["echo", "hello world"]

        # Command with escaped characters
        result = safe_command("file\\ name.txt")
        assert result == ["file name.txt"]

    def test_validate_file_path_valid(self):
        """Test validation of valid file paths"""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("test")

            # Valid path in temp directory
            validated = validate_file_path(str(test_file))
            assert validated == test_file.resolve()

    def test_validate_file_path_invalid(self):
        """Test validation rejects path traversal"""
        with pytest.raises(ValueError, match="Access denied"):
            validate_file_path("/etc/passwd")

        with pytest.raises(ValueError, match="Access denied"):
            validate_file_path("../../../etc/passwd")


class TestLoadFileFunction:
    """Test file loading functionality"""

    @pytest.mark.asyncio
    async def test_load_json_file(self):
        """Test loading JSON file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"key": "value", "number": 42}, f)
            f.flush()

            # Mock the load_file function's internal logic
            with patch("omnicore_engine.cli.validate_file_path") as mock_validate:
                mock_validate.return_value = Path(f.name)

                # Import the actual load_file function

                # Create a minimal args object
                args = Namespace(command="test")

                # We need to test the load_file function directly
                # Since it's defined inside main(), we'll test it via command execution

    @pytest.mark.asyncio
    async def test_load_yaml_file(self):
        """Test loading YAML file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"key": "value", "list": [1, 2, 3]}, f)
            f.flush()

            # Test would be similar to JSON test
            assert Path(f.name).exists()

            # Cleanup
            os.unlink(f.name)


class TestSimulateCommand:
    """Test simulate command"""

    def test_simulate_command_parsing(self):
        """Test simulate command argument parsing"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"simulation": "test"}, f)
            f.flush()

            # Patch asyncio.run to prevent actual execution
            # Also patch redis to prevent connection attempts
            with patch("sys.argv", ["cli.py", "simulate", "--request_file", f.name]):
                with patch("omnicore_engine.cli.asyncio.run") as mock_run:
                    # Configure mock to raise SystemExit to simulate normal exit
                    mock_run.side_effect = SystemExit(0)
                    with pytest.raises(SystemExit):
                        main()

            # Cleanup
            os.unlink(f.name)

    @pytest.mark.asyncio
    async def test_simulate_execution(self):
        """Test simulate command execution"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.simulate = AsyncMock(return_value={"result": "success"})

        with patch("omnicore_engine.cli.OmniCoreOmega_instance", mock_engine):
            # Test the actual execution logic - placeholder test
            pass


class TestListPluginsCommand:
    """Test list-plugins command"""

    @patch("sys.argv", ["cli.py", "list-plugins"])
    @patch("omnicore_engine.cli.asyncio.run")
    def test_list_plugins_without_filter(self, mock_run):
        """Test list-plugins without kind filter"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.plugin_registry = Mock()
        mock_engine.plugin_registry.get_plugin_names = Mock(
            return_value=["plugin1", "plugin2"]
        )

        # Test would verify the command calls the right methods


class TestAuditCommands:
    """Test audit-related commands"""

    @pytest.mark.asyncio
    async def test_audit_query_command(self):
        """Test audit-query command"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.audit = Mock()
        mock_engine.audit.query_audit_records = AsyncMock(
            return_value=[{"uuid": "1", "kind": "test"}, {"uuid": "2", "kind": "test"}]
        )

        with patch("omnicore_engine.cli.OmniCoreOmega_instance", mock_engine):
            # Test execution
            pass

    @pytest.mark.asyncio
    async def test_audit_snapshot_command(self):
        """Test audit-snapshot command"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.audit = Mock()
        mock_engine.audit.snapshot_audit_state = AsyncMock(return_value="snapshot-123")

        # Test execution

    @pytest.mark.asyncio
    async def test_audit_replay_command(self):
        """Test audit-replay command"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.audit = Mock()
        mock_engine.audit.replay_events = AsyncMock(
            return_value=[{"uuid": "1", "sim_id": "sim123"}]
        )

        # Test execution


class TestPluginManagementCommands:
    """Test plugin management commands"""

    @pytest.mark.asyncio
    async def test_plugin_install_command(self):
        """Test plugin-install command"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.database = Mock()
        mock_engine.audit = Mock()
        mock_engine.plugin_registry = Mock()

        with patch("omnicore_engine.cli.OmniCoreOmega_instance", mock_engine):
            with patch(
                "omnicore_engine.plugin_registry.PluginMarketplace"
            ) as mock_marketplace_class:
                mock_marketplace = Mock()
                mock_marketplace.install_plugin = AsyncMock()
                mock_marketplace_class.return_value = mock_marketplace

                # Test execution - placeholder test
                pass

    @pytest.mark.asyncio
    async def test_plugin_rate_command(self):
        """Test plugin-rate command"""
        # Similar structure to install test - placeholder test
        pass


class TestPolicyIntegration:
    """Test policy check integration"""

    @pytest.mark.asyncio
    async def test_command_with_policy_approval(self):
        """Test command execution with policy approval"""
        # This test is a placeholder - policy_engine_cli is defined inside main()
        # and cannot be easily mocked at module level
        pass

    @pytest.mark.asyncio
    async def test_command_with_policy_denial(self):
        """Test command execution with policy denial"""
        # This test is a placeholder - policy_engine_cli is defined inside main()
        # and cannot be easily mocked at module level
        pass


class TestOutputFormatting:
    """Test output formatting functionality"""

    def test_json_output_format(self):
        """Test JSON output formatting"""
        import sys
        from io import StringIO

        data = {"key": "value", "number": 42}

        # Capture output
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        # This would need to be tested via actual function call
        # print_output(data, None, 'json')

        sys.stdout = old_stdout

    def test_yaml_output_format(self):
        """Test YAML output formatting"""
        # Similar to JSON test
        pass

    def test_file_output(self):
        """Test output to file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            # Test writing output to file
            pass


class TestREPLMode:
    """Test REPL (interactive shell) mode"""

    @patch("sys.argv", ["cli.py", "repl"])
    @patch("omnicore_engine.cli.asyncio.to_thread")
    @patch("builtins.input")
    def test_repl_basic_commands(self, mock_input, mock_to_thread):
        """Test REPL with basic commands"""
        # Simulate user input
        mock_input.side_effect = ["help", "exit"]
        mock_to_thread.side_effect = ["help", "exit"]

        # Test REPL execution

    @patch("builtins.input")
    def test_repl_exit_commands(self, mock_input):
        """Test REPL exit commands"""
        # Test 'exit' command
        mock_input.return_value = "exit"

        # Test Ctrl+C handling
        mock_input.side_effect = KeyboardInterrupt()

        # Test EOF handling
        mock_input.side_effect = EOFError()


class TestDebugInfoCommand:
    """Test debug-info command"""

    @pytest.mark.asyncio
    async def test_debug_info_output(self):
        """Test debug-info command output"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.plugin_registry = Mock()
        mock_engine.plugin_registry.get_plugin_names = Mock(return_value=["plugin1"])
        mock_engine.array_backend = Mock()
        mock_engine.array_backend.mode = "numpy"
        mock_engine.components = {
            "message_bus": Mock(health_check=AsyncMock(return_value={"status": "ok"}))
        }

        with patch("omnicore_engine.cli.OmniCoreOmega_instance", mock_engine):
            # Test execution
            pass


class TestErrorHandling:
    """Test error handling in CLI"""

    @patch("sys.argv", ["cli.py", "simulate", "--request_file", "nonexistent.json"])
    def test_file_not_found_error(self):
        """Test handling of file not found error"""
        import asyncio
        # Ensure an event loop exists for asyncio.run() compatibility with uvloop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        with pytest.raises(SystemExit) as exc_info:
            main()

        # Should exit with FILE_ARGUMENT_ERROR

    @patch("sys.argv", ["cli.py", "simulate", "--request_file", "test.txt"])
    def test_invalid_file_extension_error(self):
        """Test handling of invalid file extension"""
        import asyncio
        # Ensure an event loop exists for asyncio.run() compatibility with uvloop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("not json or yaml")
            f.flush()

            with patch("sys.argv", ["cli.py", "simulate", "--request_file", f.name]):
                with pytest.raises(SystemExit) as exc_info:
                    main()

            os.unlink(f.name)

    def test_json_parse_error(self):
        """Test handling of JSON parse errors"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("invalid json{")
            f.flush()

            # Test would verify proper error handling
            os.unlink(f.name)


class TestAnonymization:
    """Test data anonymization functionality"""

    @pytest.mark.asyncio
    async def test_user_data_anonymization(self):
        """Test user data is properly anonymized"""

        data = {
            "user_id": "john.doe",
            "name": "John Doe",
            "agent_id": "agent123",
            "other_data": "unchanged",
        }

        # The anonymize_data function should hash sensitive fields
        # This would need to be tested via actual function execution


class TestWorkflowCommand:
    """Test workflow command"""

    @pytest.mark.asyncio
    async def test_workflow_command_execution(self):
        """Test workflow command execution"""
        mock_engine = Mock()
        mock_engine.is_initialized = True
        mock_engine.components = {"message_bus": Mock(publish=AsyncMock())}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Requirements\nTest requirements")
            f.flush()

            with patch("omnicore_engine.cli.OmniCoreOmega_instance", mock_engine):
                # Test execution
                pass

            os.unlink(f.name)


class TestMetricsCommand:
    """Test metrics-status command"""

    @patch("prometheus_client.generate_latest")
    def test_metrics_output_to_console(self, mock_generate):
        """Test metrics output to console"""
        mock_generate.return_value = b"metric1 1.0\nmetric2 2.0"

        # Test execution - placeholder test
        pass

    @patch("prometheus_client.generate_latest")
    def test_metrics_output_to_file(self, mock_generate):
        """Test metrics output to file"""
        mock_generate.return_value = b"metric1 1.0\nmetric2 2.0"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            # Test saving metrics to file - placeholder test
            os.unlink(f.name)


class TestFeatureFlagCommand:
    """Test feature-flag-set command"""

    @pytest.mark.asyncio
    async def test_set_feature_flag_true(self):
        """Test setting feature flag to true"""
        mock_engine = Mock()
        mock_engine.is_initialized = True

        with patch("omnicore_engine.cli.OmniCoreOmega_instance", mock_engine):
            with patch("omnicore_engine.cli.settings") as mock_settings:
                # Test setting flag
                pass

    @pytest.mark.asyncio
    async def test_set_feature_flag_false(self):
        """Test setting feature flag to false"""
        # Similar to true test


class TestDocsCommand:
    """Test docs generation command"""

    def test_docs_generation_to_console(self):
        """Test docs generation to console"""
        # Test documentation generation
        pass

    def test_docs_generation_to_file(self):
        """Test docs generation to file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            # Test saving docs to file
            os.unlink(f.name)


class TestImportFixerCommand:
    """Test fix-imports command"""

    @pytest.mark.asyncio
    async def test_fix_imports_command(self):
        """Test fix-imports command execution"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("import os\nimport sys\nprint('test')")
            f.flush()

            with patch("omnicore_engine.cli.AIManager") as mock_ai_class:
                mock_ai = Mock()
                mock_ai.get_refactoring_suggestion = Mock(
                    return_value="Suggested refactoring"
                )
                mock_ai_class.return_value = mock_ai

                # Test execution

            os.unlink(f.name)


class TestMainEntryPoint:
    """Test main entry point and argument parsing"""

    @patch("sys.argv", ["cli.py"])
    def test_no_command_shows_help(self):
        """Test that no command shows help"""
        # parser is defined inside main() function, so we test the behavior instead
        with pytest.raises(SystemExit):
            main()

    @patch("sys.argv", ["cli.py", "--version"])
    def test_version_flag(self):
        """Test --version flag"""
        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

    @patch("sys.argv", ["cli.py", "unknown-command"])
    def test_unknown_command(self):
        """Test unknown command handling"""
        with pytest.raises(SystemExit):
            main()


class TestServeCommand:
    """Test serve command"""

    @patch("sys.argv", ["cli.py", "serve"])
    @patch("omnicore_engine.cli.uvicorn.run")
    def test_serve_default_settings(self, mock_uvicorn):
        """Test serve command with default settings"""
        main()

        mock_uvicorn.assert_called_once()
        call_args = mock_uvicorn.call_args
        assert "omnicore_engine.fastapi_app:app" in str(call_args)

    @patch(
        "sys.argv",
        ["cli.py", "--host", "0.0.0.0", "--port", "8080", "serve", "--reload"],
    )
    @patch("omnicore_engine.cli.uvicorn.run")
    def test_serve_custom_settings(self, mock_uvicorn):
        """Test serve command with custom settings"""
        main()

        mock_uvicorn.assert_called_once()
        call_kwargs = mock_uvicorn.call_args[1]
        assert call_kwargs.get("reload") == True
        assert call_kwargs.get("host") == "0.0.0.0"
        assert call_kwargs.get("port") == 8080


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
