import os
import sys
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
from collections import defaultdict
import tempfile

# Add the parent directory to the path to allow imports from the 'cli' module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Now import the module to be tested.
from self_healing_import_fixer.cli import (
    main,
    main_async,
    _validate_path_argument,
    PluginManager,
)

# --- Fixtures ---


@pytest.fixture(autouse=True)
def mock_core_dependencies():
    """Mocks core dependencies used by cli.py by patching them where they are used."""
    mock_audit_logger_instance = MagicMock()
    mock_audit_logger_instance.log_event = MagicMock()

    with patch(
        "self_healing_import_fixer.cli.alert_operator", new=MagicMock()
    ) as mock_alert, patch(
        "self_healing_import_fixer.cli.scrub_secrets",
        new=MagicMock(side_effect=lambda x: x),
    ) as mock_scrub, patch(
        "self_healing_import_fixer.cli.SECRETS_MANAGER", new=MagicMock()
    ) as mock_secrets, patch(
        "self_healing_import_fixer.cli.cli_audit_logger", new=mock_audit_logger_instance
    ) as mock_logger:

        # Configure the mocked SecretsManager to return dummy values for any secret lookups
        mock_secrets.get_secret.return_value = "dummy_secret_value"

        yield {
            "alert_operator": mock_alert,
            "scrub_secrets": mock_scrub,
            "SECRETS_MANAGER": mock_secrets,
            "cli_audit_logger": mock_logger,
        }


@pytest.fixture(autouse=True)
def setup_teardown_env_vars():
    """Manages environment variables for each test."""
    original_prod_mode = os.getenv("PRODUCTION_MODE")

    os.environ["PRODUCTION_MODE"] = "false"

    yield

    if original_prod_mode:
        os.environ["PRODUCTION_MODE"] = original_prod_mode
    elif "PRODUCTION_MODE" in os.environ:
        del os.environ["PRODUCTION_MODE"]


@pytest.fixture
def mock_plugin_manager():
    """Mocks a PluginManager instance for testing CLI commands."""
    mock_pm = MagicMock(spec=PluginManager)
    mock_pm.plugins = {}
    mock_pm.hooks = defaultdict(list)
    # Set whitelisted_plugin_dirs to include temp directory
    mock_pm.whitelisted_plugin_dirs = [tempfile.gettempdir(), "/tmp", os.getcwd()]
    # Create an async mock for discover_and_load
    mock_pm.discover_and_load = AsyncMock(return_value=None)
    mock_pm.run_hook.return_value = None

    with patch("self_healing_import_fixer.cli.PluginManager", return_value=mock_pm):
        yield mock_pm


@pytest.fixture
def test_project_setup(tmp_path):
    """Sets up a dummy project structure for testing."""
    project_root = tmp_path / "test_project"
    project_root.mkdir()
    (project_root / "test_file.py").write_text("import os\n")

    yield {"project_root": str(project_root)}


# --- Test Cases ---


@pytest.mark.parametrize(
    "path_arg, is_dir, allow_symlink, should_raise",
    [
        ("../bad_path", True, False, True),  # Path traversal
        ("non_existent_file.txt", False, False, True),  # Non-existent file
        ("non_existent_dir", True, False, True),  # Non-existent directory
        (
            "/etc/passwd",
            False,
            False,
            True,
        ),  # Outside project scope (simulated by allowlist)
    ],
)
def test_validate_path_argument_security_checks(
    path_arg, is_dir, allow_symlink, should_raise, tmp_path
):
    """
    Tests that `_validate_path_argument` correctly catches security issues.
    """
    # Allowlist is the tmp_path itself, so /etc/passwd will fail
    allowlist = [str(tmp_path)]

    if should_raise:
        with pytest.raises(SystemExit):
            _validate_path_argument(
                path_arg,
                "test_arg",
                is_dir=is_dir,
                allow_symlink=allow_symlink,
                allowlist=allowlist,
            )
    else:
        # This part of the test is less relevant now but kept for structure
        test_path = str(tmp_path / path_arg)
        if is_dir:
            Path(test_path).mkdir(parents=True, exist_ok=True)
        else:
            Path(test_path).touch()

        result = _validate_path_argument(
            test_path,
            "test_arg",
            is_dir=is_dir,
            allow_symlink=allow_symlink,
            allowlist=allowlist,
        )
        assert result == str(Path(test_path).resolve())


