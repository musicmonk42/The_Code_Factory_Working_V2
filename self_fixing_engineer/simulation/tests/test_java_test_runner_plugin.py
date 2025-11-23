# tests/test_jest_runner_plugin.py

import pytest
import asyncio
import os
import sys
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, mock_open

# Add parent directory to path to import the plugin
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import the plugin - now using the correct module path
from simulation.plugins.jest_runner_plugin import (
    plugin_health,
    run_jest_tests,
    _which,
    _detect_package_manager,
    _get_package_version,
)

# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture
def mock_node_in_path():
    """Mocks the `which`/`where` command to find Node and package managers."""
    with patch("plugins.jest_runner_plugin._which", new=AsyncMock()) as mock_which, patch(
        "asyncio.create_subprocess_exec", new=AsyncMock()
    ) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ):  # Assume files exist

        # Configure _which to return paths for Node and npx/npm
        async def which_side_effect(cmd):
            if cmd == "npx":
                return "/usr/bin/npx"
            elif cmd == "npm":
                return "/usr/bin/npm"
            elif cmd == "yarn":
                return None
            elif cmd == "node":
                return "/usr/bin/node"
            return None

        mock_which.side_effect = which_side_effect

        # Configure subprocess to return mock version info
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"v18.12.0", b""))
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        yield {
            "mock_which": mock_which,
            "mock_subprocess": mock_subprocess,
        }


@pytest.fixture
def mock_temp_jest_project():
    """Creates a temporary Jest project with a mock file structure."""
    with tempfile.TemporaryDirectory() as temp_dir:
        project_root = Path(temp_dir) / "my-project"
        os.makedirs(os.path.join(project_root, "src"), exist_ok=True)
        os.makedirs(os.path.join(project_root, "tests"), exist_ok=True)
        os.makedirs(os.path.join(project_root, "coverage"), exist_ok=True)

        with open(os.path.join(project_root, "package.json"), "w") as f:
            json.dump(
                {
                    "name": "mock-project",
                    "version": "1.0.0",
                    "devDependencies": {"jest": "^29.5.0"},
                },
                f,
            )

        with open(os.path.join(project_root, "src", "sum.js"), "w") as f:
            f.write("function sum(a, b) { return a + b; }\nmodule.exports = sum;")

        with open(os.path.join(project_root, "tests", "sum.test.js"), "w") as f:
            f.write(
                """
                const sum = require('../src/sum');
                test('adds 1 + 2 to equal 3', () => { expect(sum(1, 2)).toBe(3); });
            """
            )

        yield project_root


# ==============================================================================
# Unit Tests for `plugin_health` and helpers
# ==============================================================================


@pytest.mark.asyncio
async def test_plugin_health_success(mock_node_in_path):
    """Test `plugin_health` returns 'ok' when all dependencies are found."""
    result = await plugin_health()
    assert result["status"] == "ok"
    assert any("npx detected" in str(detail) for detail in result["details"])
    assert any("npm detected" in str(detail) for detail in result["details"])


@pytest.mark.asyncio
async def test_plugin_health_npx_not_found():
    """Test `plugin_health` returns 'degraded' when npx is not found."""
    with patch("plugins.jest_runner_plugin._which", new=AsyncMock()) as mock_which:

        async def which_side_effect(cmd):
            if cmd == "npx":
                return None
            elif cmd == "npm":
                return "/usr/bin/npm"
            elif cmd == "yarn":
                return None
            elif cmd == "node":
                return "/usr/bin/node"
            return None

        mock_which.side_effect = which_side_effect

        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_subprocess:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"v18.12.0", b""))
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            result = await plugin_health()
            assert result["status"] == "degraded"
            assert any("npx not found" in str(detail) for detail in result["details"])


@pytest.mark.asyncio
async def test_detect_package_manager():
    """Test `_detect_package_manager` correctly finds available managers."""
    with patch("plugins.jest_runner_plugin._which", new=AsyncMock()) as mock_which:

        async def which_async(cmd):
            if cmd == "npx":
                return "/usr/bin/npx"
            elif cmd == "npm":
                return "/usr/bin/npm"
            elif cmd == "yarn":
                return None
            return None

        mock_which.side_effect = which_async

        npx, npm, yarn = await _detect_package_manager()
        assert npx == "/usr/bin/npx"
        assert npm == "/usr/bin/npm"
        assert yarn is None


