# tests/test_scala_test_runner_plugin.py

import pytest
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, mock_open

# Security fix: Use defusedxml to prevent XXE attacks

# Import the plugin from the correct directory
# Try multiple possible locations for the plugin
plugin_paths = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "plugins")),  # /plugins/
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "plugins")
    ),  # /simulation/plugins/
]
for path in plugin_paths:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from scala_test_runner_plugin import (
        plugin_health,
        run_scala_tests,
        _parse_junit_xml,
        _parse_scoverage_xml,
        _which,
        _get_sbt_version,
    )
except ImportError as e:
    print(f"Failed to import scala_test_runner_plugin. Searched in: {plugin_paths}")
    print(f"Error: {e}")
    raise


# ==============================================================================
# Pytest Fixtures for mocking external dependencies and environment
# ==============================================================================


@pytest.fixture
def mock_sbt_and_java_in_path():
    """
    Mocks the `which`/`where` command to find SBT and Java.
    """
    with patch("scala_test_runner_plugin._which") as mock_which, patch(
        "scala_test_runner_plugin._get_sbt_version", new=AsyncMock(return_value="1.9.8")
    ), patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ):  # Assume files exist

        # Configure _which to return paths for Java and SBT
        def which_side_effect(cmd):
            if cmd == "java":
                return "/usr/bin/java"
            elif cmd == "sbt":
                return "/usr/bin/sbt"
            return None

        mock_which.side_effect = which_side_effect

        # Configure the subprocess to return mock version info
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"java version 1.8"))
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        yield {
            "mock_which": mock_which,
            "mock_subprocess": mock_subprocess,
        }


# ==============================================================================
# Unit Tests for `plugin_health`
# ==============================================================================


@pytest.mark.asyncio
async def test_plugin_health_success(mock_sbt_and_java_in_path):
    """Test `plugin_health` returns 'ok' when Java and SBT are found."""
    result = await plugin_health()
    assert result["status"] == "ok"
    assert "Java detected" in str(result["details"])
    assert "SBT detected" in str(result["details"])


@pytest.mark.asyncio
async def test_plugin_health_sbt_not_found():
    """Test `plugin_health` returns 'degraded' when SBT is not found."""
    with patch("scala_test_runner_plugin._which") as mock_which, patch(
        "asyncio.create_subprocess_exec", new=AsyncMock()
    ) as mock_subprocess:

        # Java found, SBT not found
        def which_side_effect(cmd):
            if cmd == "java":
                return "/usr/bin/java"
            return None

        mock_which.side_effect = which_side_effect

        # Mock java -version
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"java version 1.8"))
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = await plugin_health()
        assert result["status"] == "degraded"
        assert "SBT not found in PATH" in str(result["details"])


# ==============================================================================
# Unit Tests for XML Parsing Helpers
# ==============================================================================


def test_parse_junit_xml_success():
    """Test parsing a valid JUnit XML file."""
    junit_xml_content = """
<testsuites tests="2" failures="1" errors="0" skipped="0">
    <testsuite tests="2" failures="1" errors="0" skipped="0" name="com.example.app.MyTest">
        <testcase name="testPass" classname="com.example.app.MyTest" />
        <testcase name="testFail" classname="com.example.app.MyTest">
            <failure message="assertion failed" />
        </testcase>
    </testsuite>
</testsuites>
"""
    with patch("os.path.exists", return_value=True), patch(
        "builtins.open", new_callable=mock_open, read_data=junit_xml_content
    ):
        summary = _parse_junit_xml("mock_junit.xml")
        assert summary == {"tests": 2, "failures": 1, "errors": 0, "skipped": 0}


def test_parse_junit_xml_malformed():
    """Test parsing a malformed JUnit XML file gracefully."""
    malformed_xml_content = "<testsuites> <testsuite tests='a' /> </testsuites>"
    with patch("os.path.exists", return_value=True), patch(
        "builtins.open", new_callable=mock_open, read_data=malformed_xml_content
    ):
        summary = _parse_junit_xml("mock_junit_malformed.xml")
        assert summary == {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}


def test_parse_scoverage_xml_success():
    """Test parsing a valid Scoverage XML file."""
    scoverage_xml_content = """
<scoverage statement-rate="85.50">
    <project name="My Project">
        <measurement type="line" statement-rate="85.50" />
    </project>
</scoverage>
"""
    with patch("os.path.exists", return_value=True), patch(
        "builtins.open", new_callable=mock_open, read_data=scoverage_xml_content
    ):
        coverage = _parse_scoverage_xml("mock_scoverage.xml")
        assert coverage == 85.50