def test_main_handles_analyze_command(
    test_project_setup, mock_plugin_manager, mock_core_dependencies
):
    """Tests the 'analyze' command's basic functionality."""
    # Mock the analyzer module and class
    mock_analyzer = MagicMock()
    mock_analyzer.generate_text_report.return_value = "Test Report"

    # Override the plugin manager's whitelisted dirs to include temp path
    mock_plugin_manager.whitelisted_plugin_dirs = [
        os.path.dirname(test_project_setup["project_root"]),
        tempfile.gettempdir(),
        os.getcwd(),
    ]

    with patch(
        "sys.argv",
        [
            "self_healing_import_fixer.cli",
            "analyze",
            test_project_setup["project_root"],
            "--output-format",
            "text",
        ],
    ), patch(
        "self_healing_import_fixer.cli.ImportGraphAnalyzer", create=True
    ) as MockAnalyzer, patch(
        "builtins.print"
    ) as mock_print:

        # Set up the mock to return our analyzer instance when instantiated
        MockAnalyzer.return_value = mock_analyzer

        # Run main - it should complete without raising SystemExit since the command succeeds
        main()

        # Assert that ImportGraphAnalyzer was instantiated with the correct path
        MockAnalyzer.assert_called_once()

        # Assert that the text report was printed
        mock_print.assert_called_with("Test Report")

        # Assert that the command execution was logged
        calls = mock_core_dependencies["cli_audit_logger"].log_event.call_args_list
        command_executed_calls = [c for c in calls if c[0][0] == "command_executed"]
        assert len(command_executed_calls) > 0
        assert any("analyze" in str(call) for call in command_executed_calls)