# ==============================================================================
# Integration Tests for `run_jest_tests` workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_run_jest_tests_success_full_workflow(mock_temp_jest_project):
    """
    Test the complete successful workflow of `run_jest_tests` using a mock project.
    """
    with patch(
        "plugins.jest_runner_plugin._which", new=AsyncMock(return_value="/usr/bin/npx")
    ), patch(
        "plugins.jest_runner_plugin._get_package_version",
        new=AsyncMock(return_value="29.5.0"),
    ), patch(
        "asyncio.create_subprocess_exec", new=AsyncMock()
    ) as mock_subprocess, patch(
        "shutil.copy2"
    ) as mock_copy2, patch(
        "shutil.copyfile"
    ), patch(
        "plugins.jest_runner_plugin._copytree_compat"
    ), patch(
        "plugins.jest_runner_plugin._install_packages",
        new=AsyncMock(return_value=(True, "")),
    ):

        # Mock Jest subprocess output for a successful run
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(
            return_value=(b'{"success": true, "numFailedTests": 0}', b"")
        )
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        jest_output = {
            "success": True,
            "numFailedTests": 0,
            "numTotalTests": 1,
            "numPassedTests": 1,
            "testResults": [],
        }

        # Track what temp directory gets created for coverage mocking
        temp_jest_dir = None

        # Mock Path.exists to handle temp project creation and file existence
        original_path_exists = Path.exists

        def mock_path_exists(self):
            nonlocal temp_jest_dir
            path_str = str(self)
            # Force temp project creation by making package.json not exist in original project
            if path_str == str(mock_temp_jest_project / "package.json"):
                return False
            if path_str == str(mock_temp_jest_project / "node_modules"):
                return False
            # Allow original project files to exist
            if str(mock_temp_jest_project) in path_str and "jest_run_" not in path_str:
                return original_path_exists(self)
            # Track temp project directory and allow temp project files to exist
            if "jest_run_" in path_str:
                # Extract the temp directory path
                import re

                match = re.search(r"jest_run_[^/\\]+", path_str)
                if match:
                    temp_jest_dir = Path(
                        path_str[: path_str.index(match.group(0)) + len(match.group(0))]
                    )
                return True
            return original_path_exists(self)

        # Mock file operations for Jest output and coverage
        def mock_open_handler(path, *args, **kwargs):
            path_str = str(path)
            if path_str.endswith("jest-results.json"):
                return mock_open(read_data=json.dumps(jest_output))()
            elif path_str.endswith("coverage-final.json"):
                # Create coverage data with the actual temp project path that was created
                if temp_jest_dir:
                    # The target file in temp project will be at temp_jest_dir/src/sum.js
                    coverage_data = {str(temp_jest_dir / "src" / "sum.js"): {"lines": {"pct": 100}}}
                    return mock_open(read_data=json.dumps(coverage_data))()
                return mock_open(read_data="{}")()
            else:
                return mock_open(read_data='{"test": "data"}')()

        with patch.object(Path, "exists", mock_path_exists), patch(
            "builtins.open", side_effect=mock_open_handler
        ):

            result = await run_jest_tests(
                test_file_path="tests/sum.test.js",
                target_identifier=os.path.join("src", "sum.js"),
                project_root=mock_temp_jest_project,
                temp_coverage_report_path_relative=os.path.join("atco_artifacts", "coverage.json"),
            )

            assert result["success"] is True
            assert "Jest tests passed" in result["reason"]
            # The coverage should be extracted from the temp project path
            assert result["coverage_increase_percent"] == 100.0

            # Assert core steps were called
            mock_subprocess.assert_called()
            mock_copy2.assert_called()


@pytest.mark.asyncio
async def test_run_jest_tests_test_failure(mock_temp_jest_project):
    """Test `run_jest_tests` when the Jest report indicates a test failure."""
    with patch(
        "plugins.jest_runner_plugin._which", new=AsyncMock(return_value="/usr/bin/npx")
    ), patch(
        "plugins.jest_runner_plugin._get_package_version",
        new=AsyncMock(return_value="29.5.0"),
    ), patch(
        "asyncio.create_subprocess_exec", new=AsyncMock()
    ) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ), patch(
        "os.makedirs"
    ), patch(
        "shutil.copy2"
    ), patch(
        "shutil.copyfile"
    ), patch(
        "plugins.jest_runner_plugin._copytree_compat"
    ), patch(
        "plugins.jest_runner_plugin._install_packages",
        new=AsyncMock(
            return_value=(
                False,
                "Failed to setup Jest environment: Failed to install packages",
            )
        ),
    ):

        # Mock Jest subprocess output for a test failure
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(
            return_value=(b'{"success": false, "numFailedTests": 1}', b"")
        )
        mock_process.returncode = 1
        mock_subprocess.return_value = mock_process

        jest_output = {"success": False, "numFailedTests": 1}

        with patch("builtins.open", mock_open(read_data='{"test": "data"}')), patch(
            "json.load", return_value=jest_output
        ):

            # Temporarily remove package.json to force temp project creation
            with patch(
                "os.path.exists",
                side_effect=lambda p: p != os.path.join(mock_temp_jest_project, "package.json"),
            ):
                result = await run_jest_tests(
                    test_file_path="tests/sum.test.js",
                    target_identifier="src/sum.js",
                    project_root=mock_temp_jest_project,
                    temp_coverage_report_path_relative="atco_artifacts/coverage.json",
                )

            assert result["success"] is False
            assert "Failed to setup Jest environment" in result["reason"]
            assert result["coverage_increase_percent"] == 0.0  # No coverage data mocked