def test_parse_scoverage_xml_no_coverage_info():
    """Test parsing an Scoverage XML file with no coverage information."""
    no_coverage_xml_content = "<scoverage><project></project></scoverage>"
    with patch("os.path.exists", return_value=True), patch(
        "builtins.open", new_callable=mock_open, read_data=no_coverage_xml_content
    ):
        coverage = _parse_scoverage_xml("mock_scoverage_no_coverage.xml")
        assert coverage == 0.0


# ==============================================================================
# Integration Tests for `run_scala_tests` workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_run_scala_tests_success_full_workflow():
    """
    Test the complete successful workflow of `run_scala_tests` with mocks.
    """
    with patch("scala_test_runner_plugin._which") as mock_which, patch(
        "scala_test_runner_plugin._get_sbt_version", new=AsyncMock(return_value="1.9.8")
    ), patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_subprocess, patch(
        "os.path.exists"
    ) as mock_exists, patch(
        "os.makedirs"
    ) as mock_makedirs, patch(
        "shutil.copy"
    ), patch(
        "shutil.copy2"
    ), patch(
        "shutil.copyfile"
    ), patch(
        "scala_test_runner_plugin._parse_junit_xml",
        return_value={"tests": 1, "failures": 0, "errors": 0, "skipped": 0},
    ), patch(
        "scala_test_runner_plugin._parse_scoverage_xml", return_value=85.50
    ), patch(
        "scala_test_runner_plugin._find_scoverage_xml",
        return_value="/mock/path/scoverage.xml",
    ), patch(
        "os.listdir", return_value=["TEST-results.xml"]
    ), patch(
        "tempfile.TemporaryDirectory"
    ) as mock_temp_dir, patch(
        "builtins.open", new_callable=mock_open
    ):

        # Configure _which to return sbt path
        mock_which.return_value = "/usr/bin/sbt"

        # Configure exists to return True for test file, False for build.sbt (to trigger temp project creation)
        def exists_side_effect(path):
            if "build.sbt" in path:
                return False
            return True

        mock_exists.side_effect = exists_side_effect

        # Mock TemporaryDirectory
        mock_temp_dir_instance = MagicMock()
        mock_temp_dir_instance.name = "/tmp/mock_sbt_temp"
        mock_temp_dir_instance.cleanup = MagicMock()
        mock_temp_dir_instance.__enter__ = MagicMock(return_value="/tmp/mock_sbt_temp")
        mock_temp_dir_instance.__exit__ = MagicMock(return_value=None)
        mock_temp_dir.return_value = mock_temp_dir_instance

        # Mock SBT subprocess output for a successful run
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"Test run finished: 1 total, 0 failed", b"")
        )
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        result = await run_scala_tests(
            test_file_path="MyTest.scala",
            target_identifier="com.example.MyClass",
            project_root="/mock/project_root",
            temp_coverage_report_path_relative="coverage.xml",
        )

        assert result["success"] is True
        assert "SBT tests passed" in result["reason"] or "SBT tests finished" in result["reason"]
        assert result["coverage_increase_percent"] == 85.50

        # Assert core steps were called
        mock_makedirs.assert_called()
        mock_subprocess.assert_called_once()


@pytest.mark.asyncio
async def test_run_scala_tests_test_failure():
    """Test `run_scala_tests` when the JUnit report indicates a test failure."""
    with patch("scala_test_runner_plugin._which") as mock_which, patch(
        "scala_test_runner_plugin._get_sbt_version", new=AsyncMock(return_value="1.9.8")
    ), patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ), patch(
        "scala_test_runner_plugin._parse_junit_xml",
        return_value={"tests": 2, "failures": 1, "errors": 0, "skipped": 0},
    ), patch(
        "scala_test_runner_plugin._parse_scoverage_xml", return_value=50.0
    ), patch(
        "scala_test_runner_plugin._find_scoverage_xml",
        return_value="/mock/path/scoverage.xml",
    ), patch(
        "os.listdir", return_value=["TEST-results.xml"]
    ), patch(
        "os.makedirs"
    ), patch(
        "shutil.copyfile"
    ), patch(
        "builtins.open", new_callable=mock_open
    ):

        # Configure _which to return sbt path
        mock_which.return_value = "/usr/bin/sbt"

        # Mock SBT subprocess output for a test failure
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"Test run finished: 2 total, 1 failed", b"")
        )
        mock_proc.returncode = 1
        mock_subprocess.return_value = mock_proc

        with tempfile.TemporaryDirectory() as temp_dir:
            result = await run_scala_tests(
                test_file_path="MyTest.scala",
                target_identifier="com.example.MyClass",
                project_root=temp_dir,
                temp_coverage_report_path_relative="coverage.xml",
            )

            assert result["success"] is False
            assert "1 failures" in result["reason"] or "SBT tests finished" in result["reason"]
            assert result["coverage_increase_percent"] == 50.0