def test_heal_command_in_prod_requires_interactive(
    test_project_setup, mock_plugin_manager, mock_core_dependencies
):
    """
    Tests that the 'heal' command in production fails without '--interactive'.
    """
    # Override the plugin manager's whitelisted dirs to include temp path
    mock_plugin_manager.whitelisted_plugin_dirs = [
        os.path.dirname(test_project_setup["project_root"]),
        tempfile.gettempdir(),
        os.getcwd(),
    ]

    # Set environment variable to match the patched PRODUCTION_MODE
    os.environ["PRODUCTION_MODE"] = "true"

    # Create a custom exception to use as a side effect for sys.exit
    class TestExit(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__(f"sys.exit({code})")

    # Mock the heal_entrypoint - it should NOT be called if security check works
    with patch(
        "sys.argv",
        ["self_healing_import_fixer.cli", "heal", test_project_setup["project_root"]],
    ), patch("self_healing_import_fixer.cli.heal_entrypoint", create=True) as mock_heal, patch(
        "self_healing_import_fixer.cli.PRODUCTION_MODE", True
    ), patch(
        "self_healing_import_fixer.cli.load_fixer"
    ) as mock_load_fixer, patch(
        "sys.exit", side_effect=lambda code: (_ for _ in ()).throw(TestExit(code))
    ):

        try:
            main()
        except TestExit as e:
            # We expect exit with code 1 due to missing --interactive
            assert e.code == 1

        # The fixer module should not have been loaded
        mock_load_fixer.assert_not_called()
        # The heal function should not have been called
        mock_heal.assert_not_called()

        # Verify the security violation was logged
        calls = mock_core_dependencies["cli_audit_logger"].log_event.call_args_list
        security_violations = [c for c in calls if c[0][0] == "command_forbidden"]
        assert len(security_violations) > 0
        assert security_violations[0][1]["reason"] == "no_interactive_in_prod"


def test_heal_command_in_prod_forbids_yes_flag(
    test_project_setup, mock_plugin_manager, mock_core_dependencies
):
    """
    Tests that the 'heal' command in production forbids the '--yes' flag.
    """
    # Override the plugin manager's whitelisted dirs to include temp path
    mock_plugin_manager.whitelisted_plugin_dirs = [
        os.path.dirname(test_project_setup["project_root"]),
        tempfile.gettempdir(),
        os.getcwd(),
    ]

    # Set environment variable to match the patched PRODUCTION_MODE
    os.environ["PRODUCTION_MODE"] = "true"

    # Create a custom exception to use as a side effect for sys.exit
    class TestExit(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__(f"sys.exit({code})")

    # Mock the heal_entrypoint - it should NOT be called if security check works
    with patch(
        "sys.argv",
        [
            "self_healing_import_fixer.cli",
            "heal",
            test_project_setup["project_root"],
            "-i",
            "-y",
        ],
    ), patch("self_healing_import_fixer.cli.heal_entrypoint", create=True) as mock_heal, patch(
        "self_healing_import_fixer.cli.PRODUCTION_MODE", True
    ), patch(
        "self_healing_import_fixer.cli.load_fixer"
    ) as mock_load_fixer, patch(
        "sys.exit", side_effect=lambda code: (_ for _ in ()).throw(TestExit(code))
    ):

        try:
            main()
        except TestExit as e:
            # We expect exit with code 1 due to forbidden --yes flag
            assert e.code == 1

        # The fixer module should not have been loaded
        mock_load_fixer.assert_not_called()
        # The heal function should not have been called
        mock_heal.assert_not_called()

        # Verify the security violation was logged
        calls = mock_core_dependencies["cli_audit_logger"].log_event.call_args_list
        security_violations = [c for c in calls if c[0][0] == "command_forbidden"]
        assert len(security_violations) > 0
        assert security_violations[0][1]["reason"] == "yes_forbidden_in_prod"


def test_serve_command_in_prod_enforces_security(
    test_project_setup, mock_plugin_manager, mock_core_dependencies
):
    """
    Tests that the 'serve' command in production requires auth and HTTPS.
    """
    # Override the plugin manager's whitelisted dirs to include temp path
    mock_plugin_manager.whitelisted_plugin_dirs = [
        os.path.dirname(test_project_setup["project_root"]),
        tempfile.gettempdir(),
        os.getcwd(),
    ]

    # Set environment variable to match the patched PRODUCTION_MODE
    os.environ["PRODUCTION_MODE"] = "true"

    # Mock the ImportGraphAnalyzer to prevent actual import
    mock_analyzer = MagicMock()
    mock_analyzer.serve_dashboard = MagicMock()

    # Create a custom exception to use as a side effect for sys.exit
    class TestExit(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__(f"sys.exit({code})")

    # Test: Missing --require-auth
    with patch(
        "sys.argv",
        ["self_healing_import_fixer.cli", "serve", test_project_setup["project_root"]],
    ), patch(
        "self_healing_import_fixer.cli.ImportGraphAnalyzer",
        create=True,
        return_value=mock_analyzer,
    ), patch(
        "self_healing_import_fixer.cli.PRODUCTION_MODE", True
    ), patch(
        "self_healing_import_fixer.cli.load_analyzer"
    ) as mock_load_analyzer, patch(
        "sys.exit", side_effect=lambda code: (_ for _ in ()).throw(TestExit(code))
    ):

        try:
            main()
        except TestExit as e:
            # We expect exit with code 1 due to missing --require-auth
            assert e.code == 1

        # The analyzer should have been loaded (happens before security check)
        mock_load_analyzer.assert_called_once()
        # But serve_dashboard should not have been called
        mock_analyzer.serve_dashboard.assert_not_called()

        # Verify the security violation was logged
        calls = mock_core_dependencies["cli_audit_logger"].log_event.call_args_list
        security_violations = [c for c in calls if c[0][0] == "command_forbidden"]
        assert len(security_violations) > 0
        assert security_violations[0][1]["reason"] == "auth_not_required_in_prod"


@pytest.mark.asyncio
async def test_main_loads_plugins_from_config(mock_plugin_manager, test_project_setup):
    """Tests that the CLI correctly initializes the plugin manager from a config file."""
    config_data = {"plugins": {"approved_plugins": {"my_plugin": "some_signature"}}}

    config_path = Path(test_project_setup["project_root"]) / "config.yaml"

    # Write actual config file for test
    import yaml

    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    # Mock analyzer to prevent it from running
    mock_analyzer = MagicMock()
    mock_analyzer.generate_text_report.return_value = "Test Report"

    # Override the plugin manager's whitelisted dirs
    mock_plugin_manager.whitelisted_plugin_dirs = [
        os.path.dirname(test_project_setup["project_root"]),
        tempfile.gettempdir(),
        os.getcwd(),
    ]

    with patch("self_healing_import_fixer.cli.load_config", return_value=config_data), patch(
        "sys.argv",
        [
            "self_healing_import_fixer.cli",
            "--config",
            str(config_path),
            "analyze",
            test_project_setup["project_root"],
            "--output-format",
            "text",
        ],
    ), patch(
        "self_healing_import_fixer.cli.ImportGraphAnalyzer",
        create=True,
        return_value=mock_analyzer,
    ), patch(
        "self_healing_import_fixer.cli.PluginManager"
    ) as MockPluginManager, patch(
        "builtins.print"
    ):

        # Set the return value for the mocked manager
        MockPluginManager.return_value = mock_plugin_manager

        await main_async()

    # Assert that PluginManager was initialized with the correct approved plugins
    MockPluginManager.assert_called_with(
        plugin_dirs=None, approved_plugins=config_data["plugins"]["approved_plugins"]
    )
    mock_plugin_manager.discover_and_load.assert_called_once()


def test_selftest_command_runs_diagnostics(mock_plugin_manager, mock_core_dependencies):
    """
    Tests that the 'selftest' command attempts to load all modules and logs results.
    """
    # Mock the analyzer and fixer modules at the global level
    mock_analyzer = MagicMock()
    mock_analyzer.build_graph.return_value = {}

    with patch("sys.argv", ["self_healing_import_fixer.cli", "selftest"]), patch(
        "self_healing_import_fixer.cli.ImportGraphAnalyzer",
        create=True,
        return_value=mock_analyzer,
    ), patch("self_healing_import_fixer.cli.heal_entrypoint", create=True), patch(
        "shutil.which", return_value=True
    ), patch(
        "sys.exit"
    ) as mock_exit:  # Catch the exit call

        main()

        # Selftest should exit with 0 on success
        mock_exit.assert_called_once_with(0)

        # Assert that audit logger was called for test events
        calls = mock_core_dependencies["cli_audit_logger"].log_event.call_args_list
        selftest_calls = [c[0][0] for c in calls]
        assert "selftest_start" in selftest_calls
        assert "selftest_complete" in selftest_calls


def test_cli_execution_failure_logs_and_aborts(mock_plugin_manager, mock_core_dependencies):
    """
    Tests that a generic unhandled exception during command execution is caught, logged,
    and results in an abort.
    """
    # Override the plugin manager's whitelisted dirs to include current dir
    mock_plugin_manager.whitelisted_plugin_dirs = [os.getcwd(), tempfile.gettempdir()]

    # Mock ImportGraphAnalyzer to raise an error when instantiated
    with patch(
        "self_healing_import_fixer.cli.ImportGraphAnalyzer",
        create=True,
        side_effect=ValueError("An unexpected error occurred."),
    ), patch("sys.argv", ["self_healing_import_fixer.cli", "analyze", "."]), patch(
        "sys.exit"
    ) as mock_exit:  # Catch the exit call

        main()

        # Should exit with code 1 for failures
        mock_exit.assert_called_once_with(1)

        # Check that a failure was audited
        calls = mock_core_dependencies["cli_audit_logger"].log_event.call_args_list
        failure_calls = [c for c in calls if c[0][0] == "cli_execution_failure"]
        assert len(failure_calls) > 0

        # Check that operator was alerted
        assert mock_core_dependencies["alert_operator"].called