@pytest.mark.asyncio
async def test_run_jest_tests_file_not_found():
    """Test that `run_jest_tests` returns an error if the test file is not found."""
    with patch(
        "plugins.jest_runner_plugin._which", new=AsyncMock(return_value="/usr/bin/npx")
    ), patch("os.path.exists", return_value=False):

        result = await run_jest_tests(
            test_file_path="nonexistent/sum.test.js",
            target_identifier="src/sum.js",
            project_root="/mock/project_root",
            temp_coverage_report_path_relative="coverage.json",
        )

        assert result["success"] is False
        assert "Jest test file not found" in result["reason"]


@pytest.mark.asyncio
async def test_run_jest_tests_timeout():
    """Test that `run_jest_tests` handles timeouts properly."""
    with patch(
        "plugins.jest_runner_plugin._which", new=AsyncMock(return_value="/usr/bin/npx")
    ), patch(
        "plugins.jest_runner_plugin._get_package_version",
        new=AsyncMock(return_value="29.5.0"),
    ), patch(
        "asyncio.create_subprocess_exec", new=AsyncMock()
    ) as mock_subprocess, patch(
        "os.makedirs"
    ), patch(
        "shutil.copy2"
    ), patch(
        "shutil.copyfile"
    ), patch(
        "plugins.jest_runner_plugin._copytree_compat"
    ), patch(
        "plugins.jest_runner_plugin._install_packages",
        new=AsyncMock(return_value=(True, "")),
    ):

        # Need to mock the actual implementation that _bound_search_for_package_json uses
        original_exists = Path.exists

        def mock_path_exists(self):
            path_str = str(self)
            # The search starts from /mock/project/tests and goes up
            # We need package.json to exist at /mock/project but not at /mock/project/tests
            if path_str.endswith(os.path.join("mock", "project", "tests", "package.json")):
                return False
            elif path_str.endswith(os.path.join("mock", "project", "package.json")):
                return True
            elif path_str.endswith(os.path.join("mock", "project", "node_modules")):
                return True
            elif path_str.endswith(os.path.join("mock", "project", "tests", "sum.test.js")):
                return True
            elif path_str.endswith(os.path.join("mock", "project", "src", "sum.js")):
                return True
            elif "jest_run_" in path_str:
                return True
            # Default to original behavior for resolve() calls
            return original_exists(self) if hasattr(self, "_accessor") else True

        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_subprocess.return_value = mock_process

        with patch.object(Path, "exists", mock_path_exists), patch(
            "builtins.open", mock_open(read_data='{"test": "data"}')
        ):

            result = await run_jest_tests(
                test_file_path=os.path.join("tests", "sum.test.js"),
                target_identifier=os.path.join("src", "sum.js"),
                project_root="/mock/project",
                temp_coverage_report_path_relative="coverage.json",
                timeout_seconds=5,
            )

        assert result["success"] is False
        assert "timeout" in result["reason"].lower() or "timed out" in result["raw_log"].lower()


@pytest.mark.asyncio
async def test_get_package_version():
    """Test `_get_package_version` extracts version correctly."""
    # Mock file content for package.json
    package_json_content = '{"devDependencies": {"jest": "^29.5.0"}}'

    # Need to mock os.path.exists to return True for the package.json file
    with patch("os.path.exists", return_value=True), patch(
        "builtins.open", mock_open(read_data=package_json_content)
    ):
        version = await _get_package_version("/mock/project", "jest")
        assert version == "29.5.0"

    # Test with missing package
    package_json_no_jest = '{"devDependencies": {}}'
    with patch("os.path.exists", return_value=True), patch(
        "builtins.open", mock_open(read_data=package_json_no_jest)
    ):
        version = await _get_package_version("/mock/project", "jest")
        assert version is None


