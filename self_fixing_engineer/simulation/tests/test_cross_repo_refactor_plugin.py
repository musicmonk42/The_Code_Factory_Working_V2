# tests/test_cross_repo_refactor_plugin.py

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the plugin from the project's root directory.
# This logic is adjusted to work even if the test file is located in a nested
# directory like 'simulation/tests/'.
try:
    # We now mock the import of the audit log module to resolve the warning.
    # The actual plugin import will succeed, and the mock will be in place.
    sys.modules["arbiter.audit_log"] = MagicMock()
    from simulation.plugins import cross_repo_refactor_plugin
    from simulation.plugins.cross_repo_refactor_plugin import (
        GITPYTHON_AVAILABLE,
        TENACITY_AVAILABLE,
        GitRepoManager,
        _is_safe_path,
        _mask_token_in_url,
        _validate_refactor_plan,
        perform_cross_repo_refactor,
        plugin_health,
    )
except ImportError as e:
    # If imports fail, provide a clear message. This can happen if the
    # project structure has changed unexpectedly.
    pytest.fail(
        f"Failed to import the plugin. Check project structure and sys.path "
        f"manipulation in the test file. Error: {e}",
        pytrace=False,
    )


# ==============================================================================
# Pytest Fixtures for Mocking External Dependencies and Environment
# ==============================================================================


@pytest.fixture(autouse=True)
def mock_external_dependencies():
    """
    Mocks all external libraries and environment variables for complete isolation.
    This fixture runs automatically for every test.
    """
    # We assume GitPython and Tenacity are available for the logic to be tested.
    # If they were not, the plugin would be disabled, which is a different test case.
    if not GITPYTHON_AVAILABLE:
        pytest.skip("GitPython is not installed, cannot run these tests.")
    if not TENACITY_AVAILABLE:
        pytest.skip("Tenacity is not installed, cannot run these tests.")

    # Mock the GitPython Repo class and its methods
    with (
        patch.object(cross_repo_refactor_plugin, "Repo") as mock_repo_class,
        patch("aiohttp.ClientSession") as mock_aiohttp,
        patch.object(
            cross_repo_refactor_plugin._sfe_audit_logger, "log_event", new=AsyncMock()
        ) as mock_audit_log,
        patch.object(cross_repo_refactor_plugin.shutil, "rmtree") as mock_rmtree,
        patch.dict(
            cross_repo_refactor_plugin.GIT_CONFIG,
            {
                "default_branch_prefix": "sfe-refactor/",
                "default_author_name": "Self-Fixing Engineer Bot",
                "default_author_email": "bot@self-fixing.engineer",
                "clone_timeout_seconds": 300,
                "push_timeout_seconds": 90,
                "git_op_timeout_seconds": 120,
                "pr_api_base_url": "https://api.github.com",
                "pr_api_token": "mock_pr_token",
                "retry_attempts": 3,
                "retry_backoff_factor": 2.0,
                "max_concurrency": 3,
                "validate_remote_in_health": False,
                "health_sample_repo_url": "",
                "scrub_pushurl_on_retain": True,
            },
        ),
        patch.dict(
            os.environ,
            {
                "GIT_PR_API_BASE_URL": "https://api.github.com",
                "GIT_PR_API_TOKEN": "mock_pr_token",
            },
        ),
    ):

        # --- Mock GitPython Repo Instance ---
        mock_repo_instance = MagicMock()
        mock_repo_instance.git.fetch = AsyncMock()
        mock_repo_instance.git.checkout = AsyncMock()
        mock_repo_instance.index.add = AsyncMock()
        mock_repo_instance.index.commit = MagicMock(
            return_value=MagicMock(hexsha="mock_commit_sha")
        )
        mock_repo_instance.remote.return_value.push = AsyncMock()
        mock_repo_instance.remote.return_value.set_url = AsyncMock()
        mock_repo_class.clone_from.return_value = mock_repo_instance
        mock_repo_class.return_value = (
            mock_repo_instance  # For when Repo is instantiated directly
        )

        yield {
            "mock_repo_class": mock_repo_class,
            "mock_repo_instance": mock_repo_instance,
            "mock_aiohttp": mock_aiohttp,
            "mock_audit_log": mock_audit_log,
            "mock_rmtree": mock_rmtree,
        }