@pytest.mark.asyncio
async def test_run_scala_tests_sbt_not_found():
    """Test that `run_scala_tests` returns an error if SBT is not in PATH."""
    with patch("scala_test_runner_plugin._which", return_value=None), patch(
        "os.path.exists", return_value=True
    ):  # Test file exists

        result = await run_scala_tests(
            test_file_path="MyTest.scala",
            target_identifier="com.example.MyClass",
            project_root="/mock/project_root",
            temp_coverage_report_path_relative="coverage.xml",
        )

        assert result["success"] is False
        assert "SBT not found in PATH" in result["reason"]


@pytest.mark.asyncio
async def test_run_scala_tests_file_not_found():
    """Test that `run_scala_tests` returns an error if the test file is not found."""
    with patch("scala_test_runner_plugin._which", return_value="/usr/bin/sbt"), patch(
        "os.path.exists", return_value=False
    ):

        result = await run_scala_tests(
            test_file_path="nonexistent/MyTest.scala",
            target_identifier="com.example.MyClass",
            project_root="/mock/project_root",
            temp_coverage_report_path_relative="coverage.xml",
        )

        assert result["success"] is False
        assert "Scala test file not found" in result["reason"]


@pytest.mark.asyncio
async def test_run_scala_tests_with_existing_build_sbt():
    """Test `run_scala_tests` when build.sbt already exists (no temp project needed)."""
    with patch("scala_test_runner_plugin._which", return_value="/usr/bin/sbt"), patch(
        "scala_test_runner_plugin._get_sbt_version", new=AsyncMock(return_value="1.9.8")
    ), patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ), patch(
        "scala_test_runner_plugin._parse_junit_xml",
        return_value={"tests": 3, "failures": 0, "errors": 0, "skipped": 1},
    ), patch(
        "scala_test_runner_plugin._parse_scoverage_xml", return_value=92.0
    ), patch(
        "scala_test_runner_plugin._find_scoverage_xml",
        return_value="/mock/path/scoverage.xml",
    ), patch(
        "os.listdir", return_value=["TEST-results.xml"]
    ), patch(
        "os.makedirs"
    ), patch(
        "shutil.copyfile"
    ), patch(
        "builtins.open", new_callable=mock_open
    ):

        # Mock SBT subprocess output for a successful run with existing project
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"Test run finished: 3 total, 0 failed, 1 skipped", b"")
        )
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        with tempfile.TemporaryDirectory() as temp_dir:
            # Create a build.sbt file
            build_sbt_path = os.path.join(temp_dir, "build.sbt")
            Path(build_sbt_path).touch()

            result = await run_scala_tests(
                test_file_path="src/test/scala/MyTest.scala",
                target_identifier="com.example.MyClass",
                project_root=temp_dir,
                temp_coverage_report_path_relative="coverage.xml",
            )

            assert result["success"] is True
            assert "3 tests, 1 skipped" in result["reason"]
            assert result["coverage_increase_percent"] == 92.0
            # Should not have created temp directories
            assert len(result["temp_dirs_used"]) == 0
    """Test that `run_scala_tests` handles timeout correctly."""
    with patch("scala_test_runner_plugin._which", return_value="/usr/bin/sbt"), patch(
        "scala_test_runner_plugin._get_sbt_version", new=AsyncMock(return_value="1.9.8")
    ), patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_subprocess, patch(
        "os.path.exists", return_value=True
    ):

        # Mock SBT subprocess that times out
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_subprocess.return_value = mock_proc

        with tempfile.TemporaryDirectory() as temp_dir:
            result = await run_scala_tests(
                test_file_path="MyTest.scala",
                target_identifier="com.example.MyClass",
                project_root=temp_dir,
                temp_coverage_report_path_relative="coverage.xml",
                timeout_seconds=1,  # Short timeout for testing
            )

            assert result["success"] is False
            assert "timed out" in result["reason"]
            assert result["raw_log"] == "TIMEOUT"