@pytest.mark.asyncio
async def test_which_command():
    """Test `_which` command detection on different platforms."""
    # Mock _shutil_which to return None so it falls back to subprocess
    with patch("plugins.jest_runner_plugin._shutil_which", return_value=None):

        # Test on Windows
        with patch("os.name", "nt"), patch(
            "asyncio.create_subprocess_exec", new=AsyncMock()
        ) as mock_exec:

            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(
                return_value=(b"C:\\Program Files\\nodejs\\node.exe\n", b"")
            )
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            result = await _which("node")
            assert result == "C:\\Program Files\\nodejs\\node.exe"

        # Test on Unix
        with patch("os.name", "posix"), patch(
            "asyncio.create_subprocess_exec", new=AsyncMock()
        ) as mock_exec:

            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"/usr/bin/node\n", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            result = await _which("node")
            assert result == "/usr/bin/node"

        # Test command not found
        with patch("os.name", "posix"), patch(
            "asyncio.create_subprocess_exec", new=AsyncMock()
        ) as mock_exec:

            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"command not found"))
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            result = await _which("nonexistent")
            assert result is None


# ==============================================================================
# Additional Edge Case Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_run_jest_tests_no_npx():
    """Test that run_jest_tests fails gracefully when npx is not found."""

    # Mock Path.exists to allow the test file to exist and avoid temp project creation
    def mock_path_exists(self):
        path_str = str(self)
        project_root = Path("/mock/project")
        # Allow the specific test file to exist
        if path_str == str(project_root / "tests" / "sum.test.js"):
            return True
        # Allow package.json and node_modules to exist to avoid temp project creation
        if path_str == str(project_root / "package.json"):
            return True
        if path_str == str(project_root / "node_modules"):
            return True
        return True

    with patch("plugins.jest_runner_plugin._which", new=AsyncMock(return_value=None)), patch.object(
        Path, "exists", mock_path_exists
    ):

        result = await run_jest_tests(
            test_file_path="tests/sum.test.js",
            target_identifier="src/sum.js",
            project_root="/mock/project",
            temp_coverage_report_path_relative="coverage.json",
        )

        assert result["success"] is False
        assert "npx not found" in result["reason"]


@pytest.mark.asyncio
async def test_run_jest_tests_with_extra_args(mock_temp_jest_project):
    """Test run_jest_tests with extra Jest arguments."""
    with patch(
        "plugins.jest_runner_plugin._which", new=AsyncMock(return_value="/usr/bin/npx")
    ), patch(
        "plugins.jest_runner_plugin._get_package_version",
        new=AsyncMock(return_value="29.5.0"),
    ), patch(
        "asyncio.create_subprocess_exec", new=AsyncMock()
    ) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ), patch(
        "os.makedirs"
    ), patch(
        "shutil.copy2"
    ), patch(
        "shutil.copyfile"
    ), patch(
        "plugins.jest_runner_plugin._copytree_compat"
    ), patch(
        "plugins.jest_runner_plugin._install_packages",
        new=AsyncMock(return_value=(True, "")),
    ):

        # Mock successful Jest execution
        mock_process = MagicMock()
        mock_process.communicate = AsyncMock(
            return_value=(b'{"success": true, "numFailedTests": 0}', b"")
        )
        mock_process.returncode = 0
        mock_subprocess.return_value = mock_process

        # Mock Path.exists properly - as instance method
        def mock_path_exists(self):
            path_str = str(self)
            # Package.json should be at project root to avoid temp project AND prevent wrong jest_project_root
            if path_str == str(mock_temp_jest_project / "package.json"):
                return True
            # Allow node_modules to exist
            if path_str == str(mock_temp_jest_project / "node_modules"):
                return True
            # Allow test file to exist
            if path_str == str(mock_temp_jest_project / "tests" / "sum.test.js"):
                return True
            # Allow target file to exist
            if path_str == str(mock_temp_jest_project / "src" / "sum.js"):
                return True
            # Don't allow package.json in tests subdirectory
            if path_str == str(mock_temp_jest_project / "tests" / "package.json"):
                return False
            # Allow temp project files
            if "jest_run_" in path_str:
                return True
            return True

        with patch.object(Path, "exists", mock_path_exists), patch(
            "builtins.open", mock_open(read_data='{"test": "data"}')
        ), patch("json.load", return_value={"success": True, "numFailedTests": 0}):

            result = await run_jest_tests(
                test_file_path="tests/sum.test.js",
                target_identifier="src/sum.js",
                project_root=mock_temp_jest_project,
                temp_coverage_report_path_relative="coverage.json",
                extra_jest_args=["--verbose", "--bail"],
            )

            assert result["success"] is True
            # Check that subprocess was called with extra args
            call_args = mock_subprocess.call_args[0]
            assert "--verbose" in call_args
            assert "--bail" in call_args