@pytest.fixture
def temp_repo_dir():
    """Creates a temporary directory to simulate a cloned repository."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def valid_refactor_plan():
    """Provides a valid, minimal refactor plan for one repository."""
    return [
        {
            "repo_url": "https://github.com/test-org/test-repo.git",
            "changes": [
                {"filepath": "src/main.py", "new_content": "print('hello world')"}
            ],
            "commit_message": "feat: initial refactor",
            "base_branch": "main",
            "create_pr": True,
        }
    ]


# ==============================================================================
# Unit Tests for Helper Functions and Validation
# ==============================================================================


def test_validate_refactor_plan_success(valid_refactor_plan):
    """Test that a well-formed refactor plan passes validation."""
    is_valid, error_msg = _validate_refactor_plan(valid_refactor_plan)
    assert is_valid is True
    assert error_msg is None


@pytest.mark.parametrize(
    "invalid_plan, expected_error",
    [
        ([], "must be a non-empty list"),
        ([{"changes": []}], "repo_url is required"),
        ([{"repo_url": "url", "changes": {}}], "changes must be a list"),
    ],
)
def test_validate_refactor_plan_failures(invalid_plan, expected_error):
    """Test various malformed refactor plans to ensure they fail validation."""
    is_valid, error_msg = _validate_refactor_plan(invalid_plan)
    assert is_valid is False
    assert expected_error in error_msg


def test_mask_token_in_url():
    """Test that secrets are correctly masked in URLs for logging."""
    url = "https://user:ghp_secrettoken123@github.com/org/repo.git"
    masked = _mask_token_in_url(url)
    assert "ghp_secrettoken123" not in masked
    assert "user:***@" in masked


def test_is_safe_path(temp_repo_dir):
    """Test path traversal prevention logic."""
    base = Path(temp_repo_dir)
    assert _is_safe_path(str(base), str(base / "src" / "file.txt")) is True
    assert (
        _is_safe_path(str(base), str(base / "src" / ".." / "file.txt")) is True
    )  # Still inside
    assert (
        _is_safe_path(str(base), str(base / "src" / ".." / ".." / "etc" / "passwd"))
        is False
    )
    assert _is_safe_path(str(base), "/etc/passwd") is False


# ==============================================================================
# Unit Tests for GitRepoManager Class
# ==============================================================================


@pytest.mark.asyncio
async def test_git_repo_manager_clone(mock_external_dependencies, temp_repo_dir):
    """Test the clone_repo method of the GitRepoManager."""
    manager = GitRepoManager("https://github.com/test/repo.git", temp_repo_dir)
    await manager.clone_repo()
    mock_external_dependencies["mock_repo_class"].clone_from.assert_called_once_with(
        "https://github.com/test/repo.git", temp_repo_dir
    )


@pytest.mark.asyncio
async def test_git_repo_manager_push(mock_external_dependencies, temp_repo_dir):
    """Test the push_branch method, ensuring it uses the correct refspec."""
    manager = GitRepoManager("https://github.com/test/repo.git", temp_repo_dir)
    # We need to "clone" first to initialize the internal _repo object
    await manager.clone_repo()
    await manager.push_branch("my-feature-branch")

    mock_push = mock_external_dependencies[
        "mock_repo_instance"
    ].remote.return_value.push
    mock_push.assert_called_once()
    # Check that the refspec 'my-feature-branch:my-feature-branch' was used
    assert mock_push.call_args[0][0] == "my-feature-branch:my-feature-branch"


# ==============================================================================
# Integration Tests for `perform_cross_repo_refactor` Workflow
# ==============================================================================


@pytest.mark.asyncio
async def test_perform_refactor_full_success_workflow(
    mock_external_dependencies, temp_repo_dir, valid_refactor_plan
):
    """
    Test the complete successful workflow: clone, branch, change, commit, push, and PR creation.
    """
    with (
        patch("tempfile.mkdtemp", return_value=temp_repo_dir),
        patch.object(
            cross_repo_refactor_plugin.GitRepoManager,
            "create_pull_request",
            new=AsyncMock(return_value="https://github.com/mock/repo/pull/1"),
        ),
    ):

        result = await perform_cross_repo_refactor(
            refactor_plan=valid_refactor_plan,
            git_credentials={"username": "testuser", "token": "testtoken"},
            dry_run=False,
            cleanup_on_success=True,
        )

    # --- Assertions ---
    assert result["success"] is True
    assert "completed successfully" in result["reason"]
    assert len(result["results_per_repo"]) == 1

    repo_result = result["results_per_repo"][0]
    assert repo_result["status"] == "SUCCESS_PR_CREATED"
    assert repo_result["commit_sha"] == "mock_commit_sha"
    assert repo_result["pull_request_url"] is not None

    # Verify mocks were called as expected
    mock_repo = mock_external_dependencies["mock_repo_instance"]
    mock_repo.git.fetch.assert_called_once()
    mock_repo.index.add.assert_called_once()
    mock_repo.index.commit.assert_called_once()
    mock_repo.remote.return_value.push.assert_called_once()
    mock_external_dependencies["mock_rmtree"].assert_called_once_with(
        temp_repo_dir
    )  # Cleanup was triggered
    mock_external_dependencies["mock_audit_log"].assert_called()


@pytest.mark.asyncio
async def test_perform_refactor_dry_run(
    mock_external_dependencies, temp_repo_dir, valid_refactor_plan
):
    """
    Test that in dry_run mode, no remote actions (commit, push, PR) are performed.
    """
    with patch("tempfile.mkdtemp", return_value=temp_repo_dir):
        result = await perform_cross_repo_refactor(
            refactor_plan=valid_refactor_plan,
            git_credentials={"username": "testuser", "token": "testtoken"},
            dry_run=True,
        )

    assert result["success"] is True
    repo_result = result["results_per_repo"][0]
    assert repo_result["status"] == "DRY_RUN_SUCCESS"
    assert "Changes applied locally" in repo_result["reason"]

    # Assert remote actions were NOT called
    mock_repo = mock_external_dependencies["mock_repo_instance"]
    mock_repo.index.commit.assert_not_called()
    mock_repo.remote.return_value.push.assert_not_called()
    mock_external_dependencies[
        "mock_aiohttp"
    ].return_value.__aenter__.return_value.post.assert_not_called()


@pytest.mark.asyncio
async def test_perform_refactor_clone_failure(
    mock_external_dependencies, temp_repo_dir, valid_refactor_plan
):
    """
    Test the workflow when a git clone operation fails.
    """
    # Simulate a clone failure
    mock_external_dependencies["mock_repo_class"].clone_from.side_effect = Exception(
        "Clone failed: repository not found"
    )

    with patch("tempfile.mkdtemp", return_value=temp_repo_dir):
        result = await perform_cross_repo_refactor(
            refactor_plan=valid_refactor_plan,
            git_credentials={"username": "testuser", "token": "testtoken"},
            dry_run=False,
            cleanup_on_failure=True,
        )

    assert result["success"] is False
    assert "One or more repositories failed" in result["reason"]
    repo_result = result["results_per_repo"][0]
    assert repo_result["status"] == "FAILED"
    assert "clone_failed" in repo_result["error_type"]
    mock_external_dependencies["mock_rmtree"].assert_called_once_with(
        temp_repo_dir
    )  # Cleanup should still run


@pytest.mark.asyncio
async def test_perform_refactor_no_cleanup_on_failure(
    mock_external_dependencies, temp_repo_dir, valid_refactor_plan
):
    """
    Test that temporary directories are retained for debugging when cleanup_on_failure is False.
    """
    mock_external_dependencies["mock_repo_class"].clone_from.side_effect = Exception(
        "Simulated failure"
    )

    with patch("tempfile.mkdtemp", return_value=temp_repo_dir):
        await perform_cross_repo_refactor(
            refactor_plan=valid_refactor_plan,
            git_credentials={"username": "testuser", "token": "testtoken"},
            cleanup_on_failure=False,  # Key setting for this test
        )

    # Assert that the cleanup function was NOT called
    mock_external_dependencies["mock_rmtree"].assert_not_called()


# ==============================================================================
# Health Check Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_plugin_health_success():
    """Test health check when Git and GitPython are available."""
    # Patch the environment variables needed for the health check to avoid warnings.
    with (
        patch(
            "simulation.plugins.cross_repo_refactor_plugin.GITPYTHON_AVAILABLE", True
        ),
        patch("asyncio.create_subprocess_exec") as mock_subprocess,
        patch.dict(
            cross_repo_refactor_plugin.GIT_CONFIG,
            {
                "pr_api_token": "mock_pr_token",
                "pr_api_base_url": "https://api.github.com",
            },
        ),
    ):

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"git version 2.30.0", b"")
        mock_proc.returncode = 0
        mock_subprocess.return_value = mock_proc

        health = await plugin_health()
        assert health["status"] == "ok"
        assert "Git CLI detected" in health["details"][0]
        assert "Git PR API token found (PR creation enabled)." in health["details"]


@pytest.mark.asyncio
async def test_plugin_health_gitpython_missing():
    """Test health check when GitPython is not installed."""
    with patch(
        "simulation.plugins.cross_repo_refactor_plugin.GITPYTHON_AVAILABLE", False
    ):
        health = await plugin_health()
        assert health["status"] == "error"
        assert "GitPython library not found" in health["details"][0]
